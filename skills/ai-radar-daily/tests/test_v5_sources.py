#!/usr/bin/env python3
"""V5.0 社区信号源解析（HN / GitHub Trending / Hugging Face / arXiv）——用真实页面精简样本离线测试。"""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "scripts"))
import fetch_sources as fs  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")
FIX = ROOT / "fixtures"


class Parsers(unittest.TestCase):
    def test_hn_real_sample(self):
        items = fs.parse_hn(json.loads((FIX / "hn_front_page_sample.json").read_text(encoding="utf-8")), TZ)
        top = items[0]
        self.assertEqual(top["heat"], 805)
        self.assertEqual(top["published_date"], "2026-10-29")  # 14:13Z = 北京 22:13
        self.assertTrue(items[-1]["url"].startswith("https://news.ycombinator.com/item?id="))  # Launch HN 无外链

    def test_hn_keyword_filter(self):
        items = fs.parse_hn(json.loads((FIX / "hn_front_page_sample.json").read_text(encoding="utf-8")), TZ)
        kept = [i["title"] for i in items if fs._match_keywords(i["title"], fs.AI_KEYWORDS_DEFAULT)]
        self.assertIn("LLM Voice", kept)
        self.assertNotIn("DoorDash to acquire Instacart", kept)
        self.assertNotIn("Grace Blackwell needs a new scheduler: 27% more usable GPUs from the same racks", kept)

    def test_short_keywords_whole_word(self):
        self.assertFalse(fs._match_keywords("Fresh air for cities", ["ai"]))
        self.assertTrue(fs._match_keywords("An agentic framework", ["agent"]))

    def test_github_trending_real_sample(self):
        items = fs.parse_github_trending((FIX / "github_trending_sample.html").read_text(encoding="utf-8"))
        self.assertEqual(len(items), 3)
        self.assertTrue(all(i["url"].startswith("https://github.com/") and i["heat"] for i in items))
        self.assertNotIn("&amp;", " ".join(i["title"] for i in items))

    def test_hf_models(self):
        items = fs.parse_hf_models([{"id": "mistralai/Mistral-Large-4", "pipeline_tag": "text-generation",
                                     "trendingScore": 412, "likes": 900}, {"likes": 1}])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["heat"], 412)
        self.assertEqual(items[0]["url"], "https://huggingface.co/mistralai/Mistral-Large-4")

    def test_arxiv_atom_title_whitespace(self):
        atom = b"""<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><entry>
          <title>Turn-Level Credit Assignment
            for Long-Horizon Agents</title><link rel='alternate' href='http://arxiv.org/abs/2610.00001v1'/>
          <published>2026-10-29T17:59:00Z</published></entry></feed>"""
        items = fs.parse_feed(atom, TZ)
        self.assertEqual(items[0]["published_date"], "2026-10-30")

    def test_run_marks_signal_layer(self):
        with tempfile.TemporaryDirectory() as td:
            reg = Path(td) / "s.json"
            reg.write_text(json.dumps({"defaults": {"timeout_seconds": 1}, "sources": [
                {"id": "hn", "entity": "HN", "type": "hn_top", "url": "http://127.0.0.1:9/x", "layer": "signal",
                 "cat": "社区热议"}]}), encoding="utf-8")
            res = fs.run(date(2026, 10, 30), "Asia/Shanghai", [], sources_path=reg)
            self.assertEqual(res["sources"][0]["status"], "ERROR")
            self.assertIn("signal_items", res["summary"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
