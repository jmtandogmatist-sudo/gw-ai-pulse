"""日期兜底提取（pubdate）与 72h 窗口门禁。

对应 2026-11-16 修的漏洞：
    原先 pipeline 写 `if c.published_at and ... < window`，published_at 为 None 时
    整段短路，零日期条目永不受窗口约束。生产上因此发布了一条 2026-10-30 的
    网信办公告（超窗 17 天）和一条 11-05 的中国电力报。
"""
from datetime import datetime, timedelta

import pytest

from collector.models import Candidate
from collector.pubdate import date_from_url, fill_published_at
from collector.sources.fetchers import CST

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=CST)


# ---------- date_from_url ----------

@pytest.mark.parametrize("url,expect", [
    # 政策站带 Y-M/D 分隔
    ("https://www.cac.gov.cn/2026-10/30/c_1795114304542687.htm", "2026-10-30"),
    ("https://www.cac.gov.cn/2026-11/13/c_1796327469005843.htm", "2026-11-13"),
    # 中文新闻站：/202611/t20261105_
    ("https://www.cpnn.com.cn/news/nytt/202611/t20261105_1924573.html", "2026-11-05"),
])
def test_extracts_date_from_url(url, expect):
    got = date_from_url(url, now=NOW)
    assert got is not None
    assert got.startswith(expect)


@pytest.mark.parametrize("url", [
    "https://github.com/elementalsouls/Claude-BugHunter",     # 无日期
    "https://qwen.ai/blog?id=qwen4-max",                       # 无日期
    "https://www.gwec.net/news/cop31-global-wind-industry-unites-behind-declaration",
    "https://arxiv.org/abs/2611.18439v1",                      # 2611 不是合法月份
])
def test_returns_none_when_no_date(url):
    assert date_from_url(url, now=NOW) is None


def test_does_not_mistake_version_number_for_date():
    """v2.4.10 / 2026-02-30 之类的假日期不能被当成发布日期。"""
    assert date_from_url("https://example.com/v2.4.10/docs", now=NOW) is None
    assert date_from_url("https://example.com/2026-02-30/x", now=NOW) is None   # 2 月无 30 日


def test_ignores_date_in_hostname():
    assert date_from_url("https://2026-10-30.example.com/page", now=NOW) is None


def test_rejects_far_future_date():
    """URL 里的未来日期多半是解析错误，不能当成发布日期。"""
    assert date_from_url("https://example.com/2027-06-01/x", now=NOW) is None


# ---------- fill_published_at ----------

def test_fill_does_not_overwrite_existing():
    c = Candidate(module="ext_ai", source_name="s", source_tier="media",
                  url="https://www.cac.gov.cn/2026-10/30/c_1.htm", title="t",
                  published_at="2026-11-15T00:00:00+08:00")
    assert fill_published_at(c, now=NOW) is True
    assert c.published_at == "2026-11-15T00:00:00+08:00"


def test_fill_sets_from_url():
    c = Candidate(module="ext_ai", source_name="s", source_tier="media",
                  url="https://www.cpnn.com.cn/news/nytt/202611/t20261105_1924573.html",
                  title="t")
    assert fill_published_at(c, now=NOW) is True
    assert c.published_at.startswith("2026-11-05")


def test_fill_returns_false_when_undatable():
    c = Candidate(module="ext_ai", source_name="s", source_tier="media",
                  url="https://github.com/foo/bar", title="t")
    assert fill_published_at(c, now=NOW) is False
    assert c.published_at is None
