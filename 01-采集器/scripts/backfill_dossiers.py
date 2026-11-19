#!/usr/bin/env python3
"""一次性回填：为四个主题建「主题档案」帖，并把 632 的 topics.url 指过去。

⚠ 2026-11-19 起，常规周更已由 collector/pipeline.py::update_feed_topics() 接管：
同一份 judgments 同时写 4 篇档案与 632。不要再手工跑本脚本——它会把档案覆盖回
建帖那一刻的快照。仅当档案被误删、需要重建时，才用 --apply 重跑。

背景（2026-11-19）
------------------
首页 04「主题追踪」的 url 由 collector/topics.py::topic_url() 决定：
有 dossier_topic_id 就用档案帖，没有就退回「该主题最新一条信号帖」。
生产从未建过档案帖（标签 dossier 的 topic_count=0），所以点进去是 /t/854 这种单条信号，
与 UAT /t/241 的「主题档案」页（当前判断 / 本周变化 / 观察点 / 近 30 天时间线 / 历史判断）
完全不是一回事。

本脚本只做三件事
----------------
1. 用 632 现有 topics 里的 judge/change/next（2026-11-19 13:48 采集器生成的那份）
   + 本地 state/index.sqlite 的近 30 天时间线，在分类 15 建 4 篇「主题档案｜xxx」；
2. 把 id 记到 state/topic_dossiers.json（不写 config/，运行期代码只读）；
3. 只改 632 的 topics[].url，不动任何判断文本。

用法
----
    ../.venv/bin/python scripts/backfill_dossiers.py            # 预演，只打印
    ../.venv/bin/python scripts/backfill_dossiers.py --apply    # 真正写论坛
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, ".")

from collector.config import load_config          # noqa: E402
from collector.discourse import Discourse, DiscourseError  # noqa: E402
from collector.render import read_feed, write_feed          # noqa: E402
from collector.store import Store                          # noqa: E402
from collector.topics import collect_material              # noqa: E402

CST = ZoneInfo("Asia/Shanghai")
CRED_DOT = {"green": "🟢", "yellow": "🟡", "red": "🔴", "g": "🟢", "y": "🟡", "r": "🔴"}
MACHINE_RE = re.compile(r'\[details="机器可读数据"\]\s*```json\s*(.*?)```', re.S)
TIMELINE_LIMIT = 20
HISTORY_LIMIT = 12
INTRO = ("> 主题档案每周五更新。判断由采集器调用模型生成（非公司结论）；"
         "信号由采集器自动采集，标 🔴 的为单一来源线索。")


def norm(s: str) -> str:
    """与 skill 的 same_body 同口径：只归一空白，其余逐字比对。"""
    return re.sub(r"[\s\u3000]+", " ", s or "").strip()


def parse_machine(raw: str) -> dict:
    m = MACHINE_RE.search(raw or "")
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def machine_block(data: dict) -> str:
    return ('[details="机器可读数据"]\n```json\n'
            + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n[/details]")


def render_body(m, j: dict, week_end: str, base: str, history: list[dict]) -> str:
    """生成 ais-dossier 正文：与 skill 的 render_post 同一套骨架，判断来源改为模型。"""
    qs = "、".join(m.questions) or "—"
    ev = {s.topic_id for s in m.week_signals[:3] if s.topic_id}
    lines = [INTRO, "", "[wrap=ais-dossier]", ""]
    lines += ["[wrap=ais-judge]", "", f"**当前判断**（{week_end}）", "", j["judge"].strip(), "", "[/wrap]", ""]
    lines += ["[wrap=ais-change]", "", "**本周变化**", "", j["change"].strip(), "", "[/wrap]", ""]
    lines += ["[wrap=ais-next]", "", "**下个观察点**", "", j["next"].strip(), "", "[/wrap]", ""]
    lines += ["[wrap=ais-meta]", "",
              f"**关联问题**　{qs}　·　**本周新增信号**　{len(m.week_signals)} 条", "", "[/wrap]", ""]
    tip = "近 30 天，新的在前；★ 为本期判断引用"
    if len(m.timeline) > TIMELINE_LIMIT:
        tip += f"；仅列最新 {TIMELINE_LIMIT} 条"
    lines += ["[wrap=ais-timeline]", "", f"**时间线**（{tip}）", ""]
    if m.timeline:
        for s in m.timeline[:TIMELINE_LIMIT]:
            day = str(s.event_date or s.discovered_at or "")[:10]
            star = "★ " if s.topic_id in ev else ""
            review = "" if s.review == "reviewed" else "（未审）"
            lines.append(f"- {star}{day[5:10]} {CRED_DOT.get(s.cred, '')} "
                         f"[{s.title}]({base}/t/{s.topic_id}){review}")
    else:
        lines.append("- 近 30 天暂无信号")
    lines += ["", "[/wrap]"]
    if len(history) > 1:
        lines += ["", "[wrap=ais-history]", "", "**历史判断**", ""]
        lines += [f"- {h['date']}：{h['judge']}" for h in history[1:HISTORY_LIMIT]]
        lines += ["", "[/wrap]"]
    lines += ["", "[/wrap]", ""]
    machine = {"name": m.name, "tag": m.tag, "updated": week_end,
               "judge": j["judge"].strip(), "change": j["change"].strip(), "next": j["next"].strip(),
               "evidence": sorted(ev), "questions": list(m.questions),
               "history": history[:HISTORY_LIMIT * 2],
               "week_signals": len(m.week_signals),
               "generated_by": "collector/scripts/backfill_dossiers.py"}
    lines += [machine_block(machine)]
    return "\n".join(lines)


def load_state(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写论坛（默认只预演）")
    ap.add_argument("--week-end", default=datetime.now(CST).date().isoformat())
    args = ap.parse_args()

    cfg = load_config()
    base = cfg.settings["discourse"]["base_url"].rstrip("/")
    fcfg = cfg.settings["feed"]
    tcfg = cfg.topics or {}
    items = tcfg.get("topics") or []
    cat = int(tcfg.get("dossier_category_id", 15))
    tags = list(tcfg.get("dossier_tags") or ["dossier"])
    state_path = cfg.root / "state" / "topic_dossiers.json"
    state = load_state(state_path)

    forum = Discourse(cfg)
    post_id = forum.first_post_id(int(fcfg["topic_id"]))
    raw = forum.post_raw(post_id)
    feed, mode = read_feed(raw)
    feed_topics = {t["name"]: t for t in (feed.get("topics") or [])}
    if not feed_topics:
        print("[停止] 632 没有 topics 字段，先跑 collector topics")
        return 2

    store = Store(cfg.root / "state" / "index.sqlite")
    now = datetime.now(CST)
    sigs = store.recent_signals((now - timedelta(days=int(tcfg.get("timeline_days", 30)))).isoformat(timespec="seconds"),
                                limit=5000)
    materials = {m.name: m for m in collect_material(
        sigs, items, now=now,
        lookback_days=int(tcfg.get("lookback_days", 7)),
        timeline_days=int(tcfg.get("timeline_days", 30)))}

    results, ids = [], {}
    for tp in items:
        name = tp.get("name")
        m, j = materials.get(name), feed_topics.get(name)
        if not m or not j:
            results.append({"name": name, "status": "SKIP_NO_MATERIAL"})
            continue
        if m.week_signals and len(m.week_signals) != j.get("week"):
            print(f"[提示] {name}：本地本周信号 {len(m.week_signals)} 条，632 卡片写 {j.get('week')} 条，以本地为准")
        tid = int(state.get(tp["tag"]) or tp.get("dossier_topic_id") or 0)
        old_raw, history = "", []
        if tid:
            old_raw = forum.post_raw(forum.first_post_id(tid))
            history = parse_machine(old_raw).get("history") or []
        history = [{"date": args.week_end, "judge": j["judge"].strip()}] + \
                  [h for h in history if h.get("date") != args.week_end]
        body = render_body(m, j, args.week_end, base, history)
        title = f"主题档案｜{name}"

        if not args.apply:
            results.append({"name": name, "topic_id": tid, "title": title,
                            "chars": len(body), "week": len(m.week_signals),
                            "timeline": min(len(m.timeline), TIMELINE_LIMIT),
                            "preview": body[:160]})
            ids[name] = tid
            continue

        try:
            if tid and old_raw and norm(old_raw) != norm(body):
                forum.update_post(forum.first_post_id(tid), body, f"主题档案 {args.week_end} 回填")
                status = "UPDATED"
            elif tid:
                status = "NOOP"
            else:
                ext = f"dossier-{tp['tag']}"
                found = forum.topic_by_external_id(ext)
                if found:
                    tid = found["id"]
                    status = "ADOPTED"
                else:
                    try:
                        res = forum.create_topic(title, body, cat, tags, ext)
                    except DiscourseError as e:
                        if e.status != 422:
                            raise
                        print(f"[提示] {name}：带标签 {tags} 建帖被拒（{e.message}），改为不带标签重试")
                        res = forum.create_topic(title, body, cat, [], f"{ext}-nt")
                    tid = res["topic_id"]
                    status = "CREATED"
                state[tp["tag"]] = tid
                save_state(state_path, state)
            back = forum.post_raw(forum.first_post_id(tid))
            if norm(back) != norm(body):
                status = "READBACK_MISMATCH"
            ids[name] = tid
            results.append({"name": name, "topic_id": tid, "status": status})
        except Exception as e:  # noqa: BLE001
            results.append({"name": name, "topic_id": tid, "status": f"ERROR: {e}"})

    if args.apply and ids:
        for name, tid in ids.items():
            if not tid:
                continue
            feed_topics[name]["url"] = f"{base}/t/{tid}"
        forum.update_post(post_id, write_feed(raw, feed, mode), "主题档案：链接指向档案帖")
        back, _ = read_feed(forum.post_raw(post_id))
        ok = True
        for t in back.get("topics") or []:
            nm = t.get("name")
            if nm in ids and ids[nm] and not (t.get("url") or "").endswith(f"/t/{ids[nm]}"):
                ok = False
        print(json.dumps({"apply": True, "requests": forum.requests_made,
                          "dossiers": results, "feed_urls": {t["name"]: t.get("url") for t in back.get("topics") or []},
                          "feed_readback_ok": ok}, ensure_ascii=False, indent=2))
        return 0 if ok else 3

    print(json.dumps({"apply": False, "dossiers": results,
                      "state_file": str(state_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
