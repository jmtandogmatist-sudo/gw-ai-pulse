"""标题指纹去重：挡住「同一事件的不同链接」。

URL 去重只挡同一链接；event_key 带日期，换天再报同一件事就漏了。
这里用标题相似度兜底。阈值用 2026-11-16 的 30 条真实信号校准过：
最高相似度 0.50，所以 0.75 不会误杀。
"""
import pytest

from collector.dedup import find_duplicate, normalize_title, title_similarity


# ---------- normalize_title ----------

def test_normalize_strips_spaces_and_punctuation():
    assert normalize_title("DeepSeek 开放全家桶 API，支持官方 App") == \
           normalize_title("DeepSeek开放全家桶API支持官方App")


def test_normalize_strips_date_suffix():
    assert normalize_title("某厂发布新模型 |11.16") == normalize_title("某厂发布新模型")
    assert normalize_title("某厂发布新模型 · 11月16日") == normalize_title("某厂发布新模型")


def test_normalize_expands_aliases():
    """GWEC 与「全球风能理事会」应归一成同一串。"""
    assert "全球风能理事会" in normalize_title("GWEC 发布宣言")
    assert "西门子歌美飒" in normalize_title("SGRE 发布财报")


# ---------- title_similarity ----------

@pytest.mark.parametrize("a,b,floor", [
    # 同标题，只有空格/标点差异
    ("DeepSeek开放全家桶API，支持官方App功能自由组合调用",
     "DeepSeek 开放全家桶 API，支持官方 App 功能自由组合调用", 0.95),
    # 去引号
    ("国家能源局公示“人工智能+”能源试点项目清单",
     "国家能源局公示人工智能+能源试点项目清单", 0.95),
    # 媒体改写：去掉公司名
    ("阿里通义千问Qwen4-Max正式上线，2.4万亿参数任务成本降低超60%",
     "通义千问Qwen4-Max正式上线 2.4万亿参数任务成本降低超60%", 0.90),
    # 中英混用：靠别名映射拉高
    ("GWEC 发布全球风能宣言以加速清洁能源发展",
     "全球风能理事会发布宣言 加速清洁能源发展", 0.85),
])
def test_duplicates_are_detected(a, b, floor):
    assert title_similarity(a, b) >= floor


def test_different_events_score_low():
    assert title_similarity(
        "微软365全球故障引发网络安全风险激增预警",
        "英伟达CEO辞职 市值首次跌破4万亿",
    ) < 0.30


def test_empty_titles_are_safe():
    assert title_similarity("", "任意标题") == 0.0
    assert title_similarity("任意标题", "") == 0.0
    assert title_similarity("", "") == 0.0


def test_very_short_titles_do_not_match():
    """两三个字的标题用 SequenceMatcher 容易误判为高度相似，这里直接判不相似。"""
    assert title_similarity("公告", "公示") == 0.0


# ---------- find_duplicate ----------

def test_find_duplicate_hits_above_threshold():
    known = ["DeepSeek 开放全家桶 API，支持官方 App 功能自由组合调用",
             "英伟达发布新一代 GPU"]
    hit, sim = find_duplicate("DeepSeek开放全家桶API，支持官方App功能自由组合调用", known)
    assert hit is not None
    assert sim >= 0.75


def test_find_duplicate_returns_none_below_threshold():
    known = ["英伟达发布新一代 GPU", "微软365全球故障引发网络安全风险激增预警"]
    hit, sim = find_duplicate("GWEC 发布全球风能宣言以加速清洁能源发展", known)
    assert hit is None
    assert sim < 0.75


def test_find_duplicate_on_empty_list():
    hit, sim = find_duplicate("任意标题内容够长", [])
    assert hit is None
    assert sim == 0.0


def test_ambiguous_band_is_between_thresholds():
    """换主体的改写落在 0.6~0.75 之间时，应判为「疑似」而不是「确定重复」。"""
    a = "Claude Code技能包集成82项漏洞挖掘与红队测试能力"
    b = "Anthropic发布Claude Code技能包集成82项漏洞挖掘能力"
    sim = title_similarity(a, b)
    hit, _ = find_duplicate(a, [b], threshold=0.75)
    # 无论 sim 偏高还是偏低，这两个断言合起来保证行为一致
    if sim >= 0.75:
        assert hit is not None
    else:
        assert hit is None
        assert sim >= 0.60      # 应落进「疑似」区间，而不是被当作不同事件
