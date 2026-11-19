#!/usr/bin/env python3
"""集团技术雷达：每季度对模型、工具与平台、方法与流程、风电场景给出集团立场（采纳 / 试用 / 评估 / 暂缓）。

  collect   读论坛：本季度信号的分布（按领域、问题、主题）、首页主题追踪的当前判断、上期雷达条目（只读）
  template  生成条目填写模板 entries.json（沿用上期条目，只写本地）
  render    校验条目、计算移动、生成雷达帖正文与首页 stance 数据（只写本地）
  publish   新建本期雷达帖，写首页底座帖 stance 字段，追加历史；写后回读（需 --confirm）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forum import FEED_BLOCK, Forum, ForumError, load_env, machine_block, same_body, write_feed_key  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parents[1]
CONFIG = SKILL_DIR / "config" / "radar.json"
RINGS = ["采纳", "试用", "评估", "暂缓"]          # 从内到外；向「采纳」移动记 ↑
RING_KEYS = {"采纳": "adopt", "试用": "trial", "评估": "assess", "暂缓": "hold"}   # 话题页样式用
NEEDS_INTERNAL = {"采纳", "试用"}
EVIDENCE_TYPES = {"internal", "signal", "dossier", "external"}
CN_Q = {1: "第一", 2: "第二", 3: "第三", 4: "第四"}


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


def parse_quarter(q: str) -> tuple[date, date, str]:
    m = re.fullmatch(r"(20\d{2})Q([1-4])", q.strip().upper())
    if not m:
        raise ValueError("季度格式应为 2027Q1")
    y, n = int(m.group(1)), int(m.group(2))
    start = date(y, 3 * n - 2, 1)
    end = date(y + (n == 4), 1 if n == 4 else 3 * n + 1, 1)
    return start, end, f"{y} 年{CN_Q[n]}季度"


def previous_entries(cfg: dict) -> Dict[str, dict]:
    if not cfg.get("history"):
        return {}
    return {e["name"]: e for e in cfg["history"][-1]["entries"]}


# ---------------------------------------------------------------- collect

def collect(forum: Forum, cfg: dict, quarter: str, max_pages: int = 60) -> dict:
    cat = cfg["forum"]["signal_category_id"]
    if not cat:
        raise ForumError("config/radar.json 的 forum.signal_category_id 还没填")
    start, end, label = parse_quarter(quarter)
    topics = []
    for page in range(max_pages):
        data = forum._req("GET", f"/c/{int(cat)}/l/latest.json?page={page}")
        batch = data.get("topic_list", {}).get("topics", [])
        if not batch:
            break
        topics += batch
        if min(str(t.get("created_at") or "9")[:10] for t in batch) < start.isoformat():
            break
    inq = [t for t in topics if start.isoformat() <= str(t.get("created_at") or "")[:10] < end.isoformat()
           and not str(t.get("title", "")).startswith("关于")]
    tags = lambda t: [x["name"] if isinstance(x, dict) else x for x in t.get("tags") or []]  # noqa: E731
    by_q, by_topic, by_domain = Counter(), Counter(), Counter()
    reviewed = 0
    samples: Dict[str, List[dict]] = {}
    for t in inq:
        tg = tags(t)
        reviewed += "reviewed" in tg
        for x in tg:
            if re.fullmatch(r"q\d{2}", x):
                by_q[x.upper()] += 1
            elif x.startswith("topic-"):
                by_topic[x] += 1
                samples.setdefault(x, []).append({"topic_id": t["id"], "title": t.get("title", ""),
                                                  "date": str(t.get("created_at"))[:10], "reviewed": "reviewed" in tg})
            elif not (x.startswith("cred-") or x in ("auto", "reviewed")):
                by_domain[x] += 1
    feed_topics = []
    try:
        raw = forum.first_post(cfg["forum"]["feed_topic_id"]).get("raw") or ""
        m = FEED_BLOCK.search(raw)
        feed_topics = (json.loads(m.group(1)).get("topics") or []) if m else []
    except (ForumError, json.JSONDecodeError):
        pass
    return {"quarter": quarter.upper(), "label": label, "start": start.isoformat(), "end": end.isoformat(),
            "signals_total": len(inq), "signals_reviewed": reviewed,
            "by_question": dict(sorted(by_q.items())), "by_topic": dict(by_topic.most_common()),
            "by_domain": dict(by_domain.most_common()),
            "topic_samples": {k: sorted(v, key=lambda s: s["date"], reverse=True)[:8] for k, v in samples.items()},
            "dossier_judgments": [{"name": t.get("name"), "judge": t.get("judge"), "url": t.get("url")} for t in feed_topics],
            "previous": list(previous_entries(cfg).values())}


# ---------------------------------------------------------------- template / validate / render

def template(material: dict) -> dict:
    keep = [{"name": e["name"], "quadrant": e["quadrant"], "ring": e["ring"], "why": e.get("why", ""),
             "evidence": e.get("evidence", []), "owner": e.get("owner", ""), "url": e.get("url", ""),
             "_上期": f"{e['ring']}：{e.get('why', '')}"} for e in material.get("previous", [])]
    example = {"name": "", "quadrant": "工具与平台", "ring": "评估", "why": "",
               "evidence": [{"type": "signal", "ref": "", "note": ""}], "owner": "", "url": "",
               "_说明": "新条目：复制本项填写；ring 为 采纳/试用/评估/暂缓；采纳、试用必须有 type=internal 的证据"}
    return {"quarter": material["quarter"], "entries": keep or [example]}


def validate(entries: dict, cfg: dict) -> tuple[List[str], List[str]]:
    errs, warns = [], []
    names = Counter()
    quads = set(cfg["quadrants"])
    for i, e in enumerate(entries.get("entries", []), 1):
        name = (e.get("name") or "").strip()
        tag = f"第 {i} 条「{name or '未命名'}」"
        if not name:
            errs.append(f"{tag}没有名称")
        names[name] += 1
        if e.get("ring") not in RINGS:
            errs.append(f"{tag}的 ring 必须是 {' / '.join(RINGS)}")
        if e.get("quadrant") not in quads:
            errs.append(f"{tag}的 quadrant 必须是 {' / '.join(cfg['quadrants'])}")
        why = (e.get("why") or "").strip()
        if not why:
            errs.append(f"{tag}没有理由（why）")
        elif len(why) > 80:
            errs.append(f"{tag}的理由有 {len(why)} 字，超过 80")
        if re.search(r"TODO|待补|XXX|\[示例\]|\{\{", why + name):
            errs.append(f"{tag}含占位符")
        ev = [x for x in e.get("evidence") or [] if (x.get("ref") or x.get("note"))]
        bad_type = [x.get("type") for x in ev if x.get("type") not in EVIDENCE_TYPES]
        if bad_type:
            errs.append(f"{tag}的证据类型 {bad_type} 不认识（只能是 {' / '.join(sorted(EVIDENCE_TYPES))}）")
        if not ev:
            errs.append(f"{tag}没有证据")
        if e.get("ring") in NEEDS_INTERNAL and not any(x.get("type") == "internal" for x in ev):
            errs.append(f"{tag}放在「{e.get('ring')}」，必须有集团内使用证据（type=internal：哪个项目、谁在用）")
    for n, c in names.items():
        if n and c > 1:
            errs.append(f"「{n}」重复了 {c} 次")
    lo, hi = cfg.get("entries_target", [15, 25])
    total = len(entries.get("entries", []))
    if not lo <= total <= hi:
        warns.append(f"本期 {total} 条，建议 {lo}–{hi} 条")
    if not any(e.get("ring") == "暂缓" for e in entries.get("entries", [])):
        warns.append("本期没有「暂缓」条目：雷达最有价值的部分往往是不该用什么，确认是否真的没有")
    return errs, warns


def movement(e: dict, prev: Dict[str, dict]) -> str:
    p = prev.get(e["name"])
    if not p:
        return "新"
    a, b = RINGS.index(p["ring"]), RINGS.index(e["ring"])
    return "—" if a == b else ("↑" if b < a else "↓")


def render_post(material: dict, entries: dict, cfg: dict) -> tuple[str, dict]:
    prev = previous_entries(cfg)
    rows = [dict(e, moved=movement(e, prev)) for e in entries["entries"]]
    lines = [
        "[wrap=ais-radar]", "",
        f"> 集团技术雷达每季度一期，由集团数字化中心定稿。本期依据本季度信号库 {material['signals_total']} 条信号"
        f"（其中已审 {material['signals_reviewed']} 条）、主题档案判断和集团内项目使用情况。",
        "", "**怎么读**：" + "；".join(f"**{r}**＝{cfg['rings'][r]}" for r in RINGS) + "。移动：新＝本期新增，↑＝向采纳移动，↓＝向暂缓移动，—＝不变。", "",
    ]
    for ring in RINGS:
        items = [r for r in rows if r["ring"] == ring]
        if not items:
            continue
        lines += [f"[wrap=ais-ring ring={RING_KEYS[ring]}]", "", f"## {ring}（{len(items)}）", "", "| 条目 | 象限 | 移动 | 理由 | 证据 |", "| --- | --- | --- | --- | --- |"]
        for r in sorted(items, key=lambda x: (cfg["quadrants"].index(x["quadrant"]), x["name"])):
            ev = "；".join(f"{'集团内' if x['type'] == 'internal' else {'signal': '信号', 'dossier': '主题档案', 'external': '外部'}[x['type']]}"
                          f"：{x.get('note') or x.get('ref')}" for x in r["evidence"] if (x.get("ref") or x.get("note")))
            name = f"[{r['name']}]({r['url']})" if r.get("url") else r["name"]
            lines.append(f"| {name} | {r['quadrant']} | {r['moved']} | {r['why']} | {ev} |")
        lines += ["", "[/wrap]", ""]
    removed = [p for n, p in prev.items() if n not in {r["name"] for r in rows}]
    changed = [r for r in rows if r["moved"] in ("↑", "↓", "新")]
    if changed or removed:
        lines += ["[wrap=ais-changes]", "", "## 本期变化", ""]
        lines += [f"- {r['moved']} **{r['name']}**：{prev[r['name']]['ring'] + ' → ' if r['name'] in prev else ''}{r['ring']}。{r['why']}" for r in changed]
        lines += [f"- 移出 **{p['name']}**（上期{p['ring']}）" for p in removed]
        lines += ["", "[/wrap]", ""]
    stance = {"issue": material["label"], "url": "", "rings": [
        {"name": ring, "rule": cfg["rings"][ring][:24],
         "items": [{"name": r["name"], "why": r["why"], "url": r.get("url", ""), "moved": "" if r["moved"] == "—" else r["moved"]}
                   for r in rows if r["ring"] == ring][:8]}
        for ring in RINGS if any(r["ring"] == ring for r in rows)]}
    machine = {"quarter": material["quarter"], "entries": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]}
    lines += ["[/wrap]", "", machine_block(machine)]
    return "\n".join(lines), stance


# ---------------------------------------------------------------- publish

def publish(forum: Forum, cfg: dict, material: dict, entries: dict) -> dict:
    if any(h["quarter"] == material["quarter"] for h in cfg.get("history", [])):
        raise ForumError(f"{material['quarter']} 已发布过；修订请直接编辑该期雷达帖，并手工同步 radar.json 的 history")
    raw, stance = render_post(material, entries, cfg)
    title = f"集团技术雷达｜{material['label']}"
    res = forum.create_topic(title, raw, cfg["forum"]["radar_category_id"], cfg["forum"]["radar_tags"])
    tid = res["topic_id"]
    back = forum.first_post(tid).get("raw") or ""
    if not same_body(back, raw):
        raise ForumError(f"雷达帖 {tid} 写入后读回不一致，请人工核对；首页未更新")
    stance["url"] = f"/t/{tid}"
    fr = write_feed_key(forum, cfg["forum"]["feed_topic_id"], "stance", stance, f"集团技术雷达 {material['label']}")
    cfg.setdefault("history", []).append({"quarter": material["quarter"], "topic_id": tid,
                                          "published_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                          "entries": [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries["entries"]]})
    save_config(cfg)
    return {"quarter": material["quarter"], "topic_id": tid, "feed": fr}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--quarter", required=True)
    c.add_argument("--env-file")
    t = sub.add_parser("template")
    t.add_argument("--material", required=True)
    for name in ("render", "publish"):
        p = sub.add_parser(name)
        p.add_argument("--material", required=True)
        p.add_argument("--entries", required=True)
        if name == "publish":
            p.add_argument("--env-file")
            p.add_argument("--confirm", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config()
    if a.cmd == "collect":
        mat = collect(Forum(load_env(a.env_file)), cfg, a.quarter)
        out = work_dir(cfg) / f"技术雷达_{mat['quarter']}_素材.json"
        out.write_text(json.dumps(mat, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(out), "signals": mat["signals_total"], "previous": len(mat["previous"])}, ensure_ascii=False))
        return 0
    mat = json.loads(Path(a.material).read_text(encoding="utf-8"))
    if a.cmd == "template":
        out = work_dir(cfg) / f"技术雷达_{mat['quarter']}_条目.json"
        if out.exists():
            print(json.dumps({"ok": False, "message": f"{out} 已存在，不覆盖"}, ensure_ascii=False))
            return 1
        out.write_text(json.dumps(template(mat), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(out)}, ensure_ascii=False))
        return 0
    ent = json.loads(Path(a.entries).read_text(encoding="utf-8"))
    errs, warns = validate(ent, cfg)
    if errs:
        print(json.dumps({"ok": False, "code": "ENTRIES_INVALID", "errors": errs, "warnings": warns}, ensure_ascii=False, indent=2))
        return 2
    if a.cmd == "render":
        raw, stance = render_post(mat, ent, cfg)
        wd = work_dir(cfg)
        (wd / f"技术雷达_{mat['quarter']}_预览.md").write_text(f"# 集团技术雷达｜{mat['label']}\n\n{raw}", encoding="utf-8")
        (wd / f"技术雷达_{mat['quarter']}_首页stance.json").write_text(json.dumps(stance, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "warnings": warns, "preview": str(wd / f"技术雷达_{mat['quarter']}_预览.md")}, ensure_ascii=False, indent=2))
        return 0
    if not a.confirm:
        print(json.dumps({"ok": False, "message": "publish 会写论坛：吕总定稿、用户确认后加 --confirm"}, ensure_ascii=False))
        return 1
    res = publish(Forum(load_env(a.env_file)), cfg, mat, ent)
    print(json.dumps({"ok": True, **res}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
