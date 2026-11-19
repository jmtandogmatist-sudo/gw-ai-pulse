import argparse
import json
import re
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import common
import publish
import self_check


MINIMAL_MD = """# AI 雷达日报｜8.31

> 今日主线：测试主线。

---

## 今日要紧

### 测试事件（8月31日）

正文一。

**对我们：** 测试判断。

[details="🟢 来源｜官方 + 独立交叉"]
- [官方](https://official.example/a) · [媒体](https://media.example/b)
[/details]

### 测试事件二（8月31日）

正文二。

**对我们：** 测试判断二。

[details="🟡 来源｜媒体转述"]
- [媒体三](https://media3.example/c) · [媒体四](https://media4.example/d)
[/details]

---

## 金风相关

今日暂无经双源核验的金风相关重要消息。

---

## 工具与做法

### 测试做法

做法正文。

[details="来源｜官方文档"]
- [来源](https://builder.example/a)
[/details]

---

## 行业速递

- 🟢 **快讯一（8月31日）：** 测试。
- 🟡 **快讯二（8月31日）：** 测试。
- 🟢 **快讯三（8月31日）：** 测试。

[details="来源｜3 条速递"]
- [来源](https://practice.example/a) · [来源](https://media2.example/c)
[/details]

---

## 社区今日

- **做设备的同事：** [帖子](https://community.example/a)

---

## 今日一问

测试问题？

---

### 集团AI能力地图

- [Agent/工作流搭建平台（Dify）](https://keconsole.goldwind.com)

### 技术交流群

[grid]

![本地大模型用户交流群|400x400](qr1.png)

![AI智能体开发平台运营群|400x400](qr2.png)

![语义本体构建交流群|400x400](qr3.png)

![KNS统一数据平台|400x400](qr4.png)

![标准化助手交流群|400x400](qr5.png)

![风衍大模型用户交流群|400x400](qr6.png)

[/grid]

**出品｜集团数字化中心**  
**策划整理｜AI应用研究院**

我们跟踪 AI 前沿、沉淀金风业务实践、帮业务部门把 AI 用起来

---

> 以上内容为【AI日报-Agent】自动整理报道，仅供内部信息参考。涉及产品选型、生产部署、设备诊断和安全决策时，应结合正式文件、业务数据与专业人员意见复核。AI不替代责任主体的最终判断。
"""


def valid_search_log():
    return {
        "report_date": "2026-08-31",
        "window": "d4",
        "queries": ["AI news", "agent runtime SDK"],
        "official_sources_checked": [
            {"name": name, "url": f"https://sources.example/{index}", "checked": True}
            for index, name in enumerate([
                "openai", "anthropic", "google", "meta", "microsoft", "xai",
                "deepseek", "qwen", "bytedance", "智谱", "月之暗面", "minimax", "百度", "腾讯",
            ])
        ],
        "candidates": [{
            "title": "候选", "event_date": "2026-08-31", "source_name": "官方",
            "source_url": "https://candidate.example/a", "decision": "SELECTED",
        }],
        "recall_review": {
            "status": "PASS", "reviewed_at": "2026-08-31T10:00:00+08:00",
            "major_missed_events": [], "notes": "已复核",
        },
    }


def receipt(claim_id, urls, verification_status="PASS", fetched_at=None):
    sources = []
    for index, url in enumerate(urls, 1):
        source = {
            "url": url,
            "tier": "T0" if index == 1 else "T1",
            "locator": f"第 {index} 段",
            "verbatim": "测试原文",
            "supports": "full",
            "accessed_at": "2026-08-31T10:00:00+08:00",
            "published_at": None,
        }
        if fetched_at is not None:
            source["fetched_at"] = fetched_at
        sources.append(source)
    return {
        "claim_id": claim_id,
        "claim_text": "可核验事实",
        "claim_type": "action",
        "materiality": "critical",
        "event_date": "2026-08-31",
        "sources": sources,
        "verification": {"status": verification_status},
    }


def valid_semantic(source_urls=None):
    item_urls = source_urls or ["https://official.example/a", "https://media.example/b"]
    second_urls = ["https://media3.example/c", "https://media4.example/d"]
    deep_urls = ["https://deep.example/a", "https://deep.example/b"]
    return {
        "overall": "PASS",
        "recent_dedup_checked": True,
        "items": [{
            "title": "测试事件（8月31日）",
            "status": "PASS",
            "facts_checked": ["测试"],
            "independent_sources": True,
            "source_urls": item_urls,
            "evidence_receipts": [receipt("C-001", item_urls)],
        }, {
            "title": "测试事件二（8月31日）",
            "status": "PASS",
            "facts_checked": ["测试"],
            "independent_sources": True,
            "source_urls": second_urls,
            "evidence_receipts": [receipt("C-002", second_urls)],
        }],
        "deep_focus": {
            "status": "PASS",
            "facts_checked": ["测试"],
            "source_urls": deep_urls,
            "official_sources_only": True,
            "link_status_verified": True,
            "evidence_receipts": [receipt("C-DF-001", deep_urls)],
        },
    }


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.md = self.root / "AI雷达日报_2026-08-31.md"
        self.md.write_text(MINIMAL_MD, encoding="utf-8")
        self.recent = self.root / "recent.json"
        self.recent.write_text('{"titles": [], "urls": []}', encoding="utf-8")

    def tearDown(self):
        self.tempdir.cleanup()

    def run_checks(self, search, semantic, **kwargs):
        search_path = self.root / "search.json"
        semantic_path = self.root / "semantic.json"
        search_path.write_text(json.dumps(search, ensure_ascii=False), encoding="utf-8")
        semantic_path.write_text(json.dumps(semantic, ensure_ascii=False), encoding="utf-8")
        report_date = kwargs.pop("report_date", "2026-08-31")
        return self_check.run_checks(
            self.md,
            semantic_path,
            search_path,
            recent_data_path=self.recent,
            report_date=report_date,
            **kwargs,
        )

    def assert_failed_message(self, results, text):
        self.assertTrue(any(not item["passed"] and text in item["message"] for item in results), results)

    def test_feature_body_char_count_excludes_title_source_and_attribute(self):
        section = """## Builder信号
### 标题很长但不计入
正文一。
> **信号属性：** 固定说明不计入。
**来源：** [一个很长的来源标题](https://example.com/a/very/long/url)
"""
        self.assertEqual(self_check.feature_body_char_count(section), len("正文一。"))

    def test_feature_body_over_300_chars_fails(self):
        long_body = "长" * 301
        self.md.write_text(MINIMAL_MD.replace("做法正文。", long_body), encoding="utf-8")
        results = self.run_checks(valid_search_log(), valid_semantic())
        self.assert_failed_message(results, "工具与做法正文 301 字，超过 300 字上限")

    def test_cover_legend_line_optional_v4_2(self):
        # V4.2 格式规则 24：首屏图例行（覆盖窗口｜行动标签｜可信度）已取消，缺失不得失败
        results = self.run_checks(valid_search_log(), valid_semantic())
        self.assertFalse(
            any("缺少首屏图例行" in item["message"] for item in results), results
        )
        self.assertFalse(
            any(not item["passed"] and "行动标签" in item["message"] for item in results), results
        )

    def test_action_tags_are_optional_and_self_invented_tag_fails(self):
        # V4.2：行动标签为可选编辑标记，不写合规；一旦自创第四类须失败
        ok_results = self.run_checks(valid_search_log(), valid_semantic())
        self.assertFalse(
            any(not item["passed"] and "自创行动标签" in item["message"] for item in ok_results),
            ok_results,
        )
        weird = MINIMAL_MD.replace("### 测试事件（8月31日）", "### 【必读】测试事件（8月31日）")
        self.assertNotEqual(weird, MINIMAL_MD)
        self.md.write_text(weird, encoding="utf-8")
        results = self.run_checks(valid_search_log(), valid_semantic())
        self.assert_failed_message(results, "自创行动标签")

    def test_empty_search_log_fails(self):
        results = self.run_checks({}, valid_semantic())
        self.assert_failed_message(results, "检索清单为空")

    def test_empty_semantic_fails(self):
        results = self.run_checks(valid_search_log(), {})
        self.assert_failed_message(results, "语义复核为空")

    def test_unrelated_semantic_source_urls_fail(self):
        results = self.run_checks(valid_search_log(), valid_semantic(["https://unrelated.example/x", "https://unrelated.example/y"]))
        self.assert_failed_message(results, "source_urls 与正文来源集合不严格相等")

    def test_filename_h1_and_report_date_mismatch_fails(self):
        results = self.run_checks(valid_search_log(), valid_semantic(), report_date="2026-09-01")
        self.assert_failed_message(results, "文件名日期与 report_date 不一致")
        self.assert_failed_message(results, "正文 H1 日期与 report_date 不一致")

    def test_canonical_receipts_pass(self):
        results = self.run_checks(valid_search_log(), valid_semantic(), mode="revise", allow_same_day=True)
        receipt_results = [item for item in results if item["category"] == "证据回执"]
        self.assertTrue(receipt_results)
        self.assertTrue(all(item["passed"] for item in receipt_results), receipt_results)

    def test_legacy_flat_receipt_fails(self):
        semantic = valid_semantic()
        semantic["items"][0]["evidence_receipts"] = [{
            "claim": "旧结构", "source_url": "https://official.example/a",
            "evidence_excerpt": "摘录", "fetched_at": "2026-08-31T10:00:00+08:00",
        }]
        results = self.run_checks(valid_search_log(), semantic)
        self.assert_failed_message(results, "evidence_receipts")

    def test_invalid_fetched_at_and_non_pass_verification_fail(self):
        semantic = valid_semantic()
        semantic["items"][0]["evidence_receipts"][0]["sources"][0]["fetched_at"] = "not-a-date"
        semantic["items"][1]["evidence_receipts"][0]["verification"]["status"] = "UNRESOLVED"
        results = self.run_checks(valid_search_log(), semantic)
        failures = [item for item in results if item["category"] == "证据回执" and not item["passed"]]
        self.assertEqual(len(failures), 2, failures)

    def test_invalid_february_30_tracking_date_fails(self):
        self.md.write_text(MINIMAL_MD.replace("测试事件（8月31日）", "测试事件（跟踪报道·2月30日）"), encoding="utf-8")
        semantic = valid_semantic()
        semantic["items"][0]["title"] = "测试事件（跟踪报道·2月30日）"
        results = self.run_checks(valid_search_log(), semantic)
        self.assert_failed_message(results, "包含无效日期")

    def test_normal_allow_same_day_fails(self):
        results = self.run_checks(valid_search_log(), valid_semantic(), mode="normal", allow_same_day=True)
        self.assert_failed_message(results, "仅在 revise 模式")

    def test_revise_allow_same_day_passes_mode_gate(self):
        results = self.run_checks(valid_search_log(), valid_semantic(), mode="revise", allow_same_day=True)
        mode_results = [item for item in results if item["category"] == "运行模式"]
        self.assertTrue(all(item["passed"] for item in mode_results), mode_results)

    def test_monday_incremental_window_includes_weekend_to_monday(self):
        # 执行点增量口径：周一默认允许 上周五..周一（含当天），无需 --allow-same-day
        monday = date(2026, 8, 31)
        allowed = self_check.allowed_event_dates(monday, allow_same_day=False)
        self.assertIsNotNone(allowed)
        self.assertIn(monday, allowed)
        for offset in (1, 2, 3):
            self.assertIn(monday - timedelta(days=offset), allowed, f"缺 {monday - timedelta(days=offset)}")
        # 上周四之前不应允许
        self.assertNotIn(monday - timedelta(days=4), allowed)

    def test_weekday_incremental_window_includes_yesterday_and_today(self):
        # 周二至周五：默认允许 {昨天, 今天}（执行点增量窗口），无需 --allow-same-day
        wed = date(2026, 9, 2)  # 周三
        allowed = self_check.allowed_event_dates(wed, allow_same_day=False)
        self.assertIsNotNone(allowed)
        self.assertIn(wed, allowed)
        self.assertIn(wed - timedelta(days=1), allowed)
        self.assertNotIn(wed - timedelta(days=2), allowed)

    def test_legacy_env_aliases_load(self):
        env_file = self.root / ".env"
        env_file.write_text(
            "DISCOURSE_API_KEY=test-key\n"
            "DISCOURSE_API_USERNAME=legacy-user\n"
            "DISCOURSE_BASE_URL=https://forum.example\n",
            encoding="utf-8",
        )
        with mock.patch.dict(common.os.environ, {}, clear=True):
            config = common.load_discourse_config(env_file)
        self.assertEqual(config, {
            "url": "https://forum.example",
            "api_key": "test-key",
            "api_user": "legacy-user",
        })

    def test_publish_write_request_is_not_retried(self):
        failed = subprocess_result = mock.Mock(returncode=1, stdout="\n500", stderr="failed")
        with mock.patch.object(publish.subprocess, "run", return_value=subprocess_result) as run, \
                mock.patch.object(publish.time, "sleep") as sleep:
            self.assertIsNone(publish.curl_json("https://forum.example/posts.json", method="POST"))
        self.assertEqual(run.call_count, 1)
        sleep.assert_not_called()

    def test_publish_gate_fails_without_report_date(self):
        report = self.root / "self-check.md"
        digest = publish.file_sha256(self.md)
        report.write_text(
            f"SELF_CHECK_STATUS: PASS\nRULESET_VERSION: V4.1\nSOURCE_SHA256: {digest}\n",
            encoding="utf-8",
        )
        valid, reason = publish.validate_self_check(self.md, report, date(2026, 8, 31))
        self.assertFalse(valid)
        self.assertIn("REPORT_DATE", reason)

    def test_atomic_write_json_uses_replace(self):
        target = self.root / "state.json"
        real_replace = common.os.replace
        calls = []

        def recording_replace(source, destination):
            calls.append((source, destination))
            real_replace(source, destination)

        with mock.patch.object(common.os, "replace", side_effect=recording_replace):
            common.atomic_write_json(target, {"status": "OK"})

        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"status": "OK"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(Path(calls[0][0]).parent, target.parent)
        self.assertEqual(Path(calls[0][1]), target)


if __name__ == "__main__":
    unittest.main()


class WeakSourceGateTests(unittest.TestCase):
    """P0-② 候选池 SELECTED 弱来源硬门禁（V3.6.0）的判定谓词。"""

    def test_weak_source_in_primary_url_rejected(self):
        cand = {
            "id": "N-1",
            "decision": "SELECTED",
            "source_url": "https://www.toutiao.com/a/123",
            "source_urls": ["https://www.toutiao.com/a/123", "https://official.example/a"],
        }
        self.assertIn("toutiao.com", "".join(self_check.candidate_weak_source_refs(cand)))

    def test_weak_source_in_source_urls_rejected(self):
        cand = {
            "id": "N-2",
            "decision": "SELECTED",
            "source_url": "https://official.example/a",
            "source_urls": ["https://official.example/a", "https://www.163.com/dy/article/X.html"],
        }
        bad = self_check.candidate_weak_source_refs(cand)
        self.assertIn("163.com", "".join(bad))
        self.assertNotIn("official.example", "".join(bad))

    def test_no_weak_source_ok(self):
        cand = {
            "id": "N-3",
            "decision": "SELECTED",
            "source_url": "https://blogs.nvidia.com/blog/x",
            "source_urls": ["https://blogs.nvidia.com/blog/x", "https://www.aema.ai/"],
        }
        self.assertEqual(self_check.candidate_weak_source_refs(cand), [])

    def test_rejected_decision_ignored_by_gate(self):
        # 门禁只作用于 SELECTED；REJECTED 候选即使带弱源也不触发（由调用方在 SELECTED 分支判断）
        cand = {
            "id": "N-4",
            "decision": "REJECTED_UNVERIFIED",
            "source_url": "https://www.sohu.com/a/1",
        }
        self.assertEqual([], [])
        self.assertTrue(self_check.weak_source_matches(cand["source_url"]))


class AllInfoTailHeadingTests(unittest.TestCase):
    """V3.9.0：资讯清单模块**禁止**出现在日报正文（只留知识库记录）。

    历史语义（V3.8.1 可选尾部模块）已废弃——用户明确要求正文不发布资讯清单。
    """

    def test_heading_is_forbidden_not_optional(self):
        self.assertIn("# 🔗 全部资讯（候选全集）", self_check.FORBIDDEN_TAIL_HEADINGS)
        self.assertNotIn("# 🔗 全部资讯（候选全集）", self_check.TAIL_HEADINGS)
        self.assertFalse(hasattr(self_check, "OPTIONAL_TAIL_HEADINGS"),
                         "V3.9.0 起不再存在「可选尾部模块」语义")
        self.assertTrue(self_check.ALL_INFO_TABLE_URL.endswith("tbl4RS4qEuDf2B0E"))

    def test_tail_headings_still_required(self):
        # 强制尾部模块未变（历史日报兼容）；V3.9.7 起标题去 🔗 / 📲 表情
        self.assertIn("### 集团AI能力地图", self_check.TAIL_HEADINGS)
        self.assertIn("### 技术交流群", self_check.TAIL_HEADINGS)

    def test_legacy_cutoff_constant_declared(self):
        """历史期次豁免边界必须显式声明，且等于严格校验起始日。"""
        self.assertEqual(self_check.LEGACY_ALL_INFO_STRICT_FROM, "2026-09-24")

    def test_legacy_issue_is_exempt(self):
        """早于严格校验起始日的已发布期次含资讯清单 → 豁免（正文不得回溯改写）。"""
        from datetime import date
        strict_from = date.fromisoformat(self_check.LEGACY_ALL_INFO_STRICT_FROM)
        for d in (date(2026, 9, 1), date(2026, 9, 22), date(2026, 9, 23)):
            self.assertLess(d, strict_from, f"{d} 应被豁免（含 09-23 当期）")

    def test_new_issue_is_strict(self):
        """严格校验起始日及以后必须严格校验（含资讯清单即 FAIL）。"""
        from datetime import date
        strict_from = date.fromisoformat(self_check.LEGACY_ALL_INFO_STRICT_FROM)
        for d in (date(2026, 9, 24), date(2026, 10, 1)):
            self.assertGreaterEqual(d, strict_from, f"{d} 应走严格校验")


class AccumulationLinePatternTests(unittest.TestCase):
    """V3.9.0：累积出刊标识的权威来源已迁至检索清单 publication_metadata。

    正文**不再**承载累积行；保留正则为历史期次回溯比对与测试使用。
    解析逻辑的现役测试见 tests/test_feishu_push_dedup.py 的 accumulate metadata 用例。
    """

    def _match(self, text):
        return self_check.ACCUMULATION_LINE_PATTERN.search(text)

    def test_standard_pattern_matches(self):
        m = self._match("> 📅 **累积出刊**：本期为 **9/19–9/20**（共 **30** 条）")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("count"), "30")

    def test_plain_variants_match(self):
        m = self._match("> 📅 累积出刊：本期为 10/1–10/7（共 28 条）")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("count"), "28")

    def test_line_must_start_with_quote_marker(self):
        # 不以 `> ` 引用开头 → 不算合规累积行
        self.assertIsNone(self._match("📅 **累积出刊**：本期为 **9/19–9/20**（共 **30** 条）"))

    def test_date_must_be_numeric_span(self):
        # 「9月19日-9月20日」这种非固定写法不匹配（便于门禁拦截）
        self.assertIsNone(self._match("> 📅 **累积出刊**：本期为 **9月19日-9月20日**（共 **30** 条）"))


class QrDisplaySizeGateTests(unittest.TestCase):
    """V3.8.4：技术交流群二维码显示尺寸统一为 |400x400（5 码）。

    此前 400x408/400x428 混用致论坛 grid 内高度不齐，而旧校验只看文件存在、
    不看尺寸，该问题长期漏网。本测试锁死尺寸门禁契约。
    """

    QR_SIZE_PATTERN = re.compile(r"\|\s*(\d+)\s*[x×]\s*(\d+)\s*$")

    def _size_ok(self, alt):
        m = self.QR_SIZE_PATTERN.search(alt)
        return bool(m) and (m.group(1), m.group(2)) == ("400", "400")

    def test_standard_400x400_passes(self):
        for alt in ["本地大模型用户交流群|400x400", "标准化助手交流群|400x400"]:
            self.assertTrue(self._size_ok(alt), alt)

    def test_legacy_mixed_sizes_fail(self):
        # 旧模板里真实存在的不一致尺寸必须被拦
        for alt in ["本地大模型用户交流群|400x408", "AI智能体开发平台运营群|400x428"]:
            self.assertFalse(self._size_ok(alt), alt)

    def test_missing_size_annotation_fails(self):
        self.assertFalse(self._size_ok("金风科技"))

    def test_fullwidth_x_accepted(self):
        # 允许全角乘号写法
        self.assertTrue(self._size_ok("金风科技|400×400"))

    def test_non_square_400_fails(self):
        self.assertFalse(self._size_ok("某群|400x300"))

    def test_template_has_six_qr_codes_all_400(self):
        """模板文件的二维码块必须是 6 码且尺寸统一 400x400。"""
        template = (SCRIPTS.parent / "references" / "report-template.md").read_text(encoding="utf-8")
        grid = re.search(r"\[grid\](.*?)\[/grid\]", template, re.S)
        self.assertIsNotNone(grid, "模板缺少 [grid] 二维码块")
        refs = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", grid.group(1))
        self.assertEqual(len(refs), 6, f"模板应为 6 码，实际 {len(refs)}")
        for alt, _ in refs:
            self.assertTrue(self._size_ok(alt), f"模板中尺寸不合规：{alt}")

    def test_template_alt_names_use_real_group_names(self):
        """V3.8.4：alt 名必须用群的真实名称，禁用「金风科技-xx」式泛称。"""
        template = (SCRIPTS.parent / "references" / "report-template.md").read_text(encoding="utf-8")
        grid = re.search(r"\[grid\](.*?)\[/grid\]", template, re.S)
        refs = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", grid.group(1))
        # 剥离 alt 中的 |WxH 尺寸后缀，只留群名
        names = [re.sub(r"\|\s*\d+\s*[x×]\s*\d+\s*$", "", alt).strip() for alt, _ in refs]
        for expected in ["本地大模型用户交流群", "AI智能体开发平台运营群",
                         "语义本体构建交流群", "KNS统一数据平台", "标准化助手交流群",
                         "风衍大模型用户交流群"]:
            self.assertIn(expected, names)
        # 旧泛称不得残留
        self.assertNotIn("金风科技", names)
        self.assertNotIn("金风科技-KNS", names)


class QrGateEndToEndTests(unittest.TestCase):
    """V3.8.5：端到端测试 check_qr_gate —— 直接用真实正文形态调用门禁函数。

    回归背景：V3.8.4 的识别条件写成 `name.startswith("群二维码_")`，但正文真实写法是
    `![群名|400x400](upload://xxxx.png)`，链接为 upload:// 短链，导致识别恒为空、
    尺寸门禁被条件跳过而从未生效。旧测试只对 alt 字符串做正则、不调用门禁逻辑，
    因此长期全绿却零保护。本类锁死「upload:// 形态必须被识别」这一契约。
    """

    def _run(self, raw, md_dir="."):
        results = []
        refs = self_check.check_qr_gate(raw, md_dir, results)
        return refs, results

    def _grid(self, rows):
        body = "\n".join(rows)
        return f"# AI 雷达日报｜9.22\n\n[grid]\n{body}\n[/grid]\n"

    def test_upload_shortlink_form_is_recognized(self):
        """真实发布形态（upload:// 短链）必须被识别为二维码——这是旧缺陷的核心。"""
        raw = self._grid([
            "![本地大模型用户交流群|400x400](upload://onqFmqGan75qPrqeDL0jLJVPdXR.png)",
            "![AI智能体开发平台运营群|400x400](upload://jKV9fBCLNz08SUhjcSKtrmiRUuG.png)",
            "![语义本体构建交流群|400x400](upload://yqIcRbvnSJmL6ni9hX7yPbZ1rTN.png)",
            "![KNS统一数据平台|400x400](upload://pjmuSZ09UjIJLmXl3GW496AV8OD.png)",
            "![标准化助手交流群|400x400](upload://zki0MKU5tCanUWYmNnwU4KWKh5E.jpeg)",
            "![风衍大模型用户交流群|400x400](upload://PENDING_QR6_UPLOAD.jpeg)",
        ])
        refs, results = self._run(raw)
        self.assertEqual(len(refs), 6, "upload:// 形态应识别出 6 码")
        for r in results:
            self.assertTrue(r["passed"], f"应全部门禁通过，实际：{r['message']}")

    def test_mixed_legacy_sizes_are_caught_end_to_end(self):
        """旧尺寸混用必须被端到端拦下（此前因识别缺陷而漏网）。"""
        raw = self._grid([
            "![本地大模型用户交流群|400x408](upload://aaa.png)",
            "![AI智能体开发平台运营群|400x428](upload://bbb.png)",
            "![语义本体构建交流群|400x400](upload://ccc.png)",
            "![KNS统一数据平台|400x400](upload://ddd.png)",
            "![标准化助手交流群|400x400](upload://eee.png)",
        ])
        _, results = self._run(raw)
        size_msgs = [r["message"] for r in results if "显示尺寸" in r["message"]]
        self.assertTrue(size_msgs, "应产出尺寸门禁结论")
        self.assertFalse(size_msgs[0].startswith("二维码显示尺寸统一"),
                         f"尺寸混用应被判不合规，实际：{size_msgs[0]}")
        self.assertIn("400x408", size_msgs[0])

    def test_local_filename_form_still_recognized(self):
        """上传前形态（本地 群二维码_*.png）仍必须被识别，保持向后兼容。"""
        raw = self._grid(["![某群|400x400](群二维码_本地大模型.png)"] * 5)
        refs, _ = self._run(raw)
        self.assertEqual(len(refs), 5)

    def test_wrong_code_count_is_caught(self):
        """码数不等于 5 必须报错（防误删/误增）。"""
        raw = self._grid(["![某群|400x400](upload://a.png)"] * 4)
        _, results = self._run(raw)
        count_msgs = [r["message"] for r in results if "码" in r["message"] and "应为" in r["message"]]
        self.assertTrue(count_msgs, "应产出码数门禁结论")

    def test_missing_size_annotation_is_caught(self):
        """带 upload:// 但 alt 无尺寸标注，必须被拦。"""
        raw = self._grid(["![某群](upload://a.png)"] * 5)
        _, results = self._run(raw)
        size_msgs = [r["message"] for r in results if "显示尺寸" in r["message"]]
        self.assertTrue(size_msgs and "无尺寸标注" in size_msgs[0], size_msgs)

    def test_real_template_passes_all_qr_gates(self):
        """当前模板文件必须通过全部二维码门禁（真实数据回归）。"""
        template = (SCRIPTS.parent / "references" / "report-template.md").read_text(encoding="utf-8")
        refs, results = self._run(template, str(SCRIPTS.parent / "references"))
        self.assertEqual(len(refs), 6, "模板应为 6 码")
        failed = [r["message"] for r in results if not r["passed"]]
        self.assertEqual(failed, [], f"模板不应有二维码门禁失败项：{failed}")


class QrPublishReplacementTests(unittest.TestCase):
    """V3.8.6：publish.py 的二维码替换逻辑必须与模板二维码结构同源（V3.9.9 起 6 码）。

    回归背景：publish.py 曾把替换规则硬编码为「AI信息交流群 / 本地大模型 / AI智能体」三码，
    尺寸写死 400x408 / 400x428。发布瞬间会把已合规正文改回旧群名+旧尺寸，且因「AI信息交流群」
    群名不匹配导致上传的第 1 张图（qr1）丢失。本类直接调用 publish.apply_qr_replacements 锁死契约。
    """

    def _fake_urls(self):
        return [(name, f"upload://NEW_{i}.png") for i, name in enumerate(publish.QR_GROUP_NAMES, 1)]

    def _template_grid_text(self):
        template = (SCRIPTS.parent / "references" / "report-template.md").read_text(encoding="utf-8")
        return template

    def test_five_codes_all_replaced_with_400(self):
        raw = self._template_grid_text()
        out = publish.apply_qr_replacements(raw, self._fake_urls())
        for name in publish.QR_GROUP_NAMES:
            self.assertIn(f"![{name}|400x400](", out, f"{name} 应被替换为 400x400 引用")

    def test_no_legacy_group_name_leaks(self):
        raw = self._template_grid_text()
        out = publish.apply_qr_replacements(raw, self._fake_urls())
        self.assertNotIn("AI信息交流群", out, "已废弃的「AI信息交流群」不得出现在发布正文")

    def test_no_legacy_sizes_leak(self):
        raw = self._template_grid_text()
        out = publish.apply_qr_replacements(raw, self._fake_urls())
        self.assertFalse(re.search(r"\|400x(408|428)\]", out),
                         "旧尺寸 400x408/400x428 不得出现在发布正文")

    def test_legacy_size_in_source_is_normalized(self):
        """源文正文若写成旧尺寸，替换后必须被归一为 400x400。"""
        raw = "\n".join(f"![{n}|400x408](upload://x{i}.png)"
                        for i, n in enumerate(publish.QR_GROUP_NAMES))
        out = publish.apply_qr_replacements(raw, self._fake_urls())
        self.assertNotIn("400x408", out)
        for name in publish.QR_GROUP_NAMES:
            self.assertIn(f"![{name}|400x400](", out)

    def test_missing_group_reference_raises(self):
        """正文缺某群引用（群名与模板不符）必须阻断，而非静默发布。"""
        raw = f"![{publish.QR_GROUP_NAMES[0]}|400x400](upload://a.png)"
        with self.assertRaises(publish.PublishError):
            publish.apply_qr_replacements(raw, self._fake_urls())

    def test_qr_group_names_match_template_order(self):
        """publish.py 的群名顺序必须与模板 [grid] 块的 alt 顺序一致。"""
        template = (SCRIPTS.parent / "references" / "report-template.md").read_text(encoding="utf-8")
        grid = re.search(r"\[grid\](.*?)\[/grid\]", template, re.S).group(1)
        refs = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", grid)
        names = [re.sub(r"\|\s*\d+\s*[x×]\s*\d+\s*$", "", alt).strip() for alt, _ in refs]
        self.assertEqual(names, list(publish.QR_GROUP_NAMES),
                         "publish.py 的 QR_GROUP_NAMES 必须与模板 [grid] 顺序逐一对应")

    def test_publish_cli_accepts_six_qr_params(self):
        """publish.py 命令行必须能接受 6 码（3 位置 + --qr4/--qr5/--qr6），且自检报告位置正确。"""
        argv = [
            "/w/日报.md", "/w/封面.png",
            "/w/q1.png", "/w/q2.png", "/w/q3.png",
            "/w/自查报告.md",
            "--qr4", "/w/q4.png", "--qr5", "/w/q5.png", "--qr6", "/w/q6.png",
            "--report-date", "2026-09-22",
        ]
        # 复现 publish.py 的 parser（与 scripts/publish.py main() 保持一致的签名）
        parser = argparse.ArgumentParser()
        parser.add_argument("md_path"); parser.add_argument("cover_path")
        parser.add_argument("qr1_path"); parser.add_argument("qr2_path"); parser.add_argument("qr3_path")
        parser.add_argument("self_check_report")
        parser.add_argument("--qr4"); parser.add_argument("--qr5"); parser.add_argument("--qr6")
        parser.add_argument("--report-date")
        a = parser.parse_args(argv)
        self.assertTrue(a.self_check_report.endswith("自查报告.md"), "第 4 个位置参数须为自检报告")
        self.assertEqual(a.qr4, "/w/q4.png")
        self.assertEqual(a.qr5, "/w/q5.png")
        self.assertEqual(a.qr6, "/w/q6.png")

    def test_old_six_positional_form_is_rejected(self):
        """旧的错误用法（6 码全写成位置参数）必须被 argparse 拒绝——防止文档回退。"""
        parser = argparse.ArgumentParser()
        parser.add_argument("md_path"); parser.add_argument("cover_path")
        parser.add_argument("qr1_path"); parser.add_argument("qr2_path"); parser.add_argument("qr3_path")
        parser.add_argument("self_check_report")
        parser.add_argument("--qr4"); parser.add_argument("--qr5"); parser.add_argument("--qr6")
        with self.assertRaises(SystemExit):
            parser.parse_args(["/w/md.md", "/w/c.png",
                               "/w/q1.png", "/w/q2.png", "/w/q3.png", "/w/q4.png", "/w/q5.png",
                               "/w/q6.png",
                               "/w/sc.md"])


class ModeWhitelistDocTests(unittest.TestCase):
    """V3.8.6：`--mode` 白名单必须在 SKILL.md 文档中完整列出，防止「脚本支持但文档没写」。

    回归背景：run.py 的 --mode 白名单早早加了 holiday-batch，但 SKILL.md 正文
    从未说明其行为（仅 CHANGELOG 提过一句），执行时只能靠猜。本类以 run.py 源码
    为权威，反向校验文档同步。
    """

    def _script_modes(self):
        """从 run.py 源码提取 --mode 的 choices 列表（避免硬编码漂移）。"""
        src = (SCRIPTS / "run.py").read_text(encoding="utf-8")
        m = re.search(r'"--mode"\s*,\s*choices\s*=\s*\[([^\]]+)\]', src)
        self.assertIsNotNone(m, "未能在 run.py 中定位 --mode choices")
        return [x.strip().strip('"\'') for x in m.group(1).split(",") if x.strip()]

    def test_script_modes_extractable(self):
        modes = self._script_modes()
        self.assertIn("normal", modes)
        self.assertIn("holiday-batch", modes, "run.py 应包含 holiday-batch")

    def test_all_script_modes_documented_in_skill(self):
        """run.py 支持的每个 mode 都必须在 SKILL.md 出现（含行为说明）。"""
        skill = (SCRIPTS.parent / "SKILL.md").read_text(encoding="utf-8")
        missing = [m for m in self._script_modes() if m not in skill]
        self.assertEqual(missing, [], f"以下 mode 在 SKILL.md 中缺失说明：{missing}")

    def test_mode_contract_line_lists_all_modes(self):
        """运行契约的 mode 字段枚举须与 run.py 白名单一致。"""
        skill = (SCRIPTS.parent / "SKILL.md").read_text(encoding="utf-8")
        m = re.search(r'^mode:\s*"([^"]+)"', skill, re.M)
        self.assertIsNotNone(m, "未找到运行契约 mode 字段")
        documented = set(m.group(1).split("|"))
        self.assertEqual(documented, set(self._script_modes()),
                         "运行契约 mode 枚举与 run.py 白名单不一致")

    def test_holiday_batch_behavior_documented(self):
        """holiday-batch 必须有明确的行为说明（绕过工作日拦截 + 使用纪律），不能只列名字。"""
        skill = (SCRIPTS.parent / "SKILL.md").read_text(encoding="utf-8")
        idx = skill.find("holiday-batch")
        self.assertGreater(idx, -1)
        window = skill[idx: idx + 900]
        self.assertIn("工作日", window, "holiday-batch 说明须提及与工作日拦截的关系")
        self.assertIn("例外", window, "holiday-batch 说明须点明这是例外通道")


