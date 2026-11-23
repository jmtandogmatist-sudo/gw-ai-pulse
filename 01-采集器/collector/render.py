"""把信号渲染成 Discourse 话题，以及把雷达数据写回底座帖。"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .config import Config
from .credibility import CRED_LABEL
from .models import Signal
from .textnorm import drop_restating_lead, is_restatement

CST = timezone(timedelta(hours=8))

log = logging.getLogger("collector.render")

# radar 条目里 summary / us 的上限。前端 normSignal 会按 LIMITS.summary=320 再裁一次，
# 所以在后端预裁到更短**不会**让读者看到更少内容——前端本来就只显示前 320 字符。
# 真正省下的是底座帖字符数：60 条 × (summary+us) 是超限的主因。
SUMMARY_LIMIT = 320

MACHINE_OPEN = '[details="机器可读数据"]'
MACHINE_CLOSE = "[/details]"
FOOTER = "摘要由 AI 生成，以原文为准。本话题由采集器自动发布。"
CRED_EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
CRED_CODE = {"green": "g", "yellow": "y", "red": "r"}   # 首页组件 ai-pulse-feed.js 认的代码


def topic_tags(sig: Signal, cfg: Config) -> list[str]:
    t = cfg.tags
    tags = [t["domains"][sig.domain]]
    tags += [q.lower() for q in sig.questions]
    tags += [t["topics"][x] for x in sig.topics if x in (t.get("topics") or {})]
    tags += [f"cred-{sig.cred}", sig.review]
    seen, out = set(), []
    for x in tags:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


CRED_CODE_SHORT = {"green": "g", "yellow": "y", "red": "r"}

# 「对金风」研判块的收尾标记（2026-11-19 起用折叠块，见 topic_body）
US_OPEN = '[details="AI 研判（自动生成，非公司结论）"]'


def _summary_lines(sig: Signal) -> list[str]:
    """渲染 [wrap=ais-summary] 内部文本。

    有 structured_summary 时输出「摘要（核心进展）+ 关键要点 + 影响与边界」约 300 字结构化内容；
    第一段紧跟在 **摘要** 空行后，保持与下游正则解析（signal_pool.py / forum.py）完全兼容。
    没有 structured_summary（抓不到正文降级或旧信号）时退回单段 sig.summary。
    """
    ss = sig.structured_summary if isinstance(sig.structured_summary, dict) else {}
    core = str(ss.get("core") or "").strip()
    points = [str(p).strip() for p in (ss.get("points") or []) if str(p).strip()]
    impact = str(ss.get("impact") or "").strip()
    if not (core and points):
        return ["[wrap=ais-summary]", "", "**摘要**", "", sig.summary, "", "[/wrap]", ""]
    lines = ["[wrap=ais-summary]", "", "**摘要**", "", core, "", "**关键要点**", ""]
    lines += [f"- {p}" for p in points]
    if impact:
        lines += ["", "**影响与边界**", "", impact]
    lines += ["", "[/wrap]", ""]
    return lines


def topic_body(sig: Signal, cfg: Config) -> str:
    """信号话题正文。用 [wrap=ais-*] 分块，主题组件「AI 情报站话题样式」渲染成卡片；没装组件时仍是可读的普通段落。

    下游解析依赖这里的写法（每日精选 signal_pool.py、mark_reviewed.py，主题档案 forum.py）：
    每个分块的标题行是 **名称**，标题行与正文之间、正文与 [/wrap] 之间都留空行。
    """
    qtext = {q["id"]: q["text"] for q in cfg.questions}
    code = CRED_CODE_SHORT[sig.cred]
    lines = [f"[wrap=ais-signal cred={code} review={sig.review}]", ""]
    lines += _summary_lines(sig)
    if sig.us:
        # gw-ai-pulse（2026-11-19）：分类 20 已对全员开放，「对金风」是模型自动研判、
        # 没有任何人工过目（review=auto），不能再以正文段落的形式直出。改成折叠块：
        # 默认收起，展开前先看到「自动生成，非公司结论」的定性。
        lines += [US_OPEN, "", sig.us, "", "[/details]", ""]
    lines += ["[wrap=ais-src]", "", "**来源**", ""]
    for s in sig.sources:
        note = "" if s.reachable else "（公司网络可能打不开）"
        lines.append(f"- [{s.name}]({s.url}){note}")
    lines += ["", "[/wrap]", "", "[wrap=ais-meta]", ""]
    # 分隔符用普通空格，不用全角空格 U+3000：Discourse 存 raw 时会剥掉 U+3000，
    # 导致发帖后「读回核对」永远对不上、每轮都被判失败（2026-11-06 实测）。
    lines.append(f"可信度：{CRED_EMOJI[sig.cred]} {CRED_LABEL[sig.cred]} · 相关度：{'●' * sig.relevance}{'○' * (3 - sig.relevance)}")
    if sig.questions:
        lines += ["", "**回答的问题**", ""]
        lines += [f"- {q} {qtext.get(q, '')}" for q in sig.questions]
    lines += ["", f"事件日期 {sig.event_date or '未知'} · 采集于 {sig.discovered_at}", "", "[/wrap]", "", "[/wrap]", "",
              "---", FOOTER]
    return "\n".join(lines)


def topic_body_focus(sig: Signal, cfg: Config) -> str:
    """焦点长文正文（2026-11-19「今日焦点单独成篇」，方案 A）。

    与 topic_body 的区别：把「摘要卡」换成「导语 + 四节正文 + 可核对事实」。
    保留 ais-signal / ais-src / ais-meta 三个块，主题的话题样式组件才认得；
    分节用普通 Markdown 标题，没装组件时也是一篇可读的文章。

    下游依赖：parse_machine_block 仍能从尾部机器块解析（字段比普通信号多
    focus_* 几项）；[details=关键事实…] 是给读者核对用的，不参与解析。
    """
    qtext = {q["id"]: q["text"] for q in cfg.questions}
    code = CRED_CODE_SHORT[sig.cred]
    sec = sig.focus_sections or {}
    lines = [f"[wrap=ais-signal cred={code} review={sig.review}]", ""]
    if sig.focus_deck:
        # 导语不用整段加粗：一段 120 字的粗体在论坛里像标题，读起来很吵
        lines += [sig.focus_deck, ""]
    if sec.get("degraded"):
        lines += ["*本篇为简讯：原文里可核对的事实不足 4 条，只保留已核实部分。*", ""]
    lines += ["## 发生了什么", "", sec.get("what") or sig.summary, ""]
    data = sec.get("data") or []
    if data:
        lines += ["## 关键数据", ""] + [f"- {x}" for x in data] + [""]
    if sec.get("impact") or sig.us:
        lines += ["## 对金风意味着什么", "", sec.get("impact") or sig.us, ""]
    if sec.get("open"):
        lines += ["## 还有什么不确定", "", sec["open"], ""]
    claims = sig.focus_claims or []
    if claims:
        lines += ['[details="关键事实与原文引文（可核对）"]', ""]
        for i, c in enumerate(claims, 1):
            lines += [f"{i}. {c.get('text', '')}", "", f"> {c.get('quote', '')}", ""]
        lines += ["[/details]", ""]
    elif sig.us and not sec.get("impact"):
        lines += [US_OPEN, "", sig.us, "", "[/details]", ""]
    lines += ["[wrap=ais-src]", "", "**来源**", ""]
    for s in sig.sources:
        note = "" if s.reachable else "（公司网络可能打不开）"
        lines.append(f"- [{s.name}]({s.url}){note}")
    lines += ["", "[/wrap]", "", "[wrap=ais-meta]", ""]
    lines.append(f"可信度：{CRED_EMOJI[sig.cred]} {CRED_LABEL[sig.cred]} · 相关度：{'●' * sig.relevance}{'○' * (3 - sig.relevance)}")
    minutes = sig.focus_minutes or 0
    tail = f"事件日期 {sig.event_date or '未知'} · 采集于 {sig.discovered_at}"
    if minutes:
        tail += f" · 阅读约 {minutes} 分钟"
    lines += ["", tail]
    if sig.questions:
        lines += ["", "**回答的问题**", ""]
        lines += [f"- {q} {qtext.get(q, '')}" for q in sig.questions]
    lines += ["", "[/wrap]", "", "[/wrap]", "", "---",
              "本篇为「今日焦点」长文：正文由采集器根据原文自动生成，事实与引文列在上面的折叠块里，"
              "以原文为准；「对金风」研判为自动生成，非公司结论。"]
    return "\n".join(lines)


def focus_topic_tags(sig: Signal, cfg: Config) -> list[str]:
    """焦点话题的标签：普通信号标签 + 专用 focus 标签。

    focus 标签是论坛里筛出焦点、以及后续做「焦点归档」的锚点；
    标题不加【焦点】前缀——前缀会同时出现在 03 雷达卡片里，把列表读成一堆方括号。
    """
    tag = (cfg.settings.get("focus") or {}).get("tag", "focus")
    out, seen = [], set()
    for x in topic_tags(sig, cfg) + [tag]:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def focus_lead_item(sig: Signal) -> dict | None:
    """首页 02 的焦点卡数据（方案 A：不改前端，靠 summary 写满 320 字 + 标题链到话题）。

    内容 = 导语 + 3 条要点。要点做成「· 」串在句尾，前端只画一段也能读。

    2026-11-19 决定：方案 B（前端把导语与要点拆成两块排版）**不做**——我们没有前端主题的
    修改权限。所以 02 就保持「一段 ≤320 字」的形态，导语要写得能单独站住，要点只是补充。
    points 仍留在数据里（focus_points + 这里拼进 summary），将来有权限时可直接拆，不用改契约。
    """
    if not sig.focus:
        return None
    deck = _clip(sig.focus_deck or sig.summary, 200)
    points = [p for p in (sig.focus_points or []) if p][:3]
    summary = deck if not points else f"{deck} " + " · ".join(points)
    return {
        "cred": CRED_CODE.get(sig.cred, "r"),
        "title": _strip_date_suffix(sig.title),
        "summary": _clip(summary, 320),
        "us": _clip(sig.us, 200),
        "sources": [{"label": src.name, "url": src.url} for src in sig.sources[:3]],
        "url": "",                      # update_feed 里填话题链接
        "topic_id": sig.topic_id,
        "kind": "focus",                # 18 个字符，够便宜：前端以后据此区分焦点/普通
    }


def parse_machine_block(raw: str) -> dict | None:
    m = re.search(r'\[details="机器可读数据"\]\s*```json\s*(.*?)```', raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def public_topic_body(sig: Signal, cfg: Config) -> str:
    """公开版正文：只讲事实与来源，不含「对金风」内部判断。

    为什么要有公开版：分类 20 是 staff-only，全公司看不到。对「双重验证 + 重要」的信号
    额外发一份到公开分类（trends），让更多人知道；内部帖保留完整判断和讨论。
    公开版刻意不带 machine block 的 us 字段，也不带问题编号——这些是内部编排信息。
    """
    qtext = {q["id"]: q["text"] for q in cfg.questions}
    code = CRED_CODE_SHORT[sig.cred]
    lines = [f"[wrap=ais-signal cred={code} review={sig.review}]", ""]
    lines += _summary_lines(sig)
    lines += ["[wrap=ais-src]", "", "**来源**", ""]
    for s in sig.sources:
        note = "" if s.reachable else "（公司网络可能打不开）"
        lines.append(f"- [{s.name}]({s.url}){note}")
    lines += ["", "[/wrap]", "", "[wrap=ais-meta]", ""]
    lines.append(f"可信度：{CRED_EMOJI[sig.cred]} {CRED_LABEL[sig.cred]}"
                 f" · 相关度：{'●' * sig.relevance}{'○' * (3 - sig.relevance)}")
    if sig.questions:
        lines += ["", "**关联议题**", ""]
        lines += [f"- {qtext.get(q, q)}" for q in sig.questions]
    lines += ["", f"事件日期 {sig.event_date or '未知'}", "", "[/wrap]", "", "[/wrap]", ""]
    lines += ["---", "本条由 AI 雷达自动采集并完成双源核验后公开，摘要由 AI 生成，以原文为准。"
                    "内部研判请见情报站「信号」分类。"]
    return "\n".join(lines)


def public_topic_title(sig: Signal) -> str:
    """公开帖标题。

    论坛不允许同分类/跨分类的完全同名话题（实测 POST 返回 422「标题 已被使用」），
    而内部帖已经占用了原标题。加一个固定后缀区分两帖，同时让公开读者知道
    「这是 AI 雷达采集的信号」而不是官方发布。
    """
    base = _strip_date_suffix(sig.title)
    suffix = "｜AI 雷达信号"
    # 论坛标题上限 255，这里按 200 保守截断
    room = 200 - len(suffix)
    if len(base) > room:
        base = base[: room - 1].rstrip() + "…"
    return base + suffix


def public_topic_tags(sig: Signal, cfg: Config) -> list[str]:
    """公开帖标签：只要领域与可信度，去掉 q0x / auto / 主题白名单这类内部编排标签。"""
    t = cfg.tags
    tags = [t["domains"][sig.domain], f"cred-{sig.cred}", "auto"]
    seen, out = set(), []
    for x in tags:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---------- 底座帖 ----------

FEED_BLOCK = re.compile(r"```json\s*(.*?)```", re.S)


def read_feed(raw: str) -> tuple[dict, str]:
    """返回 (feed 数据, 包装模式)。包装模式 fenced = 正文里有 ```json 代码块；bare = 整个正文就是 JSON。"""
    m = FEED_BLOCK.search(raw)
    if m:
        return json.loads(m.group(1)), "fenced"
    text = raw.strip()
    if text.startswith("{"):
        return json.loads(text), "bare"
    raise ValueError("底座帖里找不到 JSON 数据块")


# gw-ai-pulse（2026-11-17）：底座帖正文的字符预算。
#
# 事故：Discourse 拒绝超过 32000 字符的帖子正文，而 632 的 JSON 包在
# 2026-11-17 15:03 涨到 33950 → 整篇写入被 422 打回 → 首页一整天不刷新，
# 而采集器自己的统计里 posted=6、feed_updated=false，看起来像「跑成功了」。
# 直接原因是 radar 无上限增长（38 条占 22709 字符，即整包的 76%）。
#
# 修法分三层，从「最不影响观感」开始：
#   1. 条数上限：max_items 由 settings 控制，默认降到 40（72 小时内实际也就 38 条）。
#   2. 字段裁剪：radar 条目只保留前端真正渲染的字段（见 radar_items）。
#   3. 兜底闸：写之前量一次 json 长度，超预算就二分收缩 radar 尾部，直到放下。
#      这一层保证「无论数据怎么长，写入永远不会因为超长而失败」。
POST_CHAR_LIMIT = 32000        # Discourse 硬上限（实测 33950 被拒，29969 可写）
POST_CHAR_BUDGET = 28000       # 自己的安全线，留 4000 字符余量
# 实测（2026-11-17，50 条 72h 信号）：radar 每条均摊约 410 字符。
#   30 条 → 23444 字符（73%）   40 条 → 29184（91%）
#   45 条 → 32041（100%，被拒）  50 条 → 35001（109%，被拒）
# 所以 40 条是能稳定写下的上限；再往上必须靠 _fit_feed 自动收缩。


def write_feed(raw: str, feed: dict, mode: str) -> str:
    body = _fit_feed(feed)
    if mode == "fenced":
        m = FEED_BLOCK.search(raw)
        fence = "`" * 3
        return raw[:m.start()] + fence + "json\n" + body + "\n" + fence + raw[m.end():]
    return body


def _fit_feed(feed: dict) -> str:
    """序列化 feed；超过预算时收缩 radar，保证正文一定能写进帖子。

    为什么砍 radar 而不是别的：radar 是唯一随采集轮次无限增长的字段，
    brief/lead/picks/community/topics 都有固定条数上限。
    砍的顺序从**尾部**开始——尾部本来就是相关度最低、时间最旧的那批。
    """
    body = json.dumps(feed, ensure_ascii=False, indent=2)
    if len(body) <= POST_CHAR_BUDGET:
        return body

    radar = feed.get("radar")
    if not isinstance(radar, list) or not radar:
        # radar 都空了还超预算，说明是别的字段异常膨胀；至少不能静默超限。
        log.warning("底座帖正文 %d 字符超出预算，但 radar 已为空，无法再收缩", len(body))
        return body

    total = len(radar)
    # 二分找「能放下的最大条数」，比逐条删快，也比按比例砍精确
    lo, hi, best = 0, total, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        trial = dict(feed, radar=radar[:mid])
        n = len(json.dumps(trial, ensure_ascii=False, indent=2))
        if n <= POST_CHAR_BUDGET:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    feed["radar"] = radar[:best]
    body = json.dumps(feed, ensure_ascii=False, indent=2)
    log.warning("底座帖正文超出预算，radar 收缩 %d → %d 条，现 %d 字符",
                total, best, len(body))
    return body


# ---------- 信号雷达排序（2026-11-17） ----------
#
# 用户要求的排序：时间最新(权重5) > 信息源级别(3) > 相关度(2)。
#
# 为什么不用加权求和：实测「昨天 绿标+相关3」=8.33 会压过「今天 红标+相关2」=6.33，
# 因为 3+2=5 的合计权重正好抵消一天的时效差——用户看到的仍然是昨天的内容，
# 与「时间最新」的诉求相悖。所以把「时间权重5」落实成日粒度分组优先，
# 3:2 只用在同一天内部的排序上，两个权重 1.5 倍的关系才真正体现。
#
# 注意：必须与主题组件 ai-pulse-home.gjs 里的 sortedRadar 保持一致。
# 采集器侧排序决定 max_items 截断时先砍谁（砍的必须是「最旧」而不是「最不相关」），
# 前端排序决定最终展示顺序。两处规则不一致会出现「首屏顺序与截断逻辑矛盾」。

CRED_LEVEL = {"green": 1.0, "yellow": 0.5, "red": 0.0}   # 信息源级别，三档满量程归一


def _day_bucket(iso: str, now: datetime) -> int:
    """0=今天，1=昨天，2+=距今天数；无法解析的排到最后。"""
    day = (iso or "")[:10]
    try:
        d = datetime.fromisoformat(day).date()
    except ValueError:
        return 9999
    delta = (now.date() - d).days
    return delta if delta >= 0 else 9999


def radar_rank(sig: Signal, now: datetime) -> tuple:
    """信号雷达排序键：日分组 > 组内 3:2 加权 > 精确时间 > 标题。"""
    stamp = sig.published_at or sig.event_date or ""
    bucket = _day_bucket(stamp, now)
    # 组内分 = 3 × 信息源级别 + 2 × (相关度/3)，满分 5
    inner = 3 * CRED_LEVEL.get(sig.cred, 0.0) + 2 * (sig.relevance or 0) / 3
    # 精确时间：published_at 带时分时才能真正区分同一天内的先后
    return (bucket, -inner, -_ts(stamp), sig.title)


def radar_items(signals: list[Signal], cfg: Config, base_url: str) -> list[dict]:
    """03 信号雷达的条目。

    gw-ai-pulse（2026-11-17）：字段精简。原先每条塞 12 个字段，其中
    `discovered_at` 与 `published_at` 二选一即可（前端 parseWhen 优先读 published_at，
    缺失才退回 discovered_at），而 `tags` 在卡片上只是主题名 chip、`questions` 只用于
    筛选计数，两条合计约占 18 字符/条。60 条就是 1000+ 字符的纯冗余，
    而底座帖有 32000 字符硬上限（见 write_feed 的注释）。
    这里只保留前端 normSignal 真正读取的字段。
    """
    fmt = cfg.settings["feed"].get("cred_format", "code")
    lim = cfg.settings["feed"].get("summary_limit", SUMMARY_LIMIT)
    items = []
    for s in signals:
        src = s.sources[0] if s.sources else None
        items.append({
            "cat": s.domain,
            "cred": CRED_EMOJI[s.cred] if fmt == "emoji" else CRED_CODE[s.cred],
            "from": src.name if src else "",
            "title": s.title,
            "summary": _clip(s.summary, lim),
            "url": src.url if src else "",
            "tags": list(s.topics),
            "relevance": s.relevance,
            # gw-ai-pulse（2026-11-17）：优先用带时分的 published_at，排序要靠它区分
            # 同一天内多条信号的先后；没有时分才退回日期级的 event_date。
            "published_at": s.published_at or s.event_date or s.discovered_at,
            "topic_url": f"{base_url.rstrip('/')}/t/{s.topic_id}" if s.topic_id else "",
            "review": s.review,
            "questions": list(s.questions),
            "us": _clip(s.us, 200),
        })
    return items


# ---------- 02 右栏：金风 / 能源相关 ----------
# v0.4.7：02 右栏原先读论坛分类 18「深度阅读」，但 06 栏目更新里也有深度阅读，
# 两块重复。改成按「集团问题」筛出来的能源向信号：Q01（风电 AI 场景）、
# Q02（人工智能+ 能源政策）、Q09（AI 用电需求与风电机会），外加 domain=能源AI。
# 2026-11-17 实测 72 小时窗口内命中 7 条 / 37 条雷达，够填 3 个位置。
#
# 为什么不用 radar_items：那是给 03 雷达用的全字段卡片（15 个字段、约 600 字符/条），
# 底座帖正文有 32000 字符硬上限，02 右栏只要标题+来源+日期+链接，650 字符能省一半。
def energy_items(signals: list[Signal], cfg: Config, base_url: str, *,
                 limit: int = 3) -> list[dict]:
    """02 右栏「金风 / 能源相关」的条目（精简字段）。"""
    out = []
    for s in signals[:limit]:
        src = s.sources[0] if s.sources else None
        out.append({
            "title": s.title,
            "cat": s.domain,
            "from": src.name if src else "",
            "url": src.url if src else "",
            "topic_url": f"{base_url.rstrip('/')}/t/{s.topic_id}" if s.topic_id else "",
            "when": (s.published_at or s.event_date or s.discovered_at or "")[:10],
        })
    return out


def is_research(sig: Signal) -> bool:
    """是否属于学术论文（arXiv 等 tier=research 或 domain=前沿研究）。"""
    if sig.domain == "前沿研究":
        return True
    return bool(sig.sources) and all(s.tier == "research" for s in sig.sources)


# 02 右栏「金风 / 能源相关」的强相关词校验。
# 2026-11-20 生产实测：模型常把 Q01（行业场景）或 Q09（AI 用电）顺手标在泛 AI 收购/机器人论文上，
# 导致「英伟达收购 Inception」「AgentRoboCritic 机器人论文」占了能源右栏前两位。
# 规则：domain=能源AI 或 (domain=政策合规 且含 Q02) 直接放行；其余领域即使命中 Q01/Q02/Q09，
# 也要求标题或首发信源含明确能源/风电/电力词，避免泛 AI 内容混入能源专栏。
_ENERGY_KW = re.compile(
    r"风电|风机|风场|风能|绿电|电力|电网|储能|光伏|新能源|虚拟电厂|能源|电厂|水风光|用电|发电|"
    r"碳排|排放|双碳|零碳|海上风|陆上风|整机|金风|远景|明阳|维斯塔斯|西门子歌美飒|"
    r"Vestas|GWEC|WindEurope|Offshore|Windpower|Electrek|reNews|GE\s*Vernova|Nordex|Siemens\s*Energy",
    re.I,
)


def _has_energy_signal(sig: Signal) -> bool:
    src_name = sig.sources[0].name if sig.sources else ""
    return bool(_ENERGY_KW.search(f"{sig.title or ''} {src_name}"))


def select_radar_signals(
    signals: list[Signal],
    *,
    cap: int,
    max_papers: int = 0,
    max_per_source: int = 0,
    max_per_source_per_day: int = 0,
) -> list[Signal]:
    """筛选 03 信号雷达条目（保持传入的 radar_rank 顺序）。

    2026-11-23：此前 03 信号雷达只限制了学术论文数（radar_max_papers），对商业媒体
    完全不限额，导致周末/周一上午单一媒体（IT之家）占全量 39%、首屏 83%。
    现在对非一手/非论文信源（media / community）按站点（_limit_key）增加
    「单日上限 + 窗口总上限」双闸门；官方/监管一手源（official / authority）不限额，
    学术论文继续由独立的 max_papers 管控，设 0 即关闭对应限制。
    """
    out: list[Signal] = []
    papers = 0
    host_total: dict[str, int] = {}
    host_day: dict[tuple[str, str], int] = {}
    for x in signals:
        if len(out) >= cap:
            break
        if is_research(x):
            if max_papers > 0 and papers >= max_papers:
                continue
            papers += 1
            out.append(x)
            continue
        tier = x.sources[0].tier if x.sources else ""
        if tier in ("official", "authority"):
            out.append(x)
            continue
        host = _limit_key(x)
        day = (x.published_at or x.event_date or x.discovered_at or "")[:10]
        if max_per_source > 0 and host_total.get(host, 0) >= max_per_source:
            continue
        if max_per_source_per_day > 0 and day and host_day.get((host, day), 0) >= max_per_source_per_day:
            continue
        host_total[host] = host_total.get(host, 0) + 1
        if day:
            host_day[(host, day)] = host_day.get((host, day), 0) + 1
        out.append(x)
    return out


def pick_energy(signals: list[Signal], *, questions: set[str] | None = None,
                domains: set[str] | None = None, limit: int = 3,
                per_source: int = 0) -> list[Signal]:
    """挑出「金风 / 能源相关」的信号，保持传入顺序（调用方已按 radar_rank 排好）。

    2026-11-23：新增 per_source（按站点分桶，默认 0=不限兼容旧调用），
    避免单一媒体占满 02 右栏能源专栏的 2/3 位置。
    """
    want = questions if questions is not None else {"Q01", "Q02", "Q09"}
    doms = domains if domains is not None else {"能源AI"}
    matched = []
    for s in signals:
        qs = set(s.questions or [])
        if s.domain in doms:
            matched.append(s)
        elif s.domain == "政策合规" and "Q02" in qs and ("Q02" in want):
            matched.append(s)
        elif (want & qs) and _has_energy_signal(s):
            matched.append(s)
    return _diverse(matched, limit, per_source)


# ---------- 01 简报 / 02 焦点 ----------
# gw-ai-pulse（2026-11-16）：这两块原本只由日报 skill 写（homepage_feed.py），
# 一天一次且要等论坛发布，所以全天停在早上 09:28 的内容上。采集器现在每轮也写，
# 沿用日报的取舍语义，只把数据源从「V5 精编卡片」换成「信号池」。

BRIEF_MAX = 3
BRIEF_LIMIT = 160      # 与日报 homepage_feed.LIMITS["brief"] 一致


def _strip_date_suffix(title: str) -> str:
    """去掉标题尾部的「|11.16」「· 11月16日」这类日期，简报里不需要。"""
    return re.sub(r"\s*[|·]\s*\d{1,2}[.\-月]\d{1,2}日?\s*$", "", title or "").strip()


def _first_sentence(text: str, limit: int = 120) -> str:
    """取第一句，最多 limit 字。

    gw-ai-pulse（2026-11-20）：ASCII 句点只有**后面跟空白或结尾**时才算句末。
    原先一律当句末，版本号被打断——生产实测 04「今日值得看」第 2 条的摘要
    只剩「Claude Code v2.」，因为原句是「Claude Code v2.1.328 发布……」。
    """
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return ""
    m = re.search(r"[。！？]|\.(?=\s|$)", t)
    end = m.end() if m else len(t)
    return t[: min(end, limit)]


def _clip(text: str, limit: int) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _brief_text(sig: Signal) -> str:
    """简报一句话：标题 + 首句。标题自带冒号时用句号连接，避免「A：B：C」。

    gw-ai-pulse（2026-11-20）：模型偶尔把标题原样复述进摘要首句，机械拼接会让首页出现
    「国家能源局发布《中国虚拟电厂发展报告（2026）》：国家能源局发布了《中国虚拟电厂发展报告（2026）》。」
    这种车轱辘话（生产实例 t/866）。所以先丢掉复述标题的开头句，再取首句；
    摘要里一句新信息都没有时，只留标题——宁可短，也不要把同一句话说两遍。
    """
    t = _strip_date_suffix(sig.title)
    first = _first_sentence(drop_restating_lead(t, sig.summary, keep_if_all=False))
    if first and is_restatement(t, first):
        first = ""
    joiner = "。" if re.search(r"[：:]", t + first[:16]) else "："
    return _clip(f"{t}{joiner}{first}" if first else t, BRIEF_LIMIT)


# 01/02 的新鲜度闸门。
# gw-ai-pulse（2026-11-17）：此前排序键是 (tier, -时间, ...)，tier 是**第一关键字**，
# 于是 relevance=3 的旧条目会永久占满 01/02——生产实测：只有 3 条 relevance=3，
# 且都是 1~3 天前的，今天新发的 4 条全是 relevance=2，tier=1，永远挤不进来。
# 「今天发生了什么」却天天显示前天的内容，区块名和专业性都对不上。
#
# 修法不是把时间提到第一关键字（那样「今日要紧」的语义会丢，一条无关的今日小新闻
# 会盖掉昨天的重大政策），而是给 tier 加**时效降级**：
#   - 当天发布的                      → 不降级
#   - 昨天的                          → 降 1 档（仍可凭相关度进榜）
#   - 前天的                          → 降 3 档，落到「过期」层，只在无新料时兜底
# 阈值实测（2026-11-17 生产数据）：当天 4 条、昨天 25 条、前天 7 条。
# 用 0 天太激进——夜里断电或周末不采集时，早上 8 点会只剩 1~2 条；
# 用 1 天又太松——昨天那 25 条会继续占满，用户看到的还是「昨天的」。折中取分级。
#
# 为什么过期层要 +3 而不是 +2：档位基数最高就到 2（relevance 3 → 0，
# 能源/政策 → 1，其余 → 2）。+2 会让「前天的 relevance=3 政策」和
# 「今天的行业速递」都落到 3，两条并列，再比日期时旧的那条仍可能靠相关度赢——
# 这跟原来的 bug 是同一个形状，只是晚了一天。+3 让过期条目落到 3 之后，
# 新料永远压住旧料；新料全无时它们照常上榜，01 不会开天窗。
FRESH_DAYS = 1          # 达到这个天数（昨天）降 1 档
STALE_DAYS = 2          # 达到这个天数（前天起）彻底沉底，仅作兜底


def _age_days(sig: Signal, now: datetime | None = None) -> float:
    """发布日期距今多少天。日期未知时按很旧处理（它本来就不该上 01/02）。"""
    day = (sig.event_date or sig.discovered_at or "")[:10]
    try:
        d = datetime.fromisoformat(day).date()
    except ValueError:
        return 99.0
    base = (now or datetime.now()).date()
    return (base - d).days


def _brief_rank(sig: Signal, now: datetime | None = None) -> tuple:
    """对齐日报的板块优先级：今日要紧 > 金风相关 > 行业速递，但加入时效降级。

    基准档位（与日报语义一致）：
      今日要紧  ← relevance 3
      金风相关  ← domain 落在能源/政策，或命中 Q02/Q05 这类金风直接相关问题
      行业速递  ← relevance 2
    时效降级：昨天的降 1 档；前天及更早的落进「过期层」（排在新料之后，
    新料全无时仍会补上，保证 01/02 不开天窗）。
    次级排序：**先可信度**（green > yellow > red），再按发布日期，最后按标题保证稳定。
    （2026-11-19 改：原来日期优先，导致单一来源的二手媒体压过官方一手发布——
     官方单方发布是 yellow，单家媒体是 red，日期一靠前媒体就永远赢。）
    时效仍在 tier 里兜着（昨天降档、前天沉底），不会退回「旧的重磅永远霸榜」的老问题。
    """
    if sig.relevance >= 3:
        tier = 0
    elif sig.domain in ("能源AI", "政策合规") or set(sig.questions) & {"Q02", "Q05"}:
        tier = 1
    else:
        tier = 2
    age = _age_days(sig, now)
    if age >= STALE_DAYS:           # 前天及更早
        tier = 5                    # 过期层：留给新料，这里只做兜底
    elif is_research(sig):
        # 2026-11-20：学术论文降到 tier=4（排在今/昨真实行业、厂商、能源新闻之后），
        # 避免论文靠 relevance=3 抢占 01「今天发生了什么」头条；新闻稀少时仍可自动补位。
        tier = 4
    elif age >= FRESH_DAYS:         # 昨天
        tier += 1
    tier = min(tier, 5)
    cred_rank = {"green": 0, "yellow": 1, "red": 2}.get(sig.cred, 3)
    return (tier, cred_rank, -_ts(sig.event_date or sig.discovered_at), sig.title)


def _src_name(sig: Signal) -> str:
    """信号的首发信源名。没有来源时归到一个空名字里。"""
    return sig.sources[0].name if sig.sources else ""


def _limit_key(sig: Signal) -> str:
    """「同源限额」的分桶键：优先按站点（host）分组，没有链接才退回信源名。

    为什么按站点而不是按源名：同一个站常被拆成好几个源名——IT之家 · AI / IT之家 · 算力
    都在 ithome.com；DSH 插件生态 / DSH 主仓库 / GitHub Trending 全在 github.com。
    只按源名限，等于给同一个站开了好几张额度：2026-11-19 实测 04「今日值得看」
    四格里三个是 github 仓库，01 三条里两条来自 ithome.com 的不同子源。
    按站点限之后，同一个站无论被拆成几个源名，一共也只占 per_source 个位置。
    """
    for src in sig.sources:
        host = (urlparse(src.url or "").hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host:
            return host
    return _src_name(sig)


def _diverse(signals: list[Signal], limit: int, per_source: int) -> list[Signal]:
    """按「同一站点最多几条」挑选（分桶见 _limit_key），保持传入顺序（调用方已排好序）。

    per_source <= 0 表示不限额（旧行为，供测试与回退使用）。
    第一轮严格执行限额；若因此凑不满 limit（典型场景：当天只有一家在发稿），
    第二轮放宽到 per_source+1 补齐——宁可多一条同源，也不让 01/02 开天窗。
    （2026-11-19：IT之家一轮 27 条、一天 3 轮，不限额时 01 的三条全是它。）
    """
    if limit <= 0:
        return []
    if not per_source:
        return list(signals[:limit])
    picked, counts, taken = [], {}, set()
    for cap in (per_source, per_source + 1):
        for s in signals:
            if len(picked) >= limit:
                return picked
            if id(s) in taken:
                continue
            name = _limit_key(s)
            if counts.get(name, 0) >= cap:
                continue
            picked.append(s)
            taken.add(id(s))
            counts[name] = counts.get(name, 0) + 1
    # 仍然不够：当天只有一两家在发稿时不设限，按顺序补满，不让 01 开天窗
    for s in signals:
        if len(picked) >= limit:
            break
        if id(s) in taken:
            continue
        picked.append(s)
        taken.add(id(s))
    return picked


def _top_picks(signals: list[Signal], limit: int, per_source: int = 0,
               now: datetime | None = None) -> list[Signal]:
    """01/02/主线 共用的取数：先按 _brief_rank 排序，再按来源限额去重。

    三者共用这一个函数，是为了保证「02 焦点 = 01 的第一条」不会被两套逻辑拆散。
    """
    return _diverse(sorted(signals, key=lambda s: _brief_rank(s, now)), limit, per_source)


def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return 0.0


# 刊头日期。旧「每日精选 skill」每天写一次 date/issue_date；日报停用后没人写，
# 这两个字段就冻在停用那天（实测首页顶部一直显示「2026年11月16日 · 星期一」，
# 而下面 37 条雷达已经是次日下午采的），看起来像整站停止更新。改由采集器每轮写。
_CN_WEEK = "一二三四五六日"


def today_label(now: datetime) -> str:
    """给首页刊头用的中文日期：2026年11月17日 · 星期二。"""
    return f"{now.year}年{now.month}月{now.day}日 · 星期{_CN_WEEK[now.weekday()]}"


def brief_signals(signals: list[Signal], limit: int = BRIEF_MAX,
                  now: datetime | None = None, per_source: int = 0) -> list[Signal]:
    """01 THE BRIEF 选中的信号本体。

    单独抽出来（2026-11-20）是为了让「04 今日值得看」能按信号去重：
    原先 brief_items 只返回渲染好的三行文字，update_feed 手里拿不到「哪几条已经上过 01」，
    于是同一篇文章既在 01 又在 04（生产实例：Gemini Tool Retrieval、自我改进 Harness）。
    """
    return _top_picks(signals, limit, per_source, now)


def brief_item(sig: Signal) -> dict:
    src = sig.sources[0] if sig.sources else None
    return {
        "text": _brief_text(sig),
        "url": src.url if src else "",
        "source": src.name if src else "",
    }


def brief_items(signals: list[Signal], limit: int = BRIEF_MAX,
                now: datetime | None = None, per_source: int = 0) -> list[dict]:
    """01 THE BRIEF：至多 3 条「今天发生了什么」。per_source 见 _diverse。"""
    return [brief_item(s) for s in brief_signals(signals, limit, now=now, per_source=per_source)]


def lead_item(signals: list[Signal], now: datetime | None = None,
              per_source: int = 0) -> dict | None:
    """02 TODAY'S LEAD：取与 01 同一条（01 的第一条），保证两块不打架。"""
    picked = _top_picks(signals, 1, per_source, now)
    if not picked:
        return None
    s = picked[0]
    return {
        "cred": CRED_CODE.get(s.cred, "r"),
        "title": _strip_date_suffix(s.title),
        "summary": _clip(s.summary, 320),
        "us": _clip(s.us, 200),
        "sources": [{"label": src.name, "url": src.url} for src in s.sources[:3]],
        "url": "",      # update_feed 里填对应话题链接
        "topic_id": s.topic_id,
    }


# ---------- 页头「今日主线」 ----------
# gw-ai-pulse（2026-11-16）：这字段原先由日报编辑手写一句概括（≤160 字）。
# 日报去掉后改成自动提炼：取相关度最高的两条，各压成一句，用分号连接。
# 只取两条是因为 160 字放不下第三条，三条会挤成流水账。

MAINLINE_LIMIT = 160


def mainline_text(signals: list[Signal], now: datetime | None = None,
                   per_source: int = 0) -> str:
    """今日主线：一句话概括当天最重要的 1~2 件事（与 01 同源口径）。"""
    picked = _top_picks(signals, 2, per_source, now)
    if not picked:
        return ""
    parts = []
    for s in picked:
        t = _strip_date_suffix(s.title)
        # 标题过长时截断，给第二句留空间
        parts.append(t if len(t) <= 60 else t[:59].rstrip() + "…")
    text = "；".join(parts)
    if len(text) > MAINLINE_LIMIT:
        text = text[: MAINLINE_LIMIT - 1].rstrip() + "…"
    return text


# ---------- 04「今日值得看」 ----------
# 原先是日报从「工具与做法」板块挑 4 条可落地的（GitHub 项目、实操方法）。
# 采集器用 domain + topics 还原同一语义。
#
# 2026-11-20：条数 4 → 5。04 与 01 去重之后，可上手候选池仍稳定有 20+ 条
# （实测池 88 条信号、其中 27 条 _is_tool_like），四格装不下当天值得看的量；
# 主题组件的 .ap-picks 是垂直列表（CSS 没有固定列数），第 5 条不需要改前端。
PICKS_MAX = 5
PICK_LIMIT = 120


def _is_tool_like(sig: Signal) -> bool:
    """是否属于「能上手用」的条目。

    判定顺序（2026-11-23 修正误杀）：
      1. 政策/公告/诉讼/财报/排名/纯讨论（_HARD_NOT_PICK）直接排除
      2. 领域是「工具做法」直接通过——不再被「曝光」「警告」等常规新闻动词误杀
         （2026-11-23 实测：DeepSeek V1 作者开源 Agent 因含「曝光」、Anthropic Claude Code
         更新因含「Hook 安全警告」被误杀，导致近 2 天工具候选池归零、整块退回 3 天前老数据）
      3. 其余领域排除传闻/非实操动词（_RUMOR_NOT_PICK）后，要求标题含明确工具名词
    """
    title = sig.title or ""
    if _HARD_NOT_PICK.search(title):
        return False
    if sig.domain == "工具做法":
        return True
    if _RUMOR_NOT_PICK.search(title):
        return False
    return bool(_TOOL_NOUN.search(title))


# 工具名词：能落地使用的东西。不含「发布/上线」这类动词——它们在任何新闻里都有。
# 只有「明确可上手」的名词才算。「指南/方案/评估/报告」这类既可能是教程也可能是
# 分析文章（实测「实用指南评估组织AI代码蔓延暴露风险」就是分析文，不是工具），故不收。
_TOOL_NOUN = re.compile(
    r"开源|框架|技能包|SDK|CLI|插件|工具|工作流|模板|基准|API|接口|驱动|内核",
    re.I,
)
# 硬排除：政策公告/诉讼/行情/人事/排名/纯讨论，即使带工具词也不是可上手条目
_HARD_NOT_PICK = re.compile(
    r"热议|讨论|起诉|诉讼"
    r"|网信办|国家能源局|发改委|工信部|公示|备案|通知|公告|征求意见|法案|条例|试点|清单"
    r"|周榜|排名|登顶|市值|股价|财报|营收|辞职|离职|招聘"
)
# 传闻/非实操类：仅用于非「工具做法」领域的二次过滤，避免误杀工具更新里的「安全警告」「项目曝光」
_RUMOR_NOT_PICK = re.compile(
    r"传闻|据悉|或将|可能|曝光|称|警告|披露|宣布将|宣言|联合声明"
)


def _within_hours(sig: Signal, now: datetime, max_age_hours: int) -> bool:
    """信号发布时间是否落在最近 max_age_hours 小时内。"""
    base = now if now.tzinfo is not None else now.replace(tzinfo=CST)
    cutoff = base - timedelta(hours=max_age_hours)
    pub = (sig.published_at or "").strip()
    if len(pub) > 10:
        try:
            dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CST)
            return dt >= cutoff
        except ValueError:
            pass
    day = (sig.event_date or pub or sig.discovered_at or "")[:10]
    if not day:
        return False
    return day >= cutoff.date().isoformat()


def pick_items(signals: list[Signal], limit: int = PICKS_MAX,
               now: datetime | None = None, per_source: int = 0,
               max_age_hours: int = 0) -> list[dict]:
    """今日值得看：挑可落地的工具与做法。没有合适的就返回空列表（首页少一块，不留占位）。

    2026-11-23：新增 max_age_hours 硬性新鲜度闸门（>0 且传入 now 时生效），
    禁止用 3 天前的老数据硬填「今日值得看」；并在输出中附带 when（YYYY-MM-DD）供前端展示。
    """
    pool = [s for s in signals if _is_tool_like(s)]
    if max_age_hours > 0 and now is not None:
        pool = [s for s in pool if _within_hours(s, now, max_age_hours)]
    if not pool:
        return []
    # 可信度优先，其次发布日期新；per_source 防止一家的插件仓库刷满四格
    picked = _top_picks(pool, limit, per_source, now)
    out = []
    for s in picked:
        src = s.sources[0] if s.sources else None
        if not src:
            continue
        out.append({
            "kind": "做法",
            "title": _clip(_strip_date_suffix(s.title), PICK_LIMIT),
            "summary": _clip(_first_sentence(s.summary, 100), 100),
            "url": src.url,
            "when": (s.published_at or s.event_date or s.discovered_at or "")[:10],
        })
    return out


def picks_excluding(signals: list[Signal], *, exclude: list[Signal],
                    limit: int = PICKS_MAX, now: datetime | None = None,
                    per_source: int = 0, max_age_hours: int = 0) -> list[dict]:
    """04「今日值得看」，但排掉已经上过 01 简报 / 02 焦点的条目。

    gw-ai-pulse（2026-11-20）：01 与 04 原先各自从整个信号池里挑，谁也不看谁，
    同一篇文章就会在一屏里出现两次。生产实例（632 底座帖）：
        01[2] Gemini API Tool Retrieval      = 04[1]
        01[3] 谷歌自我改进 Harness 架构        = 04[2]
    四条推荐里两条是重复的，读者会以为系统重了。
    按信号本身 + 任一来源 URL 双重排除：不同事件键指向同一篇文章时也能拦住。
    """
    excluded_ids = {id(s) for s in exclude}
    excluded_urls = {src.url for s in exclude for src in getattr(s, "sources", [])}
    pool = [s for s in signals
            if id(s) not in excluded_ids
            and not any(src.url in excluded_urls for src in getattr(s, "sources", []))]
    return pick_items(pool, limit, now=now, per_source=per_source, max_age_hours=max_age_hours)
