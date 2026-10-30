"""把主程序挂到开机启动（最小化、不弹浏览器）。

用法：
    python tools/install_autostart.py <exe路径>            安装
    python tools/install_autostart.py <exe路径> --remove   卸载
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from make_shortcut import build


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise SystemExit("找不到 APPDATA")
    return Path(appdata) / r"Microsoft\Windows\Start Menu\Programs\Startup"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    exe = Path(sys.argv[1]).resolve()
    lnk = startup_dir() / "刷题调度.lnk"

    if "--remove" in sys.argv:
        lnk.unlink(missing_ok=True)
        print(f"已移除开机启动：{lnk}")
        return 0

    if not exe.is_file():
        print(f"找不到 exe：{exe}")
        return 1

    lnk.parent.mkdir(parents=True, exist_ok=True)
    lnk.write_bytes(build(exe, exe.parent, args="--silent", minimized=True))
    print(f"已加入开机启动：{lnk}")
    print(f"目标：{exe} --silent（最小化，不弹浏览器）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
