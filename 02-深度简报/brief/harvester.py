"""Stage 1 捕手检索：Tavily 实时全网新闻搜索 + 复用快轨 01-采集器/state/index.sqlite 近 24h 信号底料。"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .config import BriefConfig, get_secret

log = logging.getLogger("brief.harvester")
CST = timezone(timedelta(hours=8))

# 需要过滤的纯社交/视频/聚合噪声域及静态知识库页，避免稀释权威信源与穿透时效检查
# 2026-11-23：加入 tavily.com（静态公司百科页无日期，实测占 16/42 条素材）
_NOISE_HOSTS = {
    "facebook.com", "m.facebook.com", "instagram.com", "tiktok.com",
    "youtube.com", "youtu.be", "pinterest.com", "reddit.com", "x.com", "twitter.com",
    "tavily.com",
}


def _is_redirect_noise(url: str, host: str) -> bool:
    """拦截无真实来源信息、且无法做域名分级与日期核验的跳转代理链接（如 google.com/url?...）。"""
    if host in ("google.com", "news.google.com") and urlparse(url).path.startswith("/url"):
        return True
    return False


@dataclass
class Lead:
    """一条经过初筛与去重的候选新闻素材。"""
    id: str                     # 供模型引用的短编号（如 S01），防止模型编造 URL
    category: str               # ai_frontier / ai_compute / ai_domestic / ai_security / ai_industrial / wind_industry / trade_policy
    query_label: str
    title: str
    url: str
    publisher: str
    published_date: str         # YYYY-MM-DD（空串表示未知）
    snippet: str
    origin: str                 # tavily / collector
    tier: str = "T3"            # 由 verifier 填充 T0-T4
    tracking: bool = False      # 超出 48h 时效窗的历史跟进标记
    verified_live: bool = True  # URL 活性检验结果
    needs_overseas: bool = False  # 非国内直连站点自动标注〔需外网〕
    community_url: str = ""       # 若该事件已在社区分类 20 发帖，附站内直达链接
    cn_mirror_name: str = ""      # 自动补全的国内中文可直连报道来源名
    cn_mirror_url: str = ""       # 自动补全的国内中文可直连报道 URL

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm_url(url: str) -> str:
    u = (url or "").strip().split("#", 1)[0]
    u = re.sub(r"[?&](?:utm_[^=&]+|fbclid|gclid|spm|ref)=[^&]*", "", u)
    return u.rstrip("/")


def _host_of(url: str) -> str:
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _parse_date(raw: str | None, *, ref_date: datetime | None = None) -> str:
    """解析 YYYY-MM-DD / YYYY/MM/DD / YYYYMMDD / RFC2822；并识别 URL 中早于当月的 /YYYY/MM/ 旧文路径。"""
    if not raw:
        return ""
    s = str(raw).strip()
    m = re.search(r"(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])(?!\d)", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m8 = re.search(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)", s)
    if m8:
        return f"{m8.group(1)}-{m8.group(2)}-{m8.group(3)}"
    try:
        dt = parsedate_to_datetime(s)
        return dt.astimezone(CST).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        pass
    # 2026-11-23：拦截仅带 /YYYY/MM/ 的跨月旧文 URL（如 energynow.com/2026/08/...）。
    # 若年份/月份早于当前参考月，按该月末 28 日保守估算，交给 verifier 的时效闸门淘汰；
    # 若恰为当月则留空，避免误把当月文章算成 1 号。
    mym = re.search(r"/(20\d{2})/(0[1-9]|1[0-2])(?:/|$|\?)", s)
    if mym:
        y, mo = int(mym.group(1)), int(mym.group(2))
        now_d = (ref_date or datetime.now(CST)).date()
        if (y, mo) < (now_d.year, now_d.month):
            return f"{y:04d}-{mo:02d}-28"
    return ""


def _publisher_from_url(url: str) -> str:
    host = _host_of(url)
    pretty = {
        "reuters.com": "Reuters",
        "bloomberg.com": "Bloomberg",
        "theguardian.com": "The Guardian",
        "npr.org": "NPR",
        "fortune.com": "Fortune",
        "techcrunch.com": "TechCrunch",
        "cnbc.com": "CNBC",
        "gov.uk": "GOV.UK",
        "nea.gov.cn": "国家能源局",
        "gov.ca.gov": "California.gov",
        "rechargenews.com": "Recharge News",
        "siliconangle.com": "SiliconANGLE",
        "nextgov.com": "Nextgov",
        "msci.com": "MSCI",
        "offshorewind.biz": "Offshore Wind",
        "windpowermonthly.com": "Windpower Monthly",
        "renews.biz": "reNEWS",
        "windtech-international.com": "Windtech International",
        "energyglobal.com": "Energy Global",
        "oceannews.com": "Ocean News",
        "eqs-news.com": "EQS News",
        "darkreading.com": "Dark Reading",
        "infosecurity-magazine.com": "Infosecurity Magazine",
        "thehackernews.com": "The Hacker News",
        "github.com": "GitHub",
        "huggingface.co": "Hugging Face",
        "ithome.com": "IT之家",
        "36kr.com": "36氪",
        "eu.36kr.com": "36Kr",
        "qbitai.com": "量子位",
        "jiqizhixin.com": "机器之心",
        "cls.cn": "财联社",
        "wallstreetcn.com": "华尔街见闻",
        "thepaper.cn": "澎湃新闻",
        "yicai.com": "第一财经",
        "stcn.com": "证券时报",
        "eastmoney.com": "东方财富",
        "sina.com.cn": "新浪财经",
        "finance.sina.com.cn": "新浪财经",
        "tech.sina.com.cn": "新浪科技",
        "163.com": "网易",
        "qq.com": "腾讯新闻",
        "news.qq.com": "腾讯新闻",
        "sohu.com": "搜狐",
        "bjx.com.cn": "北极星电力网",
        "news.bjx.com.cn": "北极星电力网",
        "in-en.com": "国际能源网",
        "ofweek.com": "维科网",
        "cpnn.com.cn": "中国电力报",
        "china5e.com": "中国能源网",
        "news.cn": "新华网",
        "openai.com": "OpenAI",
        "anthropic.com": "Anthropic",
        "blog.google": "Google Blog",
        "blogs.nvidia.com": "NVIDIA Blog",
    }
    if host in pretty:
        return pretty[host]
    for dom, name in pretty.items():
        if host.endswith("." + dom):
            return name
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("com", "net", "org", "gov", "edu"):
        return parts[-3].capitalize()
    return parts[-2].capitalize() if len(parts) >= 2 else (host or "Web")


def fetch_tavily(cfg: BriefConfig, *, api_key: str | None = None,
                 post_fn=None) -> tuple[list[Lead], dict[str, int]]:
    sc = cfg.search
    key = api_key or get_secret(sc.get("api_key_secret", "tavily-api-key"))
    endpoint = sc.get("endpoint", "https://api.tavily.com/search")
    depth = sc.get("search_depth", "basic")
    days = int(sc.get("days", 2))
    max_res = int(sc.get("max_results_per_query", 6))
    timeout = float(sc.get("timeout_seconds", 20))
    req_timeout = (min(10.0, timeout), timeout)
    do_post = post_fn or (lambda u, pl: requests.post(u, json=pl, timeout=req_timeout))

    queries = list(sc.get("queries") or [])

    def _run_one(q: dict) -> tuple[str, list[Lead]]:
        qid = q["id"]
        payload = {
            "api_key": key,
            "query": q["query"],
            "topic": q.get("topic", "news"),
            "days": int(q.get("days", days)),
            "max_results": int(q.get("max_results", max_res)),
            "search_depth": depth,
        }
        try:
            r = do_post(endpoint, payload)
            if r.status_code != 200:
                log.warning("Tavily 查询 %s 返回 HTTP %s：%s", qid, r.status_code, r.text[:160])
                return qid, []
            results = (r.json() or {}).get("results") or []
        except Exception as e:  # noqa: BLE001
            log.warning("Tavily 查询 %s 异常：%s", qid, e)
            return qid, []

        q_leads: list[Lead] = []
        for item in results:
            url = (item.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            host = _host_of(url)
            if any(host == nh or host.endswith("." + nh) for nh in _NOISE_HOSTS) or _is_redirect_noise(url, host):
                continue
            title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
            snippet = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()[:650]
            if not title or not snippet:
                continue
            # 2026-11-23：URL 中若明确携带旧年月（如 /2026/08/），优先采信 URL 日期，
            # 防止搜索引擎抓取时间覆盖文章真实发布月份
            url_date = _parse_date(url)
            api_date = _parse_date(item.get("published_date"))
            pub_date = url_date if (url_date and api_date and url_date[:7] < api_date[:7]) else (api_date or url_date)
            q_leads.append(Lead(
                id="",
                category=q.get("category", "ai_frontier"),
                query_label=q.get("label", qid),
                title=title,
                url=url,
                publisher=_publisher_from_url(url),
                published_date=pub_date,
                snippet=snippet,
                origin="tavily",
            ))
        return qid, q_leads

    leads: list[Lead] = []
    stats: dict[str, int] = {}
    if post_fn is not None or len(queries) <= 1:
        ordered = [_run_one(q) for q in queries]
    else:
        workers = min(4, len(queries))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            ordered = list(pool.map(_run_one, queries))
    for qid, q_leads in ordered:
        leads.extend(q_leads)
        stats[qid] = len(q_leads)
    return leads, stats


def fetch_collector_signals(cfg: BriefConfig, now: datetime) -> list[Lead]:
    """只读快轨 01-采集器/state/index.sqlite 近 N 小时已发布信号，作为补充一手底料。"""
    cc = cfg.collector_sqlite
    if not cc.get("enabled", True):
        return []
    db_path = (cfg.root / cc.get("path", "../01-采集器/state/index.sqlite")).resolve()
    if not db_path.is_file():
        log.info("未找到快轨 SQLite（跳过补充底料）：%s", db_path)
        return []
    since = (now - timedelta(hours=int(cc.get("lookback_hours", 30)))).isoformat(timespec="seconds")
    limit = int(cc.get("max_items", 20))
    domain_to_cat = {
        "选型": "ai_domestic",
        "算力": "ai_compute",
        "安全": "ai_security",
        "能源AI": "wind_industry",
        "政策合规": "trade_policy",
        "工具做法": "ai_industrial",
        "前沿研究": "ai_frontier",
        "行业动态": "ai_frontier",
    }
    leads: list[Lead] = []
    since_day = (now - timedelta(hours=int(cc.get("lookback_hours", 30)))).date().isoformat()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
        if "published_at" in cols and "event_date" in cols:
            rows = conn.execute(
                "SELECT data FROM signals "
                "WHERE (published_at != '' AND published_at >= ?) "
                "   OR (published_at = '' AND event_date != '' AND event_date >= ?) "
                "   OR (published_at = '' AND event_date = '' AND discovered_at >= ?) "
                "ORDER BY COALESCE(NULLIF(published_at, ''), NULLIF(event_date, ''), discovered_at) DESC "
                "LIMIT ?",
                (since_day, since_day, since, limit * 2),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT data FROM signals WHERE discovered_at >= ? ORDER BY discovered_at DESC LIMIT ?",
                (since, limit * 2),
            ).fetchall()
    finally:
        conn.close()

    for (raw_json,) in rows:
        try:
            d = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        if not d.get("topic_id"):
            continue
        # 2026-11-23：同时校验真实发布日期，防止「3 天前发布、今早才采集」的旧信号
        # 借 discovered_at 混入深度简报的近 30h 新鲜底料
        real_pub = _parse_date(d.get("event_date") or d.get("published_at"), ref_date=now)
        if real_pub and real_pub < since_day:
            continue
        srcs = d.get("sources") or []
        if not srcs:
            continue
        src = srcs[0]
        url = (src.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        # 优先使用刚生成的结构化摘要拼接作为高密度素材
        ss = d.get("structured_summary") or {}
        if ss.get("core"):
            pts = "；".join(ss.get("points") or [])
            snippet = f"{ss['core']} {pts} {ss.get('impact', '')}".strip()[:650]
        else:
            snippet = str(d.get("summary") or "").strip()[:650]
        base_url = (cfg.discourse.get("base_url") or "").rstrip("/")
        comm_url = f"{base_url}/t/{d['topic_id']}" if (base_url and d.get("topic_id")) else ""
        # 如果该信号有多条来源且含国内源，顺便把国内源记为中文镜像
        cn_name, cn_url = "", ""
        for extra in srcs[1:]:
            eu = (extra.get("url") or "").strip()
            eh = _host_of(eu)
            if eu and (eh.endswith(".cn") or any(eh == x or eh.endswith("." + x) for x in ("ithome.com", "36kr.com", "qbitai.com"))):
                cn_name = str(extra.get("name") or _publisher_from_url(eu))
                cn_url = eu
                break
        leads.append(Lead(
            id="",
            category=domain_to_cat.get(d.get("domain", ""), "ai_frontier"),
            query_label=f"快轨社区信号({d.get('domain', '')})",
            title=str(d.get("title") or "").strip(),
            url=url,
            publisher=str(src.get("name") or _publisher_from_url(url)),
            published_date=_parse_date(d.get("event_date") or d.get("published_at") or d.get("discovered_at")),
            snippet=snippet,
            origin="collector",
            community_url=comm_url,
            cn_mirror_name=cn_name,
            cn_mirror_url=cn_url,
        ))
        if len(leads) >= limit:
            break
    return leads


def load_recent_history(root: Path, report_date, days: int = 2) -> tuple[set[str], list[str]]:
    """读取近 N 期已生成的 brief.json，用于跨期去重（URL 硬排除 + 已报选题提示）。"""
    used_urls: set[str] = set()
    covered_topics: list[str] = []
    out_root = root / "output"
    if not out_root.is_dir() or days <= 0:
        return used_urls, covered_topics

    for delta in range(1, days + 1):
        d = (report_date - timedelta(days=delta)).isoformat()
        p = out_root / d / "brief.json"
        if not p.is_file():
            continue
        try:
            bundle = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        brief = bundle.get("brief") or {}
        lead_map = {x.get("id"): x.get("url", "") for x in (bundle.get("leads") or []) if isinstance(x, dict)}
        for feat in brief.get("features") or []:
            t = str(feat.get("title") or "").strip()
            if t:
                covered_topics.append(f"[{d} 主打] {t}")
            for sid in feat.get("source_ids") or []:
                if lead_map.get(sid):
                    used_urls.add(_norm_url(lead_map[sid]))
            for pt in feat.get("points") or []:
                if isinstance(pt, dict):
                    for sid in pt.get("source_ids") or []:
                        if lead_map.get(sid):
                            used_urls.add(_norm_url(lead_map[sid]))
        for sec in brief.get("sections") or []:
            for it in sec.get("items") or []:
                if not isinstance(it, dict):
                    continue
                h = str(it.get("heading") or "").strip()
                if h and "核查" not in h:
                    covered_topics.append(f"[{d} 简讯] {h}")
                for sid in it.get("source_ids") or []:
                    if lead_map.get(sid):
                        used_urls.add(_norm_url(lead_map[sid]))
    return used_urls, covered_topics


def harvest(cfg: BriefConfig, now: datetime, *, api_key: str | None = None,
            post_fn=None, exclude_urls: set[str] | None = None) -> tuple[list[Lead], dict[str, Any]]:
    """执行 Stage 1 全量捕获、URL 去重、跨期去重、编号（S01..SN）并输出 5 大类覆盖统计。"""
    tv_leads, q_stats = fetch_tavily(cfg, api_key=api_key, post_fn=post_fn)
    col_leads = fetch_collector_signals(cfg, now)

    merged: list[Lead] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    hist_urls = exclude_urls or set()
    history_skipped = 0
    # 快轨信号已含高质量中文摘要与一手链接，与 Tavily 合并去重
    for lead in tv_leads + col_leads:
        nu = _norm_url(lead.url)
        nt = re.sub(r"\W+", "", lead.title.lower())[:36]
        if not nu or nu in seen_urls or (nt and nt in seen_titles):
            continue
        if nu in hist_urls:
            history_skipped += 1
            continue
        seen_urls.add(nu)
        if nt:
            seen_titles.add(nt)
        merged.append(lead)

    for idx, lead in enumerate(merged, 1):
        lead.id = f"S{idx:02d}"

    by_cat: dict[str, int] = {}
    for lead in merged:
        by_cat[lead.category] = by_cat.get(lead.category, 0) + 1

    meta = {
        "tavily_raw": len(tv_leads),
        "collector_raw": len(col_leads),
        "history_skipped": history_skipped,
        "deduped": len(merged),
        "by_query": q_stats,
        "by_category": by_cat,
    }
    return merged, meta
