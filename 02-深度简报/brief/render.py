"""渲染深度简报为 Discourse 杂志风长文（[wrap=ais-brief]）与首页底座帖 632.daily_brief 精简卡片。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .harvester import Lead


def _cite(source_ids: list[str], lead_by_id: dict[str, Lead],
          extra_cn: dict[str, str] | None = None) -> str:
    """把实测通过的素材编号拼成规范的 Markdown 引用：
    1. 优先前置国内直连 / 社区站内信号链接，方便公司内网读者无代理直接点击；
    2. 海外原始信源统一加注〔需外网〕，保留一手溯源。
    """
    direct_parts: list[str] = []
    overseas_parts: list[str] = []
    seen: set[str] = set()

    def _add_direct(name: str, url: str) -> None:
        u = (url or "").strip()
        if not u or u in seen:
            return
        seen.add(u)
        direct_parts.append(f"[{name}]({u})")

    if isinstance(extra_cn, dict) and extra_cn.get("url"):
        _add_direct(str(extra_cn.get("name") or "中文报道"), str(extra_cn["url"]))

    for sid in source_ids or []:
        ld = lead_by_id.get(sid)
        if not ld:
            continue
        if ld.community_url:
            _add_direct("社区信号", ld.community_url)
        if ld.cn_mirror_url:
            _add_direct(ld.cn_mirror_name or "中文报道", ld.cn_mirror_url)
        if ld.url in seen:
            continue
        seen.add(ld.url)
        if ld.needs_overseas:
            overseas_parts.append(f"[{ld.publisher}]({ld.url})〔需外网〕")
        else:
            direct_parts.append(f"[{ld.publisher}]({ld.url})")

    parts = direct_parts + overseas_parts
    if not parts:
        return ""
    return "（" + "、".join(parts) + "）"


def topic_title(report_date: date, brief: dict[str, Any]) -> str:
    """生成论坛话题标题：日期 + 主打报道 1 核心短题。"""
    feats = brief.get("features") or []
    head = str((feats[0] or {}).get("title") or "前沿 AI 与全球风能产业观察") if feats else "前沿 AI 与全球风能产业观察"
    if len(head) > 42:
        head = head[:41].rstrip("，、：: ") + "…"
    return f"每日深度简报（{report_date.isoformat()}）：{head}"


def _section_kind(title: str) -> str:
    """按简讯小节标题映射前端模块视觉色标：ai（科技蓝）/ trade（琥珀金）/ wind（风电绿）。"""
    t = title or ""
    if any(k in t for k in ("贸易", "碳", "关税", "政策", "地缘", "壁垒")):
        return "trade"
    if any(k in t for k in ("风能", "风电", "海风", "工程", "新能源")):
        return "wind"
    if any(k in t for k in ("人工智能", "AI", "前沿", "大模型", "科技")):
        return "ai"
    return "other"


def render_topic_markdown(report_date: date, brief: dict[str, Any],
                          lead_by_id: dict[str, Lead]) -> str:
    """渲染为 Discourse 帖子正文，使用 [wrap=ais-brief] 杂志排版块（无组件时也完全可读）。

    版面顺序（2026-11-20 确认）：
      1. 趋势洞察（置顶开篇，同步驱动首页 01 左栏）
      2. 主打深度报道（2 篇卡片块）
      3. 重要动态简讯（整块包裹 + 分领域色标子卡片）
      4. 延伸讨论
    """
    d_zh = f"{report_date.year}年{report_date.month}月{report_date.day}日"
    win_start = report_date - timedelta(days=2)
    win_zh = f"{win_start.month}月{win_start.day}日–{report_date.month}月{report_date.day}日"
    lines: list[str] = [
        "[wrap=ais-brief]",
        "",
        f"每日调查新闻深度简报 · **{d_zh}** · 统计窗口：{win_zh}（北京时间，含欧美昨夜今晨） · AI 前沿 × 能源贸易 × 全球风电",
        "",
    ]

    # 1. 趋势洞察置顶第一块
    insights = brief.get("insights") or []
    if insights:
        lines += [
            "[wrap=ais-brief-insights]",
            "",
            "### 趋势洞察",
            "",
        ]
        for idx, ins in enumerate(insights, 1):
            if not isinstance(ins, dict):
                continue
            h = str(ins.get("heading") or "").strip()
            b = str(ins.get("body") or "").strip()
            if h and b:
                lines.append(f"{idx}. **{h}**：{b}")
            elif b:
                lines.append(f"{idx}. {b}")
        lines += ["", "[/wrap]", ""]

    # 2. 主打深度报道
    lines += [
        "### 主打深度报道",
        "",
    ]
    for idx, feat in enumerate(brief.get("features") or [], 1):
        title = str(feat.get("title") or "").strip()
        lead = str(feat.get("lead") or "").strip()
        cite = _cite(feat.get("source_ids") or [], lead_by_id, extra_cn=feat.get("cn_mirror"))
        lines += [
            "[wrap=ais-brief-feat]",
            "",
            f"#### {idx}. {title}",
            "",
            f"{lead}{cite}",
            "",
        ]
        for pt in feat.get("points") or []:
            if not isinstance(pt, dict):
                continue
            h = str(pt.get("heading") or "").strip()
            b = str(pt.get("body") or "").strip()
            pc = _cite(pt.get("source_ids") or [], lead_by_id, extra_cn=pt.get("cn_mirror"))
            if h and b:
                lines.append(f"- **{h}**：{b}{pc}")
            elif b:
                lines.append(f"- {b}{pc}")
        lines += ["", "[/wrap]", ""]

    # 3. 重要动态简讯（外层整体包裹 + 每个领域独立子模块包裹区分）
    secs = [s for s in (brief.get("sections") or []) if str(s.get("title") or "").strip() and (s.get("items") or [])]
    if secs:
        lines += [
            "[wrap=ais-brief-news]",
            "",
            "### 重要动态简讯",
            "",
        ]
        for sec in secs:
            stitle = str(sec.get("title") or "").strip()
            items = sec.get("items") or []
            kind = _section_kind(stitle)
            lines += [
                f"[wrap=ais-brief-sec kind={kind}]",
                "",
                f"#### {stitle}",
                "",
            ]
            for it in items:
                if not isinstance(it, dict):
                    continue
                h = str(it.get("heading") or "").strip()
                b = str(it.get("body") or "").strip()
                ids = it.get("source_ids") or []
                ic = _cite(ids, lead_by_id, extra_cn=it.get("cn_mirror"))
                caveat = ""
                if it.get("t4_caveat") or (ids and all(lead_by_id.get(x) and lead_by_id[x].tier == "T4" for x in ids)):
                    caveat = " *（注：基于单一或厂商自报信息，待第三方验证）*"
                if h and b:
                    lines.append(f"- **{h}**：{b}{ic}{caveat}")
                elif b:
                    lines.append(f"- {b}{ic}{caveat}")
            lines += ["", "[/wrap]", ""]
        lines += ["[/wrap]", ""]

    discussion = str(brief.get("discussion") or "").strip()
    if discussion:
        lines += [
            f"> **延伸讨论**：{discussion}",
            "",
        ]

    lines += [
        "[/wrap]",
        "",
        "---",
        "本简报由全网实时检索与多源交叉核验自动生成，所有外链均经发布前活性校验，内容以原文为准。",
    ]
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    return s[: max(1, limit - 1)].rstrip("，、：: ") + "…"


def build_home_card(report_date: date, brief: dict[str, Any], *,
                    topic_id: int | None = None, base_url: str = "") -> dict[str, Any]:
    """构造写入底座帖 632 的 `daily_brief` 字段：首页 01 左栏核心展示【趋势洞察】，与 02/03 零重复。"""
    insights = []
    for ins in (brief.get("insights") or [])[:2]:
        if isinstance(ins, dict):
            h = _clip(str(ins.get("heading") or "").strip(), 42)
            b = _clip(str(ins.get("body") or "").strip(), 220)
            if h or b:
                insights.append({"heading": h, "body": b})
    feature_titles = [
        _clip(str(f.get("title") or "").strip(), 44)
        for f in (brief.get("features") or [])[:2]
        if str(f.get("title") or "").strip()
    ]
    url = f"{base_url.rstrip('/')}/t/{topic_id}" if (topic_id and base_url) else ""
    return {
        "date": report_date.isoformat(),
        "title": f"趋势洞察（{report_date.month}月{report_date.day}日）",
        "url": url,
        "topic_id": topic_id,
        "insights": insights,
        "feature_titles": feature_titles,
    }
