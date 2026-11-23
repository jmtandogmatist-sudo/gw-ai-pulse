"""发布深度简报到 Discourse 分类 15，并只写底座帖 632 的 daily_brief 字段（不覆盖采集器字段）。"""
from __future__ import annotations

import fcntl
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .config import BriefConfig, get_secret

_FEED_BLOCK = re.compile(r"```json\s*(.*?)```", re.S)
_CST = timezone(timedelta(hours=8))
_FEED_CHAR_BUDGET = 28500


class DiscoursePublisher:
    def __init__(self, cfg: BriefConfig):
        dc = cfg.discourse
        self.base = dc["base_url"].rstrip("/")
        self.username = dc["api_username"]
        self.api_key = get_secret(dc.get("api_key_secret", "discourse-api-key"))
        self.verify_tls = bool(dc.get("verify_tls", True))
        self.category_id = int(dc.get("category_id", 15))
        self.tags = list(dc.get("tags") or ["ai", "资讯"])
        self.feed_topic_id = int(dc.get("feed_topic_id", 632))

    @property
    def _headers(self) -> dict[str, str]:
        return {"Api-Key": self.api_key, "Api-Username": self.username}

    def create_brief_topic(self, title: str, raw: str, *, external_id: str) -> dict[str, Any]:
        payload = {
            "title": title,
            "raw": raw,
            "category": self.category_id,
            "tags": self.tags,
            "external_id": external_id,
        }
        r = requests.post(
            f"{self.base}/posts.json",
            headers=self._headers,
            json=payload,
            timeout=30,
            verify=self.verify_tls,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"发帖失败 HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        return {"topic_id": data["topic_id"], "post_id": data["id"]}

    def update_feed_daily_brief(self, card: dict[str, Any], *, reason: str) -> dict[str, Any]:
        """读-改-写底座帖 632，仅更新 daily_brief 与更新时间字段，并持有共享文件锁防并发覆盖。"""
        lock_path = Path(__file__).resolve().parents[2] / "01-采集器" / "state" / "feed632.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w", encoding="utf-8") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                return self._update_feed_daily_brief_locked(card, reason=reason)
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)

    def _update_feed_daily_brief_locked(self, card: dict[str, Any], *, reason: str) -> dict[str, Any]:
        t = requests.get(
            f"{self.base}/t/{self.feed_topic_id}.json",
            headers=self._headers,
            timeout=20,
            verify=self.verify_tls,
        )
        t.raise_for_status()
        post_id = t.json()["post_stream"]["posts"][0]["id"]
        p = requests.get(
            f"{self.base}/posts/{post_id}.json",
            headers=self._headers,
            timeout=20,
            verify=self.verify_tls,
        )
        p.raise_for_status()
        raw = p.json().get("raw") or ""
        m = _FEED_BLOCK.search(raw)
        if m:
            feed = json.loads(m.group(1))
            fenced = True
        elif raw.strip().startswith("{"):
            feed = json.loads(raw.strip())
            fenced = False
        else:
            raise RuntimeError("底座帖 632 中未找到 JSON 数据块")

        if feed.get("schema") != "air-pulse/1":
            raise RuntimeError(f"底座帖 schema 异常：{feed.get('schema')}")

        now_iso = datetime.now(_CST).isoformat(timespec="seconds")
        feed["daily_brief"] = card
        feed["daily_brief_updated_at"] = now_iso
        feed["updated_at"] = now_iso
        body_json = json.dumps(feed, ensure_ascii=False, indent=2)
        new_raw = (raw[:m.start()] + "```json\n" + body_json + "\n```" + raw[m.end():]) if fenced else body_json

        # 守住与采集器一致的字符预算（28,500，远低于 Discourse 32,000 硬上限）
        if len(new_raw) > _FEED_CHAR_BUDGET and isinstance(feed.get("radar"), list) and len(feed["radar"]) > 10:
            while len(new_raw) > _FEED_CHAR_BUDGET and len(feed["radar"]) > 10:
                feed["radar"].pop()
                body_json = json.dumps(feed, ensure_ascii=False, indent=2)
                new_raw = (raw[:m.start()] + "```json\n" + body_json + "\n```" + raw[m.end():]) if fenced else body_json

        u = requests.put(
            f"{self.base}/posts/{post_id}.json",
            headers=self._headers,
            json={"post": {"raw": new_raw, "edit_reason": reason}},
            timeout=30,
            verify=self.verify_tls,
        )
        if u.status_code >= 400:
            raise RuntimeError(f"更新底座帖 632 失败 HTTP {u.status_code}: {u.text[:300]}")

        # 读回校验
        back = requests.get(
            f"{self.base}/raw/{self.feed_topic_id}/1",
            headers=self._headers,
            timeout=20,
            verify=self.verify_tls,
        ).text
        bm = _FEED_BLOCK.search(back)
        back_feed = json.loads(bm.group(1)) if bm else json.loads(back.strip())
        if (back_feed.get("daily_brief") or {}).get("date") != card["date"]:
            raise RuntimeError("底座帖 632 写入后读回校验不一致")
        return {"post_id": post_id, "chars": len(new_raw), "radar_count": len(back_feed.get("radar") or [])}
