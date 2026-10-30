"""SQLite 连接、建表、默认值、迁移。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config

SCHEMA_VERSION = 2

DEFAULT_SETTINGS = {
    "queue_limit": "30",          # 在途卡上限
    "inbox_limit": "20",          # 待归卡区上限
    "daily_minutes": "90",        # 每日时长上限（分钟）
    "interval_days": "1,3,7,15,30",
    "morning_new": "2",
    "noon_due": "2",
    "evening_due": "1",
    "pass_threshold": "0.8",      # 检验通过率阈值
    "hold_limit": "2",            # 连续几次持平整降一档
    "oral_mode": "1",             # 晚班口述模式开关
    "total_easy": "1094",         # 题库总数（用于进度环，可改）
    "total_medium": "2334",
    "total_hard": "1056",
}

DEFAULT_CHECKS = [
    ("冷启动", "不翻资料，说出核心步骤"),
    ("口述", "3 分钟讲清这步为什么不能省"),
    ("改条件", "换个约束能自己修好"),
    ("抽骨架", "写出不含题面细节的通用骨架"),
    ("反向题", "指出一个错解错在哪、什么数据能坑死它"),
]


def connect(root: Path | None = None) -> sqlite3.Connection:
    root = Path(root) if root else config.locate()
    config.ensure_dirs(root)
    conn = sqlite3.connect(config.db_path(root), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init(conn: sqlite3.Connection) -> None:
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.executescript(
        "BEGIN;"
        + "".join(
            f"INSERT OR IGNORE INTO settings(key,value) VALUES('{k}','{v}');"
            for k, v in DEFAULT_SETTINGS.items()
        )
        + "COMMIT;"
    )
    if conn.execute("SELECT COUNT(*) c FROM check_item").fetchone()["c"] == 0:
        conn.executemany(
            "INSERT INTO check_item(name,hint,sort_order) VALUES(?,?,?)",
            [(n, h, i) for i, (n, h) in enumerate(DEFAULT_CHECKS)],
        )
    migrate(conn)
    conn.commit()


def migrate(conn: sqlite3.Connection) -> None:
    """老库升级。新库建表时字段已齐，这里自然跳过。"""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 2:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(problem)")}
        if "solved" not in cols:
            conn.execute("ALTER TABLE problem ADD COLUMN solved INTEGER NOT NULL DEFAULT 0")
        if "solved_at" not in cols:
            conn.execute("ALTER TABLE problem ADD COLUMN solved_at TEXT")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def get_int(conn: sqlite3.Connection, key: str, default: int = 0) -> int:
    try:
        return int(get_setting(conn, key, str(default)))
    except (TypeError, ValueError):
        return default


def get_float(conn: sqlite3.Connection, key: str, default: float = 0.0) -> float:
    try:
        return float(get_setting(conn, key, str(default)))
    except (TypeError, ValueError):
        return default


def set_setting(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


def intervals(conn: sqlite3.Connection) -> list[int]:
    raw = get_setting(conn, "interval_days", "1,3,7,15,30")
    out: list[int] = []
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if part.isdigit() and int(part) > 0:
            out.append(int(part))
    return out or [1, 3, 7, 15, 30]
