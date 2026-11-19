"""把日报 skill 5.2.0 的 config/sources.json 迁移成采集器的 config/sources.yaml（T10）。

用法：python scripts/migrate_sources.py <日报 skill 目录>/config/sources.json
迁移规则：
  - 原 tier T0（重点官方源）→ official；信号层（layer=signal）按来源性质映射为 community / research
  - 原 topics → 默认领域与问题：模型发布 → 选型 / Q03；AI编程、Agent基础设施 → 工具做法 / Q04
  - 原 type=page（无订阅接口）保留为 kind=page：采集器跳过，由每日精选 skill 按清单人工核查
  - 原 verified / status / note 原样带过来
新增模块（能源政策、风电同行、安全、算力、集团内部）在 EXTRA 里，地址都还没在公司网络实测。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

TOPIC_MAP = {"模型发布": ("选型", ["Q03"]), "AI编程": ("工具做法", ["Q04"]), "Agent基础设施": ("工具做法", ["Q04"])}
SIGNAL_MAP = {
    "hn_top": ("community", "行业动态", ["Q04"], "ext_ai"),
    "atom": ("research", "前沿研究", ["Q01", "Q04"], "ext_ai"),
    "github_trending": ("community", "工具做法", ["Q04"], "ext_ai"),
    "hf_trending": ("community", "选型", ["Q03"], "models"),
}

EXTRA = [
    # ---------- 国内 AI 媒体（日报里作为 A/B 层检索的来源，这里改为订阅） ----------
    {"name": "量子位", "module": "ext_ai", "kind": "rss", "url": "https://www.qbitai.com/feed", "tier": "media",
     "default_domain": "行业动态", "questions": ["Q03", "Q04"], "verified": False},
    {"name": "机器之心", "module": "ext_ai", "kind": "rss", "url": "https://www.jiqizhixin.com/rss", "tier": "media",
     "default_domain": "行业动态", "questions": ["Q03", "Q04"], "verified": False},
    {"name": "IT之家 · AI", "module": "ext_ai", "kind": "rss", "url": "https://www.ithome.com/rss/", "tier": "media",
     "default_domain": "行业动态", "questions": ["Q03", "Q04"], "keywords": ["大模型", "智能体", "人工智能", "AI", "Agent"],
     "verified": False, "note": "ithome.com 已确认公司网络可访问"},

    # ---------- energy_policy 能源政策（列表页选择器要对着真实页面调，调好前保持关闭） ----------
    {"name": "国家能源局", "module": "energy_policy", "kind": "listpage", "url": "https://www.nea.gov.cn/", "tier": "authority",
     "default_domain": "政策合规", "questions": ["Q02", "Q06"], "include_keywords": ["人工智能", "智能化", "大模型", "算力", "数字化"],
     "selectors": {"item": "li", "link": "a", "date": "span"}, "enabled": False, "verified": False},
    {"name": "国家发展改革委", "module": "energy_policy", "kind": "listpage", "url": "https://www.ndrc.gov.cn/xwdt/", "tier": "authority",
     "default_domain": "政策合规", "questions": ["Q02", "Q05", "Q06"], "include_keywords": ["人工智能", "算力", "数据", "智能"],
     "selectors": {"item": "li", "link": "a", "date": "span"}, "enabled": False, "verified": False},
    {"name": "工业和信息化部", "module": "energy_policy", "kind": "listpage", "url": "https://www.miit.gov.cn/", "tier": "authority",
     "default_domain": "政策合规", "questions": ["Q05", "Q06"], "include_keywords": ["人工智能", "算力", "大模型", "智能体"],
     "selectors": {"item": "li", "link": "a", "date": "span"}, "enabled": False, "verified": False},
    {"name": "国家网信办", "module": "energy_policy", "kind": "listpage", "url": "https://www.cac.gov.cn/", "tier": "authority",
     "default_domain": "政策合规", "questions": ["Q06"], "include_keywords": ["人工智能", "生成式", "算法", "备案", "标识"],
     "selectors": {"item": "li", "link": "a", "date": "span"}, "enabled": False, "verified": False},

    # ---------- wind_peers 风电同行（T22：名单与地址待你确认，选择器待实测） ----------
    {"name": "北极星风力发电网", "module": "wind_peers", "kind": "listpage", "url": "https://news.bjx.com.cn/fd/", "tier": "media",
     "default_domain": "能源AI", "questions": ["Q01", "Q09"], "include_keywords": ["智能", "AI", "人工智能", "大模型", "数字化", "算法", "预测", "巡检"],
     "selectors": {"item": "li", "link": "a", "date": "span"}, "enabled": False, "verified": False, "note": "地址待核实"},
    {"name": "维斯塔斯", "module": "wind_peers", "kind": "listpage", "url": "https://www.vestas.com/en/media/company-news", "tier": "official",
     "default_domain": "能源AI", "questions": ["Q01"], "include_keywords": ["AI", "artificial intelligence", "digital", "machine learning", "data"],
     "selectors": {"item": "article", "link": "a"}, "enabled": False, "verified": False, "note": "地址待核实"},
    {"name": "西门子歌美飒", "module": "wind_peers", "kind": "listpage", "url": "https://www.siemensgamesa.com/global/en/home/press-releases.html", "tier": "official",
     "default_domain": "能源AI", "questions": ["Q01"], "include_keywords": ["AI", "artificial intelligence", "digital", "machine learning"],
     "selectors": {"item": "article", "link": "a"}, "enabled": False, "verified": False, "note": "地址待核实"},

    # ---------- security 安全 ----------
    {"name": "BleepingComputer", "module": "security", "kind": "rss", "url": "https://www.bleepingcomputer.com/feed/", "tier": "media",
     "default_domain": "安全", "questions": ["Q07"], "keywords": ["ai", "llm", "agent", "mcp", "chatgpt", "copilot", "prompt injection", "openai", "claude", "gemini"],
     "verified": False, "note": "bleepingcomputer.com 已确认公司网络可访问"},
    {"name": "The Register · 安全", "module": "security", "kind": "atom", "url": "https://www.theregister.com/security/headlines.atom", "tier": "media",
     "default_domain": "安全", "questions": ["Q07"], "keywords": ["ai", "llm", "agent", "mcp", "chatgpt", "copilot", "prompt injection"],
     "verified": False, "note": "theregister.com 已确认公司网络可访问"},

    # ---------- compute 算力 ----------
    {"name": "IT之家 · 算力", "module": "compute", "kind": "rss", "url": "https://www.ithome.com/rss/", "tier": "media",
     "default_domain": "算力", "questions": ["Q05", "Q09"], "keywords": ["算力", "芯片", "GPU", "英伟达", "昇腾", "出口管制", "数据中心", "智算"],
     "verified": False},

    # ---------- internal 集团内部（T23：等保密口径定了再启用） ----------
    {"name": "集团 AI 社区 · AI工具与办公", "module": "internal", "kind": "discourse", "category_id": 7,
     "url": "https://aicopilot.goldwind.com.cn:3211/c/7", "tier": "official", "default_domain": "集团内部", "questions": ["Q08"],
     "enabled": False, "verified": True},
    {"name": "集团 AI 社区 · 金风实践", "module": "internal", "kind": "discourse", "category_id": 16,
     "url": "https://aicopilot.goldwind.com.cn:3211/c/16", "tier": "official", "default_domain": "集团内部", "questions": ["Q08"],
     "enabled": False, "verified": True},
]


def convert(old: dict) -> dict:
    t = old["type"]
    out = {"id": old["id"], "name": old.get("entity") or old["id"]}
    if old.get("layer") == "signal":
        tier, domain, qs, module = SIGNAL_MAP[t]
        out.update(module=module, kind=t, tier=tier, default_domain=domain, questions=qs)
        if module == "models":
            out["schedule"] = "daily"
        if old.get("keywords"):
            out["keywords"] = old["keywords"]
        if old.get("min_heat") is not None:
            out["min_heat"] = old["min_heat"]
    else:
        domain, qs = "选型", ["Q03"]
        for tp in old.get("topics", []):
            if tp in TOPIC_MAP:
                domain, qs = TOPIC_MAP[tp]
                break
        out.update(module="ext_ai", kind=t, tier="official", default_domain=domain, questions=qs)
    if t == "github_release":
        out["repo"] = old["repo"]
    else:
        out["url"] = old["url"]
    out["enabled"] = bool(old.get("enabled", True)) and not str(old.get("status", "")).startswith("url_dead")
    out["verified"] = bool(old.get("verified", False))
    for k in ("status", "note"):
        if old.get(k):
            out[k] = old[k]
    return out


def main(path: str) -> None:
    old = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = [convert(s) for s in old["sources"]] + EXTRA
    header = (
        "# 信源清单（由 scripts/migrate_sources.py 生成：日报 skill 5.2.0 的 24 个源 + 新增模块）\n"
        "# 字段：name · module · kind(rss|atom|listpage|discourse|page|github_release|hn_top|github_trending|hf_trending)\n"
        "#       url/repo · tier(authority|official|media|research|community) · default_domain · questions\n"
        "#       keywords（\"default\" = 内置 AI 关键词）/ include_keywords / exclude_keywords · min_heat\n"
        "#       schedule(round|daily) · enabled · verified（是否已在公司网络实测）\n"
        "# kind=page 的源采集器不抓，由每日精选 skill 按清单人工核查。\n\n"
    )
    out = Path(__file__).resolve().parent.parent / "config" / "sources.yaml"
    out.write_text(header + yaml.safe_dump({"sources": rows}, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    print(f"写入 {out}：{len(rows)} 个信源")


if __name__ == "__main__":
    main(sys.argv[1])
