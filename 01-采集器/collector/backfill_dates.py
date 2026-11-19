"""一次性回填：给历史信号补 event_date（发布日期）。

背景（2026-11-16）
------------------
72h 窗口漏洞修复前入库的信号里，有 4 条 event_date 为空。它们的来源 URL 其实
带日期戳，只是当时没人去读。其中一条是 2026-10-30 的网信办公告——超窗 17 天，
本该在 11-13 就被拦下。

补上 event_date 之后：
  * 632 的 radar 里这些条目的 published_at 变成真实发布日期，前端显示「10月30日」
    而不是误导性的「今天」；
  * 若发布日期已超出 72h 窗口，update_feed 会让它自然掉出 radar。

用法：
    ../.venv/bin/python -m collector.backfill_dates           # 只报告，不改
    ../.venv/bin/python -m collector.backfill_dates --apply   # 真的写回
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import get_secret  # noqa: F401 —— 保证钥匙串可用性在启动时就暴露
from .pubdate import date_from_url
from .store import Store

log = logging.getLogger("collector.backfill_dates")


def run(store: Store, *, apply: bool = False) -> dict:
    rows = store.db.execute("SELECT signal_id, data FROM signals").fetchall()
    checked = filled = undatable = already = 0
    changes = []
    for signal_id, blob in rows:
        checked += 1
        data = json.loads(blob)
        if data.get("event_date"):
            already += 1
            continue
        url = ((data.get("sources") or [{}])[0] or {}).get("url", "")
        iso = date_from_url(url)
        if not iso:
            undatable += 1
            continue
        filled += 1
        changes.append((signal_id, data.get("title", ""), url, iso[:10]))
        if apply:
            data["event_date"] = iso[:10]
            store.db.execute(
                "UPDATE signals SET data=? WHERE signal_id=?",
                (json.dumps(data, ensure_ascii=False), signal_id),
            )
    if apply and changes:
        store.db.commit()
    return {"checked": checked, "already": already, "filled": filled,
            "undatable": undatable, "applied": bool(apply), "changes": changes}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="给历史信号回填 event_date")
    ap.add_argument("--apply", action="store_true", help="真的写回（默认只报告）")
    ap.add_argument("--db", default="state/index.sqlite")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    store = Store(args.db)
    r = run(store, apply=args.apply)
    print(f"扫描 {r['checked']} 条：已有 event_date {r['already']}，"
          f"可回填 {r['filled']}，提不出日期 {r['undatable']}")
    for sid, title, url, day in r["changes"]:
        print(f"  {day}  {title[:44]}")
        print(f"         {url}")
    if r["changes"] and not args.apply:
        print("\n（未写入。加 --apply 生效）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
