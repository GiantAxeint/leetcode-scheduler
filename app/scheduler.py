"""调度：以「题」为单位排期，题型卡只做归纳，不参与调度。"""

# ─────────────────────────────────────────────────────────────────────────
# [甲版批注 · 2026-11-07]
# 这个文件是整个项目的重心，其余都是增删改查。写给丙版：
#
# 1. 「以题为单位」不是技术选择，是用户拍过板的结论。原话是「我再说一遍，
#    题型卡是用来归纳复习的，不应该是调度单位」。我在他第二次说之前，一直
#    在维护「卡到日子就复习整张卡」的旧设计，等于让卡上挂的题跟着卡一起过。
#    如果你以后看到某处想把 card_id 拉回调度链路 —— 方向错了，先回来读这段。
#
# 2. 这个文件里的每个数字（1/3/7/15/30、0.5、20%）都能改，但改之前先算极端
#    情况：跑到第 20 次复习时，这条规则会把题推到哪一天。我在这里栽过一次，
#    详见 advance() 的批注。
#
# 3. 想找甲版留下的其它痕迹：grep -rn "甲版批注"
# ─────────────────────────────────────────────────────────────────────────

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
    # [甲版批注] 周末录的题从周一开始，是用户自己提的：「周六周日如果写题，
    # 第一次复习时间在周一，周末不算作复习日期」。理由是周末他本来不坐下写。
    #
    # 注意这条只作用于「首次入池」，不作用于复习算出来的日期 —— 复习日期由
    # add_days() 顺延，新题日期由这里定，两个入口别搞混。丙版的 fit_due()
    # 也只避让复习日期、不动新题，否则当天会没题可做。
    return next_workday(date.today()).isoformat()


def is_workday(d: date) -> bool:
    """当天能不能排复习。skip_weekend 关掉后，一周七天都算。"""
    if not _SKIP_WEEKEND:
        return True
    return d.weekday() < 5          # 0=周一 … 4=周五


def set_skip_weekend(flag: bool) -> None:
    """由 api 层按设置项同步，避免每个调用点都往下传配置。"""
    # [甲版批注] 这是全项目最容易咬人的一处：模块级全局变量 + 测试共享进程。
    # 哪个用例调一次 PUT /api/settings 把它改成 0，后面的用例就全在「七天都排」
    # 的前提上跑，表现是 add_days / build_plan 的周末用例莫名挂掉，报错还指不到
    # 这里。丙版已经踩过一次，解法是 tests/conftest.py 里的 autouse fixture 复位。
    #
    # 你要是想把这个全局改成参数透传 —— 那是一次不小的重构，值得做但不紧急；
    # 做之前先确认没有测试在 import 期就依赖当前值。
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
    # [甲版批注] 三个参数都是用户定的，改动会直接改他的复习节奏，先说清来由。
    #
    # ① 为什么 stage 0 答对要跳到 2，而不是 1？
    #    1 天档对「第一次就做对的题」是浪费：他第二天还得再看一遍一道已经会的题。
    #    但如果第一次答错，仍走 1 天档（下面 fail 分支 max(1, ...)），因为错了就是
    #    没会。也就是说：1 天档只留给答错的题。这是 ANKI 的毕业逻辑，不是随手写的。
    #
    # ② 为什么答错是「退一档 + 打五折」，而不是把间隔改成 2,6,14,29？
    #    他一开始自己提的就是 2,6,14,29，然后补了一句「我感觉有问题」。
    #    确实有问题：2,6,14,29 大约是 1,3,7,15 的两倍，等于错得越多、复习得越少，
    #    规则会安静地反向工作。我去查了 Anki 官方 FAQ 和墨墨的算法说明，两家都是
    #    「错 → 间隔缩短」。所以这里选了 Anki 式：退一档，再在该档基数上打五折。
    #    要改这块，先自己想一遍「一道题连错五次会落到哪」，再动手。
    #
    # ③ 为什么 fuzz 要作用在 add_days 之前、而不是之后？
    #    它是天数，不是日期。先抖再算日期，才能落到相邻的工作日上；先算日期再抖
    #    会抖出周末。advance_early 里还有一层：fuzz 必须在 max(planned, 原到期日)
    #    之前做，否则会被原日期压回去，等于没抖。
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
    # [甲版批注] 「答错不动」这条很容易被后来的窗口当成 bug 修掉，所以留一句：
    # 提前复习是他自己主动加练（题还没到期，他自己想再做一遍）。加练做错了就
    # 降档，等于惩罚主动学习 —— 他明确说过不要这个。真正到期的判定才走 advance()，
    # 那里答错才退档。两条路径别合并。
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
# [甲版批注] 这个子查询不是装饰。有一版 /api/problems 用的是裸 SELECT *，
# 忘了带 cards 字段，前端照旧读 p.cards，结果「已成」里所有题都归到
# 「未归题型」—— 库里明明有归纳关系，界面上是未分类。我排查了两天才发现
# 是字段没返回，不是关联丢了。
#
# 教训：任何返回 problem 行的接口，要么都用这个 _PROB_SELECT，要么前端别读 cards。
# /api/today 当时有这段、/api/problems 没有，就这么错位的。


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
    # [甲版批注] exclude 是我后来补的，补之前有个很蠢的循环：
    # recycle_stale() 把逾期题退回候补 → 立刻调 refill() 补位 → 候补队头第一道
    # 正好就是刚退回去的那道 → 原样拉回在途 → 下次再逾期再退再拉。症状是
    # 「这道题永远在途、永远不动」，而且日志上看起来一切正常。
    # 任何「先移出再补位」的地方，都要记得把刚移出的 id 摘掉。
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
    # [甲版批注] 两处设计是用户拍板的取舍，不是漏写：
    #
    # ① 计划是快照：当天生成一次就 return，中途新到期的题不会补进来。
    #    我提过做实时补员，他没要。理由是做到一半清单还在变很烦 —— 你正做着第三
    #    道，卡片突然插进来一张新的，上下文就断了。这个取舍我保留意见，但它不是 bug。
    #
    # ② 周末保留早午晚三班和记录，只是不排到期复习。11-07 他专门截了图说
    #    「周末保留题目的早午晚班和记录功能，只是题目复习顺延」。所以这里
    #    is_workday 拦的是「自动生成」，不是「手动加题 / 判定 / 记录」。
    #    我一开始把整个今日页在周末藏了，被截图打回来过一次。
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
    # [甲版批注] 只改 phase，不动 stage —— 退候补是「让出名额」，不是「判定他做错了」。
    # 他回头从候补里重新拉出来，档位还在，接着复习。要是这里清零，等于一次没做
    # 就把之前记住的全抹了，代价太大。
    #
    # 7 天这个数是他定的（我给了「逾期警示 + 自动回收」两个方案，他选了 A）。
    # 原本还有个问题：逾期当天没做，算不算答错？查了墨墨的做法 —— 不做判定，
    # 只让到期日压着不动，逾期久了自然沉到底。所以这里没有自动判错，别加。
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


def day_load(conn: sqlite3.Connection, iso: str, exclude_id: int | None = None) -> int:
    """某天已经压了几道在途题。exclude_id 用于「把自己摘出去再数」。"""
    sql = "SELECT COUNT(*) c FROM problem WHERE phase='active' AND next_due=?"
    args: list = [iso]
    if exclude_id is not None:
        sql += " AND id<>?"
        args.append(exclude_id)
    return conn.execute(sql, args).fetchone()["c"]


def fit_due(conn: sqlite3.Connection, iso: str, cap: int,
            exclude_id: int | None = None, max_shift: int = 30) -> str:
    """把算出来的到期日落到一个没满的日子上，写库之前调用。

    为什么需要：同档位的题会算出同一个日期（S1 的几道全落 11-09），而一天只排
    得下 daily_capacity 道，多出来的只能进 backlog —— 用户看到的是「到期了但今天
    不让做」。判定完当场把它挪到最近一个有空位的日子，比事后手动分散省事。

    边界：
      - 只看 phase='active'，候补题不占名额
      - exclude_id 摘掉自己：提前复习时新日期可能等于原到期日，不摘会自己挤自己
      - 找不到空位就原样返回。宁可那天超量，也不能把题推到一个月后 —— 推到
        一个月后等于这道题放假，比超量更糟
    """
    if not iso:
        return iso
    d = date.fromisoformat(iso)
    for _ in range(max_shift + 1):
        if is_workday(d) and day_load(conn, d.isoformat(), exclude_id) < cap:
            return d.isoformat()
        d += timedelta(days=1)
    return iso


def _day_count(conn: sqlite3.Connection, iso: str) -> int:
    return day_load(conn, iso)


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
