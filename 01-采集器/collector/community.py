"""AI 社区帖子：扫描、打主题标签、组装首页「社区今日」与「今日一问」。

为什么要单独一套（2026-11-16）：
  采集器原先只处理**外部抓取**的候选，人发的帖子完全进不来。用户确认的做法是
  「原地打标签，不复制」——帖子留在原分类，只是被 LLM 标上主题标签，从而计入
  主题档案的「本周 +N」。同时「社区今日」按论坛真实帖子取数，不再依赖日报编辑推荐。

三条边界：
  1. 信号分类（默认 20）**不扫描**：那里全是采集器自己发的，扫了会自我循环。
  2. 只扫公开可读的分类；staff-only 分类（信号、管理人员）匿名读不到，也不该上首页。
  3. 已扫过的帖子记在 seen_posts 里，**标签只打一次**，避免每轮重复调用模型。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .discourse import DiscourseError

log = logging.getLogger("collector.community")

# 主题档案「本周 +N」按日历周（周一为起点）聚合
def week_key(now: datetime) -> str:
    iso = now.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _clean(s: str, limit: int) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


# 日报话题的两种标题形态（实测生产环境都有）：
#   「本地推理栈熬到 1.0 … |AI雷达11.16」   后缀形式
#   「AI 雷达日报｜11.9」                    前缀形式
# 日报已停用，但这些历史话题仍在分类 15 里，不排除的话会一直霸占「社区今日」
# ——它们是日报产物，不是社区成员发的帖子。
_DAILY_TITLE = re.compile(r"[|｜]\s*AI\s*雷达\s*\d|^AI\s*雷达\s*日报")
# Discourse 每个分类自带一篇「关于“xx”类别」的说明帖，不是成员发的内容
_ABOUT_CATEGORY = re.compile(r"^关于[「“\"'].*[」”\"']类别$")


def is_daily_report_topic(title: str) -> bool:
    return bool(_DAILY_TITLE.search(title or ""))


def _is_about_category(title: str) -> bool:
    return bool(_ABOUT_CATEGORY.match((title or "").strip()))


@dataclass
class CommunityPost:
    topic_id: int
    title: str
    url: str
    who: str = ""
    replies: int = 0
    views: int = 0
    likes: int = 0
    created_at: str = ""
    category_id: int = 0
    tags: list[str] = field(default_factory=list)
    excerpt: str = ""

    def to_feed(self) -> dict:
        """首页「社区今日」条目：{who 标题 url}，与组件 ap-recs 的字段一致。"""
        return {"who": _clean(self.who, 30), "title": _clean(self.title, 120), "url": self.url}


# ---------- 扫描 ----------

# v0.4.6：采集器自己发的话题必须排除，否则「社区新帖」会变成机器人自娱自乐。
# 采集器除了把信号发进分类 20（staff-only，已排除），还会把够格的信号再抄一份到
# 公开分类 15「趋势与情报」（settings.discourse.public_category_id）。15 是公开可读的，
# 扫描时读得到，于是 2026-11-17 实测「社区新帖」5 条里 3 条是采集器自己发的
# （791/792/795，正文是 [wrap=ais-signal] 那套格式），而且同一篇还同时出现在
# 03 信号雷达里——既自我循环又跨块重复。
# 采集器发的每条都带 auto 标签（另有 cred-* / q0x / 领域标签），用它识别最省事。
AUTO_TAG = "auto"


def scan_forum(forum, categories: list[dict], signal_category_id: int,
               *, exclude_ids: set[int] | None = None,
               per_category: int = 8,
               exclude_tags: list[str] | set[str] | None = None,
               with_authors: bool = True) -> list[CommunityPost]:
    """按分类取最新话题，汇总成一个帖子池。

    categories 来自 /categories.json，元素形如 {"id":20,"name":"信号","read_restricted":true}。
    跳过信号分类与只读受限分类；取不到的分类只记日志，不影响其余。
    带 exclude_tags（默认 {"auto"}）的话题同样跳过——那是采集器自己的产出，不是人发的。
    """
    exclude_ids = exclude_ids or set()
    excluded = {int(signal_category_id)} | set(exclude_ids)
    skip_tags = {AUTO_TAG} if exclude_tags is None else set(exclude_tags)
    out: list[CommunityPost] = []
    for c in categories:
        cid = int(c.get("id") or 0)
        if not cid or cid in excluded or c.get("read_restricted"):
            continue
        try:
            topics = forum.category_latest(cid, limit=per_category)
        except DiscourseError as e:
            log.warning("取分类 %s 最新话题失败：%s", cid, e)
            continue
        for t in topics:
            tid = int(t.get("id") or 0)
            title = (t.get("title") or "").strip()
            if not tid or not title or tid in exclude_ids:
                continue
            if is_daily_report_topic(title) or _is_about_category(title):
                continue
            if skip_tags and skip_tags & set(t.get("tags") or []):
                continue        # 采集器自己发的（带 auto 标签）不算社区内容
            slug = t.get("slug") if re.match(r"^[\w-]+$", t.get("slug") or "") else "topic"
            out.append(CommunityPost(
                topic_id=tid,
                title=title,
                url=f"{forum.base}/t/{slug}/{tid}",
                who=((t.get("posters") or [{}])[0].get("username") or "") if t.get("posters") else "",
                replies=max(0, int(t.get("posts_count") or 1) - 1),
                views=int(t.get("views") or 0),
                likes=int(t.get("like_count") or 0),
                created_at=t.get("created_at") or "",
                category_id=cid,
                tags=list(t.get("tags") or []),
            ))
    if with_authors:
        resolve_authors(forum, out)
    return out


def resolve_authors(forum, posts: list[CommunityPost]) -> list[CommunityPost]:
    """补齐发帖人。**只对最终要展示的那几条调用**，别对整个帖子池扫。

    2026-11-19 实测事故：论坛 /c/{id}/l/latest.json 返回的 topic 里**没有 posters 字段**
    （8 个分类 55 条帖子，55 条全缺），于是 scan_forum 末尾这一段对每条都发一次
    topic() 请求 —— 55 次连续请求把 admin API key 的每分钟限额打满，
    之后所有请求都吃 429 + Retry-After 睡眠，一轮跑了 43 分钟还没结束，
    单实例锁被占住，12:00 和 16:00 两轮直接被跳过，首页一整天没更新。

    现在改成：扫描时 with_authors=False，等首页选出真正要显示的那 5 条，
    再对它们单独补作者——请求数从 55 降到 5。
    """
    for p in posts:
        if p.who:
            continue
        try:
            p.who = forum.topic_author(p.topic_id)
        except DiscourseError:
            p.who = ""
    return posts


# ---------- 组装「社区今日」 ----------

def pick_community(posts: list[CommunityPost], *, limit: int = 5,
                   days: int = 7, now: datetime | None = None,
                   order: str = "hot") -> list[CommunityPost]:
    """在近 N 天的帖子池里取前 limit 条。

    order="hot"：按讨论热度降序（回复数 > 点赞数 > 浏览量 > 发帖时间）。
    order="new"：按发帖时间降序（新的在前）——v0.4.6 起首页这块改叫「社区新帖」，
                 新鲜度才是它要表达的东西；「热」由前端另拉近 30 天热门榜负责。

    为什么要有时间窗：一块挂着半年前的帖子会让人觉得社区死了。
    """
    now = now or datetime.now()
    floor = (now - timedelta(days=days)).timestamp()
    pool = []
    for p in posts:
        ts = _parse_ts(p.created_at)
        if ts is not None and ts < floor:
            continue
        pool.append(p)
    if order == "new":
        pool.sort(key=lambda p: -(_parse_ts(p.created_at) or 0.0))
    else:
        pool.sort(key=lambda p: (-p.replies, -p.likes, -p.views, -(_parse_ts(p.created_at) or 0.0)))
    return pool[:limit]


def _parse_ts(iso: str) -> float | None:
    """把论坛返回的时间解析成时间戳；解析不了返回 None（调用方按「不过滤」处理）。"""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def community_items(posts: list[CommunityPost], *, limit: int = 5, days: int = 7,
                    now: datetime | None = None, order: str = "hot") -> list[dict]:
    return [p.to_feed()
            for p in pick_community(posts, limit=limit, days=days, now=now, order=order)]


# ---------- 今日一问 ----------

_ASK_PROMPT = """你是金风科技 AI 社区的运营编辑。下面列出社区最近的帖子标题。
请基于**这些帖子实际在讨论的内容**，提出一个能引发讨论的问题，让同事愿意点进来回复。

要求：
- 一句话，30–60 字，用中文，问句结尾
- 必须和下面某个热点直接相关，不要泛泛而问（不要「你对 AI 怎么看」这类）
- 不要提到"帖子""论坛""下面"这类元信息
- 只输出问题本身，不要编号、不要引号、不要解释

社区最近的帖子：
{items}"""


def build_question(posts: list[CommunityPost], llm, *, limit: int = 8) -> str:
    """根据社区当前热点生成「今日一问」。模型不可用时退回固定规则，不返回空。"""
    if not posts:
        return ""
    tops = posts[:limit]
    items = "\n".join(f"- {p.title}" + (f"（{p.replies} 条回复）" if p.replies else "") for p in tops)
    try:
        text = (llm.chat("你是社区运营编辑，输出严格按要求。", _ASK_PROMPT.format(items=items)) or "").strip()
        text = text.splitlines()[0].strip() if text else ""
        # 只剥「两端成对」的引号，不用 str.strip：它会把「…」剥成 …」，破坏中文书名号/引号配对
        for lq, rq in (("「", "」"), ("“", "”"), ('"', '"'), ("'", "'")):
            if len(text) > 2 and text.startswith(lq) and text.endswith(rq):
                text = text[1:-1].strip()
        if 10 <= len(text) <= 120:
            return text
    except Exception as e:  # noqa: BLE001 —— 模型挂了不能拖垮整轮
        log.warning("生成今日一问失败：%s", e)
    # 退路：用最热那条帖子的标题反问，至少和社区相关
    top = tops[0]
    return _clean(f"「{top.title}」这件事，你们团队是怎么做的？还有哪些坑？", 120)


# ---------- 打标签（原地，不复制） ----------

_TAG_PROMPT = """你是金风科技 AI 情报站的编辑。下面是一批同事在内部 AI 社区发的帖子。
请判断每一条是否属于下面某个主题，只做分类，不改写内容。

可用的主题标签：
{topics}

对每一条输出一个对象：
- idx：输入序号
- topics：这条帖子属于哪些主题，填主题名数组（0–2 个）；都不属于就空数组
- relevance：对金风相关工作者的参考价值 0–3（3=直接影响工作，2=值得相关同事看，1=背景，0=无关）

输出条数必须与输入条数完全一致。只输出一个 JSON 数组。"""


def tag_posts(posts: list[CommunityPost], cfg, llm, store, *, now: datetime | None = None) -> dict:
    """给未打过标签的帖子打主题标签，记进 seen_posts。

    返回 {"scanned": n, "newly_tagged": m, "tagged": {topic_tag: count}}。
    「本周 +N」用的就是这个 tagged 统计，按日历周聚合。
    """
    now = now or datetime.now()
    topics = list((cfg.tags or {}).get("topics") or {})
    stat = {"scanned": len(posts), "newly_tagged": 0, "tagged": {}, "week": week_key(now)}
    if not posts or not topics:
        return stat
    fresh = [p for p in posts if not store.post_seen(p.topic_id)]
    if not fresh:
        return stat
    prompt = _TAG_PROMPT.format(topics="、".join(topics))
    wk = week_key(now)
    for i in range(0, len(fresh), 8):
        batch = fresh[i:i + 8]
        user = "\n".join(json.dumps({"idx": j, "title": p.title}, ensure_ascii=False)
                          for j, p in enumerate(batch))
        by_idx: dict[int, dict] = {}
        try:
            from .tagger import parse_json_array
            arr = parse_json_array(llm.chat(prompt, user))
            by_idx = {int(o.get("idx", -1)): o for o in arr if isinstance(o, dict)}
        except Exception as e:  # noqa: BLE001 —— 模型失败就整批跳过，下轮再试
            log.warning("社区帖子打标签失败：%s", e)
            continue
        for j, p in enumerate(batch):
            o = by_idx.get(j)
            if o is None:
                continue
            picked = [t for t in (o.get("topics") or []) if t in topics][:2]
            tags = [(cfg.tags["topics"][t]) for t in picked]
            store.mark_post_seen(p.topic_id, week=wk, tags=tags, title=p.title, who=p.who)
            stat["newly_tagged"] += 1
            for t in tags:
                stat["tagged"][t] = stat["tagged"].get(t, 0) + 1
    return stat
