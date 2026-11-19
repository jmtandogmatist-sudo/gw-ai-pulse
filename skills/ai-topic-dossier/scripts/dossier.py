#!/usr/bin/env python3
"""主题档案：每周五按主题汇总信号，编辑写判断，更新论坛档案帖与首页「主题追踪」。

  collect   读论坛：每个主题本周（默认 7 天）与近 30 天的信号、上一期判断 → 素材 JSON（只读）
  template  按素材生成判断填写模板 judgments.json（只写本地）
  render    校验判断并生成每个档案帖正文和首页 topics 数据（只写本地）
  publish   新建或更新档案帖，写首页底座帖 topics 字段；写后回读（需 --confirm）
  whitelist 输出给采集器 config/tags.yaml 用的 topics 片段
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forum import Forum, ForumError, load_env, machine_block, parse_machine, same_body, signal_from_topic, write_feed_key  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parents[1]
CONFIG = SKILL_DIR / "config" / "topics.json"
CRED_DOT = {"green": "🟢", "yellow": "🟡", "red": "🔴", "g": "🟢", "y": "🟡", "r": "🔴"}
LIMITS = {"judge": 160, "change": 120, "next": 80}
NO_SIGNAL_CHANGE = "本周无新信号。"


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def save_config(cfg: dict) -> None:
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CONFIG)


def work_dir(cfg: dict) -> Path:
    p = Path(cfg["work_dir"]).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------- collect

def collect(forum: Forum, cfg: dict, week_end: date) -> dict:
    cat = cfg["forum"]["signal_category_id"]
    if not cat:
        raise ForumError("config/topics.json 的 forum.signal_category_id 还没填")
    since_week = (week_end - timedelta(days=cfg.get("lookback_days", 7) - 1)).isoformat()
    since_tl = (week_end - timedelta(days=cfg.get("timeline_days", 30) - 1)).isoformat()
    out = {"week_end": week_end.isoformat(), "since": since_week, "topics": []}
    for tp in cfg["topics"]:
        signals = []
        for t in forum.tag_topics(tp["tag"]):
            if t.get("category_id") != cat:
                continue
            created = str(t.get("created_at") or "")[:10]
            if created < since_tl or created > week_end.isoformat():
                continue
            signals.append(signal_from_topic(t, forum.raw(t["id"]), forum.cfg["url"]))
        signals.sort(key=lambda s: str(s["discovered_at"]), reverse=True)
        week = [s for s in signals if str(s["discovered_at"])[:10] >= since_week]
        prev = {}
        if tp.get("dossier_topic_id"):
            prev = parse_machine(forum.first_post(tp["dossier_topic_id"]).get("raw") or "")
        out["topics"].append({"name": tp["name"], "tag": tp["tag"], "questions": tp.get("questions", []),
                              "week_signals": week, "timeline": signals,
                              "previous": {k: prev.get(k) for k in ("judge", "change", "next", "updated")} if prev else None,
                              "history": prev.get("history", []) if prev else []})
    return out


# ---------------------------------------------------------------- template / render

def template(material: dict) -> dict:
    return {"week_end": material["week_end"], "topics": [
        {"name": t["name"],
         "judge": (t.get("previous") or {}).get("judge") or "",
         "change": "" if t["week_signals"] else NO_SIGNAL_CHANGE,
         "next": (t.get("previous") or {}).get("next") or "",
         "evidence": [s["topic_id"] for s in t["week_signals"][:3]],
         "_本周信号": [f"{s['topic_id']} {CRED_DOT.get(s['cred'], '')} {s['title']}" for s in t["week_signals"]],
         "_上期判断": (t.get("previous") or {}).get("judge")} for t in material["topics"]]}


def validate(material: dict, judgments: dict) -> List[str]:
    errs = []
    by_name = {t["name"]: t for t in material["topics"]}
    seen = set()
    for j in judgments.get("topics", []):
        name = j.get("name", "")
        if name not in by_name:
            errs.append(f"「{name}」不在本次素材里")
            continue
        seen.add(name)
        m = by_name[name]
        for k, lim in LIMITS.items():
            v = (j.get(k) or "").strip()
            if not v:
                errs.append(f"「{name}」的 {k} 为空")
            elif len(v) > lim:
                errs.append(f"「{name}」的 {k} 有 {len(v)} 字，超过 {lim}")
            if re.search(r"TODO|待补|XXX|\[示例\]|\{\{", v):
                errs.append(f"「{name}」的 {k} 含占位符")
        week_ids = {s["topic_id"] for s in m["week_signals"]}
        tl_ids = {s["topic_id"] for s in m["timeline"]}
        ev = j.get("evidence") or []
        if m["week_signals"]:
            if not ev:
                errs.append(f"「{name}」本周有 {len(week_ids)} 条信号，判断至少要引用 1 条（evidence）")
            if (j.get("change") or "").strip() == NO_SIGNAL_CHANGE:
                errs.append(f"「{name}」本周有信号，本周变化不能写“{NO_SIGNAL_CHANGE}”")
        bad = [x for x in ev if x not in tl_ids]
        if bad:
            errs.append(f"「{name}」引用的信号 {bad} 不在近 30 天信号里")
    for name in by_name:
        if name not in seen:
            errs.append(f"「{name}」没有填判断")
    return errs


def render_post(m: dict, j: dict, week_end: str) -> str:
    qs = "、".join(m["questions"]) or "—"
    ev = set(j.get("evidence") or [])
    lines = [
        f"> 主题档案每周五更新。判断由 AI 应用研究院编辑撰写，信号由采集器自动采集，标 🔴 的为单一来源线索。",
        "", "[wrap=ais-dossier]", "",
        "[wrap=ais-judge]", "", f"**当前判断**（{week_end}）", "", j["judge"].strip(), "", "[/wrap]", "",
        "[wrap=ais-change]", "", "**本周变化**", "", j["change"].strip(), "", "[/wrap]", "",
        "[wrap=ais-next]", "", "**下个观察点**", "", j["next"].strip(), "", "[/wrap]", "",
        "[wrap=ais-meta]", "", f"**关联问题**　{qs}　·　**本周新增信号**　{len(m['week_signals'])} 条", "", "[/wrap]", "",
        "[wrap=ais-timeline]", "", "**时间线**（近 30 天，新的在前；★ 为本期判断引用）", "",
    ]
    if m["timeline"]:
        for s in m["timeline"]:
            star = "★ " if s["topic_id"] in ev else ""
            review = "" if s["review"] == "reviewed" else "（未审）"
            lines.append(f"- {star}{str(s['date'])[5:10]} {CRED_DOT.get(s['cred'], '')} [{s['title']}]({s['url']}){review}")
    else:
        lines.append("- 近 30 天暂无信号")
    lines += ["", "[/wrap]"]
    history = [{"date": week_end, "judge": j["judge"].strip()}] + [h for h in (m.get("history") or []) if h.get("date") != week_end]
    if len(history) > 1:
        lines += ["", "[wrap=ais-history]", "", "**历史判断**", ""]
        lines += [f"- {h['date']}：{h['judge']}" for h in history[1:13]]
        lines += ["", "[/wrap]"]
    lines += ["", "[/wrap]"]
    machine = {"name": m["name"], "tag": m["tag"], "updated": week_end, "judge": j["judge"].strip(),
               "change": j["change"].strip(), "next": j["next"].strip(), "evidence": sorted(ev),
               "questions": m["questions"], "history": history[:52]}
    lines += ["", machine_block(machine)]
    return "\n".join(lines)


def feed_topics(material: dict, judgments: dict, cfg: dict, urls: Dict[str, str]) -> List[dict]:
    jmap = {j["name"]: j for j in judgments["topics"]}
    on_home = [t for t in cfg["topics"] if t.get("on_home", True)][: cfg.get("max_topics_on_home", 5)]
    mats = {m["name"]: m for m in material["topics"]}
    out = []
    for tp in on_home:
        m, j = mats.get(tp["name"]), jmap.get(tp["name"])
        if not m or not j:
            continue
        out.append({"name": tp["name"], "week": len(m["week_signals"]), "questions": m["questions"],
                    "judge": j["judge"].strip(), "change": j["change"].strip(), "next": j["next"].strip(),
                    "url": urls.get(tp["name"]) or f"/tag/{tp['tag']}"})
    return out


# ---------------------------------------------------------------- publish

def publish(forum: Forum, cfg: dict, material: dict, judgments: dict) -> dict:
    week_end = material["week_end"]
    jmap = {j["name"]: j for j in judgments["topics"]}
    urls, results = {}, []
    for tp in cfg["topics"]:
        m = next(x for x in material["topics"] if x["name"] == tp["name"])
        raw = render_post(m, jmap[tp["name"]], week_end)
        if tp.get("dossier_topic_id"):
            post = forum.first_post(tp["dossier_topic_id"])
            if not same_body(post.get("raw") or "", raw):
                forum.update_post(post["id"], raw, f"主题档案 {week_end} 周更")
            tid = tp["dossier_topic_id"]
        else:
            res = forum.create_topic(f"主题档案｜{tp['name']}", raw, cfg["forum"]["dossier_category_id"], cfg["forum"]["dossier_tags"])
            tid = res["topic_id"]
            tp["dossier_topic_id"] = tid
            save_config(cfg)   # 立刻回填，避免中途失败后重复建帖
        back = forum.first_post(tid).get("raw") or ""
        ok = same_body(back, raw)
        results.append({"name": tp["name"], "topic_id": tid, "status": "OK" if ok else "READBACK_MISMATCH"})
        urls[tp["name"]] = f"/t/{tid}"
    feed = feed_topics(material, judgments, cfg, urls)
    fr = write_feed_key(forum, cfg["forum"]["feed_topic_id"], "topics", feed, f"主题追踪 {week_end}")
    return {"week_end": week_end, "dossiers": results, "feed": fr}


def whitelist(cfg: dict) -> str:
    lines = ["# 粘贴到采集器 config/tags.yaml，替换 topics 一节（由主题档案 skill 生成）", "topics:"]
    lines += [f"  {t['name']}: {t['tag']}" for t in cfg["topics"]]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--week-end", default=date.today().isoformat())
    c.add_argument("--env-file")
    t = sub.add_parser("template")
    t.add_argument("--material", required=True)
    r = sub.add_parser("render")
    r.add_argument("--material", required=True)
    r.add_argument("--judgments", required=True)
    p = sub.add_parser("publish")
    p.add_argument("--material", required=True)
    p.add_argument("--judgments", required=True)
    p.add_argument("--env-file")
    p.add_argument("--confirm", action="store_true")
    sub.add_parser("whitelist")
    a = ap.parse_args(argv)
    cfg = load_config()

    if a.cmd == "whitelist":
        print(whitelist(cfg))
        return 0
    if a.cmd == "collect":
        forum = Forum(load_env(a.env_file))
        mat = collect(forum, cfg, datetime.strptime(a.week_end, "%Y-%m-%d").date())
        out = work_dir(cfg) / f"主题档案_{a.week_end}_素材.json"
        out.write_text(json.dumps(mat, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(out),
                          "week_signals": {t["name"]: len(t["week_signals"]) for t in mat["topics"]}}, ensure_ascii=False))
        return 0
    mat = json.loads(Path(a.material).read_text(encoding="utf-8"))
    if a.cmd == "template":
        out = work_dir(cfg) / f"主题档案_{mat['week_end']}_判断.json"
        if out.exists():
            print(json.dumps({"ok": False, "message": f"{out} 已存在，不覆盖"}, ensure_ascii=False))
            return 1
        out.write_text(json.dumps(template(mat), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(out)}, ensure_ascii=False))
        return 0
    jud = json.loads(Path(a.judgments).read_text(encoding="utf-8"))
    errs = validate(mat, jud)
    if errs:
        print(json.dumps({"ok": False, "code": "JUDGMENTS_INVALID", "errors": errs}, ensure_ascii=False, indent=2))
        return 2
    if a.cmd == "render":
        wd = work_dir(cfg)
        jmap = {j["name"]: j for j in jud["topics"]}
        files = []
        for m in mat["topics"]:
            f = wd / f"主题档案_{mat['week_end']}_{m['tag']}.md"
            f.write_text(f"# 主题档案｜{m['name']}\n\n" + render_post(m, jmap[m["name"]], mat["week_end"]), encoding="utf-8")
            files.append(str(f))
        ft = wd / f"主题档案_{mat['week_end']}_首页topics.json"
        ft.write_text(json.dumps(feed_topics(mat, jud, cfg, {}), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "previews": files, "feed_preview": str(ft)}, ensure_ascii=False, indent=2))
        return 0
    if not a.confirm:
        print(json.dumps({"ok": False, "message": "publish 会写论坛：确认后加 --confirm"}, ensure_ascii=False))
        return 1
    res = publish(Forum(load_env(a.env_file)), cfg, mat, jud)
    (work_dir(cfg) / f"主题档案_{mat['week_end']}_发布结果.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = all(d["status"] == "OK" for d in res["dossiers"])
    print(json.dumps({"ok": ok, **res}, ensure_ascii=False, indent=2))
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
