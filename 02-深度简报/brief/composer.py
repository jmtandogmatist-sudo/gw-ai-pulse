"""Stage 3 专家合成成稿：调用 Gemini 3.8 Flash（异常自动回退内网 qwen3.8-27b）按 3 个 Skill 规则输出结构化深度简报。"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

import requests

from .config import BriefConfig, get_secret
from .harvester import Lead
from .verifier import audit_brief_sources, sanitize_markdown_links

log = logging.getLogger("brief.composer")
_BT = chr(96) * 3

SYSTEM_PROMPT = """你是金风 AI 社区的《每日深度简报》总编，同时融合了「全网 AI 捕手（ai-news-harvester）」「全球风电首席专家（wind-chief-expert）」与「信源审计官（source-verifier）」三套专业框架。

你将收到一批已经过 URL 活性校验和 T0–T4 可信度分级的真实新闻素材（编号 S01..SN）。请据此撰写今日《每日调查新闻深度简报》，严格输出一个 JSON 对象。

## 内容结构要求
1. **features（主打深度报道，固定 2 篇）**：
   - 挑选当日分量最重、最好有多条素材可交叉印证的 2 大事件（建议 1 篇聚焦前沿 AI / 算力 / 资本 / 安全治理重大事件，1 篇聚焦全球风电产业 / 能源贸易 / 碳壁垒或 AI×能源重大进展；若某天 AI 领域有多件特大事件也可按重要度灵活取舍）。
   - 每篇包含：
     - `title`：18–40 字中文标题，点出核心事实与张力（如「数字 + 转折/影响」）；
     - `lead`：70–130 字导语，说清时间、主体、关键数字与事件定性；
     - `points`：固定 3 条深度展开要点，每条含 `heading`（4–10 字加粗小标）+ `body`（80–140 字，给出具体数字、架构/条款、多源交叉细节、或因果传播链分析）+ `source_ids`（引用编号数组，如 `["S01", "S05"]`）；
     - `source_ids`：导语引用的素材编号数组。
2. **sections（重要动态简讯，固定 3 组，顺序固定）**：
   - 第 1 组 `title`: `"人工智能与前沿科技"`（2–3 条；其中最后 1 条固定做「国产大模型与开源生态动态核查」：若素材中有国产基座/开源重大发布则给出事实，若过去 24 小时内 DeepSeek/Qwen/Kimi/智谱/MiniMax/盘古/混元无新基座权重发布则明确写出「24h 核查无全新基座权重发布」并概述当前生态焦点）；
   - 第 2 组 `title`: `"{trade_section_title}"`（1–2 条，聚焦碳边境税 CBAM、ETS、新能源关税、清洁技术准入、储能/电力市场政策）；
   - 第 3 组 `title`: `"全球风能工程与产业"`（2–4 条，聚焦海风/陆风装机并网、租约与招标、整机厂/EPC/塔筒/海工船订单与供应链；遵循 wind-chief-expert 工程严谨性，带 MW/GW/机型/项目名等硬数据）。
   - 每组每条 `item` 包含：`heading`（8–22 字加粗短标）、`body`（70–140 字高密度事实）、`source_ids`（素材编号数组）、`t4_caveat`（仅当全部引用均为 T4 厂商自报数据时填 true，否则 false）。
3. **insights（趋势洞察，固定 2 条）**：
   - 从今日跨领域事件中提炼 2 条总编级战略判断（如 AI 产业边界合龙、全球风电装机重心与供应链迁移等），每条含 `heading`（10–22 字）+ `body`（70–120 字，基于因果链，不说空话套话）。
4. **discussion（延伸讨论，1 句，选填）**：
   - 紧扣今日最受关注的 1 个技术或产业议题，向社区读者抛出 1 句具体、低门槛、容易引发同行回复的开放提问（40–70 字，不用「欢迎讨论」这类空套话）。

## 铁律（违反即不合格）
1. **零编造**：只能使用下方素材里真实出现的事实、数字、机构名、日期；绝不补充素材里没有的百分比、金额或日期。
2. **只引用真实编号**：所有 `source_ids` 必须从素材编号（如 `"S01"`）里选取；正文 `body` 中**不要自己手写 URL 或 Markdown 链接**，系统会根据 `source_ids` 自动拼装经过实测的权威引用链接。
3. **公开合规口径**：本简报发布在公开可读的社区分类中，**不要写「金风」「集团」「我们」「我方」等公司内部动作或未公开计划**，统一使用外部行业与战略分析视角；涉及国际宏观与贸易时聚焦「能源政策、碳税、关税与供应链影响」，不做纯政治外交评论。
4. **时效诚实**：`features`（主打深度报道）优先且尽量只选用近 48h 无 `[追踪]` 标记的新鲜事件，严禁把 3 天前的 `[追踪]` 旧闻冒充今日突发；若简讯中引用 `[追踪]` 素材，须明确体现为「近期跟进/最新进展」。
5. 只输出合法 JSON 对象，不要输出任何额外说明文字。"""


def _extract_json(text: str) -> dict[str, Any]:
    s = (text or "").strip()
    m = re.search(_BT + r"(?:json)?\s*(.*?)" + _BT, s, re.S)
    if m:
        s = m.group(1).strip()
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("模型输出中未找到 JSON 对象")
    obj = json.loads(s[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("JSON 顶层不是对象")
    return obj


def _call_gemini(lcfg: dict[str, Any], system: str, user: str) -> str:
    key = get_secret(lcfg.get("api_key_secret", "gemini-api-key"))
    base = lcfg.get("base_url", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    model = lcfg.get("model", "gemini-3.8-flash")
    timeout = float(lcfg.get("timeout_seconds", 120))
    max_tokens = int(lcfg.get("max_output_tokens", 6500))
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": 0.25,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 1024},
        },
    }
    r = requests.post(f"{base}/models/{model}:generateContent", params={"key": key}, json=payload, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:260]}")
    body = r.json()
    cands = body.get("candidates") or []
    if not cands:
        raise RuntimeError(f"Gemini 返回空 candidates: {str(body)[:200]}")
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _call_openai_compat(lcfg: dict[str, Any], system: str, user: str) -> str:
    key = get_secret(lcfg.get("api_key_secret", "llm-api-key"))
    base = lcfg.get("base_url", "https://aicopilot.goldwind.com.cn:3213/v1").rstrip("/")
    model = lcfg.get("model", "qwen3.8-27b")
    timeout = float(lcfg.get("timeout_seconds", 120))
    max_tokens = int(lcfg.get("max_output_tokens", 5500))
    payload = {
        "model": model,
        "temperature": 0.25,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if r.status_code != 200:
        raise RuntimeError(f"OpenAI-compat HTTP {r.status_code}: {r.text[:260]}")
    return r.json()["choices"][0]["message"]["content"]


def build_user_payload(leads: list[Lead], report_date: date, cfg: BriefConfig,
                       *, covered_topics: list[str] | None = None) -> str:
    lines = [
        f"简报日期：{report_date.isoformat()}",
        f"有效核验素材总数：{len(leads)} 条（均已通过 URL 活性检查，按 T0–T4 权威度排序）",
        "",
    ]
    if covered_topics:
        lines.append("【近两期已报道清单（严禁重复选题；除非今日有实质性新进展且明确交代新变化，否则跳过）】：")
        for t in covered_topics[:20]:
            lines.append(f"- {t}")
        lines.append("")
    lines += [
        "以下是全部可引用素材（请只使用这些编号，不要编造任何素材外信息）：",
        "",
    ]
    for ld in leads[:48]:
        flag = " [追踪]" if ld.tracking else ""
        lines.append(
            f"[{ld.id}] ({ld.tier} | {ld.category} | {ld.publisher} | {ld.published_date or '日期未知'}{flag})\n"
            f"标题：{ld.title}\n"
            f"摘要：{ld.snippet}\n"
        )
    return "\n".join(lines)


def _validate_brief_schema(obj: dict[str, Any]) -> None:
    feats = obj.get("features")
    secs = obj.get("sections")
    ins = obj.get("insights")
    if not isinstance(feats, list) or len(feats) < 1:
        raise ValueError("features 为空或格式错误")
    if not isinstance(secs, list) or len(secs) < 1:
        raise ValueError("sections 为空或格式错误")
    if not isinstance(ins, list) or len(ins) < 1:
        raise ValueError("insights 为空或格式错误")


def compose_brief(leads: list[Lead], report_date: date, cfg: BriefConfig,
                  *, call_fn=None, covered_topics: list[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """调用主引擎（Gemini 3.8 Flash），失败自动回退内网模型，并返回成稿 JSON + 审计信息。"""
    trade_title = cfg.compliance.get("trade_section_title", "全球能源贸易与碳壁垒政策")
    system = SYSTEM_PROMPT.replace("{trade_section_title}", trade_title)
    user = build_user_payload(leads, report_date, cfg, covered_topics=covered_topics)

    used_engine = ""
    errors: list[str] = []
    raw_text = ""

    if call_fn is not None:
        raw_text = call_fn(system, user)
        used_engine = "custom"
    else:
        for slot in ("primary", "fallback"):
            lcfg = cfg.llm.get(slot) or {}
            if not lcfg:
                continue
            provider = lcfg.get("provider", "gemini")
            model = lcfg.get("model", "?")
            try:
                if provider == "gemini":
                    raw_text = _call_gemini(lcfg, system, user)
                else:
                    raw_text = _call_openai_compat(lcfg, system, user)
                used_engine = f"{slot}:{provider}/{model}"
                break
            except Exception as e:  # noqa: BLE001
                log.warning("成稿引擎 %s (%s) 失败：%s", slot, model, e)
                errors.append(f"{slot}:{model}:{e}")
        if not raw_text:
            raise RuntimeError("所有成稿模型均失败：" + " ; ".join(errors))

    brief = _extract_json(raw_text)
    _validate_brief_schema(brief)
    brief["date"] = report_date.isoformat()

    # 文本清洗 + source-verifier 成稿级硬审计（清除任何模型幻觉编号）
    for feat in brief.get("features") or []:
        feat["title"] = sanitize_markdown_links(str(feat.get("title") or "").strip())
        feat["lead"] = sanitize_markdown_links(str(feat.get("lead") or "").strip())
        for pt in feat.get("points") or []:
            if isinstance(pt, dict):
                pt["heading"] = str(pt.get("heading") or "").strip()
                pt["body"] = sanitize_markdown_links(str(pt.get("body") or "").strip())
    for sec in brief.get("sections") or []:
        for it in sec.get("items") or []:
            if isinstance(it, dict):
                it["heading"] = str(it.get("heading") or "").strip()
                it["body"] = sanitize_markdown_links(str(it.get("body") or "").strip())
    for ins in brief.get("insights") or []:
        if isinstance(ins, dict):
            ins["heading"] = str(ins.get("heading") or "").strip()
            ins["body"] = sanitize_markdown_links(str(ins.get("body") or "").strip())

    lead_by_id = {ld.id: ld for ld in leads}
    audit = audit_brief_sources(brief, lead_by_id)
    audit["engine"] = used_engine
    audit["fallback_errors"] = errors
    return brief, audit
