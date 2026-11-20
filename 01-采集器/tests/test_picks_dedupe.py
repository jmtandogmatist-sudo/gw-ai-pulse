"""01「今天发生了什么」与 04「今日值得看」不能推荐同一篇文章。

生产实例（2026-11-20 的 632 底座帖）：四条 04 推荐里，前两条正是 01 的第二、三条：
    Gemini API Tool Retrieval（blog.google）
    谷歌研究提出自我改进 Harness 架构（ithome）
两块各自从整个信号池里挑，谁也不看谁，于是同一屏出现两次同一件事。
"""
from datetime import datetime

from collector.models import Signal, SourceRef
from collector.render import brief_signals, picks_excluding

FIXED_NOW = datetime(2026, 11, 16, 12, 0)


def mk(title, url, *, domain="工具做法", relevance=2, cred="red"):
    return Signal(
        signal_id="s-" + title[:6], event_key="k-" + title[:6], title=title,
        summary="这是摘要第一句。第二句。", us="", domain=domain, questions=["Q03"],
        topics=[], relevance=relevance, cred=cred, event_date="2026-11-16",
        discovered_at="2026-11-16T09:00:00+08:00",
        sources=[SourceRef(name="某媒体", url=url, tier="media")],
        topic_id=700,
    )


def test_picks_drop_items_already_in_brief():
    gemini = mk("Gemini API 推出 Tool Retrieval 功能预览版", "https://blog.google/gemini-api")
    claude = mk("Claude Code 更新支持多平台自动模式及 MCP 钩子", "https://github.com/anthropics/cc")
    skills = mk("谷歌发布兼容主流编码智能体的 MCP 技能库", "https://github.com/google-labs-code/stitch-skills")
    sigs = [gemini, claude, skills]

    brief = brief_signals(sigs, 3, now=FIXED_NOW)
    assert {id(s) for s in brief} == {id(gemini), id(claude), id(skills)}

    picks = picks_excluding(sigs, exclude=brief, now=FIXED_NOW)
    assert picks == []          # 全被 01 用掉了就整块不显示，不留占位


def test_picks_keep_items_not_used_by_brief():
    gemini = mk("Gemini API 推出 Tool Retrieval 功能预览版", "https://blog.google/gemini-api")
    claude = mk("Claude Code 更新支持多平台自动模式及 MCP 钩子", "https://github.com/anthropics/cc")
    skills = mk("谷歌发布兼容主流编码智能体的 MCP 技能库", "https://github.com/google-labs-code/stitch-skills")
    picks = picks_excluding([gemini, claude, skills], exclude=[gemini], now=FIXED_NOW)
    urls = [p["url"] for p in picks]
    assert "https://blog.google/gemini-api" not in urls
    assert "https://github.com/anthropics/cc" in urls


def test_picks_dedupe_by_url_across_signals():
    """不同事件键指向同一篇文章时，也要拦住——按 URL 而不仅仅是按对象去重。"""
    gemini = mk("Gemini API 推出 Tool Retrieval 功能预览版", "https://blog.google/gemini-api")
    twin = mk("Gemini API 工具检索能力进入预览阶段", "https://blog.google/gemini-api")
    other = mk("Claude Code 更新支持多平台自动模式及 MCP 钩子", "https://github.com/anthropics/cc")
    picks = picks_excluding([gemini, twin, other], exclude=[gemini], now=FIXED_NOW)
    assert [p["url"] for p in picks] == ["https://github.com/anthropics/cc"]


def test_picks_capped_at_five_after_exclusion():
    """2026-11-20：条数 4 → 5；排除掉 01/02 用过的之后仍按上限截断。"""
    sigs = [mk(f"工具框架 v{i} 发布", f"https://example.com/{i}") for i in range(9)]
    picks = picks_excluding(sigs, exclude=sigs[:2], now=FIXED_NOW)
    assert len(picks) == 5
