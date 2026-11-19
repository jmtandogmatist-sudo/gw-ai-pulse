"""主题追踪（首页 05 TOPICS）：采集器信号 + 模型写判断，每周五 17:00 由 launchd 触发。

与 skills/ai-topic-dossier 的关系（2026-11-16）：
  那个 skill 的 judge / change / next 是**编辑手写**的，脚本只生成模板——去掉日报后
  没人写字了，所以这里让模型写。校验规则照抄 skill（长度上限、必须引用本周信号、
  无信号时只能写「本周无新信号。」），保证金标准不变，只是把手换成了模型。

写入位置：632 底座帖的 topics 字段，结构与 UAT 实测一致：
  {name, week, questions, judge, change, next, url}
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .models import Signal

log = logging.getLogger("collector.topics")

LIMITS = {"judge": 160, "change": 120, "next": 80}
NO_SIGNAL_CHANGE = "本周无新信号。"
PLACEHOLDER = re.compile(r"TODO|待补|XXX|\[示例\]|\{\{")
CRED_DOT = {"green": "🟢", "yellow": "🟡", "red": "🔴", "g": "🟢", "y": "🟡", "r": "🔴"}
MACHINE_RE = re.compile(r'\[details="机器可读数据"\]\s*```json\s*(.*?)```', re.S)
DOSSIER_INTRO = ("> 主题档案每周五更新。判断由采集器调用模型生成（非公司结论）；"
                 "信号由采集器自动采集，标 🔴 的为单一来源线索；标「人工帖」的是社区同事自己发的帖子。")


@dataclass
class TopicMaterial:
    """一个主题的本周素材：本周信号 + 近 30 天时间线 + 上期判断。"""
    name: str
    tag: str
    questions: list[str] = field(default_factory=list)
    week_signals: list[Signal] = field(default_factory=list)
    timeline: list[Signal] = field(default_factory=list)
    previous: dict | None = None
    dossier_topic_id: int | None = None   # 主题档案话题 ID（由 skill 建，没有就退回最新信号）

    def to_dict(self) -> dict:
        return {"name": self.name, "tag": self.tag, "questions": list(self.questions),
                "week": len(self.week_signals),
                "week_signals": [{"title": s.title, "summary": s.summary, "cred": s.cred,
                                  "date": (s.event_date or s.discovered_at or "")[:10],
                                  "topic_id": s.topic_id,
                                  # 2026-11-19：区分采集器信号与社区人工帖，
                                  # 模型写判断时才能说清「这条是社区同事自己发的」。
                                  "kind": "forum" if getattr(s, "review", "auto") == "human" else "signal"}
                                 for s in self.week_signals],
                "timeline_ids": [s.topic_id for s in self.timeline if s.topic_id],
                "previous": self.previous}


def collect_material(signals: list[Signal], topics_cfg: list[dict], *,
                     now: datetime, lookback_days: int = 7,
                     timeline_days: int = 30) -> list[TopicMaterial]:
    """按主题标签把信号分组。week = 近 lookback_days 天，timeline = 近 timeline_days 天。

    与 skill 的差别：skill 从论坛按标签取话题，这里直接吃本地的信号索引——
    采集器自己就是信号的生产者，不需要再绕一圈读论坛。
    """
    week_floor = (now - timedelta(days=lookback_days - 1)).date()
    tl_floor = (now - timedelta(days=timeline_days - 1)).date()
    out: list[TopicMaterial] = []
    for tp in topics_cfg:
        tag, name = tp.get("tag"), tp.get("name")
        if not tag or not name:
            continue
        # gw-ai-pulse（2026-11-16）易错点：Signal.topics 存的是**中文显示名**
        # （tagger 按 tags.yaml 的白名单返回显示名），而配置里的 tag 是论坛标签
        # （topic-power-forecast）。两者都要认，否则「本周 +N」永远是 0——
        # 生产实测踩过：4 个主题全 0，但库里明明有 1/6/12 条带标签的信号。
        wanted = {tag, name}
        tl, wm = [], []
        for s in signals:
            if not (wanted & set(s.topics or [])):
                continue
            day = _day(s.event_date or s.discovered_at)
            if day is None or day < tl_floor:
                continue
            tl.append(s)
            if day >= week_floor:
                wm.append(s)
        tl.sort(key=lambda s: _day(s.event_date or s.discovered_at) or date.min, reverse=True)
        wm.sort(key=lambda s: (-s.relevance, -(_day(s.event_date or s.discovered_at) or date.min).toordinal()))
        out.append(TopicMaterial(name=name, tag=tag, questions=list(tp.get("questions") or []),
                                 week_signals=wm, timeline=tl, previous=tp.get("previous") or None,
                                 dossier_topic_id=tp.get("dossier_topic_id")))
    return out


def _day(iso: str | None) -> date | None:
    try:
        return date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return None


JUDGE_PROMPT = """你是金风科技 AI 应用研究院的编辑，正在写每周主题追踪的「当前判断」。
金风科技是风电整机与新能源企业，读者是集团各团队的 AI 落地负责人。

对下面每个主题，基于**本周实际采集到的信号**写三段话：

- judge（当前判断，不超过 160 字）：这个主题走到哪一步、对金风意味着什么。
  必须具体到能被下期信号证伪，不要写「值得关注」这类空话。
- change（本周变化，不超过 120 字）：和上期判断比，本周信号带来了什么变化。
  本周没有信号时必须原样写「本周无新信号。」，不许编。
- next（下个观察点，不超过 80 字）：下周看什么事件或数据，看到了就能更新判断。

硬性要求：
1. 判断只能建立在信号上，只用下面给出的事实，不推测、不夸大
2. 只来自单一来源（cred=red）的，说的时候要带「据单一来源」
3. 本周没有信号的主题，judge 可以沿用上期判断并说明本周无新信号
4. 数字和公司名只能来自信号原文，不许自己算
5. judge / change / next 都**必须说完一个完整句子**，不要写到一半——超长会被截断
6. 信号与主题关联很弱时（例如一条风电合同挂到「功率预测」上），judge 里要点明
   「仅涉及…未触及…」，不要硬凑成该主题的进展

输入格式：每个主题一段 JSON，含 name / week_signals（本周信号）/ previous（上期判断）。
输出：一个 JSON 数组，每项 {name, judge, change, next}，条数与输入完全一致。
只输出 JSON 数组，不要解释。"""


def write_judgments(materials: list[TopicMaterial], llm) -> dict[str, dict]:
    """让模型写 judge/change/next。返回 {主题名: {judge, change, next}}。

    模型失败或输出不合规时，退回「沿用上期判断 + 本周无新信号」，保证首页不缺块。
    """
    if not materials:
        return {}
    payload = "\n".join(json.dumps(m.to_dict(), ensure_ascii=False) for m in materials)
    try:
        from .tagger import parse_json_array
        arr = parse_json_array(llm.chat(JUDGE_PROMPT, payload))
    except Exception as e:  # noqa: BLE001
        log.warning("主题判断生成失败，沿用上期：%s", e)
        return {m.name: _fallback(m) for m in materials}
    got = {}
    for o in arr:
        if isinstance(o, dict) and o.get("name"):
            got[o["name"]] = o
    return {m.name: _sanitize(got.get(m.name), m) for m in materials}


def _clip(text: str, limit: int) -> str:
    """按上限收敛，但尽量断在句末，不要拦腰切断。

    2026-11-16 生产实测：直接 text[:160] 会把判断切成「…并排查 SGLang 等组件漏」，
    读者看不出结论。优先在最后一个完整句号/分号处断；断不出再退到逗号；
    都没有才硬截并加省略号。
    """
    if len(text) <= limit:
        return text
    head = text[:limit]
    for mark in ("。", "；", "！", "？"):
        i = head.rfind(mark)
        if i >= limit * 0.6:          # 只在句子足够长时才用句末断，否则会只剩半句
            return head[: i + 1]
    i = head.rfind("，")
    if i >= limit * 0.7:
        return head[:i] + "。"
    return head[: limit - 1].rstrip() + "…"


def _sanitize(obj: dict | None, m: TopicMaterial) -> dict:
    """按 skill 的规则收敛：长度截断、去占位符、无信号时强制「本周无新信号。」"""
    if not obj:
        return _fallback(m)
    prev = (m.previous or {})
    out = {}
    for k in ("judge", "change", "next"):
        v = re.sub(r"\s+", " ", str(obj.get(k) or "")).strip()
        if PLACEHOLDER.search(v):
            v = ""
        out[k] = _clip(v, LIMITS[k]) if v else _clip(str(prev.get(k) or ""), LIMITS[k])
    if not m.week_signals:
        out["change"] = NO_SIGNAL_CHANGE
    elif out["change"] == NO_SIGNAL_CHANGE:
        # 有信号却说没信号：模型偷懒，用信号数补一句可验证的话
        out["change"] = f"本周新增 {len(m.week_signals)} 条信号。"
    if not out["judge"]:
        out["judge"] = _fallback(m)["judge"]
    if not out["next"]:
        out["next"] = "下周继续观察该主题是否有新信号。"
    out["evidence"] = [s.topic_id for s in m.week_signals[:3] if s.topic_id]
    return out


def _fallback(m: TopicMaterial) -> dict:
    prev = m.previous or {}
    judge = prev.get("judge") or (
        f"近 30 天没有一条带 {m.tag} 的信号，暂不形成判断。" if not m.timeline
        else f"本周新增 {len(m.week_signals)} 条信号，判断待下期更新。")
    return {"judge": _clip(str(judge), LIMITS["judge"]),
            "change": NO_SIGNAL_CHANGE if not m.week_signals else f"本周新增 {len(m.week_signals)} 条信号。",
            "next": _clip(str(prev.get("next") or "下周继续观察该主题是否有新信号。"), LIMITS["next"]),
            "evidence": [s.topic_id for s in m.week_signals[:3] if s.topic_id]}


def feed_topics(materials: list[TopicMaterial], judgments: dict[str, dict], *,
                base_url: str, max_on_home: int = 5) -> list[dict]:
    """组装 632 的 topics 字段。结构对齐线上组件的 normTopic。"""
    out = []
    for m in materials[:max_on_home]:
        j = judgments.get(m.name) or _fallback(m)
        out.append({
            "name": m.name,
            "week": len(m.week_signals),
            "questions": m.questions[:4],
            "judge": j["judge"], "change": j["change"], "next": j["next"],
            "url": topic_url(m, base_url),
        })
    return out


def topic_url(m: TopicMaterial, base_url: str) -> str:
    """点主题时的跳转目标。

    主题档案帖（skill 的 dossier_topic_id）尚未建立时，退回该主题时间线上最新的
    一条信号话题；都没有就留空——组件在 url 为空时不渲染链接，不会点进空页。
    """
    if m.dossier_topic_id:
        return f"{base_url.rstrip('/')}/t/{m.dossier_topic_id}"
    for s in m.timeline:
        if s.topic_id:
            return f"{base_url.rstrip('/')}/t/{s.topic_id}"
    return ""


def parse_machine(raw: str) -> dict:
    """从档案帖正文里读「机器可读数据」块（历史判断、上一期素材）。"""
    m = MACHINE_RE.search(raw or "")
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def machine_block(data: dict) -> str:
    return ('[details="机器可读数据"]\n```json\n'
            + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n[/details]")


def render_dossier(m: TopicMaterial, j: dict, week_end: str, base_url: str,
                   history: list[dict] | None = None, *,
                   timeline_limit: int = 20, history_limit: int = 12) -> str:
    """生成「主题档案」帖正文（ais-dossier 骨架，与 skills/ai-topic-dossier 同款）。

    2026-11-19：首页 04 的 url 指向档案帖后，档案必须和卡片同一份判断，
    所以渲染放在采集器里、由 update_feed_topics() 与 632 一起写。

    与 skill 的差别只有两处：
      · 判断来源改写成「采集器调用模型生成」，不冒充编辑撰写；
      · 时间线里区分社区人工帖（没有 cred/review 字段）：不加可信度圆点、不写「未审」，
        只标「人工帖」。
    """
    history = history or []
    base = base_url.rstrip("/")
    qs = "、".join(m.questions) or "—"
    ev = {s.topic_id for s in m.week_signals[:3] if s.topic_id}
    lines = [DOSSIER_INTRO, "", "[wrap=ais-dossier]", ""]
    lines += ["[wrap=ais-judge]", "", f"**当前判断**（{week_end}）", "", j["judge"].strip(), "", "[/wrap]", ""]
    lines += ["[wrap=ais-change]", "", "**本周变化**", "", j["change"].strip(), "", "[/wrap]", ""]
    lines += ["[wrap=ais-next]", "", "**下个观察点**", "", j["next"].strip(), "", "[/wrap]", ""]
    lines += ["[wrap=ais-meta]", "",
              f"**关联问题**　{qs}　·　**本周新增信号**　{len(m.week_signals)} 条", "", "[/wrap]", ""]
    tip = "近 30 天，新的在前；★ 为本期判断引用"
    if len(m.timeline) > timeline_limit:
        tip += f"；仅列最新 {timeline_limit} 条"
    lines += ["[wrap=ais-timeline]", "", f"**时间线**（{tip}）", ""]
    if m.timeline:
        for s in m.timeline[:timeline_limit]:
            day = str(s.event_date or s.discovered_at or "")[:10]
            star = "★ " if s.topic_id in ev else ""
            link = f"[{s.title}]({base}/t/{s.topic_id})" if s.topic_id else s.title
            if getattr(s, "review", "auto") == "human":
                lines.append(f"- {star}{day[5:10]} {link}（人工帖）")
            else:
                tail = "" if s.review == "reviewed" else "（未审）"
                lines.append(f"- {star}{day[5:10]} {CRED_DOT.get(s.cred, '')} {link}{tail}")
    else:
        lines.append("- 近 30 天暂无信号")
    lines += ["", "[/wrap]"]
    if len(history) > 1:
        lines += ["", "[wrap=ais-history]", "", "**历史判断**", ""]
        lines += [f"- {h['date']}：{h['judge']}" for h in history[1:history_limit]]
        lines += ["", "[/wrap]"]
    lines += ["", "[/wrap]", ""]
    machine = {"name": m.name, "tag": m.tag, "updated": week_end,
               "judge": j["judge"].strip(), "change": j["change"].strip(), "next": j["next"].strip(),
               "evidence": sorted(ev), "questions": list(m.questions),
               "history": history[: history_limit * 2],
               "week_signals": len(m.week_signals)}
    lines += [machine_block(machine)]
    return "\n".join(lines)
