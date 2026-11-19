#!/usr/bin/env python3
"""获取最近 N 期已发日报的标题与来源 URL，供跨期去重。"""

import argparse
import json
import re
import subprocess
import sys
import time
from urllib.parse import quote_plus

import title_spec  # V5.3.2 标题规范单一真源

from common import (
    ConfigurationError,
    atomic_write_json,
    emit_status,
    load_discourse_config,
    parse_report_date,
)


MAX_RETRIES = 3
RETRY_DELAY = 5


class FetchError(RuntimeError):
    """论坛读取失败。"""


def report_title(report_date):
    # V5.3.2：仅作历史格式对照，新期次标题以正文 H1 为准（见 title_spec）
    return title_spec.legacy_title(report_date)


def curl_json(url, headers=None):
    """带鉴权读取；鉴权失效时降级为匿名只读（V5.3.1）。

    Discourse 的读接口（`/search.json`、`/t/<id>.json`）对匿名开放，而 API Key
    被吊销/轮换后所有鉴权请求恒返回 403。此时读取路径降级匿名仍能拿到同一份
    公开数据，避免整个检索通道因凭据过期而全面停摆；**写路径（publish/feed/
    sync）不走这里，仍 fail closed**，故不会掩盖凭据问题。
    """
    try:
        return _curl_json_once(url, headers)
    except FetchError:
        if not headers:
            raise
        print(
            "[fetch_recent] WARN 鉴权读取失败（疑 API Key 失效），降级匿名只读；"
            "发布/首页/同步仍需有效密钥",
            file=sys.stderr,
        )
        return _curl_json_once(url, None)


def _curl_json_once(url, headers=None):
    cmd = ["curl", "-sS", "--fail-with-body", url]
    for name, value in (headers or {}).items():
        cmd += ["-H", f"{name}: {value}"]
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = exc
        else:
            if result.returncode == 0:
                try:
                    payload = json.loads(result.stdout)
                except (json.JSONDecodeError, ValueError) as exc:
                    last_error = exc
                else:
                    if isinstance(payload, dict):
                        return payload
                    last_error = ValueError("论坛响应结构无效")
            else:
                last_error = RuntimeError("论坛请求返回失败状态")
        if attempt + 1 < MAX_RETRIES:
            time.sleep(RETRY_DELAY)
    raise FetchError("论坛 GET 请求重试后仍失败") from last_error


TAIL_MARKERS = ("### 集团AI能力地图", "# 集团AI能力地图", "## 集团AI能力地图")
# 不参与去重的板块：社区今日（站内帖可重复推荐）、今日一问（无来源）
SKIP_SECTIONS = ("社区今日", "今日一问")
LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
V4_HEADING_RE = re.compile(r"^###\s+(?:【[^】]+】\s*)?(.+?)\s*$", re.M)
V4_DIGEST_RE = re.compile(r"^-\s*(?:🟢|🟡|🔴)?\s*\*\*([^*]+?)[：:]?\*\*", re.M)
V3_TITLE_RE = re.compile(r"🔹\s*\*\*([^*]+)\*\*")
V3_SOURCE_RE = re.compile(r"来源：[^\n]*\[([^\]]*)\]\(([^)]+)\)")


def _strip_tail(raw):
    cut = len(raw)
    for marker in TAIL_MARKERS:
        pos = raw.find(marker)
        if pos >= 0:
            cut = min(cut, pos)
    return raw[:cut]


def _drop_skipped_sections(body):
    parts = re.split(r"(?m)^(?=##\s)", body)
    kept = []
    for part in parts:
        head = part.splitlines()[0] if part else ""
        if head.startswith("## ") and any(name in head for name in SKIP_SECTIONS):
            continue
        kept.append(part)
    return "".join(kept)


def extract_news_items(raw):
    """抽取一期日报的条目标题与来源 URL，供跨期去重。

    V4.3 修复：V4 正文（2026-10-22 起）为 `### 标题` + `[details]` 来源折叠块，
    旧实现只认 V3 的 `🔹 **标题**` 与 `来源：` 行，导致 V4 期次抽出 0 标题 0 URL，
    跨期去重静默失效。现同时兼容 V4 与 V3；尾部固定模块与社区今日/今日一问不参与。
    """
    if "[wrap=air-daily]" in raw:
        # V5 三层版式：精编/跟踪/雷达全部条目都算已发布内容
        import layout_v5
        return layout_v5.all_titles_and_urls(raw)
    body = _drop_skipped_sections(_strip_tail(raw))
    titles = []
    urls = []
    for pattern in (V4_HEADING_RE, V4_DIGEST_RE, V3_TITLE_RE):
        for match in pattern.finditer(body):
            title = match.group(1).strip()
            if title and title not in titles and not title.startswith("技术交流群"):
                titles.append(title)
    for match in LINK_RE.finditer(body):
        url = match.group(2).strip()
        if url not in urls:
            urls.append(url)
    for match in V3_SOURCE_RE.finditer(body):
        url = match.group(2).strip()
        if url.startswith(("http://", "https://")) and url not in urls:
            urls.append(url)
    return titles, urls


def fetch_recent_reports(days=2, report_date=None, timezone="Asia/Shanghai", config=None):
    if days < 1:
        raise ValueError("期数必须大于 0")
    resolved_date = parse_report_date(report_date, timezone)
    if config is None:
        config = load_discourse_config()
    base_url = config["url"]
    headers = {"Api-Key": config["api_key"], "Api-Username": config["api_user"]}
    # V5.3.2：检索词改为「雷达」——新标题是「… |AI雷达11.10」，旧标题是「AI 雷达日报｜11.10」，
    # 只有「雷达」同时命中两代格式；再用 title_spec.is_report_title 严格过滤掉非日报帖。
    query = quote_plus("雷达 order:latest")
    data = curl_json(f"{base_url}/search.json?q={query}", headers)
    topics = data.get("topics")
    if not isinstance(topics, list):
        raise FetchError("论坛搜索响应缺少 topics")

    today_tag = f"{resolved_date.month}.{resolved_date.day}"
    report_topics = [
        topic for topic in topics
        if isinstance(topic, dict)
        and title_spec.is_report_title(str(topic.get("title", "")))
        and title_spec.date_tag(str(topic.get("title", ""))) != today_tag
    ]

    all_titles = []
    all_urls = set()
    fetched = 0
    for topic in report_topics[:days]:
        topic_id = topic.get("id")
        if not topic_id:
            raise FetchError("论坛搜索结果缺少 topic id")
        topic_data = curl_json(f"{base_url}/t/{topic_id}.json", headers)
        posts = topic_data.get("post_stream", {}).get("posts", [])
        if not posts or not posts[0].get("id"):
            raise FetchError(f"主题 {topic_id} 缺少首帖")
        post_data = curl_json(f"{base_url}/posts/{posts[0]['id']}.json", headers)
        raw = post_data.get("raw")
        if not isinstance(raw, str):
            raise FetchError(f"主题 {topic_id} 缺少 Markdown 正文")
        titles, urls = extract_news_items(raw)
        all_titles.extend(titles)
        all_urls.update(urls)
        fetched += 1

    return all_titles, all_urls, fetched


def main(argv=None):
    parser = argparse.ArgumentParser(description="获取近期 AI 雷达日报去重数据")
    parser.add_argument("periods", nargs="?", type=int, default=None, help="最近期数，兼容原位置参数；缺省按报告日期自动推导（执行点增量口径）")
    parser.add_argument("--report-date", help="刊发日期 YYYY-MM-DD")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--env-file")
    parser.add_argument("--output", help="可选的 titles/urls JSON 输出路径")
    args = parser.parse_args(argv)

    try:
        resolved_date = parse_report_date(args.report_date, args.timezone)
        config = load_discourse_config(args.env_file)
        # 执行点增量口径：去重回看期数按星期自适应
        # 周一（跨周末增量，含上周五/六/日）回看 4 期；周二至周五（连续每日增量）回看 2 期
        if args.periods is None:
            periods = 4 if resolved_date.weekday() == 0 else 2
        else:
            periods = args.periods
        titles, urls, fetched = fetch_recent_reports(
            periods,
            report_date=resolved_date,
            timezone=args.timezone,
            config=config,
        )
        output = {
            "report_date": resolved_date.isoformat(),
            "periods_requested": periods,
            "periods_fetched": fetched,
            "titles": titles,
            "urls": sorted(urls),
        }
        if args.output:
            atomic_write_json(args.output, output)
        emit_status(
            "OK",
            "FETCH_RECENT_COMPLETE",
            "近期日报去重数据获取完成",
            report_date=resolved_date.isoformat(),
            periods_fetched=fetched,
            title_count=len(titles),
            url_count=len(urls),
            output=args.output,
        )
        return 0
    except (ConfigurationError, FetchError, ValueError, OSError) as exc:
        emit_status("ERROR", "FETCH_RECENT_FAILED", str(exc), stream=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
