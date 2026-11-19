import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import preflight_check  # noqa: E402


MD = """# AI 雷达日报｜9.18

> 导语：测试日报。

## 今日要紧

### 【需行动】事件甲（9月18日）

正文甲。

**对我们：** 测试判断甲。

[details="🟢 来源｜官方 + 独立交叉"]
- [官方](https://official.example/a) · [媒体](https://media.example/b)
[/details]

### 【了解】旧事更新（跟踪报道·9月16日）

正文乙。

**对我们：** 测试判断乙。

[details="🟡 来源｜当事方口径"]
- [官方旧](https://official.example/old) · [媒体旧](https://media.example/old)
[/details]

---

## 金风相关

### 能源事件

能源正文。

[details="来源｜官方 + 独立交叉"]
- [能源源一](https://energy.example/a) · [能源源二](https://energy.example/b)
[/details]

### 金风事件二

金风正文二。

[details="来源｜官方 + 独立交叉"]
- [金风源一](https://energy.example/c) · [金风源二](https://energy.example/d)
[/details]

---

## 工具与做法

### Builder事件

Builder 正文。

[details="来源｜官方文档"]
- [Builder源一](https://builder.example/a) · [Builder源二](https://builder.example/b)
[/details]

### 实践事件

实践正文。

[details="来源｜官方发布 + 独立报道"]
- [实践源一](https://practice.example/a) · [实践源二](https://practice.example/b)
[/details]

---

## 行业速递

- 🟢 **快讯甲（9月18日）：** 测试。

[details="来源｜1 条速递"]
- [速递源](https://digest.example/a)
[/details]

---

### 集团AI能力地图

往期合集

### 技术交流群

"""

EVENTS = [
    ("事件甲（9月18日）", ["https://official.example/a", "https://media.example/b"], "2026-09-18"),
    ("旧事更新（跟踪报道·9月16日）", ["https://official.example/old", "https://media.example/old"], "2026-09-16"),
    ("能源事件", ["https://energy.example/a", "https://energy.example/b"], "2026-09-18"),
    ("金风事件二", ["https://energy.example/c", "https://energy.example/d"], "2026-09-18"),
    ("Builder事件", ["https://builder.example/a", "https://builder.example/b"], "2026-09-18"),
    ("实践事件", ["https://practice.example/a", "https://practice.example/b"], "2026-09-18"),
]


def receipt(title, urls, event_date):
    return {
        "claim_id": "C-" + title,
        "claim_text": title,
        "claim_type": "action",
        "materiality": "critical",
        "event_date": event_date,
        "sources": [
            {"tier": "T0", "url": url, "locator": "正文", "verbatim": "原文", "supports": "full"}
            for url in urls
        ],
        "verification": {"status": "PASS"},
    }


def semantic_payload():
    return {
        "report_date": "2026-09-18",
        "overall": "PASS",
        "recent_dedup_checked": True,
        "items": [
            {
                "title": title,
                "status": "PASS",
                "source_urls": urls,
                "evidence_receipts": [receipt(title, urls, event_date)],
            }
            for title, urls, event_date in EVENTS
        ],
        "deep_focus": {},
    }


def search_payload():
    candidates = []
    for index, (title, urls, event_date) in enumerate(EVENTS, 1):
        candidates.append({
            "id": f"NEWS-{index:02d}",
            "title": title,
            "event_date": event_date,
            "source_name": "来源一",
            "source_url": urls[0],
            "source_urls": urls,
            "category": "测试",
            "decision": "SELECTED",
            "reason": "跟踪报道" if "跟踪报道" in title else "",
        })
    return {
        "report_date": "2026-09-18",
        "window": "d2",
        "queries": ["test"],
        "official_sources_checked": [
            {"name": name, "checked": True, "url": f"https://official.example/{index}"}
            for index, name in enumerate([
                "openai", "anthropic", "google", "meta", "microsoft", "xai",
                "deepseek", "qwen", "bytedance", "智谱", "月之暗面", "minimax", "百度", "腾讯",
            ], 1)
        ],
        "candidates": candidates,
        "recall_review": {"status": "PASS", "major_missed_events": []},
    }


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.md = self.root / "AI雷达日报_2026-09-18.md"
        self.semantic = self.root / "semantic.json"
        self.search = self.root / "search.json"
        self.self_check = self.root / "self-check.md"
        self.md.write_text(MD, encoding="utf-8")
        self.semantic.write_text(json.dumps(semantic_payload(), ensure_ascii=False), encoding="utf-8")
        self.search.write_text(json.dumps(search_payload(), ensure_ascii=False), encoding="utf-8")
        digest = hashlib.sha256(self.md.read_bytes()).hexdigest()
        self.self_check.write_text(
            f"SELF_CHECK_STATUS: PASS\nREPORT_DATE: 2026-09-18\nRULESET_VERSION: V4.1\nSOURCE_SHA256: {digest}\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def run_preflight(self):
        return preflight_check.run_preflight(self.md, self.semantic, self.search, "2026-09-18")

    def test_valid_payload_passes(self):
        result = self.run_preflight()
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["error_count"], 0)

    def test_body_semantic_source_mismatch_fails(self):
        data = json.loads(self.semantic.read_text(encoding="utf-8"))
        for item in data["items"]:
            if item["title"] == "实践事件":
                item["source_urls"] = ["https://practice.example/a"]
                item["evidence_receipts"][0]["sources"] = item["evidence_receipts"][0]["sources"][:1]
        self.semantic.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.run_preflight()
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(error["code"] == "SOURCE_URLS_MISMATCH" for error in result["errors"]), result)

    def test_selected_candidate_must_cover_body_sources(self):
        data = json.loads(self.search.read_text(encoding="utf-8"))
        for cand in data["candidates"]:
            if cand["title"] == "Builder事件":
                cand["source_urls"] = ["https://builder.example/a"]
        self.search.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.run_preflight()
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(error["code"] == "SEARCH_SOURCE_MISMATCH" for error in result["errors"]), result)

    def test_candidate_schema_invalid_decision_fails(self):
        data = json.loads(self.search.read_text(encoding="utf-8"))
        data["candidates"][0]["decision"] = "SELECTED_PRIMARY"
        self.search.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.run_preflight()
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(error["code"] == "CANDIDATE_DECISION_INVALID" for error in result["errors"]), result)

    def test_tracking_title_format_fails_when_out_of_window(self):
        self.md.write_text(MD.replace("旧事更新（跟踪报道·9月16日）", "旧事更新（9月16日·跟踪报道）"), encoding="utf-8")
        data = semantic_payload()
        data["items"][1]["title"] = "旧事更新（9月16日·跟踪报道）"
        self.semantic.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.run_preflight()
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(error["code"] == "TRACKING_FORMAT_INVALID" for error in result["errors"]), result)

    def test_selected_weak_source_fails(self):
        data = json.loads(self.search.read_text(encoding="utf-8"))
        data["candidates"][0]["source_urls"].append("https://www.sohu.com/a/123")
        self.search.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.run_preflight()
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(error["code"] == "SELECTED_WEAK_SOURCE" for error in result["errors"]), result)

    def test_missing_required_official_source_fails(self):
        data = json.loads(self.search.read_text(encoding="utf-8"))
        data["official_sources_checked"] = data["official_sources_checked"][:2]
        self.search.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.run_preflight()
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(error["code"] == "OFFICIAL_SOURCES_INCOMPLETE" for error in result["errors"]), result)

    def test_daily_interpretation_too_long_fails(self):
        long_line = "**对我们：** " + "长" * 91
        self.md.write_text(MD.replace("**对我们：** 测试判断甲。", long_line), encoding="utf-8")
        result = self.run_preflight()
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(error["code"] == "DAILY_INTERPRETATION_TOO_LONG" for error in result["errors"]), result)

    def test_feature_body_too_long_fails(self):
        long_body = "Builder 正文。" + "长" * 301
        self.md.write_text(MD.replace("Builder 正文。", long_body, 1), encoding="utf-8")
        result = self.run_preflight()
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(error["code"] == "FEATURE_BODY_TOO_LONG" for error in result["errors"]), result)

    def test_package_dry_run_passes(self):
        result = preflight_check.run_preflight(
            self.md,
            self.semantic,
            self.search,
            "2026-09-18",
            self_check_path=self.self_check,
            package_dry_run=True,
        )
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["package_dry_run"]["status"], "PASS")
        self.assertEqual(result["package_dry_run"]["counts"]["events"], 6)

    def test_package_dry_run_fails_on_self_check_sha_mismatch(self):
        self.self_check.write_text(
            "SELF_CHECK_STATUS: PASS\nREPORT_DATE: 2026-09-18\nRULESET_VERSION: V4.1\nSOURCE_SHA256: 0\n",
            encoding="utf-8",
        )
        result = preflight_check.run_preflight(
            self.md,
            self.semantic,
            self.search,
            "2026-09-18",
            self_check_path=self.self_check,
            package_dry_run=True,
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(error["code"] == "PACKAGE_DRY_RUN_FAILED" for error in result["errors"]), result)


if __name__ == "__main__":
    unittest.main()
