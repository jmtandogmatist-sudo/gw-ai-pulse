"""数据结构：候选条目（抓取得到）与信号（打标签后可发布）。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Candidate:
    """一个信源模块抓到的一条原始条目。"""
    module: str            # 信源模块名，如 ext_ai
    source_name: str       # 信源显示名，如 国家能源局
    source_tier: str       # authority / official / media / research / community
    url: str
    title: str
    excerpt: str = ""      # 列表页或 RSS 里的摘要片段，送模型用
    published_at: Optional[str] = None   # ISO8601，北京时间；拿不到就是 None
    heat: Optional[float] = None         # 热度（HN 分数、今日 star、HF trending），没有就是 None
    # 检索词库命中时回填（订阅源条目为 None）：
    retrieval_label: Optional[str] = None   # 命中的检索组中文释义，如「AI 与风电能源」
    retrieval_query: Optional[str] = None   # 实际发出的英文检索词，便于复盘召回质量


@dataclass
class SourceRef:
    name: str
    url: str
    tier: str
    reachable: bool = True


@dataclass
class Signal:
    signal_id: str
    event_key: str
    title: str
    summary: str
    us: str                       # 「对金风」一句，可空
    domain: str
    questions: list[str]
    topics: list[str]             # 主题标签（白名单内）
    relevance: int                # 0-3
    cred: str                     # green / yellow / red
    review: str = "auto"          # auto / reviewed
    event_date: Optional[str] = None
    # gw-ai-pulse（2026-11-17）：原始发布时间，**带时分**（如 2026-11-17T09:30:00+08:00）。
    #
    # 为什么单独存一个字段：event_date 只保留日期（YYYY-MM-DD），因为它是给人看的
    # 「事件哪天发生」。但首页信号雷达要按「时间最新」排序，日期粒度下同一天内
    # 十几条并列、无法区分先后。Candidate.published_at 本来带时分（RSS pubDate、
    # GitHub created_at 都有），却被 (c.published_at or "")[:10] 截掉了。
    # 这里保留一份完整的供排序用；event_date 继续只做展示。
    # 不是所有源都给得到时分（列表页/sitemap 多为日期级），拿不到时为 None，
    # 排序退回日期粒度。
    published_at: Optional[str] = None
    discovered_at: str = ""
    sources: list[SourceRef] = field(default_factory=list)
    topic_id: Optional[int] = None
    post_id: Optional[int] = None
    public_topic_id: Optional[int] = None   # 2026-11-16：同时发一份到公开分类时的分类 15 话题 id

    # gw-ai-pulse（2026-11-19）「今日焦点」：这一条信号被选中做长文（见 collector/focus.py）。
    # 字段随信号一起落库（store.data 的 JSON），首页 02 靠它渲染入口卡，
    # 发帖时靠它切到长文模板 topic_body_focus()。focus=False 的信号行为与以前完全一致。
    focus: bool = False
    focus_deck: str = ""                                     # 首页导语（≤160 字）
    focus_sections: dict = field(default_factory=dict)       # 长文分节 what/data/impact/open
    focus_points: list[str] = field(default_factory=list)    # 3 条要点（首页摘要用）
    focus_claims: list[dict] = field(default_factory=list)   # 已通过逐字校验的事实：text/quote/source
    focus_minutes: int = 0                                   # 预计阅读分钟

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Signal":
        d = dict(d)
        d["sources"] = [SourceRef(**s) for s in d.get("sources", [])]
        return Signal(**d)
