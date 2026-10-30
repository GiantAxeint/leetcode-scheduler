"""卡片状态机。纯函数，不碰数据库，方便单测。

档位语义：stage 1..N 对应间隔阶梯 interval_days[stage-1]。
通过 → 升一档；持平 → 原地；挂 → 退一档。
从最后一档通过 → 归档出池。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

PASS = "pass"
HOLD = "hold"
FAIL = "fail"
SKIP = "skip"


@dataclass
class Outcome:
    result: str
    stage_from: int
    stage_to: int
    next_due: str
    hold_count: int
    fail_count: int
    archived: bool


def grade(passed: int, total: int, threshold: float) -> str:
    """按通过率判档。项目数可增删，所以用比例而不是绝对数。"""
    if total <= 0:
        return HOLD
    ratio = passed / total
    if ratio >= threshold:
        return PASS
    if ratio >= threshold * 0.5:
        return HOLD
    return FAIL


def transition(
    *,
    stage: int,
    hold_count: int,
    fail_count: int,
    passed: int,
    total: int,
    threshold: float,
    hold_limit: int,
    intervals: list[int],
    today: date | None = None,
) -> Outcome:
    today = today or date.today()
    result = grade(passed, total, threshold)
    max_stage = len(intervals)
    stage = max(1, min(stage, max_stage))

    if result == PASS:
        nxt = stage + 1
        if nxt > max_stage:
            return Outcome(PASS, stage, stage, _add(today, 0), 0, 0, True)
        return Outcome(
            PASS, stage, nxt, _add(today, intervals[nxt - 1]), 0, 0, False
        )

    if result == HOLD:
        hc = hold_count + 1
        if hc >= hold_limit:
            # 长期卡在同一档也算没掌握，降一档重新压实
            ns = max(1, stage - 1)
            return Outcome(FAIL, stage, ns, _add(today, intervals[ns - 1]), 0, fail_count + 1, False)
        return Outcome(HOLD, stage, stage, _add(today, 1), hc, fail_count, False)

    ns = max(1, stage - 1)
    return Outcome(FAIL, stage, ns, _add(today, intervals[ns - 1]), 0, fail_count + 1, False)


def _add(d: date, days: int) -> str:
    return (d + timedelta(days=days)).isoformat()
