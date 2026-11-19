#!/usr/bin/env python3
"""AI 雷达日报统一编排器（run.py · V3.9.0）

将 ai-radar-daily 八阶段流水线收编为独立子命令，让状态机、门禁、产物路径、
schema 版本自洽。V3.9.0 的核心变化是**把「可以在搜索前发现的检查」前移**，
并把「远端知识库同步」后移到飞书推送成功之后：

- `precheck`：只读环境预检（二维码/群配置/群画像/知识库 Base/服务可达/凭据键位），
  搜索前执行，把二维码缺失、Base 枚举不存在、profile 未授权等问题挡在搜索之前。
- `package`：**只**构建并校验本地知识包（stage 7），不再触碰远端。
- `push-draft`：论坛确认前预生成三群飞书草稿（链接为占位符，不触网）。
- `push`：论坛回读成功后回填真实链接并发送；发送入口 **fail closed** 拒绝占位链接。
- `sync-knowledge`：远端同步（stage 8），**硬门禁**要求所有飞书目标群已 SENT。

设计原则：
1. **冷启动可执行**：每个子命令可独立调用，不依赖外部 Agent 维护状态
2. **状态机自洽**：run-state.json 是唯一真相；schema_version 从 runtime.json 动态读取
3. **幂等可重入**：重复调用同一子命令不会副作用；可 --from-state 续跑
4. **dry-run 安全**：默认 --dry-run 不写盘，便于排错
5. **失败早爆**：stage 状态机维护，阻断而非静默
6. **先推送、后同步**：外部可见动作按「论坛 → 飞书 → 知识库」顺序推进

子命令：precheck / plan / retrieval / review / self-check / publish /
        package / push-draft / push / sync-knowledge / status / accumulate / settle
全跑入口：all（plan→retrieval→review→self-check→停等双确认）

调用示例：
    python scripts/run.py precheck --report-date 2026-09-24
    python scripts/run.py all --report-date 2026-09-24
    python scripts/run.py publish --report-date 2026-09-24 --confirm
    python scripts/run.py package --report-date 2026-09-24 --confirm
    python scripts/run.py push --report-date 2026-09-24 --confirm
    python scripts/run.py sync-knowledge --report-date 2026-09-24 --confirm
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 允许 `python run.py` 从 skill 根或 scripts/ 调用
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
RUNTIME_PATH = SKILL_DIR / "config" / "runtime.json"
SCRIPTS_DIR = SCRIPT_DIR

# 把 scripts/ 加入 path 以复用 common
sys.path.insert(0, str(SCRIPTS_DIR))
try:
    from common import (  # type: ignore
        atomic_write_json,
        load_discourse_config,
        parse_timezone,
        status_payload,
    )
except Exception as exc:  # pragma: no cover
    print(json.dumps({"ok": False, "error": f"无法导入 common: {exc}"}))
    sys.exit(2)

try:
    import holiday_pool as _pool  # type: ignore
    import seen_index as _seen  # type: ignore  # V4.3 已见索引
except Exception as exc:  # pragma: no cover
    print(json.dumps({"ok": False, "error": f"无法导入 holiday_pool/seen_index: {exc}"}))
    sys.exit(2)


def _load_work_dir() -> str:
    """从 runtime.json 读 work_dir，失败则用默认。"""
    try:
        with RUNTIME_PATH.open("r", encoding="utf-8") as f:
            return json.load(f).get("work_dir") or str(SKILL_DIR.parent / "ai-radar-daily")
    except Exception:
        return str(SKILL_DIR.parent / "ai-radar-daily")


def get_work_dir() -> Path:
    """get_work_dir() 延迟加载：env 优先 → runtime.json → skill_dir 父目录默认值。"""
    wd = os.environ.get("AI_RADAR_WORK_DIR") or _load_work_dir()
    return Path(wd).resolve()


# ============================================================
# 状态机定义
# ============================================================

# stage 1-8 与 run-state.json 的 stage id 一一对应
STAGES: List[str] = ["1", "2", "3", "4", "5", "6", "7", "8"]

# stage → 子命令的映射；子命令在推进对应 stage 时调用对应脚本
# V3.9.0 重构：stage 7（本地构包）与 stage 8（远端同步）彻底解耦——
#   package 只构建本地知识包；远端 Base 同步改由 sync-knowledge 独立执行，
#   且必须以「所有飞书目标群已 SENT」为前置条件（先推送、后同步）。
STAGE_COMMAND = {
    "1": "plan",            # plan: 合同 + 去重
    "2": "retrieval",       # retrieval: 检索 + 证据回执
    "3": "retrieval",       # retrieval: 封面图
    "4": "review",          # review: 语义复核
    "5": "self-check",      # self-check: 脚本门禁
    "6": "publish",         # publish: 论坛发布
    "7": "package",         # package: 仅本地构五表包
    "8": "sync-knowledge",  # sync-knowledge: 飞书推送成功后同步远端
}

# 子命令 → 触发的 stage 列表
COMMAND_STAGES = {
    "plan": ["1"],
    "retrieval": ["2", "3"],
    "review": ["4"],
    "self-check": ["5"],
    "publish": ["6"],
    "package": ["7"],
    "sync-knowledge": ["8"],
    "push": [],  # 推送不走 stage，纯副作用
    "all": ["1", "2", "3", "4", "5"],  # 跑到 self-check 停
}

# 阻断门（双确认）
HUMAN_GATES = {
    "WAITING_FORUM_CONFIRMATION": "论坛发布前需用户确认",
    "WAITING_HUMAN_REVIEW": "飞书推送前需用户确认",
}

# 终态
TERMINAL_STATES = {"RUN_COMPLETED", "GATE_FAILED", "PUBLISH_FAILED", "PUSH_FAILED", "SYNC_PENDING"}

# ============================================================
# 占位链接（V3.9.0）：论坛确认前预生成三群飞书草稿
# ============================================================
# 用户批准的三段式：①论坛确认门内先出草稿（链接用占位符，不触网）
# ②论坛写入并回读成功后回填真实 URL/topic_id ③发送入口拒绝一切占位链接。
# 占位符必须带明显语义且不可能与真实论坛 URL 混淆。
FORUM_URL_PLACEHOLDER = "<FORUM_URL_PENDING>"
TOPIC_ID_PLACEHOLDER = "PENDING"
PLACEHOLDER_TOKENS = (FORUM_URL_PLACEHOLDER, "PENDING", "待回填", "placeholder")


def is_placeholder_url(value: Any) -> bool:
    """判断链接是否为占位符（发送前必须为 False，否则 fail closed）。

    只做白名单式判定：必须是非空的 http(s) URL，且不含任何占位 token。
    空串、非 http 前缀、含 PENDING/待回填 等一律视为占位。
    """
    text = str(value or "").strip()
    if not text:
        return True
    if not text.lower().startswith(("http://", "https://")):
        return True
    return any(token in text for token in PLACEHOLDER_TOKENS)


def is_placeholder_topic_id(value: Any) -> bool:
    """判断 topic_id 是否为占位符（真实 topic_id 必为正整数）。"""
    text = str(value or "").strip()
    if not text:
        return True
    if text in PLACEHOLDER_TOKENS:
        return True
    try:
        return int(text) <= 0
    except (TypeError, ValueError):
        return True


def leaked_placeholder_tokens(
    payload: Dict[str, Any], article_url: Any, topic_id: Any
) -> List[str]:
    """扫描 payload 中**真正会发送出去**的内容是否残留占位 token。

    V3.9.1 修正：原实现在 cmd_push 里 `json.dumps(payload)` 后整串扫 token，而 V3.9.0
    新增的结构性字段名 `placeholder` / `placeholder_url` 自身就含 "placeholder"，
    导致每期推送都被误判为占位泄漏（2026-09-24 实测 PLACEHOLDER_LINK_BLOCKED）。
    这里只扫描群消息体、主摘要、主标题与 article_url/topic_id——即实际外发内容，
    结构性字段名一律不参与判定，同时保持 fail closed。
    """
    parts: List[str] = [str(article_url), str(topic_id)]
    for item in payload.get("payloads") or []:
        if item.get("skip"):
            continue
        for key in ("message", "main_summary", "main_title"):
            value = item.get(key)
            if isinstance(value, str):
                parts.append(value)
    blob = "\n".join(parts)
    return [token for token in PLACEHOLDER_TOKENS if token in blob]


# ============================================================
# 工具
# ============================================================

def now_iso() -> str:
    """当前时间（带时区）ISO8601。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def record_confirmation(state: Dict[str, Any], key: str, payload: Dict[str, Any]) -> None:
    """幂等登记人工确认；兼容 confirmations 的 dict 与 list 两种历史形态。

    V5.3.2 起 run-state 的 confirmations 以 dict（键=确认类型）为准，但历史期次与
    部分人工对账脚本写入的是 list（元素带 type 字段）。旧代码只按 list 遍历，
    遇到 dict 会抛 `'str' object has no attribute 'get'`，且发生在外部写动作之后，
    导致状态丢失（2026-11-11 首页数据阶段实测）。
    """
    conf = state.get("confirmations")
    if isinstance(conf, list):
        if not any(isinstance(c, dict) and c.get("type") == key for c in conf):
            conf.append(dict(payload, type=key))
        return
    if not isinstance(conf, dict):
        conf = {}
        state["confirmations"] = conf
    if key not in conf:
        conf[key] = payload


def load_runtime() -> Dict[str, Any]:
    """从 runtime.json 读 schema_version 和所有必要路径。"""
    with RUNTIME_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def schema_version() -> str:
    """动态 schema_version：始终取 runtime.json 的版本。"""
    return load_runtime().get("version", "0.0.0")


def issue_dir_for(report_date: str) -> Path:
    """AI-RADAR-<date> 目录。"""
    return get_work_dir() / f"AI-RADAR-{report_date}"


def artifact_path_for(report_date: str, name: str) -> Path:
    """当期所有 artifact 根目录。"""
    return get_work_dir() / f"AI雷达日报_{report_date}_{name}"


def issue_id_for(report_date: str) -> str:
    return f"AI-RADAR-{report_date}"


def work_files(report_date: str) -> Dict[str, Path]:
    """当期所有产物路径表。"""
    return {
        "contract": get_work_dir() / f"AI-RADAR-{report_date}" / "run-contract.json",
        "state": get_work_dir() / f"AI-RADAR-{report_date}" / "run-state.json",
        "publish_state": get_work_dir() / f"AI-RADAR-{report_date}" / "publish-state.json",
        "md": get_work_dir() / f"AI雷达日报_{report_date}.md",
        "cover": get_work_dir() / f"AI雷达日报_{report_date}_封面.png",
        "search_log": get_work_dir() / f"AI雷达日报_{report_date}_检索清单.json",
        "evidence": get_work_dir() / f"AI雷达日报_{report_date}_证据回执.json",
        "semantic": get_work_dir() / f"AI雷达日报_{report_date}_语义复核.json",
        "self_check": get_work_dir() / f"AI雷达日报_{report_date}_自查报告.md",
        "preflight_report": get_work_dir() / f"AI-RADAR-{report_date}" / "preflight-report.json",
        "recent_dedup": get_work_dir() / f"AI雷达日报_{report_date}_近期去重.json",
        "knowledge_package": get_work_dir() / f"AI-RADAR-{report_date}" / "knowledge-package" / "package.json",
        "feishu_push_json": get_work_dir() / f"AI雷达日报_{report_date}_飞书推送.json",
        "homepage_feed": get_work_dir() / f"AI雷达日报_{report_date}_首页数据.json",
        "homepage_feed_state": get_work_dir() / f"AI-RADAR-{report_date}" / "homepage-feed-state.json",
    }


def empty_run_state(report_date: str) -> Dict[str, Any]:
    """新建一份 run-state.json，schema_version 从 runtime 读。"""
    return {
        "schema_version": schema_version(),
        "issue_id": issue_id_for(report_date),
        "revision": 0,
        "contract": {},
        "run_status": "INIT",
        "current_stage": 0,
        "stages": {
            sid: {"status": "PENDING", "started_at": None, "finished_at": None,
                  "artifacts": [], "error": None}
            for sid in STAGES
        },
        "artifacts": {},
        "gates": {"G1": "PENDING", "G2": "PENDING", "G3": "PENDING"},
        "owner_session": None,
        "heartbeat": None,
        "forum": {"status": "PENDING"},
        "knowledge_sync": {"status": "PENDING"},
        "candidates_sync": {"status": "PENDING"},
        "feishu": {"review_status": "PENDING"},
        "confirmations": [],
        "errors": [],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def load_run_state(report_date: str) -> Tuple[Dict[str, Any], Path]:
    """读取现有 run-state.json；不存在则初始化。"""
    state_path = work_files(report_date)["state"]
    if state_path.exists():
        with state_path.open("r", encoding="utf-8") as f:
            return json.load(f), state_path
    state = empty_run_state(report_date)
    state["issue_dir"] = str(state_path.parent)
    return state, state_path


# ============================================================
# 会话互斥（V3.2.3）：owner_session + heartbeat 显式锁
# 背景：2026-09-07 双会话并发写同一期产物，自创模块/程序化封面/大事件不足
# 均由此放大。规则：run-state 已有 owner_session 且不同于当前会话、且
# heartbeat 新鲜（< SESSION_STALE_SECONDS）时，阻断一切状态变更命令；
# --takeover 可强制接管（接管前应按 SKILL.md 阶段 1 做结构 diff 与外部核验）。
# ============================================================
SESSION_STALE_SECONDS = 600  # heartbeat 超过 10 分钟视为会话已停滞
CURRENT_SESSION: str = "default"


def resolve_session_id(args: argparse.Namespace) -> str:
    return (
        getattr(args, "session_id", None)
        or os.environ.get("AI_RADAR_SESSION_ID")
        or "default"
    )


def validate_report_date(value: str, timezone: str = "Asia/Shanghai",
                         today: Optional[str] = None) -> Optional[str]:
    """校验 report_date，非法时返回错误信息（None 表示合法）。

    防呆护栏：拒绝格式错误、且拒绝明显异常的年份——
    - 过旧：早于 2020（防止历史残留日期被误当新刊锚点）
    - 过未来：晚于"今天 + 1 天"（防止假日期/测试日期污染目录）
    允许 future-backfill 场景 today+1 的少量余量。
    """
    from datetime import date as _date, timedelta
    try:
        d = datetime.strptime(str(value), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return f"report_date 必须为 YYYY-MM-DD，实际: {value}"
    if d.year < 2020:
        return f"report_date 年份异常过旧（{d.year}），疑似测试/残留日期，已拒绝: {value}"
    ref = _date.fromisoformat(today) if today else datetime.now(parse_timezone(timezone)).date()
    if d > ref + timedelta(days=1):
        return f"report_date 超过今日+1，疑似假日期，已拒绝: {value}"
    return None


# ============================================================
# 中国法定工作日硬校验（V3.7.0，节假日 + 调休补班）
# ============================================================
# 来源：国务院办公厅《关于2026年部分节假日安排的通知》（国办发明电〔2025〕7号）。
# 表按年份分桶（key=年份，value={MM-DD 集合}），未录入的年份回退到"仅星期"判定——
# 普通工作日放行、普通周末结束，避免跨年错配；新增年份须人工维护本表。
#
# 两个集合语义不同：
#   CHINA_HOLIDAYS        = 法定休假日（含调休后放假的所有日期，含周末）→ normal 停刊
#   CHINA_MAKEUP_WORKDAYS = 周末调休补班上班日 → normal 放行（补班出刊）
CHINA_HOLIDAYS: Dict[int, set] = {
    2026: {
        # 元旦：1/1(四)~1/3(六)
        "01-01", "01-02", "01-03",
        # 春节：2/15(日)~2/23(一)
        "02-15", "02-16", "02-17", "02-18", "02-19", "02-20", "02-21", "02-22", "02-23",
        # 清明节：4/4(六)~4/6(一)
        "04-04", "04-05", "04-06",
        # 劳动节：5/1(五)~5/5(二)
        "05-01", "05-02", "05-03", "05-04", "05-05",
        # 端午节：6/19(五)~6/21(日)
        "06-19", "06-20", "06-21",
        # 中秋节：9/25(五)~9/27(日)
        "09-25", "09-26", "09-27",
        # 国庆节：10/1(四)~10/7(三)
        "10-01", "10-02", "10-03", "10-04", "10-05", "10-06", "10-07",
    },
}
CHINA_MAKEUP_WORKDAYS: Dict[int, set] = {
    2026: {
        # 元旦调休补班 1/4(日)；春节补班 2/14(六)、2/28(六)
        "01-04", "02-14", "02-28",
        # 劳动节补班 5/9(六)；国庆节补班 9/20(日)、10/10(六)
        "05-09", "09-20", "10-10",
    },
}


# ============================================================
# 外部日历数据源（calendar-cn，2026-10-08 接入）
# ============================================================
# 单一数据源治理：优先读 ~/.workbuddy/skills/calendar-cn/data/<year>.json
# （国务院原文口径，完整 YYYY-MM-DD + 调休补班，随年度通知更新），
# 读不到才回退到上面的内置 MM-DD 表。
#
# 背景：内置表按年分桶且只录了 2026，跨年后会静默退化为「仅按星期」判定
# （2027 元旦本是休假日却会判为 workday）。接入后新增年份无需改本文件，
# 只需跑 calendar-cn 的 update_holidays.py 更新数据。
# gw-ai-pulse（2026-11-13 断链改造）：节假日数据改为环境内自带
# skills/ai-radar-daily/assets/calendar-cn/，不再读 skill 目录之外的 ~/.workbuddy。
# 数据可被环境变量 AI_RADAR_CALENDAR_DIR 覆盖；两者都不存在时回退到内置 MM-DD 表。
CALENDAR_CN_DIR = Path(os.environ.get("AI_RADAR_CALENDAR_DIR")
                       or (Path(__file__).resolve().parents[1] / "assets" / "calendar-cn"))
_CALENDAR_CN_CACHE: Dict[int, Any] = {}


def _load_calendar_cn(year: int):
    """返回 (holidays_set, makeup_set)，元素均为 YYYY-MM-DD；不可用返回 None。"""
    if year in _CALENDAR_CN_CACHE:
        return _CALENDAR_CN_CACHE[year]
    import json as _json
    from datetime import date as _cn_date

    result = None
    try:
        p = CALENDAR_CN_DIR / ("%d.json" % year)
        if p.exists():
            with p.open("r", encoding="utf-8") as fh:
                doc = _json.load(fh)
            hol, mkp = set(), set()
            for h in doc.get("holidays") or []:
                cur = _cn_date.fromisoformat(str(h["start"]))
                end = _cn_date.fromisoformat(str(h["end"]))
                while cur <= end:
                    hol.add(cur.isoformat())
                    cur = cur.fromordinal(cur.toordinal() + 1)
            for w in doc.get("workdays") or []:
                mkp.add(str(w["date"]))
            if hol:
                result = (hol, mkp)
    except Exception:
        result = None  # 外部数据异常一律静默回退，不影响主流程
    _CALENDAR_CN_CACHE[year] = result
    return result


def china_workday_status(report_date: str) -> str:
    """判定 report_date 的中国法定工作日状态，返回四态之一：
    - "off"     法定休假日（落上班日或周末都停）→ normal 应停刊
    - "makeup"  周末调休补班上班日 → normal 应放行出刊
    - "weekend" 普通周末 → normal 应停刊
    - "workday" 普通工作日 → normal 放行

    数据源优先级：calendar-cn 外部数据（国务院原文）> 内置 MM-DD 表 > 仅星期。
    """
    d = datetime.strptime(str(report_date), "%Y-%m-%d").date()
    iso = d.isoformat()

    ext = _load_calendar_cn(d.year)
    if ext:
        holidays, makeup = ext
        if iso in holidays:
            return "off"
        if iso in makeup:
            return "makeup"
        if d.weekday() >= 5:
            return "weekend"
        return "workday"

    # 回退：内置 MM-DD 表（未录入年份退化为仅星期判定）
    mmdd = d.strftime("%m-%d")
    holidays = CHINA_HOLIDAYS.get(d.year, set())
    makeup = CHINA_MAKEUP_WORKDAYS.get(d.year, set())
    if mmdd in holidays:
        return "off"
    if mmdd in makeup:
        return "makeup"
    if d.weekday() >= 5:
        return "weekend"
    return "workday"


# ============================================================
# 假日累积 → 工作日统一发布（V3.8.0）
# ============================================================
# 用户口径（2026-09-21 定稿）：
#   1. 检索每天恒定执行（不看日历），候选进累积池；
#   2. 发布触发唯一依据 = 是否工作日（china_workday_status）；
#   3. 非工作日不发布，候选留池逐日累积；
#   4. 到工作日统一发布：按重要度排序取前 30 条（30 为上限，不足全量），
#      并附「全部资讯链接」（落飞书知识库）供查阅全集。
# 语义要点：workday/makeup 视为"可发布日"，off/weekend 视为"累积日"。
# ============================================================

PUBLISHABLE_STATUSES = {"workday", "makeup"}


def _today_local(timezone_name: str = "Asia/Shanghai") -> str:
    """当前真实日期 YYYY-MM-DD（指定时区）。用于老化剔除等以"现在"为基准的判断。"""
    from datetime import date as _date
    try:
        return datetime.now(parse_timezone(timezone_name)).date().isoformat()
    except Exception:
        return _date.today().isoformat()


def is_publishable_day(report_date: str) -> bool:
    """该日是否为"可发布日"（工作日或调休补班日）。"""
    return china_workday_status(report_date) in PUBLISHABLE_STATUSES


def accumulation_due(work_dir: Path, report_date: str) -> Dict[str, Any]:
    """判断当前 report_date 是否存在"待结算的累积发布"。

    返回：
      due         是否应触发统一发布
      reason      原因码
      pending     待发布候选数
      span        累积跨度（最早~最晚检索日）
    """
    pool = _pool.load_pool(work_dir)
    pend = _pool.pending_candidates(pool)
    publishable = is_publishable_day(report_date)
    out = {
        "due": False,
        "reason": "NO_PENDING",
        "pending": len(pend),
        "span": _pool.pool_summary(pool)["pending_span"],
        "workday_status": china_workday_status(report_date),
    }
    if not publishable:
        out["reason"] = "NOT_WORKDAY"
        return out
    # 工作日：若有历史累积（待发布候选来自更早的检索日），触发统一发布
    earlier = [c for c in pend if str(c.get("collected_on", "")) < report_date]
    if earlier:
        out["due"] = True
        out["reason"] = "ACCUMULATED_PUBLISH"
        out["earlier_count"] = len(earlier)
    else:
        out["reason"] = "SINGLE_DAY"
    return out


def cmd_accumulate(args: argparse.Namespace) -> int:
    """把当期检索清单的候选追加进累积池（检索通道，恒定执行，不看日历）。

    命令：python run.py accumulate --report-date YYYY-MM-DD
    副作用：写 <work_dir>/candidates-pool.json（dry-run 不写）。
    """
    work_dir = get_work_dir()
    search_log = work_files(args.report_date)["search_log"]
    if not search_log.exists():
        emit("error", "NO_SEARCH_LOG", f"未找到检索清单: {search_log}")
        return 2
    with search_log.open("r", encoding="utf-8") as f:
        log = json.load(f)
    candidates = log.get("candidates") or []
    pool = _pool.load_pool(work_dir)
    _pool.append_candidates(pool, args.report_date, candidates)
    # 老化剔除以"真实当天"为参照，避免误删历史累积（勿传 report_date）
    _pool.prune_old(pool, _today_local(args.timezone))
    _pool.save_pool(work_dir, pool, dry_run=args.dry_run)
    if not args.dry_run:
        # V4.3：全部候选（含 REJECTED）写入已见索引，供后续期次三级查重
        with _seen.connect(work_dir) as conn:
            _seen.record_candidates(conn, args.report_date, candidates, issue_id=issue_id_for(args.report_date))
    if not log.get("seen_index_dedup"):
        emit("warn", "DEDUP_NOT_RUN", "检索清单未经 run.py dedup 查重即入池；建议先跑 dedup 再 accumulate",
             report_date=args.report_date)
    summary = _pool.pool_summary(pool)
    emit("ok", "ACCUMULATED",
         f"{args.report_date} 候选 {len(candidates)} 条已入池",
         report_date=args.report_date,
         pool=summary,
         publishable=is_publishable_day(args.report_date))
    print(json.dumps({"pool": summary, "due": accumulation_due(work_dir, args.report_date)},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_settle(args: argparse.Namespace) -> int:
    """工作日统一发布后结算累积池（V4.3：真正按重要度取前 30，按 uid 精确落账）。

    命令：python run.py settle --report-date YYYY-MM-DD --confirm [--selected-ids uid1,uid2|NEWS-01,...]
    规则：
      - 传 --selected-ids：只结算这些候选（可用 uid，或本期检索清单 id，后者按 report_date 补成 uid）；
      - 不传：取 holiday_pool.ranked_pending()（仅 SELECTED，importance 降序）前 30；
      - 结算后同步把已见索引中对应 uid 标为 published。
    """
    work_dir = get_work_dir()
    if not is_publishable_day(args.report_date):
        emit("warn", "NOT_PUBLISHABLE",
             f"{args.report_date} 非可发布日，不结算",
             workday_status=china_workday_status(args.report_date))
        return 4
    pool = _pool.load_pool(work_dir)
    pend = _pool.pending_candidates(pool)
    if not pend:
        emit("warn", "NOTHING_TO_SETTLE", "累积池无待发布候选")
        return 0
    raw_ids = [x.strip() for x in (getattr(args, "selected_ids", None) or "").split(",") if x.strip()]
    if raw_ids:
        wanted = {i if "::" in i else f"{args.report_date}::{i}" for i in raw_ids}
        selected = [c for c in pend if c.get("uid") in wanted]
        missing = sorted(wanted - {c.get("uid") for c in selected})
        if missing:
            emit("error", "SELECTED_IDS_NOT_FOUND", f"{len(missing)} 个 id 不在待发布池中", missing=missing)
            return 2
    else:
        selected = _pool.ranked_pending(pool, _pool.MAX_BATCH_SIZE)
    # V5：本期及之前累积的跟踪/雷达候选随正文一并结算（不占精编 30 条上限），避免下期重复上雷达
    side = [c for c in pend
            if str(c.get("decision") or "").upper() in ("TRACKING", "SIGNAL")
            and str(c.get("collected_on") or "") <= args.report_date
            and c not in selected]
    core_n = len(selected)
    selected = selected + side
    if args.dry_run:
        emit("info", "SETTLE_DRY_RUN",
             f"将结算 {len(selected)} 条：精编 {core_n}（上限 {_pool.MAX_BATCH_SIZE}）+ 跟踪/雷达 {len(side)}",
             report_date=args.report_date, count=len(selected), core=core_n, side=len(side))
        print(json.dumps({"would_settle": len(selected),
                          "items": [{"uid": c.get("uid"), "importance": c.get("importance"),
                                     "decision": c.get("decision"), "title": c.get("title")} for c in selected]},
                         ensure_ascii=False, indent=2))
        return 0
    if not getattr(args, "confirm", False):
        emit("error", "CONFIRM_REQUIRED", "settle 是结算动作，需 --confirm")
        return 3
    _pool.settle_pool(pool, args.report_date, selected)
    _pool.save_pool(work_dir, pool)
    with _seen.connect(work_dir) as conn:
        marked = _seen.mark_published(conn, [c.get("uid") for c in selected], issue_id_for(args.report_date))
    emit("ok", "SETTLED", f"已结算 {len(selected)} 条（上限 {_pool.MAX_BATCH_SIZE}）",
         report_date=args.report_date, count=len(selected), seen_index_marked=marked)
    return 0


def cmd_dedup(args: argparse.Namespace) -> int:
    """V4.3：检索清单入池前的三级查重（已见索引 + 可选论坛近期数据）。

    命令：python run.py dedup --report-date YYYY-MM-DD [--recent-data <fetch_recent 输出>] [--dry-run]
    副作用：SELECTED 中命中重复的改判 REJECTED_DUPLICATE（原子回写检索清单），
            输出 AI雷达日报_YYYY-MM-DD_去重报告.json。
    """
    work_dir = get_work_dir()
    search_log = work_files(args.report_date)["search_log"]
    if not search_log.exists():
        emit("error", "NO_SEARCH_LOG", f"未找到检索清单: {search_log}")
        return 2
    log = json.loads(search_log.read_text(encoding="utf-8"))
    bad_keys = _seen.generic_event_keys(log.get("candidates"))
    if bad_keys:
        emit("error", "EVENT_KEY_GENERIC",
             f"{len(bad_keys)} 条候选的 event_key 是逐日编号（如 signal-1），会与往期撞号；请按内容重写后再查重",
             examples=[f"{i}:{k}" for i, k in bad_keys[:5]])
        return 2
    with _seen.connect(work_dir) as conn:
        recent = getattr(args, "recent_data", None)
        if recent:
            data = json.loads(Path(recent).read_text(encoding="utf-8"))
            seeded = _seen.seed_published(conn, f"recent-{data.get('report_date', args.report_date)}",
                                          data.get("titles") or [], data.get("urls") or [])
            emit("info", "SEEN_SEEDED_FROM_RECENT", f"已把论坛近期数据 {seeded} 条并入索引")
        report = _seen.dedup_search_log(conn, args.report_date, log)
    out = artifact_path_for(args.report_date, "去重报告.json")
    if not args.dry_run:
        atomic_write_json(str(search_log), log)
        atomic_write_json(str(out), report)
    emit("ok", "DEDUP_DONE",
         f"查重 {report['total']} 条：新 {report['new']} / 重复 {report['duplicate']} / 跟踪 {report['update']}",
         report_date=args.report_date, report=str(out), dry_run=bool(args.dry_run))
    return 0


def cmd_seen_seed(args: argparse.Namespace) -> int:
    """V4.3：冷启动——把 fetch_recent 拉到的论坛已发布期次灌入已见索引（published）。"""
    work_dir = get_work_dir()
    recent = getattr(args, "recent_data", None)
    if not recent or not Path(recent).exists():
        emit("error", "NO_RECENT_DATA", "需 --recent-data 指向 fetch_recent.py 的输出 JSON")
        return 2
    data = json.loads(Path(recent).read_text(encoding="utf-8"))
    if args.dry_run:
        emit("info", "SEED_DRY_RUN", f"将灌入 {len(data.get('titles') or [])} 标题 / {len(data.get('urls') or [])} URL")
        return 0
    with _seen.connect(work_dir) as conn:
        n = _seen.seed_published(conn, f"recent-{data.get('report_date', args.report_date)}",
                                 data.get("titles") or [], data.get("urls") or [])
        summ = _seen.summary(conn)
    emit("ok", "SEEN_SEEDED", f"已灌入 {n} 条", seen=summ)
    return 0


def missed_retrieval_dates(work_dir: Path, report_date: str) -> Dict[str, Any]:
    """扫描累积池，返回 last_batch_date+1 .. report_date-1 之间缺失检索的日期。"""
    from datetime import date as _date, timedelta as _td
    pool = _pool.load_pool(work_dir)
    dates = sorted(str(b.get("report_date")) for b in pool.get("batches", []) if b.get("report_date"))
    rd = datetime.strptime(report_date, "%Y-%m-%d").date()
    prior = [d for d in dates if d < report_date]
    if not prior:
        return {"last_batch_date": None, "missed_dates": [], "note": "池中无更早批次（首次运行或已老化），不判缺跑"}
    last = datetime.strptime(prior[-1], "%Y-%m-%d").date()
    have = set(dates)
    missed = []
    cur = last + _td(days=1)
    while cur < rd:
        if cur.isoformat() not in have:
            missed.append(cur.isoformat())
        cur += _td(days=1)
    return {"last_batch_date": prior[-1], "missed_dates": missed}


def cmd_recover_missed(args: argparse.Namespace) -> int:
    """V4.3 脚本化缺跑扫描（原为手工待办）。

    退出码：0=无缺失；5=存在 MISSED_RETRIEVAL（输出待补检日期，执行 Agent 须逐日检索 +
    `run.py dedup/accumulate --report-date <缺失日>` 后再进入当日发布）。
    """
    work_dir = get_work_dir()
    res = missed_retrieval_dates(work_dir, args.report_date)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if res["missed_dates"]:
        emit("warn", "MISSED_RETRIEVAL", f"发现 {len(res['missed_dates'])} 个缺失检索日，须先补检入池",
             missed_dates=res["missed_dates"],
             hint="补检完成后在检索清单 publication_metadata 写 recovery_reason=missed_runs 与 missed_dates")
        return 5
    emit("ok", "NO_MISSED_RETRIEVAL", "无缺失检索日", last_batch_date=res["last_batch_date"])
    return 0


def heartbeat_fresh(state: Dict[str, Any]) -> bool:
    hb = state.get("heartbeat")
    if not hb:
        return False
    try:
        hb_dt = datetime.fromisoformat(hb)
        now_dt = datetime.now(tz=hb_dt.tzinfo) if hb_dt.tzinfo else datetime.now()
        return (now_dt - hb_dt).total_seconds() < SESSION_STALE_SECONDS
    except ValueError:
        return False


def check_session_exclusive(args: argparse.Namespace) -> Optional[str]:
    """返回阻断原因；None 表示放行。status 与 dry-run 不阻断。"""
    if getattr(args, "dry_run", False):
        return None
    state_path = work_files(args.report_date)["state"]
    if not state_path.exists():
        return None
    try:
        with state_path.open("r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return None
    owner = state.get("owner_session")
    if not owner or owner == CURRENT_SESSION:
        return None
    if not heartbeat_fresh(state):
        return None  # 对方已停滞，允许接管
    if getattr(args, "takeover", False):
        return None
    return (
        f"并行会话检测：run-state 由会话 {owner} 持有，heartbeat "
        f"{state.get('heartbeat')} 仍在 {SESSION_STALE_SECONDS // 60} 分钟新鲜期内。"
        "请等待对方会话完成或静默，或确认可接管后加 --takeover（接管前按 SKILL.md 阶段 1 "
        "完成模板结构 diff、产物重读与外部状态核验）。"
    )


def save_run_state(state: Dict[str, Any], state_path: Path, dry_run: bool = False) -> None:
    state["updated_at"] = now_iso()
    state["owner_session"] = CURRENT_SESSION
    state["heartbeat"] = now_iso()
    if dry_run:
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(state_path, state)


def set_stage(state: Dict[str, Any], sid: str, status: str, **fields: Any) -> None:
    stage = state["stages"].setdefault(sid, {})
    stage["status"] = status
    stage["updated_at"] = now_iso()
    if status == "RUNNING":
        stage["started_at"] = stage.get("started_at") or now_iso()
    if status in {"PASS", "FAIL", "SKIPPED"}:
        stage["finished_at"] = now_iso()
    for k, v in fields.items():
        stage[k] = v
    if status == "PASS" and "error" not in fields:
        stage["error"] = None
    if sid.isdigit() and status == "PASS":
        state["current_stage"] = max(int(state.get("current_stage", 0)), int(sid))


def run_subprocess(cmd: List[str], dry_run: bool, env_extra: Optional[Dict[str, str]] = None) -> int:
    """执行子进程；dry-run 打印不执行。"""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    if dry_run:
        return_json = {"dry_run": True, "cmd": " ".join(cmd)}
        print(json.dumps(return_json, ensure_ascii=False, indent=2))
        return 0
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def emit(level: str, code: str, message: str, **fields: Any) -> None:
    """统一状态输出。"""
    payload = status_payload(level.lower(), code, message, **fields)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_preflight_summary(report_path: Path, rc: int) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "status": "PASS" if rc == 0 else "FAIL",
        "report": str(report_path),
        "exit_code": rc,
        "checked_at": now_iso(),
    }
    if report_path.exists():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            summary.update({
                "status": payload.get("status", summary["status"]),
                "error_count": payload.get("error_count", 0),
                "event_count": payload.get("event_count", 0),
                "errors_preview": payload.get("errors", [])[:5],
                "package_dry_run": payload.get("package_dry_run"),
                "sha256": _file_sha256(report_path),
            })
        except (OSError, json.JSONDecodeError):
            summary["error_count"] = 1
            summary["errors_preview"] = [{"code": "PREFLIGHT_REPORT_INVALID", "message": "preflight-report.json 不可读"}]
    return summary


def record_preflight_summary(state: Dict[str, Any], report_path: Path, rc: int) -> None:
    state["preflight"] = _load_preflight_summary(report_path, rc)


def run_preflight_gate(args: argparse.Namespace, f: Dict[str, Path], *, package_dry_run: bool = False) -> int:
    """发布前总装预检：正文、语义复核、检索清单三方一致性。"""
    cmd = [
        sys.executable, str(SCRIPTS_DIR / "preflight_check.py"),
        str(f["md"]),
        "--semantic-review", str(f["semantic"]),
        "--search-log", str(f["search_log"]),
        "--report-date", args.report_date,
        "--timezone", args.timezone,
        "--report", str(f["preflight_report"]),
    ]
    if package_dry_run:
        cmd.extend([
            "--self-check-report", str(f["self_check"]),
            "--publish-state", str(f["publish_state"]),
            "--run-state", str(f["state"]),
            "--package-dry-run",
        ])
    return run_subprocess(cmd, dry_run=args.dry_run)


# ============================================================
# 只读预检（V3.9.0）：把「可在搜索前发现的环境问题」全部前移
# ============================================================
# 背景（2026-09-23 第 22 期耗时归因）：单期约 114 分钟里，可避免返工约 20 分钟，
# 其中发布前才发现二维码映射错位（阻断级）、Base 单选枚举不存在（写入失败）、
# 飞书 profile/scope 缺失、群画像缺字段等，都属于**搜索前就能查出来**的问题。
# 本预检遵守三条铁律：
#   1. 只读——不写 run-state、不发帖、不发消息、不写入 Base；
#   2. 不泄露凭据——只报告「已配置/未配置」与来源文件路径，绝不回显 key/secret/token 值；
#   3. 不阻断——退出码 0=PASS / 1=WARN（可继续但需留意）/ 2=BLOCK（应修完再搜索）。

# 6 码固定顺序（群名 ← 源文件），与 report-template.md 第 14 条、publish.py QR_GROUP_NAMES 一致。
PRECHECK_QR_FILES: List[Tuple[str, str]] = [
    ("本地大模型用户交流群", "群二维码_本地大模型.png"),
    ("AI智能体开发平台运营群", "群二维码_Dify平台.png"),
    ("语义本体构建交流群", "群二维码_金风科技.png"),
    ("KNS统一数据平台", "群二维码_KNS.png"),
    ("标准化助手交流群", "群二维码_标准化助手.png"),
    ("风衍大模型用户交流群", "群二维码_风衍大模型.jpeg"),
]

# 必填 Base 表（issues/events/sources/judgments/runs/candidates）
PRECHECK_REQUIRED_TABLES = ("issues", "events", "sources", "judgments", "runs", "candidates")

# 凭据类键名（只判断"是否存在/是否非空"，绝不回显值）
_SECRET_KEY_PATTERN = ("key", "secret", "token", "password", "passwd", "credential")


def _redact_config_probe(path: Path, required_keys: Tuple[str, ...] = ()) -> Dict[str, Any]:
    """只读探测一个配置/环境文件：报告"文件存在 + 键是否齐备"，绝不回显任何值。

    返回 {"path", "exists", "readable", "keys_present": [...], "missing_keys": [], "secret_keys": n}
    —— 只给键**名**的存在性，不给键值。
    """
    info: Dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "readable": False,
        "keys_present": [],
        "missing_keys": list(required_keys),
        "secret_key_count": 0,
    }
    if not info["exists"]:
        return info
    try:
        text = path.read_text(encoding="utf-8")
        info["readable"] = True
    except OSError:
        return info
    keys = set()
    secret_count = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            name = line.split("=", 1)[0].strip()
        else:
            name = line
        if not name:
            continue
        keys.add(name)
        if any(token in name.lower() for token in _SECRET_KEY_PATTERN):
            secret_count += 1
    info["keys_present"] = sorted(keys)
    info["missing_keys"] = sorted(k for k in required_keys if k not in keys)
    info["secret_key_count"] = secret_count
    return info


def _probe_json_config(path: Path) -> Dict[str, Any]:
    """只读探测一个 JSON 配置的结构（键/条目数），不输出内容。"""
    info: Dict[str, Any] = {"path": str(path), "exists": path.exists(), "valid_json": False, "top_keys": [], "group_count": None}
    if not info["exists"]:
        return info
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return info
    if isinstance(data, dict):
        info["valid_json"] = True
        info["top_keys"] = sorted(data.keys())
        groups = data.get("groups")
        if isinstance(groups, list):
            info["group_count"] = sum(1 for g in groups if isinstance(g, dict) and g.get("enabled"))
        elif isinstance(groups, dict):
            info["group_count"] = len(groups)
    elif isinstance(data, list):
        info["valid_json"] = True
        info["group_count"] = len(data)
    return info


def _http_reachable(url: str, timeout: float = 6.0) -> Dict[str, Any]:
    """只读探活：HEAD/GET 一次，只看状态码，不解析内容、不携带任何凭据。"""
    result: Dict[str, Any] = {"reachable": False, "status_code": None, "error": None}
    try:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "ai-radar-precheck/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 内部固定域名
            result["reachable"] = True
            result["status_code"] = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:  # 401/403 说明服务可达（只是未带凭据）
        result["reachable"] = exc.code < 500
        result["status_code"] = exc.code
        result["error"] = f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - 探活失败原因多样，统一收口
        result["error"] = type(exc).__name__
    return result


def cmd_precheck(args: argparse.Namespace) -> int:
    """只读环境预检：搜索前把可发现的问题全部前移。

    检查项：
      0. 排程声明：`config/runtime.json` → `schedule.trigger_local` 是否声明并打印（未声明仅 WARN）
      1. 二维码：6 码文件是否齐备（缺一即 BLOCK——publish.py 缺码直接 PublishError）
      2. 飞书：群配置可解析、启用群 ≥1、每群有 chat_id；群画像含必需字段且 angle_label 唯一
      3. 知识库：Base token / 六张表 id / profile 是否配置（未配置仅 WARN，不是错误）
      4. 服务可达性：论坛 base_url（关闭 TLS 校验仅用于内部自签站点探活）
      5. 凭据：.env 存在且可读，所需键位齐备（**仅报告键名，绝不回显值**）

    退出码：0=PASS / 1=WARN（有提醒但可继续）/ 2=BLOCK（应先修复再开始搜索）。
    """
    work_dir = get_work_dir()
    checks: List[Dict[str, Any]] = []
    blockers: List[str] = []
    warnings: List[str] = []

    def add(name: str, ok: bool, detail: str, *, level: str = "error", **extra: Any) -> None:
        entry = {"check": name, "ok": ok, "detail": detail, "level": level if not ok else "ok"}
        entry.update(extra)
        checks.append(entry)
        if not ok:
            (blockers if level == "error" else warnings).append(f"{name}: {detail}")

    # ---- 0. 排程声明（V5.3.0）----
    # 背景（2026-11-04 溯源）：检索触发时点 2026-09-08 确立为 10:00，后（不晚于 10-10）改为 08:30，
    # 但 SKILL.md §5 / retrieval-strategy.md §1 / self_check.py docstring 三处文档长期未同步，
    # 形成「文档说 10:00、实际跑 08:30」的数周漂移。此处每次运行打印声明值，让排程漂移可见。
    schedule = load_runtime().get("schedule") or {}
    declared_trigger = str(schedule.get("trigger_local") or "").strip()
    if declared_trigger:
        add("排程声明", True,
            f"检索触发时点声明为每日 {declared_trigger}（{schedule.get('timezone', args.timezone)}，"
            f"rrule {schedule.get('rrule', '未声明')}）；真源是自动化配置，本项仅作镜像——"
            "若与实际触发时间不符，须同步 SKILL.md §5 / references/retrieval-strategy.md §1 / "
            "scripts/self_check.py docstring",
            rrule=schedule.get("rrule"), trigger_local=declared_trigger)
    else:
        add("排程声明", False,
            "config/runtime.json 缺 schedule.trigger_local（排程无声明镜像，漂移在运行日志中不可见）",
            level="warn")

    # ---- 1. 二维码 6 码（gw-ai-pulse 起：正文不挂群码，本项仅当文件存在时才校验）----
    # 2026-11-13 用户决定：日报正文移除 6 张技术交流群二维码，飞书推送同步关闭。
    # 因此「没有二维码文件」是标准形态，不再逐张报 warn；一旦有人把码放回 work/，
    # 则按推送开关定级（推送开=error / 推送关=warn）逐张校验，防止半套结构。
    _push_on = _feishu_push_enabled()
    _qr_level = "error" if _push_on else "warn"
    qr_status = []
    for group_name, filename in PRECHECK_QR_FILES:
        path = work_dir / filename
        exists = path.exists()
        size = path.stat().st_size if exists else 0
        qr_status.append({"group": group_name, "file": filename, "exists": exists, "bytes": size})
    present = [q for q in qr_status if q["exists"]]
    if not present:
        add("二维码", True, "未使用技术交流群二维码（gw-ai-pulse 起为标准形态）", files=qr_status)
    elif len(present) == len(PRECHECK_QR_FILES):
        add("二维码", True, f"{len(PRECHECK_QR_FILES)} 码齐备", files=qr_status)
    else:
        missing = [q["file"] for q in qr_status if not q["exists"]]
        add("二维码", False,
            f"已放入 {len(present)} 张但缺 {len(missing)} 张：{', '.join(missing)}"
            "（要么补齐 6 张，要么全部移出 work/）",
            level=_qr_level, missing=missing)

    # ---- 2. 飞书群配置 + 群画像（推送关闭时仅告警，不阻断）----
    _fs_level = "error" if _push_on else "warn"
    _fs_prefix = "" if _push_on else "飞书推送已关闭，仅告警："
    rt = load_runtime()
    paths = rt.get("paths", {}) or {}
    groups_path = Path(paths.get("feishu_groups") or SKILL_DIR / "references" / "feishu-groups.json")
    profiles_path = Path(paths.get("feishu_group_profiles") or SKILL_DIR / "references" / "feishu-group-profiles.json")

    groups_info = _probe_json_config(groups_path)
    if not groups_info["exists"] or not groups_info["valid_json"]:
        add("飞书群配置", False, f"{_fs_prefix}群配置缺失或非法 JSON：{groups_path}", level=_fs_level)
    elif not groups_info["group_count"]:
        add("飞书群配置", False, f"{_fs_prefix}没有任何启用中的飞书群：{groups_path}", level=_fs_level)
    else:
        add("飞书群配置", True, f"启用群 {groups_info['group_count']} 个", config=groups_info)

    profiles_info = _probe_json_config(profiles_path)
    if not profiles_info["exists"] or not profiles_info["valid_json"]:
        add("群画像", False, f"{_fs_prefix}群画像缺失或非法 JSON：{profiles_path}", level=_fs_level)
    else:
        try:
            profiles_data = json.loads(profiles_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            profiles_data = {}
        pmap = profiles_data.get("groups", {}) if isinstance(profiles_data, dict) else {}
        angle_labels: List[str] = []
        missing_fields: List[str] = []
        for chat_id, prof in (pmap.items() if isinstance(pmap, dict) else []):
            if not isinstance(prof, dict):
                continue
            # V3.9.1：angle_suffix（群专属价值落点）改为可选，不再作为必填字段
            for field in ("angle_label", "question", "headline_keywords"):
                if not prof.get(field):
                    missing_fields.append(f"{str(chat_id)[-8:]}:{field}")
            if prof.get("angle_label"):
                angle_labels.append(str(prof["angle_label"]))
        if not pmap:
            add("群画像", False, f"{_fs_prefix}群画像未定义任何群：{profiles_path}", level=_fs_level)
        elif missing_fields:
            add("群画像", False, f"{_fs_prefix}画像字段缺失：{', '.join(missing_fields[:6])}", level=_fs_level, missing=missing_fields)
        elif len(angle_labels) != len(set(angle_labels)):
            add("群画像", False, f"{_fs_prefix}angle_label 不唯一（差异化门禁会 fail closed）：{angle_labels}", level=_fs_level)
        else:
            add("群画像", True, f"画像完整、视角标签唯一（{len(angle_labels)} 个）", config=profiles_info)

    # ---- 3. 知识库配置（未配置仅 WARN：允许先出刊、后补同步） ----
    ks = rt.get("knowledge_sync", {}) or {}
    table_ids = ks.get("table_ids", {}) or {}
    missing_tables = [t for t in PRECHECK_REQUIRED_TABLES if not table_ids.get(t)]
    if not ks.get("enabled"):
        add("知识库配置", False, "knowledge_sync.enabled=false（本期不同步远端，仅保留本地 package）", level="warn")
    elif not ks.get("base_token") or missing_tables:
        detail = "Base 未配置完整"
        if missing_tables:
            detail += f"，缺表：{', '.join(missing_tables)}"
        add("知识库配置", False, detail, level="warn", missing_tables=missing_tables)
    else:
        add("知识库配置", True, f"Base + {len(PRECHECK_REQUIRED_TABLES)} 表 id 齐备")

    # ---- 3b. 首页数据底座帖（5.2.0，WARN 级：缺了只影响首页，不影响日报） ----
    hf = rt.get("homepage_feed", {}) or {}
    if not hf.get("enabled"):
        add("首页数据", False, "homepage_feed.enabled=false（本期不更新论坛首页）", level="warn")
    elif not isinstance(hf.get("topic_id"), int) or hf.get("topic_id", 0) <= 0:
        add("首页数据", False, "homepage_feed.topic_id 未配置（首页底座帖话题 ID）", level="warn")
    else:
        add("首页数据", True, f"底座帖话题 {hf['topic_id']}，更新时刻 {hf.get('update_time', '09:30')}")

    # ---- 4. 服务可达性（只读探活） ----
    forum_base = (rt.get("forum", {}) or {}).get("base_url")
    if forum_base:
        probe = _http_reachable(forum_base)
        add("论坛可达性", probe["reachable"],
            f"{forum_base} 可达（HTTP {probe['status_code']}）" if probe["reachable"]
            else f"{forum_base} 不可达：{probe['error']}",
            level="warn", probe=probe)

    # ---- 5. 凭据键位（只报键名，绝不回显值） ----
    env_file = Path(args.env_file)
    env_required = ("DISCOURSE_API_KEY", "DISCOURSE_API_USERNAME")
    env_probe = _redact_config_probe(env_file, env_required)
    if not env_probe["exists"]:
        add("凭据文件", False, f".env 不存在：{env_file}（论坛写入会失败）")
    elif not env_probe["readable"]:
        add("凭据文件", False, f".env 不可读（权限）：{env_file}")
    elif env_probe["missing_keys"]:
        add("凭据文件", False,
            f".env 缺键：{', '.join(env_probe['missing_keys'])}（仅报键名，不回显值）",
            missing_keys=env_probe["missing_keys"])
    else:
        add("凭据文件", True, f".env 键位齐备（{len(env_required)} 项，值不读取不回显）",
            secret_key_count=env_probe["secret_key_count"])

    passed = [c for c in checks if c["ok"]]
    overall = "BLOCK" if blockers else ("WARN" if warnings else "PASS")
    emit(
        "ok" if overall == "PASS" else ("warn" if overall == "WARN" else "error"),
        f"PRECHECK_{overall}",
        f"只读预检完成：{len(passed)}/{len(checks)} 项通过"
        + (f"；{len(warnings)} 项提醒" if warnings else "")
        + (f"；{len(blockers)} 项阻断" if blockers else ""),
        overall=overall,
        checks=checks,
        blockers=blockers,
        warnings=warnings,
        note="本次预检只读：未写 run-state、未发帖、未发消息、未写入 Base；凭据只校验键名不回显值",
    )
    if blockers:
        return 2
    return 1 if warnings else 0


# ============================================================
# 子命令
# ============================================================

def cmd_plan(args: argparse.Namespace) -> int:
    """stage 1：冻结合同 + 算 issue_id + 写 run-state 雏形。"""
    state, state_path = load_run_state(args.report_date)
    state["revision"] = int(state.get("revision", 0)) + 1
    contract = {
        "issue_id": issue_id_for(args.report_date),
        "report_date": args.report_date,
        "timezone": args.timezone,
        "skill_dir": str(SKILL_DIR),
        "work_dir": str(get_work_dir()),
        "env_file": args.env_file,
        "mode": args.mode,
    }
    contract_path = state_path.parent / "run-contract.json"
    if not args.dry_run:
        contract_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(contract_path, contract)
    state["contract"] = contract
    set_stage(state, "1", "RUNNING")
    save_run_state(state, state_path, dry_run=args.dry_run)

    # 调 fetch_recent.py（去重，stage 1 末尾动作）
    fetch_cmd = [
        sys.executable, str(SCRIPTS_DIR / "fetch_recent.py"),
        "--report-date", args.report_date,
        "--timezone", args.timezone,
        "--env-file", args.env_file,
        "--output", str(get_work_dir() / f"AI雷达日报_{args.report_date}_近期去重.json"),
    ]
    rc = run_subprocess(fetch_cmd, dry_run=args.dry_run)
    if rc != 0:
        set_stage(state, "1", "FAIL", error=f"fetch_recent 退出 {rc}")
        save_run_state(state, state_path, dry_run=args.dry_run)
        emit("error", "STAGE1_FAILED", "去重阶段失败", rc=rc)
        return rc

    set_stage(state, "1", "PASS")
    state["run_status"] = "STAGE1_PLANNED"
    save_run_state(state, state_path, dry_run=args.dry_run)
    emit("ok", "STAGE1_PLANNED", "stage 1 完成", issue_id=contract["issue_id"], contract=str(contract_path))
    return 0


def cmd_retrieval(args: argparse.Namespace) -> int:
    """stage 2-3：检索 + 证据回执 + 封面图（由执行 Agent 在状态机外完成）。"""
    state, state_path = load_run_state(args.report_date)
    if state["stages"].get("1", {}).get("status") != "PASS":
        emit("error", "STAGE1_REQUIRED", "stage 1 未通过，请先 plan")
        return 2
    set_stage(state, "2", "RUNNING")
    set_stage(state, "3", "RUNNING")
    save_run_state(state, state_path, dry_run=args.dry_run)
    emit("ok", "STAGE2_READY", "stage 2-3 由执行 Agent 检索 + 封面；脚本仅记录状态")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """stage 4：语义复核。执行 Agent 跑语义复核，run.py 写状态。"""
    state, state_path = load_run_state(args.report_date)
    # V3.8.7 修正：阶段 2/3（检索清单 + 证据回执 + 封面）由执行 Agent 产出，
    # `retrieval` 子命令只「标记进入」，不会把 2/3 置为 PASS；而 10.1 契约中 review 的
    # 阻断门为「无」。原代码以 2/3 必须 PASS 阻断，与契约矛盾且无 CLI 可置 PASS，
    # 导致检索/复核完成的正常流程必然卡在 STAGE23_REQUIRED。此处改为进入阶段 4 前
    # 把已完成产出的 2/3 落为 PASS（若为 FAIL 仍如实保留，不掩盖失败）。
    for sid in ("2", "3"):
        if state["stages"].get(sid, {}).get("status") != "FAIL":
            set_stage(state, sid, "PASS")
    set_stage(state, "4", "RUNNING")
    save_run_state(state, state_path, dry_run=args.dry_run)
    semantic_path = work_files(args.report_date)["semantic"]
    if not args.dry_run and not semantic_path.exists():
        emit("error", "SEMANTIC_MISSING", f"未找到 {semantic_path}", path=str(semantic_path))
        set_stage(state, "4", "FAIL", error="semantic 缺失")
        save_run_state(state, state_path, dry_run=False)
        return 2
    set_stage(state, "4", "PASS")
    state["run_status"] = "STAGE4_REVIEWED"
    save_run_state(state, state_path, dry_run=args.dry_run)
    emit("ok", "STAGE4_REVIEWED", "stage 4 完成", semantic=str(semantic_path))
    return 0


def cmd_self_check(args: argparse.Namespace) -> int:
    """stage 5：调 self_check.py（门禁 G1/G2/G3）。"""
    state, state_path = load_run_state(args.report_date)
    if state["stages"].get("4", {}).get("status") != "PASS":
        emit("error", "STAGE4_REQUIRED", "stage 4 未通过")
        return 2

    f = work_files(args.report_date)
    if not args.dry_run and not f["md"].exists():
        emit("error", "MD_MISSING", f"未找到日报稿 {f['md']}")
        return 2

    set_stage(state, "5", "RUNNING")
    save_run_state(state, state_path, dry_run=args.dry_run)

    preflight_rc = run_preflight_gate(args, f)
    record_preflight_summary(state, f["preflight_report"], preflight_rc)
    if preflight_rc != 0:
        set_stage(state, "5", "FAIL", error=f"preflight_check 退出 {preflight_rc}")
        state["gates"]["G1"] = state["gates"].get("G1", "PENDING")
        state["gates"]["G2"] = state["gates"].get("G2", "PENDING")
        state["gates"]["G3"] = {"status": "FAIL", "rule_ids": ["PREFLIGHT"], "passed_at": None}
        state["run_status"] = "GATE_FAILED"
        save_run_state(state, state_path, dry_run=args.dry_run)
        emit("error", "PREFLIGHT_FAILED", "发布前总装预检失败，阻断 self-check 与论坛发布", rc=preflight_rc, report=str(f["preflight_report"]))
        return preflight_rc

    # self_check.py 需要 qr1/qr2/qr3；这里尝试常见路径，找不到就提示
    # V3.8.7：路径同步为 6 码映射中的前 3 码（self_check 的 check_qr_gate 实际不消费这三个值，
    # 仅作 argparse 必填占位，但为避免误导按真实群名对齐）。
    qr1 = get_work_dir() / "群二维码_本地大模型.png"
    qr2 = get_work_dir() / "群二维码_Dify平台.png"
    qr3 = get_work_dir() / "群二维码_金风科技.png"

    cmd = [
        sys.executable, str(SCRIPTS_DIR / "self_check.py"),
        str(f["md"]),
        "--semantic-review", str(f["semantic"]),
        "--search-log", str(f["search_log"]),
    ]
    # V4.3：封面不存在时不传 --cover，让 self_check 走「无封面引用语降级」分支
    # （旧实现恒传 --cover，导致文档承诺的降级路径经编排器永远走不通）。
    if f["cover"].exists():
        cmd += ["--cover", str(f["cover"])]
    else:
        emit("warn", "COVER_MISSING_FALLBACK", "封面不存在，按降级方案校验正文引用语",
             expected=str(f["cover"]))
    cmd += [
        "--qr1", str(qr1),
        "--qr2", str(qr2),
        "--qr3", str(qr3),
        "--recent-data", str(f["recent_dedup"]),
        "--mode", args.mode,
        "--report-date", args.report_date,
        "--timezone", args.timezone,
        "--require-evidence-receipts",
        "--report", str(f["self_check"]),
    ]
    rc = run_subprocess(cmd, dry_run=args.dry_run)
    if rc != 0:
        set_stage(state, "5", "FAIL", error=f"self_check 退出 {rc}")
        state["gates"]["G1"] = state["gates"].get("G1", "PENDING")
        state["gates"]["G2"] = state["gates"].get("G2", "PENDING")
        state["gates"]["G3"] = {"status": "FAIL", "rule_ids": [], "passed_at": None}
        state["run_status"] = "GATE_FAILED"
        save_run_state(state, state_path, dry_run=args.dry_run)
        emit("error", "GATE_FAILED", "G3 门禁失败，阻断后续", rc=rc)
        return rc

    if not args.dry_run:
        package_preflight_rc = run_preflight_gate(args, f, package_dry_run=True)
        record_preflight_summary(state, f["preflight_report"], package_preflight_rc)
        if package_preflight_rc != 0:
            set_stage(state, "5", "FAIL", error=f"preflight package dry-run 退出 {package_preflight_rc}")
            state["gates"]["G3"] = {"status": "FAIL", "rule_ids": ["PREFLIGHT_PACKAGE"], "passed_at": None}
            state["run_status"] = "GATE_FAILED"
            save_run_state(state, state_path, dry_run=False)
            emit("error", "PREFLIGHT_PACKAGE_FAILED", "知识包发布前 dry-run 失败，阻断论坛发布", rc=package_preflight_rc, report=str(f["preflight_report"]))
            return package_preflight_rc

    set_stage(state, "5", "PASS")
    state["gates"]["G1"] = "PASS"
    state["gates"]["G2"] = "PASS"
    state["gates"]["G3"] = {"status": "PASS", "rule_ids": ["R01", "R07", "R08", "R11", "R12", "R14"], "passed_at": now_iso()}
    state["run_status"] = "WAITING_FORUM_CONFIRMATION"
    save_run_state(state, state_path, dry_run=args.dry_run)
    emit("ok", "GATES_PASSED", "G1=G2=G3=PASS，停在 WAITING_FORUM_CONFIRMATION 等待用户确认论坛发布")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    """stage 6：论坛发布。需 --confirm。"""
    state, state_path = load_run_state(args.report_date)
    if state["stages"].get("5", {}).get("status") != "PASS":
        emit("error", "STAGE5_REQUIRED", "stage 5 未通过，需先 self-check")
        return 2
    if state["run_status"] not in {"WAITING_FORUM_CONFIRMATION", "STAGE6_PUBLISHED", "PACKAGED",
                                   "SYNC_PENDING", "RUN_COMPLETED"}:
        emit("error", "WRONG_STATE", f"run_status={state['run_status']} 不允许 publish")
        return 2
    if not args.confirm:
        emit("warn", "NEED_CONFIRM", "论坛写入外部可见，需加 --confirm 确认")
        return 2

    f = work_files(args.report_date)
    preflight_rc = run_preflight_gate(args, f, package_dry_run=not args.dry_run)
    record_preflight_summary(state, f["preflight_report"], preflight_rc)
    if preflight_rc != 0:
        set_stage(state, "6", "FAIL", error=f"preflight_check 退出 {preflight_rc}")
        state["run_status"] = "GATE_FAILED"
        save_run_state(state, state_path, dry_run=args.dry_run)
        emit("error", "PREFLIGHT_FAILED", "发布前总装预检失败，禁止论坛写入", rc=preflight_rc, report=str(f["preflight_report"]))
        return preflight_rc

    set_stage(state, "6", "RUNNING")
    save_run_state(state, state_path, dry_run=args.dry_run)
    # V3.8.7 修正：publish.py 的 6 码按 QR_GROUP_NAMES 群名顺序精确映射，路径必须一一对齐，
    # 且缺第 6 码会直接阻断（原实现只给 4 码、且群名错位，必然 PublishError）。
    # 群名 → 源文件（2026-09-23 逐张读图核验）：
    #   1 本地大模型用户交流群     ← 群二维码_本地大模型.png
    #   2 AI智能体开发平台运营群   ← 群二维码_Dify平台.png
    #   3 语义本体构建交流群       ← 群二维码_金风科技.png
    #   4 KNS统一数据平台          ← 群二维码_KNS.png
    #   5 标准化助手交流群         ← 群二维码_标准化助手.png
    #   6 风衍大模型用户交流群     ← 群二维码_风衍大模型.jpeg
    cmd = [
        sys.executable, str(SCRIPTS_DIR / "publish.py"),
        str(f["md"]), str(f["cover"]),
        str(get_work_dir() / "群二维码_本地大模型.png"),
        str(get_work_dir() / "群二维码_Dify平台.png"),
        str(get_work_dir() / "群二维码_金风科技.png"),
        str(f["self_check"]),
        "--qr4", str(get_work_dir() / "群二维码_KNS.png"),
        "--qr5", str(get_work_dir() / "群二维码_标准化助手.png"),
        "--qr6", str(get_work_dir() / "群二维码_风衍大模型.jpeg"),
        "--report-date", args.report_date,
        "--timezone", args.timezone,
        "--env-file", args.env_file,
        "--state-file", str(f["publish_state"]),
    ]
    rc = run_subprocess(cmd, dry_run=args.dry_run)
    if rc != 0:
        set_stage(state, "6", "FAIL", error=f"publish 退出 {rc}")
        state["run_status"] = "PUBLISH_FAILED"
        save_run_state(state, state_path, dry_run=args.dry_run)
        emit("error", "PUBLISH_FAILED", "论坛发布失败", rc=rc)
        return rc

    set_stage(state, "6", "PASS")
    state["run_status"] = "STAGE6_PUBLISHED"
    if not args.dry_run:
        try:
            publish_state = json.loads(f["publish_state"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            set_stage(state, "6", "FAIL", error=f"发布回执不可读: {exc}")
            state["run_status"] = "NEEDS_RECONCILIATION"
            save_run_state(state, state_path, dry_run=False)
            emit("error", "PUBLISH_STATE_INVALID", "发布子进程成功但回执不可读，禁止继续")
            return 2
        if publish_state.get("status") != "OK" or not publish_state.get("topic_id") or not publish_state.get("post_id") or not publish_state.get("url"):
            set_stage(state, "6", "FAIL", error="发布回执缺少 OK/topic_id/post_id/url")
            state["run_status"] = "NEEDS_RECONCILIATION"
            save_run_state(state, state_path, dry_run=False)
            emit("error", "PUBLISH_STATE_INVALID", "发布回执缺少已验证论坛标识，禁止继续")
            return 2
        state["forum"] = {
            "status": "PUBLISHED",
            "topic_id": publish_state["topic_id"],
            "post_id": publish_state["post_id"],
            "url": publish_state["url"],
        }
        record_confirmation(state, "forum_publish", {
            "confirmed": True,
            "confirmed_by": "user",
            "at": now_iso(),
            "report_date": args.report_date,
            "topic_id": publish_state["topic_id"],
            "post_id": publish_state["post_id"],
        })
    save_run_state(state, state_path, dry_run=args.dry_run)
    emit("ok", "STAGE6_PUBLISHED", "论坛发布完成，等待用户确认飞书推送")
    return 0


def cmd_package(args: argparse.Namespace) -> int:
    """stage 7：**仅**构建并校验本地知识包（V3.9.0 拆分）。

    与旧版的区别：不再在这里调用 sync_lark_base.py / sync_candidates_base.py。
    远端同步是外部可见动作，改由 sync-knowledge 在**飞书三群推送成功后**独立执行，
    这样「本地构包」可以更早完成、且不会因为远端不可用而拖住论坛发布后的推送。

    失败语义：本地构包失败 → stage 7 FAIL、run_status=GATE_FAILED（阻断后续）。
    """
    state, state_path = load_run_state(args.report_date)
    if state["stages"].get("6", {}).get("status") != "PASS":
        emit("error", "STAGE6_REQUIRED", "stage 6 未通过，需先 publish")
        return 2

    set_stage(state, "7", "RUNNING")
    save_run_state(state, state_path, dry_run=args.dry_run)
    f = work_files(args.report_date)

    pkg_dir = f["knowledge_package"].parent
    if not args.dry_run:
        pkg_dir.mkdir(parents=True, exist_ok=True)
    pkg_cmd = [
        sys.executable, str(SCRIPTS_DIR / "build_knowledge_package.py"),
        str(f["md"]),
        "--search-log", str(f["search_log"]),
        "--semantic-review", str(f["semantic"]),
        "--self-check-report", str(f["self_check"]),
        "--publish-state", str(f["publish_state"]),
        "--run-state", str(state_path),
        "--issue-id", state["issue_id"],
        "--report-date", args.report_date,
        "--timezone", args.timezone,
        "--output", str(f["knowledge_package"]),
    ]
    rc = run_subprocess(pkg_cmd, dry_run=args.dry_run)
    if rc != 0:
        set_stage(state, "7", "FAIL", error=f"build 退出 {rc}")
        state["knowledge_sync"]["status"] = "NOT_STARTED"
        state["run_status"] = "GATE_FAILED"
        save_run_state(state, state_path, dry_run=args.dry_run)
        emit("error", "PACKAGE_BUILD_FAILED", "本地知识包构建失败，阻断后续", rc=rc)
        return rc

    # 本地构包成功即校验产物可读（存在 + 合法 JSON），保证 stage 7 的 PASS 有实据。
    if not args.dry_run:
        if not f["knowledge_package"].exists():
            set_stage(state, "7", "FAIL", error="package.json 未生成")
            state["run_status"] = "GATE_FAILED"
            save_run_state(state, state_path, dry_run=False)
            emit("error", "PACKAGE_MISSING", f"构包命令成功但未生成 {f['knowledge_package']}")
            return 2
        try:
            package = json.loads(f["knowledge_package"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            set_stage(state, "7", "FAIL", error=f"package.json 不可读: {exc}")
            state["run_status"] = "GATE_FAILED"
            save_run_state(state, state_path, dry_run=False)
            emit("error", "PACKAGE_INVALID", "本地知识包不可读，禁止继续")
            return 2
        package_sha = _file_sha256(f["knowledge_package"])
        tables = package.get("tables") or {}
        count_of = lambda key: len(tables.get(key) or []) if isinstance(tables, dict) else 0  # noqa: E731
        state["knowledge_sync"] = {
            "status": "PACKAGED",
            "package_path": str(f["knowledge_package"]),
            "package_sha256": package_sha,
            "counts": {k: count_of(k) for k in PRECHECK_REQUIRED_TABLES if k != "candidates"},
        }

    set_stage(state, "7", "PASS")
    state["run_status"] = "PACKAGED"
    save_run_state(state, state_path, dry_run=args.dry_run)
    emit("ok", "STAGE7_PACKAGED",
         "本地知识包已构建（stage 7）；远端同步待飞书三群推送成功后由 sync-knowledge 执行",
         package=str(f["knowledge_package"]))
    return 0


def cmd_sync_knowledge(args: argparse.Namespace) -> int:
    """stage 8：远端知识库同步（V3.9.0 新增，独立命令）。

    前置条件（fail closed）：
      1. stage 7 已 PASS（本地 package 已构建）；
      2. 论坛已 PUBLISHED；
      3. 若启用了飞书推送（feishu_push.enabled=true），则**所有**飞书目标群须已 SENT
         （先推送、后同步）。gw-ai-pulse 起：推送默认关闭，此时该条不再生效——
         知识库退化为「离线归档」，不参与 run 主链路，也不再依赖飞书推送状态。

    失败语义：远端未配置/回执非 SYNCED → stage 8 FAIL、run_status=SYNC_PENDING，
    保留本地 package 与已验证日报，不删除、不回滚论坛与飞书内容。
    """
    state, state_path = load_run_state(args.report_date)
    if state["stages"].get("7", {}).get("status") != "PASS":
        emit("error", "STAGE7_REQUIRED", "stage 7 未通过，需先 package（构建本地知识包）")
        return 2
    if not args.confirm:
        emit("warn", "NEED_CONFIRM", "远端 Base 写入外部可见，需加 --confirm 确认")
        return 2
    if state.get("forum", {}).get("status") != "PUBLISHED":
        emit("error", "FORUM_REQUIRED", "论坛未完成发布回读，禁止远端知识库同步")
        return 2

    # ---- 硬门禁：所有飞书目标群必须已 SENT（仅在启用推送时生效）----
    # gw-ai-pulse（2026-11-13）：推送默认关闭后，这条门禁会把每一次归档都卡死，
    # 导入时让 run 永远停在 SYNC_PENDING。故改为「推送启用才校验」。
    feishu = state.get("feishu", {}) or {}
    feishu_groups = feishu.get("groups") or []
    not_sent = [g for g in feishu_groups if g.get("status") != "SENT"]
    if _feishu_push_enabled() and (feishu.get("status") != "SENT" or not feishu_groups or not_sent):
        detail = "飞书尚未全部发送成功"
        if not feishu_groups:
            detail += "（run-state 无群级发送记录）"
        elif not_sent:
            detail += "：" + ", ".join(f"{g.get('group_name') or g.get('chat_id')}={g.get('status')}" for g in not_sent)
        set_stage(state, "8", "FAIL", error=detail)
        state["run_status"] = "PUSH_FAILED" if feishu.get("status") == "FAILED" else "BLOCKED"
        save_run_state(state, state_path, dry_run=False)
        emit("error", "FEISHU_NOT_ALL_SENT",
             f"{detail}；知识库同步必须晚于飞书推送（先推送、后同步），已阻断",
             feishu_status=feishu.get("status"))
        return 2

    f = work_files(args.report_date)
    if not args.dry_run and not f["knowledge_package"].exists():
        set_stage(state, "8", "FAIL", error=f"缺少本地 package: {f['knowledge_package']}")
        state["run_status"] = "SYNC_PENDING"
        save_run_state(state, state_path, dry_run=False)
        emit("error", "PACKAGE_MISSING", "缺少本地知识包，禁止远端同步")
        return 2

    set_stage(state, "8", "RUNNING")
    save_run_state(state, state_path, dry_run=args.dry_run)

    # ---- 8.1 五表同步 ----
    sync_cmd = [
        sys.executable, str(SCRIPTS_DIR / "sync_lark_base.py"),
        str(f["knowledge_package"]),
        "--config", str(RUNTIME_PATH),
        "--confirm",
    ]
    rc = run_subprocess(sync_cmd, dry_run=args.dry_run)
    if rc != 0:
        set_stage(state, "8", "FAIL", error=f"sync 退出 {rc}")
        state["knowledge_sync"]["status"] = "SYNC_PENDING"
        state["run_status"] = "SYNC_PENDING"
        save_run_state(state, state_path, dry_run=args.dry_run)
        emit("warn", "SYNC_PENDING", "远端同步失败，保留本地 package（可重跑 sync-knowledge）", rc=rc)
        return rc
    if not args.dry_run:
        sync_result_path = f["knowledge_package"].with_suffix(".upsert-plan.json")
        try:
            sync_result = json.loads(sync_result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            set_stage(state, "8", "FAIL", error=f"sync 回执不可读: {exc}")
            state["knowledge_sync"]["status"] = "SYNC_PENDING"
            state["run_status"] = "SYNC_PENDING"
            save_run_state(state, state_path, dry_run=False)
            emit("warn", "SYNC_PENDING", "同步子进程成功但回执不可读，保留本地 package")
            return 2
        if sync_result.get("status") != "SYNCED":
            sync_status = str(sync_result.get("status") or "SYNC_PENDING")
            reasons = sync_result.get("reasons") or []
            set_stage(state, "8", "FAIL", error=f"sync 回执状态={sync_status}")
            state["knowledge_sync"] = {
                "status": "SYNC_PENDING",
                "remote_status": sync_status,
                "reasons": reasons,
                "result_path": str(sync_result_path),
            }
            state["run_status"] = "SYNC_PENDING"
            save_run_state(state, state_path, dry_run=False)
            emit("warn", "SYNC_PENDING", "远端回读未确认全部稳定主键，保留本地 package",
                 remote_status=sync_status, reasons=reasons)
            return 2

    # ---- 8.2 候选全集同步（失败不阻断主流程：候选表是补充信息） ----
    cand_cmd = [
        sys.executable, str(SCRIPTS_DIR / "sync_candidates_base.py"),
        "--work-dir", str(get_work_dir()),
    ]
    rc_cand = run_subprocess(cand_cmd, dry_run=args.dry_run)
    if rc_cand != 0:
        state["candidates_sync"] = {"status": "SYNC_PENDING", "exit_code": rc_cand}
        emit("warn", "CANDIDATES_SYNC_PENDING",
             "候选全集同步失败，五表不受影响（候选表为补充信息）", rc=rc_cand)
    else:
        state["candidates_sync"] = {"status": "SYNCED", "table_id": _candidates_table_id()}

    set_stage(state, "8", "PASS")
    state["knowledge_sync"]["status"] = "SYNCED"
    state["run_status"] = "RUN_COMPLETED"
    save_run_state(state, state_path, dry_run=args.dry_run)
    emit("ok", "STAGE8_SYNCED", "远端知识库已同步，全链路完成（RUN_COMPLETED）")
    return 0


def _candidates_table_id() -> str:
    """从 runtime.json 读候选全集表 id，缺省回退内置值。"""
    try:
        return str((load_runtime().get("knowledge_sync", {}).get("table_ids") or {})
                   .get("candidates") or "tbl4RS4qEuDf2B0E")
    except Exception:
        return "tbl4RS4qEuDf2B0E"


def _extract_message_id(stdout: str) -> str:
    """从 lark-cli JSON 输出里递归提取 message_id。"""
    text = (stdout or "").strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
    except ValueError:
        return ""
    found: List[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"message_id", "messageId", "messageID"} and isinstance(item, str):
                    found.append(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return found[0] if found else ""


def _feishu_push_enabled() -> bool:
    """飞书推送是否启用（gw-ai-pulse 2026-11-13 新增）。

    默认 **False** —— 这是与旧环境的刻意差异：新设计只发 AI 社区论坛，
    不再往生产飞书群推日报，因此阶段 5 / 阶段 8 的飞书前置不应阻塞主链路。
    `runtime.json` 写 `feishu_push.enabled: true` 或设环境变量
    `AI_RADAR_FEISHU_PUSH=1` 即可恢复旧行为（代码未删除，只是默认关闭）。
    """
    env = os.environ.get("AI_RADAR_FEISHU_PUSH", "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    try:
        block = (load_runtime() or {}).get("feishu_push") or {}
    except Exception:  # noqa: BLE001 - 配置不可读时按关闭处理（宁可少推不可误推生产群）
        return False
    return bool(block.get("enabled", False))


def _feishu_paths(state: Dict[str, Any]) -> Dict[str, str]:
    """飞书推送相关路径/身份（只读 runtime + 环境变量，不回显任何凭据）。"""
    try:
        rt = load_runtime()
    except Exception:
        rt = {}
    p = rt.get("paths", {}) or {}
    return {
        "groups_path": p.get("feishu_groups") or str(SKILL_DIR / "references" / "feishu-groups.json"),
        "profiles_path": p.get("feishu_group_profiles") or str(SKILL_DIR / "references" / "feishu-group-profiles.json"),
        "feishu_state_path": str(Path(p.get("feishu_state", str(get_work_dir() / "feishu-push-state.json")).format(
            work_dir=str(get_work_dir()), issue_id=state["issue_id"]))),
        "push_profile": os.environ.get("AI_RADAR_FEISHU_PROFILE") or p.get("feishu_push_profile") or "cli_aa942f2aba38dccd",
        "push_identity": os.environ.get("AI_RADAR_FEISHU_IDENTITY") or p.get("feishu_push_identity") or "user",
    }


def cmd_push_draft(args: argparse.Namespace) -> int:
    """论坛确认前预生成三群飞书草稿（V3.9.0）：链接用占位符，不触网、不发送。

    目的（用户批准的前移方案）：论坛确认门内就把推送文案准备好，用户一旦确认论坛发布，
    论坛写入回读成功后**只需回填真实 URL/topic ID** 即可发送，省掉「等论坛成功 → 才生成文案」的串行等待。

    草稿文件与正式 payload 同名同结构，另存 `--output` 指定的草稿路径；草稿内嵌
    `FORUM_URL_PLACEHOLDER`，`cmd_push` 在发送前会 fail closed 拒绝一切占位链接。
    """
    state, state_path = load_run_state(args.report_date)
    if state["stages"].get("5", {}).get("status") != "PASS":
        emit("error", "STAGE5_REQUIRED", "stage 5 未通过，需先 self-check")
        return 2

    f = work_files(args.report_date)
    if not args.dry_run and not f["md"].exists():
        emit("error", "MD_MISSING", f"未找到日报稿 {f['md']}")
        return 2

    paths = _feishu_paths(state)
    draft_path = f["feishu_push_json"].with_name(
        f["feishu_push_json"].stem + "_草稿" + f["feishu_push_json"].suffix)
    prepare_cmd = [
        sys.executable, str(SCRIPTS_DIR / "prepare_feishu_push.py"), "prepare",
        str(f["md"]), FORUM_URL_PLACEHOLDER, TOPIC_ID_PLACEHOLDER,
        "--groups", paths["groups_path"],
        "--profiles", paths["profiles_path"],
        "--state", paths["feishu_state_path"],
        "--output", str(draft_path),
        "--message-output", str(draft_path.with_suffix(".txt")),
        "--issue-id", state["issue_id"],
        "--report-date", args.report_date,
        "--search-log", str(f["search_log"]),
        "--allow-placeholder",
    ]
    rc = run_subprocess(prepare_cmd, dry_run=args.dry_run)
    if rc != 0:
        emit("error", "DRAFT_FAILED", "飞书草稿生成失败", rc=rc)
        return rc

    if not args.dry_run:
        state.setdefault("feishu", {})
        state["feishu"]["draft_path"] = str(draft_path)
        state["feishu"]["draft_created_at"] = now_iso()
        state["feishu"]["draft_status"] = "READY_PLACEHOLDER"
        save_run_state(state, state_path, dry_run=False)

    emit("ok", "PUSH_DRAFT_READY",
         "三群飞书草稿已生成（链接为占位符，论坛回读后由 push 回填）",
         draft=str(draft_path), placeholder_url=FORUM_URL_PLACEHOLDER)
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    """飞书群推送（副作用，需 --confirm）。

    V3.9.0 前置条件变更：远端同步（stage 8）已后移到推送之后，故本命令**不再要求 stage 8**，
    改为要求「stage 7 本地包已构建 + 论坛已回读 PUBLISHED」。

    安全门（fail closed）：发送前校验 article_url / topic_id **均非占位符**，
    并校验 payload 内找不到任何占位 token——防止带着 <FORUM_URL_PENDING> 的草稿被误发到群里。
    """
    state, state_path = load_run_state(args.report_date)
    # gw-ai-pulse（2026-11-13）：推送默认关闭时，本阶段直接 SKIPPED（而不是 FAIL/BLOCK）。
    # 主链路（检索→成稿→门禁→论坛发布→首页）不依赖飞书，跑完即 COMPLETE。
    if not _feishu_push_enabled():
        state.setdefault("feishu", {})
        state["feishu"].update({"status": "SKIPPED", "review_status": "SKIPPED",
                                "skipped_at": now_iso(),
                                "note": "feishu_push.enabled=false（gw-ai-pulse 默认），未推送到任何群"})
        save_run_state(state, state_path, dry_run=args.dry_run)
        emit("ok", "FEISHU_PUSH_SKIPPED", "飞书推送已关闭（feishu_push.enabled=false），本阶段跳过")
        return 0
    if state["stages"].get("7", {}).get("status") != "PASS":
        emit("error", "STAGE7_REQUIRED", "stage 7 未通过，需先 package（构建本地知识包）")
        return 2
    if not args.confirm:
        emit("warn", "NEED_CONFIRM", "飞书推送外部可见，需 --confirm")
        return 2
    if state.get("forum", {}).get("status") != "PUBLISHED":
        emit("error", "FORUM_REQUIRED", "论坛未完成发布回读，禁止飞书推送")
        return 2

    f = work_files(args.report_date)
    publish_state_path = f["publish_state"]
    if not publish_state_path.exists():
        emit("error", "PUBLISH_STATE_MISSING", f"缺少发布状态文件: {publish_state_path}")
        return 2
    with publish_state_path.open("r", encoding="utf-8") as fp:
        publish_state = json.load(fp)
    article_url = publish_state.get("url") or state.get("forum", {}).get("url")
    topic_id = str(publish_state.get("topic_id") or state.get("forum", {}).get("topic_id") or "")
    if is_placeholder_url(article_url) or is_placeholder_topic_id(topic_id):
        emit("error", "PLACEHOLDER_LINK_BLOCKED",
             "发布状态缺少真实 article_url/topic_id（疑似占位符），禁止飞书推送",
             article_url=article_url, topic_id=topic_id)
        return 2

    paths = _feishu_paths(state)
    groups_path = paths["groups_path"]
    profiles_path = paths["profiles_path"]
    feishu_state_path = paths["feishu_state_path"]
    push_profile = paths["push_profile"]
    push_identity = paths["push_identity"]

    prepare_cmd = [
        sys.executable, str(SCRIPTS_DIR / "prepare_feishu_push.py"), "prepare",
        str(f["md"]), article_url, topic_id,
        "--groups", groups_path,
        "--profiles", profiles_path,
        "--state", feishu_state_path,
        "--output", str(f["feishu_push_json"]),
        "--message-output", str(f["feishu_push_json"].with_suffix(".txt")),
        "--issue-id", state["issue_id"],
        "--report-date", args.report_date,
        "--search-log", str(f["search_log"]),
    ]
    rc = run_subprocess(prepare_cmd, dry_run=args.dry_run)
    if rc != 0:
        emit("error", "PUSH_FAILED", "飞书准备失败", rc=rc)
        return rc
    if args.dry_run:
        emit("ok", "PUSH_DRY_RUN", "飞书推送 dry-run 完成")
        return 0

    with f["feishu_push_json"].open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if payload.get("article_url") != article_url or str(payload.get("topic_id")) != topic_id:
        emit("error", "PUSH_PAYLOAD_INVALID", "飞书 payload 链接或 topic_id 与发布状态不一致")
        return 2
    # 占位链接终检（fail closed）：只扫描**真正会发送出去的内容**（V3.9.1 修正，见函数注释）。
    leaked = leaked_placeholder_tokens(payload, article_url, topic_id)
    if leaked:
        emit("error", "PLACEHOLDER_LINK_BLOCKED",
             "飞书 payload 中残留占位符，禁止发送（需先用真实论坛链接重新 prepare）",
             leaked=leaked)
        return 2

    sent_groups: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for item in payload.get("payloads", []):
        group_name = item.get("group_name") or item.get("chat_id")
        label = item.get("group_label") or group_name
        if item.get("skip"):
            continue
        chat_id = item["chat_id"]
        message = item["message"]
        idempotency_key = f"ai-radar-{args.report_date.replace('-', '')}-{topic_id}-{chat_id[-8:]}"
        attempts = 0
        last_error = ""
        message_id = ""
        sent = False
        for attempt in range(1, 4):
            attempts = attempt
            send_cmd = [
                "lark-cli", "--profile", push_profile, "--as", push_identity,
                "im", "+messages-send", "--chat-id", chat_id,
                "--markdown", message,
                "--idempotency-key", idempotency_key,
            ]
            proc = subprocess.run(send_cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                message_id = _extract_message_id(proc.stdout)
                sent = True
                break
            last_error = (proc.stderr or proc.stdout or "").strip()[:500]
        status = "SENT" if sent else "FAILED"
        record_cmd = [
            sys.executable, str(SCRIPTS_DIR / "prepare_feishu_push.py"), "record",
            topic_id, chat_id, group_name, status,
            "--state", feishu_state_path,
            "--attempts", str(attempts),
            "--issue-id", state["issue_id"],
            "--report-date", args.report_date,
        ]
        if message_id:
            record_cmd += ["--message-id", message_id]
        if last_error:
            record_cmd += ["--error", last_error]
        rec_rc = run_subprocess(record_cmd, dry_run=False)
        if rec_rc != 0:
            emit("error", "PUSH_RECORD_FAILED", f"飞书发送状态记录失败: {label}", rc=rec_rc)
            return rec_rc
        entry = {
            "chat_id": chat_id,
            "group_name": group_name,
            "status": status,
            "attempts": attempts,
            "message_id": message_id,
        }
        if sent:
            sent_groups.append(entry)
        else:
            entry["error"] = last_error
            failures.append(entry)

    state.setdefault("feishu", {})
    state["feishu"].update({
        "review_status": "CONFIRMED_BY_USER",
        "status": "SENT" if not failures else "FAILED",
        "sent_at": now_iso(),
        "groups": sent_groups + failures,
    })
    record_confirmation(state, "feishu_push", {
        "confirmed": True,
        "confirmed_by": "user",
        "at": now_iso(),
        "topic_id": topic_id,
        "groups": [g.get("group_name") for g in sent_groups],
    })
    if failures:
        state["run_status"] = "PUSH_FAILED"
        state.setdefault("errors", []).append({"stage": "push", "error": failures, "at": now_iso()})
        save_run_state(state, state_path, dry_run=False)
        emit("error", "PUSH_FAILED", "飞书推送存在失败群", failures=failures)
        return 2

    # 飞书三群已全部 SENT → 进入「远端同步待执行」而非终态（V3.9.0：
    # 知识库同步已后移到推送之后，由 sync-knowledge 独立推进并置 RUN_COMPLETED）。
    state["run_status"] = "SYNC_PENDING"
    save_run_state(state, state_path, dry_run=False)
    emit("ok", "FEISHU_SENT",
         "飞书三群已发送；下一步执行 sync-knowledge 完成远端知识库同步", groups=sent_groups)
    return 0


def next_publish_at(report_date: str, update_time: str = "09:30", max_days: int = 20) -> Optional[str]:
    """下一个可发布日（工作日 / 调休补班）的预计更新时刻，北京时间 ISO。找不到返回 None。"""
    from datetime import date as _date, timedelta as _td
    try:
        hh, mm = (int(x) for x in str(update_time).split(":", 1))
        d = _date.fromisoformat(report_date)
    except ValueError:
        return None
    for _ in range(max_days):
        d = d + _td(days=1)
        if china_workday_status(d.isoformat()) in ("workday", "makeup"):
            return f"{d.isoformat()}T{hh:02d}:{mm:02d}:00+08:00"
    return None


def cmd_feed(args: argparse.Namespace) -> int:
    """5.2.0：论坛首页「AI 雷达」数据。

    不带 --confirm：从已发布正文生成本地 _首页数据.json 并校验（无外部动作）。
    带 --confirm：再把 JSON 写入数据底座帖（外部写入），回读核对后记入 run-state.homepage_feed。
    前置：论坛已 PUBLISHED（首页链接指向当期日报）。
    """
    import homepage_feed as HF

    cfg = load_runtime().get("homepage_feed") or {}
    if not cfg.get("enabled"):
        emit("info", "FEED_DISABLED", "runtime.json homepage_feed.enabled=false，跳过首页更新")
        return 0
    state, state_path = load_run_state(args.report_date)
    forum = state.get("forum") or {}
    if forum.get("status") != "PUBLISHED" or is_placeholder_url(forum.get("url")):
        emit("error", "FORUM_NOT_PUBLISHED", "论坛尚未发布回读成功，首页不能指向未发布的日报")
        return 2
    f = work_files(args.report_date)
    if not f["md"].exists():
        emit("error", "MD_MISSING", f"未找到正文 {f['md']}")
        return 2
    try:
        search_log = json.loads(f["search_log"].read_text(encoding="utf-8")) if f["search_log"].exists() else None
    except (OSError, json.JSONDecodeError):
        search_log = None
    from datetime import date as _date
    try:
        feed, warns = HF.build_feed(f["md"].read_text(encoding="utf-8"), _date.fromisoformat(args.report_date),
                                    daily_url=forum["url"], search_log=search_log,
                                    next_update_at=next_publish_at(args.report_date, cfg.get("update_time", "09:30")),
                                    timezone=args.timezone)
    except ValueError as exc:
        emit("error", "FEED_NOT_V5", str(exc))
        return 2
    errs, vwarns = HF.validate_feed(feed)
    if errs:
        emit("error", "FEED_INVALID", "首页数据校验失败", errors=errs[:10])
        return 2
    counts = {k: len(feed[k]) for k in ("brief", "picks", "curated", "tracking", "radar", "community")}
    if not args.dry_run:
        atomic_write_json_local(f["homepage_feed"], feed)
    if not args.confirm:
        emit("ok", "FEED_PREVIEW_READY", "首页数据已生成到本地（未写论坛）；确认后加 --confirm 更新首页",
             output=str(f["homepage_feed"]), counts=counts, warnings=(warns + vwarns)[:10] or None)
        return 0
    if args.dry_run:
        emit("info", "FEED_DRY_RUN", "dry-run：不写论坛", counts=counts)
        return 0

    cmd = [sys.executable, str(SCRIPTS_DIR / "homepage_feed.py"), "publish", str(f["homepage_feed"]),
           "--env-file", args.env_file, "--state-file", str(f["homepage_feed_state"])]
    if getattr(args, "allow_older", False):
        cmd.append("--allow-older")
    rc = run_subprocess(cmd, dry_run=False)
    try:
        result = json.loads(f["homepage_feed_state"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result = {"status": "FAILED", "code": "FEED_STATE_MISSING"}
    state["homepage_feed"] = {
        "status": result.get("status"),
        "code": result.get("code"),
        "topic_id": result.get("topic_id"),
        "post_id": result.get("post_id"),
        "issue_date": result.get("issue_date"),
        "sha256": result.get("sha256"),
        "artifact": str(f["homepage_feed"]),
        "updated_at": now_iso(),
    }
    if result.get("status") in ("UPDATED", "NOOP"):
        record_confirmation(state, "homepage_feed", {
            "confirmed": True,
            "confirmed_by": "user",
            "at": now_iso(),
            "report_date": args.report_date,
            "post_id": result.get("post_id"),
            "note": "随论坛发布确认一并授权",
        })
    save_run_state(state, state_path, dry_run=False)
    if rc != 0:
        emit("error", result.get("code") or "FEED_PUBLISH_FAILED", "首页更新未完成（不影响日报与后续推送）", rc=rc)
        return rc
    emit("ok", result.get("code", "FEED_UPDATED"), "首页数据已更新", post_id=result.get("post_id"), counts=counts)
    return 0


def atomic_write_json_local(path: Path, data: Any) -> None:
    from common import atomic_write_json
    atomic_write_json(path, data)


def cmd_all(args: argparse.Namespace) -> int:
    """plan → retrieval → review → self-check 全跑，停在 WAITING_FORUM_CONFIRMATION。"""
    for sub in ["plan", "retrieval", "review", "self-check"]:
        sub_args = argparse.Namespace(**vars(args))
        sub_fn = SUB_COMMANDS[sub]
        rc = sub_fn(sub_args)
        if rc != 0:
            return rc
    emit("ok", "ALL_DONE_PRE_FORUM", "全跑到 WAITING_FORUM_CONFIRMATION，等用户确认论坛发布")
    return 0


def _gate_status(value: Any) -> str:
    """门禁值可能是字符串（G1/G2）或对象（G3/G4），统一取状态文本。"""
    if isinstance(value, dict):
        return str(value.get("status", "UNKNOWN"))
    return str(value) if value is not None else "UNKNOWN"


def cmd_status(args: argparse.Namespace) -> int:
    """查看 run-state 摘要。"""
    state_path = work_files(args.report_date)["state"]
    if not state_path.exists():
        emit("warn", "NO_STATE", f"未找到 {state_path}")
        return 1
    with state_path.open("r", encoding="utf-8") as f:
        state = json.load(f)

    if getattr(args, "brief", False):
        stages = state.get("stages", {}) or {}
        marks = " ".join(
            f"{sid}:{'PASS' if (stages.get(sid) or {}).get('status') == 'PASS' else '·'}"
            for sid in sorted(stages, key=lambda item: int(item) if str(item).isdigit() else 99)
        )
        gates = state.get("gates", {}) or {}
        gate_text = "  ".join(f"{name} {_gate_status(value)}" for name, value in gates.items())
        feishu = state.get("feishu", {}) or {}
        groups = feishu.get("groups") or []
        sent = sum(1 for group in groups if group.get("status") == "SENT")
        errors = state.get("errors") or []
        print(f"ISSUE: {state.get('issue_id')}   schema {state.get('schema_version')}")
        print(f"RUN_STATUS: {state.get('run_status')}   stage {state.get('current_stage')}   owner {state.get('owner_session')}")
        print(f"STAGES: {marks}")
        print(f"GATES: {gate_text}")
        print(f"FORUM: {(state.get('forum') or {}).get('status')}   "
              f"FEISHU: {feishu.get('status')} ({sent}/{len(groups)})   "
              f"SYNC: {(state.get('knowledge_sync') or {}).get('status')}   "
              f"FEED: {(state.get('homepage_feed') or {}).get('status')}")
        print(f"ERRORS: {len(errors) if errors else '无'}")
        for item in errors[:5]:
            print(f"  - {item}")
        return 0

    summary = {
        "issue_id": state.get("issue_id"),
        "schema_version": state.get("schema_version"),
        "run_status": state.get("run_status"),
        "current_stage": state.get("current_stage"),
        "owner_session": state.get("owner_session"),
        "heartbeat": state.get("heartbeat"),
        "gates": state.get("gates"),
        "forum_status": state.get("forum", {}).get("status"),
        "knowledge_sync": state.get("knowledge_sync", {}).get("status"),
        "feishu_review": state.get("feishu", {}).get("review_status"),
        "feishu_draft": state.get("feishu", {}).get("draft_status"),
        "homepage_feed": (state.get("homepage_feed") or {}).get("status"),
        "preflight": state.get("preflight"),
        "errors": state.get("errors"),
        "stages": {k: v.get("status") for k, v in state.get("stages", {}).items()},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


SUB_COMMANDS = {
    "precheck": cmd_precheck,
    "plan": cmd_plan,
    "retrieval": cmd_retrieval,
    "review": cmd_review,
    "self-check": cmd_self_check,
    "publish": cmd_publish,
    "package": cmd_package,
    "push-draft": cmd_push_draft,
    "push": cmd_push,
    "sync-knowledge": cmd_sync_knowledge,
    "all": cmd_all,
    "status": cmd_status,
    "accumulate": cmd_accumulate,
    "settle": cmd_settle,
    "dedup": cmd_dedup,
    "seen-seed": cmd_seen_seed,
    "recover-missed": cmd_recover_missed,
    "feed": cmd_feed,
}


# ============================================================
# CLI 入口
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run.py",
        description="AI 雷达日报统一编排器（版本随 config/runtime.json，见 --help 子命令列表）",
    )
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--report-date", required=True, help="刊发日期 YYYY-MM-DD")
    common.add_argument("--timezone", default="Asia/Shanghai")
    common.add_argument("--env-file", default=str(get_work_dir() / ".env"))
    common.add_argument("--mode", choices=["normal", "backfill", "revise", "resume", "holiday-batch"],
                       default="normal")
    common.add_argument("--dry-run", action="store_true", help="只打印不写盘")
    common.add_argument("--confirm", action="store_true",
                       help="publish/push 写入外部时显式确认")
    common.add_argument("--session-id", help="会话标识（并行互斥用；缺省读环境变量 AI_RADAR_SESSION_ID，再缺省 default）")
    common.add_argument("--takeover", action="store_true",
                       help="并行会话 heartbeat 仍新鲜时强制接管 run-state（接管前须按 SKILL.md 完成核验）")

    for name in ["plan", "retrieval", "review", "self-check"]:
        sp = sub.add_parser(name, parents=[common], help=f"子命令 {name}")
        sp.set_defaults(cmd=name)

    # 只读预检（搜索前环境检查）：无副作用，不需要 --confirm
    sub.add_parser("precheck", parents=[common],
                   help="只读环境预检（二维码/群配置/知识库/服务可达/凭据键位），搜索前执行")

    for name in ["publish", "package", "sync-knowledge", "push-draft", "push"]:
        sp = sub.add_parser(name, parents=[common], help=f"子命令 {name}（需 --confirm）")
        sp.set_defaults(cmd=name)

    sub.add_parser("accumulate", parents=[common],
                   help="把当期检索候选追加进累积池（恒定执行，不看日历）")
    sp_settle = sub.add_parser("settle", parents=[common],
                               help="工作日统一发布后结算累积池（按重要度取前 30，需 --confirm）")
    sp_settle.add_argument("--selected-ids", help="逗号分隔的 uid 或本期清单 id；缺省按重要度取前 30")
    sp_dedup = sub.add_parser("dedup", parents=[common],
                              help="检索清单入池前三级查重（已见索引），命中改判 REJECTED_DUPLICATE")
    sp_dedup.add_argument("--recent-data", help="可选：fetch_recent.py 输出，查重前并入索引")
    sp_seed = sub.add_parser("seen-seed", parents=[common], help="冷启动：论坛近期期次灌入已见索引")
    sp_seed.add_argument("--recent-data", required=False)
    sub.add_parser("recover-missed", parents=[common],
                   help="扫描累积池缺失检索日（exit 5 = 有缺失须补检）")

    sp_feed = sub.add_parser("feed", parents=[common],
                             help="论坛首页 AI 雷达数据：不带 --confirm 只生成本地预览，带 --confirm 更新底座帖")
    sp_feed.add_argument("--allow-older", action="store_true",
                         help="允许用更早一期覆盖首页（仅修订最新期以外的期次且确需回退时使用）")
    sub.add_parser("all", parents=[common], help="全跑到 WAITING_FORUM_CONFIRMATION")
    sp_status = sub.add_parser("status", parents=[common], help="查看 run-state 摘要")
    sp_status.add_argument("--brief", action="store_true",
                           help="一屏摘要（≤8 行），避免整份 run-state 灌进上下文")
    sp_status.set_defaults(cmd="status")

    return p


def main(argv: Optional[List[str]] = None) -> int:
    global CURRENT_SESSION
    parser = build_parser()
    args = parser.parse_args(argv)
    CURRENT_SESSION = resolve_session_id(args)
    report_date_err = validate_report_date(args.report_date, args.timezone)
    if report_date_err:
        emit("error", "INVALID_REPORT_DATE", report_date_err, report_date=args.report_date)
        return 2
    # 中国法定工作日硬校验（V3.7.0 / V3.8.0）：仅 normal 模式生效。
    # 普通周末/法定休假日 → 立即结束（get / dry-run 跳过，不阻断辅助命令）。
    # V3.8.0 豁免说明：accumulate 是"检索通道"，按用户口径**每天恒定执行、不看日历**，
    #   非工作日也要把候选收进累积池；故豁免工作日拦截与发布门禁。
    #   真正的"发布拦截"发生在 publish/settle（可发布日才放行），而非检索侧。
    # V3.9.0 追加豁免：precheck 是只读预检（不写盘、不发外部请求内容），
    #   在非工作日做环境体检同样有意义（可在周末把二维码/配置问题先修好）。
    _RETRIEVAL_ONLY = ("accumulate", "precheck", "dedup", "seen-seed", "recover-missed")
    if args.mode == "normal" and args.command not in ("status",) and args.command not in _RETRIEVAL_ONLY:
        _wd_status = china_workday_status(args.report_date)
        if _wd_status in ("off", "weekend"):
            emit("info", "NOT_WORKDAY", f"{args.report_date} 为{'法定休假日' if _wd_status=='off' else '普通周末'}，"
                                         "normal 模式停刊，不搜索、不写稿、不执行外部动作",
                 report_date=args.report_date, workday_status=_wd_status)
            return 0 if getattr(args, "dry_run", False) else 4
    if args.command not in ("status", "accumulate", "precheck", "dedup", "seen-seed", "recover-missed"):
        blocked_reason = check_session_exclusive(args)
        if blocked_reason:
            emit("error", "PARALLEL_SESSION_BLOCKED", blocked_reason,
                 owner_session_hint=CURRENT_SESSION)
            return 3
    handler = SUB_COMMANDS[args.command]
    try:
        return handler(args)
    except Exception as exc:  # pragma: no cover
        emit("error", "UNHANDLED", f"未捕获异常: {exc}", exception_type=type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
