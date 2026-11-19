"""主题追踪（05 TOPICS）：素材分组、模型写判断、清洗与兜底（2026-11-16）。"""
import json
from datetime import datetime, timedelta

from collector.models import Signal, SourceRef
from collector.sources.fetchers import CST
from collector.topics import (NO_SIGNAL_CHANGE, collect_material, feed_topics,
                              write_judgments)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=CST)


def _sig(tid, title, *, topics=(), days_ago=1, relevance=2, cred="yellow"):
    day = (NOW - timedelta(days=days_ago)).date().isoformat()
    return Signal(signal_id=f"s{tid}", event_key=f"k-{tid}", title=title, summary="摘要",
                  us="", domain="能源AI", questions=["Q01"], topics=list(topics),
                  relevance=relevance, cred=cred, event_date=day,
                  discovered_at=(NOW - timedelta(days=days_ago)).isoformat(timespec="seconds"),
                  sources=[SourceRef(name="某媒体", url="https://x/1", tier="media")],
                  topic_id=tid)


TOPICS_CFG = [
    {"name": "功率预测 AI", "tag": "topic-power-forecast", "questions": ["Q01", "Q02"]},
    {"name": "智能体与 MCP 安全", "tag": "topic-agent-security", "questions": ["Q07"]},
]


def test_collect_material_groups_by_tag_and_window():
    sigs = [
        _sig(1, "功率预测新模型", topics=["topic-power-forecast"], days_ago=2),
        _sig(2, "更早的功率预测", topics=["topic-power-forecast"], days_ago=20),
        _sig(3, "一个月前的功率预测", topics=["topic-power-forecast"], days_ago=45),
        _sig(4, "智能体逃逸", topics=["topic-agent-security"], days_ago=1),
        _sig(5, "无关话题", topics=[], days_ago=1),
    ]
    mats = collect_material(sigs, TOPICS_CFG, now=NOW)
    pf = next(m for m in mats if m.name == "功率预测 AI")
    assert [s.topic_id for s in pf.week_signals] == [1]      # 近 7 天
    assert [s.topic_id for s in pf.timeline] == [1, 2]       # 近 30 天
    agent = next(m for m in mats if m.name == "智能体与 MCP 安全")
    assert [s.topic_id for s in agent.week_signals] == [4]


def test_collect_material_empty_topic():
    mats = collect_material([], TOPICS_CFG, now=NOW)
    assert all(not m.week_signals and not m.timeline for m in mats)


class _JudgeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def chat(self, system, user):
        self.calls += 1
        return self.reply


def test_write_judgments_parses_model_output():
    sigs = [_sig(1, "功率预测新模型", topics=["topic-power-forecast"])]
    mats = collect_material(sigs, TOPICS_CFG, now=NOW)
    reply = json.dumps([
        {"name": "功率预测 AI", "judge": "本周出现一条功率预测信号，仍不足以判断趋势。",
         "change": "新增 1 条信号。", "next": "看下期是否有第二条独立来源。"},
        {"name": "智能体与 MCP 安全", "judge": "近 30 天无信号，不形成判断。",
         "change": NO_SIGNAL_CHANGE, "next": "继续观察。"},
    ], ensure_ascii=False)
    got = write_judgments(mats, _JudgeLLM(reply))
    assert got["功率预测 AI"]["judge"].startswith("本周出现")
    assert got["智能体与 MCP 安全"]["change"] == NO_SIGNAL_CHANGE


def test_no_signals_forces_no_signal_change():
    """本周无信号时，模型写的「本周变化」一律被覆盖成固定文案——防止它编。"""
    mats = collect_material([], TOPICS_CFG, now=NOW)
    reply = json.dumps([{"name": m.name, "judge": "某判断", "change": "编了一条变化",
                         "next": "看下期"} for m in mats], ensure_ascii=False)
    got = write_judgments(mats, _JudgeLLM(reply))
    assert all(v["change"] == NO_SIGNAL_CHANGE for v in got.values())


def test_has_signals_but_says_no_signal_is_corrected():
    sigs = [_sig(1, "功率预测新模型", topics=["topic-power-forecast"])]
    mats = collect_material(sigs, TOPICS_CFG, now=NOW)
    reply = json.dumps([{"name": "功率预测 AI", "judge": "判断", "change": NO_SIGNAL_CHANGE,
                         "next": "看下期"},
                        {"name": "智能体与 MCP 安全", "judge": "判断",
                         "change": NO_SIGNAL_CHANGE, "next": "看下期"}], ensure_ascii=False)
    got = write_judgments(mats, _JudgeLLM(reply))
    assert got["功率预测 AI"]["change"] == "本周新增 1 条信号。"


def test_lengths_clipped_to_skill_limits():
    mats = collect_material([], TOPICS_CFG, now=NOW)
    reply = json.dumps([{"name": m.name, "judge": "长" * 300, "change": "变" * 300,
                         "next": "看" * 300} for m in mats], ensure_ascii=False)
    got = write_judgments(mats, _JudgeLLM(reply))
    for v in got.values():
        assert len(v["judge"]) <= 160 and len(v["change"]) <= 120 and len(v["next"]) <= 80


def test_placeholder_rejected():
    mats = collect_material([], TOPICS_CFG, now=NOW)
    reply = json.dumps([{"name": m.name, "judge": "TODO 待补", "change": NO_SIGNAL_CHANGE,
                         "next": "{{下期}}"} for m in mats], ensure_ascii=False)
    got = write_judgments(mats, _JudgeLLM(reply))
    for v in got.values():
        assert "TODO" not in v["judge"] and "{{" not in v["next"]


def test_model_failure_falls_back_to_previous():
    class Boom:
        calls = 0

        def chat(self, system, user):
            raise RuntimeError("模型挂了")

    cfg = [{"name": "功率预测 AI", "tag": "topic-power-forecast", "questions": ["Q01"],
            "previous": {"judge": "上期判断还在", "next": "上期的观察点"}}]
    mats = collect_material([], cfg, now=NOW)
    got = write_judgments(mats, Boom())
    assert got["功率预测 AI"]["judge"] == "上期判断还在"
    assert got["功率预测 AI"]["change"] == NO_SIGNAL_CHANGE


def test_feed_topics_shape_matches_component():
    """线上组件 normTopic 认 name/week/questions/judge/change/next/url 七个字段。"""
    sigs = [_sig(1, "功率预测新模型", topics=["topic-power-forecast"]),
            _sig(2, "功率预测再一条", topics=["topic-power-forecast"], days_ago=2)]
    mats = collect_material(sigs, TOPICS_CFG, now=NOW)
    reply = json.dumps([{"name": m.name, "judge": "判断", "change": "变化", "next": "观察"}
                        for m in mats], ensure_ascii=False)
    got = write_judgments(mats, _JudgeLLM(reply))
    out = feed_topics(mats, got, base_url="https://forum.test")
    assert out[0] == {
        "name": "功率预测 AI", "week": 2, "questions": ["Q01", "Q02"],
        "judge": "判断", "change": "变化", "next": "观察",
        "url": "https://forum.test/t/1",
    }


def test_feed_topics_prefers_dossier_topic_url():
    cfg = [{"name": "功率预测 AI", "tag": "topic-power-forecast", "questions": ["Q01"],
            "dossier_topic_id": 240}]
    mats = collect_material([_sig(1, "x", topics=["topic-power-forecast"])], cfg, now=NOW)
    out = feed_topics(mats, {}, base_url="https://forum.test")
    assert out[0]["url"] == "https://forum.test/t/240"


def test_feed_topics_url_empty_when_no_signal():
    cfg = [{"name": "空主题", "tag": "topic-power-forecast", "questions": []}]
    mats = collect_material([], cfg, now=NOW)
    out = feed_topics(mats, {}, base_url="https://forum.test")
    assert out[0]["url"] == ""      # 组件在 url 为空时不渲染链接
    assert out[0]["week"] == 0


def test_collect_material_matches_display_name_too():
    """回归：Signal.topics 存的是中文显示名（tagger 按 tags.yaml 白名单返回显示名），
    配置里的 tag 是论坛标签 topic-xxx。两者都要认，否则「本周 +N」永远是 0。
    2026-11-16 生产实测：4 个主题全 0，库里却有 1/6/12 条带标签的信号。
    """
    sigs = [_sig(1, "用显示名打标", topics=["功率预测 AI"]),
            _sig(2, "用标签名打标", topics=["topic-power-forecast"])]
    mats = collect_material(sigs, TOPICS_CFG, now=NOW)
    pf = next(m for m in mats if m.name == "功率预测 AI")
    assert len(pf.week_signals) == 2


def test_clip_breaks_at_sentence_boundary():
    """回归：生产实测把判断切成「…并排查 SGLang 等组件漏」，读不出结论。

    断句策略：上限内最后一个句末标点只要在 60% 之后就用它，保证读者看到完整句子；
    句末标点太靠前（会丢掉一半内容）才退回逗号；两者都没有才硬截加省略号。
    """
    from collector.topics import _clip
    # 句子边界落在 60% 之后 → 断在句号
    text = "前" * 80 + "。" + "后" * 200
    out = _clip(text, 100)              # 句号在 80/100 = 80%，超过 60% 阈值
    assert len(out) <= 100 and out.endswith("。")
    assert out == "前" * 80 + "。"

    # 句子边界太靠前（14/60）→ 不能只留 14 字，改在逗号处或硬截
    text2 = "开头作铺垫的一句话写得比较长。第二句话也很长，" + "填充" * 80 + "。"
    out2 = _clip(text2, 60)
    assert len(out2) <= 60
    assert len(out2) >= 40              # 没有为了凑句号丢掉大半内容


def test_clip_hard_truncates_when_no_boundary():
    from collector.topics import _clip
    out = _clip("没有标点的超长文本" * 30, 40)
    assert len(out) <= 40 and out.endswith("…")


def test_clip_leaves_short_text_alone():
    from collector.topics import _clip
    assert _clip("短句。", 160) == "短句。"


def test_dossier_ids_reads_state(tmp_path):
    from collector.config import Config
    from collector.pipeline import dossier_ids
    cfg = Config(root=tmp_path, settings={}, sources=[], questions=[], tags={"domains": {}},
                 blocked_domains=[])
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "topic_dossiers.json").write_text(
        json.dumps({"topic-power-forecast": 871, "bad": "x"}), encoding="utf-8")
    assert dossier_ids(cfg) == {"topic-power-forecast": 871}


class _Forum:
    """最小论坛替身：底座帖 632=post 1，档案帖 871=post 2，其余建帖动态分配。"""

    def __init__(self):
        self.posts = {1: "```json\n" + json.dumps({"schema": "air-pulse/1", "topics": []}) + "\n```"}
        self.topics = {632: 1, 871: 2}
        self.created = {}

    def first_post_id(self, tid):
        return self.topics.get(tid, 0)

    def post_raw(self, pid):
        return self.posts.get(pid, "")

    def update_post(self, pid, raw, reason):
        self.posts[pid] = raw

    def create_topic(self, title, raw, category_id, tags, external_id):
        tid = 900 + len(self.created)
        self.created[external_id] = tid
        self.topics[tid] = tid + 1000
        self.posts[tid + 1000] = raw
        return {"topic_id": tid, "post_id": tid + 1000}

    def tag_topics(self, tag, page=0, limit_pages=3):
        return []


def _cfg(tmp_path):
    from collector.config import Config
    return Config(root=tmp_path,
                  settings={"discourse": {"base_url": "https://x", "signal_category_id": 20},
                            "feed": {"topic_id": 632}},
                  sources=[], questions=[], tags={"domains": {}}, blocked_domains=[],
                  topics={"topics": [{"name": "A", "tag": "t-a"}], "lookback_days": 7, "timeline_days": 30})


def test_update_feed_topics_prefers_dossier_url(tmp_path, monkeypatch):
    """档案帖 id 必须让 topics[].url 指向档案，而不是回退到最新信号帖；档案正文要一起写。"""
    from collector import pipeline, topics as topics_mod
    from collector.topics import TopicMaterial

    cfg = _cfg(tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "topic_dossiers.json").write_text('{"t-a": 871}', encoding="utf-8")

    monkeypatch.setattr(topics_mod, "collect_material",
                        lambda *a, **k: [TopicMaterial(name="A", tag="t-a")])
    monkeypatch.setattr(topics_mod, "write_judgments",
                        lambda mats, llm: {"A": {"judge": "j", "change": "c", "next": "n"}})
    f = _Forum()
    pipeline.update_feed_topics(cfg, f, [], None, now=NOW)
    feed = json.loads(f.posts[1].split("```json\n")[1].split("\n```")[0])
    assert feed["topics"][0]["url"] == "https://x/t/871"
    assert "[wrap=ais-dossier]" in f.posts[2]          # 632 与档案同一份判断，一起写了
    assert "**当前判断**" in f.posts[2]


def test_extra_tag_signals_skips_signal_category_and_window(tmp_path):
    from collector.pipeline import extra_tag_signals

    class _Tags:
        def tag_topics(self, tag, page=0, limit_pages=3):
            return [
                {"id": 1, "title": "内部上新", "category_id": 7, "created_at": "2026-11-17T06:00:00Z"},
                {"id": 2, "title": "信号帖", "category_id": 20, "created_at": "2026-11-17T06:00:00Z"},
                {"id": 3, "title": "太旧", "category_id": 7, "created_at": "2026-09-01T06:00:00Z"},
            ]

    items = [{"name": "A", "tag": "t-a", "questions": ["Q08"], "extra_tags": ["集团ai能力上新"]}]
    out = extra_tag_signals(_cfg(tmp_path), _Tags(), items, [], NOW, {"timeline_days": 30})
    assert [s.topic_id for s in out] == [1]
    assert out[0].review == "human" and out[0].cred == ""   # 人工帖：不带可信度
    assert out[0].topics == ["A"]

    # 已在本地索引里的同一个 topic_id 不重复计
    dup = extra_tag_signals(_cfg(tmp_path), _Tags(), items, out, NOW, {"timeline_days": 30})
    assert dup == []


def test_render_dossier_marks_human_posts_without_cred():
    from collector.models import Signal
    from collector.topics import TopicMaterial, render_dossier

    human = Signal(signal_id="forum-789", event_key="forum-789", title="同事发的上新帖",
                   summary="", us="", domain="内部", questions=["Q08"], topics=["A"],
                   relevance=2, cred="", review="human", event_date="2026-11-17",
                   discovered_at="2026-11-17T06:00:00Z", topic_id=789)
    m = TopicMaterial(name="A", tag="t-a", questions=["Q08"], week_signals=[human], timeline=[human])
    body = render_dossier(m, {"judge": "j", "change": "c", "next": "n"}, "2026-11-19", "https://x")
    line = next(ln for ln in body.splitlines() if "11-17" in ln)
    assert "（人工帖）" in line
    assert "（未审）" not in line
    assert "🔴" not in line and "🟡" not in line and "🟢" not in line
    assert "[wrap=ais-dossier]" in body and "**时间线**" in body
