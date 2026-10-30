"""生成 Windows .lnk 快捷方式（纯二进制，不依赖 COM / pywin32）。

用法：
    python tools/make_shortcut.py <目标exe> <工作目录> <快捷方式.lnk> [描述]

直接按 [MS-SHLLINK] 规范拼字节，免去 pywin32 依赖；
中文路径同时写 ANSI(GBK) 与 Unicode 两份，兼容不同区域设置。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

CLSID = bytes.fromhex("0114020000000000C000000000000046")


def _ansi(s: str) -> bytes:
    return s.encode("gbk", errors="replace") + b"\x00"


def _uni(s: str) -> bytes:
    return s.encode("utf-16-le") + b"\x00\x00"


def build(target: Path, workdir: Path, desc: str = "") -> bytes:
    t, w = str(target), str(workdir)

    vol = struct.pack("<IIII", 0x11, 3, 0, 0x10) + b"\x00"  # 17 字节，空卷标
    lbp, cps = _ansi(t), b"\x00"
    lbp_u, cps_u = _uni(t), b"\x00\x00"

    hdr_size = 0x24
    off_vol = hdr_size
    off_lbp = off_vol + len(vol)
    off_cps = off_lbp + len(lbp)
    off_lbp_u = off_cps + len(cps)
    off_cps_u = off_lbp_u + len(lbp_u)
    total = off_cps_u + len(cps_u)

    linkinfo = struct.pack(
        "<IIIIIII", total, hdr_size, 0x1, off_vol, off_lbp, 0, off_cps
    )
    linkinfo += struct.pack("<II", off_lbp_u, off_cps_u)
    linkinfo += vol + lbp + cps + lbp_u + cps_u

    flags = 0x02 | 0x80 | 0x10  # HasLinkInfo | IsUnicode | HasWorkingDir
    head = struct.pack("<I", 0x4C) + CLSID
    head += struct.pack("<II", flags, 0x20)      # flags + FILE_ATTRIBUTE_ARCHIVE
    head += b"\x00" * 24                         # 三个时间戳
    head += struct.pack("<I", 0)                 # FileSize（目标非文件时 0）
    head += struct.pack("<i", 0)                 # IconIndex
    head += struct.pack("<I", 1)                 # SW_SHOWNORMAL
    head += struct.pack("<HH", 0, 0)             # HotKey + Reserved
    head += struct.pack("<II", 0, 0)             # Reserved2/3

    sdata = struct.pack("<H", len(w)) + w.encode("utf-16-le")
    extra = struct.pack("<II", 4, 0)
    return head + linkinfo + sdata + extra


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    target, workdir, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    blob = build(target, workdir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    print(f"{out}  {len(blob)} bytes  -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
