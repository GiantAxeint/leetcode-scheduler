"""调度：以「题」为单位排期，题型卡只做归纳，不参与调度。"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, datetime, timedelta

from . import db

INTERVALS = [1, 3, 7, 15, 30]   # stage 1..5 对应的天数

# 是否跳过周末。True = 旧行为：周末不排班，复习日期落到周末就顺延到下周一。
# False = 周六周日也算可复习日，周末照常生成当天计划。
# 由 api 层在每次建连时同步（见 api.create_app 里的 conn）。
_SKIP_WEEKEND = True


def today_str() -> str:
    return date.today().isoformat()


def due_date() -> str:
    """新题该从哪天开始：默认周末录的题算下周一，关掉 skip_weekend 就按当天。"""
    return next_workday(date.today()).isoformat()


def is_workday(d: date) -> bool:
    """当天能不能排复习。skip_weekend 关掉后，一周七天都算。"""
    if not _SKIP_WEEKEND:
        return True
    return d.weekday() < 5          # 0=周一 … 4=周五


def set_skip_weekend(flag: bool) -> None:
    """由 api 层按设置项同步，避免每个调用点都往下传配置。"""
    global _SKIP_WEEKEND
    _SKIP_WEEKEND = bool(flag)


def next_workday(d: date) -> date:
    while not is_workday(d):
        d += timedelta(days=1)
    return d


def add_days(n: int, base: str | None = None) -> str:
    """往后推 n 天。默认落到周六周日就顺延到下一个工作日（skip_weekend=False 时不顺延）。"""
    d = date.fromisoformat(base) if base else date.today()
    return next_workday(d + timedelta(days=n)).isoformat()


def fuzz_days(days: int, ratio: float = 0.0) -> int:
    """给间隔加抖动，把同档位的题散到相邻几天，别全撞在同一天。

    1 天档直接返回：往下抖变 0 没意义，往上抖等于跳过。
    """
    if ratio <= 0 or days <= 1:
        return days
    lo = max(1, round(days * (1 - ratio)))
    hi = max(lo, round(days * (1 + ratio)))
    return random.randint(lo, hi)


def advance(stage: int, correct: bool, lapse: float = 0.5,
            fuzz: float = 0.0) -> tuple[int, str | None, bool]:
    """返回 (新档位, 下次日期, 是否出池)。

    答对：0 → 2（首答答对直接进 3 天档），否则升一档；第 5 档再答对就出池。
    答错：退一档（最低 1 档），下一次间隔在该档基数上乘 lapse（默认打五折）。
    fuzz：间隔抖动，作用在最终天数上，答对答错都加。
    """
    if correct:
        if stage >= len(INTERVALS):
            return len(INTERVALS), None, True
        new_stage = 2 if stage == 0 else stage + 1
        return new_stage, add_days(fuzz_days(INTERVALS[new_stage - 1], fuzz)), False

    new_stage = max(1, stage - 1)
    days = max(1, round(INTERVALS[new_stage - 1] * lapse))
    return new_stage, add_days(fuzz_days(days, fuzz)), False


def advance_early(stage: int, correct: bool, original_due: str | None,
                  fuzz: float = 0.0) -> tuple[int, str | None, bool]:
    """提前复习（还没到期就自己加练）的推进规则。

    答对：照常升一档，但下次日期取 max(抖动后的新档推算, 原到期日)。
          fuzz 必须在 max 之前做，否则会被原日期压回去，等于没抖。
    答错：档位和日期都不动 —— 多练一次不该被惩罚，这是用户明确要的。
    """
    if not correct:
        return stage, original_due, False
    if stage >= len(INTERVALS):
        return len(INTERVALS), None, True
    new_stage = 2 if stage == 0 else stage + 1
    planned = add_days(fuzz_days(INTERVALS[new_stage - 1], fuzz))
    due = max(planned, original_due) if original_due else planned
    return new_stage, due, False


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


def refill(conn: sqlite3.Connection, exclude: set[int] | None = None) -> int:
    """在途空出几个名额，就从候补队头补几个（exclude 里的不补，避免刚回收又被拉回）。"""
    limit = db.get_int(conn, "queue_limit", 15)
    active = conn.execute(
        "SELECT COUNT(*) c FROM problem WHERE phase='active'"
    ).fetchone()["c"]
    need = limit - active
    if need <= 0:
        return 0
    cap = need + (len(exclude) if exclude else 0)
    rows = conn.execute(
        "SELECT id FROM problem WHERE phase='queued' "
        "ORDER BY COALESCE(order_no, 999999), id LIMIT ?",
        (cap,),
    ).fetchall()
    filled = 0
    for r in rows:
        if filled >= need:
            break
        if exclude and r["id"] in exclude:
            continue
        conn.execute(
            "UPDATE problem SET phase='active', next_due=? WHERE id=?",
            (due_date(), r["id"]),
        )
        filled += 1
    if filled:
        conn.commit()
    return filled


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
    done = conn.execute(
        "SELECT COUNT(*) c FROM problem WHERE phase='done'"
    ).fetchone()["c"]
    return {
        "active": active,
        "active_limit": limit,
        "queued": queued,
        "inbox": inbox,
        "done": done,
        "can_add": active < limit,
        "can_add_card": True,
        "can_add_problem": True,
    }


def build_plan(conn: sqlite3.Connection, today: str | None = None) -> None:
    """当天计划只生成一次。默认周末不排班，skip_weekend 关掉后周末也排。

    容量按「天」算：daily_capacity 决定一天总共排几道，早/午/晚只是把总量切开
    —— 新题优先早班，剩下的名额由到期题补，再均分给午、晚。
    """
    today = today or today_str()
    if not is_workday(date.fromisoformat(today)):
        return
    if conn.execute(
        "SELECT 1 FROM daily_plan WHERE plan_date=? LIMIT 1", (today,)
    ).fetchone():
        return

    cap = max(1, db.get_int(conn, "daily_capacity", 6))
    n_new = min(db.get_int(conn, "morning_new", 2), cap)

    rows: list[tuple] = []
    used: set[int] = set()

    fresh = fresh_problems(conn)[:n_new]
    for r in fresh:
        rows.append((today, "morning", None, r["id"]))
        used.add(r["id"])

    # 新题不够时早班空着的位置交给到期题；其余均分，奇数时午班多担一道
    room = cap - len(fresh)
    plan_slots: list[str] = []
    if room > 0:
        plan_slots = ["morning"] * min(n_new - len(fresh), room)
        rest = room - len(plan_slots)
        if rest > 0:
            noon = (rest + 1) // 2
            plan_slots += ["noon"] * noon + ["evening"] * (rest - noon)

    due = [r for r in due_problems(conn, today) if r["id"] not in used]
    for slot, r in zip(plan_slots, due):
        rows.append((today, slot, None, r["id"]))

    conn.executemany(
        "INSERT INTO daily_plan(plan_date, slot, card_id, problem_id) VALUES(?,?,?,?)",
        rows,
    )
    conn.commit()


def recycle_stale(conn: sqlite3.Connection, today: str | None = None, days: int = 7) -> int:
    """逾期超过 N 天还没碰的题，退回候补，把在途名额让给新题。"""
    today = today or today_str()
    cutoff = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT id FROM problem WHERE phase='active' "
        "AND next_due IS NOT NULL AND next_due < ?",
        (cutoff,),
    ).fetchall()
    if not rows:
        return 0
    ids = {r["id"] for r in rows}
    for pid in ids:
        conn.execute("UPDATE problem SET phase='queued' WHERE id=?", (pid,))
    conn.commit()
    refill(conn, exclude=ids)
    return len(ids)


def _day_count(conn: sqlite3.Connection, iso: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM problem WHERE phase='active' AND next_due=?", (iso,)
    ).fetchone()["c"]


def _next_free_day(conn: sqlite3.Connection, after: str, cap: int, span: int = 90) -> str | None:
    """从 after 的次日开始，找第一个没满容量的工作日。"""
    d = date.fromisoformat(after)
    for _ in range(span):
        d += timedelta(days=1)
        if not is_workday(d):
            continue
        iso = d.isoformat()
        if _day_count(conn, iso) < cap:
            return iso
    return None


def disperse_day(conn: sqlite3.Connection, day: str, cap: int,
                 ids: list[int] | None = None, target: str | None = None,
                 targets: list[str] | None = None) -> dict:
    """把某天的题往后挪。

    两种用法：
      - 自动：只挪三分之一、优先低档位，目标日从次日起找第一个没满的工作日
      - 手动：ids 指定挪哪几道（这时不受三分之一限制，用户自己选的），
              target 指定挪到哪天（覆盖容量，用户说了算）
    """
    rows = conn.execute(
        "SELECT id, stage FROM problem WHERE phase='active' AND next_due=? "
        "ORDER BY stage ASC, id DESC",
        (day,),
    ).fetchall()
    if not rows:
        return {"ok": False, "reason": "这天没有在途的题"}

    manual = ids is not None
    if manual:
        want = {int(x) for x in ids}
        picks = [r for r in rows if r["id"] in want]
    else:
        n = len(rows)
        if n <= cap:
            return {"ok": False, "reason": f"这天 {n} 道，没超容量 {cap}"}
        quota = max(1, n // 3)      # 最多挪三分之一
        need = n - cap              # 至少挪这么多才降到容量内
        picks = rows[:min(quota, need)]

    if not picks:
        return {"ok": False, "reason": "没有可分散的题"}

    # 指定了目标日就按顺序轮流放（选多个日期时均摊），没指定就自动往后找
    seq = sorted(set(targets)) if targets else ([target] if target else [])

    batch = datetime.now().strftime("%Y%m%d%H%M%S%f")[:18]
    moves: list[dict] = []
    cursor = day
    for i, r in enumerate(picks):
        if seq:
            to = seq[i % len(seq)]
        else:
            to = _next_free_day(conn, cursor, cap)
            if to is None:
                break
            cursor = to
        conn.execute("UPDATE problem SET next_due=? WHERE id=?", (to, r["id"]))
        conn.execute(
            "INSERT INTO disperse_log(batch_id, problem_id, from_date, to_date) "
            "VALUES(?,?,?,?)",
            (batch, r["id"], day, to),
        )
        moves.append({"id": r["id"], "to": to})
    conn.commit()

    if not moves:
        return {"ok": False, "reason": "往后 90 天都没有空位"}

    spread: dict[str, int] = {}
    for m in moves:
        spread[m["to"]] = spread.get(m["to"], 0) + 1
    return {"ok": True, "batch": batch, "moved": len(moves), "spread": spread}


def undo_disperse(conn: sqlite3.Connection, batch: str) -> int:
    rows = conn.execute(
        "SELECT problem_id, from_date FROM disperse_log WHERE batch_id=?", (batch,)
    ).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE problem SET next_due=? WHERE id=?", (r["from_date"], r["problem_id"])
        )
    conn.execute("DELETE FROM disperse_log WHERE batch_id=?", (batch,))
    conn.commit()
    return len(rows)


def last_disperse(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT batch_id, COUNT(*) n, MAX(created_at) at FROM disperse_log "
        "GROUP BY batch_id ORDER BY at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return {"batch": row["batch_id"], "count": row["n"], "at": row["at"]}


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
