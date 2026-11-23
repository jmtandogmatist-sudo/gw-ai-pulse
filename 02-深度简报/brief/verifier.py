"""Stage 2 代码化 source-verifier：T0–T4 可信度分级、并发 URL 活性检验、时间窗审计与成稿链接防编造校验。"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

import requests

from .config import BriefConfig, get_secret
from .harvester import Lead, _publisher_from_url

log = logging.getLogger("brief.verifier")

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 防畸形 Markdown 链接：双括号 [Text](url1)(url2)
_DOUBLE_PAREN_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)\((https?://[^)\s]+)\)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_INTRANET_PATTERNS = ("aicopilot.goldwind.com.cn", ".internal", "localhost", "127.0.0.1", "10.")


def classify_tier(url: str, cfg: BriefConfig) -> str:
    """按域名映射 source-verifier / wind-chief-expert 的 T0–T4 证据阶梯。"""
    host = urlparse(url or "").netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    tiers = (cfg.verifier.get("tier_domains") or {})
    for tier in ("T0", "T1", "T2", "T3"):
        for dom in tiers.get(tier) or []:
            d = str(dom).lower().strip()
            if host == d or host.endswith("." + d):
                return tier
    return "T4"


def is_intranet_url(url: str) -> bool:
    host = urlparse(url or "").netloc.lower()
    return any(p in host for p in _INTRANET_PATTERNS)


def is_cn_direct(url: str, cfg: BriefConfig) -> bool:
    """判断是否属于公司内网无需代理可直连的国内站点（.cn 域名、公司内网域名、或国内白名单媒体域）。"""
    if not url:
        return False
    if is_intranet_url(url):
        return True
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(".cn"):
        return True
    for dom in (cfg.verifier.get("cn_direct_domains") or []):
        d = str(dom).lower().strip()
        if host == d or host.endswith("." + d):
            return True
    return False


def check_url_alive(url: str, timeout: float = 8.0) -> tuple[bool, int | str]:
    """实测外链活性：200/3xx 视为可达；部分新闻站拦截爬虫返回 403/401/429 时不误杀为 404 死链。"""
    if not url or not url.startswith(("http://", "https://")):
        return False, "invalid-protocol"
    if is_intranet_url(url):
        return True, "intranet"
    try:
        r = requests.head(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code in (405, 501):
            r = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True, stream=True)
            r.close()
        # 404 / 410 / 5xx 判为不可达死链；403/401/429 多为付费墙或 Cloudflare 防爬，URL 本身有效
        if r.status_code < 400 or r.status_code in (401, 403, 429):
            return True, r.status_code
        return False, r.status_code
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__


def verify_leads(leads: list[Lead], cfg: BriefConfig, report_date: date,
                 *, check_fn=None) -> tuple[list[Lead], dict[str, Any]]:
    """对 Stage 1 素材执行 T0–T4 分级、时效窗检查与并发 URL 活性验证。"""
    vc = cfg.verifier
    max_age = int(vc.get("max_age_days", 3))
    do_live = bool(vc.get("check_url_live", True))
    timeout = float(vc.get("url_timeout_seconds", 8.0))
    workers = int(vc.get("max_workers", 10))
    checker = check_fn or (lambda u: check_url_alive(u, timeout=timeout))

    # 1. 分级与时效判定
    stale_dropped = 0
    undated_dropped = 0
    tracking_marked = 0
    drop_undated = bool(vc.get("drop_undated", True))
    prelim: list[Lead] = []
    for lead in leads:
        lead.tier = classify_tier(lead.url, cfg)
        lead.needs_overseas = not is_cn_direct(lead.url, cfg)
        if not lead.published_date:
            # 2026-11-23：堵住「无日期穿透」漏洞（实测 16 条 tavily.com 静态百科页无日期
            # 却直接跳过时效检查并排在新闻前面）。仅对 T0/T1 权威机构页保留豁免。
            if drop_undated and lead.tier not in ("T0", "T1"):
                undated_dropped += 1
                continue
        else:
            try:
                d_pub = date.fromisoformat(lead.published_date)
                age = (report_date - d_pub).days
                if age < -1:
                    # 未来日期（活动预告等）不属于已发生新闻
                    stale_dropped += 1
                    continue
                if age > max_age + 1:
                    # 2026-11-23：由 max_age+2（5天）收紧为 max_age+1（3天/72h），杜绝 4-5 天前旧闻
                    stale_dropped += 1
                    continue
                if age > max_age:
                    lead.tracking = True
                    tracking_marked += 1
            except ValueError:
                if drop_undated and lead.tier not in ("T0", "T1"):
                    undated_dropped += 1
                    continue
        prelim.append(lead)

    # 2. 并发 URL 活性检验
    dead_urls: list[dict[str, Any]] = []
    live_leads: list[Lead] = []
    if do_live and prelim:
        results: dict[str, tuple[bool, int | str]] = {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futs = {pool.submit(checker, lead.url): lead.id for lead in prelim}
            for fut in as_completed(futs):
                lid = futs[fut]
                try:
                    results[lid] = fut.result()
                except Exception as e:  # noqa: BLE001
                    results[lid] = (False, type(e).__name__)
        for lead in prelim:
            ok, code = results.get(lead.id, (True, "skip"))
            lead.verified_live = ok
            if ok:
                live_leads.append(lead)
            else:
                dead_urls.append({"id": lead.id, "url": lead.url, "reason": str(code)})
    else:
        live_leads = prelim

    # 按权威度（T0 > T1 > T2 > T3 > T4）与非追踪优先排序，重新编号 S01..SN
    tier_rank = {"T0": 0, "T1": 1, "T2": 2, "T3": 3, "T4": 4}
    live_leads.sort(key=lambda x: (1 if x.tracking else 0, tier_rank.get(x.tier, 4), x.category))
    for i, lead in enumerate(live_leads, 1):
        lead.id = f"S{i:02d}"

    report = {
        "input": len(leads),
        "stale_dropped": stale_dropped,
        "undated_dropped": undated_dropped,
        "tracking_marked": tracking_marked,
        "dead_dropped": len(dead_urls),
        "dead_samples": dead_urls[:8],
        "verified_count": len(live_leads),
        "by_tier": {t: sum(1 for x in live_leads if x.tier == t) for t in ("T0", "T1", "T2", "T3", "T4")},
    }
    return live_leads, report


def sanitize_markdown_links(text: str) -> str:
    """落实 source-verifier 链接语法审计：修复双括号畸形链接与括号内多余空格/引号。"""
    s = text or ""
    s = _DOUBLE_PAREN_RE.sub(r"[\1](\2) / [相关来源](\3)", s)
    s = re.sub(r"\]\(\s*[\"']?(https?://[^)\s\"']+)[\"']?\s*\)", r"](\1)", s)
    return s


def audit_brief_sources(brief: dict[str, Any], lead_by_id: dict[str, Lead]) -> dict[str, Any]:
    """成稿后置硬审计：校验模型引用的 source_ids 全部真实存在于素材池，绝不放行编造链接。"""
    valid_ids = set(lead_by_id.keys())
    unknown_ids: list[str] = []
    t4_only_items: list[str] = []
    used_ids: set[str] = set()

    def _clean_ids(ids: list[Any], label: str) -> list[str]:
        kept: list[str] = []
        for raw_id in ids or []:
            sid = str(raw_id).strip().upper()
            if sid in valid_ids:
                if sid not in kept:
                    kept.append(sid)
                    used_ids.add(sid)
            elif sid:
                unknown_ids.append(f"{label}:{sid}")
        if kept and all(lead_by_id[x].tier == "T4" for x in kept):
            t4_only_items.append(label)
        return kept

    for i, feat in enumerate(brief.get("features") or [], 1):
        feat["source_ids"] = _clean_ids(feat.get("source_ids"), f"feature#{i}")
        for j, pt in enumerate(feat.get("points") or [], 1):
            if isinstance(pt, dict):
                pt["source_ids"] = _clean_ids(pt.get("source_ids"), f"feature#{i}.pt#{j}")

    for sec in brief.get("sections") or []:
        sname = sec.get("title", "section")
        for j, it in enumerate(sec.get("items") or [], 1):
            if isinstance(it, dict):
                it["source_ids"] = _clean_ids(it.get("source_ids"), f"{sname}#{j}")

    return {
        "used_sources": len(used_ids),
        "unknown_source_ids_removed": unknown_ids,
        "t4_only_items": t4_only_items,
    }


def _has_direct_source(ids: list[str], lead_by_id: dict[str, Lead]) -> bool:
    for sid in ids or []:
        ld = lead_by_id.get(sid)
        if not ld:
            continue
        if not ld.needs_overseas or ld.community_url or ld.cn_mirror_url:
            return True
    return False


def _mirror_title_matches(query: str, cand_title: str) -> bool:
    """严格相关性校验：防止中文镜像检索误把无关旧闻/百科简介挂到海外新闻上。"""
    q = (query or "").strip()
    t = (cand_title or "").strip()
    if not q or not t:
        return False
    # 排除公司年报、百科简介等明显非新闻页
    if re.search(r"年度报告|半年报|简介|百科|公告_.*年", t):
        return False
    # 1. 若原题含英文专有名词（如 Anthropic / OpenAI / AMD / Nordex / GE Vernova），至少命中 1 个核心专名
    en_tokens = [
        w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9.+-]{1,}", q)
        if w.lower() not in {"ai", "mw", "gw", "ipo", "eu", "us", "uk", "ppa", "gpu", "cpu"}
    ]
    tl = t.lower()
    if en_tokens and not any(tok in tl for tok in en_tokens):
        return False
    # 2. 中文实词二元组重叠检查（确保事件主题一致，而非仅撞上一个泛词）
    zh_q = "".join(re.findall(r"[\u4e00-\u9fff]+", q))
    zh_t = "".join(re.findall(r"[\u4e00-\u9fff]+", t))
    bigrams = {zh_q[i:i + 2] for i in range(max(0, len(zh_q) - 1))}
    stop = {"中国", "全球", "美国", "欧洲", "发布", "宣布", "合作", "项目", "系统", "技术", "公司", "集团", "影响", "市场"}
    meaningful = bigrams - stop
    if not en_tokens:
        hits = sum(1 for bg in meaningful if bg in zh_t)
        return hits >= 2
    hits = sum(1 for bg in meaningful if bg in zh_t)
    return hits >= 1


def _search_cn_mirror(query: str, cfg: BriefConfig, *, api_key: str | None = None,
                      post_fn=None, check_fn=None) -> tuple[str, str] | None:
    """通过 Tavily 仅在国内直连白名单域内查找同事件中文报道，严格校对标题相关性并实测 HTTP 200。"""
    sc = cfg.search
    vc = cfg.verifier
    domains = [
        d for d in (vc.get("cn_direct_domains") or [])
        if "goldwind" not in str(d)
    ]
    if not domains:
        return None
    key = api_key or get_secret(sc.get("api_key_secret", "tavily-api-key"))
    endpoint = sc.get("endpoint", "https://api.tavily.com/search")
    do_post = post_fn or (lambda u, pl: requests.post(u, json=pl, timeout=15))
    checker = check_fn or (lambda u: check_url_alive(u, timeout=8.0))

    payload = {
        "api_key": key,
        "query": query,
        "topic": "news",
        "days": 4,
        "max_results": 5,
        "search_depth": "basic",
        "include_domains": domains,
    }
    try:
        r = do_post(endpoint, payload)
        if r.status_code != 200:
            return None
        results = (r.json() or {}).get("results") or []
    except Exception:  # noqa: BLE001
        return None

    max_mirror_age = int(vc.get("cn_mirror_max_age_days", 5))
    today = date.today()
    for item in results:
        url = (item.get("url") or "").strip().split("?", 1)[0]
        cand_title = str(item.get("title") or "").strip()
        if not url.startswith(("http://", "https://")) or not is_cn_direct(url, cfg):
            continue
        if "corp/view/" in url or url.lower().endswith(".pdf"):
            continue
        if cand_title and not _mirror_title_matches(query, cand_title):
            continue
        # 校验中文镜像日期，拒绝超过 max_mirror_age 天的旧文章冒充今日快讯
        m_date = re.search(r"(20\d{2}-\d{2}-\d{2})", str(item.get("published_date") or "") + " " + url)
        if m_date:
            try:
                if (today - date.fromisoformat(m_date.group(1))).days > max_mirror_age:
                    continue
            except ValueError:
                pass
        ok, code = checker(url)
        # 国内镜像要求真能直接打开（HTTP 200），不收 403/401
        if ok and (isinstance(code, int) and code < 400):
            return _publisher_from_url(url), url
    return None


def enrich_cn_mirrors(brief: dict[str, Any], lead_by_id: dict[str, Lead], cfg: BriefConfig,
                      *, api_key: str | None = None, post_fn=None, check_fn=None) -> dict[str, int]:
    """为纯海外信源的主打报道与简讯自动补充国内可直连中文报道链接。"""
    vc = cfg.verifier
    if not vc.get("cn_mirror_enabled", True):
        return {"queried": 0, "matched": 0}
    max_q = int(vc.get("cn_mirror_max_queries", 6))
    queried = matched = 0

    targets: list[tuple[str, dict[str, Any]]] = []
    for feat in brief.get("features") or []:
        if not _has_direct_source(feat.get("source_ids") or [], lead_by_id):
            targets.append((str(feat.get("title") or ""), feat))
    for sec in brief.get("sections") or []:
        for it in sec.get("items") or []:
            if isinstance(it, dict) and not _has_direct_source(it.get("source_ids") or [], lead_by_id):
                targets.append((str(it.get("heading") or ""), it))

    for qtext, obj in targets:
        if queried >= max_q or not qtext.strip():
            break
        queried += 1
        found = _search_cn_mirror(qtext.strip(), cfg, api_key=api_key, post_fn=post_fn, check_fn=check_fn)
        if found:
            name, url = found
            obj["cn_mirror"] = {"name": name, "url": url}
            matched += 1
            log.info("已补中文直连源：%s -> %s (%s)", qtext[:28], name, url[:65])
    return {"queried": queried, "matched": matched}
