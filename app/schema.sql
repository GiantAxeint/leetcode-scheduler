-- 刷题调度 · schema v1
-- 所有日期均为本地日期字符串 YYYY-MM-DD

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS problem (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    leetcode_id INTEGER,
    slug        TEXT,
    title       TEXT NOT NULL,
    difficulty  TEXT,
    url         TEXT,
    tags        TEXT,
    status      TEXT NOT NULL DEFAULT 'inbox',      -- inbox | linked（归纳维度）
    phase       TEXT NOT NULL DEFAULT 'queued',     -- queued | active | done（进度维度）
    order_no    INTEGER,                            -- 候补队列顺序，空则按 id
    stage       INTEGER NOT NULL DEFAULT 0,         -- 0 未复习 / 1..5 对应 1,3,7,15,30 天
    next_due    TEXT,                               -- 下次该复习的日期
    last_reviewed_at TEXT,
    pass_streak INTEGER NOT NULL DEFAULT 0,
    fail_count  INTEGER NOT NULL DEFAULT 0,
    solved      INTEGER NOT NULL DEFAULT 0,
    solved_at   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_problem_lc
    ON problem(leetcode_id) WHERE leetcode_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_problem_status ON problem(status);

CREATE TABLE IF NOT EXISTS pattern_card (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    trigger_signals TEXT NOT NULL DEFAULT '',
    essence         TEXT NOT NULL DEFAULT '',
    skeleton_code   TEXT NOT NULL DEFAULT '',
    pitfalls        TEXT NOT NULL DEFAULT '',
    complexity      TEXT NOT NULL DEFAULT '',
    variants        TEXT NOT NULL DEFAULT '',
    category        TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'active', -- active | archived
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS card_problem (
    card_id           INTEGER NOT NULL REFERENCES pattern_card(id) ON DELETE CASCADE,
    problem_id        INTEGER NOT NULL REFERENCES problem(id) ON DELETE CASCADE,
    is_representative INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (card_id, problem_id)
);

CREATE TABLE IF NOT EXISTS card_state (
    card_id      INTEGER PRIMARY KEY REFERENCES pattern_card(id) ON DELETE CASCADE,
    stage        INTEGER NOT NULL DEFAULT 1,
    next_due     TEXT NOT NULL,
    pass_streak  INTEGER NOT NULL DEFAULT 0,
    hold_count   INTEGER NOT NULL DEFAULT 0,   -- 连续持平次数
    fail_count   INTEGER NOT NULL DEFAULT 0,
    archived_at  TEXT
);

CREATE TABLE IF NOT EXISTS check_item (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    hint       TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS review_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id  INTEGER REFERENCES problem(id) ON DELETE CASCADE,
    card_id     INTEGER REFERENCES pattern_card(id) ON DELETE CASCADE,
    reviewed_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    result      TEXT NOT NULL,                      -- correct | wrong | skip
    stage_from  INTEGER,
    stage_to    INTEGER,
    passed      INTEGER NOT NULL DEFAULT 0,
    total       INTEGER NOT NULL DEFAULT 0,
    checks_json TEXT NOT NULL DEFAULT '[]',
    error_type  TEXT,                               -- knowledge | index | execution
    duration_sec INTEGER NOT NULL DEFAULT 0,
    note        TEXT NOT NULL DEFAULT '',
    next_due_before TEXT,                           -- 判定前的到期日，供「撤回」精确还原
    solved_before   INTEGER                         -- 判定前的 solved 标记，同上
);

CREATE INDEX IF NOT EXISTS idx_review_card ON review_log(card_id, reviewed_at);

CREATE TABLE IF NOT EXISTS daily_plan (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date  TEXT NOT NULL,
    slot       TEXT NOT NULL,                       -- morning | noon | evening
    card_id    INTEGER REFERENCES pattern_card(id) ON DELETE CASCADE,
    problem_id INTEGER REFERENCES problem(id) ON DELETE CASCADE,
    done       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_plan_date ON daily_plan(plan_date, slot);
