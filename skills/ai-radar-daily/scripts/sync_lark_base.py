#!/usr/bin/env python3
"""为知识包生成飞书 Base upsert 计划；显式确认后才允许联网写入。"""

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

TABLES = ("issues", "events", "sources", "judgments", "runs")
PRIMARY_KEYS = {
    "issues": "issue_id", "events": "event_id", "sources": "source_id",
    "judgments": "judgment_id", "runs": "run_id",
}
MAX_BATCH = 200

# Base 中声明为 datetime 的字段；写入时 ISO 字符串必须转毫秒时间戳。
DATETIME_FIELDS = {
    "issues": {"report_date", "created_at", "updated_at"},
    "events": {"report_date", "event_date"},
    "sources": {"published_at", "retrieved_at"},
    "judgments": {"proposed_date", "reviewed_at"},
    "runs": {"started_at", "finished_at"},
}


class SyncError(RuntimeError):
    pass


def read_json(path, label):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        if label == "runtime config":
            return None
        raise SyncError(f"{label}不存在：{path}")
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(f"无法读取{label}：{exc}") from exc
    if not isinstance(value, dict):
        raise SyncError(f"{label}顶层必须是 JSON object")
    return value


def atomic_write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def chunks(values, size=MAX_BATCH):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def lark_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value


def iso_to_ms(value):
    from datetime import datetime, timedelta, timezone
    text = str(value).strip()
    local = timezone(timedelta(hours=8))
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            parsed = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=local)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=local)
    except ValueError as exc:
        raise SyncError(f"无法解析日期时间值：{text}") from exc
    return int(parsed.timestamp() * 1000)


def build_fields(table, row):
    fields = {}
    datetime_fields = DATETIME_FIELDS.get(table, set())
    for field, value in row.items():
        if value is None:
            continue
        if field in datetime_fields:
            fields[field] = iso_to_ms(value)
        else:
            fields[field] = lark_value(value)
    return fields


def validate_package(package):
    missing = [name for name in TABLES if not isinstance(package.get(name), list)]
    if missing:
        raise SyncError(f"knowledge package 缺少数组：{', '.join(missing)}")
    seen = {}
    for table in TABLES:
        key = PRIMARY_KEYS[table]
        values = []
        for row in package[table]:
            if not isinstance(row, dict) or not str(row.get(key, "")).strip():
                raise SyncError(f"{table} 存在缺少稳定主键 {key} 的记录")
            values.append(str(row[key]))
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise SyncError(f"{table}.{key} 存在重复值：{duplicates[:3]}")
        seen[table] = values
    return seen


def config_gate(config):
    sync = (config or {}).get("knowledge_sync")
    if not isinstance(sync, dict):
        return None, ["knowledge_sync"]
    missing = []
    if sync.get("enabled") is not True:
        missing.append("knowledge_sync.enabled=true")
    if not str(sync.get("base_token", "")).strip():
        missing.append("knowledge_sync.base_token")
    table_ids = sync.get("table_ids")
    if not isinstance(table_ids, dict):
        missing.append("knowledge_sync.table_ids")
        table_ids = {}
    for table in TABLES:
        if not str(table_ids.get(table, "")).strip():
            missing.append(f"knowledge_sync.table_ids.{table}")
    blocked_reason = str(sync.get("blocked_reason", "")).strip()
    if missing and blocked_reason:
        missing.append(blocked_reason)
    if not missing:
        identity = str(sync.get("identity", "user")).strip()
        if identity not in ("user", "bot"):
            missing.append("knowledge_sync.identity 必须为 user 或 bot")
        sync["identity"] = identity or "user"
        sync["profile"] = str(sync.get("profile", "")).strip()
    return sync, missing


def build_plan(package, sync, mode, status, reasons=None):
    tables = []
    table_ids = (sync or {}).get("table_ids", {})
    for table in TABLES:
        key = PRIMARY_KEYS[table]
        rows = package[table]
        tables.append({
            "table": table,
            "table_id": table_ids.get(table),
            "primary_key": key,
            "strategy": "LOOKUP_BY_STABLE_PRIMARY_KEY_THEN_CREATE_OR_UPDATE",
            "record_count": len(rows),
            "max_batch_size": MAX_BATCH,
            "batches": [
                {
                    "batch": index,
                    "operation": "UPSERT_AFTER_LOOKUP",
                    "record_count": len(batch),
                    "primary_keys": [row[key] for row in batch],
                    "records": [build_fields(table, row) for row in batch],
                }
                for index, batch in enumerate(chunks(rows), start=1)
            ],
        })
    return {
        "status": status,
        "mode": mode,
        "network_attempted": False,
        "identity": (sync or {}).get("identity", "user"),
        "write_order": list(TABLES),
        "reasons": reasons or [],
        "target_wiki_url": (sync or {}).get("target_wiki_url"),
        "local_package_dir": (sync or {}).get("local_package_dir"),
        "tables": tables,
    }


def find_ignored_fields(value):
    if isinstance(value, dict):
        ignored = value.get("ignored_fields")
        if ignored:
            return ignored
        for child in value.values():
            nested = find_ignored_fields(child)
            if nested:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = find_ignored_fields(child)
            if nested:
                return nested
    return None


def parse_cli_contract(result):
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"退出码 {result.returncode}"
        raise SyncError(f"lark-cli 执行失败：{message}")
    candidates = [result.stdout.strip()]
    candidates.extend(reversed([line.strip() for line in result.stdout.splitlines() if line.strip()]))
    payload = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "ok" in value:
            payload = value
            break
        # +record-list 返回的是 manifest（无 ok 字段），以 record_file + has_more 识别成功
        if isinstance(value, dict) and "record_file" in value and "has_more" in value:
            payload = value
            break
    if not payload or (payload.get("ok") is not True and "record_file" not in payload):
        raise SyncError("lark-cli 未返回 {ok:true} 成功契约")
    ignored = find_ignored_fields(payload)
    if ignored:
        raise SyncError(f"lark-cli 忽略字段，停止同步：{ignored}")
    return payload


def run_cli(command, cwd):
    result = subprocess.run(command, capture_output=True, text=True, timeout=180, cwd=cwd)
    payload = parse_cli_contract(result)
    return result, payload


def parse_record_file(path, primary_key):
    mapping = {}
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SyncError(f"Base 查询结果第 {line_number} 行不是有效 NDJSON") from exc
        fields = row.get("fields") if isinstance(row.get("fields"), dict) else row
        key_value = fields.get(primary_key)
        record_id = row.get("record_id") or row.get("id")
        if isinstance(key_value, list) and len(key_value) == 1:
            key_value = key_value[0]
        if key_value is None or not record_id:
            raise SyncError(f"Base 查询结果缺少 {primary_key} 或 record_id，停止同步")
        key_value = str(key_value)
        if key_value in mapping and mapping[key_value] != record_id:
            raise SyncError(f"远端 {primary_key}={key_value} 存在多条记录，停止同步")
        mapping[key_value] = str(record_id)
    return mapping


def cli_prefix(sync):
    command = ["lark-cli"]
    profile = str((sync or {}).get("profile", "")).strip()
    if profile:
        command += ["--profile", profile]
    return command


def cli_identity(sync):
    return str((sync or {}).get("identity", "user")).strip() or "user"


def query_existing(base_token, table_id, primary_key, temp_dir, sync, output_name=None):
    output_name = output_name or f"{table_id}.ndjson"
    output = Path(temp_dir) / output_name
    result, payload = run_cli(cli_prefix(sync) + [
        "base", "+record-list", "--base-token", base_token,
        "--table-id", table_id, "--field-id", primary_key, "--limit", "2000",
        "--format", "ndjson", "--output", output_name, "--as", cli_identity(sync),
    ], cwd=temp_dir)
    summary = result.stdout + "\n" + result.stderr + "\n" + json.dumps(payload, ensure_ascii=False)
    if re.search(r'has_more["\s:=]+true', summary, re.I):
        raise SyncError(f"表 {table_id} 超过单次查询上限，无法可靠完成主键查重")
    if not output.exists():
        raise SyncError(f"表 {table_id} 查询未产生 NDJSON 输出，停止同步")
    return parse_record_file(output, primary_key)


def write_payload(temp_dir, name, payload):
    path = Path(temp_dir) / name
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return name


def execute_plan(plan, package, sync):
    if shutil.which("lark-cli") is None:
        raise SyncError("未找到 lark-cli，未执行网络写入")
    base_token = str(sync["base_token"])
    table_ids = sync["table_ids"]
    results = []
    plan["network_attempted"] = True
    with tempfile.TemporaryDirectory(prefix="ai-radar-lark-sync-") as temp_dir:
        for table in TABLES:
            table_id = str(table_ids[table])
            primary_key = PRIMARY_KEYS[table]
            existing = query_existing(base_token, table_id, primary_key, temp_dir, sync)
            creates = []
            updates = {}
            for row in package[table]:
                fields = build_fields(table, row)
                record_id = existing.get(str(row[primary_key]))
                if record_id:
                    updates[record_id] = fields
                else:
                    creates.append(fields)

            table_result = {"table": table, "table_id": table_id, "looked_up": len(existing),
                            "created": 0, "updated": 0, "batches": []}
            for index, batch in enumerate(chunks(creates), start=1):
                payload = write_payload(temp_dir, f"{table}-create-{index}.json", {"create_records": batch})
                result, _ = run_cli(cli_prefix(sync) + [
                    "base", "+record-batch-create", "--base-token", base_token,
                    "--table-id", table_id, "--json", f"@{payload}", "--as", cli_identity(sync),
                ], cwd=temp_dir)
                table_result["created"] += len(batch)
                table_result["batches"].append({"operation": "create", "batch": index,
                                                "count": len(batch), "cli_stdout": result.stdout.strip()})
            update_items = list(updates.items())
            for index, batch in enumerate(chunks(update_items), start=1):
                payload_body = {"update_records": {record_id: fields for record_id, fields in batch}}
                payload = write_payload(temp_dir, f"{table}-update-{index}.json", payload_body)
                result, _ = run_cli(cli_prefix(sync) + [
                    "base", "+record-batch-update", "--base-token", base_token,
                    "--table-id", table_id, "--json", f"@{payload}", "--as", cli_identity(sync),
                ], cwd=temp_dir)
                table_result["updated"] += len(batch)
                table_result["batches"].append({"operation": "update", "batch": index,
                                                "count": len(batch), "cli_stdout": result.stdout.strip()})
            expected_keys = {str(row[primary_key]) for row in package[table]}
            accepted = {}
            missing_keys = sorted(expected_keys)
            verify_attempts = 0
            for verify_attempts in range(1, 4):
                accepted = query_existing(
                    base_token, table_id, primary_key, temp_dir, sync,
                    output_name=f"{table_id}-verify-{verify_attempts}.ndjson",
                )
                missing_keys = sorted(expected_keys - set(accepted))
                if not missing_keys:
                    break
                if verify_attempts < 3:
                    time.sleep(1)
            table_result["verify_attempts"] = verify_attempts
            table_result["verified"] = len(expected_keys) - len(missing_keys)
            table_result["missing_primary_keys"] = missing_keys
            if missing_keys:
                plan["status"] = "NEEDS_RECONCILIATION"
                plan["mode"] = "confirmed"
                plan["results"] = results + [table_result]
                plan["reasons"] = [f"{table}.{primary_key} 写后回读 3 次仍缺少 {len(missing_keys)} 个稳定主键"]
                return plan
            results.append(table_result)
    plan["status"] = "SYNCED"
    plan["mode"] = "confirmed"
    plan["results"] = results
    return plan


def default_config_path():
    return Path(__file__).resolve().parent.parent / "config" / "runtime.json"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="生成或执行 AI 雷达知识包的飞书 Base upsert 计划")
    parser.add_argument("package", help="build_knowledge_package.py 生成的 JSON package")
    parser.add_argument("--config", default=str(default_config_path()), help="默认 config/runtime.json")
    parser.add_argument("--output", help="计划/结果 JSON；默认与 package 同目录")
    parser.add_argument("--confirm", action="store_true", help="配置完整时显式允许网络查重与写入")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    output = args.output or str(Path(args.package).with_suffix(".upsert-plan.json"))
    try:
        package = read_json(args.package, "knowledge package")
        validate_package(package)
        config = read_json(args.config, "runtime config")
        sync, missing = config_gate(config)
        if missing:
            plan = build_plan(package, sync, "dry-run", "SYNC_PENDING", missing)
        elif not args.confirm:
            plan = build_plan(package, sync, "dry-run", "DRY_RUN_READY",
                              ["未提供 --confirm；未查询或写入远端"])
        else:
            plan = build_plan(package, sync, "confirmed", "SYNC_IN_PROGRESS")
            plan = execute_plan(plan, package, sync)
        atomic_write_json(output, plan)
        print(json.dumps({"status": plan["status"], "output": str(Path(output).resolve()),
                          "network_attempted": plan["network_attempted"],
                          "reasons": plan.get("reasons", [])}, ensure_ascii=False))
        if args.confirm and plan.get("network_attempted") and plan["status"] != "SYNCED":
            return 2
        return 0
    except (SyncError, OSError, subprocess.SubprocessError) as exc:
        failure = {"status": "SYNC_FAILED", "network_attempted": bool(args.confirm), "error": str(exc)}
        try:
            atomic_write_json(output, failure)
        except OSError:
            pass
        print(json.dumps(failure, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
