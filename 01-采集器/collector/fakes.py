"""离线用的模拟模型：不调用任何接口，按信源默认领域和关键词给出结果。只用于试运行和测试。"""
from __future__ import annotations

import hashlib
import json
import re

from .config import Config


class FakeLLM:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.calls = 0
        self.by_source = {s["name"]: s for s in cfg.sources}

    def chat(self, system: str, user: str, timeout=None, max_tokens=None) -> str:
        self.calls += 1
        # gw-ai-pulse（2026-11-16）：社区帖子打标签与「今日一问」用的是另外两种提示词，
        # 输入里没有 source 字段，不能共用下面的候选打标分支。
        if "社区运营编辑" in system:
            return "这周社区里讨论最多的做法，你们团队已经试过了吗？"
        if "只做分类，不改写内容" in system:
            return self._tag_posts_reply(user)
        # gw-ai-pulse（2026-11-19）：今日焦点的两次调用（见 collector/focus.py）。
        # 离线时也按真实契约返回，dry-run 与单测才能跑通整条焦点链路。
        if "资料员" in system:
            return self._facts_reply(user)
        if "主笔" in system:
            return self._article_reply(user)
        if "信号摘要编辑" in system:
            return self._structured_summary_reply(user)
        out = []
        for line in user.splitlines():
            item = json.loads(line)
            src = self.by_source.get(item["source"], {})
            domain = src.get("default_domain") or self.cfg.domains[0]
            questions = src.get("questions", [])[:2]
            title = (item["title"] + "（" + item["source"] + "）")[:40]
            digest = hashlib.sha1(item["title"].encode("utf-8")).hexdigest()[:8]
            date = (item.get("published_at") or "2026-01-01")[:10]
            keep = not re.search(r"招聘|活动预告|广告", item["title"])
            out.append({
                "idx": item["idx"], "keep": keep, "domain": domain, "questions": questions, "topics": [],
                "relevance": 2 if questions else 1, "title": title,
                "summary": (item.get("excerpt") or item["title"])[:100],
                "us": "", "event_key": f"fake-{digest}-{date}", "event_date": date,
            })
        return json.dumps(out, ensure_ascii=False)

    # ---------- 今日焦点（2026-11-19）----------

    def _facts_reply(self, user: str) -> str:
        """从「正文：」之后的行里挑几句原文当引文 —— 引文是真子串，逐字校验必然通过。"""
        body = user.split("正文：", 1)[-1]
        lines = [ln.strip() for ln in body.splitlines() if len(ln.strip()) >= 20][:6]
        claims = [{"text": ln[:30], "quote": ln[:120]} for ln in lines]
        nums = re.findall(r"\d+(?:\.\d+)?", body)[:4]
        numbers = [{"value": n, "unit": "", "what": "文中出现的数字"} for n in nums]
        return json.dumps({"claims": claims, "numbers": numbers,
                           "what_changed": "离线模拟：这条事件值得单独展开。"}, ensure_ascii=False)

    def _article_reply(self, user: str) -> str:
        try:
            payload = json.loads(user)
        except json.JSONDecodeError:
            payload = {}
        facts = [str(x) for x in (payload.get("已核对事实") or [])]
        title = str(payload.get("标题") or "焦点事件")
        deck = f"{title} 有了新的进展。以下内容全部来自原文，按发生了什么、关键数据、对我们的影响分开写。"
        what = "；".join(facts[:4]) + "。以上内容均摘自原文，未补充原文之外的背景。"
        data = facts[3:6] or ["原文未给出可量化数字"]
        return json.dumps({
            "deck": deck,
            "what": what,
            "data": [x[:24] for x in data],
            "impact": "影响面在于现有选型与成本假设需要对照原文重新核算，这一点可以被证伪。",
            "open": "原文未披露的部分暂不推测。",
            "us": "供相关团队判断是否需要跟进而用。",
            "points": [x[:24] for x in facts[:3]] or ["原文关键事实见正文"],
        }, ensure_ascii=False)

    def _structured_summary_reply(self, user: str) -> str:
        """离线模拟 ~300 字结构化摘要（见 collector/enrich.py）。"""
        title = ""
        for ln in user.splitlines():
            if ln.startswith("标题："):
                title = ln.split("：", 1)[1].strip()
                break
        body = user.split("正文：", 1)[-1].strip()
        snippets = [ln.strip() for ln in body.splitlines() if len(ln.strip()) >= 12]
        lead = (snippets[0][:60] if snippets else title) or "该来源披露了最新技术与行业进展"
        return json.dumps({
            "core": f"根据原文披露的新进展，相关机构针对该主题发布了具体更新与实测说明：{lead}。本次更新补充了前期公开信息中未展开的核心细节与适用范围。",
            "points": [
                "原文明确列出了核心技术机制、涉及版本与适用场景，强调实际部署阶段的边界条件与验证结果。",
                "测试与披露数据覆盖了主要业务环节的运行表现，对现有工具链的兼容性与风险点给出了量化说明。",
                "相关方同步说明了后续迭代节奏与配套支持方案，便于一线团队对照自身架构进行评估核对。",
            ],
            "impact": "对采用相关技术栈的企业与新能源数字化场景具有直接参考意义；原文尚未披露长期成本及跨环境实测结果，仍需后续跟踪验证。",
        }, ensure_ascii=False)

    def _tag_posts_reply(self, user: str) -> str:
        """社区帖子打标签的模拟回复：按标题关键词给主题，规则醒目到测试能断言。"""
        out = []
        for line in user.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            title = item.get("title", "")
            topics = []
            if "功率预测" in title:
                topics.append("功率预测 AI")
            if "Agent" in title or "智能体" in title:
                topics.append("智能体与 MCP 安全")
            out.append({"idx": item.get("idx"), "topics": topics,
                        "relevance": 2 if topics else 1})
        return json.dumps(out, ensure_ascii=False)
