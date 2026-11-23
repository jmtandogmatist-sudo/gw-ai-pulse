#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gw-ai-pulse —— 路径重绑定脚本 / path rebinding

把克隆进来的 skill 代码里硬编码的「旧环境路径」改写成 gw-ai-pulse 下的新路径。
典型来源：~/.workbuddy/skills/*（旧 WorkBuddy 环境）、~/Desktop/GWAIPulse/uat-runtime/*（UAT 环境）。

用法：
    .venv/bin/python rebind_paths.py plan     # 只读：列出将要发生的替换
    .venv/bin/python rebind_paths.py apply    # 执行（自动备份到 _rebind-backup/<时间戳>/）
    .venv/bin/python rebind_paths.py verify   # 校验：不应再残留旧路径

设计约束
  - 幂等：重复 apply 结果一致。
  - 可回溯：apply 前把每个将被修改的文件备份，并留一份 manifest JSON。
  - 只改 skills/ 与 01-采集器/ 下的文本文件；不碰 work/、_archive/、.venv/。
  - 不读不写 ~/.workbuddy/ 与 ~/WorkBuddy/ —— 只做字符串替换，不访问源目录。
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILLS = ROOT / "skills"
COLLECTOR = ROOT / "01-采集器"
LAUNCHD = ROOT / "launchd"
WORK = ROOT / "work"
BACKUP_DIR = ROOT / "_rebind-backup"

# 替换规则：顺序敏感，长路径必须排在短路径前面（否则 /skills/ai-radar-daily
# 会被 /skills 的规则先吃掉一半，产生 .../pulse/ai-radar-daily 这类错误路径）。
RULES: list[tuple[str, str]] = [
    # --- 旧 WorkBuddy 环境（~/.workbuddy/skills/*）---
    ("/Users/sr_jm/.workbuddy/skills/ai-radar-daily", str(SKILLS / "ai-radar-daily")),
    ("/Users/sr_jm/.workbuddy/skills/ai-tech-radar", str(SKILLS / "ai-tech-radar")),
    ("/Users/sr_jm/.workbuddy/skills/ai-topic-dossier", str(SKILLS / "ai-topic-dossier")),
    ("~/.workbuddy/skills/ai-radar-daily", str(SKILLS / "ai-radar-daily")),
    ("~/.workbuddy/skills/ai-tech-radar", str(SKILLS / "ai-tech-radar")),
    ("~/.workbuddy/skills/ai-topic-dossier", str(SKILLS / "ai-topic-dossier")),
    # --- UAT 运行环境（clone 自 uat-runtime）---
    ("/Users/sr_jm/Desktop/GWAIPulse/uat-runtime/skills/ai-radar-daily", str(SKILLS / "ai-radar-daily")),
    ("/Users/sr_jm/Desktop/GWAIPulse/uat-runtime/skills/ai-tech-radar", str(SKILLS / "ai-tech-radar")),
    ("/Users/sr_jm/Desktop/GWAIPulse/uat-runtime/skills/ai-topic-dossier", str(SKILLS / "ai-topic-dossier")),
    ("/Users/sr_jm/Desktop/GWAIPulse/uat-runtime/work/ai-radar-daily", str(WORK / "ai-radar-daily")),
    ("/Users/sr_jm/Desktop/GWAIPulse/uat-runtime/work/ai-tech-radar", str(WORK / "ai-tech-radar")),
    ("/Users/sr_jm/Desktop/GWAIPulse/uat-runtime/work/ai-topic-dossier", str(WORK / "ai-topic-dossier")),
    ("/Users/sr_jm/Desktop/GWAIPulse/uat-runtime/work", str(WORK)),
    ("/Users/sr_jm/Desktop/GWAIPulse/uat-runtime/01-采集器", str(COLLECTOR)),
    # --- 原 gw-ai-pulse 运行目录（桌面迁移）---
    ("/Users/sr_jm/Desktop/GlodWind/gw-ai-pulse", str(ROOT)),
    # --- 旧 WorkBuddy 工作目录 ---
    ("/Users/sr_jm/WorkBuddy/ai-radar-daily", str(WORK / "ai-radar-daily")),
    ("/Users/sr_jm/WorkBuddy/ai-tech-radar", str(WORK / "ai-tech-radar")),
    ("/Users/sr_jm/WorkBuddy/ai-topic-dossier", str(WORK / "ai-topic-dossier")),
    ("~/WorkBuddy/ai-radar-daily", str(WORK / "ai-radar-daily")),
    ("~/WorkBuddy/ai-tech-radar", str(WORK / "ai-tech-radar")),
    ("~/WorkBuddy/ai-topic-dossier", str(WORK / "ai-topic-dossier")),
]

# verify 用：这些前缀出现即视为「仍有跨环境引用」
STALE_PREFIXES = [
    "/Users/sr_jm/.workbuddy/skills/",
    "/Users/sr_jm/WorkBuddy/",
    "~/WorkBuddy/",
    "/Users/sr_jm/Desktop/GWAIPulse/uat-runtime/",
    "/Users/sr_jm/Desktop/GlodWind/gw-ai-pulse",
]

# verify 允许白名单：文档里作为「历史沿革 / 对照」提及旧路径不算违规。
# 格式：相对 ROOT 的路径前缀 → 允许出现的次数上限之外还要人工复核，因此默认只跳过文档。
DOC_ONLY_SUFFIXES = {".md"}

TEXT_SUFFIXES = {".py", ".json", ".md", ".sh", ".yaml", ".yml", ".txt", ".cfg", ".toml", ".ini", ".plist"}
SKIP_DIRS = {"_rebind-backup", "_archive", ".git", "__pycache__", ".venv", "node_modules", "work", "logs"}


def roots() -> list[Path]:
    return [p for p in (SKILLS, COLLECTOR, LAUNCHD) if p.exists()]


def iter_files() -> list[Path]:
    out: list[Path] = []
    for base in roots():
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            rel_parts = set(p.relative_to(ROOT).parts)
            if rel_parts & SKIP_DIRS:
                continue
            if p.suffix.lower() not in TEXT_SUFFIXES:
                continue
            out.append(p)
    return sorted(out)


def read_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def scan() -> list[tuple[Path, str, str, int]]:
    hits: list[tuple[Path, str, str, int]] = []
    for p in iter_files():
        text = read_text(p)
        if text is None:
            continue
        for old, new in RULES:
            if old == new:
                continue
            n = text.count(old)
            if n:
                hits.append((p, old, new, n))
    return hits


def plan() -> int:
    hits = scan()
    if not hits:
        print("[OK] 没有需要替换的旧路径（可能已经 apply 过）")
        return 0
    total = sum(h[3] for h in hits)
    print(f"[PLAN] 命中 {len({h[0] for h in hits})} 个文件 / 共 {total} 次替换\n")
    for p, old, new, n in hits:
        print(f"  {p.relative_to(ROOT)}  x{n}")
        print(f"      {old}")
        print(f"   -> {new}")
    return 0


def apply() -> int:
    hits = scan()
    if not hits:
        print("[OK] 无可替换项，幂等退出")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = BACKUP_DIR / stamp
    files = sorted({h[0] for h in hits})

    manifest: dict[str, object] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(ROOT),
        "rules": RULES,
        "files": {},
    }
    for p in files:
        rel = p.relative_to(ROOT)
        dst = bdir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
        per_file = [{"old": o, "new": n, "count": c} for f, o, n, c in hits if f == p]
        manifest["files"][str(rel)] = per_file  # type: ignore[index]
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[BACKUP] {len(files)} 个文件 -> {bdir.relative_to(ROOT)}/\n")

    changed = 0
    for p in files:
        text = read_text(p)
        if text is None:
            continue
        new_text = text
        for old, new in RULES:
            if old != new:
                new_text = new_text.replace(old, new)
        if new_text != text:
            p.write_text(new_text, encoding="utf-8")
            changed += 1
            print(f"  [WROTE] {p.relative_to(ROOT)}")
    print(f"\n[DONE] 已改写 {changed} 个文件")
    return verify(quiet=True)


def verify(quiet: bool = False) -> int:
    """校验：代码与配置里不该再出现跨环境的绝对路径（文档中的历史沿革除外）。"""
    leftovers: list[tuple[Path, str, int]] = []
    doc_mentions: list[tuple[Path, str, int]] = []
    for p in iter_files():
        text = read_text(p)
        if text is None:
            continue
        for old in STALE_PREFIXES:
            n = text.count(old)
            if not n:
                continue
            if p.suffix.lower() in DOC_ONLY_SUFFIXES:
                doc_mentions.append((p, old, n))
            else:
                leftovers.append((p, old, n))

    # 硬性校验 1：所有 work_dir / env_file / skill_dir 必须落在 ROOT 内
    bad_bindings: list[str] = []
    for cfg in (SKILLS / "ai-radar-daily" / "config" / "runtime.json",
                SKILLS / "ai-tech-radar" / "config" / "radar.json",
                SKILLS / "ai-topic-dossier" / "config" / "topics.json"):
        if not cfg.exists():
            bad_bindings.append(f"{cfg.relative_to(ROOT)} 不存在")
            continue
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            bad_bindings.append(f"{cfg.relative_to(ROOT)} 不是合法 JSON：{exc}")
            continue
        wd = data.get("work_dir")
        if wd and not str(wd).startswith(str(ROOT)):
            bad_bindings.append(f"{cfg.relative_to(ROOT)} 的 work_dir 越界：{wd}")
        env = (data.get("paths") or {}).get("env_file")
        if env and not str(env).startswith(str(ROOT)):
            bad_bindings.append(f"{cfg.relative_to(ROOT)} 的 paths.env_file 越界：{env}")

    if leftovers:
        print(f"\n[FAIL] 代码/配置里仍残留 {len(leftovers)} 处跨环境路径：")
        for p, old, n in leftovers:
            print(f"  {p.relative_to(ROOT)} x{n}  <- {old}")
        return 1

    if bad_bindings:
        print(f"\n[FAIL] 配置绑定越界 {len(bad_bindings)} 处：")
        for b in bad_bindings:
            print(f"  {b}")
        return 1

    if not quiet:
        print("[OK] 代码/配置无跨环境路径残留")
    if doc_mentions:
        print(f"[INFO] 文档中仍有 {len(doc_mentions)} 处旧路径提及（历史沿革，非配置）：")
        for p, old, n in doc_mentions:
            print(f"  {p.relative_to(ROOT)} x{n}  <- {old}")
    print("[OK] 校验通过：所有运行期路径已指向 gw-ai-pulse/")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    if cmd == "plan":
        return plan()
    if cmd == "apply":
        return apply()
    if cmd == "verify":
        return verify()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
