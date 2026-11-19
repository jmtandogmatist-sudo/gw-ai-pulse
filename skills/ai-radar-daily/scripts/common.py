#!/usr/bin/env python3
"""AI 雷达日报脚本共享的配置、日期、状态与原子写工具。"""

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

REQUIRED_DISCOURSE_ENV = (
    "DISCOURSE_API_KEY",
    "DISCOURSE_API_USER",
    "DISCOURSE_URL",
)
DISCOURSE_ENV_ALIASES = {
    "DISCOURSE_API_USER": "DISCOURSE_API_USERNAME",
    "DISCOURSE_URL": "DISCOURSE_BASE_URL",
}


class ConfigurationError(RuntimeError):
    """运行配置缺失或无效。"""


def _read_env_file(path):
    values = {}
    for line_number, raw_line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigurationError(f"环境文件第 {line_number} 行格式无效")
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values


def load_discourse_config(env_file=None, cwd=None):
    """从显式环境文件或工作目录 .env 加载 Discourse 配置，缺失时拒绝运行。"""
    values = dict(os.environ)
    selected = Path(env_file).expanduser() if env_file else Path(cwd or os.getcwd()) / ".env"
    if env_file and not selected.is_file():
        raise ConfigurationError(f"指定的环境文件不存在：{selected}")
    if selected.is_file():
        file_values = _read_env_file(selected)
        allowed_names = set(REQUIRED_DISCOURSE_ENV) | set(DISCOURSE_ENV_ALIASES.values())
        for name, value in file_values.items():
            if name in allowed_names:
                values[name] = value

    for canonical, alias in DISCOURSE_ENV_ALIASES.items():
        if not str(values.get(canonical, "")).strip() and str(values.get(alias, "")).strip():
            values[canonical] = values[alias]

    missing = [name for name in REQUIRED_DISCOURSE_ENV if not str(values.get(name, "")).strip()]
    if missing:
        raise ConfigurationError(f"缺少必需配置：{', '.join(missing)}")

    base_url = values["DISCOURSE_URL"].strip().rstrip("/")
    if not base_url.startswith(("https://", "http://")):
        raise ConfigurationError("DISCOURSE_URL 必须是 HTTP(S) URL")
    return {
        "url": base_url,
        "api_key": values["DISCOURSE_API_KEY"].strip(),
        "api_user": values["DISCOURSE_API_USER"].strip(),
    }


def parse_timezone(name="Asia/Shanghai"):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"无效时区：{name}") from exc


def parse_report_date(value=None, timezone="Asia/Shanghai", default=None):
    """统一解析 YYYY-MM-DD；未显式给定时使用 default，最后回退到指定时区当天。"""
    parse_timezone(timezone)
    candidate = value if value not in (None, "") else default
    if isinstance(candidate, datetime):
        return candidate.astimezone(parse_timezone(timezone)).date()
    if isinstance(candidate, date):
        return candidate
    if candidate not in (None, ""):
        try:
            return datetime.strptime(str(candidate), "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("report_date 必须为 YYYY-MM-DD") from exc
    return datetime.now(parse_timezone(timezone)).date()


def atomic_write_json(path, data):
    """在目标文件同目录写临时文件，再用 os.replace 原子替换。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
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


def status_payload(status, code, message, **details):
    payload = {"status": status, "code": code, "message": message}
    payload.update({key: value for key, value in details.items() if value is not None})
    return payload


def emit_status(status, code, message, stream=None, **details):
    """输出单行结构化状态；调用方不得把凭据放入 details。"""
    payload = status_payload(status, code, message, **details)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stream)
    return payload
