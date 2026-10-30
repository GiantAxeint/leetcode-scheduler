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
