"""普通信号结构化摘要（~300 字）的单元测试（2026-11-20）。"""
import json
import re

import pytest

from collector import enrich, focus
from collector.models import Signal, SourceRef
from collector.pipeline import run_round
from collector.render import (parse_machine_block, public_topic_body,
                              radar_items, topic_body)
from collector.watchdog import RoundTimeout
from conftest import NOW


SAMPLE_BODY = (
    "安全研究机构今天披露了一起针对 AI 开发工具链的供应链凭证泄露事件。"
    "攻击者利用第三方推理服务商签发的旧版 API 密钥，在 72 小时内访问了 14 个代码仓库与构建日志。"
    "官方响应团队已于 2026 年 11 月 18 日 04:00 吊销全部受影响密钥，并将令牌有效期从 90 天缩短至 7 天。"
    "报告同时指出，超过 60% 的受影响插件在默认配置下未启用最小权限隔离，且目前尚未确认是否存在二次外泄。"
) * 2


def _sig(title="某开发工具链曝出第三方密钥泄露事件", *, relevance=2, focus_flag=False):
    return Signal(
        signal_id="sig-test-1",
        event_key="dev-tool-key-leak-2026-11-18",
        title=title,
        summary="原有的一句话简短摘要。",
        us="使用相关开发插件的企业需核查密钥权限与轮换策略。",
        domain="安全",
        questions=["Q07"],
        topics=["智能体与 MCP 安全"],
        relevance=relevance,
        cred="yellow",
        event_date="2026-11-18",
        discovered_at="2026-11-20T12:00:00+08:00",
        sources=[SourceRef(name="测试安全报", url="https://example.org/news/leak-2026", tier="media")],
        focus=focus_flag,
    )


def test_validate_structured_accepts_clean_300_char_payload(cfg):
    raw = {
        "core": "第三方推理服务商旧版 API 密钥泄露导致 14 个开发仓库及构建日志在 72 小时内遭到未授权访问，官方已紧急吊销密钥并收紧有效期。",
        "points": [
            "- 受影响范围涉及 14 个代码仓库与构建日志，暴露窗口持续约 72 小时。",
            "2. 应急响应于 11 月 18 日完成密钥吊销，并将令牌默认有效期由 90 天缩短至 7 天。",
            "• 审计显示超过 60% 的受影响插件在默认安装状态下未启用最小权限隔离。",
        ],
        "impact": "对依赖第三方推理接口与插件生态的企业开发链路敲响警钟；原文尚未确认是否存在二次数据外泄，仍需跟踪后续披露。",
    }
    got = enrich.validate_structured(raw, cfg, title="第三方推理服务商密钥泄露事件")
    assert got is not None
    # 项目符号前缀应被自动清洗，避免渲染成 "- - "
    assert not got["points"][0].startswith("-")
    assert not got["points"][1].startswith("2.")
    assert not got["points"][2].startswith("•")
    n = enrich.structured_char_count(got)
    assert 180 <= n <= 420


def test_validate_structured_rejects_too_short_or_placeholder(cfg):
    short = {"core": "一句话太短了不够三十个字。", "points": ["要点一太短", "要点二太短"], "impact": "待观察。"}
    assert enrich.validate_structured(short, cfg) is None

    placeholder = {
        "core": "这是一段长度足够的中文核心进展描述，用来测试占位符拦截逻辑是否严格生效。",
        "points": [
            "第一条要点包含具体的版本与测试范围信息，字数完全符合要求。",
            "第二条要点里出现了 [TODO] 占位符，必须被直接拒绝不能发布。",
        ],
        "impact": "影响与边界部分字数足够，用于确保只有占位符这一条触发拦截。",
    }
    assert enrich.validate_structured(placeholder, cfg) is None


def test_validate_structured_clips_oversized_payload_under_max(cfg):
    big = {
        "core": "核心进展描述非常详细，包含了大量技术参数和发布背景说明。" * 4,
        "points": [
            "要点一详细描述了测试环境、数据规模、吞吐表现以及对比基准结果。" * 2,
            "要点二详细描述了权限隔离机制、默认配置缺陷以及官方给出的补丁时间表。" * 2,
            "要点三详细描述了受影响版本清单、兼容性说明与迁移注意事项。" * 2,
            "要点四作为补充要点在总字数超限时应优先被裁掉以控制在三百字左右。" * 2,
        ],
        "impact": "行业影响与待观察边界描述说明了后续需要验证的多项条件与成本因素。" * 2,
    }
    got = enrich.validate_structured(big, cfg)
    assert got is not None
    assert enrich.structured_char_count(got) <= cfg.settings["structured_summary"]["target_max_chars"]


def test_enrich_signal_success_and_degrade_paths(cfg, llm, store):
    cfg.settings["structured_summary"]["enabled"] = True
    sig = _sig()
    ok = enrich.enrich_signal(sig, cfg, llm, store=store, fetch=lambda _u: SAMPLE_BODY)
    assert ok is True
    assert sig.structured_summary["core"]
    assert len(sig.structured_summary["points"]) >= 2
    assert sig.structured_summary["impact"]
    # sig.summary 同步更新为精炼核心段（≤140 字），给首页 632 卡片用
    assert len(sig.summary) <= 140
    assert sig.summary != "原有的一句话简短摘要。"

    # 焦点信号跳过（已走长文模板）
    fsig = _sig(focus_flag=True)
    assert enrich.enrich_signal(fsig, cfg, llm, store=store, fetch=lambda _u: SAMPLE_BODY) is False
    assert fsig.structured_summary == {}

    # 抓不到正文 → 优雅降级，保留原简短摘要
    dsig = _sig()
    assert enrich.enrich_signal(dsig, cfg, llm, store=store, fetch=lambda _u: "太短") is False
    assert dsig.structured_summary == {}
    assert dsig.summary == "原有的一句话简短摘要。"


def test_enrich_skips_chronically_unfetchable_host(cfg, llm, store, monkeypatch):
    cfg.settings["structured_summary"]["enabled"] = True
    # 预先记录该 host 连续失败 2 次且从未成功
    focus._record_host(store, "https://blocked.example.com/a", False)
    focus._record_host(store, "https://blocked.example.com/b", False)

    called = {"n": 0}
    def fake_fetch(_url, **_kw):
        called["n"] += 1
        return SAMPLE_BODY
    monkeypatch.setattr(focus, "fetch_text", fake_fetch)

    sig = _sig()
    sig.sources = [SourceRef(name="不可抓站", url="https://blocked.example.com/c", tier="media")]
    assert enrich.enrich_signal(sig, cfg, llm, store=store) is False
    assert called["n"] == 0, "已知连续失败的站点不应再发网络请求"


def test_enrich_propagates_round_timeout(cfg, llm, store):
    cfg.settings["structured_summary"]["enabled"] = True
    sig = _sig()
    def boom(_u):
        raise RoundTimeout("超限")
    with pytest.raises(RoundTimeout):
        enrich.enrich_signals([sig], cfg, llm, store=store, fetch=boom)


def test_render_structured_summary_preserves_downstream_compat(cfg, llm, store):
    cfg.settings["structured_summary"]["enabled"] = True
    sig = _sig()
    assert enrich.enrich_signal(sig, cfg, llm, store=store, fetch=lambda _u: SAMPLE_BODY) is True

    raw = topic_body(sig, cfg)
    assert "**摘要**" in raw
    assert "**关键要点**" in raw
    assert "**影响与边界**" in raw
    assert f"- {sig.structured_summary['points'][0]}" in raw

    # 信号帖已移除机器可读数据块，正文更清爽
    assert "机器可读数据" not in raw
    assert parse_machine_block(raw) is None

    # 下游 signal_pool / forum.py 正则取 **摘要** 后第一段：必须能拿到干净核心段
    m = re.search(r"\*\*摘要\*\*\s*\n\s*\n(.*?)\n\s*\n", raw, re.S)
    assert m is not None
    assert m.group(1).strip() == sig.structured_summary["core"]

    # 公开帖也使用结构化摘要
    pub = public_topic_body(sig, cfg)
    assert "**关键要点**" in pub
    assert "**影响与边界**" in pub

    # 首页 632 radar 条目仍用精炼 sig.summary（不含 Markdown 列表）
    items = radar_items([sig], cfg, "https://forum.example.com")
    assert len(items) == 1
    assert "**关键要点**" not in items[0]["summary"]
    assert len(items[0]["summary"]) <= 140


def test_run_round_enriches_non_focus_signals(cfg, forum, llm, store, alerter, monkeypatch):
    cfg.settings["structured_summary"]["enabled"] = True
    monkeypatch.setattr(focus, "fetch_text", lambda _url, **_kw: SAMPLE_BODY)

    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.ok, rep.fatal
    assert rep.posted == 3
    # 2 条 http 源精炼成功，1 条 file:// 列表源优雅降级（不阻断发帖）
    assert rep.enriched == 2
    assert rep.enrich_skipped == 1

    # 检查写到模拟论坛的正文确实包含结构化分段
    posts = list(forum.posts.values())
    assert sum("**关键要点**" in p["raw"] and "**影响与边界**" in p["raw"] for p in posts) == 2
    assert all("机器可读数据" not in p["raw"] for p in posts)


def test_fetch_github_org_parses_non_fork_repos(monkeypatch):
    from collector.sources import special

    payload = [
        {"full_name": "Tencent-Hunyuan/Hy4", "html_url": "https://github.com/Tencent-Hunyuan/Hy4",
         "description": "Hunyuan Hy4 open-source model", "stargazers_count": 120,
         "pushed_at": "2026-11-20T02:46:39Z", "fork": False, "archived": False},
        {"full_name": "Tencent-Hunyuan/old-fork", "html_url": "https://github.com/Tencent-Hunyuan/old-fork",
         "description": "fork", "stargazers_count": 1, "pushed_at": "2026-11-20T01:00:00Z",
         "fork": True, "archived": False},
    ]
    monkeypatch.setattr(special, "http_get", lambda _u: json.dumps(payload))
    src = {"name": "腾讯混元 · GitHub 开源", "module": "ext_ai", "kind": "github_org",
           "tier": "official", "org": "Tencent-Hunyuan", "max_items": 10}
    cands = special.fetch_special(src)
    assert len(cands) == 1
    assert cands[0].url == "https://github.com/Tencent-Hunyuan/Hy4"
    assert cands[0].source_tier == "official"
    assert cands[0].published_at and cands[0].published_at.startswith("2026-11-20")
