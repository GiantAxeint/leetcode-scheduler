"""SQLite 连接、建表、默认值、迁移。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config

SCHEMA_VERSION = 9

DEFAULT_SETTINGS = {
    # [甲版批注] 这个 15 只是「新库的初始值」，别拿它当当前配置。
    # 用户机器上的库里是 36（写在 settings 表里），是他按每天 3 道新题倒推出来的：
    # 名额 36 时每天到期约 3.7 道，整体控制在 1.5 小时内。
    # 我在交接文档里写过「在途（名额 36）」，丙版指出那句有歧义 —— 36 是数据，不是默认。
    # 用户明确要求保留在界面上手改数量的权限，所以：不要把这个默认值改成 36。
    "queue_limit": "15",          # 在途：当月要复习的不同题目上限（同一题多次复习算 1）
    "inbox_limit": "999",         # 待归纳区不设上限，保留字段做兼容
    "daily_minutes": "90",        # 每日时长上限（分钟）
    "interval_days": "1,3,7,15,30",
    "lapse_factor": "0.5",        # 答错时下次间隔打几折
    "daily_capacity": "6",        # 一天总共排几道（新题 + 到期），早午晚班只是把总量切开
    "interval_fuzz": "0.2",       # 间隔抖动幅度 ±20%，把同档位的题散到相邻几天
    "morning_new": "2",
    "skip_weekend": "1",          # 1=周末不排、日期遇周末顺延到下周一；0=周末照常参与复习
    # 下面两个已废弃：容量统一由 daily_capacity 管。
    # 保留键是为了不破坏老配置和交接文档里的引用，代码不再读取。
    "noon_due": "2",
    "evening_due": "1",
    "oral_mode": "1",             # 晚班口述模式开关
    # [甲版批注] oral_mode 是死配置：全仓库没有任何代码读它，只在这里躺着。
    # 我原来在交接文档里写「晚班只口述不写码只是个标签」，说得太轻 —— 实际情况
    # 是压根没实现。用户问过这件事，口头说过「只要个情绪价值就行」，没要求真做。
    # 删不删都行（他倾向保留），但别看到这个键就以为功能存在。
    "total_easy": "1094",         # 题库总数（用于进度环，可改）
    "total_medium": "2334",
    "total_hard": "1056",
    "close_action": "tray",       # 桌面版关窗行为：tray=缩托盘 / exit=直接退出
    # 壁纸（个性化）
    "wall_mode": "none",          # none | video | picture
    "wall_kind": "video",         # 播放方式：video 播放 / picture 轮播
    "wall_files": "[]",           # JSON 数组，存绝对路径；只放行这里列出的文件
    "wall_title": "",
    "wall_dim": "0.35",           # 白色遮罩强度，越大正文越清楚
    "wall_blur": "0",
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
    # [甲版批注] 顺序是这个函数唯一的考点：建表 → 灌默认值 → 迁移 → 建索引。
    # 别动。
    #
    # 我在这里挂过一次，全站 500，报错是 no such column: problem_id。
    # 起因：schema.sql 里给 review_log(problem_id) 建了索引，但 problem_id 这一列
    # 是 v5 迁移才加上去的。老库 executescript 跑到建索引就炸，异常抛出后
    # migrate() 根本没执行，于是所有接口全挂 —— 看起来像前端崩了，其实是建库顺序。
    #
    # 所以底下那个索引是「迁移之后、判断列存在再建」。以后加任何依赖新列的索引，
    # 都放这个位置，别图省事写回 schema.sql。
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
    if version < 8:
        # 「撤回」要精确还原判定前的状态：next_due 反推不出来，
        # solved 也可能是录题时就带着的（扩展抓的「已通过」），两者都必须当场记下来
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(review_log)")}
        if "next_due_before" not in cols:
            conn.execute("ALTER TABLE review_log ADD COLUMN next_due_before TEXT")
        if "solved_before" not in cols:
            conn.execute("ALTER TABLE review_log ADD COLUMN solved_before INTEGER")
    if version < 9:
        # 日历上的「分散」要能整批撤回，得记住每题从哪天挪到了哪天
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS disperse_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id   TEXT NOT NULL,
                problem_id INTEGER NOT NULL REFERENCES problem(id) ON DELETE CASCADE,
                from_date  TEXT NOT NULL,
                to_date    TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_disperse_batch ON disperse_log(batch_id);
            """
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
