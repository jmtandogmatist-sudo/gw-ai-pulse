"""社区帖子接入一轮采集的集成测试（2026-11-16）。

验证的是「AI 社区人为发布的内容」这条链路：
  论坛真实帖子 → 扫描（排除信号分类） → 原地打主题标签 → 写进 632 的 community / question
"""
import json

from collector.community import week_key
from collector.pipeline import run_round
from collector.render import read_feed

from conftest import NOW


def _topic(tid, title, *, cat, replies=0, days_ago=0, who="张工", slug=None):
    from datetime import timedelta
    created = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return {"id": tid, "title": title, "slug": slug or f"t{tid}", "category_id": cat,
            "posts_count": replies + 1, "like_count": 0, "views": 40,
            "created_at": created, "posters": [{"username": who}], "tags": []}


def _seed_community(forum):
    forum.category_rows = [
        {"id": 20, "name": "信号", "read_restricted": True},
        {"id": 18, "name": "深度阅读", "read_restricted": False},
        {"id": 16, "name": "金风实践", "read_restricted": False},
    ]
    forum.community_rows = [
        _topic(101, "功率预测上线三个月，我们踩过的坑", cat=18, replies=7, who="张工"),
        _topic(102, "用 Agent 做周报初稿的尝试", cat=16, replies=2, who="李四"),
        _topic(103, "信号分类里的帖子不该上社区", cat=20, replies=99, who="机器人"),
        _topic(104, "三个月前的老帖", cat=18, replies=50, days_ago=90, who="王五"),
    ]


def test_round_writes_community_block_from_forum(cfg, forum, llm, store, alerter):
    cfg.settings["feed"]["enabled"] = True
    cfg.settings["community"]["enabled"] = True
    _seed_community(forum)

    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.ok, rep.fatal
    # 扫描层不过滤时间：101 / 102 / 104 都进池（信号分类那条被排除）；
    # 7 天窗口是在「组装社区今日」时生效的，所以 104 不会出现在下面。
    assert rep.community_scanned == 3

    feed, _ = read_feed(forum.posts[1]["raw"])
    assert [c["title"] for c in feed["community"]] == [
        "功率预测上线三个月，我们踩过的坑",     # 回复 7，排第一
        "用 Agent 做周报初稿的尝试",            # 回复 2
    ]   # 104（回复 50）超出 7 天窗口，不显示
    assert feed["community"][0] == {
        "who": "张工",
        "title": "功率预测上线三个月，我们踩过的坑",
        "url": f"{forum.base}/t/t101/101",
    }


def test_round_writes_question_from_community(cfg, forum, llm, store, alerter):
    cfg.settings["feed"]["enabled"] = True
    cfg.settings["community"]["enabled"] = True
    _seed_community(forum)
    run_round(cfg, forum, llm, store, alerter, now=NOW)
    feed, _ = read_feed(forum.posts[1]["raw"])
    assert feed["question"] == "这周社区里讨论最多的做法，你们团队已经试过了吗？"
    assert feed["community_updated_at"]


def test_community_tags_recorded_for_weekly_count(cfg, forum, llm, store, alerter):
    cfg.settings["community"]["enabled"] = True
    _seed_community(forum)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.community_tagged == 3
    # 功率预测那条命中「功率预测 AI」，Agent 那条命中「智能体与 MCP 安全」
    assert store.week_tag_counts(week_key(NOW)) == {
        "topic-power-forecast": 1,
        "topic-agent-security": 1,
    }
    # 第二轮不再重复计数：标签只打一次，「本周 +N」不会每轮涨
    rep2 = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep2.community_tagged == 0
    assert store.week_tag_counts(week_key(NOW)) == {
        "topic-power-forecast": 1,
        "topic-agent-security": 1,
    }


def test_community_disabled_leaves_feed_untouched(cfg, forum, llm, store, alerter):
    cfg.settings["feed"]["enabled"] = True
    cfg.settings["community"]["enabled"] = False
    _seed_community(forum)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.community_scanned == 0
    feed, _ = read_feed(forum.posts[1]["raw"])
    # 关闭时不写也不删：底座帖里原本没有 community 就保持没有
    assert "community" not in feed and "community_updated_at" not in feed


def test_no_recent_posts_drops_community_block(cfg, forum, llm, store, alerter):
    """近 7 天没人发帖 → 整块不显示，而不是挂着旧内容。"""
    cfg.settings["feed"]["enabled"] = True
    cfg.settings["community"]["enabled"] = True
    forum.category_rows = [{"id": 18, "name": "深度阅读", "read_restricted": False}]
    forum.community_rows = [_topic(105, "很久以前", cat=18, replies=3, days_ago=60)]
    run_round(cfg, forum, llm, store, alerter, now=NOW)
    feed, _ = read_feed(forum.posts[1]["raw"])
    assert "community" not in feed


def test_radar_guard_not_confused_by_community_block(cfg, forum, llm, store, alerter):
    """回归：2026-11-16 实测踩过——社区块的局部变量叫 items，覆盖了雷达的 items，
    读回校验于是拿「雷达 29 条」比「社区 5 条」，每轮误报读回不一致，底座帖其实写对了。
    这里断言两者同时写入时 update_feed 不抛异常、块数各自正确。
    """
    cfg.settings["feed"]["enabled"] = True
    cfg.settings["community"]["enabled"] = True
    _seed_community(forum)
    rep = run_round(cfg, forum, llm, store, alerter, now=NOW)
    assert rep.feed_updated, rep.feed_error
    feed, _ = read_feed(forum.posts[1]["raw"])
    assert len(feed["radar"]) == 3          # 本轮发了 3 条信号
    assert len(feed["community"]) >= 1      # 社区块同时写入


def test_updated_at_tracks_refresh_not_stale_daily(cfg, forum, llm, store, alerter):
    """日报停用后 updated_at 没人写，会永远停在最后一期日报的时间，刊头「X 更新」失真。"""
    cfg.settings["feed"]["enabled"] = True
    run_round(cfg, forum, llm, store, alerter, now=NOW)
    after, _ = read_feed(forum.posts[1]["raw"])
    # 测试夹具原本没有 updated_at 字段；关键是刷新后它被写成「本轮时间」，
    # 而不是停在最后一期日报的时间上。
    assert after["updated_at"] == NOW.isoformat(timespec="seconds")


def test_header_date_tracks_current_round(cfg, forum, llm, store, alerter):
    """刊头 date / issue_date 也必须每轮刷新。

    回归：旧「每日精选 skill」每天写一次这两个字段，日报停用后没人写，于是首页顶部
    一直显示「2026年11月16日 · 星期一」，而下面几十条雷达早就是次日下午采的——
    看起来像整站停了。采集器接手后要和 updated_at 一样跟着本轮时间走。
    """
    cfg.settings["feed"]["enabled"] = True
    run_round(cfg, forum, llm, store, alerter, now=NOW)
    after, _ = read_feed(forum.posts[1]["raw"])
    assert after["issue_date"] == NOW.date().isoformat()
    assert after["date"].startswith(f"{NOW.year}年{NOW.month}月{NOW.day}日 · 星期")
