#!/usr/bin/env python3
"""5.2.0 论坛首页数据：正文 → air-pulse/1 JSON、校验、底座帖幂等更新与 run.py feed 编排。"""
import argparse
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "scripts"))
sys.path.insert(0, str(TESTS))

import homepage_feed as HF  # noqa: E402
import test_v5_layout as V5  # noqa: E402

D = date(2026, 10, 30)
NOW = datetime.fromisoformat("2026-10-30T09:40:00+08:00")
DAILY = "https://aicopilot.goldwind.com.cn:3211/t/ai-10-30/700"


def sample_log():
    log = V5.search_log()
    for c in log["candidates"]:
        if c["decision"] == "SIGNAL" and c["title"].startswith("某模型权重"):
            c["heat"] = 805
            c["discovered_at"] = "2026-10-30T08:00:00+08:00"
    return log


def build(md=None, **kw):
    kw.setdefault("blocked", set())
    return HF.build_feed(md or V5.build_md(), D, daily_url=DAILY, search_log=sample_log(), now=NOW, **kw)


class TestBuildFeed(unittest.TestCase):
    def test_counts_and_meta(self):
        feed, warns = build()
        self.assertEqual(feed["schema"], "air-pulse/1")
        self.assertEqual(feed["issue_date"], "2026-10-30")
        self.assertEqual(feed["date"], "2026年10月30日 · 星期五")
        self.assertEqual(feed["updated_at"], "2026-10-30T09:40:00+08:00")
        self.assertEqual(feed["daily_url"], DAILY)
        self.assertEqual(feed["mainline"], "测试主线。")
        self.assertEqual((len(feed["curated"]), len(feed["tracking"]), len(feed["radar"])), (6, 1, 2))
        self.assertEqual(warns, [])

    def test_curated_card_fields(self):
        feed, _ = build()
        first = feed["curated"][0]
        self.assertEqual(first["section"], "今日要紧")
        self.assertEqual(first["cred"], "g")
        self.assertEqual(first["us"], "数字员工也需要行为监测。")
        self.assertEqual([s["url"] for s in first["sources"]], ["https://official.example/a", "https://media.example/b"])
        self.assertEqual(first["sources"][0]["label"], "official.example")  # 去掉 “↗”
        self.assertEqual(first["topic_url"], DAILY)
        speed = next(c for c in feed["curated"] if c["section"] == "行业速递")
        self.assertNotIn("us", speed)

    def test_lead_brief_picks(self):
        feed, _ = build()
        self.assertEqual(feed["lead"]["title"], "甲公司发布监测体系（10月29日）")
        self.assertEqual(feed["lead"]["url"], DAILY)
        self.assertEqual(len(feed["brief"]), 3)
        self.assertTrue(feed["brief"][0]["text"].startswith("甲公司发布监测体系："))  # 标题去日期 + 首句
        self.assertNotIn("10月29日", feed["brief"][0]["text"])
        self.assertEqual(feed["brief"][2]["text"].split("：")[0], "快讯一")  # 今日要紧只有 2 条，用行业速递补
        self.assertEqual(feed["picks"][0]["kind"], "做法")
        self.assertEqual(feed["picks"][1]["title"], "某模型权重将开放（预告）")  # 热度最高的信号排前

    def test_radar_heat_and_tail(self):
        feed, _ = build()
        hot = next(s for s in feed["radar"] if s["title"].startswith("某模型权重"))
        self.assertEqual(hot["heat"], 805)
        self.assertEqual(hot["from"], "澎湃新闻")
        self.assertEqual(hot["cat"], "行业动态")
        self.assertEqual(hot["url"], "https://news.example/s2")
        self.assertEqual(feed["community"], [{"who": "做设备的同事", "title": "帖子", "url": "https://community.example/a"}])
        self.assertEqual(feed["question"], "测试问题？")

    def test_headline_joiner_and_semicolon(self):
        self.assertEqual(HF.headline("苹果官宣：12 月上线（10月31日）", "首批 11 月 4 日测试；12 月推出。", 160),
                         "苹果官宣：12 月上线。首批 11 月 4 日测试。")
        self.assertEqual(HF.headline("某模型发布（10月29日）", "参数三万亿。其余略。", 160), "某模型发布：参数三万亿。")
        self.assertEqual(HF.headline("扩展被换成后门版（10月30日）", "Cursor 通告：约 5000 人受影响。", 160),
                         "扩展被换成后门版。Cursor 通告：约 5000 人受影响。")

    def test_picks_diverse_and_no_rumors(self):
        sigs = [("新品与工具", "r", "GitHub 今日 +1500★", "仓库甲", "一句。", "https://github.com/a/a"),
                ("新品与工具", "r", "GitHub 今日 +1400★", "仓库乙", "一句。", "https://github.com/b/b"),
                ("社区热议", "r", "Hacker News 300", "热帖丙", "一句。", "https://hn.example/c"),
                ("传闻待证", "r", "某爆料号 9999", "传闻丁", "一句。", "https://rumor.example/d")]
        md = V5.build_md(sigs=sigs)
        feed, _ = HF.build_feed(md, D, daily_url=DAILY, search_log=None, blocked=set(), now=NOW)
        titles = [p["title"] for p in feed["picks"]]
        self.assertEqual(titles[:3], ["给沙箱出网做双层封堵", "仓库甲", "热帖丙"])  # 做法 → 每类一条
        self.assertNotIn("传闻丁", titles)
        self.assertEqual(next(s for s in feed["radar"] if s["title"] == "仓库甲")["heat"], 1500)  # 标签兜底热度

    def test_community_real_issue_format(self):
        """11-02 实刊写法：`* **【场景】**：[标题](URL)`，与模板写法并存。"""
        md = V5.build_md().replace(
            "- **做设备的同事：** [帖子](https://community.example/a)",
            "* **【做交付、质量、审核的同事】**：[一个场景彻底拆开 01](https://forum.example/t/x/626/1)\n"
            "- **做设备的同事：** [帖子](https://community.example/a)")
        feed, _ = build(md)
        self.assertEqual([c["who"] for c in feed["community"]], ["做交付、质量、审核的同事", "做设备的同事"])
        self.assertEqual(feed["community"][0]["url"], "https://forum.example/t/x/626/1")

    def test_blocked_links_dropped(self):
        feed, warns = build(blocked={"media.example"})
        first = feed["curated"][0]
        self.assertEqual([s["url"] for s in first["sources"]], ["https://official.example/a"])
        self.assertTrue(any("media.example" in w for w in warns))
        self.assertEqual(HF.validate_feed(feed, blocked={"media.example"})[0], [])

    def test_not_v5_rejected(self):
        with self.assertRaises(ValueError):
            HF.build_feed("# 旧版正文", D)

    def test_frontend_parser_contract(self):
        """组件 v0.1 只认识 brief.text/url、curated、tracking、radar；新增字段不得改变这些键。"""
        feed, _ = build()
        for b in feed["brief"]:
            self.assertEqual(set(b) - {"text", "url", "source"}, set())
        for s in feed["radar"]:
            self.assertTrue({"cat", "cred", "from", "title", "summary", "url", "tags"} <= set(s))


class TestValidateAndRaw(unittest.TestCase):
    def test_valid_sample(self):
        feed, _ = build()
        errs, _ = HF.validate_feed(feed, blocked=set())
        self.assertEqual(errs, [])

    def test_rejects_bad_schema_and_urls(self):
        feed, _ = build()
        feed["schema"] = "air-pulse/2"
        feed["radar"][0]["url"] = "javascript:alert(1)"
        feed["curated"][0]["sources"][0]["url"] = "https://kotaku.com/x"
        errs, _ = HF.validate_feed(feed, blocked={"kotaku.com"})
        self.assertTrue(any("schema" in e for e in errs))
        self.assertTrue(any("radar[0].url" in e for e in errs))
        self.assertTrue(any("kotaku.com" in e for e in errs))

    def test_empty_feed_rejected(self):
        errs, _ = HF.validate_feed({"schema": "air-pulse/1", "issue_date": "2026-10-30", "date": "x", "updated_at": "y"},
                                   blocked=set())
        self.assertTrue(any("不能全为空" in e for e in errs))

    def test_raw_roundtrip_and_lenient_fence(self):
        feed, _ = build()
        raw = HF.build_raw(feed)
        self.assertTrue(raw.startswith("> 本帖是「AI 雷达」首页的数据底座"))
        self.assertEqual(HF.extract_feed(raw), feed)
        same_line = "```json\n" + json.dumps(feed, ensure_ascii=False) + "```"
        self.assertEqual(HF.extract_feed(same_line), feed)
        self.assertEqual(HF.raw_sha(raw), HF.raw_sha(raw.replace("\n", "\r\n") + "\n\n"))


class FakeForum:
    """模拟 Discourse：/t/{id}.json、/posts/{id}.json 读写。"""

    def __init__(self, raw="", readable=True, corrupt_write=False):
        self.raw = raw
        self.readable = readable
        self.corrupt_write = corrupt_write
        self.puts = 0

    def curl_json(self, url, method="GET", headers=None, data_file=None, form_fields=None):
        if url.endswith("/t/632.json"):
            return {"post_stream": {"posts": [{"post_number": 1, "id": 9001}]}}
        if url.endswith("/posts/9001.json") and method == "GET":
            return {"raw": self.raw} if self.readable else None
        if url.endswith("/posts/9001.json") and method == "PUT":
            self.puts += 1
            body = json.loads(Path(data_file).read_text(encoding="utf-8"))
            self.raw = body["post"]["raw"] + ("篡改" if self.corrupt_write else "")
            return {"post": {"id": 9001}}
        return None

    @staticmethod
    def auth_headers(config, json_content=False):
        return {}


CONFIG = {"url": "https://forum.example", "api_key": "k", "api_user": "u"}


class TestPublishFeed(unittest.TestCase):
    def run_with(self, forum, feed, **kw):
        with patch.object(HF, "_curl", return_value=(forum.curl_json, forum.auth_headers)):
            return HF.publish_feed(feed, CONFIG, topic_id=632, **kw)

    def test_update_then_noop(self):
        feed, _ = build()
        forum = FakeForum(raw="```json\n{}\n```")
        res = self.run_with(forum, feed)
        self.assertEqual((res["status"], res["post_id"], forum.puts), ("UPDATED", 9001, 1))
        res2 = self.run_with(forum, feed)
        self.assertEqual((res2["status"], forum.puts), ("NOOP", 1))

    def test_refuses_older_issue(self):
        feed, _ = build()
        live = dict(feed, issue_date="2026-11-02")
        forum = FakeForum(raw=HF.build_raw(live))
        res = self.run_with(forum, feed)
        self.assertEqual((res["status"], res["code"], forum.puts), ("BLOCKED", "FEED_OLDER_THAN_LIVE", 0))
        res2 = self.run_with(forum, feed, allow_older=True)
        self.assertEqual(res2["status"], "UPDATED")

    def test_unreadable_post_not_written(self):
        feed, _ = build()
        forum = FakeForum(readable=False)
        res = self.run_with(forum, feed)
        self.assertEqual((res["status"], res["code"], forum.puts), ("FAILED", "FEED_READ_FAILED", 0))

    def test_readback_mismatch_reported(self):
        feed, _ = build()
        forum = FakeForum(raw="old", corrupt_write=True)
        res = self.run_with(forum, feed)
        self.assertEqual((res["status"], res["code"]), ("FAILED", "FEED_READBACK_MISMATCH"))


class TestRunFeedCommand(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = os.environ.get("AI_RADAR_WORK_DIR")
        os.environ["AI_RADAR_WORK_DIR"] = str(Path(self._tmp.name) / "ai-radar-daily")
        import run  # noqa: WPS433
        self.run = run
        self.rd = "2026-10-30"
        wd = run.get_work_dir()
        wd.mkdir(parents=True, exist_ok=True)
        f = run.work_files(self.rd)
        f["md"].write_text(V5.build_md(), encoding="utf-8")
        f["search_log"].write_text(json.dumps(sample_log(), ensure_ascii=False), encoding="utf-8")
        state = run.empty_run_state(self.rd)
        state["forum"] = {"status": "PUBLISHED", "topic_id": 700, "post_id": 7001, "url": DAILY}
        f["state"].parent.mkdir(parents=True, exist_ok=True)
        f["state"].write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        self.f = f

    def tearDown(self):
        if self._env is None:
            os.environ.pop("AI_RADAR_WORK_DIR", None)
        else:
            os.environ["AI_RADAR_WORK_DIR"] = self._env
        self._tmp.cleanup()

    def args(self, **kw):
        base = dict(report_date=self.rd, timezone="Asia/Shanghai", env_file="/nonexistent/.env", mode="normal",
                    dry_run=False, confirm=False, session_id="test", takeover=False, allow_older=False)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_preview_writes_local_only(self):
        with patch("run.run_subprocess") as sp:
            rc = self.run.cmd_feed(self.args())
        self.assertEqual(rc, 0)
        sp.assert_not_called()
        feed = json.loads(self.f["homepage_feed"].read_text(encoding="utf-8"))
        self.assertEqual(feed["daily_url"], DAILY)
        self.assertEqual(feed["next_update_at"], "2026-11-02T09:30:00+08:00")  # 周五 → 下周一

    def test_requires_published_forum(self):
        state = json.loads(self.f["state"].read_text(encoding="utf-8"))
        state["forum"] = {"status": "PENDING"}
        self.f["state"].write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.run.cmd_feed(self.args()), 2)
        self.assertFalse(self.f["homepage_feed"].exists())

    def test_confirm_records_state(self):
        def fake_sub(cmd, dry_run, env_extra=None):
            self.assertIn("publish", cmd)
            self.f["homepage_feed_state"].write_text(json.dumps(
                {"status": "UPDATED", "code": "FEED_UPDATED", "topic_id": 632, "post_id": 9001,
                 "issue_date": self.rd, "sha256": "abc"}), encoding="utf-8")
            return 0
        with patch("run.run_subprocess", side_effect=fake_sub):
            rc = self.run.cmd_feed(self.args(confirm=True))
        self.assertEqual(rc, 0)
        state = json.loads(self.f["state"].read_text(encoding="utf-8"))
        self.assertEqual(state["homepage_feed"]["status"], "UPDATED")
        self.assertEqual(state["homepage_feed"]["post_id"], 9001)
        self.assertTrue(any(c["type"] == "homepage_feed" for c in state["confirmations"]))
        self.assertEqual(state["run_status"], "INIT")  # 首页更新不改动日报主状态

    def test_publish_failure_does_not_touch_run_status(self):
        def fake_sub(cmd, dry_run, env_extra=None):
            self.f["homepage_feed_state"].write_text(json.dumps(
                {"status": "BLOCKED", "code": "FEED_OLDER_THAN_LIVE"}), encoding="utf-8")
            return 2
        with patch("run.run_subprocess", side_effect=fake_sub):
            rc = self.run.cmd_feed(self.args(confirm=True))
        self.assertEqual(rc, 2)
        state = json.loads(self.f["state"].read_text(encoding="utf-8"))
        self.assertEqual(state["homepage_feed"]["code"], "FEED_OLDER_THAN_LIVE")
        self.assertFalse(any(c["type"] == "homepage_feed" for c in state["confirmations"]))


if __name__ == "__main__":
    unittest.main()
