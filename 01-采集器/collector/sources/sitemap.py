"""sitemap.xml 读取：只做两件事——列出 URL（带 lastmod）、把 URL 变成可读标题。

为什么单独一个模块（2026-11-16）：Vestas 与西门子歌美飒的新闻列表页由 JS 渲染，
静态 HTML 里 curl 不到任何条目；但两家都提供 sitemap.xml，里面有新闻页的完整清单。
用户要求「看采集时间范围内数据有没有更新」，lastmod 正是这个语义。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlsplit

from .fetchers import http_get

SM_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

# 语言前缀，标题里不该出现
_LANG_PREFIX = re.compile(r"^/(?:zh-cn|zh|en|global|cn|de|es|fr|it|ja|ko)(?=/|$)", re.I)
# URL 里的纯数字分段（文章 ID），当标题没意义
_PURE_NUM = re.compile(r"^\d+$")
# 站点把日期编进路径（/2026/05/12/xxx、/2026-05-12/xxx）
_DATE_SEG = re.compile(r"^(?:19|20)\d{2}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?$")


def load_sitemap(url: str, timeout: int = 30, _depth: int = 0) -> list[tuple[str, str | None]]:
    """返回 [(loc, lastmod)]。sitemap index 会展开一层子图。

    只展开一层：新闻站的 index 通常就是「新闻一个子图、博客一个子图」，
    再深展开会把整站几万个 URL 拉进来，对采集没有意义。
    """
    xml = http_get(url, timeout=timeout)
    try:
        root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    except ET.ParseError as e:
        raise ValueError(f"sitemap 解析失败：{e}") from e

    tag = root.tag.replace(SM_NS, "").lower()
    if tag.endswith("sitemapindex"):
        out: list[tuple[str, str | None]] = []
        if _depth >= 1:
            return out
        for sm in _iter(root, "sitemap"):
            loc = _child(sm, "loc")
            if loc:
                try:
                    out.extend(load_sitemap(loc, timeout=timeout, _depth=_depth + 1))
                except Exception:  # noqa: BLE001 —— 子图失败不影响其余
                    continue
        return out

    out = []
    for node in _iter(root, "url"):
        loc = _child(node, "loc")
        if loc:
            out.append((loc, _child(node, "lastmod")))
    return out


def _iter(root, name):
    for el in root.iter():
        if el.tag.replace(SM_NS, "") == name:
            yield el


def _child(node, name) -> str | None:
    for el in node:
        if el.tag.replace(SM_NS, "") == name:
            return (el.text or "").strip() or None
    return None


def title_from_url(url: str) -> str:
    """把新闻页 URL 变成可读标题：取最后一段 slug，去语言前缀与日期段，连字符转空格。

    https://www.vestas.com/en/media/company-news/2026/vestas-secures-order-in-brazil-xyz
      → vestas secures order in brazil xyz
    取不到有意义的 slug 时返回空串（调用方跳过该条）。
    """
    path = unquote(urlsplit(url).path).strip("/")
    if not path:
        return ""
    path = _LANG_PREFIX.sub("/", "/" + path).strip("/")
    segs = [s for s in path.split("/") if s]
    if not segs:
        return ""
    slug = segs[-1]
    if _DATE_SEG.match(slug) or _PURE_NUM.match(slug):
        return ""
    slug = re.sub(r"\.(?:html?|aspx?|php)$", "", slug, flags=re.I)
    # 去掉末尾的 ID 和扩展名残留
    slug = re.sub(r"[-_]\d{4,}$", "", slug)
    words = [w for w in re.split(r"[-_+]", slug) if w and not _PURE_NUM.match(w)]
    title = " ".join(words).strip()
    if len(title) < 12:
        return ""
    return title[0].upper() + title[1:]
