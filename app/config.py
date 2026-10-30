"""数据根定位。

硬要求：数据根可以整体搬到任意盘（D:\\MyNote\\... → E:\\...），搬家后软件照常启动，
不报错、不丢数据。做法是「指针 + 多级回退」，任何一级失效都往下一级退，绝不抛异常。

定位顺序：
  1. 环境变量 SCJ_DATA_ROOT
  2. 程序目录旁 data_root.txt（单行绝对路径）
  3. %APPDATA%/刷题调度/data_root.txt（程序目录只读时的备选）
  4. 内置默认 D:\\MyNote\\SelfNote\\算法题目
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "刷题调度"
POINTER_NAME = "data_root.txt"
DEFAULT_ROOT = Path(r"D:\MyNote\SelfNote\算法题目")

DB_NAME = "刷题调度.db"
BACKUP_DIR = "backup"
CARD_EXPORT_DIR = "cards"


def app_dir() -> Path:
    """程序目录：打包后在 exe 同级，开发时在项目根。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _pointer_candidates() -> list[Path]:
    cands = [app_dir() / POINTER_NAME]
    appdata = os.environ.get("APPDATA")
    if appdata:
        cands.append(Path(appdata) / APP_NAME / POINTER_NAME)
    return cands


def _read_pointer(p: Path) -> Path | None:
    try:
        if not p.is_file():
            return None
        raw = p.read_text(encoding="utf-8").strip().strip('"')
        if not raw:
            return None
        return Path(raw).expanduser()
    except OSError:
        return None


def _usable(p: Path | None) -> bool:
    """目录存在且可写才算可用。不存在但父目录在，也算可用（会自动创建）。"""
    if p is None:
        return False
    try:
        if p.is_dir():
            probe = p / ".write_probe"
            probe.write_text("1", encoding="utf-8")
            probe.unlink()
            return True
        return p.parent.is_dir()
    except OSError:
        return False


def locate() -> Path:
    env = os.environ.get("SCJ_DATA_ROOT")
    if env:
        p = Path(env).expanduser()
        if _usable(p):
            return p

    for ptr in _pointer_candidates():
        p = _read_pointer(ptr)
        if _usable(p):
            return p  # type: ignore[return-value]

    return DEFAULT_ROOT


def save_pointer(root: Path) -> Path | None:
    """把数据根写进指针文件，返回实际写入的位置（都可能失败时返回 None）。"""
    text = str(Path(root).resolve())
    for ptr in _pointer_candidates():
        try:
            ptr.parent.mkdir(parents=True, exist_ok=True)
            ptr.write_text(text + "\n", encoding="utf-8")
            return ptr
        except OSError:
            continue
    return None


def ensure_dirs(root: Path) -> None:
    for sub in ("", BACKUP_DIR, CARD_EXPORT_DIR):
        (root / sub).mkdir(parents=True, exist_ok=True)


def db_path(root: Path) -> Path:
    return Path(root) / DB_NAME
