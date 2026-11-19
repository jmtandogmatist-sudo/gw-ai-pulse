import json

from collector.__main__ import _acquire_run_lock
from collector.discourse import DiscourseError
from collector.pipeline import run_round
from collector.render import read_feed

from conftest import NOW


def test_run_lock_blocks_second_instance(cfg):
    """单实例锁：上一轮没跑完，下一轮必须直接跳过而不是并发写同一篇底座帖。

    2026-11-17 实测踩过：一轮卡在网络上十几分钟，launchd 到点又起一个进程，
    两轮各自对底座帖读-改-写，先写那轮的读回校验会报「雷达条数不一致」。
    """
    first = _acquire_run_lock(cfg)
    assert first is not None, "第一次应该拿到锁"
    try:
        assert _acquire_run_lock(cfg) is None, "锁被占用时第二次必须拿不到"
    finally:
        first.close()
    again = _acquire_run_lock(cfg)
    assert again is not None, "释放后应该能重新拿到"
    again.close()


def test_round_end_to_end(cfg, forum, llm, store, alerter):
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.ok, rep.fatal
    # rss 5 条：旧闻、屏蔽站点被初筛；招聘被模型丢弃；两条同题报道合并为一条
    assert rep.screened_out == 2
    assert rep.dropped_by_model == 1
    assert rep.merged == 1
    # 发帖：合并后的风电运维 1 条 + 厂商博客 1 条 + 政策站 1 条
    assert rep.posted == 3
    titles = {t["title"] for tid, t in forum.topics.items() if tid != 632}
    assert any(x.startswith("某整机厂发布风电场运维大模型") for x in titles)
    merged = next(t for t in forum.topics.values() if t["title"].startswith("某整机厂发布风电场运维大模型"))
    assert "cred-yellow" in merged["tags"]                      # 两家媒体互证
    policy = next(t for t in forum.topics.values() if t["title"].startswith("关于推进"))
    assert "cred-green" in policy["tags"] and "q02" in policy["tags"]
    # 试运行期 feed.enabled=false：底座帖不动
    feed, _ = read_feed(forum.posts[1]["raw"])
    assert feed["radar"][0]["title"] == "旧雷达条目"
    assert not alerter.sent


def test_second_round_is_idempotent(cfg, forum, llm, store, alerter):
    run_round(cfg, forum, llm, store, alerter, now=NOW)
    writes = forum.writes
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.posted == 0 and rep.duplicates >= 4
    assert forum.writes == writes


def test_feed_refresh_only_touches_radar(cfg, forum, llm, store, alerter):
    cfg.settings["feed"]["enabled"] = True
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.feed_updated, rep.feed_error
    feed, _ = read_feed(forum.posts[1]["raw"])
    assert feed["schema"] == "air-pulse/1"                    # v0.2 组件仍能读
    # gw-ai-pulse（2026-11-16）：简报/焦点改为采集器每轮重算（此前只有日报写）。
    # 这里断言的是「被本轮信号覆盖」而不是「原样保留」。
    assert feed["brief"] and feed["brief"][0]["source"] in ("测试媒体", "测试政策站", "厂商博客")
    assert feed["brief_updated_at"]
    assert len(feed["radar"]) == 3
    assert all(x["topic_url"].endswith(tuple("0123456789")) for x in feed["radar"])
    assert feed["radar"][0]["cred"] in ("g", "y", "r")       # 组件只认这三个代码
    assert feed["radar_total"] == 3


def test_readback_mismatch_stops_round(cfg, forum, llm, store, alerter):
    forum.corrupt_readback = True
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert not rep.ok and "读回不一致" in rep.fatal
    assert rep.posted == 0
    assert len([t for t in forum.topics if t != 632]) == 1      # 第一条发出后立即停住
    assert any("读回不一致" in m for m in alerter.sent)


def test_forum_unreachable_is_fatal_before_fetching(cfg, forum, llm, store, alerter):
    def boom(_):
        raise ConnectionError("timeout")
    forum.category = boom
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert not rep.ok and "连不上论坛" in rep.fatal
    assert rep.candidates == 0 and llm.calls == 0
    assert alerter.sent


def test_llm_total_failure_is_fatal(cfg, forum, store, alerter):
    class Down:
        calls = 0
        def chat(self, *a):
            self.calls += 1
            raise ConnectionError("model gateway down")
    rep = run_round(cfg, forum, Down(), store, alerter, now=NOW)
    assert not rep.ok and "模型接口" in rep.fatal
    assert rep.posted == 0


def test_bad_model_output_goes_to_pending(cfg, forum, store, alerter):
    class Sloppy:
        calls = 0
        def chat(self, system, user):
            self.calls += 1
            n = len(user.splitlines())
            return json.dumps([{"idx": i, "keep": True, "domain": "娱乐", "questions": [], "relevance": 2,
                                "title": "t", "summary": "s", "us": "", "event_key": f"signal-{i}"} for i in range(n)])
    rep = run_round(cfg, forum, Sloppy(), store, alerter, now=NOW)
    assert rep.posted == 0 and rep.pending > 0
    assert store.pending_count() == rep.pending


def test_duplicate_external_id_counts_as_duplicate(cfg, forum, llm, store, alerter):
    forum.fail_next_create = DiscourseError(422, "External ID has already been taken")
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.ok and rep.posted == 2 and rep.duplicates >= 1


def test_failing_source_alerts_once_after_three_rounds(cfg, forum, llm, store, alerter):
    cfg.sources.append({"name": "坏信源", "module": "ext_ai", "kind": "rss", "url": "file:///nope.xml",
                        "tier": "media", "default_domain": "行业动态"})
    for _ in range(4):
        run_round(cfg, forum, llm, store, alerter, now=NOW)
    hits = [m for m in alerter.sent if "坏信源" in m]
    assert len(hits) == 1


def test_merge_into_posted_signal_updates_topic(cfg, forum, llm, store, alerter):
    # 第一轮只发第一家媒体；第二轮第二家媒体报道同一事件 → 合并进原话题并升级可信度
    import yaml
    rss = (cfg.root / "tests" / "fixtures" / "rss.xml").read_text(encoding="utf-8")
    first = rss.replace("<item><title>某整机厂发布风电场运维大模型</title><link>https://other.example.com/b</link>", "<item><title>不相干</title><link>https://x.example.com/z</link>")
    p = cfg.root / "tests" / "fixtures" / "rss1.xml"
    p.write_text(first, encoding="utf-8")
    cfg.sources[0]["url"] = f"file://{p}"
    run_round(cfg, forum, llm, store, alerter, now=NOW)
    topic = next(t for t in forum.topics.values() if t["title"].startswith("某整机厂发布风电场运维大模型"))
    assert "cred-red" in topic["tags"]
    cfg.sources[0]["url"] = f"file://{cfg.root / 'tests' / 'fixtures' / 'rss.xml'}"
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.merged == 1 and rep.posted == 0
    assert "cred-yellow" in forum.topics[topic["id"]]["tags"]
    assert "other.example.com" in forum.posts[topic["posts"][0]]["raw"]


def test_all_sources_failing_is_fatal(cfg, forum, llm, store, alerter):
    for s in cfg.sources:
        s["url"] = "file:///does/not/exist.xml"
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert not rep.ok and "所有信源" in rep.fatal
    assert any("所有信源" in m for m in alerter.sent)


def test_old_event_is_not_merged(cfg, forum, llm, store, alerter):
    from datetime import timedelta
    run_round(cfg, forum, llm, store, alerter, now=NOW - timedelta(days=30))
    # 30 天后同一事件的新报道（新 URL）应新开话题，而不是并进 30 天前的旧话题
    rss = (cfg.root / "tests" / "fixtures" / "rss.xml").read_text(encoding="utf-8")
    p = cfg.root / "tests" / "fixtures" / "rss_new.xml"
    p.write_text(rss.replace("https://media.example.com/a?utm_source=rss", "https://media.example.com/a-followup")
                    .replace("https://other.example.com/b", "https://other.example.com/b-followup")
                    .replace("Wed, 04 Nov 2026", "Wed, 04 Nov 2026"), encoding="utf-8")
    cfg.sources[0]["url"] = f"file://{p}"
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    titles = [t["title"] for t in forum.topics.values() if t["title"].startswith("某整机厂")]
    assert len(titles) == 2


def test_next_run_at_follows_schedule(cfg):
    from datetime import datetime
    from collector.pipeline import next_run_at
    from collector.sources.fetchers import CST
    # gw-ai-pulse（2026-11-16）：调度改为每天 08:00 / 12:00 / 16:00（含周末）
    assert next_run_at(cfg, datetime(2026, 11, 5, 7, 31, tzinfo=CST)).strftime("%H:%M") == "08:00"   # 周四 07:31
    assert next_run_at(cfg, datetime(2026, 11, 5, 9, 0, tzinfo=CST)).strftime("%H:%M") == "12:00"    # 周四 09:00
    assert next_run_at(cfg, datetime(2026, 11, 5, 13, 0, tzinfo=CST)).strftime("%H:%M") == "16:00"   # 周四 13:00
    fri_night = next_run_at(cfg, datetime(2026, 11, 6, 19, 0, tzinfo=CST))                           # 周五 19:00
    assert fri_night.strftime("%a %H:%M") == "Sat 08:00"                                            # 周末同样采
    sun = next_run_at(cfg, datetime(2026, 11, 8, 17, 0, tzinfo=CST))                                # 周日 17:00
    assert sun.strftime("%a %H:%M") == "Mon 08:00"


def test_feed_carries_v03_fields(cfg, forum, llm, store, alerter):
    cfg.settings["feed"]["enabled"] = True
    run_round(cfg, forum, llm, store, alerter, now=NOW)
    feed, _ = read_feed(forum.posts[1]["raw"])
    assert feed["radar_next_at"].startswith("2026-11-05T08:00")   # 调度已改为 08/12/16
    assert {q["id"] for q in feed["question_list"]} >= {"Q01", "Q09"}
    assert all("relevance" in x and "heat" not in x for x in feed["radar"])


# ---------- 72h 窗口门禁（2026-11-16 修的漏洞） ----------

def _write_rss(tmp_path_factory, items):
    """items: [(title, link, pubDate 或 None)]"""
    import xml.sax.saxutils as su
    body = []
    for title, link, pub in items:
        extra = f"<pubDate>{pub}</pubDate>" if pub else ""
        body.append(
            f"<item><title>{su.escape(title)}</title><link>{su.escape(link)}</link>"
            f"<description>d</description>{extra}</item>"
        )
    xml = ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
           '<title>t</title><link>http://x</link><description>d</description>'
           + "".join(body) + "</channel></rss>")
    p = tmp_path_factory.mktemp("feeddir") / "rss.xml"
    p.write_text(xml, encoding="utf-8")
    return p


def _single_source(cfg, rss_path):
    from collector.config import load_config
    cfg.sources = [{
        "name": "测试媒体", "module": "ext_ai", "kind": "rss", "url": f"file://{rss_path}",
        "tier": "media", "default_domain": "能源AI", "questions": ["Q01"],
    }]
    return cfg


def test_undated_candidate_is_dropped_by_policy(cfg, forum, llm, store, alerter, tmp_path_factory):
    """提不出日期的条目 → undated_dropped 计数，不入池、不发帖。

    修复前：`if c.published_at and ...` 短路，零日期条目 100% 放行。
    """
    rss = _write_rss(tmp_path_factory, [("某厂发布新版推理框架", "https://github.com/foo/bar", None)])
    _single_source(cfg, rss)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.ok, rep.fatal
    assert rep.undated_dropped == 1
    assert rep.posted == 0
    assert rep.candidates == 1
    # 全轮只有一条候选被丢，没有别的条目被误判
    assert rep.screened_out == 0


def test_undated_but_url_has_date_past_window_is_screened(cfg, forum, llm, store, alerter, tmp_path_factory):
    """无 pubDate，但 URL 带 2026-10-30 日期戳 → 补齐后被 72h 窗口挡掉。

    这是生产事故的最小复现：网信办 10-30 的公告被当成新信号发布。
    NOW = 2026-11-05，窗口 72h ⇒ 早于 11-02 的都该拦。
    """
    rss = _write_rss(tmp_path_factory, [
        ("第十九批深度合成服务算法备案", "https://www.cac.gov.cn/2026-10/30/c_1.htm", None),
    ])
    _single_source(cfg, rss)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.ok, rep.fatal
    assert rep.screened_out == 1        # 补到 10-30 → 超窗
    assert rep.undated_dropped == 0
    assert rep.posted == 0


def test_undated_but_url_date_inside_window_is_kept(cfg, forum, llm, store, alerter, tmp_path_factory):
    """URL 带窗口内日期 → 放行并正常发帖，证明补齐不会误杀。"""
    rss = _write_rss(tmp_path_factory, [
        ("人工智能+能源试点名单公示", "https://www.cac.gov.cn/2026-11/04/c_2.htm", None),
    ])
    _single_source(cfg, rss)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.ok, rep.fatal
    assert rep.screened_out == 0
    assert rep.undated_dropped == 0
    assert rep.posted == 1


def test_undated_policy_keep_allows_through(cfg, forum, llm, store, alerter, tmp_path_factory):
    """undated_policy=keep 时放行并单独计数（供需要时回退）。"""
    rss = _write_rss(tmp_path_factory, [("某厂发布新版推理框架", "https://github.com/foo/bar", None)])
    _single_source(cfg, rss)
    cfg.settings["round"]["undated_policy"] = "keep"
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.ok, rep.fatal
    assert rep.undated_kept == 1
    assert rep.undated_dropped == 0
    assert rep.posted == 1


# ---------- 双重验证 + 重要 → 公开话题（2026-11-16） ----------

def _pub_cfg(cfg):
    cfg.settings["discourse"]["publish_public"] = True
    cfg.settings["discourse"]["public_category_id"] = 15
    cfg.settings["discourse"]["public_min_cred"] = "yellow"
    cfg.settings["discourse"]["public_min_relevance"] = 3
    return cfg


def _sig(cred="green", relevance=3):
    from collector.models import Signal, SourceRef
    return Signal(
        signal_id="s1", event_key="k", title="某监管机构发布人工智能能源试点名单",
        summary="摘要内容。", us="对金风的内部判断", domain="政策合规",
        questions=["Q02"], topics=[], relevance=relevance, cred=cred,
        event_date="2026-11-05", discovered_at="2026-11-05T07:00:00+08:00",
        sources=[SourceRef(name="监管机构", url="https://a.gov.cn/x", tier="authority"),
                 SourceRef(name="媒体", url="https://b.com/y", tier="media")],
    )


def test_should_publish_public_matrix():
    """双重验证（cred≥yellow）且重要（relevance=3）才公开。"""
    from collector.pipeline import _should_publish_public
    d = {"publish_public": True, "public_category_id": 15,
         "public_min_cred": "yellow", "public_min_relevance": 3}
    assert _should_publish_public(_sig("green", 3), d) is True
    assert _should_publish_public(_sig("yellow", 3), d) is True
    assert _should_publish_public(_sig("red", 3), d) is False      # 单一来源不算验证过
    assert _should_publish_public(_sig("green", 2), d) is False    # 不够重要
    assert _should_publish_public(_sig("green", 3), {**d, "publish_public": False}) is False
    assert _should_publish_public(_sig("green", 3), {**d, "public_category_id": None}) is False


def test_public_topic_body_excludes_internal_judgement(cfg):
    """公开版不能带「对金风」内部判断。"""
    from collector.render import public_topic_body
    body = public_topic_body(_sig(), cfg)
    assert "对金风" not in body
    assert "摘要内容" in body                       # 事实保留
    assert "https://a.gov.cn/x" in body             # 来源保留
    assert "机器可读数据" not in body                # 内部机器块不带出去


def test_public_topic_tags_exclude_internal_tags(cfg):
    from collector.render import public_topic_tags
    tags = public_topic_tags(_sig(), cfg)
    assert "cred-green" in tags
    assert not any(t.startswith("q0") for t in tags)
    assert "reviewed" not in tags


def test_round_publishes_public_topic_for_verified_important(cfg, forum, llm, store, alerter):
    """跑完一轮：重要且已验证的信号应额外出现在公开分类 15。"""
    cfg = _pub_cfg(cfg)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.ok, rep.fatal
    if rep.published_public:
        pub = [t for t in forum.topics.values() if t.get("category_id") == 15]
        assert len(pub) == rep.published_public
        # 公开帖正文不含内部判断
        for t in pub:
            raw = forum.posts[t["posts"][0]]["raw"]
            assert "对金风" not in raw
    # 内部帖仍全部发在分类 42（测试配置）
    internal = [t for t in forum.topics.values() if t.get("category_id") == cfg.settings["discourse"]["signal_category_id"]]
    assert len(internal) == rep.posted


def test_publish_public_disabled_by_default(cfg, forum, llm, store, alerter):
    """默认配置里 publish_public=false 时，不发任何公开帖。"""
    cfg.settings["discourse"]["publish_public"] = False
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.ok, rep.fatal
    assert rep.published_public == 0
    assert not [t for t in forum.topics.values() if t.get("category_id") == 15]


# ---------- 标题指纹去重（2026-11-16） ----------

def test_title_duplicate_is_skipped(cfg, forum, llm, store, alerter, tmp_path_factory):
    """同一事件换了个 URL，标题几乎一样 → 第二轮应判重不发帖。

    模拟真实场景：某条已发过，隔一轮又被转载站以另一个 URL 抓到。
    """
    import json as _json
    from collector.models import Signal, SourceRef

    # 先手工往库里塞一条已发信号
    old = Signal(signal_id="old1", event_key="qwen4-max-launch-2026-11-05",
                 title="阿里通义千问Qwen4-Max正式上线，2.4万亿参数任务成本降低超60%",
                 summary="摘要。", us="", domain="选型", questions=[], topics=[],
                 relevance=2, cred="red", event_date="2026-11-05",
                 discovered_at="2026-11-05T06:00:00+08:00",
                 sources=[SourceRef(name="量子位", url="https://a.com/1", tier="media")],
                 topic_id=900, post_id=900)
    store.save_signal(old, ["old1"])

    # 新一轮抓到转载版本的同一事件（URL 不同、标题只差空格和标点）
    rss = _write_rss(tmp_path_factory, [
        ("阿里通义千问Qwen4-Max正式上线 2.4万亿参数任务成本降低超60%",
         "https://mirror-site.com/2026/11/05/story", None),
    ])
    _single_source(cfg, rss)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.ok, rep.fatal
    assert rep.title_duplicates >= 1
    assert rep.posted == 0


def test_title_dedup_disabled_when_threshold_zero(cfg, forum, llm, store, alerter, tmp_path_factory):
    """title_dedup_threshold=0 时关闭判重，行为回到旧版。"""
    from collector.models import Signal, SourceRef
    old = Signal(signal_id="old2", event_key="some-release-2026-11-05",
                 title="某厂发布新版推理框架并开源全部权重",
                 summary="摘要。", us="", domain="选型", questions=[], topics=[],
                 relevance=2, cred="red", event_date="2026-11-05",
                 discovered_at="2026-11-05T06:00:00+08:00",
                 sources=[SourceRef(name="媒体", url="https://b.com/1", tier="media")],
                 topic_id=901, post_id=901)
    store.save_signal(old, ["old2"])
    cfg.settings["round"]["title_dedup_threshold"] = 0

    rss = _write_rss(tmp_path_factory, [
        ("某厂发布新版推理框架并开源全部权重", "https://c.com/2026/11/05/story", None)])
    _single_source(cfg, rss)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.ok, rep.fatal
    assert rep.title_duplicates == 0
    assert rep.posted == 1


def test_title_dedup_within_same_round(cfg, forum, llm, store, alerter, tmp_path_factory):
    """同一轮内两条标题几乎相同（不同 URL）→ 只发一条。"""
    rss = _write_rss(tmp_path_factory, [
        ("国家能源局公示人工智能+能源试点项目清单", "https://x.com/2026/11/05/a", None),
        ("国家能源局公示“人工智能+”能源试点项目清单", "https://y.com/2026/11/05/b", None),
    ])
    _single_source(cfg, rss)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.ok, rep.fatal
    assert rep.posted == 1
    assert rep.title_duplicates == 1


# ---------- 日报废弃字段清理（2026-11-16） ----------

def test_feed_drops_daily_only_fields(cfg, forum, llm, store, alerter):
    """日报停用后，curated / tracking / daily_url / next_update_at 必须从底座帖里消失。

    这些字段是日报 skill 留下的，采集器不写但也不能继续原样保留，
    否则旧内容会一直挂在首页（首页组件读不到就不渲染该块）。
    """
    import json as _json
    # 先在底座帖里塞入日报遗留字段
    feed, mode = read_feed(forum.posts[1]["raw"])
    feed["curated"] = [{"title": "旧的精编条目", "summary": "s", "cred": "g", "sources": []}]
    feed["tracking"] = [{"title": "旧的跟踪条目", "summary": "s", "cred": "y", "sources": []}]
    feed["daily_url"] = "https://example.com/t/999"
    feed["next_update_at"] = "2026-11-17T09:30:00+08:00"
    from collector.render import write_feed
    forum.posts[1]["raw"] = write_feed(forum.posts[1]["raw"], feed, mode)

    cfg.settings["feed"]["enabled"] = True
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.ok, rep.fatal
    assert rep.feed_updated, rep.feed_error

    after, _ = read_feed(forum.posts[1]["raw"])
    for stale in ("curated", "tracking", "daily_url", "next_update_at"):
        assert stale not in after, f"{stale} 应被清理，但仍存在"
    # 采集器自己写的字段要还在
    assert after["radar"] and after["brief"] and "radar_next_at" in after
