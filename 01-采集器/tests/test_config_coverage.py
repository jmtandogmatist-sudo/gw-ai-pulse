"""信源覆盖的回归测试（2026-11-19）。

起因是一次误判：运行报表里的 modules 字段是从「已选中的信源」反推的，
看报表的人会以为 internal 模块被 --modules 排除了，实际是那两个源 enabled: false。
同类问题还有：keep_bypass 的 26 条白名单里 10 条只有 kind: page 通道，
补判逻辑对它们一次都不会触发，名单看起来覆盖 26 家、实际只覆盖 16 家。

这里锁定四件事：
1. 「登记 / 启用 / 实跑」是三层，判据集中在 config.select_sources；
2. 没实跑的信源要能说出原因，报表才不会再骗人；
3. 白名单里的每个名字都必须真能抓到；
4. 生产配置现在就满足第 3 条（写死这份清单，避免空转的白名单被悄悄加回来）。
"""
from pathlib import Path

from collector.config import (coverage_summary, coverage_warnings, load_config,
                              select_sources, skipped_sources)
from collector.pipeline import run_round

from conftest import NOW

PROD_ROOT = Path(__file__).resolve().parent.parent

# 2026-11-19 从白名单移出的 10 家：只有 kind: page 通道，抓不到。
# 通道做出来之后可以把它们加回名单，同时把这里改成「已加回」的白名单。
DEAD_WHITELIST = [
    "Meta AI / Llama", "Microsoft AI", "xAI", "Alibaba / Qwen", "ByteDance / 豆包",
    "智谱", "月之暗面", "MiniMax", "百度", "腾讯混元",
]


def _src(name, kind="rss", **kw):
    base = {"name": name, "module": "ext_ai", "kind": kind, "url": "file:///dev/null",
            "tier": "media"}
    base.update(kw)
    return base


def test_select_sources_three_gates():
    sources = [
        _src("正常"),
        _src("停用", enabled=False),
        _src("页面源", kind="page"),
        _src("每日快照", schedule="daily"),
        _src("另一个模块", module="wind_peers"),
    ]
    names = [x["name"] for x in select_sources(sources)]
    assert names == ["正常", "另一个模块"]
    # --modules 只在该模块内取，page / 停用 / schedule 三关照旧生效
    assert [x["name"] for x in select_sources(sources, ["wind_peers"])] == ["另一个模块"]
    assert select_sources(sources, ["models"]) == []


def test_skipped_sources_reason_codes():
    sources = [
        _src("正常"),
        _src("停用", enabled=False),
        _src("页面源", kind="page"),
        _src("每日快照", schedule="daily"),
        _src("另一个模块", module="wind_peers"),
    ]
    # modules 优先于 schedule：显式点名某个模块时（例如 --modules models 的每日快照），
    # 「每日快照」这种 schedule: daily 的源照样会跑 —— 与 pipeline 原行为一致。
    assert skipped_sources(sources, ["ext_ai"]) == {
        "停用": "disabled", "页面源": "page", "另一个模块": "module",
    }
    # 不传 modules 时，模块本身不再是被跳过的原因，schedule 重新生效
    assert "另一个模块" not in skipped_sources(sources)
    assert skipped_sources(sources)["每日快照"] == "schedule=daily"


def test_run_report_carries_skipped_sources(cfg, forum, llm, store, alerter):
    """报表必须能回答「谁没跑、为什么没跑」——这是 modules 字段做不到的。"""
    cfg.sources.append(_src("未启用的竞对", kind="page", enabled=False))
    cfg.sources.append(_src("人工核查页", kind="page", module="wind_peers"))
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)

    assert rep.ok, rep.fatal
    assert rep.skipped_sources["人工核查页"] == "page"
    assert rep.skipped_sources["未启用的竞对"] == "disabled"
    assert "测试媒体" not in rep.skipped_sources


def _cfg_with_whitelist(tmp_config, sources, names):
    cfg = tmp_config
    cfg.sources = sources
    cfg.settings["keep_bypass"] = {"enabled": True, "sources": list(names)}
    return cfg


def test_coverage_warnings_phone_out_dead_and_typo(cfg):
    _cfg_with_whitelist(cfg, [_src("正常"), _src("页面源", kind="page")],
                        ["正常", "页面源", "压根不存在的名字"])

    warns = coverage_warnings(cfg)
    assert any("页面源" in w and "抓不到" in w for w in warns), warns
    assert any("压根不存在的名字" in w and "没有这个信源名" in w for w in warns), warns
    assert not any("正常" in w for w in warns), warns


def test_coverage_summary_counts_three_layers(cfg):
    _cfg_with_whitelist(cfg, [_src("正常"), _src("停用", enabled=False),
                             _src("页面源", kind="page"), _src("页面源2", kind="page")], [])
    cov = coverage_summary(cfg)
    assert (cov["registered"], cov["enabled"], cov["runnable"]) == (4, 3, 1)
    assert cov["not_runnable"] == {"page": 2, "disabled": 1}


def test_shipped_whitelist_is_all_runnable():
    """生产配置的白名单不得包含抓不到的信源。"""
    prod = load_config(PROD_ROOT)
    names = prod.settings["keep_bypass"]["sources"]
    runnable = {x.get("name") for x in select_sources(prod.sources)}

    assert coverage_warnings(prod) == [], coverage_warnings(prod)
    assert set(names) <= runnable, sorted(set(names) - runnable)
    for dead in DEAD_WHITELIST:
        assert dead not in names, f"{dead} 只有 page 通道，不该回到白名单里"


def test_collector_never_reads_external_config():
    """采集器不得依赖仓外的配置文件（2026-11-13 断链改造的不变量）。

    起因：settings.yaml 里「base_url 来自 ~/.workbuddy/models.json」这句注释，
    被读成了「运行时去读那个文件」。实际代码里一个引用都没有，但注释留着就会反复误导。
    这条测试把「不依赖」变成可执行的约束：collector/ 下不许出现
    Path.home() / expanduser / workbuddy / models.json。
    """
    hits = []
    for p in sorted((PROD_ROOT / "collector").rglob("*.py")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        for bad in ("Path.home()", "expanduser", "workbuddy", "models.json"):
            if bad in text:
                hits.append(f"{p.name}: {bad}")
    assert hits == [], hits


def test_shipped_config_reports_three_layers():
    prod = load_config(PROD_ROOT)
    cov = coverage_summary(prod)
    assert cov["runnable"] < cov["enabled"] <= cov["registered"]
    assert cov["not_runnable"].get("page", 0) > 0
