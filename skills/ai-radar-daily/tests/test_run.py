"""run.py 编排器测试套件（V3.2.2）。

覆盖：状态机、门禁、幂等、dry-run、schema_version 动态、双确认门。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# 5.2.0：按测试文件定位技能目录（原写死 /Users/sr_jm/... 会在别处测试新版本时导入已安装的旧版 run.py）
SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import run  # noqa: E402


# ---- 测试隔离 -------------------------------------------------
# 所有真实写盘（dry_run=False 的 plan/publish 等）重定向到一个临时 work_dir，
# 防止假日期(2099-*)目录污染真实工作目录。测试结束自动清理。
# 目录名以 ai-radar-daily 结尾，以兼容 test_get_work_dir_from_runtime 的命名断言。
_ISOLATED_WD = Path(tempfile.mkdtemp(prefix="ai-radar-test-", suffix="-ai-radar-daily"))
os.environ["AI_RADAR_WORK_DIR"] = str(_ISOLATED_WD)


def tearDownModule():
    """跑完全部用例后清理隔离目录，并恢复环境变量。"""
    wd = Path(_ISOLATED_WD)
    for child in wd.glob("AI-RADAR-*"):
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    shutil.rmtree(wd, ignore_errors=True)
    os.environ.pop("AI_RADAR_WORK_DIR", None)


class TestWorkDirResolution(unittest.TestCase):
    """WORK_DIR 动态解析。

    2026-11-03 修：本类原受 `AI_RADAR_WORK_DIR` 泄漏影响（其他用例把它设成临时目录后未恢复），
    全量运行时随机 FAIL、单跑 PASS。改为每个用例前后隔离该变量。
    """

    def setUp(self):
        self._env_snapshot = os.environ.pop("AI_RADAR_WORK_DIR", None)

    def tearDown(self):
        os.environ.pop("AI_RADAR_WORK_DIR", None)
        if self._env_snapshot is not None:
            os.environ["AI_RADAR_WORK_DIR"] = self._env_snapshot

    def test_get_work_dir_from_runtime(self):
        wd = run.get_work_dir()
        self.assertTrue(wd.exists(), f"work_dir 不存在: {wd}")
        self.assertEqual(wd.name, "ai-radar-daily")

    def test_work_dir_env_override(self):
        _env_snapshot = os.environ.get("AI_RADAR_WORK_DIR")
        os.environ["AI_RADAR_WORK_DIR"] = "/tmp/_fake_wd"
        try:
            wd = run.get_work_dir()
            # macOS 上 /tmp → /private/tmp；用 endswith 兼容
            self.assertTrue(str(wd).endswith("/tmp/_fake_wd"),
                            f"work_dir 未走 env 覆盖: {wd}")
        finally:
            # 恢复隔离变量（若原本有）
            if _env_snapshot:
                os.environ["AI_RADAR_WORK_DIR"] = _env_snapshot
            else:
                os.environ.pop("AI_RADAR_WORK_DIR", None)

    def test_fallback_to_skill_parent(self):
        """runtime.json 不可读时，回退到 skill 父目录下的 ai-radar-daily。

        2026-11-03 修：原用例把 `_load_work_dir` 打桩返回 `""`，断言 `get_work_dir()` 以
        `ai-radar-daily` 结尾——但 `get_work_dir()` 是 `env or _load_work_dir()`，打桩成空串后
        `Path("").resolve()` 落到 cwd，断言必然不成立；此前"通过"全靠别的用例把 env 泄漏成
        临时 work_dir（`...ai-radar-daily`）造成的假阳性。现改为验证真实回退分支。
        """
        _snapshot = os.environ.get("AI_RADAR_WORK_DIR")
        os.environ["AI_RADAR_WORK_DIR"] = ""
        try:
            with patch.object(run, "RUNTIME_PATH", Path("/nonexistent/runtime.json")):
                self.assertTrue(str(run._load_work_dir()).endswith("ai-radar-daily"))
                self.assertTrue(str(run.get_work_dir()).endswith("ai-radar-daily"))
        finally:
            if _snapshot is None:
                os.environ.pop("AI_RADAR_WORK_DIR", None)
            else:
                os.environ["AI_RADAR_WORK_DIR"] = _snapshot


class TestSchemaVersionDynamic(unittest.TestCase):
    """schema_version 始终从 runtime.json 读，不允许硬编码。"""

    def test_schema_version_matches_runtime(self):
        rt = run.load_runtime()
        sv = run.schema_version()
        self.assertEqual(sv, rt.get("version"))

    def test_empty_state_has_runtime_version(self):
        state = run.empty_run_state("2099-01-01")
        rt = run.load_runtime()
        self.assertEqual(state["schema_version"], rt.get("version"))


class TestStateMachine(unittest.TestCase):
    """状态机推进。"""

    def test_set_stage_running(self):
        state = run.empty_run_state("2099-01-02")
        run.set_stage(state, "1", "RUNNING")
        self.assertEqual(state["stages"]["1"]["status"], "RUNNING")
        self.assertIsNotNone(state["stages"]["1"]["started_at"])

    def test_set_stage_pass_advances_current(self):
        state = run.empty_run_state("2099-01-03")
        run.set_stage(state, "3", "PASS")
        self.assertEqual(state["current_stage"], 3)

    def test_set_stage_pass_records_finished(self):
        state = run.empty_run_state("2099-01-04")
        run.set_stage(state, "1", "PASS")
        self.assertEqual(state["stages"]["1"]["status"], "PASS")
        self.assertIsNotNone(state["stages"]["1"]["finished_at"])

    def test_custom_field_propagates(self):
        state = run.empty_run_state("2099-01-05")
        run.set_stage(state, "5", "FAIL", error="test")
        self.assertEqual(state["stages"]["5"]["error"], "test")

    def test_pass_clears_stale_error(self):
        state = run.empty_run_state("2099-01-06")
        run.set_stage(state, "5", "FAIL", error="temporary failure")
        run.set_stage(state, "5", "PASS")
        self.assertIsNone(state["stages"]["5"]["error"])


class TestPaths(unittest.TestCase):
    """产物路径表。"""

    def test_work_files_complete(self):
        f = run.work_files("2026-09-04")
        self.assertIn("md", f)
        self.assertIn("state", f)
        self.assertIn("semantic", f)
        self.assertIn("self_check", f)
        self.assertIn("knowledge_package", f)
        # 全部基于 work_dir
        for name, path in f.items():
            self.assertTrue(str(path).startswith(str(run.get_work_dir())),
                            f"{name} 路径不在 work_dir: {path}")

    def test_issue_id_format(self):
        self.assertEqual(run.issue_id_for("2026-09-04"), "AI-RADAR-2026-09-04")


class TestDryRun(unittest.TestCase):
    """dry-run 不写盘。"""

    def test_dry_run_plan_no_state_file(self):
        # 选一个未来日期
        date = "2099-02-01"
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        if state_path.exists():
            state_path.unlink()
        # dry-run plan
        ns = _ns(date, dry_run=True)
        rc = run.cmd_plan(ns)
        self.assertEqual(rc, 0)
        # state 仍未创建
        self.assertFalse(state_path.exists(),
                         f"dry-run 不应创建 {state_path}")


class TestGateEnforcement(unittest.TestCase):
    """门禁阻断：stage N 未 PASS 时，后续命令应被拒。"""
    def _push_on(self):
        """gw-ai-pulse（2026-11-13）：推送默认关闭，这些门禁测试必须先显式打开。"""
        return patch.object(run, "_feishu_push_enabled", return_value=True)


    def test_publish_requires_stage5(self):
        date = "2099-03-01"
        # 干净启动
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        if state_path.exists():
            state_path.unlink()
        ns = _ns(date, dry_run=True, confirm=True)
        rc = run.cmd_publish(ns)
        self.assertNotEqual(rc, 0)

    def test_package_requires_stage6(self):
        date = "2099-03-02"
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        if state_path.exists():
            state_path.unlink()
        ns = _ns(date, dry_run=True, confirm=True)
        rc = run.cmd_package(ns)
        self.assertNotEqual(rc, 0)

    def test_push_requires_stage7(self):
        """V3.9.0：push 前置由 stage 8 改为 stage 7（远端同步已后移）。"""
        date = "2099-03-03"
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        if state_path.exists():
            state_path.unlink()
        ns = _ns(date, dry_run=True, confirm=True)
        with self._push_on():          # gw-ai-pulse：推送默认关闭，显式开启以验证门禁
            rc = run.cmd_push(ns)
        self.assertNotEqual(rc, 0)

    def test_publish_requires_confirm(self):
        date = "2099-03-04"
        # 假装 stage 5 PASS
        state = run.empty_run_state(date)
        run.set_stage(state, "5", "PASS")
        state["run_status"] = "WAITING_FORUM_CONFIRMATION"
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        run.atomic_write_json(state_path, state)
        try:
            # 无 --confirm
            ns = _ns(date, dry_run=True, confirm=False)
            rc = run.cmd_publish(ns)
            self.assertNotEqual(rc, 0)
        finally:
            state_path.unlink(missing_ok=True)
            (state_path.parent / "run-contract.json").unlink(missing_ok=True)

    def test_publish_success_copies_verified_forum_state(self):
        date = "2099-03-05"
        state = run.empty_run_state(date)
        run.set_stage(state, "5", "PASS")
        state["run_status"] = "WAITING_FORUM_CONFIRMATION"
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        publish_state = run.work_files(date)["publish_state"]
        state_path.parent.mkdir(parents=True, exist_ok=True)
        run.atomic_write_json(state_path, state)
        run.atomic_write_json(publish_state, {
            "status": "OK", "topic_id": 521, "post_id": 673,
            "url": "https://forum.example/t/test/521",
        })
        try:
            with patch("run.run_subprocess", return_value=0) as mock_subprocess:
                rc = run.cmd_publish(_ns(date, dry_run=False, confirm=True))
            self.assertEqual(rc, 0)
            self.assertEqual(mock_subprocess.call_count, 2)  # preflight + publish
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["preflight"]["status"], "PASS")
            self.assertEqual(saved["preflight"]["exit_code"], 0)
            self.assertEqual(saved["forum"]["status"], "PUBLISHED")
            self.assertEqual(saved["forum"]["topic_id"], 521)
            self.assertEqual(saved["forum"]["post_id"], 673)
            self.assertEqual(saved["confirmations"][0]["type"], "forum_publish")
            self.assertEqual(saved["confirmations"][0]["confirmed_by"], "user")
        finally:
            shutil.rmtree(state_path.parent, ignore_errors=True)

    def test_publish_blocks_when_preflight_fails(self):
        date = "2099-03-07"
        state = run.empty_run_state(date)
        run.set_stage(state, "5", "PASS")
        state["run_status"] = "WAITING_FORUM_CONFIRMATION"
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        run.atomic_write_json(state_path, state)
        try:
            with patch("run.run_subprocess", return_value=2) as mock_subprocess:
                rc = run.cmd_publish(_ns(date, dry_run=False, confirm=True))
            self.assertEqual(rc, 2)
            self.assertEqual(mock_subprocess.call_count, 1)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["run_status"], "GATE_FAILED")
            self.assertEqual(saved["preflight"]["status"], "FAIL")
            self.assertEqual(saved["preflight"]["exit_code"], 2)
            self.assertEqual(saved["stages"]["6"]["status"], "FAIL")
            self.assertIn("preflight_check", saved["stages"]["6"]["error"])
        finally:
            shutil.rmtree(state_path.parent, ignore_errors=True)

    def test_self_check_blocks_when_preflight_fails(self):
        date = "2099-03-08"
        state = run.empty_run_state(date)
        run.set_stage(state, "4", "PASS")
        state["run_status"] = "STAGE4_REVIEWED"
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        files = run.work_files(date)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        files["md"].write_text("# mock", encoding="utf-8")
        run.atomic_write_json(state_path, state)
        try:
            with patch("run.run_subprocess", return_value=2) as mock_subprocess:
                rc = run.cmd_self_check(_ns(date, dry_run=False, confirm=False))
            self.assertEqual(rc, 2)
            self.assertEqual(mock_subprocess.call_count, 1)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["run_status"], "GATE_FAILED")
            self.assertEqual(saved["preflight"]["status"], "FAIL")
            self.assertEqual(saved["gates"]["G3"]["status"], "FAIL")
            self.assertEqual(saved["gates"]["G3"]["rule_ids"], ["PREFLIGHT"])
        finally:
            shutil.rmtree(state_path.parent, ignore_errors=True)

    def test_self_check_blocks_when_package_dry_run_fails(self):
        date = "2099-03-09"
        state = run.empty_run_state(date)
        run.set_stage(state, "4", "PASS")
        state["run_status"] = "STAGE4_REVIEWED"
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        files = run.work_files(date)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        files["md"].write_text("# mock", encoding="utf-8")
        run.atomic_write_json(state_path, state)
        try:
            with patch("run.run_subprocess", side_effect=[0, 0, 2]) as mock_subprocess:
                rc = run.cmd_self_check(_ns(date, dry_run=False, confirm=False))
            self.assertEqual(rc, 2)
            self.assertEqual(mock_subprocess.call_count, 3)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["run_status"], "GATE_FAILED")
            self.assertEqual(saved["gates"]["G3"]["rule_ids"], ["PREFLIGHT_PACKAGE"])
        finally:
            shutil.rmtree(state_path.parent, ignore_errors=True)

    def test_package_builds_local_only(self):
        """V3.9.0：package 只构建本地包，**不再**调用任何远端同步脚本。"""
        date = "2099-03-06"
        state = run.empty_run_state(date)
        run.set_stage(state, "6", "PASS")
        state["forum"] = {"status": "PUBLISHED", "topic_id": 521, "post_id": 673, "url": "https://forum.example/t/test/521"}
        state["run_status"] = "STAGE6_PUBLISHED"
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        run.atomic_write_json(state_path, state)
        files = run.work_files(date)

        seen_cmds: list = []

        def fake_subprocess(cmd, dry_run, env_extra=None):
            joined = " ".join(cmd)
            seen_cmds.append(joined)
            # 模拟 build_knowledge_package 产出合法 package.json
            files["knowledge_package"].parent.mkdir(parents=True, exist_ok=True)
            run.atomic_write_json(files["knowledge_package"], {
                "tables": {"issues": [1], "events": [1, 2], "sources": [1], "judgments": [1], "runs": [1]},
            })
            return 0

        try:
            with patch("run.run_subprocess", side_effect=fake_subprocess):
                rc = run.cmd_package(_ns(date, dry_run=False, confirm=True))
            self.assertEqual(rc, 0)
            # 只调 build_knowledge_package，绝不调 sync_*
            self.assertEqual(len(seen_cmds), 1, seen_cmds)
            self.assertIn("build_knowledge_package.py", seen_cmds[0])
            self.assertNotIn("sync_lark_base.py", seen_cmds[0])
            self.assertNotIn("sync_candidates_base.py", seen_cmds[0])
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["stages"]["7"]["status"], "PASS")
            self.assertEqual(saved["knowledge_sync"]["status"], "PACKAGED")
            self.assertEqual(saved["run_status"], "PACKAGED")
            self.assertIn("package_sha256", saved["knowledge_sync"])
            self.assertEqual(saved["knowledge_sync"]["counts"]["events"], 2)
        finally:
            shutil.rmtree(state_path.parent, ignore_errors=True)

    def test_sync_knowledge_requires_all_feishu_sent(self):
        """V3.9.0：sync-knowledge 在飞书未全部 SENT 时必须阻断。"""
        date = "2099-03-10"
        state = run.empty_run_state(date)
        run.set_stage(state, "7", "PASS")
        state["forum"] = {"status": "PUBLISHED", "topic_id": 521, "post_id": 673, "url": "https://forum.example/t/test/521"}
        state["run_status"] = "SYNC_PENDING"
        # 一个群 SENT、一个群 FAILED → 必须拒绝
        state["feishu"] = {"status": "FAILED", "groups": [
            {"chat_id": "a", "group_name": "A", "status": "SENT"},
            {"chat_id": "b", "group_name": "B", "status": "FAILED"},
        ]}
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        run.atomic_write_json(state_path, state)
        try:
            with self._push_on(), patch("run.run_subprocess", return_value=0) as mock_subprocess:
                rc = run.cmd_sync_knowledge(_ns(date, dry_run=False, confirm=True))
            self.assertEqual(rc, 2)
            mock_subprocess.assert_not_called()  # 未达前置，绝不该发起同步
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["stages"]["8"]["status"], "FAIL")
            self.assertIn("飞书", saved["stages"]["8"]["error"])
        finally:
            shutil.rmtree(state_path.parent, ignore_errors=True)

    def test_sync_knowledge_requires_confirm(self):
        """sync-knowledge 是外部写动作，缺 --confirm 即拒绝。"""
        date = "2099-03-11"
        state = run.empty_run_state(date)
        run.set_stage(state, "7", "PASS")
        state["forum"] = {"status": "PUBLISHED", "topic_id": 521, "post_id": 673, "url": "https://f/t/521"}
        state["feishu"] = {"status": "SENT", "groups": [{"chat_id": "a", "status": "SENT"}]}
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        run.atomic_write_json(state_path, state)
        try:
            with patch("run.run_subprocess", return_value=0) as mock_subprocess:
                rc = run.cmd_sync_knowledge(_ns(date, dry_run=False, confirm=False))
            self.assertEqual(rc, 2)
            mock_subprocess.assert_not_called()
        finally:
            shutil.rmtree(state_path.parent, ignore_errors=True)

    def test_sync_knowledge_success_sets_completed(self):
        """V3.9.0：全部群 SENT 且远端回执 SYNCED → stage 8 PASS + RUN_COMPLETED。"""
        date = "2099-03-12"
        state = run.empty_run_state(date)
        run.set_stage(state, "7", "PASS")
        state["forum"] = {"status": "PUBLISHED", "topic_id": 521, "post_id": 673, "url": "https://f/t/521"}
        state["feishu"] = {"status": "SENT", "groups": [
            {"chat_id": "a", "status": "SENT"}, {"chat_id": "b", "status": "SENT"}, {"chat_id": "c", "status": "SENT"},
        ]}
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        run.atomic_write_json(state_path, state)
        files = run.work_files(date)
        files["knowledge_package"].parent.mkdir(parents=True, exist_ok=True)
        run.atomic_write_json(files["knowledge_package"], {"tables": {}})

        def fake_subprocess(cmd, dry_run, env_extra=None):
            if "sync_lark_base.py" in " ".join(cmd):
                run.atomic_write_json(files["knowledge_package"].with_suffix(".upsert-plan.json"),
                                      {"status": "SYNCED"})
            return 0

        try:
            with patch("run.run_subprocess", side_effect=fake_subprocess):
                rc = run.cmd_sync_knowledge(_ns(date, dry_run=False, confirm=True))
            self.assertEqual(rc, 0)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["stages"]["8"]["status"], "PASS")
            self.assertEqual(saved["knowledge_sync"]["status"], "SYNCED")
            self.assertEqual(saved["run_status"], "RUN_COMPLETED")
        finally:
            shutil.rmtree(state_path.parent, ignore_errors=True)


class TestPlaceholderGuard(unittest.TestCase):
    """V3.9.0：占位链接门（草稿可占位、发送必拒绝）。"""
    def _push_on(self):
        """gw-ai-pulse（2026-11-13）：推送默认关闭，这些门禁测试必须先显式打开。"""
        return patch.object(run, "_feishu_push_enabled", return_value=True)


    def test_is_placeholder_url(self):
        self.assertTrue(run.is_placeholder_url(""))
        self.assertTrue(run.is_placeholder_url(None))
        self.assertTrue(run.is_placeholder_url("<FORUM_URL_PENDING>"))
        self.assertTrue(run.is_placeholder_url("https://x/t/<FORUM_URL_PENDING>"))
        self.assertTrue(run.is_placeholder_url("待回填"))
        self.assertFalse(run.is_placeholder_url("https://aicopilot.goldwind.com.cn:3211/t/ai-9-23/555"))

    def test_is_placeholder_topic_id(self):
        self.assertTrue(run.is_placeholder_topic_id(""))
        self.assertTrue(run.is_placeholder_topic_id("PENDING"))
        self.assertTrue(run.is_placeholder_topic_id("0"))
        self.assertFalse(run.is_placeholder_topic_id("555"))
        self.assertFalse(run.is_placeholder_topic_id(555))

    def test_push_blocks_placeholder_publish_state(self):
        """发布状态里是占位链接 → push fail closed。"""
        date = "2099-03-13"
        state = run.empty_run_state(date)
        run.set_stage(state, "7", "PASS")
        state["forum"] = {"status": "PUBLISHED"}
        state["run_status"] = "PACKAGED"
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        files = run.work_files(date)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        run.atomic_write_json(state_path, state)
        run.atomic_write_json(files["publish_state"], {
            "status": "OK", "topic_id": "PENDING", "post_id": 0,
            "url": "<FORUM_URL_PENDING>",
        })
        try:
            with self._push_on(), patch("run.run_subprocess", return_value=0) as mock_subprocess:
                rc = run.cmd_push(_ns(date, dry_run=False, confirm=True))
            self.assertEqual(rc, 2)
            mock_subprocess.assert_not_called()
        finally:
            shutil.rmtree(state_path.parent, ignore_errors=True)


class TestPrecheck(unittest.TestCase):
    """V3.9.0：只读预检（不写盘、不回显凭据）。"""

    def setUp(self):
        # 预检测试对 work_dir 敏感（二维码是否存在）；显式锁定隔离目录，
        # 避免被 TestWorkDirResolution 修改 AI_RADAR_WORK_DIR 的用例带偏（顺序依赖）。
        self._wd_snapshot = os.environ.get("AI_RADAR_WORK_DIR")
        os.environ["AI_RADAR_WORK_DIR"] = str(_ISOLATED_WD)

    def tearDown(self):
        if self._wd_snapshot is None:
            os.environ.pop("AI_RADAR_WORK_DIR", None)
        else:
            os.environ["AI_RADAR_WORK_DIR"] = self._wd_snapshot

    def test_precheck_missing_qr_blocks(self):
        """二维码缺失 → BLOCK(exit 2)，且不写 run-state。"""
        date = "2026-09-24"
        # 隔离 work_dir 内不放任何二维码 → 必然缺
        ns = _ns(date, dry_run=False, confirm=False)
        with redirect_stdout(io.StringIO()) as buf:
            rc = run.cmd_precheck(ns)
        self.assertEqual(rc, 2, buf.getvalue())
        # 预检只读：不得创建 run-state
        self.assertFalse(run.work_files(date)["state"].exists())

    def test_precheck_all_qr_present_passes_that_check(self):
        """5 码齐备时二维码检查应通过（用临时文件模拟）。"""
        date = "2026-09-24"
        created = []
        try:
            for _, filename in run.PRECHECK_QR_FILES:
                p = Path(_ISOLATED_WD) / filename
                if not p.exists():
                    p.write_bytes(b"fake")
                    created.append(p)
            with redirect_stdout(io.StringIO()) as buf:
                run.cmd_precheck(_ns(date, dry_run=False, confirm=False))
            data = json.loads(buf.getvalue())
            qr_check = next(c for c in data["checks"] if c["check"] == "二维码")
            self.assertTrue(qr_check["ok"], qr_check)
        finally:
            for p in created:
                p.unlink(missing_ok=True)

    def test_precheck_output_has_no_secret_values(self):
        """预检输出只报键名，绝不出现任何凭据值。"""
        date = "2026-09-24"
        ns = _ns(date, dry_run=False, confirm=False)
        with redirect_stdout(io.StringIO()) as buf:
            run.cmd_precheck(ns)
        out = buf.getvalue()
        data = json.loads(out)
        self.assertIn("checks", data)
        self.assertIn("blockers", data)
        self.assertIn("note", data)
        # 输出中不得出现任何形如 key=value 的泄漏（只允许键名）
        self.assertNotIn("DISCOURSE_API_KEY=", out)
        for check in data["checks"]:
            self.assertNotIn("value", check)

    def test_precheck_probes_do_not_write_state(self):
        """预检是纯只读探针：调用前后不产生 run-state 文件。"""
        date = "2026-09-24"
        state_path = run.work_files(date)["state"]
        state_path.unlink(missing_ok=True)
        with redirect_stdout(io.StringIO()):
            run.cmd_precheck(_ns(date, dry_run=False, confirm=False))
        self.assertFalse(state_path.exists())


class TestIdempotency(unittest.TestCase):
    """幂等：重跑 plan 不破坏已 PASS 状态。"""

    @patch("run.run_subprocess", return_value=0)
    def test_plan_increments_revision(self, mock_subproc):
        date = "2099-04-01"
        # 先 plan 一次
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        if state_path.exists():
            state_path.unlink()
        rc = run.cmd_plan(_ns(date, dry_run=False))
        self.assertEqual(rc, 0)
        self.assertEqual(mock_subproc.call_count, 1)
        with state_path.open() as f:
            rev1 = json.load(f)["revision"]
        # 再 plan 一次
        rc = run.cmd_plan(_ns(date, dry_run=False))
        self.assertEqual(rc, 0)
        self.assertEqual(mock_subproc.call_count, 2)
        with state_path.open() as f:
            rev2 = json.load(f)["revision"]
        self.assertEqual(rev2, rev1 + 1)
        # 清理
        import shutil
        shutil.rmtree(state_path.parent, ignore_errors=True)


class TestStatusSubcommand(unittest.TestCase):
    """status 子命令输出。"""

    def test_status_9_04_shows_all_passes(self):
        # 在隔离 work_dir 内构造一个全 PASS 状态，避免依赖真实 09-04 产物
        import argparse
        from pathlib import Path
        date = "2026-09-04"
        state = run.empty_run_state(date)
        for sid in ["1", "2", "3", "4", "5", "6", "7", "8"]:
            run.set_stage(state, sid, "PASS")
        state["run_status"] = "COMPLETED"
        state["current_stage"] = 8
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        run.atomic_write_json(state_path, state)
        ns = argparse.Namespace(
            report_date=date,
            timezone="Asia/Shanghai",
            env_file=str(run.get_work_dir() / ".env"),
            mode="normal",
            dry_run=True,
            confirm=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run.cmd_status(ns)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        data = json.loads(out)
        self.assertEqual(data["run_status"], "COMPLETED")
        self.assertEqual(data["current_stage"], 8)
        for sid in ["1", "2", "3", "4", "5", "6", "7", "8"]:
            self.assertEqual(data["stages"][sid], "PASS")


class TestReviewStage23Contract(unittest.TestCase):
    """V3.8.7 / V3.9.0 回归：cmd_review 不得被 stage2/3 状态阻断。

    背景：`cmd_retrieval` 只把 2/3 置 RUNNING，无 CLI 可置 PASS；而 SKILL §10.1
    明确 review 阻断门为「无」。原实现却要求 2/3 必须 PASS → 正常流程必然卡
    `STAGE23_REQUIRED`（2026-09-23 第 22 期实测返工点）。现要求进入阶段 4 前把
    非 FAIL 的 2/3 落 PASS，FAIL 仍如实保留。
    """

    def _seed(self, date):
        state = run.empty_run_state(date)
        run.set_stage(state, "1", "PASS")
        run.set_stage(state, "2", "RUNNING")
        run.set_stage(state, "3", "RUNNING")
        state["run_status"] = "STAGE3_RETRIEVED"
        state_path = run.get_work_dir() / f"AI-RADAR-{date}" / "run-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        run.atomic_write_json(state_path, state)
        # semantic 文件必须存在，否则 cmd_review 会在语义缺失分支返回 2
        run.work_files(date)["semantic"].write_text("{}", encoding="utf-8")
        return state_path

    def test_review_passes_running_stage23(self):
        date = "2099-05-01"
        state_path = self._seed(date)
        try:
            rc = run.cmd_review(_ns(date, dry_run=False))
            self.assertEqual(rc, 0)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["stages"]["2"]["status"], "PASS")
            self.assertEqual(saved["stages"]["3"]["status"], "PASS")
            self.assertEqual(saved["stages"]["4"]["status"], "PASS")
            self.assertEqual(saved["run_status"], "STAGE4_REVIEWED")
        finally:
            shutil.rmtree(state_path.parent, ignore_errors=True)

    def test_review_preserves_failed_stage23(self):
        """阶段 2/3 真失败时不得被 review 洗白成 PASS。"""
        date = "2099-05-02"
        state_path = self._seed(date)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        run.set_stage(state, "2", "FAIL", error="检索失败")
        run.atomic_write_json(state_path, state)
        try:
            rc = run.cmd_review(_ns(date, dry_run=False))
            self.assertEqual(rc, 0)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["stages"]["2"]["status"], "FAIL")
            self.assertEqual(saved["stages"]["3"]["status"], "PASS")
        finally:
            shutil.rmtree(state_path.parent, ignore_errors=True)


class TestCliHelp(unittest.TestCase):
    """CLI 结构。"""

    def test_subcommands_exist(self):
        from run import build_parser
        p = build_parser()
        for name in ["precheck", "plan", "retrieval", "review", "self-check",
                     "publish", "package", "push-draft", "push", "sync-knowledge",
                     "all", "status"]:
            self.assertIn(name, p.format_help())

    def test_dry_run_default_false(self):
        from run import build_parser
        p = build_parser()
        args = p.parse_args(["plan", "--report-date", "2099-12-31"])
        self.assertFalse(args.dry_run)
        self.assertFalse(args.confirm)


def _ns(report_date: str, dry_run: bool = False, confirm: bool = False):
    """构造测试用 Namespace。"""
    import argparse
    return argparse.Namespace(
        report_date=report_date,
        timezone="Asia/Shanghai",
        env_file=str(run.get_work_dir() / ".env"),
        mode="normal",
        dry_run=dry_run,
        confirm=confirm,
    )


class TestChinaWorkdayGuard(unittest.TestCase):
    """中国法定工作日硬校验（V3.7.0）：节假日落周中停刊、周末调休补班出刊。

    覆盖 2026 国办发明电〔2025〕7号 安排的实际日期，四态全部断言。
    """

    def test_off_holiday_weekday(self):
        # 法定休假日落在工作周 → 应停刊
        self.assertEqual(run.china_workday_status("2026-01-01"), "off")   # 元旦周四
        self.assertEqual(run.china_workday_status("2026-02-16"), "off")   # 春节周一
        self.assertEqual(run.china_workday_status("2026-02-23"), "off")   # 春节周一(返程日)
        self.assertEqual(run.china_workday_status("2026-05-01"), "off")   # 劳动节周五
        self.assertEqual(run.china_workday_status("2026-10-01"), "off")   # 国庆周四
        self.assertEqual(run.china_workday_status("2026-10-07"), "off")   # 国庆周三

    def test_off_holiday_weekend(self):
        # 法定休假日落在周末（周六周日）→ 也属于 off，normal 停刊
        self.assertEqual(run.china_workday_status("2026-01-03"), "off")   # 元旦周六
        self.assertEqual(run.china_workday_status("2026-02-21"), "off")   # 春节周六
        self.assertEqual(run.china_workday_status("2026-05-03"), "off")   # 劳动节周日
        self.assertEqual(run.china_workday_status("2026-10-04"), "off")   # 国庆周日

    def test_makeup_weekend(self):
        # 周末调休补班 → 应放行出刊
        self.assertEqual(run.china_workday_status("2026-01-04"), "makeup")  # 元旦补班周日
        self.assertEqual(run.china_workday_status("2026-02-14"), "makeup")  # 春节补班周六
        self.assertEqual(run.china_workday_status("2026-02-28"), "makeup")  # 春节补班周六
        self.assertEqual(run.china_workday_status("2026-05-09"), "makeup")  # 劳动节补班周六
        self.assertEqual(run.china_workday_status("2026-09-20"), "makeup")  # 国庆补班周日
        self.assertEqual(run.china_workday_status("2026-10-10"), "makeup")  # 国庆补班周六

    def test_weekend(self):
        # 普通周末 → 应停刊（避开 09-25~09-27 中秋节，那是 off 不是普通周末）
        self.assertEqual(run.china_workday_status("2026-10-24"), "weekend")  # 周六
        self.assertEqual(run.china_workday_status("2026-11-07"), "weekend")  # 周六
        self.assertEqual(run.china_workday_status("2026-11-08"), "weekend")  # 周日

    def test_workday(self):
        # 普通工作日 → 放行
        self.assertEqual(run.china_workday_status("2026-09-18"), "workday")  # 周五
        self.assertEqual(run.china_workday_status("2026-11-02"), "workday")  # 周一
        self.assertEqual(run.china_workday_status("2026-11-06"), "workday")  # 周五

    def test_main_blocks_normal_off(self):
        # normal 模式遇法定休假日 → 停刊：dry-run 返回 0 且不触发 subprocess/handler。
        # 用已过去的合法工作日外的休假日（2026-05-01 劳动节），避免撞"今日+1"防呆护栏。
        with patch("run.cmd_plan") as mock_plan:
            rc = run.main(["plan", "--report-date", "2026-05-01",
                           "--mode", "normal", "--dry-run"])
            mock_plan.assert_not_called()  # 停刊不该走到 handler
        self.assertEqual(rc, 0)  # dry-run 停刊返回 0（不写盘）

    def test_main_blocks_normal_off_non_dry(self):
        # normal 停刊非 dry-run 返回专用码 4（阻断后续阶段）
        with patch("run.cmd_plan") as mock_plan:
            rc = run.main(["plan", "--report-date", "2026-05-01", "--mode", "normal"])
            mock_plan.assert_not_called()
        self.assertEqual(rc, 4)

    def test_main_passes_workday(self):
        # normal 普通工作日不触发停刊（返回非专用停刊码 4；隔离 work_dir 无 state，
        # status 返回 1=NO_STATE，属环境隔离预期）
        rc = run.main(["status", "--report-date", "2026-09-18", "--mode", "normal"])
        self.assertNotEqual(rc, 4)


class TestReportDateGuard(unittest.TestCase):
    """report_date 防呆护栏（V3.3.1，拦截假日期污染目录）。

    用当前系统日期动态推算，避免硬编码固定日期随时间漂移导致的过期用例（09-11 记录：
    test_reject_fake_future 断言 2026-09-10 为未来，到 09-14 已是过去合法日期）。
    """
    @staticmethod
    def _d(offset_days):
        return (datetime.now(run.parse_timezone("Asia/Shanghai")).date()
                + timedelta(days=offset_days)).isoformat()

    def test_valid_current_date(self):
        self.assertIsNone(run.validate_report_date(self._d(0)))
        self.assertIsNone(run.validate_report_date(self._d(-1)))  # 昨天

    def test_future_backfill_grace(self):
        # today+1 仍放行（future-backfill 余量）
        self.assertIsNone(run.validate_report_date(self._d(1)))

    def test_reject_fake_future(self):
        # 后天以上拦截
        self.assertIsNotNone(run.validate_report_date(self._d(2)))
        self.assertIsNotNone(run.validate_report_date(self._d(10)))
        # 典型假日期
        self.assertIsNotNone(run.validate_report_date("2099-03-04"))
        self.assertIsNotNone(run.validate_report_date("2099-12-31"))

    def test_reject_too_old(self):
        self.assertIsNotNone(run.validate_report_date("1999-01-01"))

    def test_reject_bad_format(self):
        self.assertIsNotNone(run.validate_report_date("2026/09/08"))
        self.assertIsNotNone(run.validate_report_date("08-09-2026"))
        self.assertIsNotNone(run.validate_report_date(""))


class TestPlaceholderLeakScan(unittest.TestCase):
    """V3.9.1 回归：占位终检只扫外发内容，不得被结构性字段名误伤（2026-09-24 教训）。"""

    REAL_URL = "https://aicopilot.goldwind.com.cn:3211/t/ai-9-24/559"
    REAL_TOPIC = "559"

    def _payload(self, message):
        return {
            "article_url": self.REAL_URL,
            "topic_id": self.REAL_TOPIC,
            "placeholder": False,
            "placeholder_url": "",
            "payloads": [
                {
                    "chat_id": "oc_x",
                    "group_name": "测试群",
                    "message": message,
                    "main_summary": "【能力复用】示例摘要。",
                    "main_title": "示例头条",
                    "skip": False,
                }
            ],
        }

    def test_real_payload_with_placeholder_keys_not_flagged(self):
        """真实 payload 含 placeholder/placeholder_url 字段名时不得误报。"""
        out = run.leaked_placeholder_tokens(self._payload("正常文案，无占位。"), self.REAL_URL, self.REAL_TOPIC)
        self.assertEqual(out, [])

    def test_message_with_pending_token_still_blocked(self):
        """消息体里真的残留占位 token 仍须 fail closed。"""
        out = run.leaked_placeholder_tokens(
            self._payload("阅读全文：<FORUM_URL_PENDING>"), self.REAL_URL, self.REAL_TOPIC
        )
        self.assertIn("<FORUM_URL_PENDING>", out)

    def test_skipped_group_content_ignored(self):
        """skip=true 的群不发送，其内容不参与判定。"""
        payload = self._payload("正常文案")
        payload["payloads"][0]["skip"] = True
        payload["payloads"][0]["message"] = "阅读全文：<FORUM_URL_PENDING>"
        out = run.leaked_placeholder_tokens(payload, self.REAL_URL, self.REAL_TOPIC)
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
