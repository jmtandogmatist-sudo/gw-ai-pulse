"""本地 SQLite 索引。只是缓存：丢了可以用 `python -m collector rebuild-index` 从论坛重建。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Signal

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
  signal_id TEXT PRIMARY KEY,
  event_key TEXT NOT NULL,
  topic_id INTEGER,
  post_id INTEGER,
  discovered_at TEXT NOT NULL,
  data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_event ON signals(event_key);
CREATE INDEX IF NOT EXISTS idx_signals_time ON signals(discovered_at);
CREATE TABLE IF NOT EXISTS url_alias (
  url_id TEXT PRIMARY KEY,
  signal_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  reason TEXT NOT NULL,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_health (
  name TEXT PRIMARY KEY,
  last_ok TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  ok INTEGER,
  report TEXT
);
CREATE TABLE IF NOT EXISTS state (
  key TEXT PRIMARY KEY,
  value TEXT
);
-- 2026-11-20：候选层台账。此前 known_url 只认「已经发成信号」的 URL，
-- 被模型判掉（keep=false）的条目下一轮还会被重新抓到、重新送模型。
-- 后果不是多发帖，而是「不敢把信源窗口开大」：arXiv 从按条数(30 条/2.3 小时)
-- 改成按时间(600 条/51 小时)之后，每轮要多送 570 条重复论文给模型。
-- 有了这张表，判过的条目在窗口期内不再送判，宽窗口才付得起。
CREATE TABLE IF NOT EXISTS judged (
  url_id TEXT PRIMARY KEY,
  judged_at TEXT NOT NULL,
  keep INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  -- 2026-11-20：把模型的判定结果原样存下来。
  -- 起因：换了论文判据后 keep=true 远多于每轮发帖配额（469 判 / 182 留 / 只发 10），
  -- 而被配额挡住的条目「判过了但没发出去」，既不能按 keep=false 跳过（会永久丢失），
  -- 也不能不记（下一轮要重判 172 条，实测首轮 994 秒、逼近 1200 秒看门狗）。
  -- 存下 verdict 后：下一轮不再调模型，直接用旧判定组信号发帖。
  verdict TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_judged_time ON judged(judged_at);
-- gw-ai-pulse（2026-11-16）：社区帖子扫描记录。
-- 人发的帖子「原地打标签，不复制」：只记在这里，不生成新的信号话题。
-- 主键是 topic_id，保证同一个帖子只打一次标签、「本周 +N」不会每轮重复计数。
CREATE TABLE IF NOT EXISTS seen_posts (
  topic_id INTEGER PRIMARY KEY,
  first_seen_at TEXT NOT NULL,
  week TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '[]',
  title TEXT NOT NULL DEFAULT '',
  who TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_seen_posts_week ON seen_posts(week);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)
        # 轻量迁移：老库里 judged 表还没有 verdict 列（2026-11-20 加的）。
        # 用 PRAGMA 判断而不是 try/except，避免把「表不存在」这类真问题一起吞掉。
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(judged)")}
        if "verdict" not in cols:
            self.db.execute("ALTER TABLE judged ADD COLUMN verdict TEXT NOT NULL DEFAULT ''")
            self.db.commit()

    # ---- 信号 ----
    def known_url(self, url_id: str) -> bool:
        row = self.db.execute("SELECT 1 FROM url_alias WHERE url_id=?", (url_id,)).fetchone()
        return row is not None

    def judged_recent(self, since_iso: str) -> dict[str, tuple[int, str]]:
        """窗口期内已经判过的候选：url_id → (keep, verdict_json)。

        keep=0 的下一轮直接跳过（省预算）；keep=1 的用 verdict 复用，不再调模型。
        """
        return {r[0]: (int(r[1]), r[2] or "") for r in self.db.execute(
            "SELECT url_id, keep, verdict FROM judged WHERE judged_at>=?", (since_iso,))}

    def mark_judged(self, rows: list[tuple[str, str, int, str, str]]) -> None:
        """记下本轮确实拿到结论的候选：(url_id, judged_at, keep, reason, verdict_json)。"""
        if not rows:
            return
        self.db.executemany(
            "INSERT OR REPLACE INTO judged(url_id,judged_at,keep,reason,verdict) VALUES(?,?,?,?,?)",
            rows)
        self.db.commit()

    def by_event_key(self, key: str) -> Signal | None:
        row = self.db.execute("SELECT data FROM signals WHERE event_key=? ORDER BY discovered_at LIMIT 1", (key,)).fetchone()
        return Signal.from_dict(json.loads(row[0])) if row else None

    def save_signal(self, sig: Signal, url_ids: list[str]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO signals(signal_id,event_key,topic_id,post_id,discovered_at,data) VALUES(?,?,?,?,?,?)",
            (sig.signal_id, sig.event_key, sig.topic_id, sig.post_id, sig.discovered_at, json.dumps(sig.to_dict(), ensure_ascii=False)),
        )
        for uid in url_ids:
            self.db.execute("INSERT OR IGNORE INTO url_alias(url_id,signal_id) VALUES(?,?)", (uid, sig.signal_id))
        self.db.commit()

    def recent_signals(self, since_iso: str, limit: int = 500) -> list[Signal]:
        rows = self.db.execute(
            "SELECT data FROM signals WHERE discovered_at>=? ORDER BY discovered_at DESC LIMIT ?", (since_iso, limit)
        ).fetchall()
        return [Signal.from_dict(json.loads(r[0])) for r in rows]

    def count_signals(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM signals").fetchone()[0]

    # ---- 待人工 ----
    def add_pending(self, reason: str, data: dict) -> None:
        self.db.execute("INSERT INTO pending(created_at,reason,data) VALUES(?,?,?)",
                        (datetime.now().astimezone().isoformat(timespec="seconds"), reason, json.dumps(data, ensure_ascii=False)))
        self.db.commit()

    def pending_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM pending").fetchone()[0]

    # ---- 信源健康 ----
    def mark_source(self, name: str, ok: bool, error: str = "") -> int:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        row = self.db.execute("SELECT consecutive_failures FROM source_health WHERE name=?", (name,)).fetchone()
        fails = 0 if ok else ((row[0] if row else 0) + 1)
        self.db.execute(
            "INSERT INTO source_health(name,last_ok,consecutive_failures,last_error) VALUES(?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET consecutive_failures=excluded.consecutive_failures, "
            "last_error=excluded.last_error, last_ok=COALESCE(excluded.last_ok, source_health.last_ok)",
            (name, now if ok else None, fails, "" if ok else error[:500]),
        )
        self.db.commit()
        return fails

    def failing_sources(self, threshold: int) -> list[tuple[str, int, str]]:
        return self.db.execute(
            "SELECT name, consecutive_failures, last_error FROM source_health WHERE consecutive_failures>=? ORDER BY name",
            (threshold,),
        ).fetchall()

    # ---- 运行记录 ----
    def start_run(self) -> int:
        cur = self.db.execute("INSERT INTO runs(started_at) VALUES(?)",
                              (datetime.now().astimezone().isoformat(timespec="seconds"),))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, ok: bool, report: dict) -> None:
        self.db.execute("UPDATE runs SET finished_at=?, ok=?, report=? WHERE id=?",
                        (datetime.now().astimezone().isoformat(timespec="seconds"), int(ok),
                         json.dumps(report, ensure_ascii=False), run_id))
        self.db.commit()

    def runs_since(self, since_iso: str) -> list[dict]:
        rows = self.db.execute("SELECT started_at, ok, report FROM runs WHERE started_at>=? ORDER BY id", (since_iso,)).fetchall()
        return [{"started_at": r[0], "ok": bool(r[1]), "report": json.loads(r[2] or "{}")} for r in rows]

    def last_success_at(self) -> str | None:
        """上次**成功完成**的采集时间（finished_at）。用于补跑判断。"""
        row = self.db.execute(
            "SELECT finished_at FROM runs WHERE ok=1 AND finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def recent_titles(self, since_iso: str) -> list[str]:
        """近 N 天已发信号的标题，用于标题指纹去重。"""
        rows = self.db.execute(
            "SELECT data FROM signals WHERE discovered_at>=? ORDER BY discovered_at DESC",
            (since_iso,),
        ).fetchall()
        out = []
        for (blob,) in rows:
            try:
                out.append(json.loads(blob).get("title") or "")
            except json.JSONDecodeError:
                continue
        return [t for t in out if t]

    # ---- 社区帖子 ----
    def post_seen(self, topic_id: int) -> bool:
        row = self.db.execute("SELECT 1 FROM seen_posts WHERE topic_id=?", (topic_id,)).fetchone()
        return row is not None

    def mark_post_seen(self, topic_id: int, *, week: str, tags: list[str],
                       title: str = "", who: str = "") -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO seen_posts(topic_id,first_seen_at,week,tags,title,who) VALUES(?,?,?,?,?,?)",
            (int(topic_id), datetime.now().astimezone().isoformat(timespec="seconds"),
             week, json.dumps(list(tags), ensure_ascii=False), title[:200], who[:80]),
        )
        self.db.commit()

    def week_tag_counts(self, week: str) -> dict[str, int]:
        """某一周每个主题标签被多少个帖子命中，用于主题档案的「本周 +N」。"""
        out: dict[str, int] = {}
        for (blob,) in self.db.execute("SELECT tags FROM seen_posts WHERE week=?", (week,)).fetchall():
            try:
                for t in json.loads(blob or "[]"):
                    out[t] = out.get(t, 0) + 1
            except json.JSONDecodeError:
                continue
        return out

    # ---- 键值 ----
    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_state(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", (key, value))
        self.db.commit()
