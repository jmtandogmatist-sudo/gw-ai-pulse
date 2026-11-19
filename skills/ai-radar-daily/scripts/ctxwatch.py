#!/usr/bin/env python3
"""ctxwatch.py — 上下文水位守卫。

问题：Agent 看不见自己的上下文水位，只有当模型报 400 撞上 262,144 上限时才知道
（2026-11-04 实证：input_tokens 已达 100%，报错后才被迫中断重建）。

做法：把守卫放到**能看见水位的地方**——WorkBuddy 会话 JSONL 里每条助手消息都带
`usage.input_tokens`，这就是当轮真实提示词长度。本脚本读它，在到达阈值时输出
一行显式告警，强制当前阶段收尾、写交接物、换新会话。

用法：
  ctxwatch.py                       自动定位当前项目最近活跃的会话
  ctxwatch.py --session-file PATH   指定会话 JSONL
  ctxwatch.py --threshold 0.70      阈值（默认 0.70）
  ctxwatch.py --window 262144       上下文窗口（默认 262144）
  ctxwatch.py --json                结构化输出

退出码：0 = OK / 1 = WATCH / 2 = CHECKPOINT_NOW（可被脚本消费，用于阻断长流程）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_WINDOW = 262_144
DEFAULT_THRESHOLD = 0.70
WATCH_RATIO = 0.50
TAIL_BYTES = 4_000_000


def project_slug(work_dir: Path) -> str:
    return str(work_dir).strip("/").replace("/", "-")


def default_work_dir() -> Path:
    env = os.environ.get("AI_RADAR_WORK_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    default = Path.home() / "WorkBuddy" / "ai-radar-daily"
    return default if default.is_dir() else Path.cwd()


def newest_session_file(work_dir: Path) -> Path | None:
    override = os.environ.get("AI_RADAR_SESSION_FILE", "").strip()
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.is_file() else None
    project_dir = Path.home() / ".workbuddy" / "projects" / project_slug(work_dir)
    if not project_dir.is_dir():
        return None
    candidates = [path for path in project_dir.glob("*.jsonl") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def scan_tokens(session_file: Path):
    """返回 (last_input_tokens, peak_input_tokens, 最后一条 usage 的 output_tokens, 行数)。"""
    last_input = None
    last_output = None
    peak_input = 0
    lines = 0

    size = session_file.stat().st_size
    offset = max(0, size - TAIL_BYTES)
    with session_file.open("rb") as handle:
        if offset:
            handle.seek(offset)
            handle.readline()
        for raw in handle:
            try:
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            if not line:
                continue
            lines += 1
            if '"usage"' not in line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = _find_usage(payload)
            if not usage:
                continue
            value = usage.get("input_tokens")
            if isinstance(value, int):
                last_input = value
                last_output = usage.get("output_tokens", last_output)
                peak_input = max(peak_input, value)
    return last_input, peak_input, last_output, lines, size


def _find_usage(node):
    if isinstance(node, dict):
        usage = node.get("usage")
        if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int):
            return usage
        for value in node.values():
            found = _find_usage(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_usage(item)
            if found:
                return found
    return None


def verdict_of(ratio: float, threshold: float):
    if ratio >= threshold:
        return "CHECKPOINT_NOW", 2
    if ratio >= WATCH_RATIO:
        return "WATCH", 1
    return "OK", 0


def advice_of(verdict: str):
    if verdict == "CHECKPOINT_NOW":
        return ("立即收尾当前阶段：把阶段产物落盘（检索清单 / 正文 / 状态文件），"
                "在交接说明里写清下一步，然后开新会话继续。不要再启动新的检索或大批量读取。")
    if verdict == "WATCH":
        return "剩余额度不足以再跑一个完整阶段：优先用 verify.py 等固化命令收口，避免整文件回读。"
    return "水位健康，可继续。"


def build_parser():
    parser = argparse.ArgumentParser(prog="ctxwatch.py", description="上下文水位守卫")
    parser.add_argument("--session-file")
    parser.add_argument("--work-dir")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    try:
        import signal

        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError, ImportError):
        pass
    args = build_parser().parse_args(argv)
    work_dir = Path(args.work_dir).expanduser() if args.work_dir else default_work_dir()

    session_file = Path(args.session_file).expanduser() if args.session_file else newest_session_file(work_dir)
    if session_file is None or not session_file.is_file():
        payload = {"status": "NO_SESSION", "work_dir": str(work_dir),
                   "message": "未找到会话 JSONL；可用 --session-file 指定，或设 AI_RADAR_SESSION_FILE"}
        print(json.dumps(payload, ensure_ascii=False) if args.json else
              f"CONTEXT_STATUS: NO_SESSION\n{payload['message']}\n项目目录：{work_dir}")
        return 0

    last_input, peak_input, last_output, lines, size = scan_tokens(session_file)
    if last_input is None:
        payload = {"status": "NO_USAGE", "session_file": str(session_file),
                   "message": "会话文件中未找到 usage.input_tokens（可能尚未产生助手回合）"}
        print(json.dumps(payload, ensure_ascii=False) if args.json else
              f"CONTEXT_STATUS: NO_USAGE\n{payload['message']}")
        return 0

    ratio = last_input / args.window
    verdict, code = verdict_of(ratio, args.threshold)
    payload = {
        "status": verdict,
        "session_file": str(session_file),
        "session_bytes": size,
        "usage_records_scanned": lines,
        "input_tokens": last_input,
        "peak_input_tokens": peak_input,
        "output_tokens": last_output,
        "window": args.window,
        "watermark": round(ratio, 4),
        "threshold": args.threshold,
        "advice": advice_of(verdict),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return code

    filled = min(30, max(1, round(ratio * 30)))
    bar = "█" * filled + "·" * (30 - filled)
    print(f"CONTEXT_STATUS: {verdict}")
    print(f"WATERMARK: {ratio * 100:.1f}%  [{bar}]  {last_input:,} / {args.window:,} token")
    print(f"PEAK: {peak_input:,} token   会话文件 {size:,} 字节")
    print(f"SESSION: {session_file}")
    print(f"ADVICE: {payload['advice']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
