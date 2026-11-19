"""三种通用抓取方式：rss、listpage（列表页 + CSS 选择器）、discourse（论坛分类最新）。

每个信源在 config/sources.yaml 里登记，模块名写在 module 字段。
加一类信源 = 在 sources.yaml 里加条目；只有出现新的抓取方式时才需要改这里。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..models import Candidate

CST = timezone(timedelta(hours=8))
UA = "ai-radar-collector/0.1 (Goldwind internal intelligence station)"


def http_get(url: str, timeout: int = 20) -> str:
    if url.startswith("file://"):
        return Path(url[7:]).read_text(encoding="utf-8")
    r = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r.text


def to_cst_iso(value: str | None) -> str | None:
    """各种日期写法 → 北京时间 ISO8601；解析不了返回 None。"""
    if not value:
        return None
    value = value.strip()
    dt = None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        pass
    if dt is None:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    if dt is None:
        m = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", value)
        if m:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=CST)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt.astimezone(CST).isoformat(timespec="seconds")


def _text(el, *names) -> str:
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def _strip_html(s: str, limit: int = 600) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(s or "", "html.parser").get_text(" ")).strip()[:limit]


def fetch_rss(src: dict) -> list[Candidate]:
    raw = http_get(src["url"])
    root = ET.fromstring(raw.encode("utf-8") if isinstance(raw, str) else raw)
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall(".//item")
    out = []
    if items:
        for it in items:
            out.append(Candidate(
                module=src["module"], source_name=src["name"], source_tier=src["tier"],
                url=_text(it, "link"), title=_text(it, "title"),
                excerpt=_strip_html(_text(it, "description")),
                published_at=to_cst_iso(_text(it, "pubDate", "{http://purl.org/dc/elements/1.1/}date")),
            ))
    else:
        for it in root.findall(f".//{atom}entry"):
            link = it.find(f"{atom}link[@rel='alternate']")
            if link is None:  # Element 没有子节点时为假值，不能用 or
                link = it.find(f"{atom}link")
            out.append(Candidate(
                module=src["module"], source_name=src["name"], source_tier=src["tier"],
                url=(link.get("href") if link is not None else ""), title=_text(it, f"{atom}title"),
                excerpt=_strip_html(_text(it, f"{atom}summary", f"{atom}content")),
                published_at=to_cst_iso(_text(it, f"{atom}published", f"{atom}updated")),
            ))
    return [c for c in out if c.url and c.title]


def fetch_listpage(src: dict) -> list[Candidate]:
    """src.selectors: item（每条的容器）、link（容器内的 <a>，默认 a）、date（可选）、excerpt（可选）。"""
    html = http_get(src["url"])
    soup = BeautifulSoup(html, "html.parser")
    sel = src.get("selectors", {})
    out = []
    for node in soup.select(sel.get("item", "li")):
        a = node.select_one(sel.get("link", "a"))
        if a is None or not a.get("href"):
            continue
        title = (a.get("title") or a.get_text(" ")).strip()
        date_el = node.select_one(sel["date"]) if sel.get("date") else None
        excerpt_el = node.select_one(sel["excerpt"]) if sel.get("excerpt") else None
        out.append(Candidate(
            module=src["module"], source_name=src["name"], source_tier=src["tier"],
            url=urljoin(src["url"], a["href"]), title=re.sub(r"\s+", " ", title),
            excerpt=_strip_html(excerpt_el.get_text(" ") if excerpt_el else ""),
            published_at=to_cst_iso(date_el.get_text(" ") if date_el else None),
        ))
    return [c for c in out if c.title]


def fetch_discourse(src: dict, base_url: str) -> list[Candidate]:
    """读集团 AI 社区某分类的最新话题（内部信号）。src.category_id 必填。"""
    import json
    raw = http_get(f"{base_url.rstrip('/')}/c/{src['category_id']}/l/latest.json")
    data = json.loads(raw)
    out = []
    for t in data.get("topic_list", {}).get("topics", []):
        if t.get("pinned") and t.get("title", "").startswith("关于"):
            continue
        out.append(Candidate(
            module=src["module"], source_name=src["name"], source_tier=src["tier"],
            url=f"{base_url.rstrip('/')}/t/{t.get('slug', 'topic')}/{t['id']}",
            title=t.get("title", ""), excerpt=t.get("excerpt", "") or "",
            published_at=to_cst_iso(t.get("created_at")),
        ))
    return out


def fetch_sitemap(src: dict) -> list[Candidate]:
    """站点地图抓取：适合新闻列表由 JS 渲染、静态 HTML 里没有条目的站点（Vestas、SGRE）。

    为什么用 sitemap 而不是列表页：这两家的 /media/company-news 页面
    curl 拿到的 HTML 只有导航（实测 0 条新闻），正文由 JS 渲染；
    而 sitemap.xml 里是权威的 URL 清单，还常带 lastmod。

    src.url：sitemap 地址（可以是 sitemap index，自动展开一层）
    src.url_pattern：只保留匹配的 URL（正则），用于把新闻页从整站里筛出来
    src.date_source：lastmod（默认）| url —— lastmod 缺失时从 URL 里抠日期戳
    """
    import re as _re
    from . import sitemap as _sm

    urls = _sm.load_sitemap(src["url"], timeout=int(src.get("timeout_seconds", 30)))
    pat = _re.compile(src["url_pattern"]) if src.get("url_pattern") else None
    limit = int(src.get("max_items", 30))
    out = []
    for loc, lastmod in urls:
        if pat and not pat.search(loc):
            continue
        title = _sm.title_from_url(loc)
        if not title:
            continue
        published = None
        if src.get("date_source", "lastmod") == "lastmod" and lastmod:
            published = to_cst_iso(lastmod)
        if not published:
            # lastmod 缺失（SGRE 整站没有 lastmod）时退回 URL 日期戳
            from ..pubdate import date_from_url
            published = date_from_url(loc)
        out.append(Candidate(
            module=src["module"], source_name=src["name"], source_tier=src["tier"],
            url=loc, title=title, excerpt="", published_at=published,
        ))
        if len(out) >= limit:
            break
    return out


def fetch_changelog(src: dict) -> list[Candidate]:
    """更新日志页抓取（DeepSeek 的 /zh-cn/updates）。

    这类页面的条目不是 RSS，但结构稳定。DeepSeek 实测结构（2026-11-16）：
      <h2>时间: 2026-11-13</h2>   ← 日期块
      <h3>发布全家桶应用 API</h3>  ← 该日期下的一个发布
      <p>正文…</p>
    所以按「h2 记住日期，后续 h3 各自成一条」解析，而不是按 selectors.item 找容器
    （整页只有一个 markdown 容器，按容器解析会把所有版本揉成一条）。
    """
    html = http_get(src["url"], timeout=int(src.get("timeout_seconds", 30)))
    soup = BeautifulSoup(html, "html.parser")
    sel = src.get("selectors", {})
    container = soup.select_one(sel["item"]) if sel.get("item") else (soup.select_one("article") or soup)
    date_re = re.compile(sel.get("date_pattern") or r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")
    title_tag = sel.get("title", "h2, h3, h4")
    date_tag = sel.get("date_heading", "h2")

    out, cur_date = [], None
    for el in container.find_all(re.compile(r"^h[1-6]$")):
        text = re.sub(r"\s+", " ", el.get_text(" ")).strip().strip("\u200b").strip()
        if not text:
            continue
        if el.name == date_tag:
            m = date_re.search(text)
            # 日期块本身不是条目，只更新「当前日期」
            cur_date = "-".join(f"{int(x):02d}" for x in m.groups()) if m else None
            continue
        if cur_date is None:
            continue
        # 标题块：取它到下一个标题之间的正文当摘要
        body = []
        for sib in el.find_next_siblings():
            if re.match(r"^h[1-6]$", sib.name or ""):
                break
            body.append(sib.get_text(" "))
        excerpt = _strip_html(" ".join(body), 400)
        href = el.find("a")
        out.append(Candidate(
            module=src["module"], source_name=src["name"], source_tier=src["tier"],
            url=urljoin(src["url"], href.get("href")) if href and href.get("href") else src["url"],
            title=f"{text}（{cur_date}）" if src.get("append_date", True) else text,
            excerpt=excerpt, published_at=to_cst_iso(cur_date),
        ))
    return out


SPECIAL_KINDS = ("github_release", "hn_top", "github_trending", "hf_trending",
                 "github_repo", "github_topic")
# page：没有订阅接口的官方页面，采集器不抓，由每日精选 skill 按清单人工核查
MANUAL_KINDS = ("page",)


def fetch(src: dict, discourse_base: str = "") -> list[Candidate]:
    kind = src["kind"]
    if kind in MANUAL_KINDS:
        return []
    if kind in SPECIAL_KINDS:
        from .special import fetch_special
        return apply_filters(fetch_special(src), src)
    if kind in ("rss", "atom"):
        items = fetch_rss(src)
    elif kind == "listpage":
        items = fetch_listpage(src)
    elif kind == "discourse":
        items = fetch_discourse(src, discourse_base)
    elif kind == "sitemap":
        items = fetch_sitemap(src)
    elif kind == "changelog":
        items = fetch_changelog(src)
    else:
        raise ValueError(f"未知抓取方式 {kind}")
    return apply_filters(items, src)


def apply_filters(items: list[Candidate], src: dict) -> list[Candidate]:
    if src.get("keywords") and src["kind"] not in SPECIAL_KINDS:
        from .special import match_keywords
        items = [c for c in items if match_keywords(c.title + " " + c.excerpt, src["keywords"])]
    inc = [k.lower() for k in src.get("include_keywords", [])]
    exc = [k.lower() for k in src.get("exclude_keywords", [])]
    # min_title_chars：滤掉站点导航词（「电力」「新能源」这种栏目名被当成标题抓进来的情况）
    min_chars = int(src.get("min_title_chars") or 0)
    out = []
    for c in items:
        if min_chars and len((c.title or "").strip()) < min_chars:
            continue
        text = (c.title + " " + c.excerpt).lower()
        if inc and not any(k in text for k in inc):
            continue
        if exc and any(k in text for k in exc):
            continue
        out.append(c)
    return out[: src.get("max_items", 30)]
