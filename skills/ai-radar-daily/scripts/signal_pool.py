#!/usr/bin/env python3
"""5.3.0 每日精选：从论坛「信号」分类读采集器已发布的信号，写成与 fetch_sources.py 相同格式的 _源抓取.json。

日报不再自己抓源，改读信号库（采集器 ai-radar-collector 每轮写入）；执行 Agent 后续流程不变：
逐条分流 SELECTED / TRACKING / SIGNAL / REJECTED → 写 _检索清单.json。

与 fetch_sources.py 输出的差异（都是新增字段，旧流程忽略即可）：
  items[].type = "signal_library"，并带 signal_topic_id / signal_topic_url_internal / signal_cred / questions /
  review / summary / us / all_sources / event_key，供成稿、去重与 mark_reviewed.py 回填使用。
  manual_pages 仍来自本 skill 的 config/sources.json（type=page 的官方页，采集器不抓，需执行 Agent 逐页核查）。

读不到信号库（分类未配置、网络或权限问题）时 exit 3，执行 Agent 改跑 fetch_sources.py 兜底（5.2.0 流程）。

用法：
  python3 signal_pool.py --report-date YYYY-MM-DD [--env-file .env] [--output 路径] [--extra-dates a,b]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_sources import allowed_dates  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parents[1]
RUNTIME_PATH = SKILL_DIR / "config" / "runtime.json"
SOURCES_PATH = SKILL_DIR / "config" / "sources.json"
UA = "ai-radar-daily/5.3.0 signal-pool"
MACHINE_RE = re.compile(r'\[details="机器可读数据"\]\s*```json\s*(.*?)```', re.S)
CRED_CODE = {"green": "g", "yellow": "y", "red": "r"}
TIER_MAP = {"authority": "T0", "official": "T0", "media": "T2", "research": "T3", "community": "T4"}


def _get(url: str, headers: Dict[str, str], as_json: bool = True, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 —— 只访问配置里的论坛地址
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body) if as_json else body


def parse_signal_raw(raw: str) -> Dict[str, Any]:
    """解析采集器写的信号话题正文：机器可读块 + 摘要 + 对金风。"""
    out: Dict[str, Any] = {}
    m = MACHINE_RE.search(raw or "")
    if m:
        try:
            out.update(json.loads(m.group(1)))
        except json.JSONDecodeError:
            pass

    def section(name: str) -> str:
        # 分块正文到下一个空行为止（采集器 v0.2 起每块用 [wrap=ais-*] 包裹，正文后紧跟空行 + [/wrap]）
        mm = re.search(rf"\*\*{name}\*\*\s*\n\s*\n(.*?)(?:\n\s*\n|\Z)", raw or "", re.S)
        return re.sub(r"\s+", " ", mm.group(1)).strip() if mm else ""

    out["summary"] = section("摘要")
    out["us"] = section("对金风")
    return out


def to_item(topic: Dict[str, Any], sig: Dict[str, Any], base_url: str, discovered_now: str) -> Optional[Dict[str, Any]]:
    sources = sig.get("sources") or []
    if not sources:
        return None
    first = sources[0]
    cred = CRED_CODE.get(sig.get("cred"), "r")
    return {
        "source_id": "signal-library",
        "entity": first.get("name") or "信号库",
        "type": "signal_library",
        "tier": TIER_MAP.get(first.get("tier"), "T4"),
        # 单一来源的信号只是线索，归信号层；其余进核心候选，由执行 Agent 逐条核验分流
        "layer": "signal" if cred == "r" else "core",
        "cat": sig.get("domain") or "其他",
        "title": topic.get("title") or "",
        "url": first.get("url") or "",
        "published_at": sig.get("event_date") or "",
        "published_date": sig.get("event_date"),
        "discovered_at": sig.get("discovered_at") or discovered_now,
        "heat": sig.get("relevance"),
        "heat_unit": "对金风相关度 0-3",
        "signal_topic_id": topic.get("id"),
        # 2026-11-16：分类 20 已设为仅 staff 可读（read_restricted=true）。
        # 信号帖链接对普通员工是 404，禁止出现在日报正文/首页等对外内容里；
        # 字段仅作内部审计用（execution Agent 回填、人工排查），下游不得渲染。
        "signal_topic_url_internal": f"{base_url.rstrip('/')}/t/{topic.get('slug') or 'topic'}/{topic.get('id')}",
        "signal_cred": cred,
        # 采集器给的 event_key 直接用正式字段名，执行 Agent 照抄即可被已见索引 L3 命中；
        # event_key_hint 保留为别名（5.3.0 旧名，勿在新流程里使用）。
        "event_key": sig.get("event_key"),
        "event_key_hint": sig.get("event_key"),
        "questions": sig.get("questions") or [],
        "review": sig.get("review") or ("reviewed" if "reviewed" in (topic.get("tags") or []) else "auto"),
        "summary": sig.get("summary", ""),
        "us": sig.get("us", ""),
        "all_sources": sources,
    }


def in_window(item: Dict[str, Any], window: List[str]) -> bool:
    disc = str(item.get("discovered_at") or "")[:10]
    return disc in window or (item.get("published_date") or "") in window


def run(report_date: date, extra_dates: List[str], headers: Dict[str, str], *, runtime: Dict[str, Any],
        getter=_get, max_pages: int = 10) -> Dict[str, Any]:
    lib = runtime.get("signal_library") or {}
    base = (runtime.get("forum") or {}).get("base_url", "").rstrip("/")
    cat = lib.get("category_id")
    window = allowed_dates(report_date, extra_dates)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    result: Dict[str, Any] = {"report_date": report_date.isoformat(), "generated_at": now, "window_dates": window,
                              "items": [], "sources": [], "manual_pages": [], "mode": "signal_library"}
    if not lib.get("enabled") or not cat or not base:
        raise RuntimeError("runtime.json 的 signal_library 未启用或未填 category_id")
    oldest = min(window)
    topics: List[Dict[str, Any]] = []
    for page in range(max_pages):
        data = getter(f"{base}/c/{int(cat)}/l/latest.json?page={page}", headers)
        batch = (data.get("topic_list") or {}).get("topics") or []
        if not batch:
            break
        topics += batch
        if min(str(t.get("created_at") or "9999")[:10] for t in batch) < oldest:
            break
    seen = set()
    for t in topics:
        if t.get("id") in seen or str(t.get("title", "")).startswith("关于"):
            continue
        seen.add(t.get("id"))
        if str(t.get("created_at") or "")[:10] < oldest:
            continue
        raw = getter(f"{base}/raw/{int(t['id'])}/1", headers, as_json=False)
        item = to_item(t, parse_signal_raw(raw), base, now)
        if item and in_window(item, window):
            result["items"].append(item)
        time.sleep(float(lib.get("read_interval_seconds", 0.2)))
    result["sources"].append({"source_id": "signal-library", "entity": "论坛信号库", "type": "signal_library",
                              "status": "OK" if result["items"] else "EMPTY", "checked": True,
                              "url": f"{base}/c/{int(cat)}", "checked_at": now,
                              "entries_seen": len(topics), "in_window": len(result["items"])})
    # 采集器不抓的官方页面，仍由执行 Agent 逐页核查
    try:
        reg = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
        for src in reg.get("sources", []):
            if src.get("type") == "page" and src.get("enabled", True):
                result["manual_pages"].append({"source_id": src["id"], "entity": src["entity"], "type": "page",
                                               "tier": src.get("tier"), "url": src["url"]})
    except (OSError, json.JSONDecodeError):
        pass
    result["summary"] = {
        "sources_total": 1 + len(result["manual_pages"]),
        "fetched_ok": 1,
        "errors": 0,
        "items_in_window": len(result["items"]),
        "signal_items": sum(1 for i in result["items"] if i["layer"] == "signal"),
        "core_items": sum(1 for i in result["items"] if i["layer"] == "core"),
        "reviewed_items": sum(1 for i in result["items"] if i["review"] == "reviewed"),
        "manual_pages": len(result["manual_pages"]),
    }
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="读论坛信号库，写 _源抓取.json（只读）")
    ap.add_argument("--report-date", required=True)
    ap.add_argument("--extra-dates", default="")
    ap.add_argument("--env-file", help="提供时带论坛凭据读取（信号分类对匿名可读时可省略）")
    ap.add_argument("--output")
    a = ap.parse_args(argv)
    runtime = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
    headers: Dict[str, str] = {}
    if a.env_file:
        from common import load_discourse_config
        cfg = load_discourse_config(a.env_file)
        headers = {"Api-Key": cfg["api_key"], "Api-Username": cfg["api_user"]}
    rd = datetime.strptime(a.report_date, "%Y-%m-%d").date()
    try:
        res = run(rd, [x for x in a.extra_dates.split(",") if x], headers, runtime=runtime)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "code": "SIGNAL_LIBRARY_UNAVAILABLE",
                          "message": f"读不到信号库：{type(exc).__name__}: {str(exc)[:200]}；改跑 fetch_sources.py 兜底"},
                         ensure_ascii=False))
        return 3
    out = a.output
    if not out:
        wd = os.environ.get("AI_RADAR_WORK_DIR") or runtime.get("work_dir")
        out = str(Path(wd) / f"AI雷达日报_{a.report_date}_源抓取.json")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    tmp = out + ".tmp"
    Path(tmp).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out)
    print(json.dumps({"ok": True, "output": out, **res["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
