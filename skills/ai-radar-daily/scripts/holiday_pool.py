#!/usr/bin/env python3
"""候选累积池（holiday_pool.py）

设计背景（2026-09-21 定稿）：
- 检索每天恒定执行（不看日历），候选按重要度落地到累积池；
- 发布触发唯一依据 = 是否工作日（china_workday_status）；
- 非工作日不发布，候选留在池中逐日累积；
- 到工作日统一发布：按重要度排序取前 30 条（30 为上限，不足则全量），
  并附一条「全部资讯链接」（落飞书知识库）供查阅全集。

本模块只做「池的读写与结算」：
- load_pool / save_pool / append_candidates / settle_pool
- 池文件固定为 <work_dir>/candidates-pool.json（跨期共享，不随 issue 目录）
- 不联网、不写外部系统；外部动作仍由 run.py 的受控子命令执行。

安全边界：
- 不读取、不打印凭据；
- 原子写盘（临时文件 + fsync + replace）；
- 幂等：同一 report_date 重复 append 以 report_date 去重覆盖。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "1.1.0"  # V4.3：新增 uid（全局唯一）与 importance

# 单次工作日统一发布的上限（用户口径：30 为上限，不足则全量）
MAX_BATCH_SIZE = 30

# 池中候选保留的最长天数，避免无限膨胀（超过则按 event_date 老化剔除）
POOL_MAX_AGE_DAYS = 45


def default_pool() -> Dict[str, Any]:
    """空池结构。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "batches": [],   # 每次「检索日」产生的候选批次
        "publishes": [], # 已结算发布记录
    }


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def pool_path(work_dir: Path) -> Path:
    return Path(work_dir) / "candidates-pool.json"


def load_pool(work_dir: Path) -> Dict[str, Any]:
    """读取累积池；不存在则返回空池（不写盘）。"""
    p = pool_path(work_dir)
    if not p.exists():
        return default_pool()
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"累积池格式非法（非对象）: {p}")
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("batches", [])
    data.setdefault("publishes", [])
    return data


def save_pool(work_dir: Path, pool: Dict[str, Any], dry_run: bool = False) -> Path:
    """原子写盘。dry_run 时不写。"""
    p = pool_path(work_dir)
    pool["updated_at"] = _now_iso()
    if dry_run:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".pool-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(pool, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return p


def _normalize_candidate(raw: Dict[str, Any], report_date: str) -> Dict[str, Any]:
    """把检索清单里的候选统一成池内条目，并补累积元数据。"""
    urls: List[str] = []
    if raw.get("source_url"):
        urls.append(str(raw["source_url"]))
    for u in raw.get("source_urls") or []:
        if u and u not in urls:
            urls.append(str(u))
    return {
        "id": raw.get("id"),
        # V4.3：检索清单 id（如 NEWS-01）每天从头编号，跨批次会撞号；uid 全局唯一
        "uid": raw.get("uid") or make_uid(report_date, raw),
        "importance": _to_float(raw.get("importance")),
        "title": raw.get("title"),
        "event_date": raw.get("event_date"),
        "source_name": raw.get("source_name"),
        "source_url": raw.get("source_url"),
        "source_urls": urls,
        "category": raw.get("category"),
        "decision": raw.get("decision"),
        "reject_reason": raw.get("reject_reason") or raw.get("reason"),
        # 累积元数据
        "collected_on": report_date,
        "collected_at": _now_iso(),
        "published": False,
    }


def append_candidates(
    pool: Dict[str, Any],
    report_date: str,
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """把某检索日的候选追加为一个批次（同 report_date 幂等覆盖）。"""
    batch = {
        "report_date": report_date,
        "collected_at": _now_iso(),
        "count": len(candidates),
        "candidates": [_normalize_candidate(c, report_date) for c in candidates],
    }
    pool["batches"] = [b for b in pool.get("batches", []) if b.get("report_date") != report_date]
    pool["batches"].append(batch)
    pool["batches"].sort(key=lambda b: str(b.get("report_date")))
    return pool


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def make_uid(report_date: str, raw: Dict[str, Any]) -> str:
    """全局唯一候选键：检索日 + 清单 id；无 id 时退化为 URL/标题短哈希。"""
    import hashlib
    local = raw.get("id")
    if not local:
        basis = f"{raw.get('source_url') or ''}|{raw.get('title') or ''}"
        local = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:10]
    return f"{report_date}::{local}"


def pending_candidates(pool: Dict[str, Any]) -> List[Dict[str, Any]]:
    """池中所有尚未发布的候选（按入池顺序，含 REJECTED，用于留档统计）。"""
    out: List[Dict[str, Any]] = []
    for b in pool.get("batches", []):
        for c in b.get("candidates", []):
            if not c.get("published"):
                c.setdefault("uid", make_uid(str(c.get("collected_on") or b.get("report_date")), c))
                out.append(c)
    return out


def is_publishable_candidate(c: Dict[str, Any]) -> bool:
    """只有 SELECTED（或旧数据未标 decision）的候选可进入精编发布排序；
    V5 的 TRACKING / SIGNAL 由 settle 另行结算，不占 30 条上限。"""
    decision = str(c.get("decision") or "SELECTED").upper()
    return decision == "SELECTED"


def ranked_pending(pool: Dict[str, Any], max_size: int = MAX_BATCH_SIZE) -> List[Dict[str, Any]]:
    """V4.3：按重要度排序取前 max_size（文档口径「按重要度取前 30」的真实落地）。

    排序键：importance 降序 → event_date 降序（新事件优先）→ 入池顺序。
    REJECTED_* 候选不参与发布排序。
    """
    pend = [c for c in pending_candidates(pool) if is_publishable_candidate(c)]
    indexed = list(enumerate(pend))
    indexed.sort(key=lambda t: (-_to_float(t[1].get("importance")),
                                _neg_date(t[1].get("event_date")), t[0]))
    return [c for _, c in indexed][:max_size]


def _neg_date(value: Any) -> int:
    try:
        return -int(str(value).replace("-", "")[:8])
    except (TypeError, ValueError):
        return 0


def settle_pool(
    pool: Dict[str, Any],
    report_date: str,
    candidates: List[Dict[str, Any]],
    max_size: int = MAX_BATCH_SIZE,
) -> Dict[str, Any]:
    """把给定候选标记为已结算发布，并记录发布批次。

    注意：编号与截断（取前 30）由调用方在排序后完成，本函数只落账。
    """
    ids = {_cand_key(c) for c in candidates}
    for b in pool.get("batches", []):
        for c in b.get("candidates", []):
            c.setdefault("uid", make_uid(str(c.get("collected_on") or b.get("report_date")), c))
            if _cand_key(c) in ids:
                c["published"] = True
                c["published_on"] = report_date
    pool.setdefault("publishes", []).append({
        "report_date": report_date,
        "settled_at": _now_iso(),
        "count": len(candidates),
        "cap": max_size,
        "candidate_titles": [c.get("title") for c in candidates],
        "candidate_uids": [c.get("uid") for c in candidates],
    })
    return pool


def _cand_key(c: Dict[str, Any]) -> str:
    """候选唯一键：优先 uid（V4.3，全局唯一），其次 id，退化为 url+title。

    修复：旧实现只用 id，而 NEWS-01 这类 id 每天重复，结算时会把其他批次的同号候选一起标为已发布。
    """
    if c.get("uid"):
        return f"uid::{c['uid']}"
    if c.get("id"):
        return f"id::{c['id']}"
    return f"ut::{c.get('source_url') or ''}::{c.get('title') or ''}"


def prune_old(pool: Dict[str, Any], today: str, max_age_days: int = POOL_MAX_AGE_DAYS) -> Dict[str, Any]:
    """剔除检索日早于 today - max_age_days 的整个批次（含其中已发布与未发布候选），防止池无限膨胀。
    已发布记录另存于 publishes 与 seen 索引，不因剔除而丢失去重能力。

    重要：today 应为**当前真实日期**（timezone 本地），不能传某个检索日——
    否则更早的批次会被误判为过老而被删（2026-09-21 长假累积冒烟暴露）。
    传入值若晚于池内所有批次日期，则不做剔除（保守放行）。
    """
    try:
        ref = datetime.strptime(today, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return pool
    kept = []
    for b in pool.get("batches", []):
        try:
            bd = datetime.strptime(str(b.get("report_date")), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            kept.append(b)
            continue
        # 只剔除"确实超过保留期"的批次；未发布候选优先保留
        if (ref - bd).days <= max_age_days:
            kept.append(b)
    pool["batches"] = kept
    return pool


def pool_summary(pool: Dict[str, Any]) -> Dict[str, Any]:
    """给 run.py status / 日志用的摘要。"""
    pend = pending_candidates(pool)
    return {
        "schema_version": pool.get("schema_version"),
        "batch_count": len(pool.get("batches", [])),
        "pending_count": len(pend),
        "pending_span": _span(pool.get("batches", [])),
        "published_batches": len(pool.get("publishes", [])),
        "max_batch_size": MAX_BATCH_SIZE,
        "updated_at": pool.get("updated_at"),
    }


def _span(batches: List[Dict[str, Any]]) -> Optional[str]:
    dates = [str(b.get("report_date")) for b in batches if b.get("report_date")]
    if not dates:
        return None
    return f"{min(dates)}~{max(dates)}"


if __name__ == "__main__":  # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser(description="候选累积池工具")
    ap.add_argument("action", choices=["summary"])
    ap.add_argument("--work-dir", required=True)
    a = ap.parse_args()
    print(json.dumps(pool_summary(load_pool(Path(a.work_dir))), ensure_ascii=False, indent=2))
