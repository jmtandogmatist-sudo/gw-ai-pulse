#!/usr/bin/env python3
"""从最终日报生成飞书群动态摘要，并维护群推送幂等记录。

本脚本不直接调用飞书 API。它生成消息文件和机器可读 payload，供具备飞书
群消息工具的自动化执行器发送；发送成功或失败后再调用本脚本记录状态。
"""

import argparse
import itertools
import json
import os
import re
from datetime import datetime
from pathlib import Path

from common import atomic_write_json
import layout_v5  # V5 三层版式适配
import title_spec  # V5.3.2 标题规范单一真源

# 行尾 `\s*$` 容错：模板规则要求大事件标题行尾保留两空格做 Markdown 硬换行，
# 故正则必须容忍行尾空白（与 self_check.py 的 SECTION_TITLE_PATTERN 保持同一容错口径）。
# V4.2（2026-10-26）：`###` 标题不再强制行动标签，故新增第 4 组匹配「无标签纯标题」；
# 第 3 组保留以兼容历史期次的 `### 【行动标签】标题`。
NEWS_TITLE_PATTERN = re.compile(r"(?m)^(?:###\s+\d+\.\s*(.+)|🔹\s+\*\*(.+)\*\*|###\s+【[^】]+】(.+)|###\s+(?!\d+\.\s)(.+?))\s*$")

# 差异化群推送（2026-09-11 定稿）：三群各发『一主讯息＋两快讯＋一问题＋阅读全文＋统一签名档』
# 群画像来自 references/feishu-group-profiles.json，按群生成差异化文案。完整日报只走阅读全文链接。
GROUP_PROFILES_ARG = "profiles"
SECTION_TITLE_PATTERN = re.compile(r"(?m)^###\s+(?!\d+\.\s)(.+)$")
ANGLE_TAG_PATTERN = re.compile(r"^【[^】]+】\s*")
# V4.2（2026-10-26）：正文行动标签【需行动】【可借鉴】【了解】降为**可选**编辑标记，可能完全不存在；
# 一旦存在仍须从推送文案剥除。同时防止任何非群视角的前导 `【…】` 标签泄漏进群推送文案。
# 群视角标签【能力复用】/【数据价值】/【部署门槛】此处保留，交由 send 前的跨群标签泄漏检查兜底。
ACTION_TAG_PATTERN = re.compile(r"^(?!【(?:能力复用|数据价值|部署门槛)】)【[^】]+】\s*")
META_LINE_PREFIXES = ("本期未检索到", "今日暂无")

# 累积出刊标识（V3.9.0 改口径）：权威来源从「日报正文」迁移到**检索清单 metadata**。
# 背景：用户要求日报正文删除「全部资讯/资讯清单」模块，正文不再承载累积行；
#   meta 来源优先级：①检索清单 publication_metadata（首选）②独立 metadata 文件 ③（兼容）正文遗留行。
# 正文遗留行仅作历史兼容读取，新一期不再写入。
ACCUMULATION_LINE_PATTERN = re.compile(
    r"📅\s*\*{0,2}累积出刊\*{0,2}\s*[：:]\s*本期为\s*\*{0,2}(?P<span>[0-9]{1,2}/[0-9]{1,2}\s*[–~-]\s*[0-9]{1,2}/[0-9]{1,2})\*{0,2}\s*[（(]\s*共\s*\*{0,2}(?P<count>\d+)\*{0,2}\s*条\s*[）)]"
)


def extract_paragraph(block, limit=70):
    """提取条目正文中的第一段可读文本，跳过标题、来源行、引用行和元信息行。

    limit 默认 70（用于 deep_topic 这类需简短概括的场景）；
    大事件/快讯摘要提取时应传更宽 limit（如 130），避免把含关键数据的解读截断。
    """
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ">", "**来源", "来源", "---", "🔹")):
            continue
        if stripped.startswith(META_LINE_PREFIXES):
            continue
        if stripped.startswith("一句话解读："):
            stripped = stripped.removeprefix("一句话解读：").strip()
        return truncate(stripped, limit)
    return ""


def section_text(raw, heading, next_heading=None):
    start = raw.find(heading)
    if start < 0:
        return ""
    end = raw.find(next_heading, start + len(heading)) if next_heading else len(raw)
    return raw[start:end if end >= 0 else len(raw)]


def clean_title(title):
    title = ACTION_TAG_PATTERN.sub("", title.strip())
    return re.sub(r"\s*[（(][^）)]*\d{1,2}月\d{1,2}日[^）)]*[）)]\s*$", "", title).strip()


def truncate(text, limit):
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    # 句子边界截断：尽量在「。；！？，：」等边界处截断，避免机械断词破坏事实数据。
    # 优先在 limit 之前最近的句子结束符（。！？；）处截断；无则回退到最近逗号/冒号；再无则硬截。
    hard = text[:limit - 1].rstrip()
    cut = -1
    for sep in ("。", "！", "？", "；"):
        idx = hard.rfind(sep)
        if idx > 10:
            cut = max(cut, idx + 1)
    if cut < 0:
        for sep in ("，", "：", "、", " "):
            idx = hard.rfind(sep)
            if idx > 10:
                cut = max(cut, idx + 1)
    return (hard[:cut] if cut > 0 else hard) + "…"


def extract_section_cards(block):
    """提取一个二级板块下的三级标题，作为快讯候选。"""
    matches = list(SECTION_TITLE_PATTERN.finditer(block))
    cards = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(block)
        title = clean_title(match.group(1))
        if not title or title.startswith("📲"):
            continue
        summary = extract_paragraph(block[match.end():end], limit=130)
        cards.append((title, summary))
    return cards


DIGEST_BULLET_PATTERN = re.compile(r"(?m)^-\s+(?:[🟢🟡🔴]\s*)?\*\*(.+?)[：:]\*\*\s*(.+)$")


def extract_digest_bullets(block):
    """V5.1.0：行业速递是列表项（`- 🟢 **标题：** 事实`），原 extract_section_cards 只认 `###`，
    导致速递从未进入飞书头条候选池。此处把列表项也作为候选（标题去掉日期后缀）。"""
    out = []
    for m in DIGEST_BULLET_PATTERN.finditer(block or ""):
        title = clean_title(re.sub(r"[（(]\d{1,2}月\d{1,2}日[^）)]*[）)]\s*$", "", m.group(1)).strip())
        if title:
            out.append((title, truncate(m.group(2).strip(), 130)))
    return out


def append_unique_candidate(candidates, title, summary=""):
    title = clean_title(title)
    if not title:
        return
    if any(existing == title for existing, _ in candidates):
        return
    candidates.append((title, summary or ""))


def _normalize_span(span):
    """把 '9/19-9/20' / '9/19~9/20' 归一为半角连接符 '–'，去空白。"""
    span = re.sub(r"\s+", "", str(span or ""))
    return re.sub(r"[~-]", "–", span)


def parse_accumulation_from_metadata(metadata):
    """从检索清单 publication_metadata（或独立 metadata）解析累积出刊标识。

    期望结构（宽松读取，字段缺失按非累积处理）：
        {"is_accumulated": true, "span": "9/19–9/20", "count": 30}
    也接受嵌套写法 {"accumulation": {...}} 或 {"publication": {...}}。

    返回与 parse_accumulation 同构的 dict，供 build_message 直接消费。
    """
    data = metadata or {}
    if not isinstance(data, dict):
        return {"is_accumulated": False, "span": "", "count": None}
    for key in ("publication_metadata", "accumulation", "publication"):
        nested = data.get(key)
        if isinstance(nested, dict):
            data = {**data, **nested}
    is_acc = bool(data.get("is_accumulated") or data.get("accumulated"))
    if not is_acc:
        return {"is_accumulated": False, "span": "", "count": None}
    span = _normalize_span(data.get("span") or data.get("accumulation_span") or "")
    raw_count = data.get("count", data.get("accumulation_count"))
    try:
        count = int(raw_count) if raw_count is not None else None
    except (TypeError, ValueError):
        count = None
    return {"is_accumulated": True, "span": span, "count": count}


def load_publication_metadata(search_log_path):
    """读取检索清单的 publication_metadata（首选来源）；缺失/不可读返回 None。"""
    if not search_log_path or not os.path.exists(search_log_path):
        return None
    try:
        data = json.loads(Path(search_log_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    meta = data.get("publication_metadata")
    if isinstance(meta, dict):
        return meta
    # 顶层直接带累积字段的宽松兼容
    if "is_accumulated" in data or "accumulated" in data:
        return data
    return None


def parse_accumulation(raw):
    """解析「累积出刊」标识（V3.9.0：正则兼容读取，权威来源已迁至 metadata）。

    历史兼容：仍能从正文遗留的累积行解析出跨度与条数（老期次回填 / 补发场景）。
    但新一期正文**不再**包含该行——累积信息权威来源为检索清单 `publication_metadata`，
    由 `resolve_accumulation()` 统一按「metadata 优先、正文兜底」取值。

    返回 dict：{"is_accumulated": bool, "span": str, "count": int|None}
    """
    match = ACCUMULATION_LINE_PATTERN.search(raw or "")
    if not match:
        return {"is_accumulated": False, "span": "", "count": None}
    span = _normalize_span(match.group("span"))
    try:
        count = int(match.group("count"))
    except (TypeError, ValueError):
        count = None
    return {"is_accumulated": True, "span": span, "count": count}


def resolve_accumulation(raw, search_log_path=None, metadata=None):
    """按优先级解析累积信息：metadata 参数 > 检索清单 publication_metadata > 正文兜底。"""
    if isinstance(metadata, dict) and (
        metadata.get("is_accumulated") or metadata.get("accumulated")
    ):
        return parse_accumulation_from_metadata(metadata)
    from_log = load_publication_metadata(search_log_path)
    if from_log is not None:
        parsed = parse_accumulation_from_metadata(from_log)
        if parsed["is_accumulated"]:
            return parsed
    # 正文遗留行仅作历史兼容兜底
    return parse_accumulation(raw)


def extract_push_content(raw):
    # V5.3.2：标题解析统一走 title_spec（新格式含钩子，旧格式兼容）
    report_title = title_spec.extract_h1(raw)
    if not report_title:
        raise ValueError("无法识别日报标题")
    daily = section_text(raw, "## 今日要紧", "## 金风相关")
    matches = list(NEWS_TITLE_PATTERN.finditer(daily))
    highlights = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(daily)
        title = (match.group(1) or match.group(2) or match.group(3) or match.group(4) or "").strip()
        summary = extract_paragraph(daily[match.end():end], limit=130)
        if not summary:
            raise ValueError(f"《{title}》无法提取摘要段落")
        highlights.append((clean_title(title), summary))
    if not highlights:
        raise ValueError("今日要紧为空，无法生成动态摘要")
    # V5.0：模板允许今日要紧 2 条，而三群各需一条不同头条；不足 3 条时在下方用
    # 金风相关 → 工具与做法 → 行业速递 依次补足（旧实现直接报错，2 条的合规期次无法推送）。
    core_highlight_count = len(highlights)

    flash_candidates = list(highlights)

    energy = section_text(raw, "## 金风相关", "## 工具与做法")
    card = SECTION_TITLE_PATTERN.search(energy)
    if card:
        desc = extract_paragraph(energy[card.end():], limit=130)
        card_title = clean_title(card.group(1))
        energy_summary = f"{card_title}：{desc}" if desc else card_title
        append_unique_candidate(flash_candidates, card_title, desc)
    elif "今日暂无经双源核验的金风相关重要消息。" in energy:
        energy_summary = "今日暂无经双源核验的金风相关重要消息。"
    else:
        raise ValueError("金风相关板块既无新闻/知识卡，也缺少规定的无新闻说明")

    builder = section_text(raw, "## 工具与做法", "## 行业速递")
    practice = section_text(raw, "## 行业速递", "## 社区今日")
    for title, summary in extract_section_cards(builder) + extract_section_cards(practice) + extract_digest_bullets(practice):
        append_unique_candidate(flash_candidates, title, summary)
    # V5.1.0：头条候选池扩到 6 条（原仅补足到 3 条）。今天只有 2 条要紧 + 1 条金风，三群头条分配
    # 因候选太少而被差异化门禁拦下；门禁本身（命中画像且互不重复，否则 fail closed）不变。
    for title, summary in flash_candidates[core_highlight_count:]:
        if len(highlights) >= HEADLINE_POOL_SIZE:
            break
        append_unique_candidate(highlights, title, summary)
    if len(highlights) < 3:
        raise ValueError("精编条目不足3条（今日要紧 + 补位），无法为三群生成不同头条")

    forward_headings = ["## 后续关注", "## 明日关注", "## 下周关注"]
    forward_positions = [raw.find(heading) for heading in forward_headings]
    forward_positions = [position for position in forward_positions if position >= 0]
    deep_end = min(forward_positions) if forward_positions else len(raw)
    deep_start = raw.find("## 深度聚焦")
    if deep_start < 0:
        # V4 版式无独立深度聚焦板块，回退取「今日要紧」首条作为深度主题
        deep_start = raw.find("## 今日要紧")
        deep_end = raw.find("## 金风相关")
        if deep_end < 0:
            deep_end = len(raw)
    deep = raw[deep_start:deep_end] if deep_start >= 0 else ""
    deep_heading = re.search(r"^###\s+(.+)$", deep, re.M)
    if deep_heading:
        deep_topic = deep_heading.group(1).strip()
    else:
        paragraph = next((line.strip() for line in deep.splitlines() if line.strip() and not line.startswith("##") and not line.startswith("来源：")), "")
        deep_topic = truncate(paragraph, 70)
    deep_topic = re.sub(r"^【[^】]+】\s*", "", deep_topic)
    if not deep_topic:
        raise ValueError("无法提取深度聚焦主题")

    return report_title, highlights, flash_candidates, energy_summary, deep_topic


HEADLINE_POOL_SIZE = 6  # 头条分配候选池上限（6P3=120 种排列，开销可忽略）

# 固定签名档（每期不变，形成视觉记忆）
# 2026-11-03 用户指定：飞书推送里的日报链接一律固定为站点 /latest，
# 不再使用当日 topic URL（每期变化、二开站点偶发跳转异常），一次固定长期有效。
DAILY_URL = "https://aicopilot.goldwind.com.cn:3211/latest"
ARCHIVE_URL = DAILY_URL
SIGNATURE_BLOCK = (
    "————————————————\n"
    "**出品｜集团数字化中心**\n"
    "**策划整理｜AI应用研究院**\n"
    "我们跟踪 AI 前沿、沉淀金风业务实践、帮业务部门把 AI 用起来\n"
    "欢迎分享发布科普内容或者你的心得与技巧到 AI 社区，我们一起交流、探讨！\n"
    f"📚 [往期合集]({ARCHIVE_URL})"
)


def keyword_score(title, keywords, summary=""):
    """按标题优先、摘要补充计算画像命中分；标题命中权重为2。"""
    title_text = (title or "").lower()
    summary_text = (summary or "").lower()
    return sum(
        2 if keyword.lower() in title_text else 1 if keyword.lower() in summary_text else 0
        for keyword in (keywords or [])
    )


def rewrite_summary_for_profile(summary, profile):
    """移除日报通用判断标签，并按群画像重写为唯一价值视角。

    V3.8.7 修正：原实现先拼「【标签】正文。群专属落点」再整体 truncate(130)，
    当正文偏长时会把**群专属价值落点整段截掉**（2026-09-23 KNS 群实测：落点全丢、
    末尾只剩一个悬空「…」，与 A/C 群结构不一致，差异化设计失效）。
    现改为**先为「【标签】」前缀与「群专属落点」预留预算**，只用剩余额度截正文，
    保证三群结构一致（标签 + 事实 + 专属落点），且正文按句子边界截断、关键数据不丢。

    V3.9.1（2026-09-24 用户要求）：`angle_suffix`（「对X更值得关注的是：…」价值落点）
    改为**可选**，群画像未配置时不追加、不再阻断；三群差异化由 `angle_label` 前缀与
    唯一头条承担。同时修掉正文以「…」结尾时又补句号的「…。」双标点。
    """
    label = (profile or {}).get("angle_label", "").strip()
    suffix = (profile or {}).get("angle_suffix", "").strip()
    if not label:
        raise ValueError("群画像缺少 angle_label，无法生成差异化摘要")
    prefix = f"【{label}】"
    factual = ANGLE_TAG_PATTERN.sub("", (summary or "").strip()).rstrip("。")
    # 正文可用额度 = 130 - 前缀 - 落点（落点可为空；含正文与落点之间的连接符）
    budget = 130 - len(prefix) - (len(suffix) + 1 if suffix else 0)
    if budget < 20:
        budget = 20
    truncated = len(factual) > budget
    if truncated:
        factual = truncate(factual, budget)
    if not factual:
        return f"{prefix}{suffix}"
    # 正文以「…」结尾时不再补句号，避免「…。」双标点
    ends_with_ellipsis = factual.endswith("…")
    if not suffix:
        return f"{prefix}{factual}{'' if ends_with_ellipsis else '。'}"
    if truncated or ends_with_ellipsis:
        return f"{prefix}{factual}{suffix}"
    return f"{prefix}{factual}。{suffix}"


def select_headline(highlights, keywords, used, require_match=False):
    """从 highlights 里为本群选头条。

    优先级：本群画像命中分最高且未被占用 → 全局最早的未被占用大事。
    require_match=true 时若无画像命中，直接阻断，不再按日报顺序硬凑无关头条。
    返回 (selected_index, headline_title)。用于跨群去重的第一遍分配。
    """
    used = set(used)
    scored = [
        (keyword_score(title, keywords, summary), index, title)
        for index, (title, summary) in enumerate(highlights)
        if title not in used
    ]
    matched = [item for item in scored if item[0] > 0]
    if matched:
        _, idx, title = sorted(matched, key=lambda item: (-item[0], item[1]))[0]
        return idx, title
    if require_match:
        raise ValueError("没有未占用且命中本群画像关键词的头条，差异化门禁阻断")
    idx = next((i for i, (title, _) in enumerate(highlights) if title not in used), 0)
    return idx, highlights[idx][0]


def assign_headlines(highlights, group_profiles):
    """为全部群做全局最优的一对一头条分配，避免群顺序贪心抢占唯一匹配项。"""
    group_count = len(group_profiles)
    if len(highlights) < group_count:
        raise ValueError("差异化门禁失败：今日大事件少于启用群数量，无法分配唯一头条")
    best = None
    for indices in itertools.permutations(range(len(highlights)), group_count):
        scores = []
        valid = True
        for profile, index in zip(group_profiles, indices):
            title, summary = highlights[index]
            score = keyword_score(title, profile.get("headline_keywords"), summary)
            if profile.get("require_headline_match", True) and score <= 0:
                valid = False
                break
            scores.append(score)
        if not valid:
            continue
        candidate = (sum(scores), tuple(scores), tuple(-index for index in indices), indices)
        if best is None or candidate[:-1] > best[:-1]:
            best = candidate
    if best is None:
        raise ValueError("没有能让全部启用群同时命中画像且头条互不重复的分配方案，差异化门禁阻断")
    return list(best[-1])


def select_flash_titles(flash_candidates, keywords, blocked_titles, used_flash_titles, limit=2):
    """按群画像选择快讯，并用全局 used_flash_titles 做跨群去重。"""
    blocked = set(blocked_titles or ())
    used = set(used_flash_titles or ())
    eligible = [(t, s) for t, s in flash_candidates if t not in blocked and t not in used]

    selected = []
    # 第一优先：命中本群关键词的候选，按命中数降序、原始顺序稳定排序。
    scored = [(keyword_score(t, keywords), idx, t) for idx, (t, _) in enumerate(eligible)]
    for score, _, title in sorted(scored, key=lambda item: (-item[0], item[1])):
        if len(selected) >= limit:
            break
        if score > 0 and title not in selected:
            selected.append(title)

    # 第二优先：仍未满时，按日报顺序补齐未占用候选。
    for title, _ in eligible:
        if len(selected) >= limit:
            break
        if title not in selected:
            selected.append(title)

    # 候选不足时宁可少发，不跨群重复。
    return selected


def build_message(report_title, highlights, flash_candidates, energy_summary, deep_topic, article_url, group_profile=None, used_headlines=(), headline_index=None, flash_titles=None, accumulation=None):
    """生成本条日报告的飞书群推送文案。

    2026-09-11 差异化版（用户定稿）：
    - 结构固定为『一主讯息＋两快讯＋一问题＋阅读全文＋统一签名档』
    - 群消息只点出与群相关的价值点，完整日报交给阅读全文链接，不把整份日报塞进群
    - 每个群按 group_profile 差异化：主讯息头条（报道选题）＋互动问题＋落点角度
    - 完整版签名档来自 profiles 配置（出品方＋研究院＋投稿＋合集＋互动）

    2026-09-21 累积出刊标注（V3.8.3，用户选 A）：
    - 非工作日不发布、候选逐日累积，到工作日统一出刊（30 为上限）。
    - 累积出刊时，标题行追加「·累积出刊」，并在标题下插入一行累积期说明，
      避免长假后首期把多日累积说成"今日/昨日"，口径失实。
    - 累积期来自日报正文解析（唯一权威），非累积时为 None，保持原样。

    跨群去重：
    - 头条由 prepare 第一遍 select_headline 统一分配，这里通过 headline_index 显式传入，
      本函数不再重新选头条（避免覆盖分配结果）。
    - 快讯由 prepare 基于群画像和全局占用池分配；候选扩展至今日大事件、能源、Builder、实践板块，
      保证不同群快讯尽量不重复，候选不足时宁可少发，不强行重复。

    格式规范沿用：标题带群专属 emoji、发送必须用 lark-cli --markdown（post 富文本）。
    """
    profile = group_profile or {}
    emoji = profile.get("emoji", "📡")
    question = profile.get("question", "💬 有想了解的 AI 话题欢迎留言")
    sig = profile.get("signature") or SIGNATURE_BLOCK
    accumulation = accumulation or {}
    is_accumulated = bool(accumulation.get("is_accumulated"))
    acc_span = (accumulation.get("span") or "").strip()
    acc_count = accumulation.get("count")

    # V5.3.2：日期标签必须锚定品牌后缀取，不能对整串做 \d+\.\d+ 搜索——
    # 新格式钩子里常有价格/版本号（「0.28 元」「5.2」），宽松匹配会取错日期。
    date_tag = title_spec.date_tag(report_title)
    if not date_tag:
        raise ValueError("无法从日报标题解析日期标签")
    if is_accumulated:
        date_tag = f"{date_tag}·累积出刊"

    used = set(used_headlines or ())
    # 头条：优先用 prepare 已分配的 headline_index；未给定才自行选择（独立调用兜底）
    if headline_index is None:
        selected_index, _ = select_headline(
            highlights,
            profile.get("headline_keywords"),
            used,
            require_match=bool(profile.get("require_headline_match")),
        )
    else:
        selected_index = headline_index
    main_title, source_summary = highlights[selected_index]
    main_summary = rewrite_summary_for_profile(source_summary, profile)
    # 快讯：由 prepare 按群画像和全局占用池分配；独立调用时按本群画像兜底选择。
    if flash_titles is None:
        flash_titles = select_flash_titles(
            flash_candidates,
            profile.get("headline_keywords"),
            blocked_titles=used | {main_title},
            used_flash_titles=set(),
        )
    flash_lines = "\n".join(f"• {t}" for t in flash_titles) if flash_titles else ""

    lines = [f"【AI雷达｜{date_tag}】{emoji}", ""]
    if is_accumulated:
        count_part = f"共 {acc_count} 条" if acc_count else "多条精选"
        span_part = f"{acc_span} " if acc_span else ""
        lines.append(f"📅 本期为 {span_part}累积出刊（{count_part}），非单日快报")
        lines.append("")
    lines.append(f"**今日最值关注：{main_title}**")
    lines.append(main_summary)
    lines.append("")
    if flash_lines:
        lines.append("快讯：")
        for t in flash_titles:
            lines.append(f"• {t}")
        lines.append("")
    lines.append(f"💬 {question}")
    lines.append("")
    # 阅读全文固定指向站点 /latest（用户 2026-11-03 指定）；article_url 仍写入 payload 供审计与去重键。
    lines.append(f"阅读全文：{DAILY_URL}")
    lines.append("")
    lines.append(sig)
    return "\n".join(lines)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    atomic_write_json(path, data)


def prepare(args):
    raw = Path(args.md_path).read_text(encoding="utf-8")
    if layout_v5.is_v5(raw):
        # V5：群推送只用精编层，跟踪/雷达只留在论坛帖
        raw = layout_v5.to_v4(raw)
    report_title, highlights, flash_candidates, energy_summary, deep_topic = extract_push_content(raw)
    # V3.9.0：累积信息权威来源改为检索清单 publication_metadata（正文遗留行仅兜底）
    accumulation = resolve_accumulation(
        raw,
        search_log_path=getattr(args, "search_log", None),
    )
    # 占位链接门禁：非 --allow-placeholder 时，禁止用占位符生成正式 payload。
    article_url = str(getattr(args, "article_url", "") or "")
    topic_id = str(getattr(args, "topic_id", "") or "")
    allow_placeholder = bool(getattr(args, "allow_placeholder", False))
    is_placeholder = (
        not article_url
        or not article_url.lower().startswith(("http://", "https://"))
        or "PENDING" in article_url
        or "待回填" in article_url
        or topic_id in ("", "PENDING", "0")
    )
    if is_placeholder and not allow_placeholder:
        raise ValueError(
            "article_url/topic_id 为占位符或非法，禁止生成正式推送 payload；"
            "论坛回读后请用真实链接重新 prepare（草稿请加 --allow-placeholder）"
        )
    groups_data = load_json(args.groups, {"groups": []})
    groups = [g for g in groups_data.get("groups", []) if g.get("enabled")]
    if not groups:
        raise ValueError("没有已启用的飞书群配置")

    state = load_json(args.state, {"records": []})
    records = state.setdefault("records", [])
    profiles_data = load_json(args.profiles, {}) if args.profiles else {}
    profiles_map = profiles_data.get("groups", {}) if isinstance(profiles_data, dict) else {}
    default_sig = (profiles_data.get("signature") or {}).get("block") if isinstance(profiles_data, dict) else None
    # V5.2.3：链接单一真源的**两处**（本脚本 DAILY_URL + 画像 signature.block）必须一致。
    # 只改一处会在消息里出现两个不同链接，且没有任何门禁能发现——改为生成前 fail closed。
    if default_sig and DAILY_URL not in default_sig:
        raise ValueError(
            "签名档链接与 DAILY_URL 不一致：references/feishu-group-profiles.json 的 "
            f"signature.block 必须包含 {DAILY_URL}（改链接须两处同步）"
        )

    # 两遍生成：
    # 第一遍为每个群分配互不重复的头条（select_headline + 全局占用池）。
    # 第二遍按群画像从扩展候选池分配快讯，并维护全局快讯占用池；候选不足时宁可少发，
    # 不强行跨群复用同一快讯。
    profiles_by_group = {}
    for group in groups:
        profile = dict(profiles_map.get(group["chat_id"], {}))
        if default_sig:
            profile.setdefault("signature", default_sig)
        profiles_by_group[group["chat_id"]] = profile

    all_headlines = []  # 全体群分配的头条，作为第二遍 used_headlines
    headline_indices = []  # 与 groups 同序，记录每个群分配的头条 index
    angle_labels = [profiles_by_group[group["chat_id"]].get("angle_label", "").strip() for group in groups]
    if any(not label for label in angle_labels):
        raise ValueError("差异化门禁失败：每个启用群都必须配置 angle_label")
    if len(angle_labels) != len(set(angle_labels)):
        raise ValueError(f"差异化门禁失败：启用群 angle_label 必须唯一，当前为 {angle_labels}")

    ordered_profiles = [profiles_by_group[group["chat_id"]] for group in groups]
    try:
        headline_indices = assign_headlines(highlights, ordered_profiles)
    except ValueError:
        # 差异化无解（某群大事件头条全 miss 画像）时，把快讯候选（能源/Builder/实践板块）
        # 并入头条候选池重试，让无命中群可从契合业务板块取头条，而非硬塞无关大事件。
        for fc_title, fc_summary in flash_candidates:
            append_unique_candidate(highlights, fc_title, fc_summary)
        headline_indices = assign_headlines(highlights, ordered_profiles)
    all_headlines = [highlights[index][0] for index in headline_indices]

    payloads = []
    used_flash_titles = []
    for i, group in enumerate(groups):
        key = f"{args.topic_id}:{group['chat_id']}"
        sent = any(record.get("key") == key and record.get("status") == "SENT" for record in records)
        profile = profiles_by_group[group["chat_id"]]
        main_title, source_summary = highlights[headline_indices[i]]
        main_summary = rewrite_summary_for_profile(source_summary, profile)
        headline_match_score = keyword_score(main_title, profile.get("headline_keywords"), source_summary)
        group_flash_titles = select_flash_titles(
            flash_candidates,
            profile.get("headline_keywords"),
            blocked_titles=set(all_headlines) | {main_title},
            used_flash_titles=used_flash_titles,
        )
        used_flash_titles.extend(group_flash_titles)
        message = build_message(report_title, highlights, flash_candidates, energy_summary, deep_topic,
                                args.article_url, profile, all_headlines,
                                headline_index=headline_indices[i],
                                flash_titles=group_flash_titles,
                                accumulation=accumulation)
        expected_tag = f"【{profile['angle_label']}】"
        if expected_tag not in message:
            raise ValueError(f"差异化门禁失败：{group['name']} 缺少专属视角标签 {expected_tag}")
        # V5.2.3：日报链接固定 /latest（用户 2026-11-03 定），文案缺它即 fail closed——
        # 防止某群 profile 自带 signature 覆盖 default_sig，把链接悄悄换回当日 topic URL。
        if DAILY_URL not in message:
            raise ValueError(f"飞书文案缺少固定日报链接 {DAILY_URL}：{group['name']}")
        leaked_tags = [label for label in angle_labels if label != profile["angle_label"] and f"【{label}】" in message]
        if leaked_tags:
            raise ValueError(f"差异化门禁失败：{group['name']} 混入其他群视角标签 {leaked_tags}")

        payloads.append({
            "key": key,
            "topic_id": str(args.topic_id),
            "issue_id": str(args.issue_id) if args.issue_id is not None else "",
            "report_date": args.report_date or "",
            "chat_id": group["chat_id"],
            "group_name": group["name"],
            "group_label": profile.get("label", ""),
            "main_title": main_title,
            "angle_label": profile["angle_label"],
            "main_summary": main_summary,
            "headline_match_score": headline_match_score,
            "mention_all": False,
            "accumulated": bool(accumulation.get("is_accumulated")),
            "accumulation_span": accumulation.get("span", ""),
            "accumulation_count": accumulation.get("count"),
            "message": message,
            "skip": sent,
            "skip_reason": "该论坛文章已推送至此群" if sent else "",
        })

    output = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "article_url": args.article_url,
        "topic_id": str(args.topic_id),
        "issue_id": str(args.issue_id) if args.issue_id is not None else "",
        "report_date": args.report_date or "",
        "accumulation": accumulation,
        "placeholder": bool(is_placeholder),
        "placeholder_url": article_url if is_placeholder else "",
        "payloads": payloads,
    }
    write_json(args.output, output)
    message_path = args.message_output or str(Path(args.output).with_suffix(".txt"))
    message_lines = []
    for payload in payloads:
        message_lines.extend([
            f"目标群：{payload['group_name']}（{payload['chat_id']}）",
            f"状态：{'跳过（已发送）' if payload['skip'] else '待发送'}",
            "",
            payload["message"],
            "",
            "---",
            "",
        ])
    Path(message_path).write_text("\n".join(message_lines), encoding="utf-8")
    print(f"飞书推送 payload：{args.output}")
    print(f"飞书推送文案：{message_path}")
    if output["placeholder"]:
        print(f"  ⚠️ 占位链接草稿：article_url={article_url}（论坛回读后须用真实链接重新 prepare）")
    if accumulation.get("is_accumulated"):
        print(f"  出刊形态：累积出刊（{accumulation.get('span') or '?'}，共 {accumulation.get('count') or '?'} 条）")
    else:
        print("  出刊形态：日常单期")
    for payload in payloads:
        label = payload.get("group_label") or payload["group_name"]
        print(f"  {label}: {'SKIP' if payload['skip'] else 'READY'}")


def record(args):
    state = load_json(args.state, {"records": []})
    records = state.setdefault("records", [])
    key = f"{args.topic_id}:{args.chat_id}"
    records[:] = [item for item in records if item.get("key") != key]
    records.append({
        "key": key,
        "topic_id": str(args.topic_id),
        "issue_id": str(args.issue_id) if args.issue_id is not None else "",
        "report_date": args.report_date or "",
        "chat_id": args.chat_id,
        "group_name": args.group_name,
        "status": args.status,
        "attempts": args.attempts,
        "message_id": args.message_id or "",
        "error": args.error or "",
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    write_json(args.state, state)
    print(f"已记录 {key}: {args.status}")


def main():
    parser = argparse.ArgumentParser(description="准备或记录 AI 雷达日报飞书群推送")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("md_path")
    prepare_parser.add_argument("article_url")
    prepare_parser.add_argument("topic_id")
    prepare_parser.add_argument("--groups", required=True)
    prepare_parser.add_argument("--profiles", help="群画像/签名配置 JSON 路径（差异化推送用）")
    prepare_parser.add_argument("--state", required=True)
    prepare_parser.add_argument("--output", required=True)
    prepare_parser.add_argument("--message-output")
    prepare_parser.add_argument("--issue-id")
    prepare_parser.add_argument("--report-date")
    prepare_parser.add_argument("--search-log",
                                help="检索清单 JSON 路径（累积信息权威来源 publication_metadata）")
    prepare_parser.add_argument("--allow-placeholder", action="store_true",
                                help="允许用占位链接生成**草稿**（论坛确认前预生成的文案）；"
                                     "正式发送前必须用真实链接重新 prepare")

    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("topic_id")
    record_parser.add_argument("chat_id")
    record_parser.add_argument("group_name")
    record_parser.add_argument("status", choices=["SENT", "FAILED"])
    record_parser.add_argument("--state", required=True)
    record_parser.add_argument("--attempts", type=int, default=1)
    record_parser.add_argument("--message-id")
    record_parser.add_argument("--error")
    record_parser.add_argument("--issue-id")
    record_parser.add_argument("--report-date")

    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args)
    else:
        record(args)


if __name__ == "__main__":
    main()
