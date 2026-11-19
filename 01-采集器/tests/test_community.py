"""AI 社区帖子扫描、首页社区区块与今日一问的测试（2026-11-16）。"""
import json
from datetime import datetime, timedelta

import pytest

from collector.community import (build_question, community_items, pick_community,
                                scan_forum, tag_posts, week_key)
from collector.discourse import FakeDiscourse, DiscourseError
from collector.sources.fetchers import CST
from collector.store import Store

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=CST)


def _post(tid, title, *, cat=18, replies=0, likes=0, views=10, days_ago=0, who="u1"):
    created = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return {"id": tid, "title": title, "slug": f"t{tid}", "category_id": cat,
            "posts_count": replies + 1, "like_count": likes, "views": views,
            "created_at": created, "posters": [{"username": who}], "tags": []}


class _Forum(FakeDiscourse):
    def __init__(self, cats, rows):
        super().__init__(632, "x")
        self.category_rows = cats
        self.community_rows = rows


def test_scan_skips_signal_and_restricted_categories():
    """信号分类（20）与只读受限分类一律不扫：前者是采集器自己的产出，后者匿名读不到。"""
    cats = [{"id": 20, "name": "信号", "read_restricted": True},
            {"id": 3, "name": "管理人员", "read_restricted": True},
            {"id": 18, "name": "深度阅读", "read_restricted": False}]
    rows = [_post(1, "一篇深度阅读的帖子", cat=18),
            _post(2, "信号分类里的帖子", cat=20)]
    got = scan_forum(_Forum(cats, rows), cats, 20)
    assert [p.topic_id for p in got] == [1]


def test_scan_skips_collector_auto_topics():
    """采集器自己发的信号话题会镜像一份到公开分类 15，扫描时必须剔除。

    2026-11-17 生产实测：不剔除时「社区新帖」5 条里 3 条是采集器自己发的
    （正文是 [wrap=ais-signal] 格式），而且同一篇还同时挂在 03 信号雷达上。
    采集器的产出都带 auto 标签。
    """
    cats = [{"id": 15, "name": "趋势与情报", "read_restricted": False}]
    auto = _post(791, "采集器发的信号", cat=15, days_ago=0)
    auto["tags"] = ["auto", "cred-red", "tools"]
    human = _post(789, "同事发的实践帖", cat=15, days_ago=0)
    human["tags"] = ["agent", "dify"]
    got = scan_forum(_Forum(cats, [auto, human]), cats, 20)
    assert [p.topic_id for p in got] == [789]


def test_scan_exclude_tags_can_be_disabled():
    cats = [{"id": 15, "name": "趋势与情报", "read_restricted": False}]
    auto = _post(791, "采集器发的信号", cat=15)
    auto["tags"] = ["auto"]
    got = scan_forum(_Forum(cats, [auto]), cats, 20, exclude_tags=[])
    assert [p.topic_id for p in got] == [791]


def test_scan_url_and_author():
    cats = [{"id": 18, "name": "深度阅读", "read_restricted": False}]
    f = _Forum(cats, [_post(77, "金风实践分享", cat=18, who="张工")])
    got = scan_forum(f, cats, 20)
    assert got[0].url.endswith("/t/t77/77")
    assert got[0].who == "张工"


def test_scan_tolerates_category_failure():
    """单个分类取不到只跳过，不影响其余分类。"""
    class Boom(_Forum):
        def category_latest(self, category_id, limit=8):
            raise DiscourseError(500, "boom")
    cats = [{"id": 18, "name": "深度阅读", "read_restricted": False}]
    got = scan_forum(Boom(cats, []), cats, 20)
    assert got == []


def test_pick_community_sorts_by_replies_and_windows_by_days():
    posts = [
        _CommunityPost(1, "回复 3", replies=3, days_ago=1),
        _CommunityPost(2, "回复 9", replies=9, days_ago=2),
        _CommunityPost(3, "回复 5", replies=5, days_ago=1),
        _CommunityPost(4, "太旧了", replies=99, days_ago=30),
        _CommunityPost(5, "回复 0", replies=0, days_ago=3),
    ]
    got = pick_community(posts, limit=3, days=7, now=NOW)
    assert [p.title for p in got] == ["回复 9", "回复 5", "回复 3"]


def test_pick_community_order_new_sorts_by_time():
    """v0.4.6：「社区新帖」按发帖时间倒序，回复/浏览量不再参与排序。"""
    posts = [
        _CommunityPost(1, "三天前很多回复", replies=9, views=900, days_ago=3),
        _CommunityPost(2, "昨天零回复", replies=0, views=1, days_ago=1),
        _CommunityPost(3, "两天前两条回复", replies=2, views=50, days_ago=2),
        _CommunityPost(4, "太旧了", replies=99, days_ago=30),
    ]
    got = pick_community(posts, limit=3, days=7, now=NOW, order="new")
    assert [p.title for p in got] == ["昨天零回复", "两天前两条回复", "三天前很多回复"]
    # 默认仍是按热度，老行为不能被顺手改掉
    assert [p.title for p in pick_community(posts, limit=2, days=7, now=NOW)] == [
        "三天前很多回复",
        "两天前两条回复",
    ]


def test_community_items_shape_matches_component():
    """首页组件读 who / title / url 三个字段，多一个少一个都不行。"""
    posts = [_CommunityPost(1, "帖子标题", replies=2, days_ago=1, who="李四")]
    items = community_items(posts, limit=5, days=7, now=NOW)
    assert items == [{"who": "李四", "title": "帖子标题", "url": "https://x/t/1"}]


def test_week_key_is_calendar_week():
    assert week_key(datetime(2026, 11, 16, tzinfo=CST)) == "2026-W47"
    assert week_key(datetime(2026, 11, 22, tzinfo=CST)) == "2026-W47"   # 周日仍属同一周
    assert week_key(datetime(2026, 11, 23, tzinfo=CST)) == "2026-W48"


class _TagLLM:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = 0

    def chat(self, system, user):
        self.calls += 1
        out = []
        for line in user.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                return "「这个做法你们团队试过吗？」"
            out.append({"idx": item["idx"], "topics": self.mapping.get(item["title"], []), "relevance": 2})
        return json.dumps(out, ensure_ascii=False)


def test_tag_posts_records_and_does_not_duplicate(cfg):
    store = Store(":memory:")
    posts = [_CommunityPost(1, "功率预测做到 95%", replies=1), _CommunityPost(2, "闲聊", replies=0)]
    llm = _TagLLM({"功率预测做到 95%": ["功率预测 AI"]})
    stat = tag_posts(posts, cfg, llm, store, now=NOW)
    assert stat["newly_tagged"] == 2
    assert stat["tagged"] == {"topic-power-forecast": 1}
    assert store.week_tag_counts(week_key(NOW)) == {"topic-power-forecast": 1}
    # 第二轮同一批帖子已经打过标签，不再调模型、不再计数
    before = llm.calls
    stat2 = tag_posts(posts, cfg, llm, store, now=NOW)
    assert stat2["newly_tagged"] == 0
    assert llm.calls == before
    assert store.week_tag_counts(week_key(NOW)) == {"topic-power-forecast": 1}


def test_tag_posts_survives_model_failure(cfg):
    store = Store(":memory:")

    class Boom:
        calls = 0

        def chat(self, system, user):
            raise RuntimeError("模型挂了")

    stat = tag_posts([_CommunityPost(1, "标题", replies=0)], cfg, Boom(), store, now=NOW)
    assert stat["newly_tagged"] == 0      # 不崩，下轮再试
    assert not store.post_seen(1)


def test_build_question_uses_model_output():
    posts = [_CommunityPost(1, "智能体上线了", replies=5)]

    class Q:
        def chat(self, system, user):
            return "「智能体上线了」之后，谁负责兜底？"

    assert build_question(posts, Q()) == "「智能体上线了」之后，谁负责兜底？"


def test_build_question_falls_back_when_model_garbage():
    posts = [_CommunityPost(1, "智能体上线了", replies=5)]

    class Q:
        def chat(self, system, user):
            return "   "

    q = build_question(posts, Q())
    assert "智能体上线了" in q and q.endswith("？")


def test_build_question_empty_pool_returns_empty():
    assert build_question([], _TagLLM({})) == ""


def _CommunityPost(tid, title, *, replies=0, days_ago=0, who="u", views=0, likes=0):
    from collector.community import CommunityPost
    created = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return CommunityPost(topic_id=tid, title=title, url=f"https://x/t/{tid}",
                         who=who, replies=replies, views=views, likes=likes, created_at=created)


# ---------- 日报话题与分类说明帖的排除（2026-11-16 生产实测补的） ----------

def test_scan_excludes_daily_report_topics():
    """日报已停用，但历史话题仍在分类 15 里，两种标题形态都要排除。"""
    cats = [{"id": 15, "name": "趋势与情报", "read_restricted": False}]
    rows = [
        {"id": 1, "title": "本地推理栈熬到 1.0，一次删 35 个接口 |AI雷达11.16", "slug": "a", "category_id": 15,
         "posts_count": 1, "created_at": "2026-11-16T01:00:00Z", "posters": [{"username": "U8570"}]},
        {"id": 2, "title": "AI 雷达日报｜11.9", "slug": "b", "category_id": 15,
         "posts_count": 1, "created_at": "2026-11-09T01:00:00Z", "posters": [{"username": "U8570"}]},
        {"id": 3, "title": "一篇真正的社区帖子", "slug": "c", "category_id": 15,
         "posts_count": 1, "created_at": "2026-11-15T01:00:00Z", "posters": [{"username": "张工"}]},
    ]
    got = scan_forum(_Forum(cats, rows), cats, 20)
    assert [p.topic_id for p in got] == [3]


def test_scan_excludes_about_category_topic():
    """Discourse 每个分类自带「关于“xx”类别」说明帖，不是成员内容。"""
    cats = [{"id": 18, "name": "深度阅读", "read_restricted": False}]
    rows = [
        {"id": 1, "title": "关于“深度阅读”类别", "slug": "a", "category_id": 18,
         "posts_count": 1, "created_at": "2026-11-15T01:00:00Z", "posters": [{"username": "系统"}]},
        {"id": 2, "title": "真正的深度阅读帖", "slug": "b", "category_id": 18,
         "posts_count": 1, "created_at": "2026-11-15T01:00:00Z", "posters": [{"username": "张工"}]},
    ]
    got = scan_forum(_Forum(cats, rows), cats, 20)
    assert [p.topic_id for p in got] == [2]


def test_pick_community_falls_back_to_views_when_no_replies():
    """生产实测：社区帖子回复数普遍为 0，此时要靠浏览量区分，否则退化成纯按时间排序。"""
    posts = [
        _CommunityPost(1, "刚发没人看", replies=0, days_ago=1),
        _CommunityPost(2, "热帖", replies=0, views=1104, days_ago=3),
        _CommunityPost(3, "一般", replies=0, views=200, days_ago=2),
    ]
    got = pick_community(posts, limit=3, days=7, now=NOW)
    assert [p.title for p in got] == ["热帖", "一般", "刚发没人看"]
