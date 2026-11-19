"""来源白名单补判（方案 C 最小版，P1）的回归测试。

背景（2026-11-19）：keep 是唯一实质闸门，判据却是「与集团问题是否相关」。
官方一手发布往往只有「发布了什么」没有解读，最先被判掉；97 条信号里 30% 反而
来自 IT之家 这类二手媒体。做法是被判掉的白名单来源再问一次，只补字段、不改取舍。

这里用一个「第一遍全判掉、补判才放行」的模拟模型，验证四件事：
1. 名单内的来源被救回来，名单外的照旧丢弃；
2. keep_bypass.enabled=false 时完全回到旧行为（一键回退有效）；
3. 救回来不等于发帖：relevance 门槛照旧生效；
4. 补判本身报错不影响整轮。
"""
import json

from collector.fakes import FakeLLM
from collector.pipeline import run_round

from conftest import NOW

FORCE_MARK = "来源白名单补判"


class DropFirstPassLLM(FakeLLM):
    """第一遍把所有候选判掉；只有在补判提示词（FORCE_MARK）下才放行。"""

    def __init__(self, cfg, forced_relevance=None):
        super().__init__(cfg)
        self.forced_relevance = forced_relevance
        self.forced_calls = 0

    def chat(self, system, user):
        raw = super().chat(system, user)
        if "只做分类" in system or "社区运营编辑" in system:
            return raw                      # 社区打标签/今日一问走别的分支
        out = json.loads(raw)
        if FORCE_MARK in system:
            self.forced_calls += 1
            for o in out:
                o["keep"] = True
                if self.forced_relevance is not None:
                    o["relevance"] = self.forced_relevance
        else:
            for o in out:
                o["keep"] = False
        return json.dumps(out, ensure_ascii=False)


class BoomOnForcePassLLM(DropFirstPassLLM):
    """补判调用直接抛错：整轮必须照常跑完，不能因此失败。"""

    def chat(self, system, user):
        if FORCE_MARK in system:
            raise RuntimeError("补判接口超时")
        return super().chat(system, user)


def _titles(forum):
    return {t["title"] for tid, t in forum.topics.items() if tid != 632}


def test_whitelisted_source_is_rescued_and_others_still_dropped(cfg, forum, store, alerter):
    cfg.settings["keep_bypass"] = {"enabled": True, "sources": ["厂商博客"]}
    llm = DropFirstPassLLM(cfg)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)

    assert rep.ok, rep.fatal
    assert llm.forced_calls == 1, "只应对名单内那一条候选发起一次补判"
    assert rep.keep_bypassed == 1
    titles = _titles(forum)
    assert any(x.startswith("模型厂商发布新版本（厂商博客") for x in titles), titles
    # 名单外的媒体/政策站照旧按 keep=false 丢掉，补判不能顺带把它们放进来
    assert not any("风电场运维" in x for x in titles), titles
    # 5 条待判定里只救回 1 条，其余 4 条（含招聘、政策站）仍按 keep=false 丢弃
    assert rep.dropped_by_model == 4


def test_disabled_restores_old_behaviour(cfg, forum, store, alerter):
    cfg.settings["keep_bypass"] = {"enabled": False, "sources": ["厂商博客"]}
    llm = DropFirstPassLLM(cfg)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)

    assert rep.ok, rep.fatal
    assert llm.forced_calls == 0 and rep.keep_bypassed == 0
    assert rep.posted == 0


def test_rescued_item_still_must_clear_relevance_gate(cfg, forum, store, alerter):
    """补判只放宽 keep，不放宽 relevance：救回来但只是行业背景的，照样不发帖。"""
    cfg.settings["keep_bypass"] = {"enabled": True, "sources": ["厂商博客"]}
    llm = DropFirstPassLLM(cfg, forced_relevance=1)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)

    assert rep.ok, rep.fatal
    assert rep.keep_bypassed == 1
    assert rep.posted == 0
    assert rep.below_min_relevance == 1


def test_force_pass_failure_does_not_break_round(cfg, forum, store, alerter):
    cfg.settings["keep_bypass"] = {"enabled": True, "sources": ["厂商博客"]}
    llm = BoomOnForcePassLLM(cfg)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)

    assert rep.ok, rep.fatal
    assert rep.keep_bypassed == 0
    assert rep.posted == 0
