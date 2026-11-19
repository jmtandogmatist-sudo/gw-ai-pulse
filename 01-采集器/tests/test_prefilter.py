"""行业源关键词前置过滤（2026-11-19 漏斗归因后加的闸门）。

归因证据：一轮 627 条候选里行业媒体约 89 条，被模型 100% 判为不值得
（Electrek 30/30、PV-Tech 26/26、OffshoreWind 17/17、reNews 8/8）。
它们不是坏源（GWEC/Windpower 偶尔有硬货），只是绝大多数条目与 AI 无关。

所以这道闸默认 shadow：只统计「本会挡掉多少、其中模型实际保留了几条」。
下面的用例就是影子机制的自证——挡掉的条目里一旦有模型保留的，就说明会误伤。

夹具 rss-industry.xml 三条：一条讲 AI 运维（应放行）、一条只讲装机（应挡）、一条超窗。
"""
from conftest import NOW

from collector.fakes import FakeLLM
from collector.pipeline import run_round

FEED = "tests/fixtures/rss-industry.xml"
SINCE = "2026-01-01T00:00:00+08:00"


def _cfg_with_industry_source(cfg, mode: str, keywords: list[str]):
    cfg.sources.append({"name": "风电行业媒体", "module": "wind_peers", "kind": "rss",
                        "url": f"file://{cfg.root / FEED}", "tier": "media",
                        "default_domain": "行业动态", "questions": ["Q01"]})
    cfg.settings["prefilter"] = {"mode": mode, "modules": ["wind_peers"], "keywords": keywords}
    return cfg


def _posted_sources(store):
    return {s.sources[0].name for s in store.recent_signals(SINCE, 200)}


def test_shadow_counts_but_does_not_drop(cfg, forum, store, alerter):
    cfg = _cfg_with_industry_source(cfg, "shadow", ["zzz-绝对不存在的词"])
    llm = FakeLLM(cfg)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.prefilter_mode == "shadow"
    assert rep.prefilter_would_drop == 2                 # 两条在窗口内、都没命中关键词
    assert rep.prefilter_model_kept == 2                 # 模型其实都保留 → 影子把误伤量算出来了
    assert rep.posted >= 2                               # 影子不生效，照常发
    assert "风电行业媒体" in _posted_sources(store)


def test_live_drops_before_tagging(cfg, forum, store, alerter):
    cfg = _cfg_with_industry_source(cfg, "live", ["zzz-绝对不存在的词"])
    llm = FakeLLM(cfg)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.prefilter_mode == "live"
    assert rep.prefilter_would_drop == 2
    assert rep.prefilter_model_kept == 0                 # live 不再统计误伤（已经挡住了）
    assert "风电行业媒体" not in _posted_sources(store)   # 一条都没进信号表


def test_off_disables_the_filter(cfg, forum, store, alerter):
    cfg = _cfg_with_industry_source(cfg, "off", ["zzz-绝对不存在的词"])
    rep = run_round(cfg, forum, FakeLLM(cfg), store, alerter, now=NOW)
    assert rep.prefilter_mode == ""
    assert rep.prefilter_would_drop == 0
    assert "风电行业媒体" in _posted_sources(store)


def test_keyword_hit_lets_industry_item_through(cfg, forum, store, alerter):
    """命中关键词的行业条目必须放行——这道闸的目的是挡「跟 AI 无关」的，不是挡行业源。"""
    cfg = _cfg_with_industry_source(cfg, "live", ["大模型"])
    rep = run_round(cfg, forum, FakeLLM(cfg), store, alerter, now=NOW)
    assert rep.prefilter_would_drop == 1                 # 只挡「公布三季度装机数据」那条
    assert "风电行业媒体" in _posted_sources(store)        # 「发布风电场运维大模型」放行了
