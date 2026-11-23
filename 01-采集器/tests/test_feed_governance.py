"""首页与发帖治理回归测试（2026-11-20）。

锁定四项生产运营修复：
1. 01「今日速览」优先行业/厂商/能源新闻，不被 arXiv 论文靠 relevance=3 抢占头条；
2. 02 右栏「金风/能源相关」只收真正能源/风电/电力政策与场景，拒绝仅顺手标了 Q01 的泛 AI 新闻；
3. 今日焦点（focus）排除学术论文，并在抓正文/调 LLM 前先按首页 radar_rank 与现有焦点比稿，
   打不过就不白花成本；
4. 论文发帖闸门（max_paper_posts_per_round + paper_window_hours + paper_min_relevance）与
   03 信号雷达论文上限（radar_max_papers）对齐，避免旧论文发完即被首页截断。
"""
from datetime import datetime, timedelta, timezone

from collector import focus
from collector.models import Candidate, Signal, SourceRef
from collector.pipeline import run_round, update_feed
from collector.render import brief_signals, pick_energy, read_feed
from collector.store import Store

from conftest import NOW


CST = timezone(timedelta(hours=8))
FIXED_NOW = datetime(2026, 11, 20, 16, 30, tzinfo=CST)


def _sig(title, *, relevance=2, domain="选型", cred="red", tier="media",
         event_date="2026-11-20", questions=None, topic_id=900,
         focus_flag=False, source_name="IT之家 · AI", url=None):
    return Signal(
        signal_id=f"sig-{topic_id}-{title[:6]}",
        event_key=f"k-{topic_id}-{title[:6]}",
        title=title,
        summary=f"{title} 的核心摘要内容。",
        us="对金风的影响判断",
        domain=domain,
        questions=questions if questions is not None else ["Q03"],
        topics=[],
        relevance=relevance,
        cred=cred,
        event_date=event_date,
        published_at=f"{event_date}T10:00:00+08:00",
        discovered_at=f"{event_date}T12:00:00+08:00",
        sources=[SourceRef(
            name=source_name,
            url=url or f"https://example.com/{topic_id}",
            tier=tier,
        )],
        topic_id=topic_id,
        focus=focus_flag,
        focus_deck="导语：这是一个焦点事件。" if focus_flag else "",
        focus_points=["要点一", "要点二"] if focus_flag else [],
    )


def test_brief_demotes_research_papers_below_industry_news():
    paper = _sig("基于自蒸馏记忆机制优化LLM智能体", relevance=3, domain="前沿研究",
                 tier="research", source_name="arXiv cs.AI / cs.CL", topic_id=903)
    vendor = _sig("腾讯混元 Hy4 大模型正式开源", relevance=2, domain="选型",
                  cred="green", tier="official", source_name="GitHub · Tencent", topic_id=912)
    policy = _sig("《中国虚拟电厂发展报告（2026）》解读", relevance=2, domain="政策合规",
                  cred="green", tier="official", event_date="2026-11-19",
                  questions=["Q02"], source_name="国家能源局", topic_id=866)
    media = _sig("英伟达拟收购扩散式大模型公司", relevance=2, domain="选型",
                 cred="red", tier="media", topic_id=920)

    got = brief_signals([paper, vendor, policy, media], limit=3, now=FIXED_NOW)
    assert paper not in got
    assert [s.topic_id for s in got] == [912, 866, 920]

    # 新闻不足 3 条时，论文仍可自动补位，避免 01 开天窗
    fallback = brief_signals([paper, vendor], limit=3, now=FIXED_NOW)
    assert [s.topic_id for s in fallback] == [912, 903]


def test_pick_energy_rejects_generic_ai_with_q01_and_keeps_true_energy():
    nvidia = _sig("英伟达拟以超5亿美元收购扩散式大模型公司Inception",
                  domain="选型", questions=["Q01", "Q03"], topic_id=920)
    robot = _sig("AgentRoboCritic：前沿智能体直接控制机器人的动作级失效分析",
                 domain="前沿研究", tier="research", questions=["Q01", "Q04"],
                 source_name="arXiv cs.AI / cs.CL", topic_id=924)
    green_power = _sig("绿色电力计量人工智能创新应用大赛决赛举行",
                       domain="能源AI", questions=["Q01"], topic_id=911)
    storage_policy = _sig("解读《“十五五”新型储能实施方案》",
                          domain="政策合规", questions=["Q02"], topic_id=913)
    wind_market = _sig("GWEC预测中东北非2035年陆上风电装机达40.5GW",
                       domain="行业动态", questions=["Q09"],
                       source_name="GWEC 全球风能理事会", topic_id=863)

    got = pick_energy([nvidia, robot, green_power, storage_policy, wind_market], limit=3)
    assert [s.topic_id for s in got] == [911, 913, 863]


def test_focus_skips_research_and_weaker_candidates_than_current_focus(cfg, store):
    cfg.settings["focus"] = {
        "enabled": True,
        "allow_research": False,
        "recency_hours": 24,
        "max_tries": 3,
    }
    fetched = []

    def boom_fetch(url):
        fetched.append(url)
        return "正文" * 200

    # 1. 论文被直接排除，不会触发正文抓取
    paper = _sig("某 arXiv 论文", relevance=3, domain="前沿研究",
                 tier="research", source_name="arXiv cs.AI / cs.CL", topic_id=903)
    out_paper = focus.build([paper], cfg, llm=None, store=store, now=FIXED_NOW, fetch=boom_fetch)
    assert out_paper.sig is None
    assert fetched == []

    # 2. 首页已有今日 yellow 焦点，本轮仅有今日 red 候选 → 比稿落败，直接跳过不抓正文
    current = _sig("Claude Code 更新", relevance=2, domain="工具做法",
                   cred="yellow", tier="official", focus_flag=True, topic_id=882)
    weaker = _sig("英伟达拟收购 Inception", relevance=2, domain="选型",
                  cred="red", tier="media", topic_id=920)
    out_weak = focus.build([weaker], cfg, llm=None, store=store, now=FIXED_NOW,
                           fetch=boom_fetch, current_focus=current)
    assert out_weak.sig is None
    assert "不高于当前焦点" in out_weak.reason
    assert fetched == []

    # 3. 本轮有今日 green 官方发布（排名高于当前 yellow 焦点）→ 通过比稿进入候选
    stronger = _sig("腾讯混元 Hy4 开源", relevance=2, domain="选型",
                    cred="green", tier="official", topic_id=912)
    cands = focus.pick([weaker, stronger], cfg, store, FIXED_NOW, current_focus=current)
    assert [c.topic_id for c in cands] == [912]


def test_paper_gate_enforces_cap_window_and_min_relevance(cfg, forum, llm, store, alerter, monkeypatch):
    cfg.settings["round"]["max_paper_posts_per_round"] = 2
    cfg.settings["round"]["paper_window_hours"] = 36
    cfg.settings["round"]["paper_min_relevance"] = 3
    cfg.settings["focus"]["enabled"] = False
    cfg.settings["structured_summary"] = {"enabled": False}

    cands = [
        # 今日 rel=3 论文 × 3（只应留 2 条，标题各不相同避免触发标题去重）
        Candidate(module="ext_ai", source_name="arXiv", source_tier="research",
                  url="https://arxiv.org/abs/2611.0001", title="分布式风电叶片结冰视觉检测方法研究", excerpt="摘要", published_at="2026-11-20T09:00:00+08:00"),
        Candidate(module="ext_ai", source_name="arXiv", source_tier="research",
                  url="https://arxiv.org/abs/2611.0002", title="多智能体工具编排中的沙箱隔离基准", excerpt="摘要", published_at="2026-11-20T08:00:00+08:00"),
        Candidate(module="ext_ai", source_name="arXiv", source_tier="research",
                  url="https://arxiv.org/abs/2611.0003", title="超长上下文注意力稀疏量化压缩技术", excerpt="摘要", published_at="2026-11-20T07:00:00+08:00"),
        # 2 天前旧论文 rel=3（超过 36h 论文窗口，应被拦掉）
        Candidate(module="ext_ai", source_name="arXiv", source_tier="research",
                  url="https://arxiv.org/abs/2611.0004", title="前天发布的图神经网络工业诊断综述", excerpt="摘要", published_at="2026-11-18T09:00:00+08:00"),
        # 今日 rel=2 论文（低于 paper_min_relevance=3，应被拦掉）
        Candidate(module="ext_ai", source_name="arXiv", source_tier="research",
                  url="https://arxiv.org/abs/2611.0005", title="语音情感识别轻量化蒸馏实验报告", excerpt="摘要", published_at="2026-11-20T10:00:00+08:00"),
        # 普通行业新闻 rel=2（不受论文闸门影响，正常发帖）
        Candidate(module="ext_ai", source_name="测试媒体", source_tier="media",
                  url="https://news.example/1", title="国家能源局发布新型储能示范工程名单", excerpt="摘要", published_at="2026-11-20T10:00:00+08:00"),
    ]
    cfg.sources = [
        {"name": "arXiv", "module": "ext_ai", "kind": "rss", "url": "file:///dev/null", "tier": "research"},
        {"name": "测试媒体", "module": "ext_ai", "kind": "rss", "url": "file:///dev/null", "tier": "media"},
    ]
    monkeypatch.setattr("collector.pipeline.fetch",
                        lambda src, *a, **kw: [c for c in cands if c.source_name == src["name"]])

    def fake_tag(batch, _cfg, _llm, **_kw):
        from collector.tagger import TagResult
        out = []
        for c in batch:
            rel = 2 if c.title in ("语音情感识别轻量化蒸馏实验报告", "国家能源局发布新型储能示范工程名单") else 3
            dom = "前沿研究" if c.source_tier == "research" else "选型"
            out.append(TagResult(
                keep=True, title=c.title, summary=c.excerpt, us="影响",
                domain=dom, questions=["Q03"], topics=[], relevance=rel,
                event_key=f"ev-{c.url}", event_date=c.published_at[:10],
            ))
        return out

    monkeypatch.setattr("collector.pipeline.tag_candidates", fake_tag)
    rep = run_round(cfg, forum, llm, store, alerter, now=FIXED_NOW)
    assert rep.ok, rep.fatal
    assert rep.paper_posted == 2
    assert rep.posted == 3  # 2 篇今日 rel=3 论文 + 1 条行业新闻
    posted_titles = [t["title"] for tid, t in forum.topics.items() if tid != 632]
    assert "国家能源局发布新型储能示范工程名单" in posted_titles
    assert "前天发布的图神经网络工业诊断综述" not in posted_titles
    assert "语音情感识别轻量化蒸馏实验报告" not in posted_titles


def test_update_feed_caps_radar_papers_and_ignores_paper_focus(cfg, forum, store):
    cfg.settings["feed"]["enabled"] = True
    cfg.settings["feed"]["radar_max_papers"] = 2
    cfg.settings["feed"]["max_items"] = 10

    sigs = [
        _sig("论文焦点", relevance=3, domain="前沿研究", tier="research",
             focus_flag=True, source_name="arXiv", topic_id=901),
        _sig("论文二", relevance=3, domain="前沿研究", tier="research",
             source_name="arXiv", topic_id=902),
        _sig("论文三", relevance=3, domain="前沿研究", tier="research",
             source_name="arXiv", topic_id=903),
        _sig("厂商新闻", relevance=2, domain="选型", cred="yellow",
             tier="official", source_name="OpenAI", topic_id=904),
    ]
    for s in sigs:
        store.save_signal(s, [s.signal_id])

    update_feed(cfg, forum, store, FIXED_NOW)
    raw = forum.post_raw(forum.first_post_id(cfg.settings["feed"]["topic_id"]))
    feed, _ = read_feed(raw)

    radar_titles = [x["title"] for x in feed["radar"]]
    assert len([x for x in feed["radar"] if x["cat"] == "前沿研究"]) == 2
    assert "厂商新闻" in radar_titles
    # 论文焦点不能占 02 今日焦点，02 应落到厂商新闻
    assert feed["lead"]["title"] == "厂商新闻"


# ---------- 2026-11-23 回归：今日值得看时效与误杀、雷达/能源/焦点防单一媒体垄断、05/06 退场 ----------

def test_in_publish_window_enforces_exact_timestamp_when_present():
    from collector.pipeline import _in_publish_window

    since = "2026-11-20T08:06:37+08:00"
    stale_same_day = _sig("三日前凌晨旧文", event_date="2026-11-20", topic_id=884)
    stale_same_day.published_at = "2026-11-20T02:00:00+08:00"
    fresh_same_day = _sig("三日前上午新文", event_date="2026-11-20", topic_id=906)
    fresh_same_day.published_at = "2026-11-20T12:22:42+08:00"

    assert _in_publish_window(stale_same_day, since) is False
    assert _in_publish_window(fresh_same_day, since) is True


def test_picks_freshness_gate_and_no_false_kill_on_tool_domain():
    from collector.render import _is_tool_like, pick_items

    now = datetime(2026, 11, 23, 8, 6, tzinfo=CST)
    # 生产实测被误杀的两条：含「曝光」「警告」但 domain=工具做法
    ds_agent = _sig("DeepSeek V1作者创业项目曝光：推出专为本地部署的开源Agent",
                    domain="工具做法", event_date="2026-11-21", topic_id=938,
                    source_name="量子位", url="https://qbitai.com/938")
    ds_agent.published_at = "2026-11-21T14:32:31+08:00"
    cc_hook = _sig("Claude Code 更新增强 MCP 服务器错误提示与 Hook 安全警告",
                   domain="工具做法", cred="yellow", tier="official",
                   event_date="2026-11-20", topic_id=882,
                   source_name="Anthropic / Claude", url="https://github.com/anthropics/cc")
    cc_hook.published_at = "2026-11-20T06:14:41+08:00"
    # 非工具领域的传闻仍应排除
    rumor = _sig("消息称某厂或将开源大模型", domain="选型", event_date="2026-11-22", topic_id=999)

    assert _is_tool_like(ds_agent) is True
    assert _is_tool_like(cc_hook) is True
    assert _is_tool_like(rumor) is False

    # max_age_hours=48：只保留 48h 内的 ds_agent，排除 3 天前的 cc_hook，且附带 when 字段
    got = pick_items([ds_agent, cc_hook], now=now, per_source=2, max_age_hours=48)
    assert len(got) == 1
    assert got[0]["title"].startswith("DeepSeek V1")
    assert got[0]["when"] == "2026-11-21"


def test_select_radar_signals_and_energy_cap_single_media_monopoly():
    from collector.render import select_radar_signals

    sigs = [
        _sig(f"IT之家第{i}条", event_date="2026-11-22", topic_id=940 + i,
             source_name="IT之家 · AI", url=f"https://www.ithome.com/1/{i}.htm")
        for i in range(4)
    ] + [
        _sig(f"IT之家昨日{i}", event_date="2026-11-21", topic_id=930 + i,
             source_name="IT之家 · AI", url=f"https://www.ithome.com/2/{i}.htm")
        for i in range(3)
    ] + [
        _sig("量子位一条", event_date="2026-11-21", topic_id=938,
             source_name="量子位", url="https://www.qbitai.com/1.html"),
        _sig("腾讯混元开源", event_date="2026-11-20", cred="green", tier="official",
             topic_id=912, source_name="腾讯混元 · GitHub 开源", url="https://github.com/Tencent/Hy4"),
    ]
    picked = select_radar_signals(sigs, cap=20, max_papers=6,
                                  max_per_source=4, max_per_source_per_day=2)
    it_items = [s for s in picked if "ithome.com" in s.sources[0].url]
    assert len(it_items) == 4
    assert sum(1 for s in it_items if s.event_date == "2026-11-22") == 2
    assert sum(1 for s in it_items if s.event_date == "2026-11-21") == 2

    # 02 右栏能源同样受 per_source=1 限制
    e_sigs = [
        _sig("电网短缺一", domain="能源AI", questions=["Q09"], topic_id=1,
             source_name="IT之家 · AI", url="https://www.ithome.com/e1"),
        _sig("电网短缺二", domain="能源AI", questions=["Q09"], topic_id=2,
             source_name="IT之家 · AI", url="https://www.ithome.com/e2"),
        _sig("风电能源试点", domain="能源AI", questions=["Q01"], topic_id=3,
             source_name="新华网 · 能源频道", url="https://www.news.cn/e3"),
    ]
    e_got = pick_energy(e_sigs, limit=3, per_source=1)
    assert [s.topic_id for s in e_got[:2]] == [1, 3]


def test_focus_cooldown_prevents_back_to_back_same_media_red(cfg, store):
    cfg.settings["focus"] = {
        "enabled": True,
        "allow_research": False,
        "recency_hours": 24,
        "media_same_host_cooldown_hours": 24,
        "max_tries": 3,
    }
    now = datetime(2026, 11, 22, 16, 0, tzinfo=CST)
    cur = _sig("IT之家上午焦点", relevance=2, cred="red", tier="media",
               event_date="2026-11-22", topic_id=936, focus_flag=True,
               source_name="IT之家 · AI", url="https://www.ithome.com/1/936.htm")
    cur.published_at = "2026-11-22T07:28:10+08:00"

    same_media = _sig("IT之家下午焦点", relevance=2, cred="red", tier="media",
                      event_date="2026-11-22", topic_id=942,
                      source_name="IT之家 · AI", url="https://www.ithome.com/1/942.htm")
    same_media.published_at = "2026-11-22T15:25:35+08:00"

    other_official = _sig("官方发布新模型", relevance=2, cred="yellow", tier="official",
                          event_date="2026-11-22", topic_id=949,
                          source_name="OpenAI / Codex", url="https://openai.com/news/new")
    other_official.published_at = "2026-11-22T12:00:00+08:00"

    # 同站点 red 媒体在冷却期内被拦下
    assert focus.pick([same_media], cfg, store, now, current_focus=cur) == []
    # 官方一手仍可立即接管焦点
    assert [c.topic_id for c in focus.pick([same_media, other_official], cfg, store, now, current_focus=cur)] == [949]


def test_community_write_to_feed_false_removes_05_blocks_from_632(cfg, forum, store):
    cfg.settings["feed"]["enabled"] = True
    cfg.settings["community"] = {"enabled": True, "write_to_feed": False}
    # 模拟 632 原本带着旧的 community / question
    post_id = forum.first_post_id(cfg.settings["feed"]["topic_id"])
    raw = forum.post_raw(post_id)
    feed, mode = read_feed(raw)
    feed["community"] = [{"title": "旧社区帖", "url": "/t/1", "who": "u"}]
    feed["question"] = "旧今日一问"
    feed["community_updated_at"] = "2026-11-22T08:00:00+08:00"
    from collector.render import write_feed
    forum.update_post(post_id, write_feed(raw, feed, mode), "seed")

    s = _sig("今日信号", event_date="2026-11-20", topic_id=950)
    store.save_signal(s, [s.signal_id])
    update_feed(cfg, forum, store, FIXED_NOW, community_posts=[])

    updated, _ = read_feed(forum.post_raw(post_id))
    assert "community" not in updated
    assert "question" not in updated
    assert "community_updated_at" not in updated


# ---------- Phase 2 回归：SQLite 发布时间索引、白名单防空数组误杀、01/02 跨轨防撞题、SLA 指标 ----------

def test_store_schema_indexes_publish_time_and_filters_stale_discovered_today(tmp_path):
    """老文今日被采到（published_at=10-30, discovered_at=11-23）：SQLite recent_signals 直接拦住。"""
    db_path = tmp_path / "idx.sqlite"
    store = Store(db_path)

    old_pub = _sig("十月旧公告今日采到", event_date="2026-10-30", topic_id=961,
                   source_name="国家网信办", url="https://www.cac.gov.cn/2026-10-30/a.htm")
    old_pub.published_at = "2026-10-30T09:00:00+08:00"
    old_pub.discovered_at = "2026-11-23T08:00:00+08:00"

    fresh_pub = _sig("今日新鲜信号", event_date="2026-11-23", topic_id=962,
                     source_name="中国能源网", url="https://www.china5e.com/news/1.html")
    fresh_pub.published_at = "2026-11-23T09:00:00+08:00"
    fresh_pub.discovered_at = "2026-11-23T10:00:00+08:00"

    store.save_signal(old_pub, [old_pub.signal_id])
    store.save_signal(fresh_pub, [fresh_pub.signal_id])

    # 显式列已写入
    row = store.db.execute(
        "SELECT published_at, event_date, source_host, source_tier, relevance FROM signals WHERE signal_id=?",
        (fresh_pub.signal_id,),
    ).fetchone()
    assert row == ("2026-11-23T09:00:00+08:00", "2026-11-23", "china5e.com", "media", 2)

    got = store.recent_signals("2026-11-20T08:00:00+08:00")
    assert [s.topic_id for s in got] == [962]


def test_cross_track_dedup_shifts_focus_when_clashing_with_daily_brief(cfg, forum, store):
    """01 深度简报主打与 02 首位焦点撞题时，02 自动顺延给下一条不撞题焦点。"""
    cfg.settings["feed"]["enabled"] = True
    post_id = forum.first_post_id(cfg.settings["feed"]["topic_id"])
    raw = forum.post_raw(post_id)
    feed, mode = read_feed(raw)
    feed["daily_brief"] = {
        "date": "2026-11-23",
        "title": "趋势洞察（11月23日）",
        "url": "https://example.com/t/946",
        "insights": [{"heading": "算力涨价", "body": "内容"}],
        "feature_titles": ["AI服务器交付前最高涨价45%：内存成本飙升与PCB短缺引发算力供应链危机"],
    }
    from collector.render import write_feed
    forum.update_post(post_id, write_feed(raw, feed, mode), "seed daily_brief")

    now = datetime(2026, 11, 23, 10, 0, tzinfo=CST)
    clash = _sig("签约后也能涨价：消息称 AI 服务器厂商交付前提价，涨幅最高达 45%",
                 event_date="2026-11-22", topic_id=944, focus_flag=True,
                 source_name="IT之家 · AI", url="https://www.ithome.com/1/944.htm")
    clash.published_at = "2026-11-22T22:00:00+08:00"
    alt = _sig("风电设备 2027 年度策略报告：两海为基础，关注氢氨醇和算电协同",
               event_date="2026-11-22", topic_id=948, focus_flag=True,
               source_name="中国能源网", url="https://www.china5e.com/news/948.html")
    alt.published_at = "2026-11-22T18:00:00+08:00"
    store.save_signal(clash, [clash.signal_id])
    store.save_signal(alt, [alt.signal_id])

    metrics: dict = {}
    update_feed(cfg, forum, store, now, metrics_out=metrics)
    updated, _ = read_feed(forum.post_raw(post_id))
    assert updated["lead"]["title"].startswith("风电设备 2027 年度策略报告")
    assert metrics["radar_count"] == 2
    assert "radar_median_age_hours" in metrics


def test_force_keep_retries_single_items_when_model_returns_empty_batch(cfg):
    """白名单补判多条批次若模型偷懒返回 []，必须拆单条重试并救回一手源。"""
    from collector.tagger import tag_candidates

    class LazyBatchLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, system, user):
            self.calls += 1
            lines = [ln for ln in user.splitlines() if ln.strip()]
            if len(lines) > 1:
                return "[]"     # 多条批次偷懒返回空数组
            obj = {
                "idx": 0, "keep": True, "domain": "工具做法", "questions": ["Q04"],
                "topics": [], "relevance": 2,
                "title": "阿里开源 Qwen Code 命令行编程智能体工具",
                "summary": "官方发布命令行智能体框架，支持本地代码库分析与任务编排。",
                "us": "开源编程智能体工具可为企业研发提效提供参考。",
                "event_key": "qwen-code-open-source-2026-11-23",
                "event_date": "2026-11-23",
            }
            return json.dumps([obj], ensure_ascii=False)

    import json
    cands = [
        Candidate(module="ext_ai", source_name="阿里 Qwen · GitHub 开源", source_tier="official",
                  url="https://github.com/QwenLM/qwen-code", title="QwenLM/qwen-code",
                  excerpt="An open-source AI coding agent", published_at="2026-11-23T10:00:00+08:00"),
        Candidate(module="ext_ai", source_name="月之暗面 · GitHub 开源", source_tier="official",
                  url="https://github.com/MoonshotAI/kimi-code", title="MoonshotAI/kimi-code",
                  excerpt="Kimi Code CLI", published_at="2026-11-23T10:00:00+08:00"),
    ]
    llm = LazyBatchLLM()
    res = tag_candidates(cands, cfg, llm, force_keep=True)
    assert llm.calls == 3          # 1 次多条返回 [] + 2 次单条重试
    assert all(r.keep and not r.error for r in res)


def test_low_supply_today_floor_promotes_today_rel1_signals(cfg, forum, llm, store, alerter, monkeypatch):
    """当日高相关度信号不足 low_supply_today_floor 时，自动提拔今日非论文 rel=1 信号补位。"""
    cfg.settings["round"]["min_relevance_to_post"] = 2
    cfg.settings["round"]["low_supply_today_floor"] = 3
    cfg.settings["focus"]["enabled"] = False
    cfg.settings["structured_summary"] = {"enabled": False}
    cfg.sources = [{"name": "中国能源网", "module": "ext_ai", "kind": "rss", "url": "file:///dev/null", "tier": "media"}]

    cands = [
        Candidate(module="ext_ai", source_name="中国能源网", source_tier="media",
                  url="https://china5e.com/1", title="全国首个独立运行兆瓦级海岛微电网投运",
                  excerpt="摘要", published_at="2026-11-20T09:00:00+08:00"),
        Candidate(module="ext_ai", source_name="中国能源网", source_tier="media",
                  url="https://china5e.com/2", title="国内风电并网储能示范工程正式开工建设",
                  excerpt="摘要", published_at="2026-11-20T10:00:00+08:00"),
        Candidate(module="ext_ai", source_name="中国能源网", source_tier="media",
                  url="https://china5e.com/old", title="昨日非当日背景资讯不应被提拔进今日保底",
                  excerpt="摘要", published_at="2026-11-19T10:00:00+08:00"),
    ]
    monkeypatch.setattr("collector.pipeline.fetch", lambda src, *a, **kw: cands)

    def fake_tag(batch, _cfg, _llm, **_kw):
        from collector.tagger import TagResult
        return [
            TagResult(keep=True, title=c.title, summary="正文摘要。", us="行业参考",
                      domain="能源AI", questions=["Q01"], topics=[], relevance=1,
                      event_key=f"ev-{i}", event_date=c.published_at[:10])
            for i, c in enumerate(batch)
        ]

    monkeypatch.setattr("collector.pipeline.tag_candidates", fake_tag)
    rep = run_round(cfg, forum, llm, store, alerter, now=FIXED_NOW)
    assert rep.ok, rep.fatal
    assert rep.posted == 2          # 2 条今日 rel=1 信号被提拔，1 条昨日 rel=1 仍被挡住
    assert rep.below_min_relevance == 1


def test_save_signal_runs_before_readback_and_422_recovers_topic_id(cfg, forum, llm, store, alerter, monkeypatch):
    """2026-11-23 t/969 回归：建帖后先落库防读回断连留孤儿帖；422 external_id 自动回查真实 topic_id。"""
    import pytest
    cfg.settings["focus"]["enabled"] = False
    cfg.settings["structured_summary"] = {"enabled": False}
    cfg.sources = [{"name": "测试源", "module": "ext_ai", "kind": "rss", "url": "file:///dev/null", "tier": "media"}]
    cand = Candidate(module="ext_ai", source_name="测试源", source_tier="media",
                     url="https://example.com/969", title="数据中心海外建厂税收政策讨论",
                     excerpt="摘要", published_at="2026-11-20T09:00:00+08:00")
    monkeypatch.setattr("collector.pipeline.fetch", lambda src, *a, **kw: [cand])

    def fake_tag(batch, _cfg, _llm, **_kw):
        from collector.tagger import TagResult
        return [TagResult(keep=True, title=cand.title, summary="摘要正文。", us="行业参考",
                          domain="政策合规", questions=["Q09"], topics=[], relevance=2,
                          event_key="ev-969", event_date="2026-11-20")]

    monkeypatch.setattr("collector.pipeline.tag_candidates", fake_tag)

    # 1. 模拟 create_topic 成功但 topic() 读回抛网络异常 → 信号必须已落库且带 topic_id
    orig_topic = forum.topic
    def boom_topic(tid):
        if tid != cfg.settings["feed"]["topic_id"]:
            raise ConnectionResetError("Connection reset by peer")
        return orig_topic(tid)
    monkeypatch.setattr(forum, "topic", boom_topic)
    with pytest.raises(ConnectionResetError):
        run_round(cfg, forum, llm, store, alerter, now=FIXED_NOW)
    saved = store.by_event_key("ev-969")
    assert saved is not None and saved.topic_id is not None and saved.post_id is not None

    # 2. 模拟新库遇 422 external_id → 自动通过 topic_by_external_id 找回真实 topic_id
    monkeypatch.setattr(forum, "topic", orig_topic)
    fresh_store = Store(":memory:")
    rep2 = run_round(cfg, forum, llm, fresh_store, alerter, now=FIXED_NOW)
    assert rep2.ok, rep2.fatal
    assert rep2.duplicates == 1
    recovered = fresh_store.by_event_key("ev-969")
    assert recovered is not None and recovered.topic_id == saved.topic_id and recovered.post_id == saved.post_id
