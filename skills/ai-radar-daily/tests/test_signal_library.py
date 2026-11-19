"""5.3.0：信号库读取（signal_pool）、底座帖合并（merge_with_live）、已审回填（mark_reviewed）。"""
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from homepage_feed import merge_with_live  # noqa: E402
from mark_reviewed import plan_from_checklist, rewrite_raw  # noqa: E402
from signal_pool import parse_signal_raw, run  # noqa: E402

RAW = (HERE / "fixtures" / "signal_topic_raw.md").read_text(encoding="utf-8")
RUNTIME = {"forum": {"base_url": "https://forum.example.com"},
           "signal_library": {"enabled": True, "category_id": 42, "read_interval_seconds": 0}}


def test_parse_collector_topic_body():
    sig = parse_signal_raw(RAW)
    assert sig["summary"].startswith("新论文在多个主流智能体框架下")
    assert sig["us"].startswith("接入外部 MCP 工具前")
    assert sig["cred"] == "red" and sig["questions"] == ["Q07"] and sig["relevance"] == 3
    assert sig["sources"][0]["url"] == "https://arxiv.org/abs/2611.00001"


def test_run_builds_fetch_sources_compatible_output():
    def getter(url, headers, as_json=True):
        if "/l/latest.json?page=0" in url:
            return {"topic_list": {"topics": [
                {"id": 1001, "slug": "mcp", "title": "arXiv：MCP 工具投毒在 Agent 框架中的跨框架测量",
                 "created_at": "2026-11-05T07:31:00+08:00", "tags": ["security", "auto"]},
                {"id": 5, "slug": "about", "title": "关于“信号”类别", "created_at": "2026-01-01T00:00:00Z"},
                {"id": 900, "slug": "old", "title": "很久以前", "created_at": "2026-10-01T00:00:00Z"},
            ]}}
        if "/l/latest.json" in url:
            return {"topic_list": {"topics": []}}
        assert url.endswith("/raw/1001/1"), url
        return RAW
    res = run(date(2026, 11, 5), [], {}, runtime=RUNTIME, getter=getter)
    assert res["mode"] == "signal_library"
    assert len(res["items"]) == 1
    it = res["items"][0]
    # 与 fetch_sources.py 的 items 结构兼容
    for k in ("source_id", "entity", "type", "tier", "layer", "cat", "title", "url", "published_date", "discovered_at", "heat"):
        assert k in it
    assert it["layer"] == "signal" and it["signal_cred"] == "r"   # 单一来源只作线索
    assert it["signal_topic_id"] == 1001 and it["signal_topic_url_internal"].endswith("/t/mcp/1001")
    # 分类 20 于 2026-11-16 起仅 staff 可读，对外内容不得携带信号帖链接
    assert "signal_topic_url" not in it
    assert res["summary"]["items_in_window"] == 1


def test_run_fails_closed_when_library_not_configured():
    import pytest
    with pytest.raises(RuntimeError):
        run(date(2026, 11, 5), [], {}, runtime={"forum": {"base_url": "x"}, "signal_library": {"enabled": False}})


def test_merge_keeps_collector_and_weekly_fields():
    daily = {"schema": "air-pulse/1", "issue_date": "2026-11-05", "brief": [1], "radar": [{"title": "日报自己的"}]}
    live = {"schema": "air-pulse/1", "issue_date": "2026-11-04", "brief": [0],
            "radar": [{"title": "采集器的"}], "radar_updated_at": "2026-11-05T07:31:00+08:00", "radar_total": 120,
            "question_list": [{"id": "Q01"}], "topics": [{"name": "功率预测 AI"}], "stance": {"rings": []}}
    m = merge_with_live(daily, live)
    assert m["brief"] == [1] and m["issue_date"] == "2026-11-05"          # 日报字段用新的
    assert m["radar"] == [{"title": "采集器的"}] and m["radar_total"] == 120   # 采集器字段保留
    assert m["topics"] == [{"name": "功率预测 AI"}] and m["stance"] == {"rings": []}


def test_merge_without_collector_keeps_daily_radar():
    daily = {"schema": "air-pulse/1", "radar": [{"title": "日报自己的"}]}
    m = merge_with_live(daily, {"schema": "air-pulse/1", "radar": [{"title": "旧"}]})
    assert m["radar"] == [{"title": "日报自己的"}]                          # 采集器未上线：与 5.2.0 一致


def test_plan_and_rewrite_are_idempotent():
    checklist = {"candidates": [
        {"id": "NEWS-01", "decision": "SELECTED", "signal_topic_id": 1001, "title": "x", "us": "集团平台接入 MCP 前先做安全评审。"},
        {"id": "NEWS-02", "decision": "REJECTED_STALE", "signal_topic_id": 1002},
        {"id": "NEWS-03", "decision": "SELECTED"},
    ]}
    plan = plan_from_checklist(checklist)
    assert [p["topic_id"] for p in plan] == [1001]
    once = rewrite_raw(RAW, us=plan[0]["us"], report_date="2026-11-05", daily_url="https://f/t/x/700", decision="SELECTED")
    twice = rewrite_raw(once, us=plan[0]["us"], report_date="2026-11-05", daily_url="https://f/t/x/700", decision="SELECTED")
    assert once == twice
    assert "集团平台接入 MCP 前先做安全评审。" in once and "可对照论文的攻击面自查" not in once
    assert once.count("已进入 2026-11-05 每日精选") == 1
    assert parse_signal_raw(once)["review"] == "reviewed"


def test_rewrite_flips_wrap_review_and_inserts_us_block_when_missing():
    no_us = RAW.replace("[wrap=ais-us]\n\n**对金风**\n\n接入外部 MCP 工具前，可对照论文的攻击面自查。\n\n[/wrap]\n\n", "")
    assert "**对金风**" not in no_us
    out = rewrite_raw(no_us, us="先过安全评审。", report_date="2026-11-05", daily_url="", decision="TRACKING")
    assert "[wrap=ais-signal cred=r review=reviewed]" in out
    assert "[/wrap]\n\n[wrap=ais-us]\n\n**对金风**\n\n先过安全评审。\n\n[/wrap]" in out
    assert parse_signal_raw(out)["us"] == "先过安全评审。"
    assert parse_signal_raw(out)["summary"].startswith("新论文")
