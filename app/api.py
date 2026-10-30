"""HTTP 接口层。所有路径读写都基于「当前数据根」，换盘只需改 root。"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory

from . import config, db, scheduler, state


def create_app(root: Path | None = None) -> Flask:
    root = Path(root) if root else config.locate()
    config.ensure_dirs(root)

    app = Flask(__name__, static_folder="web", static_url_path="")
    app.config["ROOT"] = root
    app.json.ensure_ascii = False

    def conn() -> sqlite3.Connection:
        if "db" not in g:
            g.db = db.connect(app.config["ROOT"])
            db.init(g.db)
        return g.db

    @app.teardown_appcontext
    def _close(_exc):
        c = g.pop("db", None)
        if c is not None:
            c.close()

    # ---------- 静态页 ----------
    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    # ---------- 今日 ----------
    @app.get("/api/today")
    def api_today():
        c = conn()
        today = scheduler.today_str()
        scheduler.build_plan(c, today)
        rows = c.execute(
            """
            SELECT p.id AS plan_id, p.slot, p.done, p.card_id, p.problem_id,
                   c.title, c.category, c.trigger_signals,
                   s.stage, s.next_due,
                   pr.title AS problem_title, pr.leetcode_id
            FROM daily_plan p
            LEFT JOIN pattern_card c ON c.id = p.card_id
            LEFT JOIN card_state  s ON s.card_id = p.card_id
            LEFT JOIN problem    pr ON pr.id = p.problem_id
            WHERE p.plan_date = ?
            ORDER BY CASE p.slot WHEN 'morning' THEN 0 WHEN 'noon' THEN 1 ELSE 2 END, p.id
            """,
            (today,),
        ).fetchall()
        slots: dict[str, list] = {"morning": [], "noon": [], "evening": []}
        for r in rows:
            slots[r["slot"]].append(dict(r))
        return jsonify(
            {
                "date": today,
                "gate": scheduler.gate(c),
                "backlog": scheduler.backlog(c, today),
                "slots": slots,
                "checks": [dict(x) for x in _active_checks(c)],
                "settings": _settings_payload(c),
            }
        )

    # ---------- 复习 ----------
    @app.post("/api/review")
    def api_review():
        c = conn()
        body = request.get_json(force=True) or {}
        card_id = int(body["card_id"])
        today = date.today()

        st = c.execute("SELECT * FROM card_state WHERE card_id=?", (card_id,)).fetchone()
        if not st:
            return jsonify({"error": "卡片无状态记录"}), 404

        if body.get("skip"):
            _log(c, card_id, state.SKIP, st["stage"], st["stage"], 0, 0, [], None, 0, "")
            _mark_done(c, card_id)
            return jsonify({"result": "skip", "stage": st["stage"], "next_due": st["next_due"]})

        checks = body.get("checks", [])
        passed = sum(1 for x in checks if x.get("ok"))
        total = len(checks)
        threshold = db.get_float(c, "pass_threshold", 0.8)
        hold_limit = db.get_int(c, "hold_limit", 2)
        ivs = db.intervals(c)

        out = state.transition(
            stage=st["stage"],
            hold_count=st["hold_count"],
            fail_count=st["fail_count"],
            passed=passed,
            total=total,
            threshold=threshold,
            hold_limit=hold_limit,
            intervals=ivs,
            today=today,
        )

        c.execute(
            """UPDATE card_state
               SET stage=?, next_due=?, pass_streak=?, hold_count=?, fail_count=?, archived_at=?
               WHERE card_id=?""",
            (
                out.stage_to,
                out.next_due,
                (st["pass_streak"] + 1) if out.result == state.PASS else 0,
                out.hold_count,
                out.fail_count,
                today.isoformat() if out.archived else None,
                card_id,
            ),
        )
        if out.archived:
            c.execute("UPDATE pattern_card SET status='archived' WHERE id=?", (card_id,))

        _log(
            c, card_id, out.result, out.stage_from, out.stage_to, passed, total,
            checks, body.get("error_type"), int(body.get("duration_sec") or 0), body.get("note", ""),
        )
        _mark_done(c, card_id)
        c.commit()
        return jsonify(
            {
                "result": out.result,
                "stage_from": out.stage_from,
                "stage_to": out.stage_to,
                "next_due": out.next_due,
                "archived": out.archived,
                "ratio": round(passed / total, 2) if total else 0,
            }
        )

    @app.post("/api/plan/<int:plan_id>/done")
    def api_plan_done(plan_id: int):
        c = conn()
        c.execute("UPDATE daily_plan SET done=1 WHERE id=?", (plan_id,))
        c.commit()
        return jsonify({"ok": True})

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

    @app.post("/api/problems")
    def api_problem_create():
        c = conn()
        b = request.get_json(force=True) or {}
        raw = str(b.get("leetcode_id") or "").strip()
        lid = int(raw) if raw.isdigit() else None
        title = (b.get("title") or "").strip() or (f"LeetCode {lid}" if lid else "")
        if not title:
            return jsonify({"error": "至少给个题号或标题"}), 400
        url = b.get("url") or (f"https://leetcode.cn/problems/{b['slug']}/" if b.get("slug") else "")
        cur = c.execute(
            "INSERT INTO problem(leetcode_id,slug,title,difficulty,url,tags,status) VALUES(?,?,?,?,?,?,'inbox')",
            (lid, b.get("slug", ""), title, b.get("difficulty", ""), url, b.get("tags", "")),
        )
        c.commit()
        return jsonify({"id": cur.lastrowid})

    @app.post("/api/problems/<int:pid>/link")
    def api_problem_link(pid: int):
        c = conn()
        b = request.get_json(force=True) or {}
        cid = int(b["card_id"])
        c.execute("INSERT OR IGNORE INTO card_problem(card_id,problem_id) VALUES(?,?)", (cid, pid))
        c.execute("UPDATE problem SET status='linked' WHERE id=?", (pid,))
        c.commit()
        return jsonify({"ok": True})

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
        return jsonify({"ok": True, "data_root": str(target)})

    # ---------- 统计 ----------
    @app.get("/api/stats")
    def api_stats():
        c = conn()
        one = lambda q, a=(): c.execute(q, a).fetchone()[0]
        by_stage = [
            dict(r) for r in c.execute(
                "SELECT stage_from AS stage, COUNT(*) n, SUM(result='pass') p "
                "FROM review_log WHERE stage_from IS NOT NULL GROUP BY stage_from"
            )
        ]
        errors = [
            dict(r) for r in c.execute(
                "SELECT COALESCE(error_type,'未标注') t, COUNT(*) n FROM review_log "
                "WHERE result IN ('fail','hold') GROUP BY t ORDER BY n DESC"
            )
        ]
        daily = [
            dict(r) for r in c.execute(
                "SELECT substr(reviewed_at,1,10) d, COUNT(*) n FROM review_log "
                "GROUP BY d ORDER BY d DESC LIMIT 14"
            )
        ]
        return jsonify({
            "active": one("SELECT COUNT(*) FROM pattern_card WHERE status='active'"),
            "archived": one("SELECT COUNT(*) FROM pattern_card WHERE status='archived'"),
            "inbox": one("SELECT COUNT(*) FROM problem WHERE status='inbox'"),
            "due_today": len(scheduler.due_cards(c)),
            "reviews": one("SELECT COUNT(*) FROM review_log"),
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


def _log(c, card_id, result, sfrom, sto, passed, total, checks, error_type, dur, note):
    c.execute(
        """INSERT INTO review_log(card_id,result,stage_from,stage_to,passed,total,
                                  checks_json,error_type,duration_sec,note)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (card_id, result, sfrom, sto, passed, total,
         json.dumps(checks, ensure_ascii=False), error_type, dur, note),
    )


def _mark_done(c: sqlite3.Connection, card_id: int) -> None:
    c.execute(
        "UPDATE daily_plan SET done=1 WHERE plan_date=? AND card_id=?",
        (scheduler.today_str(), card_id),
    )
