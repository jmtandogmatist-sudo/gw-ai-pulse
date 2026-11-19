"""检索词库执行器：把 config/queries.yaml 的 A/B 层检索词真正跑起来。

2026-11-16 gw-ai-pulse：此前 A/B 层检索词只存在于 Markdown 散文里，没有任何代码读取，
采集器是纯订阅式（只抓 RSS/页面），检索词从未被自动执行。本模块补上这一环。

设计取舍：
  - 只接**无需 key、实测可达**的检索后端：HN Algolia、arXiv API。
  - 通用 Web 搜索需要外部 API key 或浏览器渲染，暂不接入（配置里标注为未启用）。
  - 检索结果与订阅源结果走同一条流水线（同样的去重、打标、相关度门禁），不做特殊通道。
"""
from __future__ import annotations

import json
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .models import Candidate
from .sources.fetchers import _strip_html, _text, http_get, to_cst_iso

ATOM = "{http://www.w3.org/2005/Atom}"


def load_queries(path: Path | str) -> dict[str, Any]:
    import yaml  # 延迟导入：只有跑检索时才需要
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def month_english(when: datetime) -> str:
    return when.strftime("%B")


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def iter_query_strings(entry: dict[str, Any], month_en: str) -> Iterable[str]:
    """产出实际发往检索后端的英文检索串。

    2026-11-16：实测 HN Algolia 对中文检索词返回 0 条（「大模型 发布 更新 开源」→ 0），
    因此只发 query_en。中文仅作 label_zh 用于回显，不参与检索。
    兼容旧配置里的 en 字段名。
    """
    texts = entry.get("query_en")
    if texts is None:
        texts = entry.get("en")
    for text in _as_list(texts):
        q = str(text).replace("{month_en}", month_en).strip()
        if q:
            yield q


def search_hn(engine: dict[str, Any], query: str, *, hits_per_page: int = 20,
              since_ts: int | None = None) -> list[Candidate]:
    """HN Algolia 关键词检索（无需 key）。

    since_ts：Unix 秒级下限，转成 numericFilters=created_at_i>T。
    HN 默认按**相关性**排序，不加时间过滤会把 2008 年的旧文排到前面
    （2026-11-16 实测全量 340 条，最早 2008-06-10），旧闻被模型判 keep=false 后白跑。
    """
    endpoint = engine.get("endpoint") or "https://hn.algolia.com/api/v1/search"
    url = (f"{endpoint}?query={urllib.parse.quote(query)}"
           f"&tags=story&hitsPerPage={hits_per_page}")
    if since_ts:
        url += f"&numericFilters=created_at_i%3E{int(since_ts)}"
    data = json.loads(http_get(url))
    out: list[Candidate] = []
    for hit in data.get("hits") or []:
        title = (hit.get("title") or hit.get("story_title") or "").strip()
        if not title:
            continue
        oid = hit.get("objectID")
        out.append(Candidate(
            module=engine["module"], source_name=engine["name"], source_tier=engine["tier"],
            url=hit.get("url") or f"https://news.ycombinator.com/item?id={oid}",
            title=title,
            excerpt=f"检索式：{query} · HN 讨论 https://news.ycombinator.com/item?id={oid}",
            published_at=to_cst_iso(hit.get("created_at")),
            heat=hit.get("points"),
        ))
    return out


def search_arxiv(engine: dict[str, Any], query: str, *, max_results: int = 20,
                 retries: int = 3) -> list[Candidate]:
    """arXiv API 关键词检索（all: 字段，无需 key）。

    arXiv 对高频请求返回 429，官方要求请求间隔 ≥3 秒；这里做退避重试，
    并在成功调用后 sleep 一个间隔，避免整轮检索把配额打满。
    """
    endpoint = engine.get("endpoint") or "http://export.arxiv.org/api/query"
    url = (f"{endpoint}?search_query={urllib.parse.quote('all:' + query)}"
           f"&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending")
    raw = ""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            raw = http_get(url, timeout=max(30, 20 + attempt * 10))
            last_exc = None
            break
        except Exception as exc:  # 429 / 超时 / 连接重置
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
    # 无论成败都退避，避免整轮检索把 arXiv 配额打满（官方要求间隔 ≥3 秒）
    time.sleep(float(engine.get("interval_seconds", 3)))
    if last_exc is not None:
        raise last_exc
    root = ET.fromstring(raw.encode("utf-8"))
    out: list[Candidate] = []
    for it in root.findall(f".//{ATOM}entry"):
        title = _text(it, f"{ATOM}title")
        if not title:
            continue
        link = it.find(f"{ATOM}link[@rel='alternate']")
        if link is None:
            link = it.find(f"{ATOM}link")
        out.append(Candidate(
            module=engine["module"], source_name=engine["name"], source_tier=engine["tier"],
            url=(link.get("href") if link is not None else ""),
            title=title,
            excerpt=f"检索式：{query} · " + _strip_html(_text(it, f"{ATOM}summary")),
            published_at=to_cst_iso(_text(it, f"{ATOM}published", f"{ATOM}updated")),
        ))
    return out


SEARCHERS = {"hn_search": search_hn, "arxiv_search": search_arxiv}


def run_queries(cfg_queries: dict[str, Any], *, now: datetime, layers: Iterable[str] = ("A", "B"),
                limit_per_query: int = 20, stats: dict[str, Any] | None = None,
                window_hours: int | None = None) -> list[Candidate]:
    """按层跑全部检索词，返回候选。单个检索式失败不中断整轮。"""
    month_en = month_english(now)
    since_ts = None
    if window_hours:
        since_ts = int((now - timedelta(hours=window_hours)).timestamp())
    engines = [e for e in (cfg_queries.get("search_engines") or []) if e.get("enabled", True)]
    out: list[Candidate] = []
    ok = failed = 0
    for entry in cfg_queries.get("queries") or []:
        if entry.get("layer") not in layers:
            continue
        label_zh = entry.get("label_zh") or entry.get("id") or ""
        for query in iter_query_strings(entry, month_en):
            for engine in engines:
                fn = SEARCHERS.get(engine.get("kind"))
                if fn is None:
                    continue
                src = {**engine,
                       "module": engine.get("module", "ext_ai"),
                       "name": f"{engine['id']} · {entry['id']}",
                       "tier": engine.get("tier", "community"),
                       "questions": entry.get("questions") or []}
                try:
                    if engine.get("kind") == "arxiv_search":
                        got = fn(src, query, max_results=limit_per_query)
                    elif engine.get("kind") == "hn_search" and since_ts:
                        got = fn(src, query, hits_per_page=limit_per_query, since_ts=since_ts)
                    else:
                        got = fn(src, query)
                    # 中文释义随候选下传：摘要由 tagger 出中文，retrieval_label 供回显定位
                    for c in got:
                        c.retrieval_label = label_zh
                        c.retrieval_query = query
                    out.extend(got)
                    ok += 1
                except Exception:
                    failed += 1
                    continue
    if stats is not None:
        stats.update({"query_calls_ok": ok, "query_calls_failed": failed,
                      "query_candidates": len(out), "layers": list(layers)})
    return out
