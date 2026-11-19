#!/usr/bin/env python3
"""候选全集同步到飞书知识库 Candidates 表（sync_candidates_base.py）

用途（V3.8.0）：
- 把累积池（candidates-pool.json）中的候选全集写入飞书知识库
  「候选全集 Candidates」表（tbl4RS4qEuDf2B0E）；
- 供日报正文的「全资讯链接」指向完整候选集（含未进入精编 30 条的其余候选）。

设计要点：
- 只读累积池 + 本地 package，不联网抓取；
- 走 lark-cli base 命令写入，身份与 profile 从 runtime.json 的 knowledge_sync 读；
- 幂等：以「候选ID + 期次」为主键查重，已存在则跳过（不重复写入）；
- 凭据一律不读取、不打印；仅使用 runtime.json 中的非敏感 token/id。

安全边界：与 sync_lark_base.py 一致——不打印凭据、不绕过 ACL、失败如实上报。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    import holiday_pool as hp  # type: ignore
except Exception as exc:  # pragma: no cover
    print(json.dumps({"ok": False, "error": f"无法导入 holiday_pool: {exc}"}, ensure_ascii=False))
    sys.exit(2)

CANDIDATES_TABLE_ID_DEFAULT = "tbl4RS4qEuDf2B0E"
MAX_BATCH = 200


def load_runtime() -> Dict[str, Any]:
    with (SKILL_DIR / "config" / "runtime.json").open("r", encoding="utf-8") as f:
        return json.load(f)


def run_cli(args: List[str]) -> Dict[str, Any]:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"lark-cli 退出 {proc.returncode}: {proc.stderr.strip()[:300]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"lark-cli 输出非 JSON: {exc}") from exc


def normalize_decision(decision: Any) -> str:
    """把候选池的细分 decision 归一到 Candidates 表单选项。

    Candidates 表「采选结果」是单选字段，**线上仅有 SELECTED / REJECTED 两个选项**
    （2026-09-23 读表核验）。候选池里的细分枚举（REJECTED_STALE / REJECTED_DUPLICATE /
    REJECTED_LOW_PRIORITY / REJECTED_UNVERIFIED 等）直接写入会触发
    lark-cli 800030005 not_found。细分原因仍完整保留在候选池与本地产物中，
    这里只做落到表格选项的最小归一。
    """
    value = str(decision or "").strip().upper()
    if value == "SELECTED":
        return "SELECTED"
    if value.startswith("REJECTED"):
        return "REJECTED"
    return "REJECTED"


def build_rows(pool: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把池内全部候选拍平成 Candidates 表行。"""
    rows: List[Dict[str, Any]] = []
    for b in pool.get("batches", []):
        rd = b.get("report_date")
        for c in b.get("candidates", []):
            rows.append({
                "候选ID": str(c.get("id") or ""),
                "期次": str(c.get("collected_on") or rd or ""),
                "检索日": str(c.get("collected_on") or rd or ""),
                "标题": str(c.get("title") or ""),
                "事件日期": str(c.get("event_date") or ""),
                "来源名称": str(c.get("source_name") or ""),
                "来源链接": str(c.get("source_url") or ""),
                "分类": str(c.get("category") or ""),
                "采选结果": [normalize_decision(c.get("decision"))],
                "是否进入精编": bool(c.get("published")),
                "发布时间": str(c.get("published_on") or ""),
                "写入时间": str(b.get("collected_at") or ""),
            })
    return rows


def query_existing_ids(base_token: str, table_id: str, sync: Dict[str, Any]) -> set:
    """查询表内已存在的候选ID，用于幂等去重。

    注意：lark-cli 的 --output 仅允许写当前工作目录，故临时文件落在 cwd。
    """
    out = Path.cwd() / f".candidates-existing-{table_id}.ndjson"
    # 清理上次残留（lark-cli 会同时产出 .ndjson 与 .manifest.json），保证可重入
    for stale in list(Path.cwd().glob(f".candidates-existing-{table_id}.*")):
        try:
            stale.unlink()
        except OSError:
            pass
    cmd = ["lark-cli"]
    if sync.get("profile"):
        cmd += ["--profile", str(sync["profile"])]
    cmd += [
        "base", "+record-list",
        "--base-token", base_token,
        "--table-id", table_id,
        "--field-id", "候选ID",
        "--limit", "2000",
        "--format", "ndjson",
        "--output", str(out),
        "--overwrite",
        "--as", str(sync.get("identity", "bot")),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"查询已有候选失败: {proc.stderr.strip()[:300]}")
    ids = set()
    try:
        if out.exists():
            with out.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    v = rec.get("候选ID")
                    if isinstance(v, list):
                        v = v[0] if v else None
                    if v:
                        ids.add(str(v))
    finally:
        for stale in list(Path.cwd().glob(f".candidates-existing-{table_id}.*")):
            try:
                stale.unlink()
            except OSError:
                pass
    return ids


def write_rows(base_token: str, table_id: str, sync: Dict[str, Any],
               rows: List[Dict[str, Any]], dry_run: bool) -> int:
    if dry_run or not rows:
        return 0
    written = 0
    for i in range(0, len(rows), MAX_BATCH):
        chunk = rows[i:i + MAX_BATCH]
        payload = {"create_records": chunk}
        cmd = ["lark-cli"]
        if sync.get("profile"):
            cmd += ["--profile", str(sync["profile"])]
        cmd += [
            "base", "+record-batch-create",
            "--base-token", base_token,
            "--table-id", table_id,
            "--json", json.dumps(payload, ensure_ascii=False),
            "--as", str(sync.get("identity", "bot")),
        ]
        res = run_cli(cmd)
        if not res.get("ok"):
            raise RuntimeError(f"写入失败: {res.get('error')}")
        written += len(chunk)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="候选全集同步到飞书 Candidates 表")
    ap.add_argument("--work-dir", required=True, help="work_dir（含 candidates-pool.json）")
    ap.add_argument("--table-id", default=None, help="覆盖默认 Candidates 表 id")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写盘")
    args = ap.parse_args()

    rt = load_runtime()
    sync = rt.get("knowledge_sync", {})
    base_token = str(sync.get("base_token") or "").strip()
    if not base_token:
        print(json.dumps({"ok": False, "error": "knowledge_sync.base_token 未配置"}, ensure_ascii=False))
        return 2

    table_id = args.table_id or str(
        (sync.get("table_ids") or {}).get("candidates") or CANDIDATES_TABLE_ID_DEFAULT
    )

    pool = hp.load_pool(Path(args.work_dir))
    rows = build_rows(pool)
    summary: Dict[str, Any] = {"table_id": table_id, "rows_total": len(rows)}

    if args.dry_run:
        summary.update({"ok": True, "dry_run": True, "would_write": len(rows)})
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    existing = query_existing_ids(base_token, table_id, sync)
    new_rows = [r for r in rows if r["候选ID"] and r["候选ID"] not in existing]
    written = write_rows(base_token, table_id, sync, new_rows, dry_run=False)
    summary.update({
        "ok": True,
        "existing": len(existing),
        "written": written,
        "skipped": len(rows) - len(new_rows),
    })
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
