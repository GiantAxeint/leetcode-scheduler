from datetime import date

from app import state


def _t(**kw):
    base = dict(
        stage=1, hold_count=0, fail_count=0, passed=4, total=5,
        threshold=0.8, hold_limit=2, intervals=[1, 3, 7, 15, 30],
        today=date(2026, 1, 1),
    )
    base.update(kw)
    return state.transition(**base)


def test_grade_uses_ratio_not_count():
    assert state.grade(4, 5, 0.8) == state.PASS
    assert state.grade(3, 5, 0.8) == state.HOLD
    assert state.grade(1, 5, 0.8) == state.FAIL
    # 检验项减到 2 项时，1/2 仍然能过（按比例，不看绝对数）
    assert state.grade(2, 2, 0.8) == state.PASS


def test_pass_moves_up_one_stage():
    out = _t(stage=1)
    assert out.result == state.PASS
    assert out.stage_to == 2
    assert out.next_due == "2026-01-04"  # +3 天（第二档间隔）


def test_pass_at_last_stage_archives():
    out = _t(stage=5)
    assert out.archived is True
    assert out.stage_to == 5


def test_fail_drops_one_stage():
    out = _t(stage=4, passed=0)
    assert out.result == state.FAIL
    assert out.stage_to == 3


def test_fail_never_below_stage_one():
    out = _t(stage=1, passed=0)
    assert out.stage_to == 1


def test_hold_twice_becomes_fail():
    first = _t(stage=3, passed=3)
    assert first.result == state.HOLD
    assert first.hold_count == 1
    second = _t(stage=3, passed=3, hold_count=1)
    assert second.result == state.FAIL
    assert second.stage_to == 2


def test_empty_checks_is_hold_not_crash():
    out = _t(passed=0, total=0)
    assert out.result == state.HOLD
