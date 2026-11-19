import shutil
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from collector.alerts import Alerter
from collector.config import load_config
from collector.discourse import FakeDiscourse
from collector.fakes import FakeLLM
from collector.sources.fetchers import CST
from collector.store import Store

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
NOW = datetime(2026, 11, 5, 7, 30, tzinfo=CST)


@pytest.fixture
def cfg(tmp_path):
    shutil.copytree(ROOT / "config", tmp_path / "config")
    shutil.copytree(FIX, tmp_path / "tests" / "fixtures")
    settings = yaml.safe_load((tmp_path / "config" / "settings.yaml").read_text(encoding="utf-8"))
    settings["discourse"]["signal_category_id"] = 42
    settings["discourse"]["post_interval_seconds"] = 0
    # gw-ai-pulse（2026-11-13）：生产配置已把 feed.enabled 改为 true（采集器每轮写 632）。
    # 测试需要「不写底座帖」作为默认基线，否则断言底座帖不变/写入次数恒定的用例会失败。
    # 需要验证底座帖刷新时由 test_feed_refresh_only_touches_radar 显式打开。
    settings["feed"]["enabled"] = False
    # gw-ai-pulse（2026-11-19）：今日焦点长文默认关掉——它要抓真实网页，
    # 单测里必须离线。验证焦点链路的用例自己打开并替换 collector.focus.fetch_text。
    settings.setdefault("focus", {})["enabled"] = False
    (tmp_path / "config" / "settings.yaml").write_text(yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8")
    sources = {"sources": [
        {"name": "测试媒体", "module": "ext_ai", "kind": "rss", "url": f"file://{FIX / 'rss.xml'}",
         "tier": "media", "default_domain": "能源AI", "questions": ["Q01"]},
        {"name": "厂商博客", "module": "ext_ai", "kind": "rss", "url": f"file://{FIX / 'atom.xml'}",
         "tier": "official", "default_domain": "选型", "questions": ["Q03"]},
        {"name": "测试政策站", "module": "energy_policy", "kind": "listpage", "url": f"file://{FIX / 'list.html'}",
         "tier": "authority", "default_domain": "政策合规", "questions": ["Q02"],
         "include_keywords": ["人工智能"], "selectors": {"item": "ul.news li", "link": "a", "date": "span"}},
    ]}
    (tmp_path / "config" / "sources.yaml").write_text(yaml.safe_dump(sources, allow_unicode=True), encoding="utf-8")
    # 2026-11-16：检索词库会真的发网络请求（HN Algolia）。单元测试必须离线，
    # 这里写入一份关闭状态的 queries.yaml，覆盖从生产 config 复制过来的那份。
    (tmp_path / "config" / "queries.yaml").write_text(
        yaml.safe_dump({"version": "test", "enabled": False, "search_engines": [],
                        "queries": []}, allow_unicode=True), encoding="utf-8")
    return load_config(tmp_path)


@pytest.fixture
def forum(cfg):
    return FakeDiscourse(632, (FIX / "feed_632.md").read_text(encoding="utf-8"))


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def llm(cfg):
    return FakeLLM(cfg)


@pytest.fixture
def alerter(cfg):
    return Alerter(cfg, dry_run=True)
