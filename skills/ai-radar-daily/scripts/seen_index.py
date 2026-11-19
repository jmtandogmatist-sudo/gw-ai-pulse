#!/usr/bin/env python3
"""已见索引（seen_index.py，V4.3 新增）——跨期去重的唯一真源。

为什么需要它：
- 旧去重只比对「论坛最近 2/4 期已发布正文」，看不到被淘汰候选、池中待发候选，
  也看不到 4 期以前发过的内容；V4 版式上线后正文解析还失效了（见 CHANGELOG 4.3.0）。
- 本索引把**每一条进入过检索清单的候选**（含 REJECTED）都落到本地 SQLite，
  检索后、入池前先过一遍三级查重，自查阶段再兜底。

三级查重：
- L1 URL：规范化 URL 精确匹配（去 fragment / 跟踪参数 / www / 末尾斜杠，http→https）。永久有效。
- L2 标题：规范化标题的字符 bigram Jaccard ≥ 0.6，回看 30 天。
- L3 事件键：Agent 抽取的 `event_key`（主体|动作|对象）相同且事件日期相差 ≤ 3 天，回看 30 天。
- 候选显式标 `is_update: true`（有新事实的跟踪报道）时，L2/L3 命中降级为 UPDATE 提示，不判重复；
  L1 命中已发布 URL 仍判重复（同一页面不能再发一次）。

同一 report_date 的记录不与自己比对（同日重跑幂等）。
不联网、不读凭据；飞书 Candidates 表只是远端镜像，不作为查重依据。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DB_NAME = "seen-index.sqlite"
TITLE_SIM_THRESHOLD = 0.6
# 包含度：短标题的 bigram 有多少落在长标题里；用于「同一事件、一长一短」的标题（Jaccard 会被长度差拉低）
TITLE_CONTAINMENT_THRESHOLD = 0.8
TITLE_CONTAINMENT_MIN_BIGRAMS = 8   # 短标题太短时不用包含度，避免误伤
LOOKBACK_DAYS = 30
EVENT_DATE_TOLERANCE = 3

TRACKING_PARAMS = re.compile(
    r"^(utm_.*|spm|from|source|share|share_.*|ref|ref_src|fbclid|gclid|mc_.*|scene|srcid|wfr|_ga)$",
    re.I,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    uid TEXT PRIMARY KEY,
    report_date TEXT NOT NULL,
    title TEXT,
    title_norm TEXT,
    event_key TEXT,
    event_date TEXT,
    decision TEXT,
    status TEXT NOT NULL,          -- pending | published | rejected
    issue_id TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    source_urls TEXT               -- JSON 数组（原始 URL）
);
CREATE TABLE IF NOT EXISTS urls (
    url_norm TEXT NOT NULL,
    uid TEXT NOT NULL,
    PRIMARY KEY (url_norm, uid)
);
CREATE INDEX IF NOT EXISTS idx_items_date ON items(report_date);
CREATE INDEX IF NOT EXISTS idx_items_event ON items(event_key);
"""


# ---------------------------------------------------------------- 规范化

# V5.1.0：逐日从头编号的事件键（signal-1 / track-2 / news_03 …）会跨日撞号，
# 让已见索引 L3 把次日候选整批判为重复（2026-11-02 实测 21 条误判）。
GENERIC_EVENT_KEY_RE = re.compile(r"^(?:signal|sig|track|trk|rej|news|tool|digest|item|cand)[-_ ]?\d+$", re.I)


def generic_event_keys(candidates):
    """返回 [(id, event_key)]：事件键是通用编号、不能用于跨日查重的候选。"""
    out = []
    for c in candidates or []:
        k = str((c or {}).get("event_key") or "").strip()
        if k and GENERIC_EVENT_KEY_RE.match(k):
            out.append((c.get("id"), k))
    return out


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = "https" if parts.scheme.lower() in ("http", "https") else parts.scheme.lower()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+$", "", parts.path) or ""
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if not TRACKING_PARAMS.match(k)))
    return urlunsplit((scheme, host, path, query, ""))


def normalize_title(title: str) -> str:
    value = title or ""
    value = re.sub(r"^\s*【[^】]{1,6}】\s*", "", value)
    value = re.sub(r"[（(]\s*跟踪报道[^）)]*[）)]", "", value)
    value = re.sub(r"[（(]\s*\d{1,2}月\d{1,2}日[^）)]*[）)]", "", value)
    value = re.sub(r"[\s，。；：、“”‘’\"'·—\-!！?？,.:;()（）《》<>\[\]|/]+", "", value)
    return value.lower()


def normalize_event_key(key: Optional[str]) -> str:
    if not key:
        return ""
    return re.sub(r"\s+", "", str(key)).lower()


def candidate_event_key(c: Dict[str, Any]) -> Optional[str]:
    """取候选的事件键：正式字段 event_key 优先，兼容 5.3.0 的 event_key_hint 别名。

    signal_pool 早期版本把采集器的 event_key 输出为 event_key_hint，执行 Agent 若原样照抄
    进检索清单，L3 事件键查重会静默失效（只靠 L1 URL + L2 标题兜底）。
    """
    return c.get("event_key") or c.get("event_key_hint")


def _bigrams(text: str) -> set:
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def title_similarity(a: str, b: str) -> float:
    """Jaccard 与包含度取大者（包含度按阈值折算到 Jaccard 阈值尺度，便于统一比较）。"""
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    jaccard = inter / len(ga | gb)
    short = min(len(ga), len(gb))
    if short < TITLE_CONTAINMENT_MIN_BIGRAMS:
        return jaccard
    containment = inter / short
    scaled = containment * TITLE_SIM_THRESHOLD / TITLE_CONTAINMENT_THRESHOLD
    return max(jaccard, min(scaled, 1.0))


def _parse_date(value: Any) -> Optional[date]:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def candidate_urls(c: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    for u in [c.get("source_url"), *(c.get("source_urls") or [])]:
        if u and u not in urls:
            urls.append(str(u))
    return urls


# ---------------------------------------------------------------- 存取

def db_path(work_dir: Path) -> Path:
    return Path(work_dir) / DB_NAME


def connect(work_dir: Path) -> sqlite3.Connection:
    path = db_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _status_for(decision: Any) -> str:
    d = str(decision or "").upper()
    if d.startswith("REJECTED"):
        return "rejected"
    return "pending"


def record_candidates(conn: sqlite3.Connection, report_date: str, candidates: Iterable[Dict[str, Any]],
                      issue_id: Optional[str] = None) -> int:
    """把一期检索清单的候选写入索引（幂等：同 uid 覆盖，已 published 的状态不回退）。"""
    from holiday_pool import make_uid  # 复用池的 uid 规则，保证两边键一致
    n = 0
    now = _now()
    for c in candidates:
        uid = c.get("uid") or make_uid(report_date, c)
        row = conn.execute("SELECT status, first_seen FROM items WHERE uid=?", (uid,)).fetchone()
        status = "published" if row and row["status"] == "published" else _status_for(c.get("decision"))
        first_seen = row["first_seen"] if row else now
        urls = candidate_urls(c)
        conn.execute(
            """INSERT OR REPLACE INTO items
               (uid, report_date, title, title_norm, event_key, event_date, decision, status,
                issue_id, first_seen, last_seen, source_urls)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, report_date, c.get("title"), normalize_title(c.get("title") or ""),
             normalize_event_key(candidate_event_key(c)), c.get("event_date"), c.get("decision"),
             status, issue_id, first_seen, now, json.dumps(urls, ensure_ascii=False)),
        )
        conn.execute("DELETE FROM urls WHERE uid=?", (uid,))
        for u in urls:
            nu = normalize_url(u)
            if nu:
                conn.execute("INSERT OR IGNORE INTO urls(url_norm, uid) VALUES (?,?)", (nu, uid))
        n += 1
    conn.commit()
    return n


def mark_published(conn: sqlite3.Connection, uids: Iterable[str], issue_id: str) -> int:
    n = 0
    for uid in uids:
        cur = conn.execute("UPDATE items SET status='published', issue_id=?, last_seen=? WHERE uid=?",
                           (issue_id, _now(), uid))
        n += cur.rowcount
    conn.commit()
    return n


def seed_published(conn: sqlite3.Connection, report_date: str, titles: List[str], urls: List[str],
                   issue_id: Optional[str] = None) -> int:
    """冷启动：把论坛已发布期次（fetch_recent 输出）作为 published 记录灌入索引。"""
    pseudo = []
    for i, t in enumerate(titles, start=1):
        pseudo.append({"id": f"SEED-T{i:02d}", "title": t, "decision": "SELECTED"})
    for i, u in enumerate(urls, start=1):
        pseudo.append({"id": f"SEED-U{i:02d}", "title": "", "source_url": u, "decision": "SELECTED"})
    record_candidates(conn, report_date, pseudo, issue_id=issue_id)
    from holiday_pool import make_uid
    return mark_published(conn, [make_uid(report_date, c) for c in pseudo], issue_id or f"SEED-{report_date}")


# ---------------------------------------------------------------- 查重

def check_candidate(conn: sqlite3.Connection, report_date: str, c: Dict[str, Any],
                    lookback_days: int = LOOKBACK_DAYS) -> Dict[str, Any]:
    """返回 {"verdict": NEW|DUPLICATE|UPDATE, "hits": [...]}。"""
    rd = _parse_date(report_date)
    hits: List[Dict[str, Any]] = []
    is_update = bool(c.get("is_update"))

    # L1 URL（永久）
    for u in candidate_urls(c):
        nu = normalize_url(u)
        if not nu:
            continue
        for row in conn.execute(
            """SELECT i.uid, i.report_date, i.title, i.status FROM urls x JOIN items i ON i.uid=x.uid
               WHERE x.url_norm=? AND i.report_date<>?""", (nu, report_date)):
            hits.append({"level": "L1_URL", "url": u, "uid": row["uid"], "report_date": row["report_date"],
                         "title": row["title"], "status": row["status"]})

    # L2 / L3（回看窗口内）
    tn = normalize_title(c.get("title") or "")
    ek = normalize_event_key(candidate_event_key(c))
    ed = _parse_date(c.get("event_date"))
    for row in conn.execute("SELECT uid, report_date, title, title_norm, event_key, event_date, status "
                            "FROM items WHERE report_date<>?", (report_date,)):
        prd = _parse_date(row["report_date"])
        if rd and prd and (rd - prd).days > lookback_days:
            continue
        if tn and row["title_norm"]:
            sim = title_similarity(tn, row["title_norm"])
            if sim >= TITLE_SIM_THRESHOLD:
                hits.append({"level": "L2_TITLE", "similarity": round(sim, 3), "uid": row["uid"],
                             "report_date": row["report_date"], "title": row["title"], "status": row["status"]})
        if ek and row["event_key"] == ek:
            red = _parse_date(row["event_date"])
            if not (ed and red) or abs((ed - red).days) <= EVENT_DATE_TOLERANCE:
                hits.append({"level": "L3_EVENT", "event_key": ek, "uid": row["uid"],
                             "report_date": row["report_date"], "title": row["title"], "status": row["status"]})

    if not hits:
        return {"verdict": "NEW", "hits": []}
    hard = [h for h in hits if h["level"] == "L1_URL" and h["status"] == "published"]
    if is_update and not hard:
        return {"verdict": "UPDATE", "hits": hits}
    return {"verdict": "DUPLICATE", "hits": hits}


def dedup_search_log(conn: sqlite3.Connection, report_date: str, log: Dict[str, Any]) -> Dict[str, Any]:
    """对检索清单逐条查重；DUPLICATE 的 SELECTED 候选改判 REJECTED_DUPLICATE。返回报告。"""
    report = {"report_date": report_date, "checked_at": _now(), "total": 0,
              "new": 0, "duplicate": 0, "update": 0, "items": []}
    for c in log.get("candidates") or []:
        res = check_candidate(conn, report_date, c)
        report["total"] += 1
        report[res["verdict"].lower()] += 1
        entry = {"id": c.get("id"), "title": c.get("title"), "verdict": res["verdict"], "hits": res["hits"][:5]}
        if res["verdict"] == "DUPLICATE" and str(c.get("decision", "")).upper() == "SELECTED":
            c["decision"] = "REJECTED_DUPLICATE"
            c["reject_reason"] = f"seen-index {res['hits'][0]['level']} 命中 {res['hits'][0]['report_date']}"
            entry["decision_changed"] = True
        if res["verdict"] == "UPDATE":
            c["dedup_note"] = "跟踪报道：与历史事件同簇，按更新处理"
        c["dedup_verdict"] = res["verdict"]
        report["items"].append(entry)
    log["seen_index_dedup"] = {k: report[k] for k in ("checked_at", "total", "new", "duplicate", "update")}
    return report


def summary(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute("SELECT status, COUNT(*) n FROM items GROUP BY status").fetchall()
    span = conn.execute("SELECT MIN(report_date) a, MAX(report_date) b FROM items").fetchone()
    return {"by_status": {r["status"]: r["n"] for r in rows},
            "url_count": conn.execute("SELECT COUNT(*) FROM urls").fetchone()[0],
            "span": f"{span['a']}~{span['b']}" if span and span["a"] else None}


if __name__ == "__main__":  # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser(description="已见索引工具")
    ap.add_argument("action", choices=["summary"])
    ap.add_argument("--work-dir", required=True)
    a = ap.parse_args()
    print(json.dumps(summary(connect(Path(a.work_dir))), ensure_ascii=False, indent=2))
