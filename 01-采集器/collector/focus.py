"""今日焦点：把一条值得展开的信号做成一篇可读的长文（方案 A，2026-11-19）。

## 为什么要有这个模块

02「今日焦点」原先只是 01 简报的第一条，渲染出来必然是一句话：素材只有 RSS 摘要
（实测 105 字），前端也只画一段。问题不在文案，而在三处结构缺失——
没有独立选材、没有正文素材、没有站内落点。

## 做法（每轮最多一篇）

1. 选题 focus.pick：只从**本轮新信号**里挑；一手来源优先、相关度高、24 小时内；
   同一个 event_key 7 天内只做一次焦点（「没有新焦点就沿用上一轮」，见 settings.focus）。
2. 取正文 fetch_text：抓原文页，用 bs4 启发式抽 p/li/h2 文本（≤8000 字）。
   不新增依赖（venv 里没有 lxml/trafilatura，内网 pip 不可靠）。
3. 抽事实 extract_facts：模型只做一件事——从正文里摘「事实 + 原文引文」。
4. 校验 verify_claims：**纯代码逐字比对**引文，编的一律删掉。
   这是「不能是一句话」和「不能编」的唯一调和点（参考站 ai-pulse-lab 也是给每条
   事实挂 Claim + Quotation，只是它写进 JSON-LD，我们写进折叠块）。
5. 成稿 write_article：模型按固定骨架填槽，只允许使用通过校验的事实。
6. 落库 mark_focus：写到 Signal.focus_* 字段；发帖走长文模板，首页 02 读它。

任何一步失败都退回原行为（一句话 summary），焦点失败绝不能成为新的单点故障。
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .config import Config
from .models import Signal

log = logging.getLogger("collector.focus")

# 焦点候选的来源层级优先级：一手（官方发布 / 监管原文）排前面
FIRST_HAND_TIERS = ("official", "authority")

# 抓正文用的 UA。默认的 requests UA 会被部分站点直接 403。
FETCH_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 只带 UA 会被一部分站点直接 403（openai.com 就是），把常见的浏览器请求头补齐。
FETCH_HEADERS = {
    "User-Agent": FETCH_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}

# 代码围栏（三个反引号）。写成常量是为了避免源码里出现连续三个反引号，
# 有些工具链会把它当 Markdown 围栏截断。
BT = chr(96) * 3

FACT_PROMPT = """你是情报站的资料员。下面给你一篇文章的正文，请从中摘出「可核对的事实」。

只输出一个 JSON 对象：
{"claims": [{"text": "中文一句话事实，≤40 字", "quote": "正文里支撑它的原文片段，逐字照抄，10–120 字"}],
 "numbers": [{"value": "数字", "unit": "单位", "what": "这个数字指什么"}],
 "what_changed": "一句话（≤60 字）说清这条为什么值得今天单独讲"}

硬性要求：
1. quote 必须能在正文里**逐字**找到（连标点都要一致），找不到的不要写；
2. 不要推测、不要补充原文之外的背景知识；不许出现「据悉」「可能」「或将」；
3. claims 给 4–8 条，宁少勿假；numbers 只收正文里出现的数字；
4. 只输出 JSON 对象，不要任何解释。"""

WRITE_PROMPT = """你是情报站的主笔，给集团内部读者写一篇 600–1000 字的短稿。

## 边界（最重要）
只能使用「已核对事实」里的内容。不要引入任何新的事实、数字、机构名或时间。
允许做的只有：把事实串成通顺的中文、解释它们之间的关系、指出原文没说的部分。
不许写「重磅」「史诗级」「颠覆」这类营销词，不许写行动建议（不做决定，只给判断）。

## 输出（只输出这个 JSON 对象）
{"deck": "导语，80–120 字，说清发生了什么、为什么值得今天单独讲",
 "what": "发生了什么，150–250 字",
 "data": ["关键数据，每条 ≤24 字，2–4 条"],
 "impact": "对金风意味着什么，80–120 字，只写影响面 + 一个能被证伪的判断",
 "open": "还有什么不确定，≤80 字，原文没说的、只有一方口径的",
 "us": "首页「对金风」一句，≤50 字",
 "points": ["3 条要点，每条 ≤24 字，给首页摘要用"]}"""


@dataclass
class Article:
    deck: str = ""
    sections: dict = field(default_factory=dict)   # what / data / impact / open
    points: list[str] = field(default_factory=list)
    us: str = ""
    claims: list[dict] = field(default_factory=list)
    minutes: int = 0
    chars: int = 0


@dataclass
class Outcome:
    sig: Signal | None = None
    article: Article | None = None
    degraded: bool = False
    reason: str = ""        # 没出稿 / 降级的原因，写进 round report
    tried: int = 0


def _norm(s: str) -> str:
    """引文比对用的归一化：全角转半角、去空白、小写。"""
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"\s+", "", s).lower()


def parse_json_object(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(BT + r"(?:json)?\s*(.*?)" + BT, text, re.S)
    if m:
        text = m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("模型输出里没有 JSON 对象")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("JSON 顶层不是对象")
    return obj


# ---------- 1. 取正文 ----------

def fetch_text(url: str, timeout: float = 20, max_chars: int = 8000) -> str:
    """抓原文页并抽出正文。失败抛异常，由调用方换下一条候选。"""
    r = requests.get(url, headers=FETCH_HEADERS, timeout=timeout)
    r.raise_for_status()
    # 不少中文站点不给 charset，requests 会猜成 ISO-8859-1，正文直接变乱码
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer",
                     "aside", "form", "iframe", "svg", "figure"]):
        tag.decompose()
    blocks, seen = [], set()
    for el in soup.find_all(["h1", "h2", "h3", "p", "li"]):
        t = el.get_text(" ", strip=True)
        if len(t) < 20:              # 导航、按钮、「阅读全文」这类碎片
            continue
        key = _norm(t)
        if not key or key in seen:
            continue
        seen.add(key)
        blocks.append(t)
    return "\n".join(blocks)[:max_chars]


# ---------- 2. 抽事实 + 校验 ----------

def extract_facts(sig: Signal, text: str, llm, cfg: Config) -> dict:
    fc = cfg.settings.get("focus") or {}
    user = f"标题：{sig.title}\n来源：{sig.sources[0].name if sig.sources else ''}\n\n正文：\n{text}"
    reply = llm.chat(FACT_PROMPT, user,
                     timeout=fc.get("llm_timeout_seconds", 90),
                     max_tokens=fc.get("llm_max_tokens", 1600))
    return parse_json_object(reply)


def verify_claims(facts: dict, text: str) -> tuple[list[dict], list[dict]]:
    """逐字校验：引文必须在正文里原样出现，数字必须在正文里出现。

    这是整条链路的质检闸门。模型很擅长写「听起来对」的事实，
    但只要引文对不上原文，就说明它在补背景知识，必须删。
    """
    body = _norm(text)
    claims: list[dict] = []
    for c in facts.get("claims") or []:
        if not isinstance(c, dict):
            continue
        t = str(c.get("text") or "").strip()
        q = str(c.get("quote") or "").strip()
        if not t or len(q) < 8:
            continue
        if _norm(q) not in body:
            log.info("焦点事实校验不通过（引文不在正文里）：%s", t[:40])
            continue
        claims.append({"text": t[:80], "quote": q[:240]})
    # 模型常把同一句原文拆成两条事实（「592 个」「2042.8 万千瓦」出自同一句）。
    # 合并成一条，否则「关键事实」列表读起来像重复。
    merged, by_quote = [], {}
    for c in claims:
        k = _norm(c["quote"])
        if k in by_quote:
            prev = by_quote[k]
            if c["text"] not in prev["text"]:
                prev["text"] = (prev["text"] + "；" + c["text"])[:120]
            continue
        by_quote[k] = c
        merged.append(c)
    claims = merged
    numbers: list[dict] = []
    for n in facts.get("numbers") or []:
        if not isinstance(n, dict):
            continue
        v = str(n.get("value") or "").strip()
        if not v or _norm(v) not in body:
            continue
        numbers.append({"value": v[:20], "unit": str(n.get("unit") or "")[:20],
                        "what": str(n.get("what") or "")[:40]})
    return claims, numbers


# ---------- 3. 成稿 ----------

def _clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def write_article(sig: Signal, claims: list[dict], numbers: list[dict],
                  what_changed: str, llm, cfg: Config) -> Article:
    fc = cfg.settings.get("focus") or {}
    payload = {
        "标题": sig.title,
        "事件日期": sig.event_date or "",
        "已核对事实": [c["text"] for c in claims],
        "原文引文": [c["quote"] for c in claims],
        "可用数字": numbers,
        "为什么值得展开": what_changed,
    }
    reply = llm.chat(WRITE_PROMPT, json.dumps(payload, ensure_ascii=False),
                     timeout=fc.get("llm_timeout_seconds", 90),
                     max_tokens=fc.get("llm_max_tokens", 1600))
    obj = parse_json_object(reply)

    deck = _clip(str(obj.get("deck") or ""), 160)
    what = _clip(str(obj.get("what") or ""), 600)
    impact = _clip(str(obj.get("impact") or ""), 400)
    open_ = _clip(str(obj.get("open") or ""), 200)
    us = _clip(str(obj.get("us") or sig.us or ""), 60)
    data = [_clip(str(x), 30) for x in (obj.get("data") or []) if str(x).strip()][:4]
    points = [_clip(str(x), 24) for x in (obj.get("points") or []) if str(x).strip()][:3]
    if not data:
        data = [_clip((str(n["value"]) + str(n["unit"]) + " " + str(n["what"])).strip(), 30)
                for n in numbers][:4]
    if not points:
        points = [_clip(c["text"], 24) for c in claims][:3]

    body_chars = len(deck) + len(what) + len(impact) + len(open_) + sum(len(x) for x in data)
    if len(deck) < 30 or len(what) < 80:
        raise ValueError(f"成稿过短（导语 {len(deck)} 字、正文 {len(what)} 字）")
    minutes = max(1, round(body_chars / 350))     # 中文阅读约 350 字/分钟
    return Article(deck=deck, sections={"what": what, "data": data,
                                        "impact": impact, "open": open_},
                   points=points, us=us, minutes=minutes, chars=body_chars)


# ---------- 4. 选题 ----------

def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _rank_key(sig: Signal, stats: dict | None = None) -> tuple:
    tier = sig.sources[0].tier if sig.sources else ""
    # 站点可抓性排在最前面：焦点是硬要求（抓不到正文就出不了稿），
    # 一手/相关度只在「都抓得到」的前提下比较，否则每轮都白花 3 次超时在同一批 JS 渲染页上。
    return (0 if not stats else _host_penalty(sig, stats),
            0 if tier in FIRST_HAND_TIERS else 1,
            -sig.relevance,
            {"green": 0, "yellow": 1, "red": 2}.get(sig.cred, 3),
            -_ts(sig.published_at or sig.event_date or sig.discovered_at))


def recent_keys(store, days: int = 7, now: datetime | None = None) -> set:
    """近 N 天做过的焦点事件键。state 表里一行 JSON，够用且不依赖新表。

    时间基准必须用调用方传进来的 now（和 remember() 写进去的是同一个钟），
    不能用 datetime.now()：否则补跑历史、或单测里固定的 now 会和真实时钟错位，
    同一个事件会被反复做成焦点。
    """
    raw = store.get_state("focus_history") or "{}"
    try:
        hist = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    base = now or datetime.now()
    cutoff = (base - timedelta(days=days)).date().isoformat()
    return {k for k, d in hist.items() if str(d) >= cutoff}


def remember(store, event_key: str, now: datetime) -> None:
    raw = store.get_state("focus_history") or "{}"
    try:
        hist = json.loads(raw)
    except json.JSONDecodeError:
        hist = {}
    hist[event_key] = now.date().isoformat()
    if len(hist) > 60:                            # 别让这个 state 无限长
        for k in sorted(hist, key=lambda x: hist[x])[:-60]:
            hist.pop(k, None)
    store.set_state("focus_history", json.dumps(hist, ensure_ascii=False))


def pick(signals: list, cfg: Config, store, now: datetime) -> list:
    """挑出本轮可以做成焦点的候选，按优先级排好序。"""
    fc = cfg.settings.get("focus") or {}
    hours = int(fc.get("recency_hours", 24))
    cutoff = (now - timedelta(hours=hours)).date().isoformat()
    done = recent_keys(store, now=now)
    out = [s for s in signals
           if s.relevance >= 2
           and not s.focus
           and s.sources and (s.sources[0].url or "").startswith("http")
           and (s.event_date or s.discovered_at or "")[:10] >= cutoff
           and s.event_key not in done]
    stats = host_stats(store)
    out.sort(key=lambda s: _rank_key(s, stats))
    return out


def mark_focus(sig: Signal, art: Article, degraded: bool) -> None:
    """把成稿写回信号本身。发帖与首页都读这几个字段。"""
    sig.focus = True
    sig.focus_deck = art.deck
    sig.focus_sections = dict(art.sections)
    sig.focus_points = list(art.points)
    sig.focus_claims = list(art.claims)
    sig.focus_minutes = art.minutes
    if art.us:
        sig.us = art.us
    if degraded:
        # 简讯版：事实少，只在正文里标注，不占首页摘要的位置
        sig.focus_sections["degraded"] = "1"


def candidate_urls(sig: Signal) -> list:
    """这条信号所有可抓的原文地址（按现有顺序，去重）。

    一条信号常聚合了多家来源（合并更新的结果）。新华网这类站点正文是 JS 渲染的，
    bs4 抓不到；同一条事件若有官方原文（能源局/工信部这种静态页），抓得到。
    所以按来源顺序逐个试，而不是只认 sources[0]。
    """
    out, seen = [], set()
    for s in sig.sources:
        u = (s.url or "").strip()
        if u.startswith("http") and u not in seen:
            seen.add(u)
            out.append((s, u))
    return out


def host_stats(store) -> dict:
    """各站点「能不能抓到正文」的历史记录。

    首轮之后就能看出：新华网（JS 渲染）永远抓不到、能源局官网永远抓得到。
    把它记下来，选题时优先挑抓得到的站点——否则每轮都在同一批抓不到的候选上白花 3 次超时。
    """
    raw = (store.get_state("focus_host_stats") if store is not None else None) or "{}"
    try:
        stats = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in stats.items() if isinstance(v, dict)}


def _record_host(store, url: str, ok: bool) -> None:
    if store is None:
        return
    host = urlparse(url).netloc.lower()
    if not host:
        return
    stats = host_stats(store)
    row = stats.get(host) or {"ok": 0, "fail": 0}
    row["ok" if ok else "fail"] = int(row.get("ok" if ok else "fail", 0)) + 1
    stats[host] = row
    if len(stats) > 120:                          # 只留最近用得上的，别无限长
        stats = dict(sorted(stats.items(), key=lambda kv: -(kv[1].get("ok", 0) + kv[1].get("fail", 0)))[:120])
    store.set_state("focus_host_stats", json.dumps(stats, ensure_ascii=False))


def _host_penalty(sig: Signal, stats: dict) -> int:
    """0 = 没记录或抓到过；1 = 只失败过（排在后面，但没有被排除）。"""
    worst = 0
    for _src, url in candidate_urls(sig):
        row = stats.get(urlparse(url).netloc.lower())
        if not row:
            return 0
        if int(row.get("ok", 0)) == 0:
            worst = 1
    return worst


def _fetch_any(sig: Signal, fetch, fc: dict, store=None):
    """按来源顺序抓正文，返回 (正文, 真正抓到的那条来源)；都失败返回 (None, None)。"""
    min_chars = int(fc.get("min_body_chars", 200))
    for src, url in candidate_urls(sig)[:2]:
        try:
            text = fetch(url)
        except Exception as e:                      # noqa: BLE001
            _record_host(store, url, False)
            log.info("焦点正文抓取失败（换下一个来源）：%s | %s | %s", src.name, url[:60], e)
            continue
        if len(text) < min_chars:
            # 实测新华网正文由 JS 注入，HTML 里一个 <p> 都没有：抓到的是导航栏。
            _record_host(store, url, False)
            log.info("焦点正文过短（%d 字，可能是 JS 渲染页）：%s | %s", len(text), src.name, url[:60])
            continue
        _record_host(store, url, True)
        return text, src
    return None, None


def _fetch_body(sig: Signal, fc: dict, fetch, store=None):
    """抓正文并把「真正抓到的那条来源」排到 sources 首位。"""
    text, used = _fetch_any(sig, fetch, fc, store)
    if text is not None and used is not None and sig.sources and sig.sources[0] is not used:
        # 事实出自哪条来源，来源块就首列哪条 —— 不能把「抓不到的那条」摆在首位
        sig.sources.sort(key=lambda s: 0 if s is used else 1)
    return text, used


def fetch_signal_body(sig: Signal, cfg: Config, fetch=None, store=None):
    """对外入口：给一条信号抓正文（scripts/focus_pilot.py 也走这里，保证与线上一致）。"""
    fc = cfg.settings.get("focus") or {}
    fetch = fetch or (lambda url: fetch_text(url,
                                            timeout=fc.get("fetch_timeout_seconds", 20),
                                            max_chars=fc.get("fetch_max_chars", 8000)))
    return _fetch_body(sig, fc, fetch, store)


def build(signals: list, cfg: Config, llm, store, now: datetime, fetch=None) -> Outcome:
    """本轮最多出一篇焦点。返回 Outcome；失败时 sig 为 None，reason 说明原因。"""
    fc = cfg.settings.get("focus") or {}
    out = Outcome()
    if not fc.get("enabled"):
        out.reason = "focus.enabled=false"
        return out
    cands = pick(signals, cfg, store, now)
    if not cands:
        out.reason = "本轮没有新的焦点级事件（沿用上一轮焦点）"
        return out

    if fetch is None:
        fetch = (lambda url: fetch_text(url,
                                        timeout=fc.get("fetch_timeout_seconds", 20),
                                        max_chars=fc.get("fetch_max_chars", 8000)))
    claims_min = int(fc.get("claims_min", 4))
    degraded_min = int(fc.get("degraded_claims_min", 2))
    for sig in cands[: int(fc.get("max_tries", 3))]:
        out.tried += 1
        # 整条候选的每一步都包在 except 里：焦点是「锦上添花」，任何未知异常都只能
        # 让它跳过这一条，绝不能把整轮采集带崩（模型的返回格式没法穷举）。
        try:
            text, _used = _fetch_body(sig, fc, fetch, store)
        except Exception as e:                      # noqa: BLE001
            log.warning("焦点候选准备失败（跳过）：%s | %s", sig.title[:32], e)
            continue
        if text is None:
            continue
        try:
            facts = extract_facts(sig, text, llm, cfg)
            if not isinstance(facts, dict):
                raise ValueError("事实抽取返回的不是对象")
            claims, numbers = verify_claims(facts, text)
            if len(claims) < degraded_min:
                log.info("焦点候选可核对事实不足（%d/%d 条，跳过）：%s",
                         len(claims), degraded_min, sig.title[:32])
                continue
            degraded = len(claims) < claims_min
            art = write_article(sig, claims, numbers, str(facts.get("what_changed") or ""), llm, cfg)
            art.claims = claims
            mark_focus(sig, art, degraded)
        except Exception as e:                      # noqa: BLE001
            log.warning("焦点抽取/成稿失败（跳过）：%s | %s", sig.title[:32], e)
            continue
        # 注意：这里**不**记 focus_history。成稿成功不等于发帖成功（可能 429、可能读回不一致），
        # 万一没发出去，事件键却已被记成「做过焦点」，这个事件就白丢 7 天。
        # 记事件的时机由调用方在**发帖成功后**掌握（pipeline 里那行 focus.remember）。
        out.sig, out.article, out.degraded = sig, art, degraded
        if degraded:
            out.reason = f"可核对事实只有 {len(claims)} 条，出简讯版"
        return out

    out.reason = f"试了 {out.tried} 条候选都没通过（正文抓取或事实校验）"
    return out
