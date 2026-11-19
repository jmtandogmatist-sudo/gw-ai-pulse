"""补跑判断：断电、关机、断网导致错过采集时间点时，决定是否现在补一轮。

为什么需要这个
--------------
launchd 的 ``StartCalendarInterval`` 只在固定时刻触发，**错过就永久丢失**。
把电脑合上或者拔电，16:00 那轮就没了，而且不会有任何补偿。

配套的 launchd 设置是 ``RunAtLoad: true``：开机或 launchd 重新加载时立刻执行一次
``collector run``。但 RunAtLoad 每次开机都跑，一天开关机五次就会跑五次——
所以真正的判断放在这里，由它决定「这次启动到底要不要真跑」。

判断规则
--------
1. 距上次**成功完成**超过 ``catchup_after_hours``（默认 8 小时）→ 说明错过了至少一轮
2. 但若距**下一个计划时间点**不到 ``catchup_min_gap_minutes``（默认 90 分钟）→ 不跑，
   马上就到点了，现在跑等于重复抓一遍同样的内容

两条都满足才补跑。

实测场景（时刻表 08:00 / 12:00 / 16:00）
----------------------------------------
  16:00 跑完 → 23:00 关机 → 次日 07:00 开机
      距上次 15h > 8h ✓，距下个计划点(08:00) 60min < 90min ✗ → 不补跑，等 08:00
  16:00 跑完 → 18:00 关机 → 次日 10:00 开机
      距上次 18h > 8h ✓，距下个计划点(12:00) 120min > 90min ✓ → 补跑
  08:00 跑完 → 10:00 关机 → 10:30 开机
      距上次 2.5h < 8h ✗ → 不补跑（刚跑过）
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from .sources.fetchers import CST

log = logging.getLogger("collector.catchup")

# 距离下个计划点少于这么多分钟就不补跑，避免刚补完就又到点重复抓
DEFAULT_MIN_GAP_MINUTES = 90
DEFAULT_AFTER_HOURS = 8


def seconds_until_next_run(schedule: dict, now: datetime) -> Optional[int]:
    """距下一个计划时间点还有多少秒。schedule 形如 {"weekdays": ["08:00"], "weekends": [...]}。"""
    now = now.astimezone(CST)
    for add in range(0, 8):
        day = (now + timedelta(days=add)).date()
        times = schedule.get("weekends" if day.weekday() >= 5 else "weekdays") or []
        for t in sorted(times):
            try:
                hh, mm = (int(x) for x in t.split(":"))
            except ValueError:
                continue
            cand = datetime(day.year, day.month, day.day, hh, mm, tzinfo=CST)
            if cand > now:
                return int((cand - now).total_seconds())
    return None


def should_catch_up(
    last_success_iso: Optional[str],
    schedule: dict,
    now: datetime,
    *,
    after_hours: float = DEFAULT_AFTER_HOURS,
    min_gap_minutes: float = DEFAULT_MIN_GAP_MINUTES,
) -> tuple[bool, str]:
    """返回 (是否补跑, 原因说明)。

    last_success_iso 为 None 表示从未成功跑过 —— 这种情况应当补跑（冷启动）。
    """
    now = now.astimezone(CST)

    if not last_success_iso:
        return True, "没有成功完成的采集记录，冷启动补跑"

    try:
        last = datetime.fromisoformat(last_success_iso)
    except (TypeError, ValueError):
        return True, f"上次完成时间无法解析（{last_success_iso}），按补跑处理"

    if last.tzinfo is None:
        last = last.replace(tzinfo=CST)

    gap_hours = (now - last).total_seconds() / 3600
    if gap_hours < after_hours:
        return False, f"距上次成功仅 {gap_hours:.1f} 小时（阈值 {after_hours}h），不补跑"

    nxt = seconds_until_next_run(schedule, now)
    if nxt is not None and nxt < min_gap_minutes * 60:
        return False, (f"距上次成功 {gap_hours:.1f} 小时，但距下个计划点只剩 "
                       f"{nxt // 60} 分钟（< {min_gap_minutes:.0f}），留给定时触发")

    soon = f"{nxt // 60} 分钟后" if nxt is not None else "无计划点"
    return True, f"距上次成功 {gap_hours:.1f} 小时（阈值 {after_hours}h），且下个计划点在 {soon}，补跑"


def _just_past_slot(schedule: dict, now: datetime, *, within_minutes: float = 15) -> bool:
    """当前时刻是否刚过某个计划时间点（说明这次启动是定时器触发的，不是开机触发的）。"""
    now = now.astimezone(CST)
    times = schedule.get("weekends" if now.weekday() >= 5 else "weekdays") or []
    for t in times:
        try:
            hh, mm = (int(x) for x in t.split(":"))
        except ValueError:
            continue
        slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        delta = (now - slot).total_seconds()
        if 0 <= delta <= within_minutes * 60:
            return True
    return False
