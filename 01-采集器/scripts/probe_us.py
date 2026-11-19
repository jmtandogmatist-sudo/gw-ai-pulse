"""用生产提示词，对同一批既有信号重算一次 `us`，和库里的旧 `us` 逐条对比。

为什么需要这个脚本：改了 tagger 的 us 口径（2026-11-19：公开可读，不写集团内部动作），
要验证「模型真的照做」，但跑整轮 dry-run 有两个麻烦——
  1. 今晚跑会撞 URL 去重（今天的条目都在库里），候选会被吃光；
  2. 一轮 6 分钟，只为看十几个字太重。
这个脚本直接从 index.sqlite 取已有信号当物料，用**完全相同的 system/user prompt**
问一次模型，只比 `us`。一次调用，几秒钟。

用法（在 01-采集器 目录下）：
    ../.venv/bin/python scripts/probe_us.py            # 用生产模型
    ../.venv/bin/python scripts/probe_us.py --fake     # 不调模型，只验证脚本本身能跑通

输出：终端表格 + logs/us-probe-<时间戳>.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collector.config import load_config                      # noqa: E402
from collector.fakes import FakeLLM                          # noqa: E402
from collector.models import Candidate                       # noqa: E402
from collector.store import Store                            # noqa: E402
from collector.tagger import (LLMClient, build_system_prompt,  # noqa: E402
                              build_user_prompt, parse_json_array)

CST = ZoneInfo("Asia/Shanghai")
BAD = ["集团", "公司需", "需核查", "安排", "申报", "内部", "我方", "团队", "跟进"]


def to_candidate(sig) -> Candidate:
    src = sig.sources[0] if sig.sources else None
    return Candidate(module="ext_ai", source_name=(src.name if src else "未知"),
                     source_tier=(src.tier if src else "media"), url=(src.url if src else ""),
                     title=sig.title, excerpt=sig.summary,
                     published_at=sig.published_at or sig.discovered_at)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8, help="取样条数（默认 8）")
    ap.add_argument("--fake", action="store_true", help="用模拟模型，只验证脚本能跑通")
    args = ap.parse_args()

    cfg = load_config()
    # 注意 recent_signals 的第一个位置参数是 since_iso 时间戳，不是条数（传错会静默返回 0 行）
    since = (datetime.now(CST) - timedelta(days=30)).isoformat(timespec="seconds")
    sigs = [s for s in Store(ROOT / "state" / "index.sqlite").recent_signals(since, 300)
            if (s.us or "").strip()]
    if not sigs:
        print("库里没有带 us 的信号，无法对比")
        return 2
    # 跨领域取样，避免全是同一类
    seen_domain, picked = {}, []
    for s in sigs:
        if seen_domain.get(s.domain, 0) >= 2:
            continue
        seen_domain[s.domain] = seen_domain.get(s.domain, 0) + 1
        picked.append(s)
        if len(picked) >= args.n:
            break

    batch = [to_candidate(s) for s in picked]
    client = FakeLLM(cfg) if args.fake else LLMClient(cfg)
    try:
        reply = client.chat(build_system_prompt(cfg), build_user_prompt(batch))
    except Exception as e:                                   # 内网接口不通时给出人话
        print(f"模型调用失败：{type(e).__name__}: {e}")
        print(f"（接口 {cfg.settings['llm']['base_url']}；若本机不在集团内网/VPN，请在有内网的环境重跑）")
        return 3
    items = {int(o["idx"]): o for o in parse_json_array(reply) if "idx" in o}

    rows, date = [], datetime.now(CST).strftime("%Y%m%d-%H%M%S")
    for i, s in enumerate(picked):
        new = (items.get(i) or {}).get("us") or ""
        rows.append({"title": s.title, "domain": s.domain, "old_us": s.us,
                     "new_us": str(new).strip(), "keep": (items.get(i) or {}).get("keep")})
    out = ROOT / "logs" / f"us-probe-{date}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n取样 {len(rows)} 条，模型 {cfg.settings['llm']['model']}，结果写到 {out}\n")
    for r in rows:
        print(f"[{r['domain']}] {r['title'][:34]}")
        print(f"   旧：{r['old_us']}")
        print(f"   新：{r['new_us'] or '（空）'}")
        print()

    def stats(key):
        vals = [r[key] for r in rows if r[key]]
        if not vals:
            return 0, 0.0, 0.0
        bad = sum(1 for v in vals if any(b in v for b in BAD))
        return len(vals), sum(map(len, vals)) / len(vals), bad / len(vals)

    for label, key in (("旧 us", "old_us"), ("新 us", "new_us")):
        n, avg, bad = stats(key)
        print(f"{label}: 非空 {n}/{len(rows)} · 平均 {avg:.0f} 字 · 含内部动作词 {bad:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
