#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 debs/ 目录，生成 apt 源索引（Packages / Packages.gz / Release）。

以后加插件只需要：把 .deb 丢进 debs/，再跑一次 python tools/build_repo.py。
"""

import bz2
import gzip
import hashlib
import io
import lzma
import os
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEBS_DIR = os.path.join(ROOT, "debs")

REPO_ORIGIN = "Heli"
REPO_LABEL = "Heli"
REPO_DESCRIPTION = "Heli 的越狱插件源"

# Packages 条目里字段的固定顺序，Sileo / Saily / Zebra 都能吃。
FIELD_ORDER = [
    "Package", "Name", "Version", "Architecture", "Section",
    "Maintainer", "Author", "Depends", "Conflicts", "Replaces",
    "Provides", "Priority", "Description",
]


def _decompress(blob):
    if blob[:6] == b"\xfd7zXZ\x00":
        return lzma.decompress(blob)
    if blob[:2] == b"\x1f\x8b":
        return gzip.decompress(blob)
    if blob[:3] == b"BZh":
        return bz2.decompress(blob)
    return blob


def read_control(deb_path):
    """从 deb 的 control.tar.* 里取出 control 文本。"""
    data = open(deb_path, "rb").read()
    if not data.startswith(b"!<arch>\n"):
        raise ValueError("%s 不是合法的 deb（缺少 ar 头）" % deb_path)

    offset = 8
    while offset < len(data) - 60:
        header = data[offset:offset + 60]
        if header[:2] == b"\x60\x0a":
            break
        name = header[:16].decode("utf-8", "replace").strip().rstrip("/")
        try:
            size = int(header[48:58].decode().strip())
        except ValueError:
            break
        body = data[offset + 60:offset + 60 + size]

        if name.startswith("control.tar"):
            raw = _decompress(body)
            archive = tarfile.open(fileobj=io.BytesIO(raw))
            for member in archive.getmembers():
                if member.name.split("/")[-1] == "control":
                    return archive.extractfile(member).read().decode("utf-8", "replace")
        offset += 60 + size + (size % 2)

    raise ValueError("%s 里找不到 control" % deb_path)


def parse_control(text):
    """把 control 解析成有序字段；多行字段（以空格续行）合并保留。"""
    fields = {}
    current = None
    for raw_line in text.splitlines():
        if not raw_line.strip():
            current = None
            continue
        if raw_line[0] in " \t" and current:
            fields[current] += "\n" + raw_line
            continue
        if ":" not in raw_line:
            continue
        key, _, value = raw_line.partition(":")
        current = key.strip()
        fields[current] = value.strip()
    return fields


def build_entry(deb_path, rel_path):
    fields = parse_control(read_control(deb_path))
    blob = open(deb_path, "rb").read()

    entry = {}
    for key in FIELD_ORDER:
        if key in fields and fields[key]:
            entry[key] = fields[key]

    # 有些包只写了 Package 没写 Name，补一个人能看的名字。
    entry.setdefault("Name", entry.get("Package", os.path.basename(deb_path)))
    entry.setdefault("Architecture", "iphoneos-arm64")
    entry.setdefault("Section", "Tweaks")

    entry["Filename"] = rel_path.replace(os.sep, "/")
    entry["Size"] = str(len(blob))
    if "Installed-Size" in fields:
        entry["Installed-Size"] = fields["Installed-Size"]
    entry["MD5sum"] = hashlib.md5(blob).hexdigest()
    entry["SHA1"] = hashlib.sha1(blob).hexdigest()
    entry["SHA256"] = hashlib.sha256(blob).hexdigest()

    for extra in ("Icon", "Sileodepiction", "Depiction", "Homepage"):
        if fields.get(extra):
            entry[extra] = fields[extra]

    return entry


def format_entry(entry):
    lines = []
    for key, value in entry.items():
        if key == "Description":
            parts = value.split("\n")
            lines.append("%s: %s" % (key, parts[0].strip()))
            for extra_line in parts[1:]:
                lines.append(" " + extra_line.strip())
        else:
            lines.append("%s: %s" % (key, value))
    return "\n".join(lines)


def main():
    if not os.path.isdir(DEBS_DIR):
        raise SystemExit("找不到 debs 目录：%s" % DEBS_DIR)

    debs = sorted(f for f in os.listdir(DEBS_DIR) if f.endswith(".deb"))
    if not debs:
        raise SystemExit("debs/ 里没有 .deb 文件")

    entries = []
    for filename in debs:
        path = os.path.join(DEBS_DIR, filename)
        rel = os.path.join("debs", filename)
        entries.append(build_entry(path, rel))
        print("  收录 %-52s %8d 字节" % (filename, os.path.getsize(path)))

    # 同一个包出现多个版本时，按版本号字符串倒序，新的在前（Sileo 取第一条）。
    entries.sort(key=lambda e: (e.get("Package", ""), e.get("Version", "")), reverse=True)

    packages_text = "\n\n".join(format_entry(e) for e in entries) + "\n"

    with open(os.path.join(ROOT, "Packages"), "w", encoding="utf-8", newline="\n") as fp:
        fp.write(packages_text)
    with gzip.open(os.path.join(ROOT, "Packages.gz"), "wb", 9) as fp:
        fp.write(packages_text.encode("utf-8"))

    archs = sorted({e.get("Architecture", "iphoneos-arm64") for e in entries})
    release = (
        "Origin: %s\n"
        "Label: %s\n"
        "Suite: stable\n"
        "Version: 1.0\n"
        "Codename: ios\n"
        "Architectures: %s\n"
        "Components: main\n"
        "Description: %s\n"
    ) % (REPO_ORIGIN, REPO_LABEL, " ".join(archs), REPO_DESCRIPTION)
    with open(os.path.join(ROOT, "Release"), "w", encoding="utf-8", newline="\n") as fp:
        fp.write(release)

    print("\n生成完成：%d 个包" % len(entries))
    print("  Packages      %d 字节" % len(packages_text.encode("utf-8")))
    print("  Packages.gz   %d 字节" % os.path.getsize(os.path.join(ROOT, "Packages.gz")))
    print("  Release       架构 %s" % " ".join(archs))


if __name__ == "__main__":
    main()
