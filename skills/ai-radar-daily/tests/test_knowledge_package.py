import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BUILD_PATH = ROOT / "scripts" / "build_knowledge_package.py"
SYNC_PATH = ROOT / "scripts" / "sync_lark_base.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD = load_module("build_knowledge_package", BUILD_PATH)
SYNC = load_module("sync_lark_base", SYNC_PATH)


class KnowledgePackageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.md = self.directory / "AI雷达日报_2026-09-03.md"
        self.search = self.directory / "search.json"
        self.semantic = self.directory / "semantic.json"
        self.self_check = self.directory / "self-check.md"
        self.output = self.directory / "package.json"
        self.md.write_text(
            """# AI 雷达日报｜9.3

> 导语：测试日报。

## 今日要紧

### 【需行动】事件甲（9月2日）

主体甲发布模型更新。

**对我们：** 测试判断。

[details="🟢 来源｜官方 + 独立交叉"]
- [官方](https://example.com/news?id=1&utm_source=test) · [官方镜像链接](https://example.com/news?utm_source=duplicate&id=1)
- [媒体](https://media.example.org/a)
[/details]

---

## 金风相关

今日暂无经双源核验的金风相关重要消息。

---

## 工具与做法

今日暂无新增可复用的工具与做法。

---

## 行业速递

- 🟢 **快讯（9月2日）：** 测试。

[details="来源｜1 条速递"]
- [来源](https://example.com/news?id=1)
[/details]

---

## 后续关注｜判断台账

**本期新增**

| 关注项 | 提出期 | 状态 | 回看结果 |
|---|---|---|---|
| 等待后续测试结果 | 2026-09-03 | 跟踪中 | 等待正式口径 |
""",
            encoding="utf-8",
        )
        self.search.write_text(json.dumps({
            "report_date": "2026-09-03",
            "window": "d2",
            "candidates": [{
                "title": "事件甲（9月2日）",
                "event_date": "2026-09-02",
                "source_name": "官方",
                "source_url": "https://example.com/news?id=1",
                "decision": "SELECTED",
                "subjects": ["主体甲"],
                "region": "全球",
                "industry": ["AI"],
                "importance": "HIGH",
                "confidence": 0.9,
                "source_level": "T0",
                "source_excerpt": "发布模型更新",
            }],
        }, ensure_ascii=False), encoding="utf-8")
        self.semantic.write_text(json.dumps({
            "report_date": "2026-09-03",
            "overall": "PASS",
            "recent_dedup_checked": True,
            "items": [{
                "title": "事件甲（9月2日）",
                "status": "PASS",
                "independent_sources": True,
                "source_urls": [
                    "https://example.com/news?id=1",
                    "https://media.example.org/a",
                ],
                "facts_checked": ["日期", "主体"],
                "evidence_receipts": [{
                    "claim_id": "C-001",
                    "item_id": "NEWS-01",
                    "claim_text": "主体甲发布模型更新",
                    "claim_type": "action",
                    "materiality": "critical",
                    "event_date": "2026-09-02",
                    "date_basis": "发生",
                    "sources": [
                        {
                            "source_id": "S-001", "tier": "T0", "publisher": "官方",
                            "title": "官方发布", "url": "https://example.com/news?id=1",
                            "published_at": "2026-09-02T08:00:00+08:00",
                            "accessed_at": "2026-09-03T08:00:00+08:00",
                            "locator": "正文第1段", "verbatim": "发布模型更新",
                            "supports": "full", "independence_group": "origin-001",
                        },
                        {
                            "source_id": "S-002", "tier": "T2", "publisher": "媒体",
                            "title": "媒体报道", "url": "https://media.example.org/a",
                            "published_at": "2026-09-02T09:00:00+08:00",
                            "accessed_at": "2026-09-03T08:05:00+08:00",
                            "locator": "正文第2段", "verbatim": "主体甲更新模型",
                            "supports": "full", "independence_group": "origin-002",
                        },
                    ],
                    "verification": {
                        "status": "PASS", "independent_source_count": 2,
                        "single_official_exception": False, "notes": "双源一致",
                    },
                }],
            }],
        }, ensure_ascii=False), encoding="utf-8")
        digest = hashlib.sha256(self.md.read_bytes()).hexdigest()
        self.self_check.write_text(
            f"SELF_CHECK_STATUS: PASS\nREPORT_DATE: 2026-09-03\n"
            f"RULESET_VERSION: V4.1\nSOURCE_SHA256: {digest}\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def args(self, output=None):
        return BUILD.parse_args([
            str(self.md), "--search-log", str(self.search),
            "--semantic-review", str(self.semantic),
            "--self-check-report", str(self.self_check),
            "--issue-id", "airadar-2026-09-03", "--report-date", "2026-09-03",
            "--timezone", "Asia/Shanghai", "--output", str(output or self.output),
        ])

    def update_semantic(self, update):
        payload = json.loads(self.semantic.read_text(encoding="utf-8"))
        update(payload)
        self.semantic.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_minimal_parse_and_source_url_dedup(self):
        package = BUILD.build_package(self.args())
        self.assertEqual(package["schema_version"], "1.0.0")
        self.assertEqual(len(package["issues"]), 1)
        self.assertEqual(len(package["events"]), 1)
        self.assertEqual(len(package["sources"]), 2)
        event = package["events"][0]
        self.assertEqual(event["event_date"], "2026-09-02")
        self.assertEqual(event["lens"], "N/A")
        self.assertEqual(event["subjects"], ["主体甲"])
        self.assertEqual(event["source_ids"], sorted(source["source_id"] for source in package["sources"]))
        self.assertIn("SEMANTIC_REVIEW", {row["judgment_type"] for row in package["judgments"]})
        self.assertTrue(all(source["semantic_review_bound"] for source in package["sources"]))
        self.assertEqual(package["issues"][0]["rule_version"], BUILD.RULE_VERSION)
        self.assertEqual(package["runs"][0]["status"], "PASS")

    def test_semantic_overall_fail_rejected(self):
        self.update_semantic(lambda payload: payload.update(overall="FAIL"))
        with self.assertRaises(BUILD.PackageError):
            BUILD.build_package(self.args())

    def test_semantic_item_fail_rejected(self):
        self.update_semantic(lambda payload: payload["items"][0].update(status="FAIL"))
        with self.assertRaises(BUILD.PackageError):
            BUILD.build_package(self.args())

    def test_missing_nested_evidence_receipts_rejected(self):
        self.update_semantic(lambda payload: payload["items"][0].pop("evidence_receipts"))
        with self.assertRaises(BUILD.PackageError):
            BUILD.build_package(self.args())

    def test_media_url_does_not_inherit_official_t0_metadata(self):
        package = BUILD.build_package(self.args())
        sources = {source["normalized_url"]: source for source in package["sources"]}
        media = sources[BUILD.normalize_url("https://media.example.org/a")]
        self.assertEqual(media["source_level"], "T2")
        self.assertEqual(media["source_name"], "媒体")
        self.assertIsNone(media["published_at"])

    def test_date_mismatch_fails_closed(self):
        payload = json.loads(self.search.read_text(encoding="utf-8"))
        payload["report_date"] = "2026-09-02"
        self.search.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(BUILD.PackageError):
            BUILD.build_package(self.args())

    def test_empty_evidence_fails_closed(self):
        self.semantic.write_text("{}", encoding="utf-8")
        with self.assertRaises(BUILD.PackageError):
            BUILD.build_package(self.args())

    def test_stable_ids_ignore_output_and_tracking_parameters(self):
        first = BUILD.build_package(self.args(self.directory / "first.json"))
        second = BUILD.build_package(self.args(self.directory / "second.json"))
        self.assertEqual(first["events"][0]["event_id"], second["events"][0]["event_id"])
        self.assertEqual(
            [row["source_id"] for row in first["sources"]],
            [row["source_id"] for row in second["sources"]],
        )
        self.assertEqual(first["runs"][0]["run_id"], second["runs"][0]["run_id"])
        self.assertEqual(
            BUILD.normalize_url("https://example.com/news?id=1&utm_source=a"),
            BUILD.normalize_url("https://example.com/news?utm_source=b&id=1"),
        )

    def test_sync_missing_config_returns_pending_without_network(self):
        package = BUILD.build_package(self.args())
        self.output.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
        missing_config = self.directory / "missing-runtime.json"
        plan = self.directory / "plan.json"
        result = subprocess.run(
            [sys.executable, str(SYNC_PATH), str(self.output), "--config", str(missing_config),
             "--output", str(plan), "--confirm"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(plan.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "SYNC_PENDING")
        self.assertFalse(payload["network_attempted"])
        self.assertEqual([table["table"] for table in payload["tables"]],
                         ["issues", "events", "sources", "judgments", "runs"])


class LarkBaseSyncTest(unittest.TestCase):
    def setUp(self):
        self.package = {
            "schema_version": "1.0.0",
            **{
                table: [{SYNC.PRIMARY_KEYS[table]: f"key-{table}", "value": table}]
                for table in SYNC.TABLES
            },
        }
        self.sync = {
            "enabled": True,
            "base_token": "base-token",
            "table_ids": {table: f"tbl-{table}" for table in SYNC.TABLES},
            "target_wiki_url": "https://example.test/wiki",
            "local_package_dir": "/tmp/packages",
        }

    @staticmethod
    def ok_result(payload=None):
        body = {"ok": True}
        if payload:
            body.update(payload)
        return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")

    def test_current_runtime_schema_metadata_enters_plan(self):
        sync, reasons = SYNC.config_gate({"knowledge_sync": self.sync})
        self.assertEqual(reasons, [])
        plan = SYNC.build_plan(self.package, sync, "dry-run", "DRY_RUN_READY")
        self.assertEqual(plan["target_wiki_url"], self.sync["target_wiki_url"])
        self.assertEqual(plan["local_package_dir"], self.sync["local_package_dir"])

    def test_sync_uses_relative_temp_paths_and_explicit_cwd(self):
        calls = []
        update_payloads = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            cwd = Path(kwargs["cwd"])
            if "--output" in command:
                output_name = command[command.index("--output") + 1]
                primary_key = command[command.index("--field-id") + 1]
                table_id = command[command.index("--table-id") + 1]
                table = table_id.removeprefix("tbl-")
                row = self.package[table][0]
                (cwd / output_name).write_text(json.dumps({
                    "record_id": f"rec-{table}", "fields": {primary_key: row[primary_key]},
                }) + "\n", encoding="utf-8")
            if "--json" in command:
                reference = command[command.index("--json") + 1]
                if "+record-batch-update" in command:
                    update_payloads.append(json.loads((cwd / reference[1:]).read_text(encoding="utf-8")))
            return self.ok_result()

        plan = SYNC.build_plan(self.package, self.sync, "confirmed", "SYNC_IN_PROGRESS")
        with mock.patch.object(SYNC.shutil, "which", return_value="/usr/bin/lark-cli"), \
                mock.patch.object(SYNC.subprocess, "run", side_effect=fake_run):
            result = SYNC.execute_plan(plan, self.package, self.sync)

        self.assertEqual(result["status"], "SYNCED")
        self.assertTrue(calls)
        for command, kwargs in calls:
            self.assertTrue(kwargs.get("cwd"))
            if "--output" in command:
                self.assertFalse(Path(command[command.index("--output") + 1]).is_absolute())
            if "--json" in command:
                reference = command[command.index("--json") + 1]
                self.assertTrue(reference.startswith("@"))
                self.assertFalse(Path(reference[1:]).is_absolute())
        for table, payload in zip(SYNC.TABLES, update_payloads):
            self.assertEqual(list(payload["update_records"]), [f"rec-{table}"])

    def test_ignored_fields_prevents_synced(self):
        ignored = self.ok_result({"ignored_fields": ["unknown_field"]})
        plan = SYNC.build_plan(self.package, self.sync, "confirmed", "SYNC_IN_PROGRESS")
        with mock.patch.object(SYNC.shutil, "which", return_value="/usr/bin/lark-cli"), \
                mock.patch.object(SYNC.subprocess, "run", return_value=ignored):
            with self.assertRaises(SYNC.SyncError):
                SYNC.execute_plan(plan, self.package, self.sync)
        self.assertNotEqual(plan["status"], "SYNCED")

    def test_missing_readback_acceptance_cannot_sync(self):
        primary_key = SYNC.PRIMARY_KEYS["issues"]
        lookup = {self.package["issues"][0][primary_key]: "rec-issues"}
        plan = SYNC.build_plan(self.package, self.sync, "confirmed", "SYNC_IN_PROGRESS")
        with mock.patch.object(SYNC.shutil, "which", return_value="/usr/bin/lark-cli"), \
                mock.patch.object(SYNC, "query_existing", side_effect=[lookup, {}, {}, {}]), \
                mock.patch.object(SYNC, "run_cli", return_value=(self.ok_result(), {"ok": True})), \
                mock.patch.object(SYNC.time, "sleep"):
            result = SYNC.execute_plan(plan, self.package, self.sync)
        self.assertEqual(result["status"], "NEEDS_RECONCILIATION")
        self.assertNotEqual(result["status"], "SYNCED")
        self.assertEqual(result["results"][0]["verify_attempts"], 3)

    def test_readback_retry_accepts_eventual_consistency(self):
        primary_key = SYNC.PRIMARY_KEYS["issues"]
        key = self.package["issues"][0][primary_key]
        plan = SYNC.build_plan(self.package, self.sync, "confirmed", "SYNC_IN_PROGRESS")
        full_by_table = [
            {self.package[table][0][SYNC.PRIMARY_KEYS[table]]: f"rec-{table}"}
            for table in SYNC.TABLES
        ]
        side_effect = [{key: "rec-issues"}, {}, {key: "rec-issues"}]
        for mapping in full_by_table[1:]:
            side_effect.extend([mapping, mapping])
        with mock.patch.object(SYNC.shutil, "which", return_value="/usr/bin/lark-cli"), \
                mock.patch.object(SYNC, "query_existing", side_effect=side_effect), \
                mock.patch.object(SYNC, "run_cli", return_value=(self.ok_result(), {"ok": True})), \
                mock.patch.object(SYNC.time, "sleep") as sleep:
            result = SYNC.execute_plan(plan, self.package, self.sync)
        self.assertEqual(result["status"], "SYNCED")
        self.assertEqual(result["results"][0]["verify_attempts"], 2)
        sleep.assert_called_once_with(1)

    def test_confirmed_main_returns_nonzero_without_synced_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "sync-result.json"
            with mock.patch.object(SYNC, "read_json", side_effect=[self.package, {"knowledge_sync": self.sync}]), \
                    mock.patch.object(SYNC, "config_gate", return_value=(self.sync, [])), \
                    mock.patch.object(SYNC, "execute_plan", return_value={
                        "status": "NEEDS_RECONCILIATION",
                        "network_attempted": True,
                        "reasons": ["readback missing"],
                    }):
                rc = SYNC.main([str(root / "package.json"), "--confirm", "--output", str(output)])
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "NEEDS_RECONCILIATION")


if __name__ == "__main__":
    unittest.main()
