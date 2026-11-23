"""读取 config/brief.yaml 与 macOS 钥匙串密钥（service=gw-ai-pulse）。"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
KEYCHAIN_SERVICE = os.environ.get("AIR_KEYCHAIN_SERVICE") or "gw-ai-pulse"


@dataclass
class BriefConfig:
    root: Path
    raw: dict[str, Any]

    @property
    def discourse(self) -> dict[str, Any]:
        return self.raw.get("discourse") or {}

    @property
    def llm(self) -> dict[str, Any]:
        return self.raw.get("llm") or {}

    @property
    def search(self) -> dict[str, Any]:
        return self.raw.get("search") or {}

    @property
    def collector_sqlite(self) -> dict[str, Any]:
        return self.raw.get("collector_sqlite") or {}

    @property
    def verifier(self) -> dict[str, Any]:
        return self.raw.get("verifier") or {}

    @property
    def compliance(self) -> dict[str, Any]:
        return self.raw.get("compliance") or {}

    def skill_text(self, name: str) -> str:
        p = self.root / "skills" / f"{name}.md"
        return p.read_text(encoding="utf-8") if p.is_file() else ""


def load_config(root: Path | None = None) -> BriefConfig:
    base = Path(root or os.environ.get("BRIEF_ROOT") or ROOT)
    cfg_path = base / "config" / "brief.yaml"
    with open(cfg_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return BriefConfig(root=base, raw=data)


def get_secret(name: str) -> str:
    """优先读环境变量 BRIEF_<NAME> / AIR_<NAME>，其次读 macOS 钥匙串（service=gw-ai-pulse）。"""
    env_key = name.upper().replace("-", "_")
    for prefix in ("BRIEF_", "AIR_", ""):
        val = os.environ.get(prefix + env_key)
        if val and val.strip():
            return val.strip()
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    raise RuntimeError(f"找不到密钥 {name}（钥匙串 service={KEYCHAIN_SERVICE} 或环境变量 BRIEF_{env_key}）")
