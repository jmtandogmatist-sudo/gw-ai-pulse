"""读取 config/ 下的配置与钥匙串里的密钥。"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Config:
    root: Path
    settings: dict
    sources: list[dict]
    questions: list[dict]
    tags: dict
    blocked_domains: list[str]
    queries: dict = None  # config/queries.yaml（A/B 层检索词库），缺失时为空
    topics: dict = None   # config/topics.yaml（首页 05 主题追踪的白名单），缺失时为空

    @property
    def question_ids(self) -> set[str]:
        return {q["id"] for q in self.questions}

    @property
    def domains(self) -> list[str]:
        """领域显示名列表；tags.yaml 里 domains 是 {显示名: 论坛标签}。"""
        return list(self.tags["domains"])

    @property
    def topic_tags(self) -> list[str]:
        """主题显示名列表；tags.yaml 里 topics 是 {显示名: 论坛标签}，由主题档案 skill 维护。"""
        return list(self.tags.get("topics") or {})

    def all_forum_tags(self) -> list[str]:
        t = self.tags
        out = list(t["domains"].values()) + list((t.get("topics") or {}).values())
        out += [q.lower() for q in self.question_ids]
        out += ["cred-green", "cred-yellow", "cred-red", "auto", "reviewed"]
        # gw-ai-pulse（2026-11-19）：今日焦点长文额外打的标签（settings.focus.tag）。
        # 放在这里，是为了「新论坛要预先建好哪些标签」的清单里不缺它——
        # 漏了它，第一次出焦点时 update_tags 会撞上「标签不存在」。
        focus_tag = (self.settings.get("focus") or {}).get("tag")
        if focus_tag:
            out.append(focus_tag)
        return sorted(set(out))


def load_config(root: Path | None = None) -> Config:
    root = Path(root or os.environ.get("AIR_ROOT") or ROOT)
    cfg = root / "config"
    reach = _load(cfg / "link-reachability.yaml")
    return Config(
        root=root,
        settings=_load(cfg / "settings.yaml"),
        sources=_load(cfg / "sources.yaml").get("sources", []),
        questions=_load(cfg / "questions.yaml").get("questions", []),
        tags=_load(cfg / "tags.yaml"),
        blocked_domains=[d.lower() for d in reach.get("blocked", [])],
        queries=_load(cfg / "queries.yaml") if (cfg / "queries.yaml").is_file() else {},
        topics=_load(cfg / "topics.yaml") if (cfg / "topics.yaml").is_file() else {},
    )


# ---------- 信源实际覆盖 ----------
# 与 sources/fetchers.py 的 MANUAL_KINDS 保持一致：没有订阅接口的官方页面，
# 采集器不抓，由每日精选 skill 按清单人工核查。
MANUAL_KINDS = ("page",)


def select_sources(sources: list[dict], modules: list[str] | None = None) -> list[dict]:
    """主任务真正会抓取的信源。

    与 pipeline.run_round 第 1 步完全同源（2026-11-19 抽出）：启用 → 非 page →
    属于本轮的 schedule（默认 round）。抽出来的原因：此前「配置里登记了什么」和
    「实际抓了什么」没有同一份判据——78 个登记源里只有 40 个真的跑，
    而运行报表的 modules 字段是从「已选中的信源」反推的，永远看不出谁被静默跳过。
    """
    out = [x for x in sources
           if x.get("enabled", True) and x.get("kind") not in MANUAL_KINDS]
    if modules:
        return [x for x in out if x.get("module") in modules]
    return [x for x in out if x.get("schedule", "round") == "round"]


def skipped_sources(sources: list[dict], modules: list[str] | None = None) -> dict:
    """没被 select_sources 选中的信源 → 原因码。

    2026-11-19：加这个字段的直接原因是一次误判——运行报表里的 modules 是从
    「已选中的信源」反推的，看报表的人（包括我）会以为 internal 模块是被 --modules
    排除的，实际是那两个源 enabled: false。报表必须能回答「谁没跑、为什么没跑」。

    同名多通道（OpenAI / Anthropic / DeepMind 各有多条）在这里会按名字合并，
    只留最后一个原因；这只影响报表可读性，不影响采集。
    """
    chosen = {id(x) for x in select_sources(sources, modules)}
    out = {}
    for x in sources:
        if id(x) in chosen:
            continue
        if not x.get("enabled", True):
            reason = "disabled"
        elif x.get("kind") in MANUAL_KINDS:
            reason = "page"
        elif modules and x.get("module") not in modules:
            reason = "module"
        else:
            reason = f"schedule={x.get('schedule')}"
        out[x.get("name")] = reason
    return out


def coverage_summary(cfg: Config) -> dict:
    """登记 → 启用 → 实跑 的三层计数，以及没实跑的原因分布。

    2026-11-19：纯粹为了让「启用」和「实跑」不再混为一谈。当前生产配置是
    78 登记 / 52 启用 / 40 实跑，差额全在 page 源和 schedule 上；不把这层差
    显式打出来，看配置的人会以为覆盖了 52 个源。
    """
    skipped = skipped_sources(cfg.sources)
    reasons: dict[str, int] = {}
    for reason in skipped.values():
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "registered": len(cfg.sources),
        "enabled": sum(1 for x in cfg.sources if x.get("enabled", True)),
        "runnable": len(select_sources(cfg.sources)),
        "not_runnable": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
    }


def coverage_warnings(cfg: Config) -> list[str]:
    """覆盖缺陷提示。只打印，不阻断运行。

    为什么是警告不是错误：白名单写错只会让一手源「少收」，不会写坏数据；
    放进 validate_config 的硬错误里，会让一轮采集因为一条注释性配置而整轮不跑——
    这正是本项目最忌讳的失败模式（首页整天停更）。

    检查的是 keep_bypass 白名单里的名字是否真的抓得到。2026-11-19 实测：
    26 条名单里 10 条只有 kind=page 通道（Meta / Microsoft / xAI / 通义 / 豆包 /
    智谱 / 月之暗面 / MiniMax / 百度 / 腾讯混元），补判逻辑对它们一次都不会触发，
    名单看起来覆盖了 26 家，实际只覆盖 16 家。
    """
    out = []
    runnable = {x.get("name") for x in select_sources(cfg.sources)}
    known = {x.get("name") for x in cfg.sources}
    for name in (cfg.settings.get("keep_bypass") or {}).get("sources") or []:
        if name not in known:
            out.append(f"白名单 {name}：sources.yaml 里没有这个信源名（拼写错误？）")
        elif name not in runnable:
            out.append(f"白名单 {name}：登记了但抓不到（page 源 / schedule 非 round / 已停用），这条白名单不生效")
    return out


def validate_config(cfg: Config) -> list[str]:
    """返回配置问题清单；空列表表示通过。"""
    problems = []
    s = cfg.settings
    for key in ("discourse", "llm", "round", "feed"):
        if key not in s:
            problems.append(f"settings.yaml 缺少 {key}")
    ids = cfg.question_ids
    if not ids:
        problems.append("questions.yaml 没有任何问题")
    domains = set(cfg.domains)
    for src in cfg.sources:
        name = src.get("name", "?")
        kinds = ("rss", "atom", "listpage", "discourse", "page", "changelog", "sitemap",
                 "github_release", "github_repo", "github_topic",
                 "hn_top", "github_trending", "hf_trending")
        if src.get("kind") not in kinds:
            problems.append(f"信源 {name}：kind 只能是 {' / '.join(kinds)}")
        if src.get("kind") in ("github_release", "github_repo") and not src.get("repo"):
            problems.append(f"信源 {name}：{src.get('kind')} 需要 repo")
        if src.get("kind") == "github_topic" and not src.get("topic"):
            problems.append(f"信源 {name}：github_topic 需要 topic")
        if src.get("kind") == "sitemap" and not src.get("url_pattern"):
            problems.append(f"信源 {name}：sitemap 需要 url_pattern（否则会把整站抓进来）")
        elif src.get("kind") not in ("github_release", "github_repo", "github_topic", "discourse")                 and not src.get("url"):
            problems.append(f"信源 {name}：缺少 url")
        if src.get("tier") not in ("authority", "official", "media", "research", "community"):
            problems.append(f"信源 {name}：tier 不合法")
        for q in src.get("questions", []):
            if q not in ids:
                problems.append(f"信源 {name}：问题 {q} 不在 questions.yaml")
        if src.get("default_domain") and src["default_domain"] not in domains:
            problems.append(f"信源 {name}：default_domain 不在 tags.yaml")
    return problems


# ---------- 密钥 ----------

# gw-ai-pulse（2026-11-13）：钥匙串服务名改为本环境专用，可用 AIR_KEYCHAIN_SERVICE 覆盖。
# 旧环境（ai-radar-collector）的条目原样保留，两者互不影响，便于回滚。
KEYCHAIN_SERVICE = os.environ.get("AIR_KEYCHAIN_SERVICE") or "gw-ai-pulse"


def get_secret(name: str) -> str:
    """先读环境变量 AIR_<NAME>，再读 macOS 钥匙串（service=ai-radar-collector, account=<name>）。"""
    env = os.environ.get("AIR_" + name.upper().replace("-", "_"))
    if env:
        return env
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    raise RuntimeError(f"找不到密钥 {name}：请设置环境变量 AIR_{name.upper()} 或存入钥匙串")
