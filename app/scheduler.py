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


_INFLIGHT_SQL = """
    SELECT p.id, p.leetcode_id, p.title, p.difficulty, p.tags, p.status, p.url, p.slug,
           MIN(s.next_due) AS next_due, MAX(s.stage) AS stage
    FROM problem p
    LEFT JOIN card_problem cp ON cp.problem_id = p.id
    LEFT JOIN pattern_card c  ON c.id = cp.card_id AND c.status = 'active'
    LEFT JOIN card_state  s   ON s.card_id = c.id
    WHERE p.status = 'inbox'
       OR EXISTS (SELECT 1 FROM card_problem cp2
                  JOIN pattern_card c2 ON c2.id = cp2.card_id
                  WHERE cp2.problem_id = p.id AND c2.status = 'active')
    GROUP BY p.id
"""


def inflight_problems(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """在途 = 当月还要复习的不同题目（同一题复习多少次都算 1 题）。"""
    return conn.execute(
        _INFLIGHT_SQL + " ORDER BY (next_due IS NULL), next_due ASC, p.id DESC"
    ).fetchall()


def gate(conn: sqlite3.Connection) -> dict:
    """容量闸门：按「不同题目数」算，不按复习次数算。"""
    limit = db.get_int(conn, "queue_limit", 15)
    active = conn.execute(f"SELECT COUNT(*) c FROM ({_INFLIGHT_SQL})").fetchone()["c"]
    inbox = conn.execute(
        "SELECT COUNT(*) c FROM problem WHERE status='inbox'"
    ).fetchone()["c"]
    return {
        "active": active,
        "active_limit": limit,
        "inbox": inbox,
        "can_add": active < limit,
        "can_add_card": True,       # 题型卡本身不再设上限
        "can_add_problem": active < limit,
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

    # 早班：先排待归纳区的题；在途满了就改成复习到期卡
    placed_new = 0
    if g["can_add_problem"]:
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


def backlog_cards(conn: sqlite3.Connection, today: str | None = None) -> list[sqlite3.Row]:
    """今天到期、但没排进今日计划的卡（积压）。"""
    today = today or today_str()
    return conn.execute(
        """
        SELECT c.id AS card_id, c.title, c.category, s.next_due, s.stage
        FROM card_state s
        JOIN pattern_card c ON c.id = s.card_id
        WHERE c.status = 'active' AND s.next_due <= ?
          AND c.id NOT IN (
              SELECT card_id FROM daily_plan
              WHERE plan_date = ? AND card_id IS NOT NULL
          )
        ORDER BY s.next_due ASC
        """,
        (today, today),
    ).fetchall()


def backlog(conn: sqlite3.Connection, today: str | None = None) -> int:
    return len(backlog_cards(conn, today))
