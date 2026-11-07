"""判定后写入 next_due 的自动避让 —— 走真实 HTTP 链路，不只测纯函数。

pytest 里同时开两条 sqlite 连接是坑（见乙版交接文档坑 7）：写事务没提交会
database is locked。所以这里的顺序是「先 seed 再关连接，然后才调 API」。
"""

import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from app import db
from app.api import create_app


def _seed(root: Path, rows: list[tuple]) -> list[int]:
    """rows = [(title, stage, next_due), ...]，返回自增 id。"""
    con = db.connect(root)
    db.init(con)
    ids = []
    for title, stage, due in rows:
        ids.append(
            con.execute(
                "INSERT INTO problem(title,phase,stage,next_due) "
                "VALUES(?, 'active', ?, ?)",
                (title, stage, due),
            ).lastrowid
        )
    con.commit()
    con.close()
    return ids


@pytest.fixture()
def app_client():
    root = Path(tempfile.mkdtemp())
    app = create_app(root)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c, root


def test_review_moves_due_off_a_full_day(app_client):
    """容量 1，3 天后已经压了一道 → 判完要顺延到第 4 天。"""
    c, root = app_client
    c.put("/api/settings", json={
        "daily_capacity": "1", "skip_weekend": "0", "interval_fuzz": "0",
    })
    today = date.today()
    target = (today + timedelta(days=3)).isoformat()
    ids = _seed(root, [("待判", 0, today.isoformat()), ("占位", 2, target)])

    res = c.post("/api/review", json={"problem_id": ids[0], "correct": True})
    assert res.status_code == 200
    body = res.get_json()
    assert body["stage_to"] == 2
    assert body["next_due"] == (today + timedelta(days=4)).isoformat()


def test_review_keeps_due_when_day_has_room(app_client):
    """容量富余时不该乱挪：该哪天还是哪天。"""
    c, root = app_client
    c.put("/api/settings", json={
        "daily_capacity": "6", "skip_weekend": "0", "interval_fuzz": "0",
    })
    today = date.today()
    ids = _seed(root, [("待判", 0, today.isoformat())])

    res = c.post("/api/review", json={"problem_id": ids[0], "correct": True})
    assert res.status_code == 200
    assert res.get_json()["next_due"] == (today + timedelta(days=3)).isoformat()
