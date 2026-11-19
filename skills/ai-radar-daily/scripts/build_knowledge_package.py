#!/usr/bin/env python3
"""将 AI 雷达日报及其证据文件构建为纯本地、可同步的知识包。"""

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import layout_v5  # V5 三层版式适配
import title_spec  # V5.3.2 标题规范单一真源

SCHEMA_VERSION = "1.0.0"
RULE_VERSION = "V4.1"  # 单一准绳：必须与 scripts/self_check.py 的 RULESET_VERSION、scripts/publish.py 的 RULESET_VERSION 三处一致
LENSES = {"价值层移动", "部署边界", "能力阈值", "业务距离"}
CLAIM_TYPES = {"date", "actor", "action", "number", "specification", "status", "quote"}
MATERIALITIES = {"critical", "supporting"}
SOURCE_TIERS = {"T0", "T1", "T2", "T3", "T4"}
SUPPORT_RESULTS = {"full", "partial", "contradicts"}
SECTIONS = ["今日要紧", "金风相关", "工具与做法", "行业速递"]
WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
TRACKING_QUERY_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm", "from"}
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


class PackageError(ValueError):
    pass


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_id(prefix, *parts):
    text = "\x1f".join(str(part).strip() for part in parts)
    return f"{prefix}_{hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]}"


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_url(url):
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise PackageError(f"无效来源 URL：{url}")
    query = urlencode(sorted(
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
    ))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def normalize_title(title):
    # V4.1：先剥离条目标题前的行动标签，保证正文「### 【需行动】标题」能与语义复核、
    # 检索清单里不带标签的标题比对（否则 preflight 绑定与去重会全部失配）。
    value = re.sub(r"^\s*【(?:需行动|可借鉴|了解)】\s*", "", title)
    value = re.sub(r"[（(]\s*跟踪报道\s*[·．.]?\s*\d{1,2}月\d{1,2}日\s*[）)]", "", value)
    value = re.sub(r"[（(]\s*\d{1,2}月\d{1,2}日[^）)]*[）)]", "", value)
    value = re.sub(r"\s+", "", value)
    return re.sub(r"[，。；：、“”‘’\"·—\-!！?？]", "", value).lower()


def read_text_required(path, label):
    try:
        value = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PackageError(f"无法读取{label}：{exc}") from exc
    if not value.strip():
        raise PackageError(f"{label}为空，拒绝构包")
    return value


def read_json_required(path, label):
    raw = read_text_required(path, label)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PackageError(f"{label}不是有效 JSON：{exc}") from exc
    if not isinstance(value, dict) or not value:
        raise PackageError(f"{label}缺少结构化内容，拒绝构包")
    return value


def read_json_optional(path, label):
    return read_json_required(path, label) if path else {}


def parse_report_date(value):
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PackageError(f"--report-date 必须为 YYYY-MM-DD：{value}") from exc


def validate_dates(report_date, md_path, md_raw, inputs):
    expected = report_date.isoformat()
    filename_match = re.search(r"(\d{4}-\d{2}-\d{2})", Path(md_path).name)
    if filename_match and filename_match.group(1) != expected:
        raise PackageError(f"日报文件名日期 {filename_match.group(1)} 与 --report-date {expected} 不一致")
    # V5.3.2：标题日期校验统一走 title_spec（旧正则对新格式必然失配→日期校验被静默跳过）
    title_text = title_spec.extract_h1(md_raw)
    if title_text:
        parsed = title_spec.parse(title_text)
        if parsed and (int(parsed["month"]), int(parsed["day"])) != (report_date.month, report_date.day):
            raise PackageError("日报标题日期与 --report-date 不一致")
    for label, payload in inputs:
        for key in ("report_date", "date", "issue_date"):
            value = payload.get(key)
            if value and str(value) != expected:
                raise PackageError(f"{label}.{key}={value} 与 --report-date {expected} 不一致")


def section_text(raw, name):
    match = re.search(rf"(?m)^##\s+{re.escape(name)}(?:｜[^\n]+)?\s*$", raw)
    if not match:
        return ""
    rest = raw[match.end():]
    next_match = re.search(r"(?m)^#{1,2}\s+", rest)
    end = next_match.start() if next_match else len(rest)
    # V5.2.2：尾部「集团AI能力地图」由 H1 降为 H3，`^#{1,2}` 不再截断它，
    # 会把「### 技术交流群」等尾部模块吞进最后一个板块（11-03 实测报《技术交流群》事实摘要为空）。
    tail_match = re.search(rf"(?m)^{re.escape(layout_v5.TAIL_START)}\s*$", rest)
    if tail_match:
        end = min(end, tail_match.start())
    return rest[:end]


def split_blocks(section, pattern):
    matches = list(pattern.finditer(section))
    blocks = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        blocks.append((match.group(1).strip(), section[match.end():end].strip()))
    return blocks


def clean_body(block):
    lines = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "---" or re.match(r"^\*{0,2}来源：", stripped):
            continue
        if stripped.startswith("> **信号属性：**") or stripped.startswith("> **案例属性：**"):
            continue
        lines.append(stripped)
    body = "\n".join(lines)
    body = re.sub(r"^一句话解读：", "", body).strip()
    return body


def title_event_date(title, report_date):
    matches = list(re.finditer(r"(\d{1,2})月(\d{1,2})日", title))
    if not matches:
        return None
    month, day = map(int, matches[-1].groups())
    try:
        candidate = date(report_date.year, month, day)
        if candidate > report_date:
            candidate = date(report_date.year - 1, month, day)
        return candidate.isoformat()
    except ValueError as exc:
        raise PackageError(f"事件标题包含无效日期：{title}") from exc


def event_type(section, title):
    if section == "今日要紧":
        return "新闻事件"
    if section == "金风相关":
        return "产业事件" if re.search(r"\d{1,2}月\d{1,2}日", title) else "业务知识卡"
    return {"工具与做法": "工具做法", "行业速递": "行业速递", "今日要紧": "今日要紧", "金风相关": "金风相关", "Builder信号": "Builder信号", "AI工作实践": "工作实践", "深度聚焦": "深度主题"}.get(section, section)


def parse_events(md_raw, report_date):
    parsed = []
    compact = re.compile(r"(?m)^🔹\s+\*\*(.+?)\*\*\s*$")
    heading = re.compile(r"(?m)^###\s+(?!📲)(?!技术交流群\s*$)(?!集团AI能力地图\s*$)(.+?)\s*$")
    for section in SECTIONS:
        body = section_text(md_raw, section)
        pattern = compact if section == "今日大事件" else heading
        for title, block in split_blocks(body, pattern):
            urls = [(name.strip(), url.strip()) for name, url in LINK_RE.findall(block)
                    if "aiplatform.goldwind" not in url and "keconsole.goldwind" not in url]
            summary = clean_body(block)
            if not summary:
                raise PackageError(f"《{title}》事实摘要为空")
            lens_match = re.search(r"【(价值层移动|部署边界|能力阈值|业务距离)】", summary)
            lens = lens_match.group(1) if lens_match else "N/A"
            summary = re.sub(r"^【(?:价值层移动|部署边界|能力阈值|业务距离)】", "", summary).strip()
            parsed.append({
                "section_type": section,
                "event_type": event_type(section, title),
                "title": title,
                "normalized_title": normalize_title(title),
                "event_date": title_event_date(title, report_date),
                "lens": lens,
                "fact_summary": summary,
                "links": urls,
            })
    if not parsed:
        raise PackageError("日报未解析出任何结构化事件")
    return parsed


def evidence_candidates(search_log):
    candidates = search_log.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise PackageError("search_log.candidates 为空，缺少检索证据")
    return [item for item in candidates if isinstance(item, dict)]


def semantic_records(semantic):
    items = semantic.get("items")
    deep = semantic.get("deep_focus")
    if not isinstance(items, list) or not items:
        raise PackageError("semantic_review.items 为空，缺少语义复核证据")
    records = [item for item in items if isinstance(item, dict)]
    if isinstance(deep, dict) and deep:
        copy = dict(deep)
        copy.setdefault("title", deep.get("title", "深度聚焦"))
        copy["_deep_focus"] = True
        records.append(copy)
    return records


def find_by_title(records, title):
    key = normalize_title(title)
    for item in records:
        item_title = str(item.get("title", ""))
        if normalize_title(item_title) == key:
            return item
    return None


def find_candidate(candidates, event):
    match = find_by_title(candidates, event["title"])
    if match:
        return match
    event_urls = {normalize_url(url) for _, url in event["links"]}
    for item in candidates:
        url = item.get("source_url")
        if url and normalize_url(str(url)) in event_urls:
            return item
    return {}


def candidate_event_metadata(candidates, event):
    return find_by_title(candidates, event["title"]) or {}


def find_source_candidate(candidates, normalized_url):
    for item in candidates:
        url = item.get("source_url")
        if url and normalize_url(str(url)) == normalized_url:
            return item
    return {}


def semantic_for_event(records, event):
    match = find_by_title(records, event["title"])
    if match:
        return match
    if event["section_type"] == "深度聚焦":
        return next((item for item in records if item.get("_deep_focus")), None)
    return None


def source_level(candidate, url):
    explicit = candidate.get("source_level") or candidate.get("level") or candidate.get("tier")
    if explicit:
        value = str(explicit).upper()
        return value if value in {"T0", "T1", "T2", "T3", "T4"} else "UNKNOWN"
    return "UNKNOWN"


def list_value(value):
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def validate_semantic_gate(semantic):
    if semantic.get("overall") != "PASS":
        raise PackageError("semantic_review.overall 非 PASS，拒绝构包")
    if semantic.get("recent_dedup_checked") is not True:
        raise PackageError("semantic_review.recent_dedup_checked 必须为 true，拒绝构包")


def validate_evidence_receipts(review, event):
    receipts = review.get("evidence_receipts")
    if not isinstance(receipts, list) or not receipts:
        raise PackageError(f"《{event['title']}》缺少符合 V3.2 的 evidence_receipts")
    body_urls = {normalize_url(url) for _, url in event["links"]}
    source_index = {}
    for index, receipt in enumerate(receipts, start=1):
        owner = f"《{event['title']}》evidence_receipts[{index}]"
        if not isinstance(receipt, dict):
            raise PackageError(f"{owner} 必须为 object")
        required = ("claim_id", "claim_text", "claim_type", "materiality", "sources", "verification")
        if any(not receipt.get(field) for field in required):
            raise PackageError(f"{owner} 缺少 V3.2 必填字段")
        if receipt["claim_type"] not in CLAIM_TYPES or receipt["materiality"] not in MATERIALITIES:
            raise PackageError(f"{owner} 的 claim_type 或 materiality 无效")
        sources = receipt["sources"]
        verification = receipt["verification"]
        if not isinstance(sources, list) or not sources or not isinstance(verification, dict):
            raise PackageError(f"{owner} 的 sources 或 verification 结构无效")
        if verification.get("status") != "PASS":
            raise PackageError(f"{owner}.verification.status 非 PASS，拒绝构包")
        for source_number, source in enumerate(sources, start=1):
            source_owner = f"{owner}.sources[{source_number}]"
            source_required = ("tier", "url", "locator", "verbatim", "supports")
            if not isinstance(source, dict) or any(source.get(field) in (None, "") for field in source_required):
                raise PackageError(f"{source_owner} 缺少 V3.2 必填字段")
            if source["tier"] not in SOURCE_TIERS or source["supports"] not in SUPPORT_RESULTS:
                raise PackageError(f"{source_owner} 的 tier 或 supports 无效")
            normalized = normalize_url(str(source["url"]))
            if normalized not in body_urls:
                raise PackageError(f"{source_owner}.url 不属于正文来源")
            source_index.setdefault(normalized, source)
    return source_index


def parse_self_check(report_raw, md_sha, report_date):
    status = re.search(r"(?m)^SELF_CHECK_STATUS:\s*(PASS|FAIL)\s*$", report_raw)
    declared_date = re.search(r"(?m)^REPORT_DATE:\s*(\d{4}-\d{2}-\d{2})\s*$", report_raw)
    ruleset = re.search(r"(?m)^RULESET_VERSION:\s*(\S+)\s*$", report_raw)
    source_sha = re.search(r"(?m)^SOURCE_SHA256:\s*([0-9a-fA-F]{64})\s*$", report_raw)
    if not status or status.group(1) != "PASS":
        raise PackageError("self_check report 非 PASS，拒绝构包")
    if not declared_date or declared_date.group(1) != report_date.isoformat():
        raise PackageError("self_check report 的 REPORT_DATE 与 --report-date 不一致")
    if not ruleset or ruleset.group(1) != RULE_VERSION:
        raise PackageError(f"self_check report 的 RULESET_VERSION 必须为 {RULE_VERSION}")
    if not source_sha:
        raise PackageError("self_check report 缺少 SOURCE_SHA256")
    declared = source_sha.group(1).lower()
    if declared != md_sha:
        raise PackageError("self_check report 的 SOURCE_SHA256 与日报不一致")
    return declared


def parse_judgment_paragraphs(md_raw):
    values = []
    for label, kind in (("编辑部排序", "EDITOR_RANKING"), ("当日论点", "DAILY_THESIS")):
        match = re.search(rf"(?m)^\*\*{label}\*\*｜【编辑判断】(.+)$", md_raw)
        if match:
            values.append((kind, match.group(1).strip()))
    return values


def parse_followups(md_raw):
    body = section_text(md_raw, "后续关注")
    rows = []
    for line in body.splitlines():
        if not line.strip().startswith("|") or re.match(r"^\s*\|?\s*-", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4 or cells[0] == "关注项":
            continue
        rows.append(cells)
    return rows


def build_package(args):
    report_date = parse_report_date(args.report_date)
    try:
        timezone = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError as exc:
        raise PackageError(f"未知时区：{args.timezone}") from exc

    md_raw = read_text_required(args.md_path, "日报 Markdown")
    if layout_v5.is_v5(md_raw):
        # V5：知识包 Events 只收精编层；跟踪/雷达候选经 sync_candidates_base 进候选表留档
        md_raw = layout_v5.to_v4(md_raw)
    search_log = read_json_required(args.search_log, "search_log")
    semantic = read_json_required(args.semantic_review, "semantic_review")
    self_check_raw = read_text_required(args.self_check_report, "self_check report")
    publish_state = read_json_optional(args.publish_state, "publish_state")
    run_state = read_json_optional(args.run_state, "run_state")
    validate_dates(report_date, args.md_path, md_raw, [
        ("search_log", search_log), ("semantic_review", semantic),
        ("publish_state", publish_state), ("run_state", run_state),
    ])

    md_sha = file_sha256(args.md_path)
    validate_semantic_gate(semantic)
    self_check_sha = parse_self_check(self_check_raw, md_sha, report_date)
    candidates = evidence_candidates(search_log)
    reviews = semantic_records(semantic)
    parsed_events = parse_events(md_raw, report_date)

    source_map = {}
    events = []
    review_bindings = []
    for parsed in parsed_events:
        if not parsed["links"]:
            raise PackageError(f"《{parsed['title']}》没有来源 URL，拒绝构包")
        candidate = candidate_event_metadata(candidates, parsed)
        review = semantic_for_event(reviews, parsed)
        if not review:
            raise PackageError(f"《{parsed['title']}》缺少可绑定的语义复核记录")
        if review.get("status") != "PASS":
            raise PackageError(f"《{parsed['title']}》语义复核 status 非 PASS，拒绝构包")
        review_urls = {normalize_url(str(url)) for url in review.get("source_urls", [])}
        body_urls = {normalize_url(url) for _, url in parsed["links"]}
        if review_urls != body_urls:
            raise PackageError(f"《{parsed['title']}》语义复核 source_urls 与正文来源不严格一致")
        receipt_sources = validate_evidence_receipts(review, parsed)

        event_date = parsed["event_date"]
        event_id = stable_id("evt", event_date or parsed["section_type"], parsed["normalized_title"])
        source_ids = []
        for name, raw_url in parsed["links"]:
            normalized = normalize_url(raw_url)
            source_candidate = find_source_candidate(candidates, normalized)
            receipt_source = receipt_sources.get(normalized, {})
            source_id = stable_id("src", normalized)
            source_ids.append(source_id)
            excerpt_map = review.get("source_excerpts", {})
            excerpt = ""
            if isinstance(excerpt_map, dict):
                excerpt = str(excerpt_map.get(raw_url) or excerpt_map.get(normalized) or "")
            excerpt = excerpt or str(source_candidate.get("source_excerpt") or source_candidate.get("excerpt") or source_candidate.get("snippet") or receipt_source.get("verbatim") or "")
            row = source_map.setdefault(source_id, {
                "source_id": source_id,
                "issue_id": args.issue_id,
                "event_ids": [],
                "source_url": raw_url,
                "normalized_url": normalized,
                "source_name": source_candidate.get("source_name") or receipt_source.get("publisher") or "",
                "source_level": source_level(source_candidate, normalized) if source_candidate else str(receipt_source.get("tier") or "UNKNOWN"),
                "source_excerpt": excerpt,
                # published_at/retrieved_at 仅采信检索清单中精确 URL 匹配的 candidate；
                # receipt 只提供 tier/publisher/verbatim，避免跨来源继承时间元数据。
                "published_at": source_candidate.get("published_at") or source_candidate.get("source_date"),
                "language": source_candidate.get("language", ""),
                "semantic_review_bound": False,
                "retrieved_at": source_candidate.get("retrieved_at"),
                "content_sha256": source_candidate.get("content_sha256", ""),
                "status": "ACTIVE",
            })
            if event_id not in row["event_ids"]:
                row["event_ids"].append(event_id)
            row["semantic_review_bound"] = row["semantic_review_bound"] or normalized in review_urls

        if review is not None and not review_urls.issubset({source_map[source_id]["normalized_url"] for source_id in source_ids}):
            raise PackageError(f"《{parsed['title']}》语义复核 URL 与日报来源不一致")
        if review is not None and not all(source_map[source_id]["semantic_review_bound"] for source_id in source_ids):
            raise PackageError(f"《{parsed['title']}》存在未被语义复核绑定的日报来源")

        importance = candidate.get("importance") or candidate.get("priority")
        confidence = (review or {}).get("confidence", candidate.get("confidence"))
        event = {
            "event_id": event_id,
            "issue_id": args.issue_id,
            "first_seen_issue_id": candidate.get("first_seen_issue_id", args.issue_id),
            "report_date": report_date.isoformat(),
            "section_type": parsed["section_type"],
            "event_type": candidate.get("event_type") or parsed["event_type"],
            "title": parsed["title"],
            "normalized_title": parsed["normalized_title"],
            "subjects": list_value(candidate.get("subjects") or candidate.get("subject")),
            "regions": list_value(candidate.get("regions") or candidate.get("region")),
            "industries": list_value(candidate.get("industries") or candidate.get("industry")),
            "lens": parsed["lens"],
            "importance": importance,
            "confidence": confidence,
            "fact_summary": parsed["fact_summary"],
            "editorial_judgment": candidate.get("editorial_judgment", ""),
            "event_date": event_date or candidate.get("event_date"),
            "source_ids": sorted(set(source_ids)),
            "semantic_review_status": (review or {}).get("status", "NOT_APPLICABLE"),
            "semantic_review_facts": list_value((review or {}).get("facts_checked")),
            "change_type": candidate.get("change_type", "NEW"),
            "revision_version": int(publish_state.get("revision_version", 1)),
            "revision_reason": publish_state.get("revision_reason", "INITIAL"),
        }
        events.append(event)
        if review is not None:
            review_bindings.append((event, review))

    judgments = []
    revision_version = int(publish_state.get("revision_version", 1))
    revision_reason = publish_state.get("revision_reason", "INITIAL")
    for kind, statement in parse_judgment_paragraphs(md_raw):
        judgments.append({
            "judgment_id": stable_id("jdg", args.issue_id, kind, statement),
            "issue_id": args.issue_id, "event_id": None, "source_ids": [],
            "judgment_type": kind, "lens": "N/A", "statement": statement,
            "status": "PASS", "confidence": None, "proposed_date": None,
            "review_result": "", "facts_checked": [], "reviewed_at": None,
            "rule_version": RULE_VERSION,
            "revision_version": revision_version, "revision_reason": revision_reason,
        })
    for event, review in review_bindings:
        statement = f"《{event['title']}》语义复核：{review.get('status', 'UNKNOWN')}"
        judgments.append({
            "judgment_id": stable_id("jdg", args.issue_id, "SEMANTIC_REVIEW", event["event_id"]),
            "issue_id": args.issue_id, "event_id": event["event_id"],
            "source_ids": event["source_ids"], "judgment_type": "SEMANTIC_REVIEW",
            "lens": event["lens"], "statement": statement,
            "status": review.get("status", "FAIL"), "confidence": review.get("confidence"),
            "proposed_date": None, "review_result": review.get("notes", ""),
            "facts_checked": list_value(review.get("facts_checked")),
            "reviewed_at": review.get("reviewed_at"),
            "rule_version": RULE_VERSION,
            "revision_version": revision_version, "revision_reason": revision_reason,
        })
    status_map = {"跟踪中": "TRACKING", "已命中": "HIT", "未到期": "NOT_DUE", "落空": "MISSED"}
    for statement, proposed, status, result in parse_followups(md_raw):
        judgments.append({
            "judgment_id": stable_id("jdg", "FOLLOW_UP", normalize_title(statement), proposed),
            "issue_id": args.issue_id, "event_id": None, "source_ids": [],
            "judgment_type": "FOLLOW_UP", "lens": "N/A", "statement": statement,
            "status": status_map.get(status, status), "confidence": None,
            "proposed_date": proposed, "review_result": result, "facts_checked": [],
            "reviewed_at": None, "rule_version": RULE_VERSION,
            "revision_version": revision_version, "revision_reason": revision_reason,
        })

    generated_at = datetime.now(timezone).isoformat(timespec="seconds")
    title_match = re.search(r"(?m)^#\s*(.+)$", md_raw)
    intro_match = re.search(r"(?m)^>\s*导语：(.+)$", md_raw)
    publish_status = publish_state.get("publish_status") or publish_state.get("status") or "SELF_CHECKED"
    # Base 单选合法选项：DRAFT/SELF_CHECKED/PUBLISHED/REVISED/FAILED；publish.py 的 "OK" 需映射为 PUBLISHED
    _publish_status_map = {"OK": "PUBLISHED", "POST_PUBLISHED": "PUBLISHED", "EXISTS": "PUBLISHED"}
    publish_status = _publish_status_map.get(str(publish_status).upper(), publish_status)
    issue = {
        "issue_id": args.issue_id,
        "report_date": report_date.isoformat(), "weekday": WEEKDAYS[report_date.weekday()],
        "timezone": args.timezone, "search_window": search_log.get("window", ""),
        "title": title_match.group(1).strip() if title_match else f"AI 雷达日报｜{report_date.month}.{report_date.day}",
        "summary": intro_match.group(1).strip() if intro_match else "",
        "publish_status": publish_status,
        "topic_id": publish_state.get("topic_id"), "post_id": publish_state.get("post_id"),
        "forum_url": publish_state.get("forum_url") or publish_state.get("url"),
        "lark_status": publish_state.get("lark_status") or "SYNC_PENDING",
        "self_check_sha256": self_check_sha,
        "rule_version": RULE_VERSION,
        "revision_version": revision_version, "revision_reason": revision_reason,
        "package_sha256": "", "created_at": publish_state.get("created_at", generated_at),
        "updated_at": generated_at,
    }

    sources = sorted(source_map.values(), key=lambda row: row["source_id"])
    events.sort(key=lambda row: (SECTIONS.index(row["section_type"]), row["event_id"]))
    judgments.sort(key=lambda row: row["judgment_id"])
    input_paths = {
        "md": args.md_path, "search_log": args.search_log,
        "semantic_review": args.semantic_review, "self_check_report": args.self_check_report,
        "publish_state": args.publish_state, "run_state": args.run_state,
    }
    input_hashes = {name: file_sha256(path) for name, path in input_paths.items() if path}
    business = {"schema_version": SCHEMA_VERSION, "issues": [issue], "events": events,
                "sources": sources, "judgments": judgments}
    package_sha = hashlib.sha256(canonical_json(business).encode("utf-8")).hexdigest()
    issue["package_sha256"] = package_sha
    run_id = str(run_state.get("run_id") or stable_id("run", args.issue_id, "BUILD_PACKAGE", canonical_json(input_hashes)))
    build_checks = {
        "semantic_overall": semantic.get("overall") == "PASS",
        "recent_dedup_checked": semantic.get("recent_dedup_checked") is True,
        "all_events_reviewed": len(review_bindings) == len(events),
        "all_reviews_passed": all(review.get("status") == "PASS" for _, review in review_bindings),
        "all_sources_bound": all(source["semantic_review_bound"] for source in sources),
        "self_check_sha_matches": self_check_sha == md_sha,
    }
    run_status = "PASS" if all(build_checks.values()) else "FAIL"
    if run_status != "PASS":
        raise PackageError("知识包完整性门禁未全部通过，拒绝构包")
    run = {
        "run_id": run_id, "issue_id": args.issue_id, "run_type": "BUILD_PACKAGE",
        "started_at": run_state.get("started_at"), "finished_at": generated_at,
        "status": run_status, "timezone": args.timezone, "input_sha256": input_hashes,
        "self_check_sha256": self_check_sha, "package_sha256": package_sha,
        "rule_version": issue["rule_version"], "revision_version": revision_version,
        "revision_reason": revision_reason, "sync_status": "SYNC_PENDING",
        "error_summary": "", "counts": {"issues": 1, "events": len(events),
        "sources": len(sources), "judgments": len(judgments), "runs": 1},
    }
    return {"schema_version": SCHEMA_VERSION, "issues": [issue], "events": events,
            "sources": sources, "judgments": judgments, "runs": [run]}


def atomic_write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="构建 AI 雷达日报本地知识包（不联网）")
    parser.add_argument("md_path", help="日报 Markdown")
    parser.add_argument("--search-log", required=True)
    parser.add_argument("--semantic-review", required=True)
    parser.add_argument("--self-check-report", required=True)
    parser.add_argument("--publish-state")
    parser.add_argument("--run-state")
    parser.add_argument("--issue-id", required=True)
    parser.add_argument("--report-date", required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        package = build_package(args)
        atomic_write_json(args.output, package)
    except (PackageError, OSError) as exc:
        print(json.dumps({"status": "BUILD_FAILED", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": "BUILD_OK", "output": str(Path(args.output).resolve()),
        "issue_id": args.issue_id,
        "counts": {name: len(package[name]) for name in ("issues", "events", "sources", "judgments", "runs")},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
