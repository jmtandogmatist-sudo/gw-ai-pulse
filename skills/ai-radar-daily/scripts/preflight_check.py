#!/usr/bin/env python3
"""AI 雷达日报发布前总装预检（Preflight）。

目标：在 self_check / publish 之前发现跨产物漂移，尤其是：
- 正文 Markdown 标题/来源 URL 与 semantic_review.source_urls 不一致；
- 正文条目无法绑定 semantic_review；
- 正文条目无法被 search_log SELECTED 候选覆盖；
- 候选池 schema、SELECTED 弱源、跟踪报道格式在发布前才暴露。

本脚本只读本地产物，不访问外部系统，不修改文件。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from build_knowledge_package import (  # type: ignore
    PackageError,
    build_package,
    normalize_title,
    normalize_url,
    parse_events,
    section_text as package_section_text,
    semantic_records,
    semantic_for_event,
)
from common import parse_report_date  # type: ignore
import layout_v5  # type: ignore
from self_check import (  # type: ignore
    WEAK_SOURCE_PATTERNS,
    MAX_FEATURE_BODY_CHARS,
    MAX_DIGEST_BODY_CHARS,
    candidate_weak_source_refs,
    date_fields_valid,
    feature_body_char_count,
    parse_iso_date,
)

VALID_DECISIONS = {
    "SELECTED",
    "TRACKING",   # V5 第二层·本周跟踪
    "SIGNAL",     # V5 第三层·信号雷达
    "REJECTED_DUPLICATE",
    "REJECTED_STALE",
    "REJECTED_LOW_PRIORITY",
    "REJECTED_UNVERIFIED",
}
REQUIRED_OFFICIAL_SOURCES = [
    "openai", "anthropic", "google", "meta", "microsoft", "xai",
    "deepseek", "qwen", "bytedance", "智谱", "月之暗面", "minimax", "百度", "腾讯",
]
TRACKING_TITLE_RE = re.compile(r"[（(]\s*跟踪报道\s*[·．.]\s*\d{1,2}月\d{1,2}日\s*[）)]")
# V4.1：判断行的两种写法都要认（新增「对我们」，保留「一句话解读」兼容历史期次）
INTERPRETATION_RE = re.compile(r"(?m)^(?:\*\*)?(?:一句话解读|对我们)：(?:\*\*)?\s*(.+?)\s*$")
# V4.1：今日要紧条目为「### 【行动标签】标题」；兼容无标签写法
DAILY_ITEM_RE = re.compile(r"(?m)^###\s+(?:【[^】]+】)?\s*(.+?)\s*$")
MAX_INTERPRETATION_CHARS = 90
# V4.1 板块字数上限；今日要紧不设板块上限（逐条「对我们」行受 90 字约束）
FEATURE_LIMITS_V4 = [
    ("金风相关", MAX_FEATURE_BODY_CHARS),
    ("工具与做法", MAX_FEATURE_BODY_CHARS),
    ("行业速递", MAX_DIGEST_BODY_CHARS),
]
# V3 历史期次保留兼容
FEATURE_LIMITS_V3 = [
    ("AI×能源风电", MAX_FEATURE_BODY_CHARS),
    ("Builder信号", MAX_FEATURE_BODY_CHARS),
    ("AI工作实践", MAX_FEATURE_BODY_CHARS),
    ("深度聚焦", MAX_FEATURE_BODY_CHARS),
]


class PreflightError(ValueError):
    pass


def read_text_required(path: Path, label: str) -> str:
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PreflightError(f"无法读取{label}: {exc}") from exc
    if not value.strip():
        raise PreflightError(f"{label}为空")
    return value


def read_json_required(path: Path, label: str) -> Dict[str, Any]:
    raw = read_text_required(path, label)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PreflightError(f"{label}不是有效 JSON: {exc}") from exc
    if not isinstance(value, dict) or not value:
        raise PreflightError(f"{label}缺少结构化内容")
    return value


def add_error(errors: List[Dict[str, str]], code: str, message: str) -> None:
    errors.append({"code": code, "message": message})


def normalized_url_set(urls: Iterable[str]) -> Set[str]:
    normalized: Set[str] = set()
    for url in urls:
        try:
            normalized.add(normalize_url(str(url)))
        except Exception:
            normalized.add(str(url).strip())
    return normalized


def candidate_urls(candidate: Dict[str, Any]) -> Set[str]:
    urls: List[str] = []
    if candidate.get("source_url"):
        urls.append(str(candidate["source_url"]))
    if isinstance(candidate.get("source_urls"), list):
        urls.extend(str(url) for url in candidate["source_urls"] if url)
    return normalized_url_set(urls)


def event_urls(event: Dict[str, Any]) -> Set[str]:
    return normalized_url_set(url for _, url in event.get("links", []))


def candidate_matches_event(candidate: Dict[str, Any], event: Dict[str, Any]) -> bool:
    title_match = normalize_title(str(candidate.get("title", ""))) == event.get("normalized_title")
    body_urls = event_urls(event)
    cand_urls = candidate_urls(candidate)
    url_match = bool(body_urls) and body_urls.issubset(cand_urls)
    return title_match or url_match


def selected_candidates_for_event(candidates: Sequence[Dict[str, Any]], event: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        cand for cand in candidates
        if cand.get("decision") == "SELECTED" and candidate_matches_event(cand, event)
    ]


def check_official_sources(search_log: Dict[str, Any], errors: List[Dict[str, str]]) -> None:
    official_entries = search_log.get("official_sources_checked", [])
    if not isinstance(official_entries, list) or not official_entries:
        add_error(errors, "OFFICIAL_SOURCES_EMPTY", "search_log.official_sources_checked 为空或不是数组")
        return
    official_names = " ".join(str(item.get("name", "")).lower() for item in official_entries if isinstance(item, dict))
    missing = [name for name in REQUIRED_OFFICIAL_SOURCES if name.lower() not in official_names]
    malformed = [
        item for item in official_entries
        if not isinstance(item, dict) or not item.get("name") or "checked" not in item or not item.get("url")
    ]
    if missing or malformed:
        detail = ", ".join(missing) if missing else "存在 name/checked/url 不完整记录"
        add_error(errors, "OFFICIAL_SOURCES_INCOMPLETE", f"重点官方源记录缺失或不完整：{detail}")


def check_candidate_schema(search_log: Dict[str, Any], errors: List[Dict[str, str]]) -> None:
    candidates = search_log.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        add_error(errors, "SEARCH_CANDIDATES_EMPTY", "search_log.candidates 为空或不是数组")
        return
    for index, cand in enumerate(candidates, 1):
        owner = str(cand.get("id") or f"candidate[{index}]") if isinstance(cand, dict) else f"candidate[{index}]"
        if not isinstance(cand, dict):
            add_error(errors, "CANDIDATE_MALFORMED", f"{owner} 必须是 object")
            continue
        for field in ("title", "event_date", "source_name", "source_url", "decision"):
            if not cand.get(field):
                add_error(errors, "CANDIDATE_REQUIRED_FIELD", f"{owner} 缺少必填字段 {field}")
        decision = cand.get("decision")
        if decision and decision not in VALID_DECISIONS:
            add_error(errors, "CANDIDATE_DECISION_INVALID", f"{owner} decision={decision} 不在白名单")
        if not parse_iso_date(cand.get("event_date")):
            add_error(errors, "CANDIDATE_DATE_INVALID", f"{owner} event_date={cand.get('event_date')} 不是 YYYY-MM-DD")
        if not date_fields_valid(cand):
            add_error(errors, "CANDIDATE_DATE_FIELD_INVALID", f"{owner} 含非法日期字段")
        from seen_index import GENERIC_EVENT_KEY_RE, candidate_event_key  # type: ignore
        _ek = candidate_event_key(cand)
        if GENERIC_EVENT_KEY_RE.match(str(_ek or "").strip()):
            add_error(errors, "CANDIDATE_EVENT_KEY_GENERIC",
                      f"{owner} 的 event_key={_ek} 是逐日编号，须按内容生成（如 sig|<链接 slug>）")
        if decision == "SELECTED":
            weak = candidate_weak_source_refs(cand)
            if weak:
                add_error(errors, "SELECTED_WEAK_SOURCE", f"{owner} 为 SELECTED 但包含弱来源: {', '.join(weak)}")


def expected_dates_for(report_date: date, search_log: "Optional[Dict[str, Any]]" = None) -> Set[str]:
    if report_date.weekday() == 0:
        expected = {(report_date - timedelta(days=offset)).isoformat() for offset in range(0, 4)}
    else:
        expected = {report_date.isoformat(), (report_date - timedelta(days=1)).isoformat()}
    # 缺跑补检（V3.9.3 / retrieval-strategy §2）：若某工作日未执行，下一次执行的增量窗口
    # 自动顺延覆盖"上次执行至今"的间隔。脚本原先只按 d2/d4 硬算，累积出刊时把补检日
    # 全部误判为越窗并强制加「跟踪报道」前缀，与技能口径冲突。此处按检索清单
    # publication_metadata.missed_dates 扩展允许窗口。
    meta = (search_log or {}).get("publication_metadata")
    if isinstance(meta, dict) and meta.get("recovery_reason") == "missed_runs":
        missed = meta.get("missed_dates")
        if isinstance(missed, list):
            expected |= {str(d) for d in missed if str(d)}
    return expected


def check_tracking_format(events: Sequence[Dict[str, Any]], report_date: date, errors: List[Dict[str, str]], search_log: "Optional[Dict[str, Any]]" = None) -> None:
    expected = expected_dates_for(report_date, search_log)
    for event in events:
        title = event.get("title", "")
        event_date = event.get("event_date")
        if event_date and event_date not in expected and not TRACKING_TITLE_RE.search(title):
            add_error(
                errors,
                "TRACKING_FORMAT_INVALID",
                f"《{title}》事件日期 {event_date} 不在窗口 {sorted(expected)}，标题必须使用（跟踪报道·M月D日）格式",
            )
        if "跟踪报道" in title and not TRACKING_TITLE_RE.search(title):
            add_error(errors, "TRACKING_FORMAT_INVALID", f"《{title}》跟踪报道标题格式错误，应为（跟踪报道·M月D日）")


def check_daily_interpretation(md_raw: str, section_name: str, errors: List[Dict[str, str]]) -> None:
    """校验今日要紧（V4）/今日大事件（V3）每条的判断行存在且不超过 90 字。"""
    section = package_section_text(md_raw, section_name)
    if not section.strip():
        return
    pattern = DAILY_ITEM_RE if section_name == "今日要紧" else re.compile(r"(?m)^🔹\s+\*\*(.+?)\*\*\s*$")
    matches = list(pattern.finditer(section))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        block = section[match.end():end]
        interp = INTERPRETATION_RE.search(block)
        title = match.group(1).strip()
        if not interp:
            add_error(errors, "DAILY_INTERPRETATION_MISSING", f"《{title}》缺少判断行（ V4 写「**对我们：**」，旧版写「一句话解读：」）")
            continue
        length = len(re.sub(r"\s+", "", interp.group(1)))
        if length > MAX_INTERPRETATION_CHARS:
            add_error(
                errors,
                "DAILY_INTERPRETATION_TOO_LONG",
                f"《{title}》判断行 {length} 字，超过 {MAX_INTERPRETATION_CHARS} 字上限",
            )


def check_feature_lengths(md_raw: str, errors: List[Dict[str, str]]) -> None:
    """V4 板块（金风相关/工具与做法/行业速递）与 V3 历史板块的正文长度校验。"""
    for limits in (FEATURE_LIMITS_V4, FEATURE_LIMITS_V3):
        for feature_name, limit in limits:
            feature_section = package_section_text(md_raw, feature_name)
            if not feature_section.strip():
                continue
            feature_chars = feature_body_char_count(feature_section)
            if feature_chars > limit:
                add_error(
                    errors,
                    "FEATURE_BODY_TOO_LONG",
                    f"{feature_name}正文 {feature_chars} 字，超过 {limit} 字上限",
                )


def check_format_lint(md_raw: str, errors: List[Dict[str, str]]) -> None:
    check_daily_interpretation(md_raw, "今日要紧", errors)
    check_daily_interpretation(md_raw, "今日大事件", errors)
    check_feature_lengths(md_raw, errors)


def check_semantic_bindings(events: Sequence[Dict[str, Any]], semantic: Dict[str, Any], errors: List[Dict[str, str]]) -> None:
    try:
        records = semantic_records(semantic)
    except PackageError as exc:
        add_error(errors, "SEMANTIC_RECORDS_INVALID", str(exc))
        return
    for event in events:
        title = str(event.get("title", ""))
        review = semantic_for_event(records, event)
        if not review:
            add_error(errors, "SEMANTIC_BINDING_MISSING", f"《{title}》缺少可绑定的语义复核记录")
            continue
        if review.get("status") != "PASS":
            add_error(errors, "SEMANTIC_STATUS_INVALID", f"《{title}》语义复核 status 非 PASS")
        body_urls = event_urls(event)
        review_urls = normalized_url_set(review.get("source_urls", []))
        if body_urls != review_urls:
            add_error(
                errors,
                "SOURCE_URLS_MISMATCH",
                f"《{title}》正文来源与 semantic_review.source_urls 不一致: body={sorted(body_urls)} semantic={sorted(review_urls)}",
            )
        receipt_urls: Set[str] = set()
        receipts = review.get("evidence_receipts", [])
        if isinstance(receipts, list):
            for receipt in receipts:
                for source in receipt.get("sources", []) if isinstance(receipt, dict) else []:
                    if isinstance(source, dict) and source.get("url"):
                        receipt_urls.update(normalized_url_set([source["url"]]))
        if receipt_urls and not body_urls.issubset(receipt_urls):
            add_error(
                errors,
                "RECEIPT_SOURCE_MISMATCH",
                f"《{title}》存在正文来源未被 evidence_receipts 覆盖: missing={sorted(body_urls - receipt_urls)}",
            )


def check_search_bindings(events: Sequence[Dict[str, Any]], search_log: Dict[str, Any], errors: List[Dict[str, str]]) -> None:
    candidates = [item for item in search_log.get("candidates", []) if isinstance(item, dict)]
    for event in events:
        title = str(event.get("title", ""))
        matches = selected_candidates_for_event(candidates, event)
        if not matches:
            add_error(errors, "SEARCH_BINDING_MISSING", f"《{title}》没有可覆盖正文来源的 SELECTED 候选")
            continue
        body_urls = event_urls(event)
        if not any(body_urls.issubset(candidate_urls(candidate)) for candidate in matches):
            details = [sorted(candidate_urls(candidate)) for candidate in matches]
            add_error(
                errors,
                "SEARCH_SOURCE_MISMATCH",
                f"《{title}》正文来源未被 SELECTED 候选 source_urls 完整覆盖: body={sorted(body_urls)} candidates={details}",
            )


def check_package_dry_run(
    md_path: Path,
    semantic_path: Path,
    search_log_path: Path,
    self_check_path: Optional[Path],
    publish_state_path: Optional[Path],
    run_state_path: Optional[Path],
    report_date_value: str,
    timezone: str,
    errors: List[Dict[str, str]],
) -> Dict[str, Any]:
    if not self_check_path:
        return {"status": "SKIPPED", "reason": "self_check report 未传入"}
    if not self_check_path.exists():
        return {"status": "SKIPPED", "reason": f"self_check report 不存在: {self_check_path}"}
    with tempfile.TemporaryDirectory(prefix="ai-radar-preflight-package-") as tmp:
        publish_path = publish_state_path if publish_state_path and publish_state_path.exists() else Path(tmp) / "publish-state.json"
        run_path = run_state_path if run_state_path and run_state_path.exists() else Path(tmp) / "run-state.json"
        output_path = Path(tmp) / "package.json"
        if not publish_path.exists():
            publish_path.write_text(json.dumps({"status": "SELF_CHECKED", "revision_version": 1, "revision_reason": "PREFLIGHT_DRY_RUN"}, ensure_ascii=False), encoding="utf-8")
        if not run_path.exists():
            run_path.write_text(json.dumps({"issue_id": f"AI-RADAR-{report_date_value}", "run_status": "PREFLIGHT_DRY_RUN"}, ensure_ascii=False), encoding="utf-8")
        args = SimpleNamespace(
            md_path=str(md_path),
            search_log=str(search_log_path),
            semantic_review=str(semantic_path),
            self_check_report=str(self_check_path),
            publish_state=str(publish_path),
            run_state=str(run_path),
            issue_id=f"AI-RADAR-{report_date_value}",
            report_date=report_date_value,
            timezone=timezone,
            output=str(output_path),
        )
        try:
            package = build_package(args)
        except Exception as exc:
            add_error(errors, "PACKAGE_DRY_RUN_FAILED", f"知识包发布前 dry-run 失败：{exc}")
            return {"status": "FAIL", "error": str(exc)}
    return {
        "status": "PASS",
        "counts": {name: len(package[name]) for name in ("issues", "events", "sources", "judgments", "runs")},
    }


def run_preflight(md_path: Path, semantic_path: Path, search_log_path: Path, report_date_value: str, timezone: str = "Asia/Shanghai", self_check_path: Optional[Path] = None, publish_state_path: Optional[Path] = None, run_state_path: Optional[Path] = None, package_dry_run: bool = False) -> Dict[str, Any]:
    errors: List[Dict[str, str]] = []
    try:
        report_date = parse_report_date(report_date_value, timezone)
        md_raw = read_text_required(md_path, "日报 Markdown")
        semantic = read_json_required(semantic_path, "semantic_review")
        search_log = read_json_required(search_log_path, "search_log")
        if layout_v5.is_v5(md_raw):
            # V5：结构门禁 + 跟踪/雷达与检索清单绑定，失败即 preflight FAIL；之后按 V4 适配结构继续
            v5_checks = layout_v5.check_structure(md_raw, report_date, WEAK_SOURCE_PATTERNS)
            v5_checks += layout_v5.check_against_search_log(md_raw, search_log)
            for ok, message in v5_checks:
                if not ok:
                    add_error(errors, "V5_LAYOUT", message)
            md_raw = layout_v5.to_v4(md_raw)
        events = parse_events(md_raw, report_date)
    except Exception as exc:
        add_error(errors, "PREFLIGHT_INPUT_INVALID", str(exc))
        events = []
        semantic = {}
        search_log = {}
        try:
            report_date = parse_report_date(report_date_value, timezone)
        except Exception:
            report_date = date.today()

    package_dry_run_result: Dict[str, Any] = {"status": "SKIPPED", "reason": "未启用"}
    if search_log:
        check_official_sources(search_log, errors)
        check_candidate_schema(search_log, errors)
    if events:
        check_tracking_format(events, report_date, errors, search_log)
        check_format_lint(md_raw, errors)
    if events and semantic:
        check_semantic_bindings(events, semantic, errors)
    if events and search_log:
        check_search_bindings(events, search_log, errors)
    if package_dry_run:
        package_dry_run_result = check_package_dry_run(
            md_path,
            semantic_path,
            search_log_path,
            self_check_path,
            publish_state_path,
            run_state_path,
            report_date_value,
            timezone,
            errors,
        )

    return {
        "status": "PASS" if not errors else "FAIL",
        "report_date": report_date.isoformat(),
        "event_count": len(events),
        "error_count": len(errors),
        "errors": errors,
        "package_dry_run": package_dry_run_result,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI 雷达日报发布前总装预检")
    parser.add_argument("md_path", help="日报 Markdown")
    parser.add_argument("--semantic-review", required=True)
    parser.add_argument("--search-log", required=True)
    parser.add_argument("--self-check-report", help="用于发布前 knowledge package dry-run 的自查报告")
    parser.add_argument("--publish-state", help="可选：真实发布状态；不存在时 dry-run 使用 SELF_CHECKED 占位")
    parser.add_argument("--run-state", help="可选：真实运行状态；不存在时 dry-run 使用占位")
    parser.add_argument("--package-dry-run", action="store_true", help="执行不写盘的 knowledge package 绑定预检")
    parser.add_argument("--report-date", required=True)
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--report", help="可选：写出 JSON 预检报告")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    result = run_preflight(
        Path(args.md_path),
        Path(args.semantic_review),
        Path(args.search_log),
        args.report_date,
        args.timezone,
        self_check_path=Path(args.self_check_report) if args.self_check_report else None,
        publish_state_path=Path(args.publish_state) if args.publish_state else None,
        run_state_path=Path(args.run_state) if args.run_state else None,
        package_dry_run=args.package_dry_run,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
