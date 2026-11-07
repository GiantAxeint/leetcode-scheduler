"""存量题的 next_due 按当前排期设置重算。

解决的问题：`skip_weekend` 开关只影响「之后新算的日期」，已经写进库的
`next_due` 还是旧规则算出来的。表现是：明明勾着「跳过周末」，日历上
却有一批题压在周六。

能做的事（只做这两件，别扩大）：
  1. 落在周末的日期 → 顺延到下一个工作日（仅在 skip_weekend=1 时）
  2. 顺延后会超 daily_capacity 的 → 往后找第一个有空位的日子

不能做的事：把工作日「反向挪回」周末。原日期没记录它本来该是哪天，
反推就是瞎猜。所以 skip_weekend=0 时这个脚本什么都不做。

用法：
    .venv\\Scripts\\python tools\\recalc_schedule.py            # 只看，不写
    .venv\\Scripts\\python tools\\recalc_schedule.py --apply    # 真写（会自动备份）

数据根按 config.locate() 走（环境变量 → data_root.txt → APPDATA → 默认）。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db, scheduler  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _count(con, iso: str, exclude_id: int, extra: dict[str, int]) -> int:
    """当天已占名额 = 库里的 + 本次演练中已分配到那天的。"""
    return scheduler.day_load(con, iso, exclude_id) + extra.get(iso, 0)


def _fit(con, iso: str, cap: int, exclude_id: int, extra: dict[str, int],
         span: int = 31) -> str:
    """与 scheduler.fit_due 同规则，但多了一个 extra 参数用于 dry-run 演练。"""
    d = date.fromisoformat(iso)
    for _ in range(span + 1):
        if scheduler.is_workday(d):
            key = d.isoformat()
            if _count(con, key, exclude_id, extra) < cap:
                return key
        d += timedelta(days=1)
    return iso


def main() -> int:
    ap = argparse.ArgumentParser(description="按当前设置重算存量题的 next_due")
    ap.add_argument("--apply", action="store_true", help="真的写库（默认只预览）")
    ap.add_argument("--db", default="", help="指定 db 文件，默认按 data_root.txt 定位")
    args = ap.parse_args()

    if args.db:
        db_file = Path(args.db)
        root = db_file.parent
    else:
        root = config.locate()
        db_file = config.db_path(root)

    if not db_file.is_file():
        print(f"找不到数据库：{db_file}")
        return 2

    con = db.connect(root)
    db.init(con)
    scheduler.set_skip_weekend(db.get_int(con, "skip_weekend", 1) != 0)

    skip_weekend = db.get_int(con, "skip_weekend", 1) != 0
    cap = max(1, db.get_int(con, "daily_capacity", 6))

    print(f"数据库    {db_file}")
    print(f"skip_weekend={int(skip_weekend)}  daily_capacity={cap}")
    print(f"模式      {'APPLY（会写库）' if args.apply else 'DRY-RUN（只看）'}")
    print("-" * 64)

    if not skip_weekend:
        print("当前开关允许周末，没有可顺延的日期。什么都没做。")
        return 0

    rows = con.execute(
        "SELECT id, title, leetcode_id, stage, next_due FROM problem "
        "WHERE phase='active' AND next_due IS NOT NULL "
        "ORDER BY next_due, stage, id"
    ).fetchall()

    moves: list[tuple] = []
    extra: dict[str, int] = {}

    for r in rows:
        d = date.fromisoformat(r["next_due"])
        if d.weekday() < 5:
            continue                      # 本来就在工作日，一个字节都不动
        target = _fit(con, r["next_due"], cap, r["id"], extra)
        if target == r["next_due"]:
            continue
        moves.append((r["id"], r["next_due"], target, r["title"], r["stage"]))
        # 不管最后写不写库，都先按「已分配」计数，否则两条题会算到同一天
        extra[target] = extra.get(target, 0) + 1

    if not moves:
        print("没有落在周末的在途题，不需要重算。")
        return 0

    print(f"{'题号':<8}{'档位':<6}{'原日期':<14}{'新日期':<14}标题")
    for pid, old, new, title, stage in moves:
        lc = next((r["leetcode_id"] for r in rows if r["id"] == pid), "")
        print(f"{str(lc or pid):<8}S{stage:<5}{old:<14}{new:<14}{title}")

    print("-" * 64)
    print(f"共 {len(moves)} 道要挪。")

    if not args.apply:
        print("这是预览。确认没问题后加 --apply 真写（会自动备份）。")
        return 0

    # 先备份、再写。顺序不能反 —— 反了备份下来的就是改过的库。
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bdir = root / config.BACKUP_DIR
    bdir.mkdir(parents=True, exist_ok=True)
    bak = bdir / f"刷题调度_before_recalc_{stamp}.db"
    shutil.copy2(db_file, bak)

    for pid, _old, new, *_ in moves:
        con.execute("UPDATE problem SET next_due=? WHERE id=?", (new, pid))
    con.commit()
    con.close()
    print(f"已写入。备份：{bak}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
