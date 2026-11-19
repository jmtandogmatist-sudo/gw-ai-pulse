"""可信度按来源规则计算，不交给模型判断。

沿用日报三档：
  green  官方 + 独立：监管机构原文，或「当事方/官方」与至少一个独立来源互证
  yellow 当事方 / 媒体：官方单方发布，或两个以上媒体来源
  red    单一来源：只有一个非官方来源（单篇论文、单家媒体、社区帖子）
"""
from __future__ import annotations

from .dedup import host_of
from .models import SourceRef

INDEPENDENT = {"media", "research"}


def compute_cred(sources: list[SourceRef]) -> str:
    reachable = [s for s in sources if s.reachable] or sources
    tiers = [s.tier for s in reachable]
    hosts = {host_of(s.url) for s in reachable}
    if "authority" in tiers:
        return "green"
    has_official = "official" in tiers
    independent_hosts = {host_of(s.url) for s in reachable if s.tier in INDEPENDENT}
    if has_official and independent_hosts:
        return "green"
    if has_official:
        return "yellow"
    if len(hosts) >= 2:
        return "yellow"
    return "red"


CRED_LABEL = {"green": "官方 + 独立", "yellow": "当事方 / 媒体", "red": "单一来源"}
