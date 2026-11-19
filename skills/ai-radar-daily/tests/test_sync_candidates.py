#!/usr/bin/env python3
"""候选全集同步逻辑单元测试（sync_candidates_base）。

只测纯函数（build_rows），不联网、不调 lark-cli——外部动作由真实验收覆盖。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sync_candidates_base as scb  # noqa: E402
import holiday_pool as hp  # noqa: E402


class BuildRows(unittest.TestCase):
    def _pool(self):
        pool = hp.default_pool()
        hp.append_candidates(pool, "2026-09-20", [
            {"id": "c1", "title": "入选事件", "event_date": "2026-09-20",
             "source_name": "OpenAI", "source_url": "https://openai.com/x",
             "category": "模型发布", "decision": "SELECTED"},
            {"id": "c2", "title": "被淘汰候选", "event_date": "2026-09-20",
             "source_name": "聚合号", "source_url": "https://toutiao.com/y",
             "category": "传闻", "decision": "REJECTED"},
        ])
        return pool

    def test_build_rows_count(self):
        rows = scb.build_rows(self._pool())
        self.assertEqual(len(rows), 2)

    def test_select_field_is_list(self):
        rows = scb.build_rows(self._pool())
        for r in rows:
            self.assertIsInstance(r["采选结果"], list)
            self.assertEqual(len(r["采选结果"]), 1)

    def test_checkbox_is_bool(self):
        rows = scb.build_rows(self._pool())
        for r in rows:
            self.assertIsInstance(r["是否进入精编"], bool)

    def test_rejected_candidate_included(self):
        """全集必须包含被淘汰候选——这正是'全资讯'的意义。"""
        rows = scb.build_rows(self._pool())
        decisions = {r["候选ID"]: r["采选结果"][0] for r in rows}
        self.assertEqual(decisions["c2"], "REJECTED")

    def test_empty_pool(self):
        self.assertEqual(scb.build_rows(hp.default_pool()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
