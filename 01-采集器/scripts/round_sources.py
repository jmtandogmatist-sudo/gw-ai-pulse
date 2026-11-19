"""按信源统计最近一轮（或最近 N 小时）发出的信号，用来判断「一手来源占比」有没有改善。

用法：
    ../.venv/bin/python scripts/round_sources.py            # 最近一轮
    ../.venv/bin/python scripts/round_sources.py 24         # 最近 24 小时
    ../.venv/bin/python scripts/round_sources.py 12 --by-tier

只读，不写论坛、不改库。
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collector.config import load_config          # noqa: E402
from collector.sources.fetchers import CST        # noqa: E402
from collector.store import Store                 # noqa: E402


def main() -> int:
    cfg = load_config(ROOT)
    store = Store(ROOT / "state" / "index.sqlite")

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    by_tier = "--by-tier" in sys.argv

    runs = store.runs_since((datetime.now(CST) - timedelta(days=3)).isoformat())
    last = runs[-1] if runs and not args else None
    if last is not None:
        since = last["started_at"]
        print(f"最近一轮：{since} → {'成功' if last['ok'] else '失败'}  "
              f"发帖 {(last['report'] or {}).get('posted', 0)}  "
              f"白名单补判 {(last['report'] or {}).get('keep_bypassed', 0)}  条")
    else:
        hours = float(args[0]) if args else 6.0
        since = (datetime.now(CST) - timedelta(hours=hours)).isoformat(timespec="seconds")
        print(f"窗口：最近 {hours:g} 小时（{since}）")

    sigs = store.recent_signals(since, limit=2000)
    if not sigs:
        print("这段时间没有新信号。")
        return 0

    print(f"共 {len(sigs)} 条信号\n")
    key3 = Counter()
    for s in sigs:
        src = s.sources[0].name if s.sources else "（无数源）"
        tier = s.sources[0].tier if s.sources else "?"
        print(f"  [{tier:9s}] {src:22s} | rel={s.relevance} cred={s.cred:6s} | {s.title[:46]}")
        key3[(tier, src)] += 1

    print()
    print("按信源：")
    for src, n in Counter({f"{t} / {s}": c for (t, s), c in key3.items()}).most_common():
        print(f"  {n:3d}  {src}")

    if by_tier:
        print()
        print("按信源层级：")
        tiers = Counter(s.sources[0].tier if s.sources else "?" for s in sigs)
        total = sum(tiers.values())
        for t, n in tiers.most_common():
            print(f"  {n:3d}  {t:9s} {n / total * 100:5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
