#!/usr/bin/env python3
"""rd.py — 输出闸门（Read-Digest）。

任何「可能把大块输出灌进 Agent 上下文」的读取或命令，都走这里：

  - 单次回显硬上限 MAX_CHARS（默认 4096），超限即截断；
  - 被截断的全文自动落盘，回显只给路径，绝不整文件回读；
  - 每次调用向 stderr 报一行「回显 N / 原始 M 字符」，让上下文预算可见。

设计意图（2026-11-04 上下文溢出复盘）：把「别把 497KB JSON 打印出来」从
一条提醒变成一次不可能发生的操作——这是代码闸门，不是纪律。

用法：
  rd.py head  FILE [N]              前 N 行（默认 40）
  rd.py tail  FILE [N]              后 N 行
  rd.py grep  FILE PATTERN         正则匹配行（带行号，最多 40 行）
  rd.py keys  FILE.json [PATH]     JSON 结构摘要（键 / 类型 / 长度）
  rd.py jq    FILE.json K [K ...]  只取指定键（支持 a.b.0.c 点路径）
  rd.py count FILE                 行数 / 字符数 / 字节数
  rd.py stat  FILE ...             大小 / 修改时间 / sha256 前 12 位
  rd.py run   CMD [ARGS ...]       执行命令并捕获输出（超限落盘）

通用参数：
  --max-chars N   单次回显上限（默认 4096）
  --no-spill      超限时不落盘，仅截断
  --quiet         不打印 stderr 预算行

退出码：0 成功 / 2 用法或文件错误 / 子命令 run 时透传被调命令退出码
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_MAX_CHARS = 4096
SPILL_SUBDIR = ".rd-spill"
DEFAULT_GREP_LINES = 40
DEFAULT_HEAD_LINES = 40
MAX_KEY_LINES = 60
MAX_KEYS_PER_OBJECT = 30
MAX_KEY_DEPTH = 3


def work_dir() -> Path:
    """落盘目录：优先环境变量，其次项目默认路径，最后当前目录。"""
    env = os.environ.get("AI_RADAR_WORK_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    default = Path.home() / "WorkBuddy" / "ai-radar-daily"
    if default.is_dir():
        return default
    return Path.cwd()


def _safe_label(label: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_.\-]+", "_", label).strip("_")
    return cleaned[:60] or "out"


def spill(text: str, label: str, no_spill: bool, suffix: str = ".txt"):
    if no_spill:
        return None
    target_dir = work_dir() / SPILL_SUBDIR
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{_safe_label(label)}-{datetime.now():%H%M%S}-{os.getpid()}{suffix}"
        path.write_text(text, encoding="utf-8")
        return path
    except OSError:
        return None


def emit(text, label, max_chars, no_spill, quiet, extra_hint=None):
    """统一回显入口：截断 + 落盘 + 预算行。"""
    total = len(text)
    truncated = total > max_chars
    shown = text[:max_chars] if truncated else text
    sys.stdout.write(shown)
    if shown and not shown.endswith("\n"):
        sys.stdout.write("\n")

    if truncated:
        spill_path = spill(text, label, no_spill)
        line_count = text.count("\n") + 1
        percent = max(1, max_chars * 100 // total)
        print(f"\n… 已截断：回显 {max_chars}/{total} 字符（{percent}%），全文 {line_count} 行")
        if spill_path:
            print(f"全文落盘：{spill_path}")
        print(extra_hint or "继续定位请用 rd.py grep / keys / jq / head <n>，不要整文件回读。")

    if not quiet:
        emitted = max_chars if truncated else total
        tail = " · 已截断" if truncated else ""
        sys.stderr.write(f"[rd] {label} → 回显 {emitted}/{total} 字符（上限 {max_chars}）{tail}\n")
    return 0


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _need_file(path_value: str) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_file():
        print(f"rd: 文件不存在：{path}", file=sys.stderr)
        raise SystemExit(2)
    return path


def cmd_head(args):
    path = _need_file(args.target)
    lines = read_text(path).splitlines()
    n = args.count if args.count is not None else DEFAULT_HEAD_LINES
    return emit("\n".join(lines[:n]) + ("\n" if lines[:n] else ""), f"head:{path.name}", args.max_chars, args.no_spill, args.quiet)


def cmd_tail(args):
    path = _need_file(args.target)
    lines = read_text(path).splitlines()
    n = args.count if args.count is not None else DEFAULT_HEAD_LINES
    return emit("\n".join(lines[-n:]) + ("\n" if lines[-n:] else ""), f"tail:{path.name}", args.max_chars, args.no_spill, args.quiet)


def cmd_grep(args):
    path = _need_file(args.target)
    try:
        pattern = re.compile(args.pattern)
    except re.error as exc:
        print(f"rd: 正则无效：{exc}", file=sys.stderr)
        return 2
    limit = args.count if args.count is not None else DEFAULT_GREP_LINES
    hits = []
    for number, line in enumerate(read_text(path).splitlines(), 1):
        if pattern.search(line):
            hits.append(f"{number}:{line}")
            if len(hits) >= limit:
                break
    body = "\n".join(hits) + ("\n" if hits else "")
    if not hits:
        body = f"（无匹配：{args.pattern}）\n"
    return emit(body, f"grep:{path.name}", args.max_chars, args.no_spill, args.quiet)


def _short_value(value):
    if isinstance(value, str):
        text = value if len(value) <= 60 else value[:57] + "…"
        return json.dumps(text, ensure_ascii=False)
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        return f"dict{{{len(value)}}}"
    return type(value).__name__


def _describe(node, prefix, depth, out):
    if len(out) >= MAX_KEY_LINES:
        return
    if isinstance(node, dict):
        keys = list(node.keys())
        for key in keys[:MAX_KEYS_PER_OBJECT]:
            if len(out) >= MAX_KEY_LINES:
                return
            child = node[key]
            path = f"{prefix}.{key}" if prefix else str(key)
            out.append(f"  {path} = {_short_value(child)}")
            if depth + 1 < MAX_KEY_DEPTH and isinstance(child, (dict, list)):
                _describe(child, path, depth + 1, out)
        if len(keys) > MAX_KEYS_PER_OBJECT:
            out.append(f"  … 另 {len(keys) - MAX_KEYS_PER_OBJECT} 个键未展开")
    elif isinstance(node, list):
        for index, child in enumerate(node[:MAX_KEYS_PER_OBJECT]):
            if len(out) >= MAX_KEY_LINES:
                return
            path = f"{prefix}[{index}]"
            out.append(f"  {path} = {_short_value(child)}")
            if depth + 1 < MAX_KEY_DEPTH and isinstance(child, (dict, list)):
                _describe(child, path, depth + 1, out)
        if len(node) > MAX_KEYS_PER_OBJECT:
            out.append(f"  … 另 {len(node) - MAX_KEYS_PER_OBJECT} 项未展开")


def _load_json(path: Path):
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        print(f"rd: JSON 解析失败 {path.name}：{exc}", file=sys.stderr)
        raise SystemExit(2)


def _walk(node, path):
    current = node
    for part in [p for p in str(path).split(".") if p != ""]:
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                raise KeyError(path)
        elif isinstance(current, dict):
            if part not in current:
                raise KeyError(path)
            current = current[part]
        else:
            raise KeyError(path)
    return current


def cmd_keys(args):
    path = _need_file(args.target)
    data = _load_json(path)
    node = data
    header = f"$ ({type(data).__name__})"
    if args.pattern:
        try:
            node = _walk(data, args.pattern)
        except KeyError:
            print(f"rd: 路径不存在：{args.pattern}", file=sys.stderr)
            return 2
        header = f"$.{args.pattern} = {_short_value(node)}"
    lines = [header]
    _describe(node, args.pattern or "", 0, lines)
    return emit("\n".join(lines) + "\n", f"keys:{path.name}", args.max_chars, args.no_spill, args.quiet)


def cmd_jq(args):
    path = _need_file(args.target)
    data = _load_json(path)
    lines = []
    for key in args.keys:
        try:
            value = _walk(data, key)
        except KeyError:
            lines.append(f"{key} = <路径不存在>")
            continue
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        elif isinstance(value, str):
            rendered = value
        else:
            rendered = json.dumps(value, ensure_ascii=False)
        lines.append(f"{key} = {rendered}")
    return emit("\n".join(lines) + "\n", f"jq:{path.name}", args.max_chars, args.no_spill, args.quiet)


def cmd_count(args):
    path = _need_file(args.target)
    raw = read_text(path)
    body = f"{path.name}\n  行数 {raw.count(chr(10)) + 1}\n  字符 {len(raw)}\n  字节 {path.stat().st_size}\n"
    return emit(body, f"count:{path.name}", args.max_chars, args.no_spill, args.quiet)


def cmd_stat(args):
    lines = []
    for value in args.targets:
        path = Path(value).expanduser()
        if not path.is_file():
            lines.append(f"{value} — 不存在")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{path.name} — {path.stat().st_size:,} 字节 · {stamp} · sha256:{digest}")
    return emit("\n".join(lines) + "\n", "stat", args.max_chars, args.no_spill, args.quiet)


def cmd_run(args):
    if not args.targets:
        print("rd: run 需要命令", file=sys.stderr)
        return 2
    try:
        done = subprocess.run(args.targets, capture_output=True, text=True, errors="replace")
    except FileNotFoundError as exc:
        print(f"rd: 命令不存在：{exc}", file=sys.stderr)
        return 2
    label = "run:" + " ".join(args.targets[:2])
    banner = f"$ {shlex.join(args.targets)}\n[exit {done.returncode}]\n"
    combined = banner + done.stdout
    if done.stderr.strip():
        combined += "\n--- stderr ---\n" + done.stderr
    code = emit(combined, label, args.max_chars, args.no_spill, args.quiet,
                extra_hint="继续定位请用 rd.py grep / keys / jq，或把命令改为只打印摘要。")
    return done.returncode if code == 0 else code


def _globals_parser():
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help=f"单次回显上限（默认 {DEFAULT_MAX_CHARS}）")
    parser.add_argument("--no-spill", action="store_true", help="超限时不落盘")
    parser.add_argument("--quiet", action="store_true", help="不打印 stderr 预算行")
    return parser


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(prog="rd.py", description="输出闸门：读取与命令输出统一截断 + 落盘",
                                     parents=[_globals_parser()])
    subs = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (("head", "前 N 行"), ("tail", "后 N 行")):
        sub = subs.add_parser(name, help=help_text)
        sub.add_argument("target")
        sub.add_argument("count", nargs="?", type=int)

    sub = subs.add_parser("grep", help="正则匹配行")
    sub.add_argument("target")
    sub.add_argument("pattern")
    sub.add_argument("count", nargs="?", type=int)

    sub = subs.add_parser("keys", help="JSON 结构摘要")
    sub.add_argument("target")
    sub.add_argument("pattern", nargs="?")

    sub = subs.add_parser("jq", help="只取指定键")
    sub.add_argument("target")
    sub.add_argument("keys", nargs="+")

    sub = subs.add_parser("count", help="行数/字符数/字节数")
    sub.add_argument("target")

    sub = subs.add_parser("stat", help="大小/修改时间/hash")
    sub.add_argument("targets", nargs="+")

    sub = subs.add_parser("run", help="执行命令并捕获输出（其后 token 原样透传，可用 -- 显式分隔）")
    sub.add_argument("targets", nargs="*")

    return parser


def _restore_default_sigpipe():
    """被 `| head` 等下游提前关闭管道时，按 Unix 惯例由 SIGPIPE 终止，避免关停期刷屏报错。"""
    try:
        import signal

        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError, ImportError):
        pass


def main(argv=None):
    _restore_default_sigpipe()
    argv = list(sys.argv[1:] if argv is None else argv)
    # run 子命令单独解析：其后的 token 原样作为命令，避免被调命令的 -c / --flag 被 argparse 吞掉。
    if "run" in argv:
        index = argv.index("run")
        args = _globals_parser().parse_args(argv[:index])
        command = argv[index + 1:]
        if command and command[0] == "--":
            command = command[1:]
        args.command = "run"
        args.targets = command
        return cmd_run(args)

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_chars < 200:
        print("rd: --max-chars 不得小于 200", file=sys.stderr)
        return 2
    handler = {
        "head": cmd_head,
        "tail": cmd_tail,
        "grep": cmd_grep,
        "keys": cmd_keys,
        "jq": cmd_jq,
        "count": cmd_count,
        "stat": cmd_stat,
        "run": cmd_run,
    }[args.command]
    if args.command in {"head", "tail", "grep", "keys", "jq", "count"}:
        args.targets = [args.target]
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
