"""调度：以「题」为单位排期，题型卡只做归纳，不参与调度。"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import db

INTERVALS = [1, 3, 7, 15, 30]   # stage 1..5 对应的天数


def today_str() -> str:
    return date.today().isoformat()


def due_date() -> str:
    """新题该从哪天开始：周末录的题算下周一。"""
    return next_workday(date.today()).isoformat()


def is_workday(d: date) -> bool:
    return d.weekday() < 5          # 0=周一 … 4=周五


def next_workday(d: date) -> date:
    while not is_workday(d):
        d += timedelta(days=1)
    return d


def add_days(n: int, base: str | None = None) -> str:
    """往后推 n 天，落到周六周日就顺延到下一个工作日。"""
    d = date.fromisoformat(base) if base else date.today()
    return next_workday(d + timedelta(days=n)).isoformat()


def advance(stage: int, correct: bool, lapse: float = 0.5) -> tuple[int, str | None, bool]:
    """返回 (新档位, 下次日期, 是否出池)。

    答对：0 → 2（首答答对直接进 3 天档），否则升一档；第 5 档再答对就出池。
    答错：退一档（最低 1 档），下一次间隔在该档基数上乘 lapse（默认打五折）。
    """
    if correct:
        if stage >= len(INTERVALS):
            return len(INTERVALS), None, True
        new_stage = 2 if stage == 0 else stage + 1
        return new_stage, add_days(INTERVALS[new_stage - 1]), False

    new_stage = max(1, stage - 1)
    days = max(1, round(INTERVALS[new_stage - 1] * lapse))
    return new_stage, add_days(days), False


_PROB_SELECT = """
    SELECT p.*,
           (SELECT GROUP_CONCAT(c.title, '、') FROM card_problem cp
            JOIN pattern_card c ON c.id = cp.card_id
            WHERE cp.problem_id = p.id) AS cards
    FROM problem p
"""


def due_problems(conn: sqlite3.Connection, today: str | None = None) -> list[sqlite3.Row]:
    today = today or today_str()
    return conn.execute(
        _PROB_SELECT
        + """ WHERE p.phase='active' AND p.next_due IS NOT NULL AND p.next_due <= ?
              ORDER BY p.next_due ASC, p.id ASC""",
        (today,),
    ).fetchall()


def fresh_problems(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """还没复习过的新题。"""
    return conn.execute(
        _PROB_SELECT
        + """ WHERE p.phase='active' AND p.stage=0
              ORDER BY COALESCE(p.order_no, 999999), p.id ASC"""
    ).fetchall()


def inflight_problems(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        _PROB_SELECT + " WHERE p.phase='active' "
        "ORDER BY (p.next_due IS NULL), p.next_due ASC, p.id DESC"
    ).fetchall()


def queued_problems(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        _PROB_SELECT
        + """ WHERE p.phase='queued'
              ORDER BY COALESCE(p.order_no, 999999), p.id"""
    ).fetchall()


def queue_order(conn: sqlite3.Connection, ids: list[int]) -> None:
    for i, pid in enumerate(ids):
        conn.execute(
            "UPDATE problem SET order_no=? WHERE id=? AND phase='queued'", (i, pid)
        )
    conn.commit()


def refill(conn: sqlite3.Connection) -> int:
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
        conn.execute(
            "UPDATE problem SET phase='active', next_due=? WHERE id=?",
            (due_date(), r["id"]),
        )
    if rows:
        conn.commit()
    return len(rows)


def gate(conn: sqlite3.Connection) -> dict:
    limit = db.get_int(conn, "queue_limit", 15)
    active = conn.execute(
        "SELECT COUNT(*) c FROM problem WHERE phase='active'"
    ).fetchone()["c"]
    queued = conn.execute(
        "SELECT COUNT(*) c FROM problem WHERE phase='queued'"
    ).fetchone()["c"]
    inbox = conn.execute(
        "SELECT COUNT(*) c FROM problem WHERE status='inbox' AND phase<>'done'"
    ).fetchone()["c"]
    return {
        "active": active,
        "active_limit": limit,
        "queued": queued,
        "inbox": inbox,
        "can_add": active < limit,
        "can_add_card": True,
        "can_add_problem": True,
    }


def build_plan(conn: sqlite3.Connection, today: str | None = None) -> None:
    """当天计划只生成一次。周末不排班。"""
    today = today or today_str()
    if not is_workday(date.fromisoformat(today)):
        return
    if conn.execute(
        "SELECT 1 FROM daily_plan WHERE plan_date=? LIMIT 1", (today,)
    ).fetchone():
        return

    n_new = db.get_int(conn, "morning_new", 2)
    n_noon = db.get_int(conn, "noon_due", 2)
    n_even = db.get_int(conn, "evening_due", 1)

    rows: list[tuple] = []
    used: set[int] = set()

    fresh = fresh_problems(conn)[:n_new]
    for r in fresh:
        rows.append((today, "morning", None, r["id"]))
        used.add(r["id"])

    due = [r for r in due_problems(conn, today) if r["id"] not in used]
    for slot, need in (("morning", n_new - len(fresh)), ("noon", n_noon), ("evening", n_even)):
        for r in due:
            if need <= 0:
                break
            if r["id"] in used:
                continue
            rows.append((today, slot, None, r["id"]))
            used.add(r["id"])
            need -= 1

    conn.executemany(
        "INSERT INTO daily_plan(plan_date, slot, card_id, problem_id) VALUES(?,?,?,?)",
        rows,
    )
    conn.commit()


def backlog_problems(conn: sqlite3.Connection, today: str | None = None) -> list[sqlite3.Row]:
    """今天到期但没排进今日计划的题。"""
    today = today or today_str()
    return conn.execute(
        _PROB_SELECT
        + """ WHERE p.phase='active' AND p.next_due IS NOT NULL AND p.next_due <= ?
              AND p.id NOT IN (SELECT problem_id FROM daily_plan
                               WHERE plan_date=? AND problem_id IS NOT NULL)
              ORDER BY p.next_due ASC""",
        (today, today),
    ).fetchall()


def backlog(conn: sqlite3.Connection, today: str | None = None) -> int:
    return len(backlog_problems(conn, today))
