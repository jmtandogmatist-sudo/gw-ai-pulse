"""日报标题规范（title_spec）与各脚本标题解析的回归测试（V5.3.2）。

覆盖三类风险——它们都不会"明确报错"，只会静默错判：
1. 新格式上线后 layout_v5 认不出 H1 → V5 版式整段判失败；
2. fetch_recent 用旧子串 "AI 雷达日报" 过滤自家帖 → 自家日报被当成社区信号；
3. prepare_feishu 的 date_tag 用宽松 \\d+\\.\\d+ → 钩子里的「0.28 元」被当成日期。
"""

import sys
import unittest
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import title_spec as ts  # noqa: E402
import layout_v5  # noqa: E402

HOOK = "5 次示范就到 95%，机器人不用先攒大数据"
NEW_TITLE = "5 次示范就到 95%，机器人不用先攒大数据 |AI雷达11.10"
LEGACY_TITLE = "AI 雷达日报｜11.10"


class FormatTitleTest(unittest.TestCase):
    def test_format_title_uses_hook_and_suffix(self):
        self.assertEqual(ts.format_title(HOOK, date(2026, 11, 10)), NEW_TITLE)

    def test_format_title_does_not_pad_month_day(self):
        self.assertEqual(ts.format_title("三次示范就到 90%", date(2026, 9, 5)),
                         "三次示范就到 90% |AI雷达9.5")

    def test_format_title_rejects_empty_hook(self):
        with self.assertRaises(ValueError):
            ts.format_title("   ", date(2026, 11, 10))

    def test_legacy_title_still_available(self):
        self.assertEqual(ts.legacy_title(date(2026, 11, 10)), LEGACY_TITLE)


class ParseTitleTest(unittest.TestCase):
    def test_parse_new_format(self):
        parsed = ts.parse(NEW_TITLE)
        self.assertEqual(parsed["hook"], HOOK)
        self.assertEqual((parsed["month"], parsed["day"]), (11, 10))
        self.assertFalse(parsed["is_legacy"])

    def test_parse_legacy_format(self):
        parsed = ts.parse(LEGACY_TITLE)
        self.assertTrue(parsed["is_legacy"])
        self.assertEqual((parsed["month"], parsed["day"]), (11, 10))

    def test_parse_rejects_unrelated_title(self):
        self.assertIsNone(ts.parse("关于 AI 智能体平台升级的通知"))
        self.assertFalse(ts.is_report_title("关于 AI 智能体平台升级的通知"))

    def test_is_report_title_accepts_both_generations(self):
        self.assertTrue(ts.is_report_title(NEW_TITLE))
        self.assertTrue(ts.is_report_title(LEGACY_TITLE))

    def test_parse_tolerates_extra_spaces_around_suffix(self):
        self.assertIsNotNone(ts.parse("三次示范就到 90%  |  AI雷达9.5"))


class H1ExtractionTest(unittest.TestCase):
    def test_extract_h1_from_new_body(self):
        raw = f"[wrap=air-daily]\n# {NEW_TITLE}\n\n> 今日主线：示例\n"
        self.assertEqual(ts.extract_h1(raw), NEW_TITLE)

    def test_extract_h1_ignores_h3_headings(self):
        raw = f"# {NEW_TITLE}\n\n### 集团AI能力地图\n"
        self.assertEqual(ts.extract_h1(raw), NEW_TITLE)

    def test_is_h1_line_only_matches_report_titles(self):
        self.assertTrue(ts.is_h1_line(f"# {NEW_TITLE}"))
        self.assertTrue(ts.is_h1_line(f"# {LEGACY_TITLE}"))
        self.assertFalse(ts.is_h1_line("# 随便一个一级标题"))

    def test_h1_date_matches_report_date(self):
        raw = f"# {NEW_TITLE}\n"
        self.assertEqual(ts.h1_date(raw, date(2026, 11, 10)), date(2026, 11, 10))

    def test_h1_date_cross_year(self):
        raw = "# 三次示范就到 90% |AI雷达12.31\n"
        self.assertEqual(ts.h1_date(raw, date(2027, 1, 2)), date(2026, 12, 31))


class DateTagTest(unittest.TestCase):
    def test_date_tag_from_new_title(self):
        self.assertEqual(ts.date_tag(NEW_TITLE), "11.10")

    def test_date_tag_ignores_decimal_numbers_in_hook(self):
        # 关键回归：钩子里的价格不能被当成日期
        title = "2K 出图 0.28 元、能改 100 轮 |AI雷达11.10"
        self.assertEqual(ts.date_tag(title), "11.10")

    def test_date_tag_from_legacy_title(self):
        self.assertEqual(ts.date_tag(LEGACY_TITLE), "11.10")

    def test_date_tag_empty_when_unparseable(self):
        self.assertEqual(ts.date_tag("关于平台升级的通知"), "")


class ValidateHookTest(unittest.TestCase):
    def test_good_hook_passes(self):
        self.assertEqual(ts.validate_hook(HOOK), [])

    def test_hook_must_contain_digit(self):
        problems = ts.validate_hook("某公司发布新模型")
        self.assertTrue(any("数字" in p for p in problems))

    def test_hook_rejects_marketing_words(self):
        problems = ts.validate_hook("重磅！模型能力彻底改变")
        self.assertTrue(any("营销" in p for p in problems))

    def test_hook_rejects_pipe_and_tag(self):
        self.assertTrue(ts.validate_hook("示范 5 次就到 95% | 机器人"))
        self.assertTrue(ts.validate_hook("AI雷达 5 次示范就到 95%"))

    def test_hook_length_bounds(self):
        self.assertTrue(any("过短" in p for p in ts.validate_hook("5次")))
        self.assertTrue(any("过长" in p for p in ts.validate_hook("五" * 30 + "95%")))

    def test_hook_rejects_trailing_punctuation(self):
        self.assertTrue(any("标点" in p for p in ts.validate_hook("5 次示范就到 95%，")))

    def test_validate_title_legacy_exempt_from_hook_rules(self):
        self.assertEqual(ts.validate_title(LEGACY_TITLE), [])

    def test_validate_title_reports_illegal_date(self):
        self.assertTrue(any("日期非法" in p for p in ts.validate_title("三次示范 |AI雷达13.40")))


class LayoutIntegrationTest(unittest.TestCase):
    def test_layout_v5_recognizes_new_h1(self):
        raw = (
            "[wrap=air-daily]\n"
            f"# {NEW_TITLE}\n\n"
            "> 今日主线：示例主线。\n\n"
            "## 今日要紧\n\n"
        )
        doc = layout_v5.parse(raw)
        self.assertEqual(doc["h1"], f"# {NEW_TITLE}")

    def test_layout_v5_still_recognizes_legacy_h1(self):
        raw = f"[wrap=air-daily]\n# {LEGACY_TITLE}\n\n## 今日要紧\n\n"
        doc = layout_v5.parse(raw)
        self.assertEqual(doc["h1"], f"# {LEGACY_TITLE}")


if __name__ == "__main__":
    unittest.main()
