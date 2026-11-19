#!/usr/bin/env python3
"""5.3.0 阶段 6C：日报发布后，把进入本期精编 / 跟踪的信号话题标为「已审」。

对 _检索清单.json 里 decision 为 SELECTED / TRACKING 且带 signal_topic_id 的候选（来自 signal_pool.py）：
  1. 话题标签 auto → reviewed
  2. 第 1 楼机器可读块 review 改为 reviewed；候选带 us（人写的「对我们」）时替换「对金风」一段
  3. 正文末尾追加一行「已进入 YYYY-MM-DD 每日精选」并链接本期日报
写后回读核对。不带 --confirm 只打印计划，不写论坛。

用法：
  python3 mark_reviewed.py --report-date YYYY-MM-DD --checklist <检索清单.json> --env-file .env \
      --daily-url https://... --state-file <run-state 目录>/mark_reviewed.json [--confirm]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ConfigurationError, atomic_write_json, emit_status, load_discourse_config  # noqa: E402

MACHINE_RE = re.compile(r'(\[details="机器可读数据"\]\s*```json\s*)(.*?)(```)', re.S)
US_RE = re.compile(r"(\*\*对金风\*\*\s*\n\s*\n)(.*?)(\n\s*\n)", re.S)
SUMMARY_END_RE = re.compile(r"(\*\*摘要\*\*\s*\n\s*\n.*?\n\s*\n(?:\[/wrap\]\s*\n\s*\n?)?)", re.S)
WRAP_REVIEW_RE = re.compile(r"(\[wrap=ais-signal[^\]]*?\breview=)auto\b")
MARK_PREFIX = "> 已进入 "


def plan_from_checklist(checklist: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for c in checklist.get("candidates") or []:
        if c.get("decision") in ("SELECTED", "TRACKING") and c.get("signal_topic_id"):
            out.append({"topic_id": int(c["signal_topic_id"]), "decision": c["decision"],
                        "title": c.get("title", ""), "us": (c.get("us") or "").strip()})
    seen, uniq = set(), []
    for p in out:
        if p["topic_id"] not in seen:
            seen.add(p["topic_id"])
            uniq.append(p)
    return uniq


def rewrite_raw(raw: str, *, us: str, report_date: str, daily_url: str, decision: str) -> str:
    """改机器可读块的 review、替换或补上「对金风」、追加已进入日报的标记。幂等：同一期重复执行不重复追加。"""
    def fix_machine(m):
        try:
            data = json.loads(m.group(2))
        except json.JSONDecodeError:
            return m.group(0)
        data["review"] = "reviewed"
        data["reviewed_in"] = report_date
        return m.group(1) + json.dumps(data, ensure_ascii=False, indent=2) + "\n" + m.group(3)

    out = MACHINE_RE.sub(fix_machine, raw, count=1)
    out = WRAP_REVIEW_RE.sub(r"\1reviewed", out, count=1)   # 话题页样式里的审核标识
    if us:
        if US_RE.search(out):
            out = US_RE.sub(lambda m: m.group(1) + us + m.group(3), out, count=1)
        else:
            block = ("[wrap=ais-us]\n\n**对金风**\n\n" + us + "\n\n[/wrap]\n\n") if "[wrap=ais-" in out else ("**对金风**\n\n" + us + "\n\n")
            out = SUMMARY_END_RE.sub(lambda m: m.group(1) + block, out, count=1)
    where = "精编" if decision == "SELECTED" else "本周跟踪"
    line = f"{MARK_PREFIX}{report_date} 每日精选（{where}）" + (f"：[阅读]({daily_url})" if daily_url else "")
    if f"{MARK_PREFIX}{report_date} 每日精选" not in out:
        out = out.rstrip() + "\n\n" + line + "\n"
    return out


def _curl():
    from publish import auth_headers, curl_json
    return curl_json, auth_headers


def apply(plan: List[Dict[str, Any]], config: Dict[str, str], *, report_date: str, daily_url: str) -> List[Dict[str, Any]]:
    curl_json, auth_headers = _curl()
    results = []
    for p in plan:
        tid = p["topic_id"]
        r: Dict[str, Any] = {"topic_id": tid}
        topic = curl_json(f"{config['url']}/t/{tid}.json", headers=auth_headers(config))
        if not topic:
            results.append({**r, "status": "FAILED", "reason": "读不到话题"})
            continue
        posts = (topic.get("post_stream") or {}).get("posts") or []
        pid = posts[0]["id"] if posts else None
        post = curl_json(f"{config['url']}/posts/{pid}.json", headers=auth_headers(config)) if pid else None
        raw = (post or {}).get("raw")
        if not raw:
            results.append({**r, "status": "FAILED", "reason": "读不到第 1 楼原文"})
            continue
        new_raw = rewrite_raw(raw, us=p["us"], report_date=report_date, daily_url=daily_url, decision=p["decision"])
        tags = [t["name"] if isinstance(t, dict) else t for t in topic.get("tags") or []]
        new_tags = [t for t in tags if t != "auto"]
        if "reviewed" not in new_tags:
            new_tags.append("reviewed")
        ok = True
        for url, body in ((f"{config['url']}/posts/{pid}.json", {"post": {"raw": new_raw, "edit_reason": f"进入 {report_date} 每日精选"}}),
                          (f"{config['url']}/t/-/{tid}.json", {"tags": new_tags})):
            if url.endswith(f"/posts/{pid}.json") and new_raw == raw:
                continue
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
                json.dump(body, fh, ensure_ascii=False)
                fpath = fh.name
            try:
                ok = ok and bool(curl_json(url, method="PUT", headers=auth_headers(config, json_content=True), data_file=fpath))
            finally:
                Path(fpath).unlink(missing_ok=True)
        back = curl_json(f"{config['url']}/posts/{pid}.json", headers=auth_headers(config)) or {}
        back_topic = curl_json(f"{config['url']}/t/{tid}.json", headers=auth_headers(config)) or {}
        back_tags = {t["name"] if isinstance(t, dict) else t for t in back_topic.get("tags") or []}
        if not ok or (back.get("raw") or "").strip() != new_raw.strip() or "reviewed" not in back_tags:
            results.append({**r, "status": "FAILED", "reason": "写入或回读不一致"})
        else:
            results.append({**r, "status": "UPDATED"})
    return results


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="把进入本期日报的信号话题标为已审")
    ap.add_argument("--report-date", required=True)
    ap.add_argument("--checklist", required=True)
    ap.add_argument("--env-file", required=True)
    ap.add_argument("--daily-url", default="")
    ap.add_argument("--state-file", required=True)
    ap.add_argument("--confirm", action="store_true")
    a = ap.parse_args(argv)
    plan = plan_from_checklist(json.loads(Path(a.checklist).read_text(encoding="utf-8")))
    if not a.confirm:
        emit_status("ok", "MARK_REVIEWED_PLAN", f"将标记 {len(plan)} 条信号为已审（未写论坛，加 --confirm 执行）", plan=plan)
        return 0
    try:
        config = load_discourse_config(a.env_file)
    except ConfigurationError as exc:
        emit_status("error", "CONFIG_ERROR", str(exc))
        return 2
    results = apply(plan, config, report_date=a.report_date, daily_url=a.daily_url)
    state = {"report_date": a.report_date, "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
             "results": results}
    atomic_write_json(a.state_file, state)
    failed = [x for x in results if x["status"] != "UPDATED"]
    emit_status("ok" if not failed else "error", "MARK_REVIEWED_DONE" if not failed else "MARK_REVIEWED_PARTIAL",
                f"已标记 {len(results) - len(failed)} 条，失败 {len(failed)} 条", failed=failed or None)
    return 0 if not failed else 3


if __name__ == "__main__":
    sys.exit(main())
