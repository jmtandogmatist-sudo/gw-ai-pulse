"""从日报 skill 5.2.0 的 fetch_sources.py 迁移过来的专用抓取：GitHub Release、HN 首页、GitHub Trending、HF 热门模型。

解析逻辑与日报 skill 保持一致（含其测试样例），只把输出换成采集器的 Candidate。
"""
from __future__ import annotations

import html as html_lib
import json
import re
from datetime import datetime

from ..models import Candidate
from .fetchers import CST, http_get, to_cst_iso

AI_KEYWORDS_DEFAULT = [
    "ai", "llm", "gpt", "claude", "gemini", "model", "agent", "transformer", "diffusion", "rag",
    "inference", "openai", "anthropic", "deepseek", "qwen", "mcp", "copilot", "neural", "embedding",
    "大模型", "智能体", "人工智能",
]


def match_keywords(text: str, keywords) -> bool:
    """短词（ai / llm / rag）整词匹配，避免 air、brag 误中；长词允许后缀。"""
    if keywords == "default":
        keywords = AI_KEYWORDS_DEFAULT
    if not keywords:
        return True
    low = (text or "").lower()
    for k in keywords:
        k = k.lower()
        tail = r"(?![a-z])" if len(k) <= 3 else ""
        if re.search(r"(?<![a-z])" + re.escape(k) + tail, low):
            return True
    return False


def _cand(src: dict, title: str, url: str, published: str | None = None, heat=None, excerpt: str = "") -> Candidate:
    return Candidate(module=src["module"], source_name=src["name"], source_tier=src["tier"], url=url,
                     title=title, excerpt=excerpt, published_at=to_cst_iso(published), heat=heat)


def fetch_github_release(src: dict) -> list[Candidate]:
    repo = src["repo"]
    data = json.loads(http_get(f"https://api.github.com/repos/{repo}/releases?per_page=20"))
    out = []
    for rel in data if isinstance(data, list) else []:
        if rel.get("draft"):
            continue
        if rel.get("prerelease") and not src.get("include_prerelease", False):
            continue
        body = re.sub(r"\s+", " ", rel.get("body") or "")[:500]
        out.append(_cand(src, f"{repo} 发布 {rel.get('name') or rel.get('tag_name')}", rel.get("html_url") or "",
                         rel.get("published_at") or rel.get("created_at"), excerpt=body))
    return [c for c in out if c.url]


def parse_hn(src: dict, data: dict) -> list[Candidate]:
    out = []
    for hit in data.get("hits") or []:
        title = (hit.get("title") or "").strip()
        if not title:
            continue
        oid = hit.get("objectID")
        out.append(_cand(src, title, hit.get("url") or f"https://news.ycombinator.com/item?id={oid}",
                         hit.get("created_at"), heat=hit.get("points"),
                         excerpt=f"Hacker News 讨论：https://news.ycombinator.com/item?id={oid}"))
    return out


GH_REPO_RE = re.compile(r'<h2 class="h3 lh-condensed">\s*<a[^>]*?href="(?:https://github\.com)?/([^"/]+/[^"/?#]+)"', re.S)
GH_DESC_RE = re.compile(r'<p class="col-9[^"]*">\s*(.*?)\s*</p>', re.S)
GH_TODAY_RE = re.compile(r'([\d,]+)\s+stars (?:today|this week)')


def parse_github_trending(src: dict, html: str) -> list[Candidate]:
    out = []
    for block in html.split('<article class="Box-row">')[1:]:
        m = GH_REPO_RE.search(block)
        if not m:
            continue
        repo = m.group(1).strip()
        desc_m = GH_DESC_RE.search(block)
        desc = html_lib.unescape(re.sub(r"<[^>]+>", "", desc_m.group(1))).strip() if desc_m else ""
        today = GH_TODAY_RE.search(block)
        # 2026-11-19：趋势榜条目本身没有发布日期，之前 11/12 条因此被 undated 丢弃。
        # 但「今天这个仓库上了趋势榜」确实是今天发生的事——日期取采集时刻是诚实的，
        # 也符合去重（同一仓库 URL 只发一次）。不要伪造仓库创建时间。
        out.append(_cand(src, repo + (f"：{desc}" if desc else ""), f"https://github.com/{repo}",
                         published=datetime.now(CST).isoformat(timespec="seconds"),
                         heat=int(today.group(1).replace(",", "")) if today else None, excerpt=desc))
    return out


def parse_hf_models(src: dict, data: list) -> list[Candidate]:
    out = []
    for m in data or []:
        mid = m.get("id") or m.get("modelId")
        if not mid:
            continue
        heat = m.get("trendingScore") if m.get("trendingScore") is not None else m.get("likes")
        # 2026-11-20：热门模型列表没有发布日期，原实现不设 published_at →
        # 20/20 全撞上 undated_policy=drop，整条通道 100% 丢弃、历史累计 0 条信号，
        # 报告里只留一个无归因的 undated_dropped 总数（审计 A1）。
        # 与上面 github_trending 的修法保持一致：日期取采集时刻，语义是「此刻在热榜上」，
        # 不伪造模型发布时间。
        out.append(_cand(src, mid + (f"（{m['pipeline_tag']}）" if m.get("pipeline_tag") else ""),
                         f"https://huggingface.co/{mid}", heat=heat,
                         published=datetime.now(CST).isoformat(timespec="seconds")))
    return out


def fetch_repo_activity(src: dict) -> list[Candidate]:
    """仓库活跃度：看 pushed_at 有没有落在采集窗口内（用户要的「数据有没有更新」）。

    GitHub 网页在本机 curl 超时（实测 20s 无响应），API 正常，所以只用 api.github.com。
    产物是一条「最近更新」候选——如果 pushed_at 早于窗口，pipeline 的 72 小时门禁会自己拦掉，
    不需要在这里判断。
    """
    repo = src["repo"]
    data = json.loads(http_get(f"https://api.github.com/repos/{repo}"))
    if not isinstance(data, dict) or data.get("message"):
        return []
    pushed = data.get("pushed_at")
    stars = data.get("stargazers_count")
    desc = re.sub(r"\s+", " ", data.get("description") or "")[:300]
    name = data.get("full_name") or repo
    title = f"{name} 更新（{stars} stars）" if stars is not None else f"{name} 更新"
    return [_cand(src, title, data.get("html_url") or f"https://github.com/{repo}",
                  published=pushed, heat=stars,
                  excerpt=f"{desc} · 最近提交 {pushed or '未知'}")]


def fetch_github_topic(src: dict) -> list[Candidate]:
    """按 topic 搜索最近更新的仓库：/search/repositories?q=topic:xxx&sort=updated。

    用于 dsh-plugin 这类生态信号——插件作者在做什么，比官方仓库本身更能反映生态热度。
    """
    topic = src["topic"]
    per_page = int(src.get("max_items", 10))
    url = (f"https://api.github.com/search/repositories?q=topic:{topic}"
           f"&sort=updated&order=desc&per_page={per_page}")
    data = json.loads(http_get(url))
    out = []
    for it in (data.get("items") or [])[:per_page]:
        desc = re.sub(r"\s+", " ", it.get("description") or "")[:240]
        title = f"{it.get('full_name', '?')}：{desc}" if desc else str(it.get("full_name", "?"))
        out.append(_cand(src, title[:200], it.get("html_url") or "", published=it.get("pushed_at"),
                         heat=it.get("stargazers_count"), excerpt=desc))
    return out


def fetch_special(src: dict) -> list[Candidate]:
    kind = src["kind"]
    if kind == "github_release":
        items = fetch_github_release(src)
    elif kind == "github_repo":
        items = fetch_repo_activity(src)
    elif kind == "github_topic":
        items = fetch_github_topic(src)
    elif kind == "hn_top":
        items = parse_hn(src, json.loads(http_get(src["url"])))
    elif kind == "github_trending":
        items = parse_github_trending(src, http_get(src["url"]))
    elif kind == "hf_trending":
        items = parse_hf_models(src, json.loads(http_get(src["url"])))
    else:
        raise ValueError(f"未知抓取方式 {kind}")
    kw = src.get("keywords")
    if kw:
        items = [c for c in items if match_keywords(c.title + " " + c.excerpt, kw)]
    min_heat = src.get("min_heat")
    if min_heat is not None:
        items = [c for c in items if c.heat is not None and c.heat >= min_heat]
    return items
