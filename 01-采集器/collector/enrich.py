"""普通信号的结构化摘要精炼（2026-11-20）。

## 为什么要有这个模块
批量初筛阶段（tagger.py）只喂了 RSS / 列表页的 excerpt[:500]，且提示词限死 ≤120 字，
导致普通信号帖正文往往只有一句话（40–80 字）。如果只放宽字数上限而不抓原文正文，
模型拿到一两句话的 RSS 摘要硬凑 300 字，必然注水或编造。

## 做法（只对过门槛待发帖的非焦点信号执行）
1. 复用 focus.fetch_text 抓取原文正文（优先跳过已知连续抓不到的站，避免白等超时）；
2. 调用模型基于原文正文生成 ~300 字中文结构化摘要：
   - core   ：核心进展（80–110 字，不复述标题）
   - points ：2–4 条关键要点 / 数据（每条 35–55 字，只写原文明确给出的事实或数字）
   - impact ：影响与边界（50–80 字，公开可读口径，指出影响面与待观察项）
3. 严格校验字段完整度、中文占比、占位符与字数区间；任一步失败（抓不到正文、超时、
   格式不合法、字数过短）直接降级回原简短摘要，绝不阻断发帖、绝不凭标题硬编。
4. 帖子正文渲染完整结构化内容；首页底座帖 632 的 radar[].summary 仍保留精炼单段，
   避免撑爆 Discourse 32,000 字符上限和前端卡片单段展示。
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from . import focus
from .config import Config
from .models import Signal
from .tagger import zh_chars
from .textnorm import clean_summary_punctuation, drop_restating_lead
from .watchdog import RoundTimeout

log = logging.getLogger("collector.enrich")

ENRICH_PROMPT = """你是情报站的信号摘要编辑。根据下面给出的原文正文，将这条资讯提炼为约 300 字的中文结构化摘要。

只输出一个 JSON 对象，不要任何解释：
{"core": "核心进展，70–95 字：说清主体、具体发生了什么以及相比之前的新变化，第一句不要把标题原样复述一遍",
 "points": ["关键要点 1（30–45 字，优先写原文明确给出的数字、版本、机制、范围、时间或原话表态）",
            "关键要点 2（30–45 字）",
            "关键要点 3（30–45 字，共 2–3 条；原文事实不够就只写 2 条，不要硬凑）"],
 "impact": "影响与边界，45–70 字：说明对行业或实际落地的影响点，以及原文未交代或尚待验证的部分"}

硬性要求：
1. 严格忠实于原文：只写正文里明确出现的事实、数字、机构与结论，绝不推测、不补充原文之外的背景；
2. 控制三部分合计总字数在 240–320 字（约 300 字），每句都写完整，紧凑、无套话，不写「重磅」「颠覆」「值得关注」等空话；
3. 公开可读口径：帖子对所有人可见，主语不要写「集团」「金风」「我们」「我方」，涉及行业影响时用「企业」「行业」「风电/新能源领域」等外部视角；
4. points 每条是纯中文句子，不要自带项目符号（- / •）或序号（1. / 一、）；
5. 全文使用中文（专有名词、产品名、版本号、单位除外）；只输出一个 JSON 对象。"""

_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-•·*▪]+|\d+[.)、]|[一二三四五六七八九十]+[、.])\s*")
_PLACEHOLDER_RE = re.compile(r"\[(?:TODO|待补|占位)|\{\{|XXX")


def structured_char_count(ss: dict) -> int:
    """统计结构化摘要的读者可见正文字符数。"""
    if not isinstance(ss, dict):
        return 0
    core = str(ss.get("core") or "").strip()
    impact = str(ss.get("impact") or "").strip()
    points = [str(p).strip() for p in (ss.get("points") or []) if str(p).strip()]
    return len(core) + sum(len(p) for p in points) + len(impact)


def _clip_sentence(text: str, limit: int) -> str:
    """尽量按中文/英文句末标点截断，不生硬截断半个词，也不留末尾分号。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return clean_summary_punctuation(text)
    cut = text[:limit]
    # 优先找完整句末标点；不得已才退回分号并转成句号
    for sep in ("。", "！", "？", ". "):
        idx = cut.rfind(sep)
        if idx >= limit // 2:
            return cut[: idx + (1 if len(sep) == 1 else 0)].strip()
    for sep in ("；", "; "):
        idx = cut.rfind(sep)
        if idx >= limit // 2:
            return clean_summary_punctuation(cut[:idx].strip())
    return cut[: max(1, limit - 1)].rstrip("，,、：:；; ") + "…"


def _clean_point(text: str) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return _BULLET_PREFIX_RE.sub("", s).strip()


def validate_structured(obj: dict, cfg: Config, title: str = "") -> dict | None:
    """校验并清洗模型输出的结构化摘要；不达标返回 None（调用方降级回简版）。"""
    if not isinstance(obj, dict):
        return None
    ec = cfg.settings.get("structured_summary") or {}
    min_chars = int(ec.get("target_min_chars", 160))
    max_chars = int(ec.get("target_max_chars", 460))

    core = re.sub(r"\s+", " ", str(obj.get("core") or "")).strip()
    if title:
        core = drop_restating_lead(title, core)
    raw_points = obj.get("points")
    if not isinstance(raw_points, list):
        return None
    points = [_clean_point(p) for p in raw_points]
    points = [p for p in points if len(p) >= 8][:4]
    impact = re.sub(r"\s+", " ", str(obj.get("impact") or "")).strip()

    if len(core) < 30 or len(points) < 2 or len(impact) < 15:
        return None
    joined = core + "".join(points) + impact
    if _PLACEHOLDER_RE.search(joined):
        return None
    if zh_chars(core) < 15 or zh_chars(joined) < 80:
        return None

    ss = {"core": core, "points": points, "impact": impact}
    total = structured_char_count(ss)
    if total < min_chars:
        return None

    # 略微超长时优先裁掉第 4 条要点或温和收口，而不是白白浪费一次有效提炼
    if total > max_chars and len(ss["points"]) > 3:
        ss["points"] = ss["points"][:3]
        total = structured_char_count(ss)
    if total > max_chars:
        over = total - max_chars
        if len(ss["impact"]) - over >= 30:
            ss["impact"] = _clip_sentence(ss["impact"], len(ss["impact"]) - over)
        else:
            ss["core"] = _clip_sentence(ss["core"], 130)
            ss["points"] = [_clip_sentence(p, 70) for p in ss["points"][:3]]
            ss["impact"] = _clip_sentence(ss["impact"], 80)
        if structured_char_count(ss) < min_chars:
            return None

    return ss


def _host_chronically_unfetchable(url: str, stats: dict, fail_threshold: int = 2) -> bool:
    """只失败过且达到阈值的站点直接跳过抓取（例如 openai.com 403、新华网 JS 渲染）。"""
    host = urlparse(url).netloc.lower()
    row = stats.get(host)
    if not isinstance(row, dict):
        return False
    return int(row.get("ok", 0)) == 0 and int(row.get("fail", 0)) >= fail_threshold


def _fetch_for_signal(sig: Signal, cfg: Config, store=None, fetch=None) -> str | None:
    ec = cfg.settings.get("structured_summary") or {}
    min_chars = int(ec.get("min_body_chars", 200))
    timeout = float(ec.get("fetch_timeout_seconds", 15))
    max_chars = int(ec.get("fetch_max_chars", 6000))
    fail_threshold = int(ec.get("skip_host_after_fails", 2))
    stats = focus.host_stats(store) if store is not None else {}

    do_fetch = fetch or (lambda u: focus.fetch_text(u, timeout=timeout, max_chars=max_chars))
    for src, url in focus.candidate_urls(sig)[:2]:
        if fetch is None and _host_chronically_unfetchable(url, stats, fail_threshold):
            log.info("结构化摘要跳过已知不可抓站点：%s | %s", src.name, url[:60])
            continue
        try:
            text = do_fetch(url)
        except RoundTimeout:
            raise
        except Exception as e:  # noqa: BLE001
            focus._record_host(store, url, False)
            log.info("结构化摘要正文抓取失败：%s | %s | %s", src.name, url[:60], e)
            continue
        if not text or len(text) < min_chars:
            focus._record_host(store, url, False)
            log.info("结构化摘要正文过短（%d 字，降级回简版）：%s | %s",
                     len(text or ""), src.name, url[:60])
            continue
        focus._record_host(store, url, True)
        return text
    return None


def enrich_signal(sig: Signal, cfg: Config, llm, store=None, fetch=None) -> bool:
    """给单条非焦点信号补充 ~300 字结构化摘要。成功返回 True，降级/跳过返回 False。"""
    if sig.focus:
        return False
    ec = cfg.settings.get("structured_summary") or {}
    min_rel = int(ec.get("min_relevance", 2))
    if sig.relevance < min_rel:
        return False

    text = _fetch_for_signal(sig, cfg, store=store, fetch=fetch)
    if not text:
        return False

    src_name = sig.sources[0].name if sig.sources else ""
    user = (
        f"标题：{sig.title}\n"
        f"来源：{src_name}\n"
        f"初筛摘要：{sig.summary}\n\n"
        f"正文：\n{text}"
    )
    reply = llm.chat(
        ENRICH_PROMPT,
        user,
        timeout=float(ec.get("llm_timeout_seconds", 45)),
        max_tokens=int(ec.get("llm_max_tokens", 900)),
    )
    obj = focus.parse_json_object(reply)
    ss = validate_structured(obj, cfg, title=sig.title)
    if not ss:
        log.info("结构化摘要未通过校验（降级回简版）：%s", sig.title[:36])
        return False

    sig.structured_summary = ss
    # 首页 632 的 radar[].summary 使用基于原文提炼的精炼核心进展（≤140 字），
    # 既避免撑爆 32,000 字符上限，又比原来 40 字的 RSS 一句话更有信息量。
    sig.summary = _clip_sentence(ss["core"], 140)
    return True


def enrich_signals(signals: list[Signal], cfg: Config, llm, store=None, fetch=None) -> tuple[int, int]:
    """批量精炼待发帖的非焦点信号，返回 (精炼成功数, 跳过/降级数)。"""
    ec = cfg.settings.get("structured_summary") or {}
    if not ec.get("enabled"):
        return 0, 0
    done = skipped = 0
    for sig in signals:
        if sig.focus:
            continue
        try:
            ok = enrich_signal(sig, cfg, llm, store=store, fetch=fetch)
        except RoundTimeout:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("结构化摘要异常（降级回简版）：%s | %s", sig.title[:36], e)
            ok = False
        if ok:
            done += 1
        else:
            skipped += 1
    return done, skipped
