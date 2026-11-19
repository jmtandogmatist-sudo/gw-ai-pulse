"""漏斗归因：把「626 条候选 → 30 条帖子」逐层拆开，回答「丢掉的都是什么」。

三个问题对应三个阶段：
  --fetch   抓一次并存快照（之后分析不必再联网）
  (默认)    规则层归因：重复 / 超窗 / 无日期 / 域名拦截，各自丢了多少、丢了谁的
  --tag     模型层归因：keep=false 的到底是「真不值得」还是「提示词没问对」

设计要点：规则层严格复刻 pipeline.run_round 的顺序与判据（去重 → 补日期 → 窗口 →
域名拦截 → 无日期策略），这样归因数字能和 runs 表里的 screened_out/undated_dropped 对上。
去重基准用生产 index.sqlite（不是 dryrun 库）——那才是明天真实一轮要面对的历史。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collector.config import load_config, select_sources          # noqa: E402
from collector.dedup import is_blocked, signal_id                 # noqa: E402
from collector.models import Candidate                            # noqa: E402
from collector.pubdate import fill_published_at                   # noqa: E402
from collector.store import Store                                 # noqa: E402
from collector.sources.fetchers import fetch                      # noqa: E402

CST = ZoneInfo("Asia/Shanghai")
SNAP = ROOT / "logs" / "analysis"

FIELDS = ["module", "source_name", "source_tier", "url", "title", "excerpt",
          "published_at", "heat", "retrieval_label", "retrieval_query"]


def do_fetch() -> Path:
    cfg = load_config()
    base = cfg.settings["discourse"]["base_url"]
    t0 = datetime.now(CST)
    out = []
    for src in select_sources(cfg.sources):
        try:
            got = fetch(src, base)
        except Exception as e:                                   # noqa: BLE001
            print(f"  抓取失败 {src['name']}: {e}")
            continue
        for c in got:
            d = {k: getattr(c, k, None) for k in FIELDS}
            d["via"] = "source"
            out.append(d)
        print(f"  {src['name']}: {len(got)}")
    qcfg = cfg.queries or {}
    if qcfg.get("enabled", True) and (qcfg.get("queries") or []):
        try:
            from collector.queries import run_queries
            qs: dict = {}
            found = run_queries(qcfg, now=t0, layers=qcfg.get("layers") or ["A", "B"],
                                limit_per_query=int(qcfg.get("limit_per_query", 20)),
                                window_hours=int(cfg.settings["round"].get("window_hours", 72)),
                                stats=qs)
            for c in found:
                d = {k: getattr(c, k, None) for k in FIELDS}
                d["via"] = "query"
                out.append(d)
            print(f"  检索词库: {len(found)} 条")
        except Exception as e:                                   # noqa: BLE001
            print(f"  检索词库失败: {e}")

    SNAP.mkdir(parents=True, exist_ok=True)
    p = SNAP / f"cands-{t0:%Y%m%d-%H%M%S}.json"
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"\n快照 {len(out)} 条 → {p}")
    return p


def latest() -> Path:
    files = sorted(SNAP.glob("cands-*.json"))
    if not files:
        raise SystemExit("还没有快照，先跑 --fetch")
    return files[-1]


def screen(cands: list[Candidate], cfg, window_hours: int):
    """复刻 pipeline 的规则层，逐条留下「为什么被丢」。"""
    t0 = datetime.now(CST)
    window = t0 - timedelta(hours=window_hours)
    policy = cfg.settings["round"].get("undated_policy", "drop")
    store = Store(ROOT / "state" / "index.sqlite")
    seen, rows = set(), []
    for c in cands:
        uid = signal_id(c.url)
        if uid in seen or store.known_url(uid):
            rows.append((c, "duplicate", None))
            continue
        seen.add(uid)
        fill_published_at(c, now=t0)
        if c.published_at:
            dt = datetime.fromisoformat(c.published_at)
            if dt < window:
                rows.append((c, "over_window", (t0 - dt).total_seconds() / 3600))
                continue
        elif policy == "drop":
            rows.append((c, "undated", None))
            continue
        if is_blocked(c.url, cfg.blocked_domains):
            rows.append((c, "blocked_domain", None))
            continue
        rows.append((c, "kept", None))
    return rows


def report_rules(rows):
    cnt = Counter(r for _, r, _ in rows)
    print(f"规则层（去重基准：生产 index.sqlite）\n候选合计 {len(rows)}")
    order = ["kept", "duplicate", "over_window", "undated", "blocked_domain"]
    label = {"kept": "进入模型", "duplicate": "URL 重复", "over_window": "超出 72h 窗口",
             "undated": "无发布日期（策略=drop）", "blocked_domain": "域名被拦截"}
    for k in order:
        if cnt.get(k):
            print(f"  {label[k]:<20} {cnt[k]:>4}  {cnt[k] / len(rows):>5.1%}")

    for reason in ("over_window", "undated", "blocked_domain"):
        sub = [(c, h) for c, r, h in rows if r == reason]
        if not sub:
            continue
        print(f"\n—— {label[reason]}：按信源 ——")
        by = Counter(c.source_name for c, _ in sub)
        for name, n in by.most_common(10):
            print(f"  {name:<28} {n:>3}")
        if reason == "over_window":
            ages = sorted(h for _, h in sub if h)
            print(f"  超窗时长：中位 {ages[len(ages) // 2]:.0f}h，最长 {ages[-1]:.0f}h（窗口 72h）")
        for c, _ in sub[:8]:
            print(f"    · [{c.source_name}] {c.title[:44]}")
    return cnt


def do_tag(rows, cfg, sample: int):
    from collector.tagger import LLMClient, tag_candidates
    kept = [c for c, r, _ in rows if r == "kept"]
    print(f"\n模型层：送 {len(kept)} 条（batch {cfg.settings['llm'].get('batch_size', 8)}，约 "
          f"{len(kept) // cfg.settings['llm'].get('batch_size', 8) + 1} 次调用）")
    res = tag_candidates(kept, cfg, LLMClient(cfg))
    buckets = defaultdict(list)
    for c, r in zip(kept, res):
        if r.error:
            buckets["校验失败"].append((c, r))
        elif r.keep:
            buckets["保留"].append((c, r))
        else:
            buckets["判为不值得"].append((c, r))
    for k in ("保留", "判为不值得", "校验失败"):
        print(f"  {k:<8} {len(buckets[k]):>4}")
    if buckets["判为不值得"]:
        print("\n—— 判为不值得：按信源 ——")
        for name, n in Counter(c.source_name for c, _ in buckets["判为不值得"]).most_common(12):
            total = sum(1 for c, r in zip(kept, res) if c.source_name == name)
            print(f"  {name:<28} {n:>3}/{total}")
        print(f"\n—— 判为不值得：样例（前 {sample} 条标题）——")
        for c, _ in buckets["判为不值得"][:sample]:
            print(f"  · [{c.source_name}] {c.title[:56]}")
    if buckets["校验失败"]:
        print("\n—— 校验失败：原因 ——")
        for reason, n in Counter((r.error or "")[:40] for _, r in buckets["校验失败"]).most_common(8):
            print(f"  {n:>3}  {reason}")
    print("\n保留项领域分布：", dict(Counter(r.domain for _, r in buckets["保留"])))
    print("保留项问题分布：", dict(Counter(q for _, r in buckets["保留"] for q in (r.questions or []))))
    return res, kept


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--tag", action="store_true")
    ap.add_argument("--sample", type=int, default=25)
    ap.add_argument("--file")
    a = ap.parse_args()
    cfg = load_config()
    if a.fetch:
        do_fetch()
        return 0
    p = Path(a.file) if a.file else latest()
    data = json.loads(p.read_text(encoding="utf-8"))
    cands = [Candidate(**{k: d.get(k) for k in FIELDS}) for d in data]
    print(f"快照 {p.name}：{len(cands)} 条\n")
    rows = screen(cands, cfg, int(cfg.settings["round"].get("window_hours", 72)))
    report_rules(rows)
    if a.tag:
        do_tag(rows, cfg, a.sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
