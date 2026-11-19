#!/usr/bin/env python3
"""论坛首页「AI 雷达」数据（5.2.0）：从已通过门禁的 V5 日报正文生成 air-pulse/1 JSON，并更新数据底座帖。

数据流：日报正文（G3 PASS、已发布）→ build_feed() → 校验 → 写本地 _首页数据.json →（人工确认后）
PUT 底座帖第 1 楼 → 回读核对哈希。首页主题组件「AI 雷达首页」只读这一个帖子。

设计要点
- 只从**最终正文**取材，不从候选池取：首页展示的内容与已发布日报逐条一致，跟着同一套门禁走。
- 检索清单只用来补充信号热度（heat）与发现时间，不引入正文里没有的条目。
- 公司网络打不开的链接（config/link-reachability.json 的 blocked）一律剔除；正文本应已被 R19 拦下。
- 幂等：底座帖现有内容哈希相同则不写；若底座帖已是更新一期的数据，拒绝用旧期覆盖（防 revise 旧刊时误覆盖首页）。
- 凭据只经 env 文件读取，不打印。

子命令
  build    正文 → JSON（只写本地，不联网）
  publish  JSON → 底座帖（外部写入，须由 run.py feed --confirm 调用或人工明确授权）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import layout_v5 as L  # noqa: E402
from common import ConfigurationError, atomic_write_json, emit_status, load_discourse_config, parse_timezone  # noqa: E402

SCHEMA = "air-pulse/1"
WEEKDAYS = "一二三四五六日"
HEADER = "> 本帖是「AI 雷达」首页的数据底座，由采集器、每日精选、主题档案、技术雷达四方自动维护，请勿手动编辑。\n\n"
MAX_POST_CHARS = 30000  # Discourse 默认单帖上限 32000，留余量
LIMITS = {"title": 120, "summary": 320, "us": 200, "brief": 160, "pick": 120}
MAX_ITEMS = {"brief": 3, "picks": 4, "curated": 40, "tracking": 20, "radar": 120, "community": 5}
CRED = {"g", "y", "r"}
RUNTIME_PATH = Path(__file__).resolve().parents[1] / "config" / "runtime.json"
REACH_PATH = Path(__file__).resolve().parents[1] / "config" / "link-reachability.json"

MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
# 「社区今日」两种实际写法都要认：模板 `- **场景：** [标题](URL)`，11-02 实刊 `* **【场景】**：[标题](URL)`
COMMUNITY_RE = re.compile(r"^[-*]\s*\*\*(.+?)\*\*\s*[：:]?\s*\[([^\]]+)\]\((https?://[^)\s]+|/[^)\s]*)\)")


def community_who(label: str) -> str:
    return re.sub(r"^[【\[]|[】\]]$", "", label.strip().rstrip("：:").strip())


# ---------------------------------------------------------------- 文本工具

def plain(text: str) -> str:
    """Markdown → 纯文本：链接留文字，去掉 * ` 与多余空白。"""
    text = MD_LINK_RE.sub(lambda m: m.group(1), text or "")
    text = re.sub(r"[*`]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def clip(text: str, limit: int) -> str:
    text = plain(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def first_sentence(text: str, limit: int) -> str:
    """取首句；以分号断开时改成句号，避免“…；”半截收尾（11-02 实测）。"""
    text = plain(text)
    m = re.search(r"^(.+?[。！？!?；;])", text)
    out = m.group(1) if m else text
    out = re.sub(r"[；;]$", "。", out)
    return clip(out, limit)


def headline(title: str, body: str, limit: int) -> str:
    """简报一句话：标题 + 首句。标题自带冒号时用句号连接，避免“A：B：C”。"""
    t = strip_date(title)
    first = first_sentence(body, 120)
    joiner = "。" if re.search(r"[：:]", t + first[:16]) else "："
    return clip(f"{t}{joiner}{first}", limit)


def strip_date(title: str) -> str:
    return L.DATE_SUFFIX_RE.sub("", title or "").strip()


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def load_blocked(path: Path = REACH_PATH) -> set:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {str(x.get("domain", "")).lower() for x in data.get("blocked", []) if x.get("domain")}


def is_blocked(url: str, blocked: set) -> bool:
    host = domain_of(url)
    return any(host == d or host.endswith("." + d) for d in blocked)


def norm_title(t: str) -> str:
    t = re.sub(r"[（(][^）)]*[）)]\s*$", "", t or "")
    return re.sub(r"[\s，。；：、“”\"'·—\-!！?？,.:;]", "", t).lower()


def cn_date(d: date) -> str:
    return f"{d.year}年{d.month}月{d.day}日 · 星期{WEEKDAYS[d.weekday()]}"


# ---------------------------------------------------------------- 构建

def _sources(card: Dict[str, Any], blocked: set, warnings: List[str]) -> List[Dict[str, str]]:
    out = []
    for text, url in card["links"]:
        if is_blocked(url, blocked):
            warnings.append(f"《{(card['title'] or '')[:20]}》来源 {domain_of(url)} 公司网络不可达，已从首页剔除")
            continue
        out.append({"label": text.replace("↗", "").strip() or domain_of(url), "url": url})
    return out[:4]


def build_feed(raw_md: str, report_date: date, *, daily_url: str = "", search_log: Optional[Dict[str, Any]] = None,
               blocked: Optional[set] = None, now: Optional[datetime] = None,
               next_update_at: Optional[str] = None, timezone: str = "Asia/Shanghai") -> Tuple[Dict[str, Any], List[str]]:
    """把 V5 正文转成首页 feed。返回 (feed, warnings)。非 V5 正文抛 ValueError。"""
    if not L.is_v5(raw_md):
        raise ValueError("正文不是 V5 版式（缺少 [wrap=air-daily]），首页数据只支持 V5")
    blocked = blocked if blocked is not None else load_blocked()
    warnings: List[str] = []
    doc = L.parse(raw_md)
    now = now or datetime.now(parse_timezone(timezone))

    heat_by_title: Dict[str, Any] = {}
    for c in (search_log or {}).get("candidates") or []:
        if c.get("decision") == "SIGNAL":
            heat_by_title[norm_title(c.get("title", ""))] = c

    def card_item(c: Dict[str, Any], kind: str) -> Dict[str, Any]:
        item = {
            "section": L.card_section(c) or ("跟踪" if kind == "tracking" else "精编"),
            "cred": c["cred"] if c["cred"] in CRED else "r",
            "title": clip(c["title"] or "", LIMITS["title"]),
            "summary": clip(" ".join(c["body"]), LIMITS["summary"]),
            "sources": _sources(c, blocked, warnings),
            "topic_url": daily_url,
        }
        if kind == "curated" and c["us"]:
            item["us"] = clip(c["us"][0], LIMITS["us"])
        return item

    core = [c for c in doc["cards"] if c["layer"] == 1]
    track = [c for c in doc["cards"] if c["layer"] == 2]
    sigs = [c for c in doc["cards"] if c["layer"] == 3]
    curated = [card_item(c, "curated") for c in core]
    tracking = [card_item(c, "tracking") for c in track]

    radar = []
    for c in sigs:
        srcs = _sources(c, blocked, warnings)
        frm = L.chip_of(c, "from")
        cand = heat_by_title.get(norm_title(c["title"] or ""), {})
        sig = {
            "cat": c["cat"] or "其他",
            "cred": c["cred"] if c["cred"] in CRED else "r",
            "from": clip(frm["text"] if frm else "", 40),
            "title": clip(c["title"] or "", LIMITS["title"]),
            "summary": clip(" ".join(c["body"]), LIMITS["summary"]),
            "url": srcs[0]["url"] if srcs else "",
            "tags": [],
        }
        if isinstance(cand.get("heat"), (int, float)):
            sig["heat"] = cand["heat"]
        elif frm:  # 无检索清单时，从来源标签末尾的数字兜底（Hacker News 866 / GitHub 今日 +1526★）
            m = re.search(r"\+?(\d[\d,]*)\s*★?\s*$", frm["text"])
            if m and not re.search(r"\d{1,2}-\d{1,2}\s*$", frm["text"]):  # 排除“arXiv 10-30”这类日期
                sig["heat"] = int(m.group(1).replace(",", ""))
        if cand.get("discovered_at"):
            sig["discovered_at"] = str(cand["discovered_at"])
        radar.append(sig)

    # 今天发生了什么：今日要紧优先，不足 3 条依次用金风相关、行业速递补
    order = ["今日要紧", "金风相关", "行业速递"]
    pool = sorted((c for c in core if L.card_section(c) in order), key=lambda c: order.index(L.card_section(c)))
    brief = []
    for c in pool[: MAX_ITEMS["brief"]]:
        srcs = _sources(c, [], [])  # 已在上面告警过，这里不重复
        srcs = [s for s in srcs if not is_blocked(s["url"], blocked)]
        brief.append({
            "text": headline(c["title"], " ".join(c["body"]), LIMITS["brief"]),
            "url": srcs[0]["url"] if srcs else daily_url,
            "source": srcs[0]["label"] if srcs else "",
        })

    # 今日焦点：第一条今日要紧
    lead = None
    first = next((c for c in core if L.card_section(c) == "今日要紧"), None)
    if first is not None:
        lead = {k: v for k, v in card_item(first, "curated").items() if k in ("title", "summary", "us", "sources", "cred")}
        lead["url"] = daily_url

    # 今日值得看：工具与做法优先，不足 4 条用热度最高的信号补
    picks = []
    for c in core:
        if L.card_section(c) == "工具与做法" and len(picks) < MAX_ITEMS["picks"]:
            srcs = [s for s in _sources(c, [], []) if not is_blocked(s["url"], blocked)]
            picks.append({"kind": "做法", "title": clip(c["title"] or "", LIMITS["pick"]),
                          "summary": first_sentence(" ".join(c["body"]), 80), "url": srcs[0]["url"] if srcs else daily_url})
    # 各源热度量级不同（GitHub 星数远大于 HN 分），先每类取一条保证多样，再按热度补位；传闻不作推荐
    ranked = sorted((s for s in radar if s.get("url") and s["cat"] != "传闻待证"), key=lambda s: -(s.get("heat") or 0))
    seen_cats = {p["kind"] for p in picks}
    chosen: List[Dict[str, Any]] = []
    for s in ranked:
        if s["cat"] not in seen_cats:
            chosen.append(s)
            seen_cats.add(s["cat"])
    chosen += [s for s in ranked if s not in chosen]
    for s in chosen[: max(0, MAX_ITEMS["picks"] - len(picks))]:
        picks.append({"kind": s["cat"], "title": clip(s["title"], LIMITS["pick"]),
                      "summary": first_sentence(s["summary"], 80), "url": s["url"]})

    community = []
    for line in doc["tail_sections"].get("## 社区今日", []):
        m = COMMUNITY_RE.match(line.strip())
        if m and len(community) < MAX_ITEMS["community"]:
            community.append({"who": clip(community_who(m.group(1)), 30), "title": clip(m.group(2), LIMITS["title"]),
                              "url": m.group(3)})
    question = clip(" ".join(s for s in doc["tail_sections"].get("## 今日一问", []) if s.strip()), 160)
    mainline = clip(re.sub(r"^>\s*今日主线[：:]\s*", "", doc["mainline"] or ""), 160)

    feed: Dict[str, Any] = {
        "schema": SCHEMA,
        "issue_date": report_date.isoformat(),
        "date": cn_date(report_date),
        "updated_at": now.isoformat(timespec="seconds"),
        "daily_url": daily_url,
        "mainline": mainline,
        "brief": brief,
        "lead": lead,
        "picks": picks,
        "curated": curated,
        "tracking": tracking,
        "radar": radar,
        "community": community,
        "question": question,
    }
    if next_update_at:
        feed["next_update_at"] = next_update_at
    if not radar and not curated:
        warnings.append("精编与雷达均为空，首页将不显示 AI 雷达区块")
    return feed, warnings


# ---------------------------------------------------------------- 校验与序列化

def _url_ok(u: Any) -> bool:
    return isinstance(u, str) and bool(re.match(r"^https?://\S+$", u) or re.match(r"^/(?!/)\S*$", u))


def validate_feed(feed: Dict[str, Any], blocked: Optional[set] = None) -> Tuple[List[str], List[str]]:
    blocked = blocked if blocked is not None else load_blocked()
    errs: List[str] = []
    warns: List[str] = []
    if not isinstance(feed, dict):
        return ["顶层必须是 JSON 对象"], warns
    if feed.get("schema") != SCHEMA:
        errs.append(f"schema 必须是 {SCHEMA}")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(feed.get("issue_date", ""))):
        errs.append("issue_date 须为 YYYY-MM-DD")
    for k in ("date", "updated_at"):
        if not feed.get(k):
            errs.append(f"缺少 {k}")
    for k, mx in MAX_ITEMS.items():
        v = feed.get(k, [])
        if not isinstance(v, list):
            errs.append(f"{k} 必须是数组")
        elif len(v) > mx:
            warns.append(f"{k} 有 {len(v)} 条，页面只取前 {mx} 条")
    if not any(feed.get(k) for k in ("curated", "tracking", "radar")):
        errs.append("curated / tracking / radar 不能全为空")
    urls: List[Tuple[str, Any]] = [("daily_url", feed.get("daily_url"))] if feed.get("daily_url") else []
    for name in ("curated", "tracking"):
        for i, c in enumerate(feed.get(name) or []):
            if not c.get("title"):
                errs.append(f"{name}[{i}] 缺少 title")
            if c.get("cred") not in CRED:
                errs.append(f"{name}[{i}].cred 必须是 g/y/r")
            urls += [(f"{name}[{i}].sources", s.get("url")) for s in c.get("sources") or []]
    for i, s in enumerate(feed.get("radar") or []):
        if not s.get("title"):
            errs.append(f"radar[{i}] 缺少 title")
        if s.get("cred") not in CRED:
            errs.append(f"radar[{i}].cred 必须是 g/y/r")
        if s.get("url"):
            urls.append((f"radar[{i}].url", s["url"]))
    for key in ("brief", "picks", "community"):
        for i, b in enumerate(feed.get(key) or []):
            if b.get("url"):
                urls.append((f"{key}[{i}].url", b["url"]))
    for where, u in urls:
        if not _url_ok(u):
            errs.append(f"{where} 不是 http(s) 或站内链接：{str(u)[:60]}")
        elif str(u).startswith("http") and is_blocked(u, blocked):
            errs.append(f"{where} 落在公司网络不可达名单：{domain_of(u)}")
    raw = build_raw(feed)
    if len(raw) > MAX_POST_CHARS:
        errs.append(f"序列化后 {len(raw)} 字，超过安全上限 {MAX_POST_CHARS}")
    return errs, warns


def build_raw(feed: Dict[str, Any]) -> str:
    return f"{HEADER}```json\n{json.dumps(feed, ensure_ascii=False, indent=2)}\n```\n"


def raw_sha(raw: str) -> str:
    return hashlib.sha256(raw.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


def extract_feed(raw: str) -> Optional[Dict[str, Any]]:
    """从底座帖原文取 JSON（容忍结尾围栏不换行），与首页组件的解析口径一致。"""
    m = re.search(r"```[a-z]*[ \t]*\r?\n([\s\S]*?)\s*```", raw or "", re.I)
    try:
        data = json.loads(m.group(1) if m else (raw or "").strip())
    except (json.JSONDecodeError, AttributeError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- 论坛读写

def _curl():
    from publish import auth_headers, curl_json  # 延迟导入：build 子命令不需要网络依赖
    return curl_json, auth_headers


def resolve_post_id(config: Dict[str, str], topic_id: int, post_number: int = 1) -> Optional[int]:
    curl_json, auth_headers = _curl()
    data = curl_json(f"{config['url']}/t/{int(topic_id)}.json", headers=auth_headers(config))
    for p in ((data or {}).get("post_stream") or {}).get("posts") or []:
        if p.get("post_number") == post_number:
            return int(p["id"])
    return None


def read_post_raw(config: Dict[str, str], post_id: int) -> Optional[str]:
    curl_json, auth_headers = _curl()
    data = curl_json(f"{config['url']}/posts/{int(post_id)}.json", headers=auth_headers(config))
    return data.get("raw") if isinstance(data, dict) and isinstance(data.get("raw"), str) else None


def put_post_raw(config: Dict[str, str], post_id: int, raw: str, reason: str) -> bool:
    curl_json, auth_headers = _curl()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump({"post": {"raw": raw, "edit_reason": reason}}, fh, ensure_ascii=False)
        body = fh.name
    try:
        data = curl_json(f"{config['url']}/posts/{int(post_id)}.json", method="PUT",
                         headers=auth_headers(config, json_content=True), data_file=body)
    finally:
        Path(body).unlink(missing_ok=True)
    return bool(data)


# 5.3.0：底座帖由三方共同维护，各写各的字段，互不覆盖
COLLECTOR_KEYS = ("radar", "radar_updated_at", "radar_next_at", "radar_total", "question_list")  # 采集器每轮写
WEEKLY_KEYS = ("topics",)    # 主题档案 skill 每周写
STANCE_KEYS = ("stance",)    # 技术雷达 skill 每季度写


def merge_with_live(feed: Dict[str, Any], live: Dict[str, Any]) -> Dict[str, Any]:
    """把本期日报生成的 feed 合进底座帖现有内容。

    - 采集器在运行（底座帖有 radar_updated_at）：信号雷达沿用采集器写的，日报不覆盖；
      采集器未上线：沿用日报自己的 radar（与 5.2.0 行为一致）。
    - 主题追踪、技术雷达始终保留底座帖现有值。
    """
    merged = dict(feed)
    if live.get("radar_updated_at"):
        for k in COLLECTOR_KEYS:
            if k in live:
                merged[k] = live[k]
            else:
                merged.pop(k, None)
    for k in WEEKLY_KEYS + STANCE_KEYS:
        if k in live:
            merged[k] = live[k]
    return merged


def publish_feed(feed: Dict[str, Any], config: Dict[str, str], *, topic_id: int, post_id: Optional[int] = None,
                 post_number: int = 1, allow_older: bool = False) -> Dict[str, Any]:
    """幂等更新底座帖。返回结果字典（status: UPDATED / NOOP / BLOCKED / FAILED）。

    5.3.0：写入前先读底座帖现有内容并合并（merge_with_live），只替换日报负责的字段。
    """
    result: Dict[str, Any] = {"topic_id": topic_id, "issue_date": feed.get("issue_date")}
    pid = post_id or resolve_post_id(config, topic_id, post_number)
    if not pid:
        return {**result, "status": "FAILED", "code": "FEED_POST_NOT_FOUND",
                "message": f"读不到话题 {topic_id} 第 {post_number} 楼"}
    result["post_id"] = pid
    current = read_post_raw(config, pid)
    if current is None:
        return {**result, "status": "FAILED", "code": "FEED_READ_FAILED",
                "message": "读不到底座帖原文（账号可能没有编辑权限），为避免盲写已停止"}
    live = extract_feed(current) or {}
    feed = merge_with_live(feed, live)
    raw = build_raw(feed)
    sha = raw_sha(raw)
    result["sha256"] = sha
    result["kept_from_live"] = sorted(k for k in COLLECTOR_KEYS + WEEKLY_KEYS + STANCE_KEYS
                                      if k in live and live.get(k) == feed.get(k))
    if raw_sha(current) == sha:
        return {**result, "status": "NOOP", "code": "FEED_UNCHANGED", "message": "底座帖内容已是本期，无需更新"}
    live_date = str(live.get("issue_date") or "")
    if live_date and live_date > str(feed.get("issue_date")) and not allow_older:
        return {**result, "status": "BLOCKED", "code": "FEED_OLDER_THAN_LIVE", "live_issue_date": live_date,
                "message": f"首页现为 {live_date} 期，不用更早的 {feed.get('issue_date')} 期覆盖（需要时加 --allow-older）"}
    if not put_post_raw(config, pid, raw, f"AI 雷达 {feed.get('issue_date')} 首页数据"):
        return {**result, "status": "FAILED", "code": "FEED_PUT_FAILED", "message": "更新底座帖请求失败"}
    back = read_post_raw(config, pid)
    if back is None or raw_sha(back) != sha:
        return {**result, "status": "FAILED", "code": "FEED_READBACK_MISMATCH",
                "message": "写入后回读与本地哈希不一致，请人工核对底座帖"}
    return {**result, "status": "UPDATED", "code": "FEED_UPDATED", "message": "底座帖已更新并回读一致"}


# ---------------------------------------------------------------- CLI

def load_feed_config(path: Path = RUNTIME_PATH) -> Dict[str, Any]:
    try:
        return (json.loads(Path(path).read_text(encoding="utf-8")) or {}).get("homepage_feed") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="AI 雷达首页数据：build 生成本地 JSON；publish 更新论坛底座帖")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("md")
    b.add_argument("--report-date", required=True)
    b.add_argument("--daily-url", default="")
    b.add_argument("--search-log")
    b.add_argument("--next-update-at")
    b.add_argument("--output", required=True)
    p = sub.add_parser("publish")
    p.add_argument("feed")
    p.add_argument("--env-file", required=True)
    p.add_argument("--topic-id", type=int)
    p.add_argument("--post-id", type=int)
    p.add_argument("--state-file", required=True)
    p.add_argument("--allow-older", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "build":
        rd = datetime.strptime(a.report_date, "%Y-%m-%d").date()
        log = json.loads(Path(a.search_log).read_text(encoding="utf-8")) if a.search_log else None
        try:
            feed, warns = build_feed(Path(a.md).read_text(encoding="utf-8"), rd, daily_url=a.daily_url,
                                     search_log=log, next_update_at=a.next_update_at)
        except ValueError as exc:
            emit_status("error", "FEED_NOT_V5", str(exc))
            return 2
        errs, vwarns = validate_feed(feed)
        if errs:
            emit_status("error", "FEED_INVALID", "首页数据校验失败", errors=errs[:10])
            return 2
        atomic_write_json(a.output, feed)
        emit_status("ok", "FEED_BUILT", "首页数据已生成（本地）", output=a.output, warnings=(warns + vwarns)[:10] or None,
                    counts={k: len(feed[k]) for k in ("brief", "picks", "curated", "tracking", "radar", "community")})
        return 0

    feed = json.loads(Path(a.feed).read_text(encoding="utf-8"))
    errs, _ = validate_feed(feed)
    if errs:
        emit_status("error", "FEED_INVALID", "首页数据校验失败，未发送", errors=errs[:10])
        return 2
    cfg = load_feed_config()
    topic_id = a.topic_id or cfg.get("topic_id")
    if not topic_id:
        emit_status("error", "FEED_TOPIC_MISSING", "未配置底座帖话题 ID（runtime.json homepage_feed.topic_id）")
        return 2
    try:
        config = load_discourse_config(a.env_file)
    except ConfigurationError as exc:
        emit_status("error", "CONFIG_ERROR", str(exc))
        return 2
    res = publish_feed(feed, config, topic_id=int(topic_id), post_id=a.post_id or cfg.get("post_id"),
                       post_number=int(cfg.get("post_number") or 1), allow_older=a.allow_older)
    res["finished_at"] = datetime.now(parse_timezone()).isoformat(timespec="seconds")
    atomic_write_json(a.state_file, res)
    emit_status("ok" if res["status"] in ("UPDATED", "NOOP") else "error", res["code"], res["message"],
                post_id=res.get("post_id"), status_detail=res["status"])
    return 0 if res["status"] in ("UPDATED", "NOOP") else (2 if res["status"] == "BLOCKED" else 3)


if __name__ == "__main__":
    sys.exit(main())
