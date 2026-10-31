import tempfile
from pathlib import Path

import pytest

from app import db
from app import scheduler

WORKDAY = "2026-10-30"   # 周五，测试固定用工作日


@pytest.fixture()
def conn():
    tmp = Path(tempfile.mkdtemp())
    c = db.connect(tmp)
    db.init(c)
    yield c
    c.close()


def _add_card(c, title="滑动窗口"):
    cur = c.execute(
        "INSERT INTO pattern_card(title,trigger_signals) VALUES(?,?)", (title, "连续子数组")
    )
    cid = cur.lastrowid
    c.execute(
        "INSERT INTO card_state(card_id,stage,next_due) VALUES(?,1,?)",
        (cid, scheduler.today_str()),
    )
    c.commit()
    return cid


def test_plan_generated_once(conn):
    _add_card(conn)
    scheduler.build_plan(conn, WORKDAY)
    n1 = conn.execute("SELECT COUNT(*) c FROM daily_plan").fetchone()["c"]
    scheduler.build_plan(conn, WORKDAY)
    n2 = conn.execute("SELECT COUNT(*) c FROM daily_plan").fetchone()["c"]
    assert n1 == n2


def _add_problem(c, title, stage=0, next_due=None, phase="active"):
    return c.execute(
        "INSERT INTO problem(title,phase,stage,next_due) VALUES(?,?,?,?)",
        (title, phase, stage, next_due),
    ).lastrowid


def test_due_problem_appears_in_plan(conn):
    pid = _add_problem(conn, "题A", stage=3, next_due=WORKDAY)
    scheduler.build_plan(conn, WORKDAY)
    rows = conn.execute("SELECT problem_id FROM daily_plan").fetchall()
    assert pid in [r["problem_id"] for r in rows]


def test_fresh_problem_goes_to_morning(conn):
    pid = _add_problem(conn, "新题", stage=0, next_due=None)
    scheduler.build_plan(conn, WORKDAY)
    row = conn.execute(
        "SELECT slot FROM daily_plan WHERE problem_id=?", (pid,)
    ).fetchone()
    assert row["slot"] == "morning"


def test_advance_first_correct_jumps_to_three_days():
    stage, due, out = scheduler.advance(0, True)
    assert stage == 2 and out is False
    assert due == scheduler.add_days(3)


def test_advance_first_wrong_stays_one_day():
    stage, due, out = scheduler.advance(0, False)
    assert stage == 1 and due == scheduler.add_days(1)


def test_advance_wrong_steps_down_and_halves():
    # 15 天档答错 → 退到 7 天档，且在 7 天上打五折 ≈ 4 天
    stage, due, _ = scheduler.advance(4, False)
    assert stage == 3
    assert due == scheduler.add_days(4)


def test_advance_wrong_at_top_keeps_progress():
    # 30 天档答错不会归零，只退到 15 天档的一半
    stage, due, _ = scheduler.advance(5, False)
    assert stage == 4
    assert due == scheduler.add_days(8)


def test_advance_last_stage_correct_leaves_pool():
    stage, due, out = scheduler.advance(5, True)
    assert out is True and due is None


def _link(c, cid, title="Two Sum", phase="active"):
    pid = c.execute(
        "INSERT INTO problem(title,status,phase) VALUES(?, 'linked', ?)", (title, phase)
    ).lastrowid
    c.execute("INSERT INTO card_problem(card_id,problem_id) VALUES(?,?)", (cid, pid))
    c.commit()
    return pid


def test_gate_counts_distinct_problems(conn):
    cid = _add_card(conn)
    _link(conn, cid)
    db.set_setting(conn, "queue_limit", 1)
    g = scheduler.gate(conn)
    assert g["active"] == 1
    assert g["can_add"] is False


def test_queued_problem_not_in_flight(conn):
    cid = _add_card(conn)
    _link(conn, cid, phase="queued")
    g = scheduler.gate(conn)
    assert g["active"] == 0
    assert g["queued"] == 1


def test_refill_pulls_from_queue_head(conn):
    db.set_setting(conn, "queue_limit", 1)
    for t in ("A", "B", "C"):
        conn.execute("INSERT INTO problem(title,phase) VALUES(?, 'queued')", (t,))
    conn.commit()
    assert scheduler.refill(conn) == 1
    g = scheduler.gate(conn)
    assert g["active"] == 1 and g["queued"] == 2


def test_queue_order_is_respected(conn):
    ids = []
    for t in ("A", "B", "C"):
        ids.append(conn.execute(
            "INSERT INTO problem(title,phase) VALUES(?, 'queued')", (t,)
        ).lastrowid)
    conn.commit()
    scheduler.queue_order(conn, [ids[2], ids[0], ids[1]])
    rows = scheduler.queued_problems(conn)
    assert [r["title"] for r in rows] == ["C", "A", "B"]


def test_archived_card_problem_leaves_flight(conn):
    cid = _add_card(conn)
    pid = _link(conn, cid)
    conn.execute("UPDATE problem SET phase='done' WHERE id=?", (pid,))
    conn.commit()
    assert scheduler.gate(conn)["active"] == 0


def test_full_queue_stops_new_problems(conn):
    db.set_setting(conn, "queue_limit", 0)
    conn.execute("INSERT INTO problem(title,status) VALUES('Two Sum','inbox')")
    conn.commit()
    scheduler.build_plan(conn, WORKDAY)
    news = conn.execute("SELECT * FROM daily_plan WHERE problem_id IS NOT NULL").fetchall()
    assert news == []


def test_backlog_counts_unplanned_due(conn):
    for i in range(5):
        _add_problem(conn, f"p{i}", stage=2, next_due=WORKDAY)
    db.set_setting(conn, "noon_due", 1)
    db.set_setting(conn, "evening_due", 1)
    db.set_setting(conn, "morning_new", 0)
    scheduler.build_plan(conn, WORKDAY)
    assert scheduler.backlog(conn, WORKDAY) == 3


def test_add_days_skips_weekend():
    # 2026-10-30 是周五
    assert scheduler.add_days(1, "2026-10-30") == "2026-11-02"   # 周六 → 周一
    assert scheduler.add_days(3, "2026-10-30") == "2026-11-02"
    assert scheduler.add_days(7, "2026-10-30") == "2026-11-06"


def test_build_plan_skips_weekend(conn):
    _add_problem(conn, "周末题", stage=2, next_due="2026-10-31")
    scheduler.build_plan(conn, "2026-10-31")      # 周六
    assert conn.execute("SELECT COUNT(*) c FROM daily_plan").fetchone()["c"] == 0


def test_recycle_stale_returns_to_queue(conn):
    from datetime import date, timedelta

    old = (date.fromisoformat(WORKDAY) - timedelta(days=8)).isoformat()
    pid = _add_problem(conn, "老题", stage=2, next_due=old)
    db.set_setting(conn, "queue_limit", 5)
    assert scheduler.recycle_stale(conn, WORKDAY) == 1
    row = conn.execute("SELECT phase FROM problem WHERE id=?", (pid,)).fetchone()
    assert row["phase"] == "queued"


def test_recycle_does_not_pull_itself_back(conn):
    from datetime import date, timedelta

    old = (date.fromisoformat(WORKDAY) - timedelta(days=8)).isoformat()
    pid = _add_problem(conn, "老题", stage=2, next_due=old)
    db.set_setting(conn, "queue_limit", 5)
    scheduler.recycle_stale(conn, WORKDAY)
    row = conn.execute("SELECT phase FROM problem WHERE id=?", (pid,)).fetchone()
    assert row["phase"] == "queued"
