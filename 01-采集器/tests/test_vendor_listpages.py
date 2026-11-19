"""claude.com/resources 与 anthropic.com/events 的解析（2026-11-20 新增官方源）。

两家都不是 RSS，只能列表页抓。两个真实坑：
1. claude.com 的卡片**本身就是 <a>**，容器里没有子 <a>，所以 link 要支持 "self"；
2. 卡片链接的文本混着「Article / Nov 12, 2026 / 标题 / 产品标签」，直接取 <a> 全文会得到
   "Article Nov 12, 2026 Using Claude Opus 5.5 ... Claude Code" 这种标题，所以要能指定 title 元素。
另外两个站的日期都写英文月名（"Nov 30, 2026"），原来的解析器只有 ISO/中文/邮件格式三种，全接不住。
"""
from pathlib import Path

import pytest

from collector.sources import fetchers

HTML = (Path(__file__).parent / "fixtures" / "vendor-cards.html").read_text(encoding="utf-8")


def _src(url="https://claude.com/resources", **sel):
    return {"name": "测试源", "module": "ext_ai", "kind": "listpage", "tier": "official",
            "default_domain": "工具做法", "questions": ["Q03"],
            "url": url, "selectors": sel}


def test_listpage_self_link_with_title_and_date(monkeypatch):
    monkeypatch.setattr(fetchers, "http_get", lambda url, timeout=20: HTML)
    got = fetchers.fetch(_src(item='a[class*="ResourceCard"][class*="__card"]', link="self",
                              title='h3[class*="ResourceCard"][class*="__title"]',
                              date='span[class*="ResourceCard"][class*="__meta"]'))
    titles = [c.title for c in got]
    # 标题只取 h3，不带 "Article" 和日期
    assert "Using Claude Opus 5.5 as your daily driver" in titles
    assert all("Article" not in t and "Nov 12" not in t for t in titles)
    dated = {c.title: c.published_at for c in got if c.published_at}
    assert dated["The AI-native SDLC playbook"].startswith("2026-08-21")
    assert dated["Using Claude Opus 5.5 as your daily driver"].startswith("2026-11-12")
    # "1 min watch" 不是日期 —— 留空交给 undated_policy，不许猜
    video = [c for c in got if "Fable" in c.title][0]
    assert video.published_at is None


def test_listpage_event_item(monkeypatch):
    monkeypatch.setattr(fetchers, "http_get", lambda url, timeout=20: HTML)
    got = fetchers.fetch(_src("https://www.anthropic.com/events",
                              item="div.event_list_item", link="a[href]",
                              title="div.event_list_column.cc-name",
                              date="div.event_list_column.cc-date"))
    assert [c.title for c in got] == ["Anthropic at AWS re:Invent 2026"]
    assert got[0].published_at.startswith("2026-11-30")
    assert got[0].url == "https://www.anthropic.com/events/anthropic-at-aws-re-invent-2026"


@pytest.mark.parametrize("raw,iso", [
    ("Nov 30, 2026", "2026-11-30T00:00:00+08:00"),
    ("November 19, 2026", "2026-11-19T00:00:00+08:00"),
    ("Aug 21, 2026", "2026-08-21T00:00:00+08:00"),
    ("1 min watch", None),
    ("", None),
])
def test_english_month_dates(raw, iso):
    assert fetchers.to_cst_iso(raw) == iso
