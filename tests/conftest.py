import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app import scheduler


@pytest.fixture(autouse=True)
def _reset_scheduler_flags():
    """scheduler 的 skip_weekend 是模块级全局（api 层每请求同步），
    某个用例通过 PUT /api/settings 改了它会漏给后面的用例 ——
    test_review_fit 把容量和开关改小之后，test_scheduler 里
    「周六不排班」的断言就会莫名其妙地失败。每个用例结束后复位。
    """
    yield
    scheduler.set_skip_weekend(True)
