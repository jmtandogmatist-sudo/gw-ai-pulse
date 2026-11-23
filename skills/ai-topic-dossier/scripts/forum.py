"""论坛读写小工具（主题档案、技术雷达两个 skill 共用同一份代码）。

凭据：与 ai-radar-daily 共用 .env（DISCOURSE_URL / DISCOURSE_API_KEY / DISCOURSE_API_USER，或 DISCOURSE_API_USERNAME）。
底座帖：只改调用方负责的那一个字段（topics 或 stance），其余字段原样保留；写后回读核对。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

UA = "goldwind-ai-station-skill/1.0"
FEED_BLOCK = re.compile(r"```[a-z]*[ \t]*\r?\n([\s\S]*?)\s*```", re.I)
MACHINE_RE = re.compile(r'\[details="机器可读数据"\]\s*```json\s*(.*?)```', re.S)


class ForumError(RuntimeError):
    pass


def load_env(env_file: Optional[str]) -> Dict[str, str]:
    values = dict(os.environ)
    path = Path(env_file).expanduser() if env_file else Path.cwd() / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            values[k.strip()] = v
    user = values.get("DISCOURSE_API_USER") or values.get("DISCOURSE_API_USERNAME")
    missing = [k for k, v in (("DISCOURSE_URL", values.get("DISCOURSE_URL")), ("DISCOURSE_API_KEY", values.get("DISCOURSE_API_KEY")),
                              ("DISCOURSE_API_USER", user)) if not v]
    if missing:
        raise ForumError(f"缺少论坛凭据：{', '.join(missing)}（检查 .env）")
    return {"url": values["DISCOURSE_URL"].rstrip("/"), "api_key": values["DISCOURSE_API_KEY"], "api_user": user}


class Forum:
    def __init__(self, cfg: Dict[str, str]):
        self.cfg = cfg

    def _req(self, method: str, path: str, body: Optional[dict] = None, raw_text: bool = False):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.cfg["url"] + path, data=data, method=method, headers={
            "User-Agent": UA, "Api-Key": self.cfg["api_key"], "Api-Username": self.cfg["api_user"],
            "Content-Type": "application/json", "Accept": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                    text = resp.read().decode("utf-8", "replace")
                    return text if raw_text else (json.loads(text) if text else {})
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    time.sleep(int(e.headers.get("Retry-After", "15")))
                    continue
                detail = e.read().decode("utf-8", "replace")[:300]
                raise ForumError(f"{method} {path} → HTTP {e.code}：{detail}") from None
        raise ForumError(f"{method} {path} 频率限制")

    # 读
    def topic(self, tid: int) -> dict:
        return self._req("GET", f"/t/{int(tid)}.json")

    def first_post(self, tid: int) -> dict:
        posts = self.topic(tid).get("post_stream", {}).get("posts", [])
        if not posts:
            raise ForumError(f"话题 {tid} 没有帖子")
        return self._req("GET", f"/posts/{posts[0]['id']}.json")

    def tag_topics(self, tag: str, max_pages: int = 5) -> List[dict]:
        out = []
        for page in range(max_pages):
            data = self._req("GET", f"/tag/{tag}.json?page={page}")
            batch = data.get("topic_list", {}).get("topics", [])
            if not batch:
                break
            out += batch
        return out

    def raw(self, tid: int) -> str:
        return self._req("GET", f"/raw/{int(tid)}/1", raw_text=True)

    # 写
    def create_topic(self, title: str, raw: str, category: int, tags: List[str]) -> dict:
        return self._req("POST", "/posts.json", {"title": title, "raw": raw, "category": category, "tags": tags})

    def update_post(self, post_id: int, raw: str, reason: str) -> None:
        self._req("PUT", f"/posts/{int(post_id)}.json", {"post": {"raw": raw, "edit_reason": reason}})


def parse_machine(raw: str) -> dict:
    m = MACHINE_RE.search(raw or "")
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def machine_block(data: dict) -> str:
    return '[details="机器可读数据"]\n```json\n' + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n[/details]"


def write_feed_key(forum: Forum, topic_id: int, key: str, value: Any, reason: str) -> dict:
    """只改底座帖 JSON 里的一个字段。读不到或解析不了就停，不盲写。"""
    post = forum.first_post(topic_id)
    raw = post.get("raw") or ""
    m = FEED_BLOCK.search(raw)
    if not m:
        raise ForumError("底座帖里找不到 JSON 代码块，已停止，未修改")
    feed = json.loads(m.group(1))
    if feed.get("schema") != "air-pulse/1":
        raise ForumError(f"底座帖 schema 不是 air-pulse/1（是 {feed.get('schema')}），已停止，未修改")
    if feed.get(key) == value:
        return {"status": "NOOP", "post_id": post["id"]}
    feed[key] = value
    new_raw = raw[:m.start()] + "```json\n" + json.dumps(feed, ensure_ascii=False, indent=2) + "\n```" + raw[m.end():]
    forum.update_post(post["id"], new_raw, reason)
    back = forum.first_post(topic_id).get("raw") or ""
    bm = FEED_BLOCK.search(back)
    if not bm or json.loads(bm.group(1)).get(key) != value:
        raise ForumError("底座帖写入后读回不一致，请人工核对")
    return {"status": "UPDATED", "post_id": post["id"]}


_TAG_DOMAIN = {
    "energy-ai": "能源AI", "policy": "政策合规", "model-selection": "选型",
    "tools": "工具做法", "compute": "算力", "security": "安全",
    "research": "前沿研究", "industry": "行业动态", "internal": "集团动态",
}
_SRC_LINE_RE = re.compile(r"^\s*-\s*\[([^\]]+)\]\((https?://[^)]+)\)", re.M)
_META_DATE_RE = re.compile(r"事件日期\s+(20\d{2}-\d{2}-\d{2})")
_META_DISC_RE = re.compile(r"采集于\s+(\S+)")
_META_REL_RE = re.compile(r"相关度：([●○]+)")


def signal_from_topic(t: dict, raw: str, base_url: str) -> dict:
    """把采集器发的信号话题转成档案/雷达用的精简条目（兼容旧帖机器块与新帖纯正文）。"""
    mach = parse_machine(raw)
    src = (mach.get("sources") or [{}])[0]
    if not src.get("url"):
        m_src = _SRC_LINE_RE.search(raw or "")
        if m_src:
            src = {"name": m_src.group(1).strip(), "url": m_src.group(2).strip()}
    summ = re.search(r"\*\*摘要\*\*\s*\n\s*\n(.*?)\n\s*\n", raw or "", re.S)
    tags = [x["name"] if isinstance(x, dict) else x for x in t.get("tags") or []]
    m_date = _META_DATE_RE.search(raw or "")
    m_disc = _META_DISC_RE.search(raw or "")
    m_rel = _META_REL_RE.search(raw or "")
    domain = mach.get("domain") or next((_TAG_DOMAIN[x] for x in tags if x in _TAG_DOMAIN), "")
    relevance = mach.get("relevance")
    if relevance is None and m_rel:
        relevance = m_rel.group(1).count("●")
    return {
        "topic_id": t.get("id"),
        "url": f"{base_url}/t/{t.get('slug') or 'topic'}/{t.get('id')}",
        "title": t.get("title", ""),
        "date": (mach.get("event_date") or (m_date.group(1) if m_date else "") or str(t.get("created_at") or "")[:10]),
        "discovered_at": mach.get("discovered_at") or (m_disc.group(1) if m_disc else None) or t.get("created_at"),
        "cred": mach.get("cred") or next((x[5:] for x in tags if x.startswith("cred-")), "red"),
        "review": mach.get("review") or ("reviewed" if "reviewed" in tags else "auto"),
        "questions": mach.get("questions") or [x.upper() for x in tags if re.fullmatch(r"q\d{2}", x)],
        "domain": domain,
        "relevance": relevance,
        "source": src.get("name", ""),
        "source_url": src.get("url", ""),
        "summary": re.sub(r"\s+", " ", summ.group(1)).strip() if summ else "",
    }


def same_body(a: str, b: str) -> bool:
    """回读比对：归一空白后逐字比对。

    【UAT 专用运行环境的本地改动，非原包默认值】
    原实现是 `back.strip() != raw.strip()`，但 Discourse 在 markdown 落库时会把全角空格
    U+3000 归一为半角空格（11-06/11-09 实跑：仅 `ais-meta` 里的全角分隔符触发，
    正文其余部分逐字一致），导致「内容完全正确但字符串不等」被判为
    READBACK_MISMATCH / 抛出「读回不一致」。这里在比对前统一归一空白，
    其余字符仍逐字比对——不放宽任何语义校验，只吸收论坛侧确定的空白规范化行为。
    与 ai-radar-daily/scripts/publish.py 的 _body_digest 保持同一口径。
    """
    norm = lambda s: re.sub(r"[\s\u3000]+", " ", (s or "")).strip()
    return norm(a) == norm(b)
