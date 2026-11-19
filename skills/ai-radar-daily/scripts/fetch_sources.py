#!/usr/bin/env python3
"""源注册表确定性抓取（fetch_sources.py，V4.3 新增；V5.0 增加社区信号源）。

读 config/sources.json，对 type=rss/atom/github_release 的源逐个抓取，筛出刊发窗口内的条目，
输出 <work_dir>/AI雷达日报_YYYY-MM-DD_源抓取.json：
  - items：窗口内条目（title / url / published_at / entity / tier / source_id），供执行 Agent 作为候选线索；
  - sources：每个源的抓取状态（OK / EMPTY / ERROR + 原因），可直接转写检索清单 official_sources_checked；
  - manual_pages：type=page 的源（无订阅接口），执行 Agent 须逐一打开核查。

V5.0 社区信号源（供第三层·信号雷达）：
  - hn_top：Hacker News 首页（Algolia API），热度 = points
  - github_trending：GitHub Trending 日榜（HTML），热度 = 今日新增 star
  - hf_trending：Hugging Face 热门模型（API），热度 = trendingScore
  - arXiv 走 atom 类型（export.arxiv.org API），热度为空
每条带 layer（core / signal）、cat（雷达类别）、heat；源配置可设 keywords 做 AI 相关性过滤。
trending 类没有发布日期，不做窗口过滤，记 discovered_at。

只读：不写论坛、不发消息、不读凭据。GitHub 未认证 API 有频率限制（60 次/小时），足够日常使用。
窗口：周二至周五 {D-1, D}；周一 {D-3..D}；可用 --extra-dates 追加缺跑补检日期。
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
SOURCES_PATH = SKILL_DIR / "config" / "sources.json"
UA = "ai-radar-daily/4.3 (+internal news digest)"


def allowed_dates(report_date: date, extra: Optional[List[str]] = None) -> List[str]:
    back = 3 if report_date.weekday() == 0 else 1
    days = {(report_date - timedelta(days=i)).isoformat() for i in range(back + 1)}
    days.update(extra or [])
    return sorted(days)


def _http_get(url: str, timeout: int, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (受控 URL 来自注册表)
        return resp.read()


def _to_local_date(value: str, tz: ZoneInfo) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    dt: Optional[datetime] = None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            m = re.match(r"(\d{4}-\d{2}-\d{2})", value)
            return m.group(1) if m else None
    if dt.tzinfo is None:
        return dt.date().isoformat()
    return dt.astimezone(tz).date().isoformat()


def parse_feed(body: bytes, tz: ZoneInfo) -> List[Dict[str, Any]]:
    root = ET.fromstring(body)
    items: List[Dict[str, Any]] = []
    # RSS 2.0
    for it in root.iter("item"):
        items.append({
            "title": (it.findtext("title") or "").strip(),
            "url": (it.findtext("link") or "").strip(),
            "published_at": it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date") or "",
        })
    # Atom
    ns = "{http://www.w3.org/2005/Atom}"
    for en in root.iter(f"{ns}entry"):
        link = ""
        for ln in en.findall(f"{ns}link"):
            if ln.get("rel") in (None, "alternate"):
                link = ln.get("href") or link
        items.append({
            "title": (en.findtext(f"{ns}title") or "").strip(),
            "url": link,
            "published_at": en.findtext(f"{ns}published") or en.findtext(f"{ns}updated") or "",
        })
    for it in items:
        it["published_date"] = _to_local_date(it["published_at"], tz)
    return items


def fetch_github_releases(repo: str, timeout: int, tz: ZoneInfo) -> List[Dict[str, Any]]:
    body = _http_get(f"https://api.github.com/repos/{repo}/releases?per_page=20", timeout,
                     accept="application/vnd.github+json")
    data = json.loads(body)
    out = []
    for rel in data if isinstance(data, list) else []:
        if rel.get("draft"):
            continue
        published = rel.get("published_at") or rel.get("created_at") or ""
        out.append({
            "title": f"{repo} {rel.get('name') or rel.get('tag_name')}",
            "url": rel.get("html_url") or "",
            "published_at": published,
            "published_date": _to_local_date(published, tz),
            "prerelease": bool(rel.get("prerelease")),
        })
    return out


AI_KEYWORDS_DEFAULT = [
    "ai", "llm", "gpt", "claude", "gemini", "model", "agent", "transformer", "diffusion", "rag",
    "inference", "openai", "anthropic", "deepseek", "qwen", "mcp", "copilot", "neural", "embedding",
    "大模型", "智能体", "人工智能",
]


def _match_keywords(text: str, keywords: Optional[List[str]]) -> bool:
    if not keywords:
        return True
    low = (text or "").lower()

    def hit(k: str) -> bool:
        k = k.lower()
        # 短词（ai / llm / rag）整词匹配，避免 "air"、"brag" 误中；长词允许复数等后缀
        tail = r"(?![a-z])" if len(k) <= 3 else ""
        return re.search(r"(?<![a-z])" + re.escape(k) + tail, low) is not None

    return any(hit(k) for k in keywords)


def parse_hn(data: Dict[str, Any], tz: ZoneInfo) -> List[Dict[str, Any]]:
    out = []
    for hit in data.get("hits") or []:
        title = (hit.get("title") or "").strip()
        if not title:
            continue
        oid = hit.get("objectID")
        out.append({
            "title": title,
            "url": hit.get("url") or f"https://news.ycombinator.com/item?id={oid}",
            "discussion_url": f"https://news.ycombinator.com/item?id={oid}",
            "published_at": hit.get("created_at") or "",
            "published_date": _to_local_date(hit.get("created_at") or "", tz),
            "heat": hit.get("points"),
            "heat_unit": "HN points",
        })
    return out


GH_REPO_RE = re.compile(r'<h2 class="h3 lh-condensed">\s*<a[^>]*?href="(?:https://github\.com)?/([^"/]+/[^"/?#]+)"', re.S)
GH_DESC_RE = re.compile(r'<p class="col-9[^"]*">\s*(.*?)\s*</p>', re.S)
GH_TODAY_RE = re.compile(r'([\d,]+)\s+stars (?:today|this week)')


def parse_github_trending(html: str) -> List[Dict[str, Any]]:
    out = []
    for block in html.split('<article class="Box-row">')[1:]:
        m = GH_REPO_RE.search(block)
        if not m:
            continue
        repo = m.group(1).strip()
        desc_m = GH_DESC_RE.search(block)
        desc = html_lib.unescape(re.sub(r"<[^>]+>", "", desc_m.group(1))).strip() if desc_m else ""
        today = GH_TODAY_RE.search(block)
        out.append({
            "title": f"{repo}" + (f"：{desc}" if desc else ""),
            "url": f"https://github.com/{repo}",
            "published_at": "", "published_date": None,
            "heat": int(today.group(1).replace(",", "")) if today else None,
            "heat_unit": "stars today",
        })
    return out


def parse_hf_models(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for m in data or []:
        mid = m.get("id") or m.get("modelId")
        if not mid:
            continue
        out.append({
            "title": f"{mid}" + (f"（{m.get('pipeline_tag')}）" if m.get("pipeline_tag") else ""),
            "url": f"https://huggingface.co/{mid}",
            "published_at": m.get("createdAt") or "", "published_date": None,
            "heat": m.get("trendingScore") if m.get("trendingScore") is not None else m.get("likes"),
            "heat_unit": "HF trending",
        })
    return out


def run(report_date: date, timezone: str, extra_dates: List[str], sources_path: Path = SOURCES_PATH,
        only: Optional[List[str]] = None, include_covered: bool = False) -> Dict[str, Any]:
    """抓取日报侧的源清单。

    include_covered=False（默认，2026-11-16 起）：跳过 collector_covered=true 的源——这些源已由
    采集器 sources.yaml 覆盖并写入论坛「信号」分类，兜底路径重复抓取只会制造重复候选。
    需要完整重抓（例如采集器停摆期间核实官方源）时传 --include-covered 或 --only 指定 id。
    """
    tz = ZoneInfo(timezone)
    reg = json.loads(sources_path.read_text(encoding="utf-8"))
    timeout = int(reg.get("defaults", {}).get("timeout_seconds", 15))
    cap = int(reg.get("defaults", {}).get("max_items_per_source", 20))
    window = allowed_dates(report_date, extra_dates)
    now = datetime.now(tz).isoformat(timespec="seconds")
    result: Dict[str, Any] = {"report_date": report_date.isoformat(), "generated_at": now,
                              "window_dates": window, "items": [], "sources": [], "manual_pages": [],
                              "skipped_covered": []}
    for src in reg.get("sources", []):
        if not src.get("enabled", True) or (only and src["id"] not in only):
            continue
        # --only 是显式指令，优先于覆盖跳过；否则默认跳过采集器已覆盖的源
        if not only and not include_covered and src.get("collector_covered"):
            result["skipped_covered"].append(src["id"])
            continue
        base = {"source_id": src["id"], "entity": src["entity"], "type": src["type"], "tier": src.get("tier")}
        if src["type"] == "page":
            result["manual_pages"].append({**base, "url": src["url"]})
            continue
        try:
            if src["type"] in ("rss", "atom"):
                entries = parse_feed(_http_get(src["url"], timeout), tz)
                where = src["url"]
            elif src["type"] == "github_release":
                entries = fetch_github_releases(src["repo"], timeout, tz)
                where = f"https://github.com/{src['repo']}/releases"
            elif src["type"] == "hn_top":
                entries = parse_hn(json.loads(_http_get(src["url"], timeout, accept="application/json")), tz)
                where = src["url"]
            elif src["type"] == "github_trending":
                entries = parse_github_trending(_http_get(src["url"], timeout).decode("utf-8", "replace"))
                where = src["url"]
            elif src["type"] == "hf_trending":
                entries = parse_hf_models(json.loads(_http_get(src["url"], timeout, accept="application/json")))
                where = src["url"]
            else:
                raise ValueError(f"未知源类型 {src['type']}")
        except Exception as exc:  # 网络/解析失败如实记录，不伪造已检查
            result["sources"].append({**base, "status": "ERROR", "checked": False,
                                      "url": src.get("url") or f"https://github.com/{src.get('repo')}/releases",
                                      "reason": f"{type(exc).__name__}: {str(exc)[:160]}", "checked_at": now})
            continue
        for e in entries:
            e["title"] = re.sub(r"\s+", " ", e.get("title") or "").strip()
        keywords = src.get("keywords")
        if keywords == "default":
            keywords = AI_KEYWORDS_DEFAULT
        entries = [e for e in entries if _match_keywords(e["title"], keywords)]
        undated = src["type"] in ("github_trending", "hf_trending")
        in_window = [e for e in entries[:cap * 3] if undated or e.get("published_date") in window][:cap]
        if src.get("min_heat") is not None:
            in_window = [e for e in in_window if (e.get("heat") or 0) >= src["min_heat"]]
        latest = entries[0]["title"] if entries else "NONE"
        result["sources"].append({**base, "status": "OK" if in_window else "EMPTY", "checked": True,
                                  "url": where, "checked_at": now, "entries_seen": len(entries),
                                  "in_window": len(in_window), "latest_update": latest})
        for e in in_window:
            result["items"].append({**base, "layer": src.get("layer", "core"), "cat": src.get("cat"),
                                    "discovered_at": now, **e})
    result["summary"] = {
        "sources_total": len(result["sources"]) + len(result["manual_pages"]),
        "fetched_ok": sum(1 for s in result["sources"] if s["status"] in ("OK", "EMPTY")),
        "errors": sum(1 for s in result["sources"] if s["status"] == "ERROR"),
        "items_in_window": len(result["items"]),
        "signal_items": sum(1 for i in result["items"] if i.get("layer") == "signal"),
        "manual_pages": len(result["manual_pages"]),
    }
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="源注册表确定性抓取（只读）")
    ap.add_argument("--report-date", required=True)
    ap.add_argument("--timezone", default="Asia/Shanghai")
    ap.add_argument("--extra-dates", default="", help="逗号分隔的缺跑补检日期")
    ap.add_argument("--only", default="", help="逗号分隔的 source_id，仅抓这些")
    ap.add_argument("--include-covered", action="store_true",
                    help="同时抓取采集器已覆盖的源（默认跳过，避免与信号库重复）")
    ap.add_argument("--output", help="输出路径；缺省写 work_dir")
    a = ap.parse_args(argv)
    rd = datetime.strptime(a.report_date, "%Y-%m-%d").date()
    extra = [x for x in a.extra_dates.split(",") if x]
    res = run(rd, a.timezone, extra, only=[x for x in a.only.split(",") if x] or None,
              include_covered=a.include_covered)
    out = a.output
    if not out:
        wd = os.environ.get("AI_RADAR_WORK_DIR")
        if not wd:
            wd = json.loads((SKILL_DIR / "config" / "runtime.json").read_text(encoding="utf-8")).get("work_dir")
        out = str(Path(wd) / f"AI雷达日报_{a.report_date}_源抓取.json")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    tmp = out + ".tmp"
    Path(tmp).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out)
    print(json.dumps({"ok": True, "output": out, **res["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
