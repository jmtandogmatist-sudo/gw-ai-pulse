#!/usr/bin/env python3
"""校验并发布 AI 雷达日报到 Discourse。"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote_plus

from common import (
    ConfigurationError,
    atomic_write_json,
    emit_status,
    load_discourse_config,
    parse_report_date,
    status_payload,
)

import title_spec  # V5.3.2 标题规范单一真源

RULESET_VERSION = "V4.1"  # 单一准绳：必须与 scripts/self_check.py 的 RULESET_VERSION、scripts/build_knowledge_package.py 的 RULE_VERSION 三处一致
DEFAULT_RUNTIME_CONFIG = Path(__file__).resolve().parents[1] / "config" / "runtime.json"
MAX_RETRIES = 3
RETRY_DELAY = 5

# 技术交流群二维码：固定 5 码，顺序与 references/report-template.md 的 [grid] 块严格一致。
# V3.8.6 修正：此前替换规则硬编码为「AI信息交流群 / 本地大模型 / AI智能体」三码，
# 且尺寸写死 400x408 / 400x428，与 V3.8.4 统一后的 5 码 400x400 结构冲突——
# 后果是发布瞬间把已合规正文改回旧群名+旧尺寸，并导致上传的第 1 张图（qr1）因群名不匹配而丢失。
# 现改为按群名精确映射，模板/门禁/发布脚本三者同源。
QR_GROUP_NAMES = (
    "本地大模型用户交流群",
    "AI智能体开发平台运营群",
    "语义本体构建交流群",
    "KNS统一数据平台",
    "标准化助手交流群",
    "风衍大模型用户交流群",
)
QR_DISPLAY_SIZE = "400x400"
QR_EXPECTED_COUNT = len(QR_GROUP_NAMES)


def apply_qr_replacements(raw, qr_urls):
    """把正文中 5 个技术交流群二维码的图片引用替换为上传后的 URL，并强制显示尺寸 400x400。

    qr_urls: [(群名, 论坛图片 URL), ...]，群名须取自 QR_GROUP_NAMES。
    容忍原 alt 已带尺寸标注（形如 `群名|WxH`）或未带（仅 `群名`），一律归一为 400x400。
    若某群名在正文中找不到引用，抛 PublishError——宁可阻断也不静默发布结构不符的正文。
    """
    for name, url in qr_urls:
        pattern = rf"!\[{re.escape(name)}(?:\|\s*\d+\s*[x×]\s*\d+)?\]\([^)]*\)"
        replacement = f"![{name}|{QR_DISPLAY_SIZE}]({url})"
        raw = re.sub(pattern, replacement, raw)
    missing = [name for name, _ in qr_urls if f"![{name}|{QR_DISPLAY_SIZE}](" not in raw]
    if missing:
        raise PublishError(f"正文缺少以下群的二维码引用（群名与模板不一致）：{'、'.join(missing)}")
    return raw


def merge_state_file(state_path, result):
    """合并写入状态文件：保留既有字段（topic_id/url/title/created_at/reconciliation 等），
    仅用本次结果覆盖对应键。修复 2026-09-07 问题：更新模式下整文件被覆写为存根，
    导致后续 build_knowledge_package 缺上下文、需人工重建 publish-state。"""
    state_file = Path(state_path)
    if not state_file.exists():
        return result
    try:
        existing = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            return result
    except (OSError, ValueError):
        return result
    merged = dict(existing)
    merged.update(result)
    if "created_at" not in result and "created_at" in existing:
        merged["created_at"] = existing["created_at"]
    if "mode" not in result and "mode" in existing:
        merged["mode"] = existing["mode"]
    return merged


class PublishError(RuntimeError):
    """发布流程被阻断或执行失败。"""


class ReconciliationRequired(RuntimeError):
    """写操作结果不确定，必须回读或人工核对。"""


def title_for_date(report_date):
    return f"AI 雷达日报｜{report_date.month}.{report_date.day}"


def report_date_from_path(path):
    match = re.search(r"AI雷达日报_(\d{4}-\d{2}-\d{2})\.md$", Path(path).name)
    return match.group(1) if match else None


def markdown_h1(raw):
    # V5.3.2：H1 解析统一走 title_spec（新格式含钩子，旧格式兼容）
    return title_spec.extract_h1(raw)


def file_sha256(file_path):
    digest = hashlib.sha256()
    with open(file_path, "rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_self_check(md_path, report_path, report_date=None):
    if not os.path.isfile(report_path):
        return False, "自查报告不存在"
    report = Path(report_path).read_text(encoding="utf-8")
    status_match = re.search(r"^SELF_CHECK_STATUS:\s*(PASS|FAIL)\s*$", report, re.M)
    hash_match = re.search(r"^SOURCE_SHA256:\s*([0-9a-f]{64})\s*$", report, re.M)
    report_date_match = re.search(r"^REPORT_DATE:\s*(\d{4}-\d{2}-\d{2})\s*$", report, re.M)
    ruleset_match = re.search(r"^RULESET_VERSION:\s*(\S+)\s*$", report, re.M)
    if not status_match or status_match.group(1) != "PASS":
        return False, "自查未通过或报告格式无效"
    if not hash_match or hash_match.group(1) != file_sha256(md_path):
        return False, "日报正文哈希与自查报告不一致"
    if not report_date_match:
        return False, "自查报告缺少 REPORT_DATE"
    if not report_date or report_date_match.group(1) != report_date.isoformat():
        return False, "自查报告日期与 report_date 不一致"
    if not ruleset_match or ruleset_match.group(1) != RULESET_VERSION:
        return False, f"自查报告 RULESET_VERSION 必须为 {RULESET_VERSION}"
    return True, "自查通过且日期、规则版本、正文哈希一致"


def curl_json(url, method="GET", headers=None, data_file=None, form_fields=None):
    method = method.upper()
    cmd = ["curl", "-sS", "-w", "\n%{http_code}"]
    if method != "GET":
        cmd += ["-X", method]
    for name, value in (headers or {}).items():
        cmd += ["-H", f"{name}: {value}"]
    if data_file:
        cmd += ["--data-binary", f"@{data_file}"]
    for name, value in form_fields or []:
        cmd += ["-F", f"{name}={value}"]
    cmd.append(url)

    attempts = MAX_RETRIES if method == "GET" else 1
    for attempt in range(attempts):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None:
            body, separator, status = result.stdout.rpartition("\n")
            if separator and status.startswith("2"):
                try:
                    payload = json.loads(body)
                    if isinstance(payload, dict):
                        return payload
                except json.JSONDecodeError:
                    pass
        if attempt + 1 < attempts:
            time.sleep(RETRY_DELAY)
    return None


def load_publish_settings(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"运行配置无法读取：{path}") from exc
    forum = payload.get("forum") if isinstance(payload, dict) else None
    if not isinstance(forum, dict):
        raise ConfigurationError("运行配置缺少 forum")
    category = forum.get("category_id")
    tags = forum.get("tags")
    if not isinstance(category, int) or category <= 0:
        raise ConfigurationError("运行配置 forum.category_id 无效")
    if not isinstance(tags, list) or not tags or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        raise ConfigurationError("运行配置 forum.tags 无效")
    return category, [tag.strip() for tag in tags]


def auth_headers(config, json_content=False):
    headers = {"Api-Key": config["api_key"], "Api-Username": config["api_user"]}
    if json_content:
        headers["Content-Type"] = "application/json"
    return headers


def check_api_key(config):
    data = curl_json(f"{config['url']}/session/current.json", headers=auth_headers(config))
    return bool(data and data.get("current_user"))


def check_existing_post(title, config):
    """返回 (搜索成功, 已存在链接)。搜索失败必须由调用方阻断。"""
    data = curl_json(
        f"{config['url']}/search.json?q={quote_plus(title)}",
        headers=auth_headers(config),
    )
    if data is None:
        return False, None
    topics = data.get("topics")
    if not isinstance(topics, list):
        # 二开站点在零结果时省略 topics 键；HTTP 2xx 且含 posts/grouped_search_result 的
        # 响应是真实搜索成功，按零匹配处理，不得误判为搜索失败而阻断发布
        if isinstance(data.get("posts"), list) or isinstance(data.get("grouped_search_result"), dict):
            topics = []
        else:
            return False, None
    for topic in topics:
        if str(topic.get("title", "")).strip() == title.strip():
            return True, f"{config['url']}/t/{topic.get('slug')}/{topic.get('id')}"
    return True, None


def upload_image(file_path, config):
    data = curl_json(
        f"{config['url']}/uploads.json",
        method="POST",
        headers=auth_headers(config),
        form_fields=[("type", "composer"), ("files[]", f"@{file_path}")],
    )
    if data is None:
        raise ReconciliationRequired("图片上传响应不确定；未自动重试，请核对论坛上传状态")
    if "errors" in data or "error_type" in data or not data.get("short_url"):
        raise PublishError("图片上传失败")
    return data["short_url"]


def build_and_publish(md_path, cover_path, qr1_path, qr2_path, qr3_path, self_check_report,
                      dry_run=False, update_post_id=None, topic_id=None, report_date=None,
                      timezone="Asia/Shanghai", config=None, category=None, tags=None,
                      qr4_path=None, qr5_path=None, qr6_path=None):
    resolved_date = parse_report_date(report_date, timezone, default=report_date_from_path(md_path))
    markdown = Path(md_path).read_text(encoding="utf-8")
    # V5.3.2：标题以正文 H1 为唯一真源（含钩子），不再由 report_date 拼装；
    # 这里只校验「格式合规 + H1 日期与 report_date 一致」，避免标题与正文脱钩。
    title = markdown_h1(markdown)
    if not title:
        raise PublishError("Markdown 缺少合法院日报 H1（应为「{钩子} |AI雷达M.D」或历史格式「AI 雷达日报｜M.D」）")
    title_problems = title_spec.validate_title(title)
    if title_problems:
        raise PublishError("日报标题不合规：" + "；".join(title_problems))
    if title_spec.h1_date(markdown, resolved_date) != resolved_date:
        raise PublishError("正文 H1 日期与 report_date 不一致")
    valid, reason = validate_self_check(md_path, self_check_report, resolved_date)
    if not valid:
        raise PublishError(reason)
    if category is None or tags is None:
        raise PublishError("缺少发布分类或标签配置")
    if dry_run:
        return status_payload("OK", "DRY_RUN_OK", "自查、标题与运行配置校验通过，未调用论坛 API", report_date=resolved_date.isoformat())
    if config is None:
        config = load_discourse_config()
    if not check_api_key(config):
        raise PublishError("论坛凭据无效或网络不可用")

    if not update_post_id:
        search_ok, existing = check_existing_post(title, config)
        if not search_ok:
            raise PublishError("重复帖子搜索失败，发布已阻断")
        if existing:
            return status_payload("EXISTS", "POST_ALREADY_EXISTS", "同标题日报已存在", report_date=resolved_date.isoformat(), url=existing)

    raw = re.sub(r"^# AI 雷达日报[^\n]*\n", "", markdown, count=1, flags=re.M)  # V5：H1 位于 [wrap=air-daily] 之后
    if cover_path != "-":
        cover_url = upload_image(cover_path, config)
        if not cover_url:
            raise PublishError("封面图上传失败")
        raw = raw.replace(
            f"![AI雷达日报封面]({os.path.basename(cover_path)})",
            f"![AI雷达日报封面|1024x683]({cover_url})",
        )
        if cover_url not in raw:
            raw = re.sub(r"!\[AI雷达日报封面\|?\d*x\d*\]\([^)]+\)", f"![AI雷达日报封面|1024x683]({cover_url})", raw)

    # 二维码替换 —— gw-ai-pulse（2026-11-13）起默认关闭：
    # 用户决定日报正文不再挂 6 张技术交流群二维码（飞书推送同步关闭）。
    # 行为分三种：
    #   ① 正文无群码、未传 qr 参数 → 跳过（标准形态，不报错）
    #   ② 正文有群码但未传齐 qr 参数 → PublishError（不静默发出结构不符的正文）
    #   ③ 传齐 6 张 qr 参数 → 仍走原逻辑（保留能力，便于将来恢复）
    qr_paths_in_order = [qr1_path, qr2_path, qr3_path, qr4_path, qr5_path, qr6_path]
    provided = [(name, path) for name, path in zip(QR_GROUP_NAMES, qr_paths_in_order) if path]
    body_has_qr = any(
        re.search(rf"!\[{re.escape(name)}(?:\|\s*\d+\s*[x×]\s*\d+)?\]", raw) for name in QR_GROUP_NAMES
    )
    if provided:
        if len(provided) != QR_EXPECTED_COUNT:
            raise PublishError(
                f"二维码参数不足：需 {QR_EXPECTED_COUNT} 张（{ '、'.join(QR_GROUP_NAMES) }），实际提供 {len(provided)} 张"
            )
        qr_urls = []
        for name, path in provided:
            url = upload_image(path, config)
            if not url:
                raise PublishError(f"二维码上传失败：{name}（{path}）")
            qr_urls.append((name, url))
        raw = apply_qr_replacements(raw, qr_urls)
    elif body_has_qr:
        raise PublishError(
            f"正文含技术交流群二维码但未提供 qr 参数：{ '、'.join(QR_GROUP_NAMES) }"
            "（要么补齐 6 张，要么从正文移除群码）"
        )

    # 泛化处理：把技术交流群等区域中的额外本地图片（非封面、非标准三张二维码）自动上传并替换。
    # 扫描正文所有仍为本地相对路径的图片引用，逐个上传为论坛 URL。
    base_dir = os.path.dirname(os.path.abspath(md_path))
    pending = re.findall(r"!\[([^\]]*)\]\(([^)\s]*\.(?:png|jpe?g|gif|webp))\)", raw)
    for alt, rel in pending:
        if not rel or rel.startswith("http"):
            continue
        local_img = rel if os.path.isabs(rel) else os.path.join(base_dir, rel)
        if not os.path.isfile(local_img):
            continue
        # 已在上一步被替换为标准 URL 的（封面/三张二维码）不再处理
        if rel.startswith("http") or "://" in rel:
            continue
        try:
            img_url = upload_image(local_img, config)
        except Exception:  # 单个图片上传失败不阻断整体，但不再抛错仅记录
            img_url = None
        if not img_url:
            raise PublishError(f"额外图片上传失败：{rel}")
        # 只替换路径为论坛 URL，保留原 alt（可能已含 |400x400 尺寸标注），避免尺寸重复
        raw = re.sub(
            rf"!\[([^\]]*)\]\([^)]*{re.escape(os.path.basename(rel))}[^)]*\)",
            lambda m: f"![{m.group(1)}]({img_url})",
            raw,
        )

    expected_raw = raw.strip()
    payload = {"raw": expected_raw, "category": category, "tags": tags}
    if not update_post_id:
        payload["title"] = title
    fd, payload_path = tempfile.mkstemp(prefix="ai-radar-payload-", suffix=".json", dir=str(Path(md_path).parent))
    os.close(fd)
    try:
        atomic_write_json(payload_path, payload)
        if update_post_id:
            data = curl_json(
                f"{config['url']}/posts/{update_post_id}.json",
                method="PUT",
                headers=auth_headers(config, json_content=True),
                data_file=payload_path,
            )
        else:
            data = curl_json(
                f"{config['url']}/posts.json",
                method="POST",
                headers=auth_headers(config, json_content=True),
                data_file=payload_path,
            )
    finally:
        Path(payload_path).unlink(missing_ok=True)
    if not data or "errors" in data or "error_type" in data:
        raise ReconciliationRequired("写操作响应失败或不确定；未自动重试，请核对论坛状态")

    response_topic_id = data.get("topic_id") or topic_id
    post_id = data.get("id") or update_post_id
    if not response_topic_id:
        raise ReconciliationRequired("写响应缺少 topic_id；请核对论坛状态")
    verification = curl_json(f"{config['url']}/t/{response_topic_id}.json", headers=auth_headers(config))
    if verification is None:
        raise ReconciliationRequired("帖子写入后回读失败；请核对论坛状态")
    remote_title = str(verification.get("title", "")).strip()
    remote_category = verification.get("category_id")
    remote_tags = verification.get("tags")
    if remote_tags and isinstance(remote_tags, list) and remote_tags and isinstance(remote_tags[0], dict):
        remote_tags = [tag.get("name") for tag in remote_tags]
    posts = verification.get("post_stream", {}).get("posts", [])
    first_post = next((post for post in posts if str(post.get("id")) == str(post_id)), posts[0] if posts else {})
    remote_raw = first_post.get("raw")
    if not isinstance(remote_raw, str) and post_id:
        post_verification = curl_json(f"{config['url']}/posts/{post_id}.json", headers=auth_headers(config))
        remote_raw = post_verification.get("raw") if post_verification else None
    verified = (
        remote_title == title
        and remote_category == category
        and isinstance(remote_tags, list)
        and set(remote_tags) == set(tags)
        and isinstance(remote_raw, str)
        and hashlib.sha256(remote_raw.strip().encode("utf-8")).hexdigest()
        == hashlib.sha256(expected_raw.encode("utf-8")).hexdigest()
    )
    if not verified:
        raise ReconciliationRequired("帖子写入后回读字段不足或 title/category/tags/正文不一致；请核对论坛状态")
    slug = verification.get("slug") or data.get("topic_slug") or "topic"
    url = f"{config['url']}/t/{slug}/{response_topic_id}"
    return status_payload(
        "OK",
        "POST_UPDATED" if update_post_id else "POST_PUBLISHED",
        "日报更新并回读校验成功" if update_post_id else "日报发布并回读校验成功",
        report_date=resolved_date.isoformat(),
        topic_id=response_topic_id,
        post_id=post_id,
        url=url,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="发布通过自查的 AI 雷达日报")
    parser.add_argument("md_path")
    parser.add_argument("cover_path")
    parser.add_argument("qr1_path")
    parser.add_argument("qr2_path")
    parser.add_argument("qr3_path")
    parser.add_argument("self_check_report")
    parser.add_argument("--dry-run", action="store_true", help="仅执行本地门禁，不调用论坛 API")
    parser.add_argument("--update-post-id")
    parser.add_argument("--topic-id")
    parser.add_argument("--report-date", help="刊发日期 YYYY-MM-DD")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--env-file")
    parser.add_argument("--config", default=str(DEFAULT_RUNTIME_CONFIG), help="运行配置 JSON")
    parser.add_argument("--state-file")
    parser.add_argument("--qr4", help="第 4 张技术交流群二维码（KNS统一数据平台）")
    parser.add_argument("--qr5", help="第 5 张技术交流群二维码（标准化助手交流群）")
    parser.add_argument("--qr6", help="第 6 张技术交流群二维码（风衍大模型用户交流群）")
    args = parser.parse_args(argv)

    try:
        category, tags = load_publish_settings(args.config)
        config = None if args.dry_run else load_discourse_config(args.env_file)
        result = build_and_publish(
            args.md_path, args.cover_path, args.qr1_path, args.qr2_path, args.qr3_path,
            args.self_check_report, dry_run=args.dry_run, update_post_id=args.update_post_id,
            topic_id=args.topic_id, report_date=args.report_date, timezone=args.timezone, config=config,
            category=category, tags=tags, qr4_path=args.qr4, qr5_path=args.qr5,
            qr6_path=args.qr6,
        )
        if args.state_file:
            atomic_write_json(args.state_file, merge_state_file(args.state_file, result))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "OK" else 3
    except ReconciliationRequired as exc:
        result = emit_status("NEEDS_RECONCILIATION", "WRITE_RESULT_UNCERTAIN", str(exc), stream=sys.stderr)
        if args.state_file:
            atomic_write_json(args.state_file, merge_state_file(args.state_file, result))
        return 4
    except (ConfigurationError, PublishError, ValueError, OSError) as exc:
        result = emit_status("ERROR", "PUBLISH_BLOCKED", str(exc), stream=sys.stderr)
        if args.state_file:
            atomic_write_json(args.state_file, merge_state_file(args.state_file, result))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
