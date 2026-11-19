from collector.credibility import compute_cred
from collector.dedup import check_event_key, is_blocked, normalize_url, signal_id
from collector.models import SourceRef
from collector.render import parse_machine_block, read_feed, topic_body, topic_tags, write_feed
from collector.models import Signal
from collector.sources.fetchers import fetch, to_cst_iso
from collector.tagger import build_system_prompt, parse_json_array, validate_item
from collector.config import validate_config


def test_normalize_drops_tracking_and_www():
    a = normalize_url("https://www.Example.com/a/?utm_source=x&id=3#frag")
    b = normalize_url("https://example.com/a?id=3")
    assert a == b
    assert signal_id("https://www.example.com/a?utm_medium=rss") == signal_id("https://example.com/a")


def test_event_key_rejects_numbered_keys():
    assert check_event_key("signal-1")
    assert check_event_key("track_12")
    assert check_event_key("")
    assert check_event_key("Has Space-2026") is not None
    assert check_event_key("deepseek-series-b-2026-11-04") is None


def test_blocked_domains():
    assert is_blocked("https://www.kotaku.com/x", ["kotaku.com"])
    assert is_blocked("https://news.kotaku.com/x", ["kotaku.com"])
    assert not is_blocked("https://notkotaku.com/x", ["kotaku.com"])


def test_cred_rules():
    m1 = SourceRef("媒体甲", "https://a.com/1", "media")
    m2 = SourceRef("媒体乙", "https://b.com/1", "media")
    off = SourceRef("官网", "https://vendor.com/1", "official")
    gov = SourceRef("能源局", "https://nea.gov.cn/1", "authority")
    assert compute_cred([m1]) == "red"
    assert compute_cred([m1, m2]) == "yellow"
    assert compute_cred([off]) == "yellow"
    assert compute_cred([off, m1]) == "green"
    assert compute_cred([gov]) == "green"


def test_dates_to_beijing_time():
    assert to_cst_iso("Wed, 04 Nov 2026 22:00:00 GMT") == "2026-11-05T06:00:00+08:00"
    assert to_cst_iso("2026-11-05") == "2026-11-05T00:00:00+08:00"
    assert to_cst_iso("2026年11月5日") == "2026-11-05T00:00:00+08:00"
    assert to_cst_iso("不是日期") is None


def test_fetchers(cfg):
    rss, atom, listpage = cfg.sources
    items = fetch(rss)
    assert len(items) == 5 and items[0].title.startswith("某整机厂")
    a = fetch(atom)
    assert a[0].url == "https://vendor.example.com/blog/v2"
    lp = fetch(listpage)
    assert [x.title for x in lp] == ["关于推进人工智能+能源试点的通知"]   # 关键词初筛
    assert lp[0].published_at.startswith("2026-11-05")


def test_validate_item_whitelists(cfg):
    good = {"keep": True, "domain": "选型", "questions": ["Q03"], "topics": ["国产大模型选型", "不存在的主题"],
            "relevance": 3, "title": "智谱 GLM 被欧洲金融公司选为默认编程模型", "summary": "摘要。", "us": "", "event_key": "glm-switch-2026-11-05",
            "event_date": "2026-11-05"}
    r = validate_item(good, cfg)
    assert r.error is None and r.topics == ["国产大模型选型"]
    bad = dict(good, domain="娱乐", questions=["Q99"], event_key="signal-3", summary="{{占位}}")
    r = validate_item(bad, cfg)
    assert "领域" in r.error and "Q99" in r.error and "编号式" in r.error and "占位" in r.error
    assert validate_item({"keep": False}, cfg).keep is False


def test_us_prompt_is_public_safe(cfg):
    """`us` 写进对所有人可见的话题，提示词里必须写明这一点（2026-11-19 决定）。

    背景：分类 20 与 632 匿名可读，实测 /c/20/show.json 返回 read_restricted=false。
    原来的提示词把 us 定义成「对金风」的研判，模型的自然写法是「集团需核查…」这类
    内部动作，等于把内部备忘贴在公网上。这条断言防止口径被改回去。
    """
    prompt = build_system_prompt(cfg)
    assert "对所有人可见" in prompt and "未登录访客" in prompt
    assert "不写集团内部动作" in prompt
    # 2026-11-19 追加：公开帖不写「集团 / 金风」这类主体称谓（实测 4/29 仍在写）
    assert "主语不要写「集团」「金风」" in prompt


class _Resp:
    def __init__(self, code):
        self.status_code = code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {"choices": [{"message": {"content": "OK"}}]}


def _client(cfg, monkeypatch, codes):
    """把 requests.post 换成按序返回 codes 的假接口，并屏蔽睡眠与钥匙串。"""
    from collector import tagger
    seen = []

    def fake_post(url, **kw):
        seen.append(url)
        return _Resp(codes[min(len(seen) - 1, len(codes) - 1)])

    monkeypatch.setattr(tagger.requests, "post", fake_post)
    monkeypatch.setattr(tagger.time, "sleep", lambda s: None)
    monkeypatch.setattr(tagger, "get_secret", lambda name: "test-key")
    return tagger.LLMClient(cfg), seen


def test_llm_chat_retries_when_rate_limited(cfg, monkeypatch):
    """418/429 是 WAF 限流，不是模型错——补两次就能过（2026-11-19 换网关后新增的失败模式）。"""
    client, seen = _client(cfg, monkeypatch, [418, 429, 200])
    assert client.chat("s", "u") == "OK"
    assert len(seen) == 3
    assert client.rate_streak == 0          # 成功后计数器归零


def test_llm_chat_gives_up_under_sustained_rate_limit(cfg, monkeypatch):
    """整轮都在被限流时不要硬撑：连续 3 次就失败，让整轮快速落到 fatal。"""
    import pytest
    client, seen = _client(cfg, monkeypatch, [418])
    with pytest.raises(Exception):
        client.chat("s", "u")
    assert client.rate_streak >= 3


def test_llm_chat_does_not_retry_real_errors(cfg, monkeypatch):
    """401/400 这类不是限流，重试没意义，必须立刻抛。"""
    import pytest
    client, seen = _client(cfg, monkeypatch, [401])
    with pytest.raises(Exception):
        client.chat("s", "u")
    assert len(seen) == 1


def _cand(title):
    from collector.models import Candidate
    return Candidate(module="ext_ai", source_name="测试媒体", source_tier="media",
                     url="https://example.com/" + str(hash(title)), title=title, excerpt="摘要片段")


def test_tag_retries_batch_once_when_json_is_malformed(cfg):
    """模型输出 JSON 少逗号时重试一次，别让整批 8 条进待人工（2026-11-19 实测 3.2% 损失）。"""
    from collector.tagger import tag_candidates

    class Flaky:
        def __init__(self):
            self.n = 0

        def chat(self, system, user, timeout=None, max_tokens=None):
            self.n += 1
            if self.n == 1:
                return '{"broken": [1, 2'          # 第 1 次：语法残缺
            return '[{"idx":0,"keep":false},{"idx":1,"keep":false}]'

    client = Flaky()
    out = tag_candidates([_cand("标题一标题一标题一"), _cand("标题二标题二标题二")], cfg, client)
    assert client.n == 2                            # 整批 1 次 + 重试 1 次
    assert all(r.error is None for r in out)        # 不再记为「模型调用失败」
    assert all(r.keep is False for r in out)


def test_tag_gives_up_after_second_failure(cfg):
    from collector.tagger import tag_candidates

    class AlwaysBroken:
        """一直返回坏 JSON：重试一次仍失败，才落成「模型调用失败」。"""

        def __init__(self):
            self.n = 0

        def chat(self, system, user, timeout=None, max_tokens=None):
            self.n += 1
            return '{"broken": [1, 2'

    client = AlwaysBroken()
    out = tag_candidates([_cand("标题一标题一标题一")], cfg, client)
    assert client.n == 2
    assert out[0].keep is True and "模型调用失败" in (out[0].error or "")


def test_parse_json_array_with_fence():
    assert parse_json_array('好的：\n```json\n[{"idx":0}]\n```') == [{"idx": 0}]


def test_topic_render_roundtrip(cfg):
    sig = Signal(signal_id="abc123", event_key="x-launch-2026-11-05", title="标题", summary="摘要", us="一句",
                 domain="安全", questions=["Q07"], topics=["智能体与 MCP 安全"], relevance=3, cred="red",
                 discovered_at="2026-11-05T07:31:00+08:00",
                 sources=[SourceRef("arXiv", "https://arxiv.org/abs/1", "research")])
    body = topic_body(sig, cfg)
    assert "摘要由 AI 生成" in body and "Q07" in body
    assert parse_machine_block(body)["event_key"] == "x-launch-2026-11-05"
    assert topic_tags(sig, cfg) == ["security", "q07", "topic-agent-security", "cred-red", "auto"]


def test_feed_rewrite_keeps_other_fields():
    raw = "说明文字\n\n```json\n{\"schema\": \"air-pulse/1\", \"brief\": [1], \"radar\": []}\n```\n尾巴"
    feed, mode = read_feed(raw)
    feed["radar"] = [{"title": "新"}]
    out = write_feed(raw, feed, mode)
    assert out.startswith("说明文字") and out.endswith("尾巴")
    back, _ = read_feed(out)
    assert back["brief"] == [1] and back["schema"] == "air-pulse/1" and back["radar"][0]["title"] == "新"


def test_shipped_config_is_valid():
    from collector.config import load_config
    assert validate_config(load_config()) == []


def test_lowercase_question_ids_are_accepted(cfg):
    obj = {"keep": True, "domain": "选型", "questions": ["q03"], "relevance": 2,
           "title": "智谱 GLM 被欧洲金融公司选为默认编程模型", "summary": "摘要。", "us": "",
           "event_key": "glm-switch-2026-11-05"}
    r = validate_item(obj, cfg)
    assert r.error is None and r.questions == ["Q03"]


def test_special_fetchers_with_daily_skill_fixtures():
    import json
    from pathlib import Path
    from collector.sources.special import match_keywords, parse_github_trending, parse_hn
    fix = Path(__file__).parent / "fixtures"
    src = {"module": "ext_ai", "name": "HN", "tier": "community"}
    hn = parse_hn(src, json.loads((fix / "hn_front_page_sample.json").read_text(encoding="utf-8")))
    assert hn and all(c.url and c.title for c in hn) and any(c.heat for c in hn)
    gh = parse_github_trending(src, (fix / "github_trending_sample.html").read_text(encoding="utf-8"))
    assert gh and gh[0].url.startswith("https://github.com/")
    assert match_keywords("New LLM agent framework", "default")
    assert not match_keywords("Fresh air in the brag room", "default")


def test_migrated_sources_cover_daily_skill():
    import json
    from pathlib import Path
    from collector.config import load_config
    daily = Path("/home/claude/src/daily/ai-radar-daily/config/sources.json")
    cfg = load_config()
    ids = {s.get("id") for s in cfg.sources}
    if daily.exists():
        old = {s["id"] for s in json.loads(daily.read_text(encoding="utf-8"))["sources"]}
        assert old <= ids
    dead = next(s for s in cfg.sources if s.get("id") == "deepseek-api-news")
    assert dead["enabled"] is False


# ---------- event_date 与信源发布日期对账（2026-11-16） ----------

def _sig_obj(event_date):
    return {"keep": True, "domain": "选型", "questions": ["Q03"], "topics": [],
            "relevance": 2, "title": "某厂发布新版推理框架", "summary": "摘要。", "us": "",
            "event_key": "foo-release-2026-11-05", "event_date": event_date}


def test_event_date_corrected_when_model_fabricates(cfg):
    """模型把 10-30 的公告标成今天 → 以信源日期为准。

    生产实例：网信办 2026-10-30 的备案公告被标成 event_date=2026-11-16，
    于是它看起来像当日新闻，一路穿过窗口判断。
    """
    from collector.models import Candidate
    cand = Candidate(module="m", source_name="国家网信办", source_tier="authority",
                     url="https://www.cac.gov.cn/2026-10/30/c_1.htm", title="t",
                     published_at="2026-10-30T00:00:00+08:00")
    r = validate_item(_sig_obj("2026-11-16"), cfg, cand)
    assert r.event_date == "2026-10-30"


def test_event_date_kept_when_within_tolerance(cfg):
    """事件日与发布日差 1 天以内属于正常（晚上发布、次日采集），保留模型判断。"""
    from collector.models import Candidate
    cand = Candidate(module="m", source_name="s", source_tier="media", url="u", title="t",
                     published_at="2026-11-06T00:00:00+08:00")
    r = validate_item(_sig_obj("2026-11-05"), cfg, cand)
    assert r.event_date == "2026-11-05"


def test_event_date_filled_from_pubdate_when_model_omits(cfg):
    """模型没给 event_date，但有信源日期 → 用信源日期，别留空。"""
    from collector.models import Candidate
    cand = Candidate(module="m", source_name="s", source_tier="media", url="u", title="t",
                     published_at="2026-11-05T09:30:00+08:00")
    r = validate_item(_sig_obj(None), cfg, cand)
    assert r.event_date == "2026-11-05"


def test_event_date_untouched_without_candidate(cfg):
    """向后兼容：不传 cand 时行为与旧版一致。"""
    r = validate_item(_sig_obj("2026-11-05"), cfg)
    assert r.event_date == "2026-11-05"


# ---------- radar 展示按发布日期过滤（2026-11-16） ----------

def test_radar_hides_signal_older_than_publish_window():
    """recent_signals 按 discovered_at 过滤，旧公告只要今天采到就能挂满 72h。
    追加的发布日期窗口判断要把 10-30 的条目挡在 radar 之外。
    """
    from collector.pipeline import _in_publish_window
    from collector.models import Signal

    def mk(event_date):
        return Signal(signal_id="x", event_key="k", title="t", summary="", us="",
                      domain="政策合规", questions=[], topics=[], relevance=3, cred="red",
                      event_date=event_date, discovered_at="2026-11-16T13:00:00+08:00")

    since = "2026-11-13T13:00:00+08:00"      # 72h 窗口起点
    assert _in_publish_window(mk("2026-10-30"), since) is False   # 超窗 17 天 → 隐藏
    assert _in_publish_window(mk("2026-11-05"), since) is False   # 超窗 8 天 → 隐藏
    assert _in_publish_window(mk("2026-11-16"), since) is True    # 当日 → 展示
    assert _in_publish_window(mk("2026-11-13"), since) is True    # 恰在窗口边界 → 展示
    assert _in_publish_window(mk(None), since) is True            # 日期未知 → 放行（入池阶段已判过）

# ---------- event_key 中文实体（2026-11-17） ----------
# 背景：pending 里躺了一条 huawei-麒麟-9050-ai-audio-video-2026-11-16，
# 连续 3 天没有任何命令能把它捞出来。原因是模型把中文实体名拼进了 event_key，
# 而 VALID_KEY 只放行 [a-z0-9-]，归一化阶段又不处理中文 → 判不合格 → 进 pending → 无出口。

from collector.dedup import check_event_key, normalize_event_key  # noqa: E402


def test_cjk_event_key_is_normalized_not_rejected():
    n = normalize_event_key("huawei-麒麟-9050-ai-audio-video-2026-11-16")
    assert check_event_key(n) is None, f"中文实体名仍被判不合格：{n}"
    assert "麒麟" not in n            # 非 ASCII 已消除
    assert "huawei" in n and "9050" in n


def test_same_cjk_maps_to_same_key():
    """同一中文实体在不同轮次必须映射到同一个键，否则去重会失效。"""
    a = normalize_event_key("华为-麒麟-9050")
    b = normalize_event_key("华为 麒麟 9050")
    assert a == b


def test_different_cjk_maps_to_different_keys():
    """不能把所有中文一律抹平成同一个 token，否则两个不同事件会撞键。"""
    assert normalize_event_key("麒麟-9050") != normalize_event_key("昇腾-9100")


def test_pure_cjk_key_still_valid():
    n = normalize_event_key("华为发布新芯片")
    assert check_event_key(n) is None


def test_generic_key_still_rejected():
    """放宽中文不能顺手放宽「编号式键」这条不变式。"""
    for bad in ("signal-3", "track_12", "item-7"):
        assert check_event_key(normalize_event_key(bad)) is not None


def test_version_dots_still_normalized():
    assert normalize_event_key("baidu-ernie-5.5-flash") == "baidu-ernie-5-5-flash"
