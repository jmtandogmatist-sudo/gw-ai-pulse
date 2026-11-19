"""今日焦点长文（方案 A，2026-11-19）的回归测试。

四条底线，每条一个用例：
1. 只有「引文能在正文里逐字找到」的事实才算数 —— 模型编的一律删掉；
2. 事实太少不出稿（退回一句话 summary），不能硬写；
3. 同一个 event_key 七天只做一次焦点（规则 a：没有新事件就沿用上一轮）；
4. 发帖走长文模板，首页 02 用「导语 + 3 条要点」的入口卡，且 01 让位。
"""
import json

from collector import focus
from collector.fakes import FakeLLM
from collector.models import Signal, SourceRef
from collector.pipeline import run_round
from collector.render import (focus_lead_item, focus_topic_tags, read_feed,
                              topic_body_focus)

from conftest import NOW

BODY = """国家能源局发布《中国虚拟电厂发展报告（2026）》，报告提出到 2030 年虚拟电厂调节能力达到 5000 万千瓦。
报告认为，虚拟电厂可以把分散的分布式电源、储能和可调负荷聚合起来参与电力系统调节。
截至 2025 年底，全国已有 20 个省份开展虚拟电厂试点，累计聚合可调容量超过 3000 万千瓦。
报告建议完善虚拟电厂参与电力市场的机制，明确其市场准入、计量结算和价格机制。
报告还提到，虚拟电厂参与辅助服务市场时，需要解决计量与结算口径不统一的问题，否则难以核算收益。
目前各地试点以需求响应补贴为主，容量补偿和现货市场参与仍在探索阶段，商业模式尚未稳定。
"""


def mk_sig(title="国家能源局发布虚拟电厂发展报告", *, relevance=2, cred="green",
           tier="authority", date="2026-11-05", event_key="nea-vpp-2026-11-05",
           url="https://news.example/vpp"):
    return Signal(
        signal_id=title[:8], event_key=event_key, title=title, summary="一句话摘要。",
        us="对金风一句。", domain="政策合规", questions=["Q02"], topics=[],
        relevance=relevance, cred=cred, event_date=date,
        discovered_at=f"{date}T07:00:00+08:00",
        sources=[SourceRef(name="新华网 · 能源频道", url=url, tier=tier)],
        topic_id=None,
    )


class Store:
    """focus 只需要 get_state / set_state，单测给个最小实现。"""

    def __init__(self):
        self.d = {}

    def get_state(self, key, default=None):
        return self.d.get(key, default)

    def set_state(self, key, value):
        self.d[key] = value


def build(cfg, sigs, fetch=None, store=None):
    cfg.settings.setdefault("focus", {})["enabled"] = True
    return focus.build(sigs, cfg, FakeLLM(cfg), store or Store(), NOW,
                       fetch=fetch or (lambda url: BODY))


# ---------- 1. 逐字校验 ----------

def test_fabricated_quote_is_dropped():
    facts = {"claims": [
        {"text": "真实的", "quote": "全国已有 20 个省份开展虚拟电厂试点"},
        {"text": "编的", "quote": "报告提出 2035 年虚拟电厂装机达到 1 亿千瓦"},   # 正文里没有
    ], "numbers": [{"value": "2030", "unit": "年", "what": "目标年份"},
                   {"value": "9999", "unit": "万千瓦", "what": "编的数字"}]}
    claims, numbers = focus.verify_claims(facts, BODY)
    assert [c["text"] for c in claims] == ["真实的"]
    assert [n["value"] for n in numbers] == ["2030"]


def test_quote_matching_ignores_width_and_whitespace():
    facts = {"claims": [{"text": "全角半角不影响", "quote": "全国已有 20 个省份　　开展虚拟电厂试点"}]}
    claims, _ = focus.verify_claims(facts, BODY)
    assert len(claims) == 1


# ---------- 2. 事实不足不出稿 ----------

def test_too_few_facts_means_no_article(cfg):
    """引文全部对不上 → 事实 0 条 → 本轮不出焦点，绝不让模型硬写。"""
    sig = mk_sig()
    out = focus.build([sig], cfg, FakeLLM(cfg), Store(), NOW,
                      fetch=lambda url: BODY)
    cfg.settings.setdefault("focus", {})["enabled"] = True

    class BadFacts(FakeLLM):
        def chat(self, system, user, timeout=None, max_tokens=None):
            if "资料员" in system:
                return json.dumps({"claims": [{"text": "编的", "quote": "正文里根本没有这句话"}],
                                   "numbers": [], "what_changed": ""}, ensure_ascii=False)
            return super().chat(system, user)

    out = focus.build([sig], cfg, BadFacts(cfg), Store(), NOW, fetch=lambda url: BODY)
    assert out.sig is None
    assert "都没通过" in out.reason
    assert not sig.focus


def test_short_body_skips_to_next_candidate(cfg):
    a = mk_sig(title="第一条太短", event_key="k1")
    b = mk_sig(title="第二条可用", event_key="k2")
    cfg.settings.setdefault("focus", {})["enabled"] = True
    texts = {"https://news.example/vpp": BODY}

    def fetch(url):
        return texts[url]

    # a 与 b 同源同 URL，用长度门槛模拟「a 抽不出正文」
    calls = {"n": 0}

    def fetch_seq(url):
        calls["n"] += 1
        return "太短" if calls["n"] == 1 else BODY

    out = focus.build([a, b], cfg, FakeLLM(cfg), Store(), NOW, fetch=fetch_seq)
    assert out.tried == 2
    assert out.sig is b


def test_falls_back_to_second_source_and_reorders(cfg):
    """第一条来源抓不到（新华网正文是 JS 渲染的），要能换到官方原文并把它排到首位。"""
    sig = mk_sig()
    sig.sources.append(SourceRef(name="国家能源局", url="https://www.nea.example/c.html", tier="official"))
    calls = []

    def fetch(url):
        calls.append(url)
        if "news.example" in url:
            raise RuntimeError("403 Forbidden")
        return BODY

    out = build(cfg, [sig], fetch=fetch)
    assert out.sig is sig
    assert [c for c in calls] == ["https://news.example/vpp", "https://www.nea.example/c.html"]
    assert sig.sources[0].name == "国家能源局"


# ---------- 3. 七天只做一次 ----------

def test_same_event_key_is_not_focused_twice(cfg):
    store = Store()
    sig = mk_sig()
    out = build(cfg, [sig], store=store)
    assert out.sig is sig and sig.focus
    # 事件键由发帖成功后调用方记录（build 自己不记，避免发帖失败还占着这个事件）
    assert focus.recent_keys(store, now=NOW) == set()
    focus.remember(store, sig.event_key, NOW)
    # 第二轮同一条（新对象、同 event_key）不能再选
    again = mk_sig(title="同一事件的转载")
    out2 = build(cfg, [again], store=store)
    assert out2.sig is None and "没有新的焦点级事件" in out2.reason


def test_relevance_and_age_filter(cfg):
    old = mk_sig(title="太旧的事件", date="2026-11-01", event_key="k-old")
    weak = mk_sig(title="相关度不够", relevance=1, event_key="k-weak")
    out = build(cfg, [old, weak])
    assert out.sig is None


def test_hosts_that_never_yield_text_are_tried_later(cfg):
    """新华网这种 JS 渲染页永远抓不到，选题要往「抓得到的站点」偏。"""
    store = Store()
    focus._record_host(store, "https://news.example/vpp", False)
    focus._record_host(store, "https://news.example/vpp", False)
    dead = mk_sig(title="抓不到正文的媒体稿", event_key="k-dead", tier="official",
                  url="https://news.example/vpp")
    alive = mk_sig(title="抓得到正文的官网稿", event_key="k-alive", tier="media",
                   url="https://static.example/nea.html")
    # dead 的前排条件更好（一手 + 相关度满分），但站点从没抓到过正文
    dead.relevance = 3
    out = build(cfg, [dead, alive], store=store)
    assert out.sig is alive


def test_fetch_attempts_are_recorded_per_host(cfg):
    store = Store()
    build(cfg, [mk_sig()], store=store)
    stats = focus.host_stats(store)
    assert stats["news.example"]["ok"] == 1
    assert stats["news.example"]["fail"] == 0


# ---------- 4. 渲染与首页衔接 ----------

def test_focus_topic_body_has_sections_claims_and_meta(cfg):
    sig = mk_sig()
    out = build(cfg, [sig])
    art = out.article
    body = topic_body_focus(sig, cfg)
    assert art.deck[:10] in body
    assert "## 发生了什么" in body and "## 关键数据" in body
    assert "## 对金风意味着什么" in body
    assert "关键事实与原文引文（可核对）" in body
    assert art.claims[0]["quote"][:12] in body
    assert "focus" in focus_topic_tags(sig, cfg)


def test_focus_lead_summary_is_capped_and_links_topic(cfg):
    sig = mk_sig()
    build(cfg, [sig])
    sig.topic_id = 999
    lead = focus_lead_item(sig)
    assert lead["kind"] == "focus"
    assert len(lead["summary"]) <= 320
    assert lead["summary"].startswith(sig.focus_deck[:10])
    assert " · " in lead["summary"]            # 3 条要点串在导语后面
    assert lead["topic_id"] == 999


def test_non_focus_signal_has_no_focus_lead():
    assert focus_lead_item(mk_sig()) is None


# ---------- 5. 整轮：出稿、01 让位、首页焦点卡 ----------

class Recorder:
    def __init__(self):
        self.text = ""


def test_round_publishes_focus_long_form(cfg, forum, store, alerter, monkeypatch):
    cfg.settings.setdefault("focus", {})["enabled"] = True
    cfg.settings["feed"]["enabled"] = True
    monkeypatch.setattr(focus, "fetch_text", lambda url, timeout=20, max_chars=8000: BODY)

    rep = run_round(cfg, forum, FakeLLM(cfg), store, alerter, now=NOW)
    assert rep.ok, rep.fatal
    assert rep.focus_posted == 1, rep.focus_skipped
    assert rep.focus_chars and rep.focus_claims >= 4

    focus_topics = [t for tid, t in forum.topics.items()
                    if tid != 632 and "focus" in t["tags"]]
    assert len(focus_topics) == 1
    raw = forum.posts[focus_topics[0]["posts"][0]]["raw"]
    assert "## 关键数据" in raw and "关键事实与原文引文" in raw

    feed, _ = read_feed(forum.posts[1]["raw"])
    assert feed["lead"]["kind"] == "focus"
    assert feed["lead"]["url"].endswith(str(focus_topics[0]["id"]))
    # 01 让位：焦点标题不出现在简报里
    assert all(feed["lead"]["title"] not in b["text"] for b in feed["brief"])
