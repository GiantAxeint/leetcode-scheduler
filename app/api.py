"""HTTP 接口层。所有路径读写都基于「当前数据根」，换盘只需改 root。"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from . import config, db, host, scheduler, state

# 每个数据根只初始化一次；否则两个并发请求同时建表会撞写锁
_INIT_LOCK = threading.Lock()
_INITED: set[str] = set()


def create_app(root: Path | None = None) -> Flask:
    root = Path(root) if root else config.locate()
    config.ensure_dirs(root)

    app = Flask(__name__, static_folder="web", static_url_path="")
    app.config["ROOT"] = root
    app.json.ensure_ascii = False

    @app.after_request
    def _cors(resp):
        # 只监听 127.0.0.1，放开 CORS 让浏览器扩展能直接请求
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
        return resp

    def conn() -> sqlite3.Connection:
        if "db" not in g:
            root = str(app.config["ROOT"])
            g.db = db.connect(app.config["ROOT"])
            if root not in _INITED:
                with _INIT_LOCK:
                    if root not in _INITED:
                        db.init(g.db)
                        _INITED.add(root)
        return g.db

    @app.errorhandler(Exception)
    def _on_error(e):
        if isinstance(e, HTTPException):
            return e
        return jsonify({"error": f"服务端错误：{e}"}), 500

    @app.teardown_appcontext
    def _close(_exc):
        c = g.pop("db", None)
        if c is not None:
            c.close()

    # ---------- 静态页 ----------
    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    # ---------- 给扩展用的探活 ----------
    @app.get("/api/health")
    def api_health():
        return jsonify({"ok": True, "app": "刷题调度", "version": "0.3.0",
                        "mode": "desktop" if host.available("show_window") else "headless",
                        "data_root": str(app.config["ROOT"])})

    # ---------- 桌面壳联动 ----------
    @app.post("/api/ui/show")
    def api_ui_show():
        """单实例唤起：第二次双击 exe 时打这里，把已有窗口叫到前面而不是起新进程。"""
        try:
            host.call("show_window")
            return jsonify({"ok": True})
        except host.HostUnavailable:
            return jsonify({"ok": False, "error": "当前不是桌面模式"}), 501

    @app.get("/api/desktop/state")
    def api_desktop_state():
        desktop = host.available("show_window")
        out = {
            "mode": "desktop" if desktop else "headless",
            "close_action": "tray",
            "autostart": False,
        }
        if desktop:
            # 这两项只有桌面壳做得到，headless 下保持上面的默认值
            try:
                out["close_action"] = host.call("close_action")
            except host.HostUnavailable:
                pass
            try:
                out["autostart"] = host.call("autostart_enabled")
            except host.HostUnavailable:
                pass
        return jsonify(out)

    @app.post("/api/desktop/autostart")
    def api_desktop_autostart():
        b = request.get_json(force=True, silent=True) or {}
        enabled = bool(b.get("enabled"))
        try:
            return jsonify({"autostart": host.call("autostart_set", enabled)})
        except host.HostUnavailable:
            return jsonify({"error": "当前不是桌面模式"}), 501

    @app.get("/api/lookup")
    def api_lookup():
        """扩展用：这道题是不是已经收过了。"""
        c = conn()
        raw = (request.args.get("leetcode_id") or "").strip()
        if not raw.isdigit():
            return jsonify({"exists": False})
        row = c.execute(
            "SELECT * FROM problem WHERE leetcode_id=?", (int(raw),)
        ).fetchone()
        if not row:
            return jsonify({"exists": False})
        cards = c.execute(
            "SELECT c.id, c.title FROM pattern_card c "
            "JOIN card_problem cp ON cp.card_id=c.id WHERE cp.problem_id=?",
            (row["id"],),
        ).fetchall()
        return jsonify({"exists": True, "problem": dict(row),
                        "cards": [dict(x) for x in cards]})

    # ---------- 今日 ----------
    @app.get("/api/today")
    def api_today():
        c = conn()
        today = scheduler.today_str()
        recycled = scheduler.recycle_stale(c, today)
        scheduler.build_plan(c, today)
        rows = c.execute(
            """
            SELECT d.id AS plan_id, d.slot, d.done, d.problem_id,
                   p.title, p.leetcode_id, p.url, p.slug, p.difficulty,
                   p.stage, p.next_due, p.status,
                   CAST(julianday(?) - julianday(p.next_due) AS INTEGER) AS overdue,
                   (p.last_reviewed_at IS NOT NULL
                    AND substr(p.last_reviewed_at, 1, 10) = ?) AS reviewed_today,
                   (SELECT GROUP_CONCAT(cc.title, '、') FROM card_problem cp
                    JOIN pattern_card cc ON cc.id = cp.card_id
                    WHERE cp.problem_id = p.id) AS cards
            FROM daily_plan d
            JOIN problem p ON p.id = d.problem_id
            WHERE d.plan_date = ?
            ORDER BY CASE d.slot WHEN 'morning' THEN 0 WHEN 'noon' THEN 1 ELSE 2 END, d.id
            """,
            (today, today, today),
        ).fetchall()
        slots: dict[str, list] = {"morning": [], "noon": [], "evening": []}
        for r in rows:
            slots[r["slot"]].append(dict(r))
        return jsonify(
            {
                "date": today,
                "rest": not scheduler.is_workday(date.today()),
                "recycled": recycled,
                "gate": scheduler.gate(c),
                "backlog": scheduler.backlog(c, today),
                "slots": slots,
                "checks": [dict(x) for x in _active_checks(c)],
                "settings": _settings_payload(c),
            }
        )

    # ---------- 复习（判的是题，不是卡） ----------
    @app.post("/api/review")
    def api_review():
        c = conn()
        b = request.get_json(force=True) or {}
        pid = int(b.get("problem_id") or 0)
        if not pid:
            return jsonify({"error": "缺 problem_id"}), 400
        p = c.execute("SELECT * FROM problem WHERE id=?", (pid,)).fetchone()
        if not p:
            return jsonify({"error": "题目不存在"}), 404

        if b.get("skip"):
            _log(c, pid, None, "skip", p["stage"], p["stage"], 0, 0, [], None, 0, "")
            _mark_plan_done(c, pid)
            c.commit()
            return jsonify({"result": "skip", "stage": p["stage"], "next_due": p["next_due"]})

        # 一天只判一次。原来没有任何拦截，连点「答对」会 0→2→3→4 一路把档位推上去，
        # 用户看到的是「我只是连点了几下，怎么就变 7 天后了」。
        # force=true 留给将来「手动改判」，界面暂不暴露。
        if (p["last_reviewed_at"] or "")[:10] == scheduler.today_str() and not b.get("force"):
            return jsonify({
                "error": "这道题今天已经判定过了，明天再来",
                "code": "already_reviewed",
                "stage": p["stage"],
                "next_due": p["next_due"],
            }), 409

        correct = bool(b.get("correct"))
        stage_from = p["stage"] or 0
        today = scheduler.today_str()
        # 提前复习（还没到期就自己加练）：规则跟到期复习不一样，
        # 由后端按 next_due 自动识别，前端不用传标志
        early = bool(p["next_due"]) and p["next_due"] > today
        if early:
            stage_to, next_due, out_pool = scheduler.advance_early(
                stage_from, correct, p["next_due"]
            )
        else:
            lapse = db.get_float(c, "lapse_factor", 0.5)
            stage_to, next_due, out_pool = scheduler.advance(stage_from, correct, lapse)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        c.execute(
            """UPDATE problem
               SET stage=?, next_due=?, last_reviewed_at=?,
                   pass_streak=?, fail_count=?, phase=?, solved=?, solved_at=?
               WHERE id=?""",
            (
                stage_to,
                next_due,
                now,
                (p["pass_streak"] or 0) + 1 if correct else 0,
                (p["fail_count"] or 0) + (0 if correct else 1),
                "done" if out_pool else p["phase"],
                1 if (correct or p["solved"]) else 0,
                (now[:10] if correct else p["solved_at"]),
                pid,
            ),
        )
        _log(
            c, pid, None,
            "correct" if correct else "wrong",
            stage_from, stage_to,
            int(b.get("passed") or 0), int(b.get("total") or 0),
            b.get("checks") or [], b.get("error_type"),
            int(b.get("duration_sec") or 0), b.get("note", ""),
            # 撤回要吃这两个原值：next_due 反推不出来，solved 也可能是录题自带的
            next_due_before=p["next_due"],
            solved_before=p["solved"],
        )
        _mark_plan_done(c, pid)
        c.commit()
        if out_pool:
            scheduler.refill(c)
        return jsonify(
            {
                "result": "correct" if correct else "wrong",
                "stage_from": stage_from,
                "stage_to": stage_to,
                "next_due": next_due,
                "out_pool": out_pool,
            }
        )

    @app.post("/api/review/undo")
    def api_review_undo():
        """撤回今天的判定 —— 手滑点错时用。

        只允许撤今天最近的一条：更早的判定已经影响了后续排期，
        倒着撤会把整条链搅乱，收益也远不如风险大。
        """
        c = conn()
        b = request.get_json(force=True) or {}
        pid = int(b.get("problem_id") or 0)
        if not pid:
            return jsonify({"error": "缺 problem_id"}), 400
        p = c.execute("SELECT * FROM problem WHERE id=?", (pid,)).fetchone()
        if not p:
            return jsonify({"error": "题目不存在"}), 404

        today = scheduler.today_str()
        last = c.execute(
            "SELECT * FROM review_log WHERE problem_id=? "
            "AND substr(reviewed_at, 1, 10)=? AND result IN ('correct','wrong') "
            "ORDER BY id DESC LIMIT 1",
            (pid, today),
        ).fetchone()

        if last is None:
            # 没有「答对/未答对」，但还是可能被标成已处理：
            # ① 细想弹窗里点了「跳过」（result='skip'，不改档位）
            # ② 直接点了卡片的完成标记（daily_plan.done=1）
            # 这两种都得能撤，否则用户会看到「已处理」却撤不回去。
            skipped = c.execute(
                "SELECT id FROM review_log WHERE problem_id=? "
                "AND substr(reviewed_at, 1, 10)=? AND result='skip' "
                "ORDER BY id DESC LIMIT 1",
                (pid, today),
            ).fetchone()
            if skipped:
                c.execute("DELETE FROM review_log WHERE id=?", (skipped["id"],))
                c.execute(
                    "UPDATE daily_plan SET done=0 WHERE plan_date=? AND problem_id=?",
                    (today, pid),
                )
                c.commit()
                return jsonify(
                    {"ok": True, "undone": "skip", "stage": p["stage"],
                     "next_due": p["next_due"], "phase": p["phase"]}
                )

            cur = c.execute(
                "UPDATE daily_plan SET done=0 "
                "WHERE plan_date=? AND problem_id=? AND done=1",
                (today, pid),
            )
            if cur.rowcount == 0:
                return jsonify({"error": "今天没有可撤回的判定"}), 400
            c.commit()
            return jsonify(
                {"ok": True, "undone": "mark", "stage": p["stage"],
                 "next_due": p["next_due"], "phase": p["phase"]}
            )

        stage_back = last["stage_from"] if last["stage_from"] is not None else 0
        # 再往前一条判定的时间：撤回后 last_reviewed_at 要回退到它，
        # 否则这道题会被当成「今天已经判过」，按钮还是灰的
        prev = c.execute(
            "SELECT reviewed_at FROM review_log "
            "WHERE problem_id=? AND id<? AND result IN ('correct','wrong') "
            "ORDER BY id DESC LIMIT 1",
            (pid, last["id"]),
        ).fetchone()

        correct = last["result"] == "correct"
        # v8 之后每次判定都记了原值，能精确还原；v8 之前的老记录两列为 NULL，
        # next_due 退回今天，solved 不敢瞎猜（可能是题录进来时就带的「已通过」）
        next_due_back = last["next_due_before"] or today
        solved_before = last["solved_before"]
        if solved_before is None:
            solved_val, solved_at_val = p["solved"], p["solved_at"]
        elif solved_before:
            solved_val, solved_at_val = 1, p["solved_at"]
        else:
            solved_val, solved_at_val = 0, None

        c.execute(
            """UPDATE problem
               SET stage=?, next_due=?, last_reviewed_at=?, pass_streak=?, fail_count=?,
                   solved=?, solved_at=?
               WHERE id=?""",
            (
                stage_back,
                next_due_back,
                prev["reviewed_at"] if prev else None,
                max(0, (p["pass_streak"] or 0) - 1) if correct else 0,
                max(0, (p["fail_count"] or 0) - (0 if correct else 1)),
                solved_val,
                solved_at_val,
                pid,
            ),
        )
        # 撤回的是「出池」那一下，得把题放回池子，否则永远排不上
        if p["phase"] == "done" and stage_back < 5:
            c.execute("UPDATE problem SET phase='active' WHERE id=?", (pid,))
        c.execute("DELETE FROM review_log WHERE id=?", (last["id"],))
        c.execute(
            "UPDATE daily_plan SET done=0 WHERE plan_date=? AND problem_id=?", (today, pid)
        )
        c.commit()

        row = c.execute(
            "SELECT stage, next_due, phase FROM problem WHERE id=?", (pid,)
        ).fetchone()
        return jsonify(
            {
                "ok": True,
                "undone": "review",
                "stage": row["stage"],
                "next_due": row["next_due"],
                "phase": row["phase"],
            }
        )

    @app.delete("/api/plan/<int:plan_id>")
    def api_plan_remove(plan_id: int):
        """把题从今天的清单里移出（拖回题库抽屉）。

        只删 daily_plan 这一行：phase、stage、next_due 全都不动，
        题还在原来的在途/候补里，随时能再拖回来。
        """
        c = conn()
        row = c.execute("SELECT * FROM daily_plan WHERE id=?", (plan_id,)).fetchone()
        if not row:
            return jsonify({"error": "今天没有这一条"}), 404
        c.execute("DELETE FROM daily_plan WHERE id=?", (plan_id,))
        c.commit()
        return jsonify({"ok": True, "problem_id": row["problem_id"]})

    @app.post("/api/plan/<int:plan_id>/done")
    def api_plan_done(plan_id: int):
        c = conn()
        c.execute("UPDATE daily_plan SET done=1 WHERE id=?", (plan_id,))
        c.commit()
        return jsonify({"ok": True})

    @app.put("/api/plan/<int:plan_id>")
    def api_plan_move(plan_id: int):
        """拖到另一个班次。"""
        c = conn()
        b = request.get_json(force=True) or {}
        slot = b.get("slot")
        if slot not in SLOTS:
            return jsonify({"error": "班次不合法"}), 400
        c.execute("UPDATE daily_plan SET slot=? WHERE id=?", (slot, plan_id))
        c.commit()
        return jsonify({"ok": True, "slot": slot})

    @app.post("/api/plan/add")
    def api_plan_add():
        """把一道题手动补进今天某个班次（题库抽屉拖拽走这里）。

        候补 / 待归纳 / 积压的题都收：拖进来只是进当日清单，
        不改 phase、不占在途名额。已经在今天的重复拖入按「换班」处理。
        """
        c = conn()
        b = request.get_json(force=True) or {}
        pid = int(b.get("problem_id") or 0)
        if not pid:
            return jsonify({"error": "缺 problem_id"}), 400
        if not c.execute("SELECT 1 FROM problem WHERE id=?", (pid,)).fetchone():
            return jsonify({"error": "题目不存在"}), 404

        slot = b.get("slot")
        if slot == "auto":
            slot = _auto_slot()
        today = scheduler.today_str()
        row = c.execute(
            "SELECT id FROM daily_plan WHERE plan_date=? AND problem_id=?", (today, pid)
        ).fetchone()
        if row:
            if slot in SLOTS:
                c.execute(
                    "UPDATE daily_plan SET slot=? WHERE id=?", (slot, row["id"])
                )
                c.commit()
                return jsonify({"ok": True, "slot": slot, "moved": True})
            return jsonify({"ok": True, "slot": None, "moved": False})

        placed = _place_in_slot(c, pid, slot)
        c.commit()
        return jsonify({"ok": True, "slot": placed, "moved": False})

    # ---------- 卡片 ----------
    @app.get("/api/cards")
    def api_cards():
        c = conn()
        q = "SELECT c.*, s.stage, s.next_due, s.archived_at, " \
            "(SELECT COUNT(*) FROM card_problem cp WHERE cp.card_id=c.id) AS problem_count " \
            "FROM pattern_card c LEFT JOIN card_state s ON s.card_id=c.id WHERE 1=1"
        args: list = []
        if (st := request.args.get("status")):
            q += " AND c.status=?"
            args.append(st)
        if (kw := request.args.get("q")):
            q += " AND (c.title LIKE ? OR c.trigger_signals LIKE ?)"
            args += [f"%{kw}%", f"%{kw}%"]
        if request.args.get("due") == "1":
            q += " AND c.status='active' AND s.next_due <= ?"
            args.append(scheduler.today_str())
        q += " ORDER BY c.status ASC, s.next_due ASC, c.id DESC"
        return jsonify([dict(r) for r in c.execute(q, args)])

    @app.post("/api/cards")
    def api_card_create():
        c = conn()
        b = request.get_json(force=True) or {}
        if not (b.get("title") or "").strip():
            return jsonify({"error": "标题不能为空"}), 400
        g_ = scheduler.gate(c)
        if not g_["can_add_card"]:
            return jsonify({"error": f"在途卡已达上限 {g_['active_limit']}，先清队列", "gate": g_}), 409
        cur = c.execute(
            """INSERT INTO pattern_card(title,trigger_signals,essence,skeleton_code,pitfalls,complexity,variants,category)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                b["title"].strip(), b.get("trigger_signals", ""), b.get("essence", ""),
                b.get("skeleton_code", ""), b.get("pitfalls", ""), b.get("complexity", ""),
                b.get("variants", ""), b.get("category", ""),
            ),
        )
        cid = cur.lastrowid
        c.execute(
            "INSERT INTO card_state(card_id,stage,next_due) VALUES(?,1,?)",
            (cid, scheduler.today_str()),
        )
        for pid in b.get("problem_ids", []):
            c.execute(
                "INSERT OR IGNORE INTO card_problem(card_id,problem_id,is_representative) VALUES(?,?,0)",
                (cid, pid),
            )
            c.execute("UPDATE problem SET status='linked' WHERE id=?", (pid,))
        c.commit()
        return jsonify({"id": cid})

    @app.get("/api/cards/<int:cid>")
    def api_card_detail(cid: int):
        c = conn()
        card = c.execute(
            "SELECT c.*, s.stage, s.next_due, s.hold_count, s.archived_at "
            "FROM pattern_card c LEFT JOIN card_state s ON s.card_id=c.id WHERE c.id=?",
            (cid,),
        ).fetchone()
        if not card:
            return jsonify({"error": "not found"}), 404
        probs = c.execute(
            "SELECT p.*, cp.is_representative FROM problem p "
            "JOIN card_problem cp ON cp.problem_id=p.id WHERE cp.card_id=? ORDER BY cp.is_representative DESC",
            (cid,),
        ).fetchall()
        logs = c.execute(
            "SELECT * FROM review_log WHERE card_id=? ORDER BY reviewed_at DESC LIMIT 30", (cid,)
        ).fetchall()
        return jsonify({"card": dict(card), "problems": [dict(x) for x in probs],
                        "logs": [dict(x) for x in logs]})

    @app.put("/api/cards/<int:cid>")
    def api_card_update(cid: int):
        c = conn()
        b = request.get_json(force=True) or {}
        fields = ["title", "trigger_signals", "essence", "skeleton_code",
                  "pitfalls", "complexity", "variants", "category"]
        sets = ", ".join(f"{f}=?" for f in fields if f in b)
        vals = [b[f] for f in fields if f in b]
        if sets:
            c.execute(
                f"UPDATE pattern_card SET {sets}, updated_at=datetime('now','localtime') WHERE id=?",
                (*vals, cid),
            )
        c.commit()
        return jsonify({"ok": True})

    @app.post("/api/cards/<int:cid>/archive")
    def api_card_archive(cid: int):
        c = conn()
        # 题型卡只是归纳容器，归档它不影响题的复习排期
        c.execute("UPDATE pattern_card SET status='archived' WHERE id=?", (cid,))
        c.execute(
            "UPDATE card_state SET archived_at=? WHERE card_id=?", (scheduler.today_str(), cid)
        )
        c.commit()
        return jsonify({"ok": True})

    @app.post("/api/cards/<int:cid>/reopen")
    def api_card_reopen(cid: int):
        c = conn()
        c.execute("UPDATE pattern_card SET status='active' WHERE id=?", (cid,))
        c.execute(
            "UPDATE card_state SET stage=3, next_due=?, archived_at=NULL WHERE card_id=?",
            (scheduler.today_str(), cid),
        )
        c.commit()
        return jsonify({"ok": True})

    @app.delete("/api/cards/<int:cid>")
    def api_card_delete(cid: int):
        c = conn()
        c.execute("DELETE FROM pattern_card WHERE id=?", (cid,))
        c.commit()
        return jsonify({"ok": True})

    # ---------- 题目 ----------
    @app.get("/api/problems")
    def api_problems():
        c = conn()
        st = request.args.get("status")
        q = "SELECT * FROM problem"
        args: list = []
        if st:
            q += " WHERE status=?"
            args.append(st)
        q += " ORDER BY id DESC LIMIT 300"
        return jsonify([dict(r) for r in c.execute(q, args)])

    @app.get("/api/problems/<int:pid>")
    def api_problem_detail(pid: int):
        """复习页要用的：题本身 + 它归属的题型（骨架、坑这些）。"""
        c = conn()
        p = c.execute("SELECT * FROM problem WHERE id=?", (pid,)).fetchone()
        if not p:
            return jsonify({"error": "题目不存在"}), 404
        cards = c.execute(
            "SELECT c.* FROM pattern_card c JOIN card_problem cp ON cp.card_id=c.id "
            "WHERE cp.problem_id=? ORDER BY c.status ASC",
            (pid,),
        ).fetchall()
        logs = c.execute(
            "SELECT reviewed_at, result, stage_from, stage_to, note FROM review_log "
            "WHERE problem_id=? ORDER BY reviewed_at DESC LIMIT 20",
            (pid,),
        ).fetchall()
        return jsonify({"problem": dict(p),
                        "cards": [dict(x) for x in cards],
                        "logs": [dict(x) for x in logs]})

    @app.get("/api/inflight")
    def api_inflight():
        """在途：当月还要复习的不同题目。"""
        c = conn()
        return jsonify([dict(r) for r in scheduler.inflight_problems(c)])

    @app.get("/api/backlog")
    def api_backlog():
        """积压：今天到期但没排进今日计划的卡。"""
        c = conn()
        return jsonify([dict(r) for r in scheduler.backlog_problems(c)])

    @app.get("/api/queued")
    def api_queued():
        """候补队列。"""
        c = conn()
        return jsonify([dict(r) for r in scheduler.queued_problems(c)])

    @app.put("/api/queued/order")
    def api_queued_order():
        c = conn()
        b = request.get_json(force=True) or {}
        ids = [int(x) for x in (b.get("ids") or [])]
        scheduler.queue_order(c, ids)
        return jsonify({"ok": True, "count": len(ids)})

    @app.post("/api/problems/<int:pid>/activate")
    def api_problem_activate(pid: int):
        c = conn()
        g_ = scheduler.gate(c)
        if not g_["can_add"]:
            return jsonify({"error": "在途已满，先归档或移出一题"}), 409
        c.execute("UPDATE problem SET phase='active' WHERE id=?", (pid,))
        c.commit()
        return jsonify({"ok": True})

    @app.post("/api/problems/<int:pid>/deactivate")
    def api_problem_deactivate(pid: int):
        """手动移回候补（排到队尾），空出的名额由队头补上。"""
        c = conn()
        nxt = c.execute(
            "SELECT COALESCE(MAX(order_no), 0) + 1 m FROM problem WHERE phase='queued'"
        ).fetchone()["m"]
        c.execute("UPDATE problem SET phase='queued', order_no=? WHERE id=?", (nxt, pid))
        c.commit()
        others = c.execute(
            "SELECT COUNT(*) c FROM problem WHERE phase='queued' AND id<>?", (pid,)
        ).fetchone()["c"]
        if others == 0:
            return jsonify({"ok": True, "refilled": 0,
                            "note": "候补队列是空的，这个名额先空着"})
        return jsonify({"ok": True, "refilled": scheduler.refill(c)})

    @app.delete("/api/problems/<int:pid>")
    def api_problem_delete(pid: int):
        c = conn()
        c.execute("DELETE FROM problem WHERE id=?", (pid,))
        c.commit()
        scheduler.refill(c)
        return jsonify({"ok": True})

    @app.post("/api/problems")
    def api_problem_create():
        c = conn()
        b = request.get_json(force=True) or {}
        raw = str(b.get("leetcode_id") or "").strip()
        lid = int(raw) if raw.isdigit() else None
        title = (b.get("title") or "").strip() or (f"LeetCode {lid}" if lid else "")
        if not title:
            return jsonify({"error": "至少给个题号或标题"}), 400

        if lid:
            dup = c.execute("SELECT * FROM problem WHERE leetcode_id=?", (lid,)).fetchone()
            if dup:
                return jsonify({"error": "此题已录用", "code": "DUPLICATE"}), 409

        g_ = scheduler.gate(c)
        phase = "active" if g_["can_add"] else "queued"
        url = b.get("url") or (f"https://leetcode.cn/problems/{b['slug']}/" if b.get("slug") else "")
        solved = 1 if b.get("solved") else 0
        cur = c.execute(
            "INSERT INTO problem(leetcode_id,slug,title,difficulty,url,tags,status,phase,"
            "next_due,solved,solved_at) VALUES(?,?,?,?,?,?,'inbox',?,?,?,?)",
            (lid, b.get("slug", ""), title, b.get("difficulty", ""), url, b.get("tags", ""),
             phase, scheduler.due_date(), solved, scheduler.today_str() if solved else None),
        )
        pid = cur.lastrowid
        placed = _place_in_slot(c, pid, b.get("slot")) if phase == "active" else None
        c.commit()
        return jsonify({"id": pid, "queued": phase == "queued", "slot": placed})

    @app.put("/api/problems/<int:pid>")
    def api_problem_update(pid: int):
        c = conn()
        b = request.get_json(force=True) or {}
        if "solved" in b:
            solved = 1 if b["solved"] else 0
            c.execute(
                "UPDATE problem SET solved=?, solved_at=? WHERE id=?",
                (solved, scheduler.today_str() if solved else None, pid),
            )
        c.commit()
        return jsonify({"ok": True})

    @app.post("/api/capture")
    def api_capture():
        """扩展专用：一步搞定「入库 + 挂卡 + 排班」，方便离线排队重放。"""
        c = conn()
        b = request.get_json(force=True) or {}
        raw = str(b.get("leetcode_id") or "").strip()
        lid = int(raw) if raw.isdigit() else None
        title = (b.get("title") or "").strip() or (f"LeetCode {lid}" if lid else "")
        if not title:
            return jsonify({"error": "缺标题"}), 400

        created = False
        row = None
        if lid:
            row = c.execute("SELECT * FROM problem WHERE leetcode_id=?", (lid,)).fetchone()
        if row:
            # 同一题只录用一次，避免重复占在途名额
            return jsonify({"error": "此题已录用", "code": "DUPLICATE",
                            "problem_id": row["id"]}), 409

        g_ = scheduler.gate(c)
        phase = "active" if g_["can_add"] else "queued"   # 在途满了就排队，不拒收

        solved = 1 if b.get("solved") else 0
        cur = c.execute(
            "INSERT INTO problem(leetcode_id,slug,title,difficulty,url,tags,status,phase,"
            "next_due,solved,solved_at) VALUES(?,?,?,?,?,?,'inbox',?,?,?,?)",
            (lid, b.get("slug", ""), title, b.get("difficulty", ""), b.get("url", ""),
             b.get("tags", ""), phase, scheduler.due_date(), solved,
             scheduler.today_str() if solved else None),
        )
        pid = cur.lastrowid
        created = True

        card_id = b.get("card_id")
        if not card_id and (b.get("new_card_title") or "").strip():
            cur = c.execute(
                "INSERT INTO pattern_card(title,trigger_signals,category) VALUES(?,?,?)",
                (b["new_card_title"].strip(), b.get("new_card_trigger", ""), b.get("category", "")),
            )
            card_id = cur.lastrowid
            c.execute(
                "INSERT INTO card_state(card_id,stage,next_due) VALUES(?,1,?)",
                (card_id, scheduler.today_str()),
            )
        if card_id:
            c.execute(
                "INSERT OR IGNORE INTO card_problem(card_id,problem_id) VALUES(?,?)",
                (card_id, pid),
            )
            c.execute("UPDATE problem SET status='linked' WHERE id=?", (pid,))

        placed = _place_in_slot(c, pid, b.get("slot")) if phase == "active" else None
        c.commit()
        return jsonify({"problem_id": pid, "created": created, "card_id": card_id,
                        "duplicate": not created, "queued": phase == "queued",
                        "slot": placed})

    @app.post("/api/problems/<int:pid>/link")
    def api_problem_link(pid: int):
        c = conn()
        b = request.get_json(force=True) or {}
        cid = int(b["card_id"])
        c.execute("INSERT OR IGNORE INTO card_problem(card_id,problem_id) VALUES(?,?)", (cid, pid))
        c.execute("UPDATE problem SET status='linked' WHERE id=?", (pid,))
        c.commit()
        return jsonify({"ok": True})

    @app.delete("/api/cards/<int:cid>/problems/<int:pid>")
    def api_card_unlink(cid: int, pid: int):
        """从题型里移除一道题，没挂别的题型就退回待归纳区。"""
        c = conn()
        c.execute("DELETE FROM card_problem WHERE card_id=? AND problem_id=?", (cid, pid))
        left = c.execute(
            "SELECT COUNT(*) c FROM card_problem WHERE problem_id=?", (pid,)
        ).fetchone()["c"]
        if left == 0:
            c.execute("UPDATE problem SET status='inbox' WHERE id=?", (pid,))
        c.commit()
        return jsonify({"ok": True, "back_to_inbox": left == 0})

    # ---------- 检验项 ----------
    @app.get("/api/checks")
    def api_checks():
        return jsonify([dict(r) for r in _active_checks(conn())])

    @app.post("/api/checks")
    def api_check_create():
        c = conn()
        b = request.get_json(force=True) or {}
        name = (b.get("name") or "").strip()
        if not name:
            return jsonify({"error": "名称不能为空"}), 400
        mx = c.execute("SELECT COALESCE(MAX(sort_order),0) m FROM check_item").fetchone()["m"]
        cur = c.execute(
            "INSERT INTO check_item(name,hint,sort_order,is_active) VALUES(?,?,?,1)",
            (name, b.get("hint", ""), mx + 1),
        )
        c.commit()
        return jsonify({"id": cur.lastrowid})

    @app.put("/api/checks/<int:iid>")
    def api_check_update(iid: int):
        c = conn()
        b = request.get_json(force=True) or {}
        if "is_active" in b:
            c.execute("UPDATE check_item SET is_active=? WHERE id=?", (1 if b["is_active"] else 0, iid))
        if "name" in b:
            c.execute("UPDATE check_item SET name=? WHERE id=?", (b["name"], iid))
        if "hint" in b:
            c.execute("UPDATE check_item SET hint=? WHERE id=?", (b["hint"], iid))
        c.commit()
        return jsonify({"ok": True})

    @app.delete("/api/checks/<int:iid>")
    def api_check_delete(iid: int):
        c = conn()
        c.execute("DELETE FROM check_item WHERE id=?", (iid,))
        c.commit()
        return jsonify({"ok": True})

    # ---------- 设置 ----------
    @app.get("/api/settings")
    def api_settings():
        c = conn()
        return jsonify({"settings": _settings_payload(c), "data_root": str(app.config["ROOT"])})

    @app.put("/api/settings")
    def api_settings_update():
        c = conn()
        b = request.get_json(force=True) or {}
        for k, v in b.items():
            if k in db.DEFAULT_SETTINGS:
                db.set_setting(c, k, v)
        return jsonify({"settings": _settings_payload(c)})

    # ---------- 数据根迁移 ----------
    @app.post("/api/data-root")
    def api_data_root():
        b = request.get_json(force=True) or {}
        target = Path(str(b.get("path", "")).strip())
        if not str(target):
            return jsonify({"error": "路径为空"}), 400
        old = Path(app.config["ROOT"])
        try:
            target.mkdir(parents=True, exist_ok=True)
            if b.get("migrate", True) and old.resolve() != target.resolve():
                for item in old.iterdir():
                    dst = target / item.name
                    if item.is_dir():
                        shutil.copytree(item, dst, dirs_exist_ok=True)
                    elif not dst.exists():
                        shutil.copy2(item, dst)
        except OSError as e:
            return jsonify({"error": f"迁移失败：{e}"}), 500
        config.save_pointer(target)
        app.config["ROOT"] = target
        _INITED.discard(str(target))
        return jsonify({"ok": True, "data_root": str(target)})

    # ---------- 统计 ----------
    @app.get("/api/stats")
    def api_stats():
        c = conn()
        one = lambda q, a=(): c.execute(q, a).fetchone()[0]
        this_year = date.today().year
        year = int(request.args.get("year") or this_year)

        # ---- 解题进度（本地口径）----
        diff_rows = c.execute(
            "SELECT COALESCE(NULLIF(difficulty,''),'未标注') d, COALESCE(SUM(solved),0) s "
            "FROM problem GROUP BY d"
        ).fetchall()
        by_diff = {r["d"]: r["s"] for r in diff_rows}
        totals = {
            "简单": db.get_int(c, "total_easy", 1094),
            "中等": db.get_int(c, "total_medium", 2334),
            "困难": db.get_int(c, "total_hard", 1056),
        }
        progress, solved_all, total_all = [], 0, 0
        for name, tot in totals.items():
            s = by_diff.get(name, 0)
            progress.append({"name": name, "solved": s, "total": tot})
            solved_all += s
            total_all += tot

        # ---- 该自然年每天完成次数（答对答错都算）----
        cal_rows = c.execute(
            "SELECT substr(reviewed_at,1,10) d, COUNT(*) n FROM review_log "
            "WHERE substr(reviewed_at,1,4)=? GROUP BY d",
            (str(year),),
        ).fetchall()
        calendar = {r["d"]: r["n"] for r in cal_rows}

        streak = 0
        if year == this_year:
            cur = date.today()
            if cur.isoformat() not in calendar:
                cur -= timedelta(days=1)
            while cur.isoformat() in calendar:
                streak += 1
                cur -= timedelta(days=1)

        years = [int(r["y"]) for r in c.execute(
            "SELECT DISTINCT substr(reviewed_at,1,4) y FROM review_log "
            "WHERE reviewed_at IS NOT NULL ORDER BY y DESC")]
        if this_year not in years:
            years.insert(0, this_year)

        recent = c.execute(
            "SELECT r.reviewed_at, r.problem_id, p.title, p.difficulty FROM review_log r "
            "JOIN problem p ON p.id = r.problem_id WHERE r.result='correct' "
            "ORDER BY r.reviewed_at DESC LIMIT 12"
        ).fetchall()

        by_stage = [
            dict(r) for r in c.execute(
                "SELECT stage_from AS stage, COUNT(*) n, SUM(result='correct') p "
                "FROM review_log WHERE result IN ('correct','wrong') "
                "AND stage_from IS NOT NULL GROUP BY stage_from"
            )
        ]
        errors = [
            dict(r) for r in c.execute(
                "SELECT COALESCE(error_type,'未标注') t, COUNT(*) n FROM review_log "
                "WHERE result='wrong' GROUP BY t ORDER BY n DESC"
            )
        ]
        daily = [
            dict(r) for r in c.execute(
                "SELECT substr(reviewed_at,1,10) d, COUNT(*) n FROM review_log "
                "GROUP BY d ORDER BY d DESC LIMIT 14"
            )
        ]
        return jsonify({
            "active": one("SELECT COUNT(*) FROM problem WHERE phase='active'"),
            "done": one("SELECT COUNT(*) FROM problem WHERE phase='done'"),
            "queued": one("SELECT COUNT(*) FROM problem WHERE phase='queued'"),
            "cards": one("SELECT COUNT(*) FROM pattern_card WHERE status='active'"),
            "inbox": one("SELECT COUNT(*) FROM problem WHERE status='inbox' AND phase<>'done'"),
            "due_today": len(scheduler.due_problems(c)),
            "reviews": one("SELECT COUNT(*) FROM review_log WHERE result<>'skip'"),
            "gate": scheduler.gate(c),
            "backlog": scheduler.backlog(c),
            "progress": progress,
            "progress_total": {"solved": solved_all, "total": total_all},
            "calendar": calendar,
            "calendar_stats": {
                "year": year,
                "total": sum(calendar.values()),
                "active_days": len(calendar),
                "streak": streak,
            },
            "years": years,
            "recent_pass": [dict(r) for r in recent],
            "by_stage": by_stage,
            "errors": errors,
            "daily": list(reversed(daily)),
        })

    # ---------- 导出 ----------
    @app.post("/api/export/cards")
    def api_export_cards():
        c = conn()
        outdir = Path(app.config["ROOT"]) / config.CARD_EXPORT_DIR
        outdir.mkdir(parents=True, exist_ok=True)
        n = 0
        for r in c.execute("SELECT * FROM pattern_card"):
            safe = "".join(ch for ch in r["title"] if ch not in '\\/:*?"<>|').strip() or f"card-{r['id']}"
            md = [
                f"# {r['title']}", "",
                f"**触发信号**\n{r['trigger_signals']}", "",
                f"**一句话本质**\n{r['essence']}", "",
                "**骨架**\n```\n" + (r["skeleton_code"] or "") + "\n```", "",
                f"**边界与坑**\n{r['pitfalls']}", "",
                f"**复杂度**\n{r['complexity']}", "",
                f"**变形族**\n{r['variants']}", "",
            ]
            (outdir / f"{safe}.md").write_text("\n".join(md), encoding="utf-8")
            n += 1
        return jsonify({"ok": True, "count": n, "dir": str(outdir)})

    return app


# ---------- 辅助 ----------
SLOTS = ("morning", "noon", "evening")


def _auto_slot() -> str:
    """按录入时刻自动落到哪个班：上午 12 点前早班，18 点前午班，之后晚班。"""
    h = datetime.now().hour
    if h < 12:
        return "morning"
    if h < 18:
        return "noon"
    return "evening"


def _place_in_slot(c: sqlite3.Connection, problem_id: int, slot: str | None) -> str | None:
    """给了班次就直接排进今天的那个班；没给就留在待归纳区等下一次排班。"""
    if slot == "auto":
        slot = _auto_slot()
    if slot not in SLOTS or not problem_id:
        return None
    today = scheduler.today_str()
    exists = c.execute(
        "SELECT 1 FROM daily_plan WHERE plan_date=? AND problem_id=?", (today, problem_id)
    ).fetchone()
    if exists:
        return None
    c.execute(
        "INSERT INTO daily_plan(plan_date, slot, problem_id) VALUES(?,?,?)",
        (today, slot, problem_id),
    )
    return slot


def _active_checks(c: sqlite3.Connection):
    return c.execute(
        "SELECT * FROM check_item WHERE is_active=1 ORDER BY sort_order, id"
    ).fetchall()


def _settings_payload(c: sqlite3.Connection) -> dict:
    out = {}
    for k, v in db.DEFAULT_SETTINGS.items():
        out[k] = db.get_setting(c, k, v)
    out["intervals"] = db.intervals(c)
    return out


def _log(c, problem_id, card_id, result, sfrom, sto, passed, total,
         checks, error_type, dur, note, next_due_before=None, solved_before=None):
    c.execute(
        """INSERT INTO review_log(problem_id,card_id,result,stage_from,stage_to,
                                  passed,total,checks_json,error_type,duration_sec,note,
                                  next_due_before,solved_before)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (problem_id, card_id, result, sfrom, sto, passed, total,
         json.dumps(checks, ensure_ascii=False), error_type, dur, note,
         next_due_before, solved_before),
    )


def _mark_plan_done(c: sqlite3.Connection, problem_id: int) -> None:
    c.execute(
        "UPDATE daily_plan SET done=1 WHERE plan_date=? AND problem_id=?",
        (scheduler.today_str(), problem_id),
    )
