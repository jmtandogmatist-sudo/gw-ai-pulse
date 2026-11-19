"""01/02/页头/picks 的自动生成逻辑（2026-11-16 日报停用后改由采集器产出）。"""
from datetime import datetime

import pytest

from collector.models import Signal, SourceRef
from collector.render import (BRIEF_MAX, MAINLINE_LIMIT, PICKS_MAX, brief_items,
                              _is_tool_like, energy_items, lead_item, mainline_text,
                              pick_energy, pick_items, today_label)


# mk() 造的信号日期写死在这个时刻；凡是走「今日/日分组」逻辑的函数，
# 测试里必须显式传 now=FIXED_NOW，否则断言会随真实日期漂移。
FIXED_NOW = datetime(2026, 11, 16, 12, 0)


def mk(title, *, relevance=2, domain="选型", topics=None, cred="red",
       event_date="2026-11-16", summary="这是摘要第一句。第二句。", questions=None):
    return Signal(
        signal_id=title[:8], event_key="k-" + title[:6], title=title, summary=summary,
        us="对金风判断", domain=domain, questions=questions or ["Q03"], topics=topics or [],
        relevance=relevance, cred=cred, event_date=event_date,
        discovered_at=f"{event_date}T09:00:00+08:00",
        sources=[SourceRef(name="某媒体", url="https://x.com/2026/11/16/a", tier="media")],
        topic_id=700,
    )


# ---------- mainline ----------

def test_pick_energy_uses_business_questions_and_domain():
    """02 右栏「金风/能源相关」：按 Q01/Q02/Q09 或 domain=能源AI 筛，保持传入顺序。"""
    q09 = mk("数据中心用电推高风电需求", questions=["Q09"])
    q02 = mk("人工智能+ 能源政策试点", questions=["Q02"])
    energy = mk("风场功率预测模型更新", questions=[], domain="能源AI")
    other = mk("某模型降价", questions=["Q03"], domain="选型")
    got = pick_energy([other, q09, q02, energy], limit=3)
    assert [s.title for s in got] == [
        "数据中心用电推高风电需求", "人工智能+ 能源政策试点", "风场功率预测模型更新"]
    assert pick_energy([other], limit=3) == []


def test_energy_items_are_slim_and_have_required_fields():
    """02 右栏条目只要 6 个字段：底座帖正文有 32000 字符上限，不能塞整张雷达卡片。"""
    s = mk("风电占比提升降低电价", questions=["Q09"], domain="能源AI")
    s.topic_id = 800
    got = energy_items([s], None, "https://x.example.com/", limit=3)
    assert set(got[0]) == {"title", "cat", "from", "url", "topic_url", "when"}
    assert got[0]["topic_url"] == "https://x.example.com/t/800"
    assert got[0]["cat"] == "能源AI"


def test_today_label_is_chinese_weekday():
    """刊头日期：日报停用后采集器每轮写，格式必须和旧值一致（2026年11月16日 · 星期一）。"""
    from datetime import datetime
    assert today_label(datetime(2026, 11, 16, 9, 0)) == "2026年11月16日 · 星期一"
    assert today_label(datetime(2026, 11, 17, 16, 12)) == "2026年11月17日 · 星期二"
    assert today_label(datetime(2026, 11, 22, 8, 0)) == "2026年11月22日 · 星期日"


def test_mainline_uses_top_two():
    # 必须传固定的 now：mk() 的信号日期写死 2026-11-16，靠默认 datetime.now() 的话
    # 这条测试会随真实日期漂移（2026-11-19 实测：信号变成 3 天前，日分组降级后
    # 排序只剩标题码位，断言就挂了）。
    sigs = [mk("重要事件甲", relevance=3), mk("重要事件乙", relevance=3), mk("次要", relevance=2)]
    text = mainline_text(sigs, now=FIXED_NOW)
    assert "重要事件甲" in text
    assert "；" in text


def test_mainline_respects_limit():
    sigs = [mk("很长的标题" * 12, relevance=3), mk("另一个很长的标题" * 12, relevance=3)]
    assert len(mainline_text(sigs)) <= MAINLINE_LIMIT


def test_mainline_empty_when_no_signals():
    assert mainline_text([]) == ""


# ---------- picks ----------

@pytest.mark.parametrize("title,expect", [
    ("阿里开源混合架构代码审查工具", True),        # 工具词
    ("腾讯开源LoopForge工作流编排", True),         # 工具词
    ("DeepSeek开放全家桶API", True),               # API 也算工具
    ("Hacker News社区热议某个模型", False),        # 讨论帖排除
    ("倡导团体起诉AI巨头", False),                 # 诉讼排除
    ("国家网信办公示备案清单", False),              # 政策排除
])
def test_is_tool_like(title, expect):
    assert _is_tool_like(mk(title)) is expect


def test_picks_returns_at_most_four():
    sigs = [mk(f"开源工具{i}", relevance=3) for i in range(10)]
    assert len(pick_items(sigs)) == PICKS_MAX


def test_picks_empty_when_nothing_suitable():
    sigs = [mk("国家能源局公示试点名单"), mk("倡导团体起诉AI巨头")]
    assert pick_items(sigs) == []


def test_picks_have_required_fields():
    p = pick_items([mk("阿里开源代码审查工具", relevance=3)])[0]
    assert p["kind"] == "做法"
    assert p["title"] and p["url"]
    assert len(p["summary"]) <= 100


# ---------- brief / lead ----------

def test_brief_caps_at_three():
    sigs = [mk(f"事件{i}", relevance=3) for i in range(6)]
    assert len(brief_items(sigs)) == BRIEF_MAX


def test_brief_text_has_title_and_first_sentence():
    b = brief_items([mk("某厂发布新框架")])[0]
    assert "某厂发布新框架" in b["text"]
    assert b["source"] == "某媒体"


def test_lead_is_highest_ranked():
    sigs = [mk("次要新闻", relevance=2), mk("重要新闻", relevance=3)]
    lead = lead_item(sigs, now=FIXED_NOW)     # 同上：不能依赖真实日期
    assert lead["title"] == "重要新闻"
    assert lead["topic_id"] == 700


def test_lead_none_on_empty():
    assert lead_item([]) is None

# ---------- 时效降级（2026-11-17） ----------
# 背景：01 THE BRIEF 区块名叫「今天发生了什么」，实际却长期显示前天的内容。
# 原因不是调度失败，而是 _brief_rank 把 relevance 档位当成了**主关键字**：
# 全库只有 3 条 relevance=3，且都是 1~3 天前的，今天新采的 4 条全是 relevance=2，
# 永远挤不进前 3。修法是给档位加时效降级，用「今天」压住「要紧但陈旧」。

from datetime import datetime  # noqa: E402

NOW = datetime(2026, 11, 17, 14, 0, 0)


def test_todays_item_beats_stale_important_one():
    """今天的 relevance=2 必须压过前天的 relevance=3。"""
    stale = mk("前天的大政策", relevance=3, event_date="2026-11-15")
    fresh = mk("今天发生的事", relevance=2, event_date="2026-11-17")
    lead = lead_item([stale, fresh], now=NOW)
    assert lead["title"] == "今天发生的事"


def test_yesterday_ranked_between_fresh_and_stale():
    """昨天的降 1 档：仍能进榜，但排在今天的同类之前，早于前天的。"""
    titles = [x["text"].split("：")[0] for x in brief_items(
        [mk("前天", relevance=2, event_date="2026-11-15"),
         mk("昨天", relevance=2, event_date="2026-11-16"),
         mk("今天", relevance=2, event_date="2026-11-17")], now=NOW)]
    assert titles == ["今天", "昨天", "前天"]


def test_stale_item_survives_when_nothing_fresh():
    """断电/周末后没有今日信号时，不能把 01 清空——旧条目照常上榜。"""
    sigs = [mk(f"前天事件{i}", relevance=2, event_date="2026-11-15") for i in range(3)]
    assert len(brief_items(sigs, now=NOW)) == 3


def test_unknown_date_sinks_to_bottom():
    """event_date 缺失/非法时按极旧处理，不占 01/02。"""
    bad = mk("日期缺失", relevance=3, event_date="")
    bad.discovered_at = ""
    good = mk("今天发生的事", relevance=2, event_date="2026-11-17")
    assert lead_item([bad, good], now=NOW)["title"] == "今天发生的事"


def test_aging_does_not_change_order_within_same_day():
    """同一天的多条，仍按相关度/可信度/日期排序，时效降级不干扰。"""
    a = mk("今日要闻", relevance=3, event_date="2026-11-17", cred="green")
    b = mk("今日速递", relevance=2, event_date="2026-11-17", cred="green")
    assert lead_item([b, a], now=NOW)["title"] == "今日要闻"

# ---------- 底座帖字符预算（2026-11-17） ----------
# 事故：632 的 JSON 包涨到 33950 字符，Discourse 硬上限 32000 → 整篇写入 422，
# 首页一整天不刷新，而采集器记 feed_updated=false 看起来像「跑成功了」。
# _fit_feed 是最后一道闸：无论数据怎么长，写入都不能因为超长而失败。

from collector.render import POST_CHAR_BUDGET, _fit_feed  # noqa: E402


def _big_radar(n, each=600):
    return [{"cat": "安全", "cred": "r", "from": "某媒体", "title": f"事件{i}",
             "summary": "摘" * (each - 100), "url": "https://x.com/a", "tags": [],
             "relevance": 2, "published_at": "2026-11-17", "topic_url": "",
             "review": "auto", "questions": [], "us": ""} for i in range(n)]


def test_small_feed_untouched():
    feed = {"radar": _big_radar(2, 100), "brief": []}
    assert len(_fit_feed(feed)) <= POST_CHAR_BUDGET
    assert len(feed["radar"]) == 2, "没超预算就不该动数据"


def test_oversized_feed_is_shrunk_under_budget():
    feed = {"radar": _big_radar(120)}
    body = _fit_feed(feed)
    assert len(body) <= POST_CHAR_BUDGET, f"收缩后仍超预算：{len(body)}"
    assert 0 < len(feed["radar"]) < 120, "应该砍掉一部分而不是全砍或不动"


def test_shrink_keeps_head_drops_tail():
    """砍的必须是最旧/最不相关的那批——即尾部，不能动头部。"""
    feed = {"radar": _big_radar(120)}
    before = [x["title"] for x in feed["radar"]]
    _fit_feed(feed)
    after = [x["title"] for x in feed["radar"]]
    assert after == before[: len(after)]


def test_empty_radar_does_not_crash():
    feed = {"radar": [], "brief": [{"text": "x"}]}
    assert _fit_feed(feed)


def test_shrunk_body_is_valid_json():
    import json
    feed = {"radar": _big_radar(120)}
    parsed = json.loads(_fit_feed(feed))
    assert isinstance(parsed["radar"], list)

# ---------- 读回校验基准（2026-11-17） ----------
# 踩过：write_feed 超预算会把 radar 从 40 收缩到 37，写入是成功的，
# 但 update_feed 拿收缩前的 items（40）去比对读回的 37 → 误报失败并触发告警。

def test_fit_feed_mutates_radar_in_place():
    """_fit_feed 收缩后必须把结果写回 feed，读回校验才能拿到真实条数。"""
    feed = {"radar": _big_radar(120)}
    _fit_feed(feed)
    assert len(feed["radar"]) < 120, "收缩结果没有回写 feed，读回校验会误判"


def test_fit_feed_no_shrink_leaves_radar_intact():
    feed = {"radar": _big_radar(3, 100)}
    before = len(feed["radar"])
    _fit_feed(feed)
    assert len(feed["radar"]) == before

# ---------- 信号雷达排序（2026-11-17） ----------
# 用户要求：时间最新(权重5) > 信息源级别(3) > 相关度(2)。
# 实现成「日分组优先 + 组内 3:2 加权」而不是加权求和，原因见 render.radar_rank 注释：
# 加权求和会让「昨天绿标+相关3」(8.33) 压过「今天红标+相关2」(6.33)，
# 与「时间最新」相悖——3+2=5 的合计权重刚好抵消一天的时效差。

from collector.render import CRED_LEVEL, _day_bucket, radar_rank  # noqa: E402

NOW = datetime(2026, 11, 17, 16, 0, 0)


def rs(title, *, day="2026-11-17", cred="red", relevance=2, published_at=None):
    s = mk(title, relevance=relevance, cred=cred, event_date=day)
    s.published_at = published_at
    return s


def order(sigs):
    return [s.title for s in sorted(sigs, key=lambda s: radar_rank(s, NOW))]


def test_today_beats_yesterday_even_if_yesterday_is_better():
    """核心诉求：今天的普通条目必须排在昨天的优质条目前面。"""
    stale = rs("昨天官方重磅", day="2026-11-16", cred="green", relevance=3)
    fresh = rs("今天单一来源", day="2026-11-17", cred="red", relevance=2)
    assert order([stale, fresh]) == ["今天单一来源", "昨天官方重磅"]


def test_within_day_green_beats_red():
    a = rs("绿标", cred="green")
    b = rs("红标", cred="red")
    assert order([b, a]) == ["绿标", "红标"]


def test_within_day_same_source_relevance3_first():
    a = rs("相关3", cred="red", relevance=3)
    b = rs("相关2", cred="red", relevance=2)
    assert order([b, a]) == ["相关3", "相关2"]


def test_source_level_outranks_relevance_within_day():
    """信息源权重3 > 相关度权重2：黄标相关2 要压过 红标相关3。"""
    yellow2 = rs("黄标相关2", cred="yellow", relevance=2)
    red3 = rs("红标相关3", cred="red", relevance=3)
    assert order([red3, yellow2]) == ["黄标相关2", "红标相关3"]


def test_full_timestamp_orders_within_same_day():
    """同一天同档位，带时分时新的在前（published_at 补齐后才生效）。"""
    early = rs("上午", published_at="2026-11-17T09:00:00+08:00")
    late = rs("下午", published_at="2026-11-17T15:00:00+08:00")
    assert order([early, late]) == ["下午", "上午"]


def test_undated_sinks_to_bottom():
    bad = rs("日期无法解析", day="")
    good = rs("今天", day="2026-11-17")
    assert order([bad, good]) == ["今天", "日期无法解析"]


def test_day_bucket_boundaries():
    assert _day_bucket("2026-11-17", NOW) == 0
    assert _day_bucket("2026-11-16", NOW) == 1
    assert _day_bucket("2026-11-14", NOW) == 3
    assert _day_bucket("", NOW) == 9999
    assert _day_bucket("2026-11-20", NOW) == 9999   # 未来日期按异常处理


def test_cred_level_covers_all_three():
    assert CRED_LEVEL == {"green": 1.0, "yellow": 0.5, "red": 0.0}


def test_sort_is_stable_across_rounds():
    """同一批数据反复排序结果必须一致（否则每轮刷新顺序会跳动）。"""
    sigs = [rs(f"事件{i}", cred=["green", "yellow", "red"][i % 3]) for i in range(9)]
    assert order(sigs) == order(list(reversed(sigs)))
