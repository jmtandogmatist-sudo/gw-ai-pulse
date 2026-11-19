"""2026-11-19 事故的回归测试：论坛限速、429 别久睡、本轮硬超时、社区补作者别扫全池。

事故经过（真实生产）：
  08:00 那一轮跑了 43 分钟仍未结束，进程 0% CPU、无打开的 socket，
  sample 显示主线程停在 time.sleep —— 论坛 admin API key 的每分钟限额被
  「逐条补发帖人」（55 次 topic 请求）打满，之后每个请求都吃 429 + Retry-After 睡眠。
  单实例锁被这一轮长期占住，12:00 / 16:00 两轮启动即跳过，首页一整天没更新。
"""
import time

import pytest

from collector import watchdog
from collector.community import pick_community, resolve_authors, scan_forum
from collector.discourse import Discourse, DiscourseError
from collector.pipeline import run_round
from collector.watchdog import RoundTimeout

from conftest import NOW


def _client(cfg, *, min_interval=0.0, max_per_minute=0) -> Discourse:
    cfg.settings["discourse"]["min_request_interval"] = min_interval
    cfg.settings["discourse"]["max_requests_per_minute"] = max_per_minute
    return Discourse(cfg)


class _Resp429:
    status_code = 429
    headers = {"Retry-After": "120"}     # 论坛给的是 120 秒，必须被压到 10 秒以内
    text = ""
    content = b"{}"

    def json(self):
        return {"errors": ["您执行此操作的次数过多。请等待 120 秒后再试。"]}


# ---------- 请求限速 ----------

def test_pace_enforces_min_interval(cfg, monkeypatch):
    """相邻两次请求之间必须有最小间隔，否则一次社区扫描就能把限额打满。"""
    c = _client(cfg, min_interval=1.0)
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    c._last_req = time.monotonic()          # 假装上一个请求刚刚发出
    c._pace()
    assert slept, "应该睡一会儿再发下一个请求"
    assert 0 < slept[0] <= 1.0
    assert c.requests_made == 1


def test_pace_enforces_per_minute_cap(cfg, monkeypatch):
    """一分钟内已经发满上限时，要等到最早那一次滑出窗口。"""
    c = _client(cfg, min_interval=0.0, max_per_minute=3)
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    now = time.monotonic()
    c._window[:] = [now - 2, now - 1, now]      # 一分钟内已发 3 次
    c._pace()
    assert slept and slept[0] > 50, "应当等接近 60 秒，让最早的请求滑出窗口"


def test_429_waits_at_most_ten_seconds_and_retries_once(cfg, monkeypatch):
    """429 只重试一次、等待压到 10 秒：旧实现按 120 秒睡眠并重试两次，一轮就废了。"""
    import collector.discourse as d
    c = _client(cfg)
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    calls = {"n": 0}

    def fake_request(*a, **k):
        calls["n"] += 1
        return _Resp429()

    monkeypatch.setattr(d.requests, "request", fake_request)
    with pytest.raises(DiscourseError) as ei:
        c.category(42)
    assert ei.value.status == 429
    assert calls["n"] == 2, "总共只该请求两次（首发 + 一次重试）"
    assert slept == [10.0], "Retry-After 120 必须被压到 10 秒"
    assert c.rate_limited == 2


# ---------- 本轮硬超时（看门狗） ----------

def test_watchdog_interrupts_blocking_sleep():
    assert watchdog.arm(0.05)
    try:
        with pytest.raises(RoundTimeout):
            time.sleep(2)
    finally:
        watchdog.clear()


def test_round_timeout_is_recorded_as_failure(cfg, forum, llm, store, alerter, monkeypatch):
    """卡住的轮次必须自己结束：失败入库 + 发告警，绝不带着锁一直挂着。"""
    cfg.settings["round"]["max_seconds"] = 0.05
    monkeypatch.setattr("collector.pipeline.fetch", lambda *a, **k: time.sleep(1) or [])
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert not rep.ok
    assert "超时" in rep.fatal
    assert alerter.sent, "失败要发告警，否则又变成「安静地卡住」"
    last = list(store.db.execute("SELECT ok FROM runs ORDER BY id DESC LIMIT 1"))
    assert last and last[0][0] == 0, "runs 表必须记成失败，不能写成功"


# ---------- 社区帖子：补作者不能扫全池 ----------

def _seed_community(forum, n=5):
    forum.category_rows = [{"id": 7, "name": "AI工具与办公", "read_restricted": False}]
    forum.community_rows = [
        {"id": 900 + i, "title": f"社区帖子第 {i} 条内容", "slug": f"t-{i}",
         "posts_count": 3, "views": 10, "like_count": 1,
         "created_at": "2026-11-04T09:00:00+00:00", "category_id": 7}
        for i in range(n)
    ]
    forum.post_authors = {}


def test_scan_forum_can_skip_author_lookup(cfg, forum, monkeypatch):
    """with_authors=False：扫描阶段一个 topic 请求都不该发（55 次请求就是这么来的）。"""
    _seed_community(forum, 5)
    asked = []
    monkeypatch.setattr(forum, "topic_author", lambda tid: asked.append(tid) or "某人")
    posts = scan_forum(forum, forum.category_rows, 42, with_authors=False)
    assert len(posts) == 5
    assert asked == [], "扫描阶段不该补作者"
    assert {p.who for p in posts} == {""}


def test_resolve_authors_only_for_picked(cfg, forum, monkeypatch):
    """只为最终展示的那几条补作者：5 条池子取 2 条 → 只发 2 次请求。"""
    _seed_community(forum, 5)
    asked = []
    monkeypatch.setattr(forum, "topic_author", lambda tid: asked.append(tid) or f"人{tid}")
    posts = scan_forum(forum, forum.category_rows, 42, with_authors=False)
    picked = pick_community(posts, limit=2, days=14, order="new", now=NOW)
    resolve_authors(forum, picked)
    assert len(asked) == 2
    assert all(p.who for p in picked)


# ---------- 模型故障不灌人工队列 ----------

class _FlakyFirst:
    """第一次调用就失败（模拟模型接口超时），之后交给 FakeLLM 正常回答。"""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    def chat(self, system, user):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("HTTPConnectionPool: Read timed out. (read timeout=45)")
        return self.inner.chat(system, user)


def test_model_failure_is_counted_not_pending(cfg, forum, store, alerter, llm, monkeypatch):
    """模型超时的那一条记进 model_failed，不进 pending（2026-11-19：48/65 条是这种噪声）。"""
    cfg.settings["llm"]["batch_size"] = 1
    rep = run_round(cfg, forum, _FlakyFirst(llm), store, alerter, now=NOW)
    assert rep.model_failed == 1, rep.fatal
    assert rep.pending == 0
    assert store.pending_count() == 0

def test_disabled_source_is_not_reported_as_chronic(cfg, forum, llm, store, alerter):
    """停用的信源不该出现在「长期失败」名单里。

    2026-11-19 实测：机器之心 11-17 就停用了，但 source_health 那一行还在，
    连续失败次数冻结在 14，于是每轮都被列进「长期失败」并告警——看起来像没停干净。
    """
    cfg.sources.append({"name": "已停用的坏源", "module": "ext_ai", "kind": "rss",
                        "url": "file:///nope.xml", "enabled": False})
    for _ in range(4):
        store.mark_source("已停用的坏源", ok=False, error="boom")
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.ok, rep.fatal
    assert "已停用的坏源" not in rep.chronic_sources
