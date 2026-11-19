"""详情页补日期（2026-11-19 漏斗归因后加）。

归因：每轮 64 条候选因为「列表页不给日期」被 undated_policy=drop 丢掉，
其中包含国家网信办的政策条目。实测这些站的列表页没有日期元素，详情页才有：
  The Hacker News  <meta itemprop="datePublished" ...>
  中国能源网        <div class="showtitinfo">2026-11-19 09:17:23</div>

这里守住三件事：能补到、有预算上限、有缓存不重复抓。
"""
from collector.sources import fetchers

LIST_HTML = """<html><body><ul class="news">
<li><a href="/a1.html">第一条新闻标题</a></li>
<li><a href="/a2.html">第二条新闻标题</a></li>
<li><a href="/a3.html">第三条新闻标题</a></li>
</ul></body></html>"""

DETAIL_META = ('<html><head><meta itemprop="datePublished" '
               'content="2026-11-19T10:00:00+08:00"/></head><body></body></html>')
DETAIL_SEL = '<html><body><div class="showtitinfo">2026-11-18 09:17:23</div></body></html>'


def _src(**kw):
    s = {"name": "测试站", "module": "ext_ai", "kind": "listpage", "url": "https://example.com/",
         "tier": "media", "default_domain": "能源AI", "questions": ["Q01"],
         "selectors": {"item": "ul.news li", "link": "a"}}
    s.update(kw)
    return s


def _patch(monkeypatch, tmp_path, detail_html):
    monkeypatch.setattr(fetchers, "DATE_CACHE", tmp_path / "date_cache.json")
    calls = []

    def fake_get(url, timeout=20):
        calls.append(url)
        return LIST_HTML if url.rstrip("/") == "https://example.com" else detail_html

    monkeypatch.setattr(fetchers, "http_get", fake_get)
    return calls


def test_detail_date_from_meta(tmp_path, monkeypatch):
    calls = _patch(monkeypatch, tmp_path, DETAIL_META)
    got = fetchers.fetch(_src(date_from_detail=True))
    assert len(got) == 3
    assert all(c.published_at.startswith("2026-11-19") for c in got)
    assert len(calls) == 4           # 1 次列表 + 3 次详情


def test_detail_date_from_selector(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path, DETAIL_SEL)
    got = fetchers.fetch(_src(date_from_detail=True, detail_date_selector="div.showtitinfo"))
    assert all(c.published_at.startswith("2026-11-18") for c in got)


def test_detail_date_respects_budget(tmp_path, monkeypatch):
    calls = _patch(monkeypatch, tmp_path, DETAIL_META)
    got = fetchers.fetch(_src(date_from_detail=True, max_detail_fetches=2))
    assert len(calls) == 3           # 1 次列表 + 最多 2 次详情
    assert sum(1 for c in got if c.published_at) == 2


def test_detail_date_cache_avoids_refetch(tmp_path, monkeypatch):
    calls = _patch(monkeypatch, tmp_path, DETAIL_META)
    fetchers.fetch(_src(date_from_detail=True))
    first = len(calls)
    calls.clear()
    got = fetchers.fetch(_src(date_from_detail=True))
    assert len(calls) == 1           # 第二次只抓列表页
    assert all(c.published_at.startswith("2026-11-19") for c in got)
    assert first == 4


def test_detail_date_off_by_default(tmp_path, monkeypatch):
    """没开 date_from_detail 的信源不该多发一个请求。"""
    calls = _patch(monkeypatch, tmp_path, DETAIL_META)
    got = fetchers.fetch(_src())
    assert len(calls) == 1
    assert all(c.published_at is None for c in got)


def test_detail_date_failure_keeps_item_undated(tmp_path, monkeypatch):
    """详情页拿不到就留空，由 undated_policy 决定去留，不要瞎猜日期。"""
    monkeypatch.setattr(fetchers, "DATE_CACHE", tmp_path / "date_cache.json")

    def fake_get(url, timeout=20):
        if url.rstrip("/") == "https://example.com":
            return LIST_HTML
        raise RuntimeError("HTTP 503")

    monkeypatch.setattr(fetchers, "http_get", fake_get)
    got = fetchers.fetch(_src(date_from_detail=True, max_detail_fetches=5))
    assert len(got) == 3
    assert all(c.published_at is None for c in got)
