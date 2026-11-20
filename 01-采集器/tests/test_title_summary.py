"""标题与摘要的规整：首页 01 不能出现「标题：标题的复述」这种车轱辘话。

生产实例（2026-11-20，t/866 / 632 底座帖）：
    title   国家能源局发布《中国虚拟电厂发展报告（2026）》
    summary 国家能源局发布了《中国虚拟电厂发展报告（2026）》。该报告由官方渠道新华网能源频道报道。
    01 卡   …（报告）》：国家能源局发布了《…（报告）》。
这条既是「摘要复述标题」，也是「标题与原文体裁不符」（原文是解读稿）。前者用确定性规则拦，
后者靠提示词与数据修正。
"""
from collector.models import Signal, SourceRef
from collector.render import _brief_text
from collector.tagger import build_system_prompt, validate_item
from collector.textnorm import (drop_restating_lead, flatten_book_marks,
                                is_restatement)


def mk(title, summary):
    return Signal(
        signal_id="s1", event_key="k-1", title=title, summary=summary, us="", domain="政策合规",
        questions=["Q02"], topics=[], relevance=2, cred="green", event_date="2026-11-19",
        discovered_at="2026-11-19T12:08:14+08:00",
        sources=[SourceRef(name="某官方", url="https://x.gov.cn/a", tier="official")], topic_id=866,
    )


# ---------- 复述判定 ----------

def test_restatement_detects_rewording_of_title():
    t = "国家能源局发布《中国虚拟电厂发展报告（2026）》"
    assert is_restatement(t, "国家能源局发布了《中国虚拟电厂发展报告（2026）》。")
    assert is_restatement(t, t)


def test_restatement_keeps_real_news_sentence():
    t = "Qwen相关事件显示自托管AI模型需更强控制"
    assert not is_restatement(t, "Dark Reading报道指出，部署AI模型需更强管控。")


# ---------- 摘要去重 ----------

def test_drop_restating_lead_keeps_following_sentence():
    t = "国家能源局发布《中国虚拟电厂发展报告（2026）》"
    s = "国家能源局发布了《中国虚拟电厂发展报告（2026）》。该报告由官方渠道新华网能源频道报道。"
    assert drop_restating_lead(t, s) == "该报告由官方渠道新华网能源频道报道。"


def test_drop_restating_lead_never_empties_summary():
    """整段都在复述时保留原文——清空会被下游判成「摘要为空」丢信号。"""
    t = "某厂发布新框架"
    s = "某厂发布新框架。"
    assert drop_restating_lead(t, s) == s
    assert drop_restating_lead(t, s, keep_if_all=False) == ""


def test_flatten_nested_book_marks():
    assert flatten_book_marks("《某局发布《某报告》解读》") == "《某局发布「某报告」解读》"
    assert flatten_book_marks("某局发布《某报告》") == "某局发布《某报告》"


# ---------- 01 卡片文案 ----------

def test_brief_text_does_not_repeat_title():
    text = _brief_text(mk("国家能源局发布《中国虚拟电厂发展报告（2026）》",
                          "国家能源局发布了《中国虚拟电厂发展报告（2026）》。该报告由官方渠道新华网能源频道报道。"))
    assert text.count("中国虚拟电厂发展报告") == 1
    assert text == "国家能源局发布《中国虚拟电厂发展报告（2026）》：该报告由官方渠道新华网能源频道报道。"


def test_brief_text_title_only_when_summary_is_pure_restatement():
    text = _brief_text(mk("某局发布新报告", "某局发布新报告。"))
    assert text == "某局发布新报告"


def test_brief_text_still_appends_new_information():
    text = _brief_text(mk("Gemini API 推出 Tool Retrieval 功能预览版",
                          "Google DeepMind 在 Gemini API 中推出 Tool Retrieval 预览功能，允许智能体访问数百个工具。"))
    assert text.startswith("Gemini API 推出 Tool Retrieval 功能预览版：Google DeepMind")


# ---------- 入库规整 ----------

def test_validate_item_normalizes_title_and_summary(cfg):
    obj = {"keep": True, "domain": "政策合规", "questions": ["Q02"], "topics": [], "relevance": 2,
           "title": "《某局发布《中国虚拟电厂发展报告（2026）》解读》",
           "summary": "《某局发布「中国虚拟电厂发展报告（2026）」解读》。报告中披露投运592个。",
           "us": "", "event_key": "nea-vpp-2026-11-19", "event_date": "2026-11-19"}
    r = validate_item(obj, cfg)
    assert "「中国虚拟电厂发展报告（2026）」" in r.title
    assert r.summary.startswith("报告中披露投运592个")


def test_prompt_forbids_title_summary_duplication(cfg):
    """提示词里必须写明这两条口径，否模型会继续把标题写进摘要首句。"""
    p = build_system_prompt(cfg)
    assert "必须与原文标题语义一致" in p
    assert "不要嵌套" in p
    assert "第一句必须给标题之外的新信息" in p
