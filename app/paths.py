"""打包后的资源定位。

为什么单独一个模块：数据目录由 config.py 管（用户数据可以搬到任意盘），
但**程序自带资源**（图标、schema.sql）是另一套规则——
PyInstaller onedir 会把 datas 塞进 `_internal/`，此时 `sys._MEIPASS` 指向那里；
开发态则是项目根。两种形态必须靠同一个函数收敛，否则各处自己拼
`if getattr(sys,'frozen')` 迟早写歪一个。
"""

from __future__ import annotations

import sys
from pathlib import Path


def base_dir() -> Path:
    """程序自带资源所在的根。"""
    if getattr(sys, "frozen", False):
        # onedir 模式下 PyInstaller 会设 _MEIPASS 指向 _internal
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource(*parts: str) -> Path:
    return base_dir().joinpath(*parts)


def asset(name: str) -> Path:
    return resource("assets", name)


def icon_path() -> Path | None:
    """窗口/托盘图标。找不到就返回 None，调用方自己兜底，不许抛异常。"""
    for name in ("icon.ico", "icon.png"):
        p = asset(name)
        if p.is_file():
            return p
    return None


def extension_dir() -> Path:
    """随程序分发的 Edge 扩展目录（打包时一起带出去）。"""
    return resource("extension")
