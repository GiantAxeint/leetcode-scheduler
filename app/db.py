"""SQLite 连接、建表、默认值、迁移。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config

SCHEMA_VERSION = 7

DEFAULT_SETTINGS = {
    "queue_limit": "15",          # 在途：当月要复习的不同题目上限（同一题多次复习算 1）
    "inbox_limit": "999",         # 待归纳区不设上限，保留字段做兼容
    "daily_minutes": "90",        # 每日时长上限（分钟）
    "interval_days": "1,3,7,15,30",
    "lapse_factor": "0.5",        # 答错时下次间隔打几折
    "morning_new": "2",
    "noon_due": "2",
    "evening_due": "1",
    "oral_mode": "1",             # 晚班口述模式开关
    "total_easy": "1094",         # 题库总数（用于进度环，可改）
    "total_medium": "2334",
    "total_hard": "1056",
    "close_action": "tray",       # 桌面版关窗行为：tray=缩托盘 / exit=直接退出
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
    # 依赖新列的索引放在迁移之后建，否则老库会在 executescript 阶段就炸
    if "problem_id" in {r["name"] for r in conn.execute("PRAGMA table_info(review_log)")}:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_review_problem "
            "ON review_log(problem_id, reviewed_at)"
        )
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
    if version < 3:
        # 在途语义从「卡数」改成「不同题目数」，老库的 30 是旧口径，换成新默认 15
        row = conn.execute("SELECT value FROM settings WHERE key='queue_limit'").fetchone()
        if row and row["value"] == "30":
            conn.execute("UPDATE settings SET value='15' WHERE key='queue_limit'")
        conn.execute("UPDATE settings SET value='999' WHERE key='inbox_limit' AND value='20'")
    if version < 4:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(problem)")}
        if "phase" not in cols:
            conn.execute("ALTER TABLE problem ADD COLUMN phase TEXT NOT NULL DEFAULT 'queued'")
        if "order_no" not in cols:
            conn.execute("ALTER TABLE problem ADD COLUMN order_no INTEGER")
        # 老数据全按「在途」处理，超出的进候补
        limit = int(
            (conn.execute("SELECT value FROM settings WHERE key='queue_limit'").fetchone()
             or {"value": "15"})["value"]
        )
        ids = [r["id"] for r in conn.execute("SELECT id FROM problem ORDER BY id")]
        for i, pid in enumerate(ids):
            conn.execute(
                "UPDATE problem SET phase=? WHERE id=?",
                ("active" if i < limit else "queued", pid),
            )
    if version < 5:
        # 调度单位从「题型卡」改成「题」：题自己带 stage / next_due
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(problem)")}
        for name, ddl in (
            ("stage", "ALTER TABLE problem ADD COLUMN stage INTEGER NOT NULL DEFAULT 0"),
            ("next_due", "ALTER TABLE problem ADD COLUMN next_due TEXT"),
            ("last_reviewed_at", "ALTER TABLE problem ADD COLUMN last_reviewed_at TEXT"),
            ("pass_streak", "ALTER TABLE problem ADD COLUMN pass_streak INTEGER NOT NULL DEFAULT 0"),
            ("fail_count", "ALTER TABLE problem ADD COLUMN fail_count INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in cols:
                conn.execute(ddl)

        # review_log 允许挂在题上（card_id 可空）
        rcols = {r["name"] for r in conn.execute("PRAGMA table_info(review_log)")}
        if "problem_id" not in rcols:
            conn.executescript(
                """
                CREATE TABLE review_log_v5 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    problem_id INTEGER REFERENCES problem(id) ON DELETE CASCADE,
                    card_id INTEGER REFERENCES pattern_card(id) ON DELETE CASCADE,
                    reviewed_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    result TEXT NOT NULL,
                    stage_from INTEGER,
                    stage_to INTEGER,
                    passed INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0,
                    checks_json TEXT NOT NULL DEFAULT '[]',
                    error_type TEXT,
                    duration_sec INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT ''
                );
                INSERT INTO review_log_v5
                    (id, card_id, reviewed_at, result, stage_from, stage_to,
                     passed, total, checks_json, error_type, duration_sec, note)
                SELECT id, card_id, reviewed_at, result, stage_from, stage_to,
                       passed, total, checks_json, error_type, duration_sec, note
                FROM review_log;
                DROP TABLE review_log;
                ALTER TABLE review_log_v5 RENAME TO review_log;
                CREATE INDEX IF NOT EXISTS idx_review_problem ON review_log(problem_id, reviewed_at);
                CREATE INDEX IF NOT EXISTS idx_review_card ON review_log(card_id, reviewed_at);
                """
            )

        # 老题继承所属卡的档位和到期日
        conn.execute(
            """
            UPDATE problem SET
                stage = COALESCE((
                    SELECT s.stage FROM card_problem cp
                    JOIN card_state s ON s.card_id = cp.card_id
                    JOIN pattern_card c ON c.id = cp.card_id AND c.status = 'active'
                    WHERE cp.problem_id = problem.id
                    ORDER BY s.stage DESC LIMIT 1), 0),
                next_due = COALESCE((
                    SELECT s.next_due FROM card_problem cp
                    JOIN card_state s ON s.card_id = cp.card_id
                    JOIN pattern_card c ON c.id = cp.card_id AND c.status = 'active'
                    WHERE cp.problem_id = problem.id
                    ORDER BY s.next_due ASC LIMIT 1), date('now','localtime'))
            WHERE phase = 'active'
            """
        )
        conn.execute(
            "UPDATE problem SET next_due = date('now','localtime') WHERE next_due IS NULL"
        )
    if version < 6:
        # 旧版的「标记完成」不含判定信息，把还没判过的那些复位，重新出现在今日任务里
        conn.execute(
            "UPDATE daily_plan SET done=0 "
            "WHERE problem_id IS NOT NULL AND problem_id IN "
            "(SELECT id FROM problem WHERE stage=0)"
        )
    if version < 7:
        # 复习只排周一到周五，落在周末的到期日顺延到下一个工作日
        from datetime import date as _date, timedelta as _td

        def _next_workday(d):
            while d.weekday() >= 5:
                d += _td(days=1)
            return d.isoformat()

        for r in conn.execute(
            "SELECT id, next_due FROM problem WHERE next_due IS NOT NULL"
        ).fetchall():
            d = _date.fromisoformat(r["next_due"])
            if d.weekday() >= 5:
                conn.execute(
                    "UPDATE problem SET next_due=? WHERE id=?", (_next_workday(d), r["id"])
                )
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
