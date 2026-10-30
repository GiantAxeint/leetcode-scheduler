import tempfile
from pathlib import Path

import pytest

from app import db, scheduler


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
    scheduler.build_plan(conn)
    n1 = conn.execute("SELECT COUNT(*) c FROM daily_plan").fetchone()["c"]
    scheduler.build_plan(conn)
    n2 = conn.execute("SELECT COUNT(*) c FROM daily_plan").fetchone()["c"]
    assert n1 == n2


def test_due_card_appears_in_plan(conn):
    cid = _add_card(conn)
    scheduler.build_plan(conn)
    rows = conn.execute("SELECT card_id FROM daily_plan WHERE card_id IS NOT NULL").fetchall()
    assert cid in [r["card_id"] for r in rows]


def test_gate_blocks_when_queue_full(conn):
    db.set_setting(conn, "queue_limit", 1)
    _add_card(conn)
    assert scheduler.gate(conn)["can_add_card"] is False


def test_full_queue_stops_new_problems(conn):
    db.set_setting(conn, "queue_limit", 0)
    conn.execute("INSERT INTO problem(title,status) VALUES('Two Sum','inbox')")
    conn.commit()
    scheduler.build_plan(conn)
    news = conn.execute("SELECT * FROM daily_plan WHERE problem_id IS NOT NULL").fetchall()
    assert news == []


def test_backlog_counts_unplanned_due(conn):
    for i in range(5):
        _add_card(conn, f"card{i}")
    db.set_setting(conn, "noon_due", 1)
    db.set_setting(conn, "evening_due", 1)
    db.set_setting(conn, "morning_new", 0)
    scheduler.build_plan(conn)
    assert scheduler.backlog(conn) == 3
