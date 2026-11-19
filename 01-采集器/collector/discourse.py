"""Discourse 接口封装。真实客户端与内存里的模拟论坛接口一致，试运行和测试用模拟论坛。"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests

from .config import Config, get_secret

log = logging.getLogger("collector.discourse")

# 429 只重试一次、单次等待最多 10 秒（2026-11-19 事故）。
# 旧代码按 Retry-After 睡眠、最多两次、单次上限 120 秒；限额被打满时，
# 几十个请求各自睡几十秒，一轮能拖到 40 分钟以上。
RATE_LIMIT_RETRIES = 1
RATE_LIMIT_MAX_WAIT = 10.0

# 每个请求之间的最小间隔与每分钟上限：admin API key 的限额是每分钟 N 次，
# 一轮采集集中在社区扫描那一段打请求，不排队就会撞限额。
DEFAULT_MIN_INTERVAL = 1.0
DEFAULT_MAX_PER_MINUTE = 40


class DiscourseError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class Discourse:
    def __init__(self, cfg: Config):
        s = cfg.settings["discourse"]
        self.base = s["base_url"].rstrip("/")
        self.username = s.get("api_username", "ai-radar")
        self.key_name = s.get("api_key_secret", "discourse-api-key")
        self.interval = s.get("post_interval_seconds", 16)
        self.verify = s.get("verify_tls", True)
        self._last_post = 0.0
        # 请求限速状态（见 _pace 的注释）
        self.min_interval = float(s.get("min_request_interval", DEFAULT_MIN_INTERVAL))
        self.max_per_minute = int(s.get("max_requests_per_minute", DEFAULT_MAX_PER_MINUTE))
        self.requests_made = 0
        self.rate_limited = 0
        self._window: list[float] = []
        self._last_req = 0.0

    def reset_counters(self) -> None:
        """每轮开始时清零，让报告里的请求数只统计本轮。"""
        self.requests_made = 0
        self.rate_limited = 0
        self._window.clear()
        self._last_req = 0.0

    def _pace(self) -> None:
        """发请求前排队：最小间隔 + 每分钟上限。

        为什么需要（2026-11-19 实测）：Discourse 对 admin API key 有每分钟次数上限，
        超了返回 429（Discourse-Rate-Limit-Error-Code: admin_api_key_rate_limit）。
        那一轮社区扫描要给 55 条帖子「补发帖人」，等于连发 55 次 topic()，
        限额瞬间打满，之后每个请求都要睡 Retry-After——这就是首页一整天不更新的起点。

        限速落在客户端而不是靠 429 兜底，是因为 429 之后只能睡，睡多少都不由自己定；
        在这里排队等待是可控的（一轮多花几秒到几十秒，远好过整轮被卡死）。
        """
        now = time.monotonic()
        if self.max_per_minute > 0:
            cutoff = now - 60.0
            self._window[:] = [t for t in self._window if t > cutoff]
            if len(self._window) >= self.max_per_minute:
                need = self._window[0] + 60.0 - now
                if need > 0:
                    log.info("论坛限速：本分钟已发 %d 次，等待 %.1f 秒", len(self._window), need)
                    time.sleep(need)
                    now = time.monotonic()
                    self._window[:] = [t for t in self._window if t > now - 60.0]
        if self.min_interval > 0 and self._last_req:
            need = self._last_req + self.min_interval - now
            if need > 0:
                time.sleep(need)
                now = time.monotonic()
        self._last_req = now
        self._window.append(now)
        self.requests_made += 1

    def _headers(self) -> dict:
        return {"Api-Key": get_secret(self.key_name), "Api-Username": self.username,
                "Content-Type": "application/json"}

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        for attempt in range(3):
            self._pace()
            r = requests.request(method, self.base + path, headers=self._headers(),
                                 data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None,
                                 timeout=30, verify=self.verify)
            if r.status_code == 429:
                self.rate_limited += 1
                if attempt < RATE_LIMIT_RETRIES:
                    try:
                        wait = min(float(r.headers.get("Retry-After") or 5), RATE_LIMIT_MAX_WAIT)
                    except (TypeError, ValueError):
                        wait = 5.0
                    log.warning("论坛限速：%s%s，等待 %.0f 秒后重试", method, path, wait)
                    time.sleep(max(wait, 0.0))
                    continue
                raise DiscourseError(429, "触发论坛频率限制")
            if r.status_code >= 400:
                try:
                    msg = "；".join(r.json().get("errors", [])) or r.text[:300]
                except ValueError:
                    msg = r.text[:300]
                raise DiscourseError(r.status_code, msg)
            return r.json() if r.content else {}
        raise DiscourseError(429, "触发论坛频率限制")

    # ---- 读 ----
    def category(self, category_id: int) -> dict:
        return self._req("GET", f"/c/{category_id}/show.json").get("category", {})

    def topic(self, topic_id: int) -> dict:
        return self._req("GET", f"/t/{topic_id}.json")

    def topic_by_external_id(self, external_id: str) -> dict | None:
        try:
            return self._req("GET", f"/t/external_id/{external_id}.json")
        except DiscourseError as e:
            if e.status == 404:
                return None
            raise

    def post_raw(self, post_id: int) -> str:
        return self._req("GET", f"/posts/{post_id}.json").get("raw", "")

    def first_post_id(self, topic_id: int) -> int:
        return self.topic(topic_id)["post_stream"]["posts"][0]["id"]

    # ---- 社区帖子（首页「社区今日」用） ----
    # gw-ai-pulse（2026-11-16）：日报停用后，首页社区区块改为直接读论坛真实帖子。
    def categories(self) -> list[dict]:
        """全部分类（含 read_restricted 标记），用于决定哪些分类可以扫。"""
        data = self._req("GET", "/categories.json")
        cl = data.get("category_list") or {}
        cats = cl.get("categories") if isinstance(cl, dict) else cl
        return list(cats or [])

    def category_latest(self, category_id: int, limit: int = 8) -> list[dict]:
        data = self._req("GET", f"/c/{category_id}/l/latest.json?page=0")
        return list((data.get("topic_list") or {}).get("topics") or [])[:limit]

    def topic_author(self, topic_id: int) -> str:
        try:
            t = self.topic(topic_id)
        except DiscourseError:
            return ""
        posts = (t.get("post_stream") or {}).get("posts") or []
        return (posts[0].get("username") or "") if posts else ""

    # ---- 写 ----
    def create_topic(self, title: str, raw: str, category_id: int, tags: list[str], external_id: str) -> dict:
        wait = self.interval - (time.time() - self._last_post)
        if wait > 0:
            time.sleep(wait)
        res = self._req("POST", "/posts.json", {"title": title, "raw": raw, "category": category_id,
                                               "tags": tags, "external_id": external_id})
        self._last_post = time.time()
        return {"topic_id": res["topic_id"], "post_id": res["id"]}

    def update_post(self, post_id: int, raw: str, reason: str) -> None:
        self._req("PUT", f"/posts/{post_id}.json", {"post": {"raw": raw, "edit_reason": reason}})

    def update_tags(self, topic_id: int, tags: list[str]) -> None:
        self._req("PUT", f"/t/-/{topic_id}.json", {"tags": tags})

    def tag_topics(self, tag: str, page: int = 0, limit_pages: int = 3) -> list[dict]:
        """按标签取话题（公开列表，不需要额外权限）。

        2026-11-19：主题「集团 AI 能力上新」要收社区同事自己发的内部上新帖，
        这些帖子不在采集器的信号索引里，只能按论坛标签回读。
        标签不存在时 Discourse 返回 404，按空列表处理——配置里可以提前写未来才会用的标签。
        """
        from urllib.parse import quote
        out: list[dict] = []
        for p in range(limit_pages):
            path = f"/tag/{quote(tag, safe='')}.json" + (f"?page={p}" if p else "")
            try:
                data = self._req("GET", path)
            except DiscourseError as e:
                if e.status == 404:
                    return out
                raise
            rows = (data.get("topic_list") or {}).get("topics") or []
            out.extend(rows)
            if len(rows) < 30:
                break
        return out


class FakeDiscourse:
    """内存里的模拟论坛：试运行与测试用，行为按 Discourse 的规则简化实现。"""

    def __init__(self, feed_topic_id: int = 632, feed_raw: str = "", dump_dir: Path | None = None):
        self.topics: dict[int, dict] = {}
        self.posts: dict[int, dict] = {}
        self.by_external: dict[str, int] = {}
        self._next_topic, self._next_post = 1000, 5000
        self.dump_dir = dump_dir
        self.writes = 0
        self.fail_next_create = None   # 测试用：下一次 create 抛出的异常
        self.corrupt_readback = False  # 测试用：读回时返回被改过的正文
        self.base = "https://forum.test"         # 模拟论坛的站点地址（社区帖子链接用）
        self.category_rows: list[dict] = []      # 测试用：/categories.json 的分类列表
        self.community_rows: list[dict] = []     # 测试用：各分类的最新话题
        self.post_authors: dict[int, str] = {}   # 测试用：话题 → 作者
        self.tag_rows: dict[str, list[dict]] = {}  # 测试用：标签 → 话题行（tag_topics 的返回）
        # 底座帖
        self.posts[1] = {"id": 1, "topic_id": feed_topic_id, "raw": feed_raw}
        self.topics[feed_topic_id] = {"id": feed_topic_id, "title": "AI 雷达首页数据", "tags": [], "posts": [1], "category_id": 2}

    def category(self, category_id: int) -> dict:
        return {"id": category_id, "name": "信号"}

    def topic(self, topic_id: int) -> dict:
        t = self.topics.get(topic_id)
        if not t:
            raise DiscourseError(404, "not found")
        return {"id": t["id"], "title": t["title"], "tags": list(t["tags"]),
                "post_stream": {"posts": [{"id": p} for p in t["posts"]]}}

    def topic_by_external_id(self, external_id: str) -> dict | None:
        tid = self.by_external.get(external_id)
        return self.topic(tid) if tid else None

    def post_raw(self, post_id: int) -> str:
        raw = self.posts[post_id]["raw"]
        return raw + "\n（被改动）" if self.corrupt_readback else raw

    def first_post_id(self, topic_id: int) -> int:
        return self.topics[topic_id]["posts"][0]

    def create_topic(self, title, raw, category_id, tags, external_id):
        if self.fail_next_create:
            err, self.fail_next_create = self.fail_next_create, None
            raise err
        if external_id in self.by_external:
            raise DiscourseError(422, "External ID 已被使用")
        tid, pid = self._next_topic, self._next_post
        self._next_topic += 1
        self._next_post += 1
        self.topics[tid] = {"id": tid, "title": title, "tags": list(tags), "posts": [pid], "category_id": category_id}
        self.posts[pid] = {"id": pid, "topic_id": tid, "raw": raw}
        self.by_external[external_id] = tid
        self.writes += 1
        if self.dump_dir:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            (self.dump_dir / f"topic-{tid}.md").write_text(f"# {title}\n\n标签：{', '.join(tags)}\n\n{raw}", encoding="utf-8")
        return {"topic_id": tid, "post_id": pid}

    def update_post(self, post_id, raw, reason):
        # 2026-11-19：帖 ID 不存在时按真实论坛的行为抛 404，而不是 KeyError。
        # 起因：试运行库跨进程累积，合并路径拿到的是上一轮进程建的 post_id，
        # 这里直接 KeyError 把整轮打崩；而生产环境同样情况（帖被删）会返回 404，
        # 被 pipeline 的 except DiscourseError 记成待人工。测试替身必须一样地失败，
        # 否则「合并更新失败」这条兜底路径永远测不到。
        if post_id not in self.posts:
            raise DiscourseError(404, f"话题 {post_id} 不存在（可能是跨进程残留的 ID）")
        self.posts[post_id]["raw"] = raw
        self.writes += 1
        if self.dump_dir:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            (self.dump_dir / f"post-{post_id}.md").write_text(raw, encoding="utf-8")

    def update_tags(self, topic_id, tags):
        self.topics[topic_id]["tags"] = list(tags)
        self.writes += 1

    # ---- 社区帖子（测试用最小实现） ----
    def categories(self) -> list[dict]:
        return list(self.category_rows)

    def category_latest(self, category_id: int, limit: int = 8) -> list[dict]:
        rows = [t for t in self.community_rows if int(t.get("category_id") or 0) == int(category_id)]
        return rows[:limit]

    def topic_author(self, topic_id: int) -> str:
        return self.post_authors.get(topic_id, "")

    def tag_topics(self, tag: str, page: int = 0, limit_pages: int = 3) -> list[dict]:
        return list(self.tag_rows.get(tag, [])) if page == 0 else []
