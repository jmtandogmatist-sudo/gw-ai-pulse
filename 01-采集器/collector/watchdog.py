"""本轮采集的硬性时间上限（看门狗）。

为什么要它（2026-11-19 实测事故）：08:00 那一轮跑了 43 分钟还没结束，
进程 0% CPU、没有任何打开的 socket，sample 显示主线程停在 time.sleep——
是撞上论坛 admin API key 的每分钟限额（HTTP 429 + Retry-After）后逐次睡眠重试，
一轮里几十次「补发帖人」请求把限额打满，睡眠时间被不断累加。
同一天 12:00 / 16:00 两轮启动后立刻判定「上一轮仍在运行」并跳过（单实例锁），
首页一整天没有更新——比「跑失败」更糟，因为失败至少会告警。

所以除了治因的几处改动（少发请求、请求限速、429 不再久睡）之外，还要有这个兜底：
不管卡在哪一步，超过上限就抛 RoundTimeout，让 run_round 走 finally
（写 runs 表、发告警、释放锁），进程退出，下一轮才能正常跑。

只在主线程可用（signal 的限制）。cmd_run 就在主线程，够用。
"""
from __future__ import annotations

import signal


class RoundTimeout(BaseException):
    """本轮超过 max_seconds 被强制中止。

    刻意继承 BaseException（和 KeyboardInterrupt 同类），不是 Exception：
    一轮采集里到处是「单个信源失败不影响其余」的 except Exception，
    超时刻意不能走那些分支——被它们接住就变成「某信源失败」，这一轮还会继续跑下去，
    看门狗等于白设。（2026-11-19 实测：第一版继承 Exception，
    测试里超时被信源级的 except Exception 吞掉，整轮照样跑完。）
    """


def arm(seconds: float) -> bool:
    """开启看门狗。返回 True 表示已开启；0 或负数表示不设上限。"""
    if not seconds or float(seconds) <= 0:
        return False
    if not hasattr(signal, "SIGALRM"):      # 非 POSIX 平台：没有 alarm，直接跳过
        return False

    def _on_alarm(signum, frame):           # noqa: ANN001
        raise RoundTimeout(f"本轮已运行超过 {int(seconds)} 秒，强制中止")

    try:
        signal.signal(signal.SIGALRM, _on_alarm)
        signal.setitimer(signal.ITIMER_REAL, float(seconds))
    except (ValueError, OSError):
        # 非主线程调用 signal.signal 会抛 ValueError；测试里跑线程时忽略即可
        return False
    return True


def clear() -> None:
    """关掉看门狗。必须放在 finally 里，否则定时器会跟着进程活到下一轮。"""
    if not hasattr(signal, "SIGALRM"):
        return
    try:
        signal.setitimer(signal.ITIMER_REAL, 0)
    except (ValueError, OSError):
        pass
