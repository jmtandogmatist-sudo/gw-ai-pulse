#!/usr/bin/env python3
"""verify.py — 发布前校验固化（verdict-only）。

背景（2026-11-04 上下文溢出复盘）：单期有 124 次「取数据 → 人工判断」的往返，
其中 45 次数字符、40 次跑正则、39 次调 run.py，每次都把原始数据带回上下文。
本脚本把这些检查在**同一个进程内**跑完，只回「判定 + 一行明细」。

覆盖：
  1. 逐条发布前自查（复用 self_check.run_checks，含 V5 版式与全部硬门禁）
  2. 总装预检（复用 preflight_check.run_preflight，含知识包 dry-run 绑定）
  3. 产物齐备性 + 正文 sha256 + 封面字节数 + 6 码二维码文件存在性

用法：
  verify.py --report-date 2026-11-04 [选项]

选项：
  --work-dir DIR       覆盖工作目录（默认读 config/runtime.json）
  --timezone TZ        默认 Asia/Shanghai
  --mode M             normal / backfill / revise / resume（默认 normal）
  --recent-days N      跨期去重回看天数（默认 2）
  --full               展开全部逐条明细（默认只回类别汇总 + 失败明细）
  --json               以 JSON 输出结构化结果
  --read-only          绝不写盘（审计历史期次用）
  --force-write        即使当期已发布也重写自查报告（默认拒绝覆盖已发布期次）
  --no-package-dry-run 跳过知识包 dry-run 绑定预检

退出码：0 全部 PASS / 2 存在 FAIL / 3 输入产物缺失
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import OrderedDict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import preflight_check  # noqa: E402
import self_check  # noqa: E402

DEFAULT_QR_FILES = (
    ("本地大模型用户交流群", "群二维码_本地大模型.png"),
    ("AI智能体开发平台运营群", "群二维码_Dify平台.png"),
    ("语义本体构建交流群", "群二维码_金风科技.png"),
    ("KNS统一数据平台", "群二维码_KNS.png"),
    ("标准化助手交流群", "群二维码_标准化助手.png"),
    ("风衍大模型用户交流群", "群二维码_风衍大模型.jpeg"),
)
COVER_MIN_BYTES = 500 * 1024
# V5 版式把证据回执内嵌进语义复核 JSON，独立回执文件不再产出；缺失不算失败。
OPTIONAL_ARTIFACTS = ("evidence",)


def _qr_files():
    try:
        import run as run_module  # type: ignore

        return tuple(run_module.PRECHECK_QR_FILES)
    except Exception:
        return DEFAULT_QR_FILES


def _work_dir():
    try:
        import run as run_module  # type: ignore

        return Path(run_module.get_work_dir())
    except Exception:
        return Path.home() / "WorkBuddy" / "ai-radar-daily"


def _resolve_paths(work_dir: Path, report_date: str):
    try:
        import run as run_module  # type: ignore

        files = dict(run_module.work_files(report_date))
    except Exception:
        base = work_dir
        files = {
            "md": base / f"AI雷达日报_{report_date}.md",
            "cover": base / f"AI雷达日报_{report_date}_封面.png",
            "search_log": base / f"AI雷达日报_{report_date}_检索清单.json",
            "semantic": base / f"AI雷达日报_{report_date}_语义复核.json",
            "self_check": base / f"AI雷达日报_{report_date}_自查报告.md",
            "recent_dedup": base / f"AI雷达日报_{report_date}_近期去重.json",
            "state": base / f"AI-RADAR-{report_date}" / "run-state.json",
            "publish_state": base / f"AI-RADAR-{report_date}" / "publish-state.json",
            "preflight_report": base / f"AI-RADAR-{report_date}" / "preflight-report.json",
            "knowledge_package": base / f"AI-RADAR-{report_date}" / "knowledge-package" / "package.json",
            "homepage_feed": base / f"AI雷达日报_{report_date}_首页数据.json",
            "homepage_feed_state": base / f"AI-RADAR-{report_date}" / "homepage-feed-state.json",
            "contract": base / f"AI-RADAR-{report_date}" / "run-contract.json",
            "evidence": base / f"AI雷达日报_{report_date}_证据回执.json",
            "feishu_push_json": base / f"AI雷达日报_{report_date}_飞书推送.json",
        }
    return {name: Path(value) for name, value in files.items()}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(args):
    work_dir = Path(args.work_dir) if args.work_dir else _work_dir()
    files = _resolve_paths(work_dir, args.report_date)

    missing_required = [name for name in ("md", "semantic", "search_log") if not files[name].is_file()]
    if missing_required:
        return {
            "status": "INPUT_MISSING",
            "report_date": args.report_date,
            "work_dir": str(work_dir),
            "missing": [f"{name}: {files[name]}" for name in missing_required],
        }

    raw = files["md"].read_text(encoding="utf-8")
    reset = self_check.resolve_report_date(str(files["md"]), raw, args.report_date, args.timezone)
    report_date = reset[0]

    artifacts = OrderedDict()
    for name, path in files.items():
        artifacts[name] = {"path": str(path), "exists": path.is_file(),
                           "bytes": path.stat().st_size if path.is_file() else 0}

    qr_checks = []
    for group_name, filename in _qr_files():
        qr_path = work_dir / filename
        qr_checks.append({"group": group_name, "file": filename,
                          "exists": qr_path.is_file(),
                          "bytes": qr_path.stat().st_size if qr_path.is_file() else 0})

    cover = files["cover"]
    cover_bytes = cover.stat().st_size if cover.is_file() else 0

    qr1 = work_dir / "群二维码_本地大模型.png"
    qr2 = work_dir / "群二维码_Dify平台.png"
    qr3 = work_dir / "群二维码_金风科技.png"

    results = self_check.run_checks(
        str(files["md"]),
        str(files["semantic"]),
        str(files["search_log"]),
        cover_path=str(cover) if cover.is_file() else None,
        qr1_path=str(qr1),
        qr2_path=str(qr2),
        qr3_path=str(qr3),
        recent_days=args.recent_days,
        recent_data_path=str(files["recent_dedup"]) if files["recent_dedup"].is_file() else None,
        report_date=args.report_date,
        timezone=args.timezone,
        require_evidence_receipts=True,
        mode=args.mode,
    )

    preflight = preflight_check.run_preflight(
        files["md"],
        files["semantic"],
        files["search_log"],
        args.report_date,
        args.timezone,
        self_check_path=files["self_check"] if files["self_check"].is_file() else None,
        publish_state_path=files["publish_state"] if files["publish_state"].is_file() else None,
        run_state_path=files["state"] if files["state"].is_file() else None,
        package_dry_run=not args.no_package_dry_run,
    )

    return {
        "status": "COLLECTED",
        "report_date": report_date.isoformat(),
        "work_dir": str(work_dir),
        "body": {"path": str(files["md"]), "chars": len(raw), "lines": raw.count("\n") + 1,
                 "sha256": _sha256(files["md"])},
        "cover": {"path": str(cover), "exists": cover.is_file(), "bytes": cover_bytes,
                  "meets_min": cover_bytes >= COVER_MIN_BYTES},
        "qr_files": qr_checks,
        "artifacts": artifacts,
        "self_check": {"total": len(results),
                       "passed": sum(1 for item in results if item["passed"]),
                       "failed": [item for item in results if not item["passed"]],
                       "items": results},
        "preflight": preflight,
        "files": {name: str(path) for name, path in files.items()},
    }


def summarize(data):
    """按类别聚合判定。只保留判定与失败明细，不回原始数据。"""
    by_category = OrderedDict()
    for item in data["self_check"]["items"]:
        bucket = by_category.setdefault(item["category"], {"total": 0, "failed": 0})
        bucket["total"] += 1
        if not item["passed"]:
            bucket["failed"] += 1
    return by_category


def render(data, full: bool) -> int:
    if data["status"] == "INPUT_MISSING":
        print("VERIFY_STATUS: INPUT_MISSING")
        print(f"REPORT_DATE: {data['report_date']}")
        print(f"WORK_DIR: {data['work_dir']}")
        for item in data["missing"]:
            print(f"  MISSING {item}")
        return 3

    sc = data["self_check"]
    pf = data["preflight"]
    artifacts = data["artifacts"]
    present = sum(1 for item in artifacts.values() if item["exists"])
    qr_ok = sum(1 for item in data["qr_files"] if item["exists"])

    failed_categories = {name: bucket for name, bucket in summarize(data).items() if bucket["failed"]}
    ok = not sc["failed"] and pf["status"] == "PASS"

    print(f"VERIFY_STATUS: {'PASS' if ok else 'FAIL'}")
    print(f"REPORT_DATE: {data['report_date']}   WORK_DIR: {data['work_dir']}")
    body = data["body"]
    print(f"BODY: {body['chars']:,} 字符 / {body['lines']} 行   sha256:{body['sha256'][:12]}")
    cover = data["cover"]
    cover_flag = "OK" if cover["meets_min"] else "TOO_SMALL"
    print(f"COVER: {cover['bytes']:,} 字节 [{cover_flag}]   二维码文件 {qr_ok}/{len(data['qr_files'])}")
    print(f"SELF_CHECK: {sc['passed']}/{sc['total']} PASS")
    dry = pf.get("package_dry_run", {})
    print(f"PREFLIGHT: {pf['status']}（errors {pf['error_count']}，events {pf['event_count']}，"
          f"package dry-run {dry.get('status', 'N/A')}）")
    print(f"ARTIFACTS: {present}/{len(artifacts)} 存在")

    print("按类别：")
    for name, bucket in summarize(data).items():
        mark = "" if not bucket["failed"] else f"  ← {bucket['failed']} FAIL"
        print(f"  {name:<12} {bucket['total'] - bucket['failed']}/{bucket['total']}{mark}")

    missing_artifacts = [name for name, item in artifacts.items() if not item["exists"]]
    missing_required_artifacts = [name for name in missing_artifacts if name not in OPTIONAL_ARTIFACTS]
    missing_optional = [name for name in missing_artifacts if name in OPTIONAL_ARTIFACTS]
    if missing_required_artifacts:
        print(f"缺失产物：{', '.join(missing_required_artifacts)}")
    if missing_optional:
        print(f"可选产物未产出（V5 已内嵌）：{', '.join(missing_optional)}")
    missing_qr = [item["group"] for item in data["qr_files"] if not item["exists"]]
    if missing_qr:
        print(f"缺失二维码：{', '.join(missing_qr)}")

    if sc["failed"] or pf["error_count"]:
        print("FAILURES:")
        for item in sc["failed"]:
            print(f"  [自查/{item['category']}] {item['message']}")
        for item in pf["errors"]:
            print(f"  [预检/{item['code']}] {item['message']}")
    else:
        print("FAILURES: 无")

    if full:
        print("全部自查明细：")
        for item in sc["items"]:
            print(f"  [{'PASS' if item['passed'] else 'FAIL'}] [{item['category']}] {item['message']}")
        print(f"预检明细：{pf['error_count']} 条错误，package dry-run {json.dumps(dry, ensure_ascii=False)}")

    if failed_categories or pf["error_count"] or missing_required_artifacts or missing_qr:
        return 2
    return 0


def persist(data, args) -> str:
    """默认写回自查报告；已发布期次拒绝覆盖，除非 --force-write。"""
    if args.read_only:
        return "SKIPPED_READ_ONLY"
    target = Path(data["files"]["self_check"])
    if _is_published_target(data) and not args.force_write:
        return "SKIPPED_ALREADY_PUBLISHED"
    status, _ = self_check.write_report(
        str(target), data["body"]["path"], data["self_check"]["items"], report_date=data["report_date"]
    )
    return f"WRITTEN_{status}"


def _is_published_target(data) -> bool:
    state = Path(data["artifacts"]["publish_state"]["path"])
    if not state.is_file():
        return False
    try:
        payload = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return str(payload.get("status", "")).upper() == "OK"


def build_parser():
    parser = argparse.ArgumentParser(prog="verify.py", description="发布前校验固化（verdict-only）")
    parser.add_argument("--report-date", required=True,
                        help="刊发日期 YYYY-MM-DD；当天须为有效报告日（与 validate_report_date 同口径）")
    parser.add_argument("--work-dir")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--mode", choices=("normal", "backfill", "revise", "resume"), default="normal")
    parser.add_argument("--recent-days", type=int, default=2)
    parser.add_argument("--full", action="store_true", help="展开全部逐条明细")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--read-only", action="store_true", help="绝不写盘")
    parser.add_argument("--force-write", action="store_true", help="已发布期次也重写自查报告")
    parser.add_argument("--no-package-dry-run", action="store_true")
    return parser


def main(argv=None):
    try:
        import signal

        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError, ImportError):
        pass
    args = build_parser().parse_args(argv)
    try:
        data = collect(args)
    except Exception as exc:
        print(f"VERIFY_STATUS: ERROR  {type(exc).__name__}: {exc}")
        return 1

    write_result = persist(data, args)

    if args.json:
        payload = dict(data)
        payload["self_check"] = {key: value for key, value in data["self_check"].items() if key != "items"}
        payload["write_result"] = write_result
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if data["status"] != "INPUT_MISSING" and not data["self_check"]["failed"] and data["preflight"]["status"] == "PASS" else 2

    if write_result != "SKIPPED_READ_ONLY":
        print(f"SELF_CHECK_REPORT: {write_result}")
    return render(data, args.full)


if __name__ == "__main__":
    raise SystemExit(main())
