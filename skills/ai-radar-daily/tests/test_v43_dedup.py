#!/usr/bin/env python3
"""V4.3 回归：跨期去重解析、候选 uid、重要度结算、已见索引三级查重、缺跑扫描。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import fetch_recent  # noqa: E402
import holiday_pool as hp  # noqa: E402
import seen_index as si  # noqa: E402

V4_POST = """# AI 雷达日报｜10.29

> 今日主线：xxx

## 今日要紧

### 【需行动】OpenAI 发布 GPT-X（10月28日）

正文。

**对我们：** 判断。

[details="🟢 来源｜官方 + 独立交叉"]
- 发布：[官方](https://openai.com/index/gpt-x/) · [Reuters](https://www.reuters.com/tech/gpt-x?utm_source=x)
[/details]

### 某开源框架 2.0（10月29日）

正文。

[details="🟡 来源｜媒体转述"]
- [媒体](https://example.com/a)
[/details]

---

## 行业速递

- 🟢 **某公司完成融资（10月28日）：** 一句话。

[details="来源｜1 条速递"]
- 融资：[媒体](https://example.com/b)
[/details]

## 社区今日

- **同事：** [站内帖](https://aicopilot.goldwind.com.cn:3211/t/600)

## 今日一问

一个问题。

### 集团AI能力地图

- [AI基础能力支持](https://aiplatform.goldwind.com.cn/portal/algorithmMarket)

### 技术交流群
"""


class FetchRecentV4(unittest.TestCase):
    def test_extracts_v4_titles_and_urls(self):
        titles, urls = fetch_recent.extract_news_items(V4_POST)
        self.assertIn("OpenAI 发布 GPT-X（10月28日）", titles)
        self.assertIn("某开源框架 2.0（10月29日）", titles)
        self.assertIn("某公司完成融资（10月28日）", titles)
        self.assertIn("https://openai.com/index/gpt-x/", urls)
        self.assertIn("https://example.com/b", urls)

    def test_excludes_tail_and_community(self):
        titles, urls = fetch_recent.extract_news_items(V4_POST)
        self.assertFalse(any("goldwind.com.cn" in u for u in urls))
        self.assertFalse(any("技术交流群" in t for t in titles))

    def test_v3_still_supported(self):
        raw = "## 今日大事件\n\n🔹 **旧版标题（9月1日）**\n正文\n来源：[官方](https://a.com/x)\n"
        titles, urls = fetch_recent.extract_news_items(raw)
        self.assertEqual(titles, ["旧版标题（9月1日）"])
        self.assertEqual(urls, ["https://a.com/x"])


def _c(i, imp=0, decision="SELECTED", **kw):
    d = {"id": f"NEWS-{i:02d}", "title": f"事件{i}", "event_date": "2026-10-28",
         "source_url": f"https://x.com/{i}", "decision": decision, "importance": imp}
    d.update(kw)
    return d


class PoolUidAndRanking(unittest.TestCase):
    def test_same_id_across_days_does_not_collide(self):
        pool = hp.default_pool()
        hp.append_candidates(pool, "2026-10-24", [_c(1)])
        hp.append_candidates(pool, "2026-10-25", [dict(_c(1), title="另一件事", source_url="https://y.com")])
        first = [c for c in hp.pending_candidates(pool) if c["collected_on"] == "2026-10-24"]
        hp.settle_pool(pool, "2026-10-26", first)
        remain = hp.pending_candidates(pool)
        self.assertEqual(len(remain), 1)
        self.assertEqual(remain[0]["title"], "另一件事")

    def test_ranked_by_importance_and_selected_only(self):
        pool = hp.default_pool()
        hp.append_candidates(pool, "2026-10-24", [_c(1, 3), _c(2, 9), _c(3, 5, "REJECTED_STALE")])
        hp.append_candidates(pool, "2026-10-25", [_c(1, 7)])
        ranked = hp.ranked_pending(pool, 30)
        self.assertEqual([c["importance"] for c in ranked], [9.0, 7.0, 3.0])
        self.assertEqual(len(hp.ranked_pending(pool, 2)), 2)


class SeenIndex(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.conn = si.connect(Path(self.td.name))
        si.record_candidates(self.conn, "2026-10-28", [
            _c(1, title="OpenAI 发布 GPT-X 新模型（10月28日）", source_url="http://www.openai.com/index/gpt-x/?utm_source=t",
               event_key="openai|发布|gpt-x"),
            _c(2, title="英伟达财报超预期", source_url="https://n.com/q3", decision="REJECTED_LOW_PRIORITY"),
        ])
        si.mark_published(self.conn, ["2026-10-28::NEWS-01"], "AI-RADAR-2026-10-28")

    def tearDown(self):
        self.conn.close()
        self.td.cleanup()

    def test_l1_url_normalized(self):
        r = si.check_candidate(self.conn, "2026-10-30", {"title": "完全不同", "source_url": "https://openai.com/index/gpt-x"})
        self.assertEqual(r["verdict"], "DUPLICATE")
        self.assertEqual(r["hits"][0]["level"], "L1_URL")

    def test_l2_title(self):
        r = si.check_candidate(self.conn, "2026-10-30", {"title": "OpenAI发布GPT-X新模型", "source_url": "https://z.com"})
        self.assertEqual(r["verdict"], "DUPLICATE")
        self.assertTrue(any(h["level"] == "L2_TITLE" for h in r["hits"]))

    def test_l2_containment_long_vs_short_title(self):
        si.record_candidates(self.conn, "2026-10-29", [_c(5, title="首批风云卫星极端天气 AI 模型发布，强对流预警提前至 4 小时（10月29日）",
                                                        source_url="https://a.com/fy")])
        r = si.check_candidate(self.conn, "2026-10-31", {"title": "首批风云卫星极端天气AI模型正式发布", "source_url": "https://b.com/fy"})
        self.assertEqual(r["verdict"], "DUPLICATE")
        # 短标题不走包含度，避免误伤
        r2 = si.check_candidate(self.conn, "2026-10-31", {"title": "AI模型发布", "source_url": "https://c.com"})
        self.assertEqual(r2["verdict"], "NEW")

    def test_l3_event_key(self):
        r = si.check_candidate(self.conn, "2026-10-30", {"title": "奥特曼宣布新一代模型上线", "source_url": "https://z.com",
                                                         "event_key": "OpenAI | 发布 | GPT-X", "event_date": "2026-10-29"})
        self.assertEqual(r["verdict"], "DUPLICATE")
        self.assertTrue(any(h["level"] == "L3_EVENT" for h in r["hits"]))

    def test_rejected_history_also_blocks(self):
        r = si.check_candidate(self.conn, "2026-10-30", {"title": "英伟达财报超预期", "source_url": "https://other.com"})
        self.assertEqual(r["verdict"], "DUPLICATE")

    def test_update_flag(self):
        r = si.check_candidate(self.conn, "2026-10-30", {"title": "OpenAI 发布 GPT-X 新模型", "source_url": "https://z.com/new",
                                                         "is_update": True})
        self.assertEqual(r["verdict"], "UPDATE")
        hard = si.check_candidate(self.conn, "2026-10-30", {"title": "x", "source_url": "https://openai.com/index/gpt-x/",
                                                            "is_update": True})
        self.assertEqual(hard["verdict"], "DUPLICATE")

    def test_new_and_same_day_rerun(self):
        self.assertEqual(si.check_candidate(self.conn, "2026-10-30", {"title": "全新事件", "source_url": "https://new.com"})["verdict"], "NEW")
        self.assertEqual(si.check_candidate(self.conn, "2026-10-28", {"title": "英伟达财报超预期", "source_url": "https://n.com/q3"})["verdict"], "NEW")

    def test_lookback_window(self):
        r = si.check_candidate(self.conn, "2026-12-30", {"title": "英伟达财报超预期", "source_url": "https://other.com"})
        self.assertEqual(r["verdict"], "NEW")

    def test_dedup_search_log_rewrites_decision(self):
        log = {"candidates": [_c(9, title="全新事件A", source_url="https://new.com/a"),
                              _c(10, title="OpenAI 发布 GPT-X 新模型", source_url="https://openai.com/index/gpt-x/")]}
        rep = si.dedup_search_log(self.conn, "2026-10-30", log)
        self.assertEqual((rep["new"], rep["duplicate"]), (1, 1))
        self.assertEqual(log["candidates"][1]["decision"], "REJECTED_DUPLICATE")
        self.assertIn("seen_index_dedup", log)


class SignalPoolEventKey(unittest.TestCase):
    """signal_pool → 检索清单 → 已见索引 L3 的事件键贯通（跨源同事件的兜底）。"""

    SIGNAL = {
        "signal_id": "abc123",
        "event_key": "caplite-prompt-injection-defense-2026-11-13",
        "title": "CapLite提出无需模型生成安全工件的小模型智能体提示注入防御方案",
        "domain": "安全", "relevance": 2, "cred": "red", "event_date": "2026-11-13",
        "discovered_at": "2026-11-13T15:05:31+08:00", "summary": "摘要", "us": "对金风",
        "sources": [{"name": "arXiv", "url": "https://arxiv.org/abs/2611.17115v1", "tier": "research"}],
    }
    TOPIC = {"id": 729, "title": "CapLite 提示注入防御", "slug": "caplite"}

    def _item(self):
        import signal_pool
        return signal_pool.to_item(self.TOPIC, self.SIGNAL, "https://forum.example", "")

    def test_to_item_emits_official_event_key(self):
        item = self._item()
        self.assertEqual(item["event_key"], self.SIGNAL["event_key"])
        # 旧名保留为别名，值必须一致
        self.assertEqual(item["event_key_hint"], item["event_key"])

    def test_candidate_event_key_accepts_both_names(self):
        key = self.SIGNAL["event_key"]
        self.assertEqual(si.candidate_event_key({"event_key": key}), key)
        self.assertEqual(si.candidate_event_key({"event_key_hint": key}), key)
        # 空串与缺失等价，都视为"没有事件键"，交给 L1/L2 兜底
        self.assertFalse(si.candidate_event_key({"event_key": ""}))
        self.assertIsNone(si.candidate_event_key({}))

    def test_cross_source_same_event_hits_l3_even_without_url_or_title(self):
        """日报先用媒体 URL + 改写标题收录；采集器给的原始 URL/标题完全不同，仍应判重。"""
        with tempfile.TemporaryDirectory() as td:
            conn = si.connect(Path(td))
            try:
                si.record_candidates(conn, "2026-11-12", [_c(
                    7, title="某媒体改写后的完全不相关标题", source_url="https://media.example/caplite.html",
                    event_key=self.SIGNAL["event_key"], event_date="2026-11-13")])
                si.mark_published(conn, ["2026-11-12::NEWS-07"], "ISSUE-TEST")
                item = self._item()
                # 执行 Agent 照 SKILL.md 把 event_key 原样抄进候选
                cand = {"title": item["title"], "source_url": item["url"],
                        "event_key": item["event_key"], "event_date": "2026-11-13"}
                verdict = si.check_candidate(conn, "2026-11-13", cand)
                self.assertEqual(verdict["verdict"], "DUPLICATE")
                self.assertTrue(any(h["level"] == "L3_EVENT" for h in verdict["hits"]))
                # 只写旧字段名同样要命中（向前兼容）
                legacy = dict(cand)
                legacy["event_key_hint"] = legacy.pop("event_key")
                legacy["title"] = "另一个改写标题"
                self.assertEqual(si.check_candidate(conn, "2026-11-13", legacy)["verdict"], "DUPLICATE")
            finally:
                conn.close()

    def test_record_candidates_persists_key_from_hint(self):
        with tempfile.TemporaryDirectory() as td:
            conn = si.connect(Path(td))
            try:
                si.record_candidates(conn, "2026-11-12", [{"title": "标题", "source_url": "https://a.com",
                                                           "event_key_hint": self.SIGNAL["event_key"],
                                                           "event_date": "2026-11-13"}])
                row = conn.execute("SELECT event_key FROM items").fetchone()
                self.assertEqual(row["event_key"], self.SIGNAL["event_key"].lower())
            finally:
                conn.close()


class RunCommands(unittest.TestCase):
    def _run(self, wd, *args):
        env = dict(os.environ, AI_RADAR_WORK_DIR=str(wd), AI_RADAR_SESSION_ID="test")
        return subprocess.run([sys.executable, str(SCRIPTS / "run.py"), *args],
                              capture_output=True, text=True, env=env)

    def test_recover_missed_and_dedup_flow(self):
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            pool = hp.default_pool()
            hp.append_candidates(pool, "2026-10-26", [_c(1)])
            hp.save_pool(wd, pool)
            r = self._run(wd, "recover-missed", "--report-date", "2026-10-29")
            self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
            self.assertIn("2026-10-27", r.stdout)
            self.assertIn("2026-10-28", r.stdout)
            log = {"report_date": "2026-10-29", "candidates": [_c(1, title="事件1"), _c(2, title="完全新的事件二")]}
            (wd / "AI雷达日报_2026-10-29_检索清单.json").write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
            # 先把 10-26 批次写进索引
            with si.connect(wd) as conn:
                si.record_candidates(conn, "2026-10-26", [_c(1)])
            r = self._run(wd, "dedup", "--report-date", "2026-10-29")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = json.loads((wd / "AI雷达日报_2026-10-29_检索清单.json").read_text(encoding="utf-8"))
            self.assertEqual(out["candidates"][0]["decision"], "REJECTED_DUPLICATE")
            self.assertEqual(out["candidates"][1]["decision"], "SELECTED")
            self.assertTrue((wd / "AI雷达日报_2026-10-29_去重报告.json").exists())


class FetchSources(unittest.TestCase):
    RSS = b"""<?xml version='1.0'?><rss><channel>
      <item><title>New model</title><link>https://openai.com/index/a</link><pubDate>Thu, 29 Oct 2026 17:00:00 GMT</pubDate></item>
      <item><title>Old post</title><link>https://openai.com/index/b</link><pubDate>Mon, 20 Oct 2026 10:00:00 GMT</pubDate></item>
    </channel></rss>"""
    ATOM = b"""<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>
      <entry><title>Atom item</title><link rel='alternate' href='https://x.com/a'/><updated>2026-10-30T01:00:00Z</updated></entry>
    </feed>"""

    def test_parse_rss_and_timezone(self):
        import fetch_sources as fs
        from zoneinfo import ZoneInfo
        items = fs.parse_feed(self.RSS, ZoneInfo("Asia/Shanghai"))
        self.assertEqual(items[0]["published_date"], "2026-10-30")  # 17:00 GMT = 次日 01:00 北京
        self.assertEqual(items[1]["published_date"], "2026-10-20")

    def test_parse_atom(self):
        import fetch_sources as fs
        from zoneinfo import ZoneInfo
        items = fs.parse_feed(self.ATOM, ZoneInfo("Asia/Shanghai"))
        self.assertEqual(items[0]["url"], "https://x.com/a")

    def test_window(self):
        import fetch_sources as fs
        from datetime import date
        self.assertEqual(fs.allowed_dates(date(2026, 10, 30)), ["2026-10-29", "2026-10-30"])
        self.assertEqual(len(fs.allowed_dates(date(2026, 11, 2))), 4)

    def test_errors_recorded_not_faked(self):
        import fetch_sources as fs
        from datetime import date
        with tempfile.TemporaryDirectory() as td:
            reg = Path(td) / "s.json"
            reg.write_text(json.dumps({"defaults": {"timeout_seconds": 1}, "sources": [
                {"id": "bad", "entity": "X", "type": "rss", "url": "http://127.0.0.1:9/feed"},
                {"id": "pg", "entity": "Y", "type": "page", "url": "https://y.com"}]}), encoding="utf-8")
            res = fs.run(date(2026, 10, 30), "Asia/Shanghai", [], sources_path=reg)
            self.assertEqual(res["sources"][0]["status"], "ERROR")
            self.assertFalse(res["sources"][0]["checked"])
            self.assertEqual(len(res["manual_pages"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
