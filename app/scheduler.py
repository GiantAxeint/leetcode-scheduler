"""调度：谁到期、闸门开不开、今天排哪几道。"""

from __future__ import annotations

import sqlite3
from datetime import date

from . import db


def today_str() -> str:
    return date.today().isoformat()


def due_cards(conn: sqlite3.Connection, today: str | None = None) -> list[sqlite3.Row]:
    """到期且未归档的卡，越逾期越靠前。"""
    today = today or today_str()
    return conn.execute(
        """
        SELECT c.id, c.title, c.category, c.status,
               s.stage, s.next_due, s.pass_streak, s.hold_count, s.fail_count
        FROM card_state s
        JOIN pattern_card c ON c.id = s.card_id
        WHERE c.status = 'active' AND s.next_due <= ?
        ORDER BY s.next_due ASC, s.stage ASC
        """,
        (today,),
    ).fetchall()


def gate(conn: sqlite3.Connection) -> dict:
    """两道闸门的状态。"""
    limit = db.get_int(conn, "queue_limit", 30)
    inbox_limit = db.get_int(conn, "inbox_limit", 20)
    active = conn.execute(
        "SELECT COUNT(*) c FROM pattern_card WHERE status='active'"
    ).fetchone()["c"]
    inbox = conn.execute(
        "SELECT COUNT(*) c FROM problem WHERE status='inbox'"
    ).fetchone()["c"]
    return {
        "active": active,
        "active_limit": limit,
        "inbox": inbox,
        "inbox_limit": inbox_limit,
        "can_add_card": active < limit,
        "can_add_problem": inbox < inbox_limit,
    }


def build_plan(conn: sqlite3.Connection, today: str | None = None) -> None:
    """当天计划只生成一次；已有就跳过。"""
    today = today or today_str()
    exists = conn.execute(
        "SELECT 1 FROM daily_plan WHERE plan_date=? LIMIT 1", (today,)
    ).fetchone()
    if exists:
        return

    n_new = db.get_int(conn, "morning_new", 2)
    n_noon = db.get_int(conn, "noon_due", 2)
    n_even = db.get_int(conn, "evening_due", 1)
    g = gate(conn)

    due = list(due_cards(conn, today))
    used_cards: set[int] = set()
    rows: list[tuple] = []

    # 早班：先排待归卡区的题；闸门关了就改成复习到期卡
    placed_new = 0
    if g["can_add_card"]:
        news = conn.execute(
            "SELECT id FROM problem WHERE status='inbox' ORDER BY created_at ASC LIMIT ?",
            (n_new,),
        ).fetchall()
        for r in news:
            rows.append((today, "morning", None, r["id"]))
            placed_new += 1

    for slot, need in (("morning", n_new - placed_new), ("noon", n_noon), ("evening", n_even)):
        for row in due:
            if need <= 0:
                break
            if row["id"] in used_cards:
                continue
            rows.append((today, slot, row["id"], None))
            used_cards.add(row["id"])
            need -= 1

    conn.executemany(
        "INSERT INTO daily_plan(plan_date, slot, card_id, problem_id) VALUES(?,?,?,?)",
        rows,
    )
    conn.commit()


def backlog(conn: sqlite3.Connection, today: str | None = None) -> int:
    """今天排不下、被迫顺延的到期卡数量。"""
    today = today or today_str()
    total = len(due_cards(conn, today))
    planned = conn.execute(
        "SELECT COUNT(*) c FROM daily_plan WHERE plan_date=? AND card_id IS NOT NULL",
        (today,),
    ).fetchone()["c"]
    return max(0, total - planned)
