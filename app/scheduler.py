"""调度：谁到期、闸门开不开、今天排哪几道。"""

from __future__ import annotations

import sqlite3
from datetime import date

from . import db

# 题目的三个进度态：
#   queued 候补——录进来了但没轮到，不参与排班
#   active 在途——正在复习循环里，每天可能被排到
#   done   完成——题型复习满 5 档归档，退出循环


def today_str() -> str:
    return date.today().isoformat()


def due_cards(conn: sqlite3.Connection, today: str | None = None) -> list[sqlite3.Row]:
    """到期且未归档的卡。

    一张卡挂的题如果全在候补，这张卡不该被排进来 —— 所以要求
    「卡没挂题」或「卡上至少有一道在途题」。
    """
    today = today or today_str()
    return conn.execute(
        """
        SELECT c.id, c.title, c.category, c.status,
               s.stage, s.next_due, s.pass_streak, s.hold_count, s.fail_count
        FROM card_state s
        JOIN pattern_card c ON c.id = s.card_id
        WHERE c.status = 'active' AND s.next_due <= ?
          AND (
                NOT EXISTS (SELECT 1 FROM card_problem cp WHERE cp.card_id = c.id)
             OR EXISTS (SELECT 1 FROM card_problem cp
                        JOIN problem p ON p.id = cp.problem_id
                        WHERE cp.card_id = c.id AND p.phase = 'active')
          )
        ORDER BY s.next_due ASC, s.stage ASC
        """,
        (today,),
    ).fetchall()


_INFLIGHT_SQL = """
    SELECT p.id, p.leetcode_id, p.title, p.difficulty, p.tags, p.status, p.url, p.slug,
           p.phase, p.order_no,
           MIN(s.next_due) AS next_due, MAX(s.stage) AS stage
    FROM problem p
    LEFT JOIN card_problem cp ON cp.problem_id = p.id
    LEFT JOIN pattern_card c  ON c.id = cp.card_id AND c.status = 'active'
    LEFT JOIN card_state  s   ON s.card_id = c.id
    WHERE p.phase = 'active'
    GROUP BY p.id
"""


def inflight_problems(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        _INFLIGHT_SQL + " ORDER BY (next_due IS NULL), next_due ASC, p.id DESC"
    ).fetchall()


def queued_problems(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """候补队列，按手动顺序，没排过序的按录入先后。"""
    return conn.execute(
        """
        SELECT p.id, p.leetcode_id, p.title, p.difficulty, p.tags, p.status, p.url, p.slug,
               p.phase, p.order_no, p.created_at,
               (SELECT GROUP_CONCAT(c.title, '、') FROM card_problem cp
                JOIN pattern_card c ON c.id = cp.card_id
                WHERE cp.problem_id = p.id) AS cards
        FROM problem p
        WHERE p.phase = 'queued'
        ORDER BY COALESCE(p.order_no, 999999), p.id
        """
    ).fetchall()


def queue_order(conn: sqlite3.Connection, ids: list[int]) -> None:
    """按传入顺序重写候补队列序号。"""
    for i, pid in enumerate(ids):
        conn.execute(
            "UPDATE problem SET order_no=? WHERE id=? AND phase='queued'", (i, pid)
        )
    conn.commit()


def refill(conn: sqlite3.Connection) -> int:
    """在途空出几个名额，就从候补队头补几个。"""
    limit = db.get_int(conn, "queue_limit", 15)
    active = conn.execute(
        "SELECT COUNT(*) c FROM problem WHERE phase='active'"
    ).fetchone()["c"]
    need = limit - active
    if need <= 0:
        return 0
    rows = conn.execute(
        "SELECT id FROM problem WHERE phase='queued' "
        "ORDER BY COALESCE(order_no, 999999), id LIMIT ?",
        (need,),
    ).fetchall()
    for r in rows:
        conn.execute("UPDATE problem SET phase='active' WHERE id=?", (r["id"],))
    if rows:
        conn.commit()
    return len(rows)


def gate(conn: sqlite3.Connection) -> dict:
    """在途 / 候补 / 待归纳 / 积压 四个数。"""
    limit = db.get_int(conn, "queue_limit", 15)
    active = conn.execute(
        "SELECT COUNT(*) c FROM problem WHERE phase='active'"
    ).fetchone()["c"]
    queued = conn.execute(
        "SELECT COUNT(*) c FROM problem WHERE phase='queued'"
    ).fetchone()["c"]
    inbox = conn.execute(
        "SELECT COUNT(*) c FROM problem WHERE status='inbox'"
    ).fetchone()["c"]
    return {
        "active": active,
        "active_limit": limit,
        "queued": queued,
        "inbox": inbox,
        "can_add": active < limit,
        "can_add_card": True,
        "can_add_problem": True,     # 满了就进候补，不再拒收
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

    due = list(due_cards(conn, today))
    used_cards: set[int] = set()
    rows: list[tuple] = []

    # 早班先排「在途 + 还没归题型」的题，没有就补到期卡
    placed_new = 0
    news = conn.execute(
        "SELECT id FROM problem WHERE phase='active' AND status='inbox' "
        "ORDER BY COALESCE(order_no, 999999), id LIMIT ?",
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
