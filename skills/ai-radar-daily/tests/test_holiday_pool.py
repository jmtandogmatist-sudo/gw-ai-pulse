#!/usr/bin/env python3
"""候选累积池单元测试（holiday_pool）。

覆盖：
- 空池初始化
- append 幂等（同 report_date 覆盖）
- pending_candidates 只取未发布
- settle_pool 落账并标记 published
- 30 上限截断由调用方排序后保证（此处验证标记契约）
- prune_old 老化剔除
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import holiday_pool as hp  # noqa: E402


def _cand(i, url=None):
    return {
        "id": f"c{i}",
        "title": f"事件{i}",
        "event_date": "2026-09-19",
        "source_name": "OpenAI",
        "source_url": url or f"https://openai.com/blog/{i}",
        "source_urls": [url or f"https://openai.com/blog/{i}"],
        "category": "模型发布",
        "decision": "SELECTED",
    }


class PoolBasics(unittest.TestCase):
    def test_empty_pool(self):
        pool = hp.default_pool()
        self.assertEqual(pool["batches"], [])
        self.assertEqual(pool["publishes"], [])
        self.assertEqual(hp.pending_candidates(pool), [])
        s = hp.pool_summary(pool)
        self.assertEqual(s["pending_count"], 0)
        self.assertIsNone(s["pending_span"])

    def test_append_idempotent(self):
        pool = hp.default_pool()
        hp.append_candidates(pool, "2026-09-19", [_cand(1), _cand(2)])
        hp.append_candidates(pool, "2026-09-19", [_cand(3)])
        # 同日期覆盖，不累加
        self.assertEqual(len(pool["batches"]), 1)
        self.assertEqual(pool["batches"][0]["count"], 1)
        self.assertEqual(len(hp.pending_candidates(pool)), 1)

    def test_multi_day_accumulation(self):
        pool = hp.default_pool()
        for d, n in [("2026-09-19", 3), ("2026-09-20", 4), ("2026-09-21", 5)]:
            hp.append_candidates(pool, d, [_cand(f"{d}-{i}") for i in range(n)])
        pend = hp.pending_candidates(pool)
        self.assertEqual(len(pend), 12)
        s = hp.pool_summary(pool)
        self.assertEqual(s["batch_count"], 3)
        self.assertEqual(s["pending_span"], "2026-09-19~2026-09-21")

    def test_settle_marks_published(self):
        pool = hp.default_pool()
        hp.append_candidates(pool, "2026-09-19", [_cand(1), _cand(2), _cand(3)])
        pend = hp.pending_candidates(pool)
        # 取前 2 条结算
        hp.settle_pool(pool, "2026-09-21", pend[:2])
        remain = hp.pending_candidates(pool)
        self.assertEqual(len(remain), 1)
        self.assertEqual(remain[0]["id"], "c3")
        self.assertEqual(len(pool["publishes"]), 1)
        self.assertEqual(pool["publishes"][0]["count"], 2)

    def test_prune_old(self):
        pool = hp.default_pool()
        hp.append_candidates(pool, "2026-01-01", [_cand("old")])
        hp.append_candidates(pool, "2026-09-19", [_cand("new")])
        hp.prune_old(pool, "2026-09-21", max_age_days=45)
        dates = [b["report_date"] for b in pool["batches"]]
        self.assertIn("2026-09-19", dates)
        self.assertNotIn("2026-01-01", dates)

    def test_prune_does_not_drop_recent_accumulation(self):
        """回归（2026-09-21 冒烟暴露）：参照点晚于全部批次时，不得误删近期累积。

        长假场景：多日累积 + 用真实当天日期剔老，应保留全部近期批次。
        """
        pool = hp.default_pool()
        days = ["2026-09-19", "2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23"]
        for d in days:
            hp.append_candidates(pool, d, [_cand(f"{d}-{i}") for i in range(5)])
        hp.prune_old(pool, "2026-09-24", max_age_days=45)
        self.assertEqual(len(pool["batches"]), 5)
        self.assertEqual(len(hp.pending_candidates(pool)), 25)


class PoolIO(unittest.TestCase):
    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            pool = hp.load_pool(wd)
            hp.append_candidates(pool, "2026-09-19", [_cand(1)])
            hp.save_pool(wd, pool)
            again = hp.load_pool(wd)
            self.assertEqual(len(hp.pending_candidates(again)), 1)

    def test_dry_run_no_write(self):
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            pool = hp.default_pool()
            hp.append_candidates(pool, "2026-09-19", [_cand(1)])
            hp.save_pool(wd, pool, dry_run=True)
            self.assertFalse(hp.pool_path(wd).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
