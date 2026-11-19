"""日期兜底提取：给 published_at 为空的候选补一个发布日期。

背景（2026-11-16）
--------------------
pipeline 的 72 小时窗口判断写的是::

    if c.published_at and datetime.fromisoformat(c.published_at) < window:

``published_at`` 为空时整段判断被短路，也就是**零日期内容永远不受窗口约束**。
实测后果：一条 2026-10-30 的网信办公告（超窗口 17 天）被当成新信号发布，
另一条 11-05 的中国电力报同样漏过。这类条目来自 ``manual_pages``（政策官网、
GitHub、企业博客），它们天然没有 ``pubDate``。

这里的做法是「从 URL 里抠日期」——最可靠的信号源，因为政策站、新闻站、
GitHub release 页的 URL 普遍自带 ``/2026-10/30/`` 或 ``/202611/t20261105_``
这类日期戳，且不会被页面改版影响。

提取不到日期时返回 None，由调用方决定放行还是丢弃（见 pipeline 的
``undated_policy``）。**不要**在这里瞎猜日期。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .sources.fetchers import CST

# 只认 20xx 年，且必须在合理区间内；防止把 "v2.4.10" 之类的版本号误判成日期
_MIN_YEAR = 2000
_MAX_FUTURE_DAYS = 2      # 允许"明天"（时区误差），再多就是解析错了

# 1) 路径分段形式：/2026-10/30/、/2026/10/30/、/2026-10-30/
_RE_YMD_SEP = re.compile(
    r"(?<!\d)(20\d{2})[-_/.](0?[1-9]|1[0-2])[-_/.](0?[1-9]|[12]\d|3[01])(?!\d)"
)
# 2) 中文新闻站常见的 t20261105_ ／ 20261105 形式
_RE_YMD8 = re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)")
# 3) 年月分段 + 文件名内嵌日期：/202611/t20261105_ 已由上面两条覆盖；
#    这里补 /2026/11/5 这种不补零的
_RE_YM_D1 = re.compile(r"/(20\d{2})/(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])(?:/|$|\?)")


def _safe_dt(y: int, m: int, d: int, now: Optional[datetime] = None) -> Optional[datetime]:
    """构造并校验日期，过滤明显不合理的时间戳。"""
    if y < _MIN_YEAR:
        return None
    try:
        dt = datetime(y, m, d, tzinfo=CST)
    except ValueError:
        return None          # 2026-02-30 这类
    ref = now or datetime.now(CST)
    if dt > ref.replace(hour=23, minute=59, second=59) + _delta_days(_MAX_FUTURE_DAYS):
        return None
    return dt


def _delta_days(n: int):
    from datetime import timedelta
    return timedelta(days=n)


def date_from_url(url: str, *, now: Optional[datetime] = None) -> Optional[str]:
    """从 URL 里提取发布日期，返回北京时间 ISO8601；提不出返回 None。

    优先级：带分隔符的 Y-M-D > 8 位连写 YYYYMMDD > /Y/M/D 路径。
    取**第一个**命中且通过合法性校验的结果——URL 里日期通常只出现在路径中段，
    第一个就是文章日期；后面的（如评论页 id）不是日期。
    """
    if not url:
        return None
    # 只看路径 + query，跳过 scheme//host，避免把域名里的数字当日期
    probe = url
    m_scheme = re.match(r"^[a-zA-Z][\w+.-]*://[^/]+(/.*)?$", url)
    if m_scheme:
        probe = m_scheme.group(1) or ""

    for rx in (_RE_YMD_SEP, _RE_YMD8, _RE_YM_D1):
        for m in rx.finditer(probe):
            dt = _safe_dt(int(m.group(1)), int(m.group(2)), int(m.group(3)), now)
            if dt is not None:
                return dt.isoformat(timespec="seconds")
    return None


def fill_published_at(candidate, *, now: Optional[datetime] = None) -> bool:
    """就地补 candidate.published_at。返回是否补到了。已有值不覆盖。"""
    if getattr(candidate, "published_at", None):
        return True
    iso = date_from_url(getattr(candidate, "url", "") or "", now=now)
    if iso:
        candidate.published_at = iso
        return True
    return False
