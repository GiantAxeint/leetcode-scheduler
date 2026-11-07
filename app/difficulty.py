"""难度补全：从 LeetCode 题库接口一次性拉全量难度，按题号回填。

为什么不继续靠扩展抓：扩展读的是页面 DOM，题目页分段渲染，抓的那一刻
难度标签常常还没出现，抓到空串是常态（乙版交接文档坑 5）。这里拉的是官方
题库清单，一次请求覆盖全部题目，不依赖页面渲染时机。

代价：接口返回 4489 条、约 3–4 秒。所以只在用户点按钮时调一次，不做定时轮询。
网络不通时返回明确错误，不静默失败。
"""

from __future__ import annotations

import json
import sqlite3
import urllib.request

SOURCE = "https://leetcode.cn/api/problems/all/"
LEVELS = {1: "简单", 2: "中等", 3: "困难"}
VALID = set(LEVELS.values())


def fetch_map(timeout: float = 12.0) -> dict[str, str]:
    """题号（字符串）→ 中文难度。用 frontend_question_id 匹配。

    注意：接口里 `stat.question_id` 是老题库的内部 ID（1052 对应的是
    Campus Bikes），页面上显示的题号在 `stat.frontend_question_id`。
    用错字段会静默回填成别的题的难度，这比不填更糟。
    """
    req = urllib.request.Request(SOURCE, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))

    out: dict[str, str] = {}
    for p in data.get("stat_status_pairs") or []:
        stat = p.get("stat") or {}
        qid = stat.get("frontend_question_id")
        level = (p.get("difficulty") or {}).get("level")
        if qid is None or level not in LEVELS:
            continue
        out[str(qid)] = LEVELS[level]
    return out


_COLS = "id, leetcode_id, title, difficulty"


def missing(conn: sqlite3.Connection) -> list[dict]:
    """难度为空或不认识的题。只有这三种会出现在列表里。"""
    rows = conn.execute(
        f"SELECT {_COLS} FROM problem "
        "WHERE difficulty IS NULL OR TRIM(difficulty)='' "
        "   OR difficulty NOT IN ('简单','中等','困难') "
        "ORDER BY leetcode_id IS NULL, leetcode_id"
    ).fetchall()
    return [dict(r) for r in rows]


def refresh(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    """拉一次题库清单，把缺难度的题补上。返回明细，前端要显示。"""
    table = fetch_map()
    rows = missing(conn)

    fixed: list[dict] = []
    missed: list[dict] = []
    for r in rows:
        qid = r["leetcode_id"]
        value = table.get(str(qid)) if qid else None
        if not value:
            missed.append({**r, "reason": "题库里没有这道题" if qid else "缺题号"})
            continue
        if not dry_run:
            conn.execute(
                "UPDATE problem SET difficulty=? WHERE id=?", (value, r["id"])
            )
        fixed.append({**r, "difficulty": value})

    if not dry_run and fixed:
        conn.commit()
    return {"checked": len(rows), "fixed": fixed, "missed": missed}
