"""补跑判断（catchup）：断电、关机、断网导致错过时间点时决定是否补一轮。"""
from datetime import datetime

import pytest

from collector.catchup import _just_past_slot, seconds_until_next_run, should_catch_up
from collector.sources.fetchers import CST

SCH = {"weekdays": ["08:00", "12:00", "16:00"],
       "weekends": ["08:00", "12:00", "16:00"]}


def dt(*args):
    return datetime(*args, tzinfo=CST)


# ---------- should_catch_up ----------

def test_cold_start_runs():
    go, why = should_catch_up(None, SCH, dt(2026, 11, 16, 9, 0))
    assert go is True
    assert "冷启动" in why


def test_just_ran_does_not_catch_up():
    """10:30 开机，上次 08:00 刚跑完 —— 不补跑。"""
    go, why = should_catch_up("2026-11-16T08:03:00+08:00", SCH, dt(2026, 11, 16, 10, 30))
    assert go is False
    assert "2.5 小时" in why or "不补跑" in why


def test_missed_overnight_catches_up():
    """16:00 跑完 → 18:00 关机 → 次日 10:00 开机：距下个点 12:00 还有 2 小时，补跑。"""
    go, why = should_catch_up("2026-11-15T16:05:00+08:00", SCH, dt(2026, 11, 16, 10, 0))
    assert go is True
    assert "17.9 小时" in why or "补跑" in why


def test_skips_when_next_slot_is_close():
    """次日 07:00 开机：虽然隔了 15 小时，但 60 分钟后就是 08:00，留给定时触发。"""
    go, why = should_catch_up("2026-11-15T16:05:00+08:00", SCH, dt(2026, 11, 16, 7, 0))
    assert go is False
    assert "下个计划点" in why


def test_threshold_is_configurable():
    """把阈值调到 20 小时，17.9 小时的间隔就不再触发补跑。"""
    go, _ = should_catch_up("2026-11-15T16:05:00+08:00", SCH, dt(2026, 11, 16, 10, 0),
                            after_hours=20)
    assert go is False


def test_unparsable_timestamp_catches_up():
    go, why = should_catch_up("这不是时间", SCH, dt(2026, 11, 16, 9, 0))
    assert go is True
    assert "无法解析" in why


# ---------- seconds_until_next_run ----------

def test_next_run_same_day():
    secs = seconds_until_next_run(SCH, dt(2026, 11, 16, 9, 0))
    assert secs == 3 * 3600          # 09:00 → 12:00


def test_next_run_rolls_to_next_day():
    secs = seconds_until_next_run(SCH, dt(2026, 11, 16, 17, 0))
    assert secs == 15 * 3600         # 17:00 → 次日 08:00


def test_next_run_weekend_uses_same_schedule():
    secs = seconds_until_next_run(SCH, dt(2026, 11, 14, 17, 0))   # 周六
    assert secs == 15 * 3600


# ---------- _just_past_slot（区分定时触发 vs 开机触发）----------

@pytest.mark.parametrize("hour,minute,expect", [
    (8, 0, True),      # 08:00 整点触发
    (8, 12, True),     # 延迟 12 分钟仍算定时触发
    (10, 0, False),    # 开机触发
    (8, 30, False),    # 超过 15 分钟，算开机触发
    (11, 59, False),
])
def test_just_past_slot(hour, minute, expect):
    assert _just_past_slot(SCH, dt(2026, 11, 16, hour, minute)) is expect


def test_just_past_slot_uses_weekend_schedule():
    """周末时刻表与工作日相同时，周六 16:05 也应识别为定时触发。"""
    assert _just_past_slot(SCH, dt(2026, 11, 14, 16, 5)) is True   # 周六
