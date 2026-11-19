#!/usr/bin/env python3
"""V5 三层版式（2026-10-30 起）：解析、结构门禁与 V4 适配层。

V5 正文 = Discourse 原生 Markdown + [wrap=air-*] 区块 + <span data-air-chip> 行内标签，
由主题组件「AI 雷达日报样式 v2」（assets/discourse-theme）渲染成卡片。三层：
  第一层 · 精编日报   —— 今日要紧 / 金风相关 / 工具与做法 / 行业速递，沿用 V4 证据门禁
  第二层 · 本周跟踪   —— 窗口前 7 天内已报大事，一行带过
  第三层 · 信号雷达   —— 单一来源可入，必须标类别、可信度与来源名

设计：V5 原文先过 check_structure()（版式与三层规则），再经 to_v4() 把精编层转成 V4
结构，交给现有 self_check / preflight / 飞书推送 / 知识库构包复用，避免两套门禁口径分裂。
本模块不读写文件、不联网。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import title_spec  # V5.3.2 标题规范单一真源（H1 识别）

DAILY_WRAP = "[wrap=air-daily]"
LAYER_HEADINGS = ["## 第一层 · 精编日报", "## 第二层 · 本周跟踪", "## 第三层 · 信号雷达"]
TAIL_SECTIONS = ["## 社区今日", "## 今日一问"]
TAIL_START = "### 集团AI能力地图"
CORE_SECTIONS = ["今日要紧", "金风相关", "工具与做法", "行业速递"]
TRACK_CHIP = "跟踪"
SIGNAL_CATS = ("新品与工具", "开源与模型", "前沿论文", "行业动态", "安全与研究", "能源与金风", "社区热议", "传闻待证")
NO_ENERGY_LINE = "今日暂无经双源核验的金风相关重要消息。"
NO_TOOLS_LINE = "今日暂无新的可直接借鉴的做法。"
CRED_EMOJI = {"g": "🟢", "y": "🟡", "r": "🔴"}
CRED_DETAILS_V4 = {"g": "🟢 来源｜官方 + 独立交叉", "y": "🟡 来源｜当事方口径", "r": "🔴 来源｜单一来源"}

# 条数与字数（正文不含标题、标签行、“对我们”与来源行）
COUNT_LIMITS = {"今日要紧": (2, 8), "金风相关": (0, 2), "工具与做法": (0, 4), "行业速递": (3, 10),
                "跟踪": (0, 8), "雷达": (0, 40)}
BODY_LIMITS = {"今日要紧": 160, "金风相关": 160, "工具与做法": 120, "行业速递": 100, "跟踪": 100, "雷达": 100}
US_LIMIT = 90
TRACK_LOOKBACK_DAYS = 7

WRAP_OPEN_RE = re.compile(r'^\[wrap=([\w-]+)((?:\s+[\w-]+=(?:"[^"]*"|[^\s\]]+))*)\]\s*$')
WRAP_CLOSE_RE = re.compile(r"^\[/wrap\]\s*$")
ATTR_RE = re.compile(r'([\w-]+)=("[^"]*"|[^\s\]]+)')
CHIP_RE = re.compile(r'<span\s+data-air-chip="(\w+)"(?:\s+data-cred="(\w)")?\s*>(.*?)</span>')
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
DATE_SUFFIX_RE = re.compile(r"[（(](\d{1,2})月(\d{1,2})日[^）)]*[）)]\s*$")
STAT_NUM_RE = re.compile(r"^\*\*(\d+)\*\*$")
US_RE = re.compile(r"^\*\*对我们：\*\*\s*(.+)$")


def is_v5(raw: str) -> bool:
    return DAILY_WRAP in raw


def _attrs(s: str) -> Dict[str, str]:
    return {k: v.strip('"') for k, v in ATTR_RE.findall(s or "")}


def _visible_len(text: str) -> int:
    text = LINK_RE.sub(lambda m: m.group(1), text)
    text = re.sub(r"[*`>]", "", text)
    return len(re.sub(r"\s+", "", text))


# ---------------------------------------------------------------- 解析

def parse(raw: str) -> Dict[str, Any]:
    """把 V5 正文解析为结构模型；解析层只记录事实，判定放在 check_structure。"""
    lines = raw.splitlines()
    doc: Dict[str, Any] = {"h1": None, "mainline": None, "cover_lines": [], "stats": [], "mix": "",
                           "cards": [], "layer_order": [], "layer_free_lines": {1: [], 2: [], 3: []},
                           "tail_sections": {}, "tail_order": [], "wrap_issues": [], "daily_open": None,
                           "daily_close": None, "tail_start": None, "lede": {}}
    stack: List[Tuple[str, Dict[str, str], int]] = []
    layer = 0
    tail_name: Optional[str] = None
    card: Optional[Dict[str, Any]] = None
    sub: Optional[str] = None          # 卡片内当前子块：us / src
    stat: Optional[Dict[str, Any]] = None
    in_mix = False
    in_lede = False

    for no, line in enumerate(lines, start=1):
        s = line.strip()
        if s.count("[wrap=") > 1:
            doc["wrap_issues"].append((no, "同一行出现多个 [wrap=]，Discourse 会误配对（行内标签请用 <span data-air-chip>）"))
        if "[wrap=" in s and not WRAP_OPEN_RE.match(s):
            doc["wrap_issues"].append((no, "[wrap=…] 必须独占一行"))
        if "[/wrap]" in s and not WRAP_CLOSE_RE.match(s):
            doc["wrap_issues"].append((no, "[/wrap] 必须独占一行"))

        m_open = WRAP_OPEN_RE.match(s)
        if m_open:
            name, attrs = m_open.group(1), _attrs(m_open.group(2))
            stack.append((name, attrs, no))
            if name == "air-daily" and doc["daily_open"] is None:
                doc["daily_open"] = no
            elif name in ("air-card", "air-sig"):
                card = {"kind": "card" if name == "air-card" else "sig", "layer": layer, "line": no,
                        "cred": attrs.get("cred"), "cat": attrs.get("cat"), "chips": [], "title": None,
                        "body": [], "us": [], "links": [], "src_seen": False}
            elif name == "air-us" and card is not None:
                sub = "us"
            elif name == "air-src" and card is not None:
                sub = "src"
                card["src_seen"] = True
            elif name == "air-stat":
                stat = {"num": None, "label": []}
            elif name == "air-mix":
                in_mix = True
            elif name == "air-lede":
                in_lede = True
            continue
        if WRAP_CLOSE_RE.match(s):
            if not stack:
                doc["wrap_issues"].append((no, "多余的 [/wrap]"))
                continue
            name, _attrs_unused, _ = stack.pop()
            if name == "air-daily":
                doc["daily_close"] = no
                tail_name = None
                layer = 0
            elif name in ("air-card", "air-sig") and card is not None:
                doc["cards"].append(card)
                card = None
                sub = None
            elif name in ("air-us", "air-src"):
                sub = None
            elif name == "air-stat" and stat is not None:
                doc["stats"].append({"num": stat["num"], "label": "".join(stat["label"]).strip()})
                stat = None
            elif name == "air-mix":
                in_mix = False
            elif name == "air-lede":
                in_lede = False
            continue

        # 标题与分区
        if s.startswith(TAIL_START):
            doc["tail_start"] = no
            tail_name = None
            continue
        if title_spec.is_h1_line(s) and doc["h1"] is None:
            doc["h1"] = s
            continue
        if s in LAYER_HEADINGS:
            layer = LAYER_HEADINGS.index(s) + 1
            doc["layer_order"].append(layer)
            tail_name = None
            continue
        if s in TAIL_SECTIONS:
            tail_name = s
            doc["tail_order"].append(s)
            doc["tail_sections"][s] = []
            layer = 0
            continue

        if card is not None:
            if not s:
                continue
            if sub == "us":
                m = US_RE.match(s)
                card["us"].append(m.group(1) if m else s)
                continue
            if sub == "src":
                card["links"].extend(LINK_RE.findall(s))
                continue
            chips = CHIP_RE.findall(s)
            if chips and CHIP_RE.sub("", s).strip() == "":
                card["chips"].extend({"type": t, "cred": c, "text": x.strip()} for t, c, x in chips)
                continue
            if card["title"] is None and card["kind"] == "card" and s.startswith("#### "):
                card["title"] = s[5:].strip()
                continue
            if card["title"] is None and card["kind"] == "sig" and re.match(r"^\*\*.+\*\*$", s):
                card["title"] = s.strip("*").strip()
                continue
            if US_RE.match(s):  # “对我们”写在 air-us 外：记录以便报错
                card.setdefault("loose_us", []).append(s)
                continue
            card["body"].append(s)
            continue

        if stat is not None and s:
            m = STAT_NUM_RE.match(s)
            if m and stat["num"] is None:
                stat["num"] = int(m.group(1))
            else:
                stat["label"].append(s)
            continue
        if in_mix and s:
            doc["mix"] += s
            continue
        if in_lede and s:
            doc["lede"].setdefault(layer, s)
            continue
        if tail_name:
            doc["tail_sections"][tail_name].append(line)
            continue
        if layer == 0 and doc["h1"] and doc["tail_start"] is None:
            if s.startswith("> 今日主线"):
                doc["mainline"] = s
            elif s.startswith("![AI雷达日报封面") or s.startswith("> AI 雷达日报 · "):
                doc["cover_lines"].append(s)
            continue
        if layer in (1, 2, 3) and s and s != "---":
            doc["layer_free_lines"][layer].append((no, s))

    for name, _a, no in stack:
        doc["wrap_issues"].append((no, f"[wrap={name}] 未闭合"))
    return doc


def card_section(c: Dict[str, Any]) -> Optional[str]:
    for chip in c["chips"]:
        if chip["type"] == "sec":
            return chip["text"]
    return None


def chip_of(c: Dict[str, Any], t: str) -> Optional[Dict[str, Any]]:
    return next((x for x in c["chips"] if x["type"] == t), None)


def title_date(title: str, report_date: date) -> Optional[date]:
    m = DATE_SUFFIX_RE.search(title or "")
    if not m:
        return None
    try:
        d = date(report_date.year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
    if d > report_date:
        d = date(report_date.year - 1, d.month, d.day)
    return d


def window_start(report_date: date) -> date:
    return report_date - timedelta(days=3 if report_date.weekday() == 0 else 1)


# ---------------------------------------------------------------- 门禁

def check_structure(raw: str, report_date: Optional[date],
                    weak_patterns: Iterable[str] = ()) -> List[Tuple[bool, str]]:
    """返回 [(passed, message)]；消息以「V5」开头，供 self_check / preflight 统一记录。"""
    doc = parse(raw)
    out: List[Tuple[bool, str]] = []
    add = lambda ok, msg: out.append((bool(ok), f"V5 {msg}"))
    weak = [re.compile(p) for p in weak_patterns]

    add(not doc["wrap_issues"], "wrap 标记规范" if not doc["wrap_issues"] else
        "wrap 标记问题：" + "；".join(f"第{n}行 {m}" for n, m in doc["wrap_issues"][:5]))
    add(doc["daily_open"] is not None and doc["daily_close"] is not None,
        "正文包在 [wrap=air-daily] 容器内" if doc["daily_open"] else "缺少 [wrap=air-daily] 容器")
    tail_ok = doc["tail_start"] is not None and (doc["daily_close"] or 0) < doc["tail_start"]
    add(tail_ok, "尾部「集团AI能力地图」位于 air-daily 容器之外" if tail_ok else "尾部能力地图缺失或被包进 air-daily 容器")
    add(doc["mainline"] is not None, "存在「今日主线」引用行" if doc["mainline"] else "缺少「> 今日主线：」行")
    add(doc["layer_order"] == [1, 2, 3], "三层标题齐全且顺序正确" if doc["layer_order"] == [1, 2, 3]
        else f"三层标题缺失或顺序错误：{doc['layer_order']}")
    add(doc["tail_order"] == TAIL_SECTIONS, "社区今日 / 今日一问 齐全" if doc["tail_order"] == TAIL_SECTIONS
        else "缺少「## 社区今日」或「## 今日一问」，或顺序错误")
    missing_lede = [i for i in (1, 2, 3) if i not in doc["lede"]]
    add(not missing_lede, "三层均有一句 air-lede 说明" if not missing_lede else f"第{missing_lede}层缺少 air-lede 说明")

    cards = doc["cards"]
    core = [c for c in cards if c["layer"] == 1]
    track = [c for c in cards if c["layer"] == 2]
    sigs = [c for c in cards if c["layer"] == 3]
    stray = [c for c in cards if c["layer"] not in (1, 2, 3)]
    add(not stray, "所有卡片都位于三层之内" if not stray else f"{len(stray)} 张卡片不在任何一层下")
    add(all(c["kind"] == "card" for c in core + track) and all(c["kind"] == "sig" for c in sigs),
        "卡片类型与所在层一致（精编/跟踪用 air-card，雷达用 air-sig）")

    # 统计块与可信度图例
    expect_stats = [len(core), len(track), len(sigs), len(cards)]
    got_stats = [s["num"] for s in doc["stats"]]
    add(got_stats == expect_stats, f"统计块与实际条数一致 {expect_stats}" if got_stats == expect_stats
        else f"统计块 {got_stats} 与实际条数 {expect_stats}（精编/跟踪/雷达/合计）不一致")
    cnt = {k: sum(1 for c in cards if c["cred"] == k) for k in "gyr"}
    legend = {k: re.search(CRED_EMOJI[k] + r"[^\d🟢🟡🔴]*?(\d+)", doc["mix"]) for k in "gyr"}
    legend_ok = all(legend[k] and int(legend[k].group(1)) == cnt[k] for k in "gyr")
    add(legend_ok, f"可信度图例与卡片一致 {cnt}" if legend_ok else f"可信度图例与卡片实际分布 {cnt} 不一致")

    # 逐卡
    problems: List[str] = []
    for c in cards:
        label = (c["title"] or f"第{c['line']}行卡片")[:24]
        sec = card_section(c)
        cred_chip = chip_of(c, "cred")
        if c["cred"] not in CRED_EMOJI:
            problems.append(f"《{label}》cred 属性须为 g/y/r")
        if not cred_chip or cred_chip["cred"] != c["cred"] or not cred_chip["text"].startswith(CRED_EMOJI.get(c["cred"], "?")):
            problems.append(f"《{label}》可信度标签与 cred 属性不一致")
        if not c["title"]:
            problems.append(f"第{c['line']}行卡片缺少标题")
        if not c["src_seen"] or not c["links"]:
            problems.append(f"《{label}》缺少 air-src 来源行")
        if c.get("loose_us"):
            problems.append(f"《{label}》“对我们”须写在 [wrap=air-us] 内")
        for _t, url in c["links"]:
            if any(p.search(url) for p in weak):
                problems.append(f"《{label}》来源命中弱来源黑名单：{url}")
        body_len = _visible_len(" ".join(c["body"]))
        if c["layer"] == 1:
            if sec not in CORE_SECTIONS:
                problems.append(f"《{label}》板块标签须为 {'/'.join(CORE_SECTIONS)}")
                continue
            limit = BODY_LIMITS[sec]
            if sec in ("今日要紧", "金风相关") and len(c["us"]) != 1:
                problems.append(f"《{label}》{sec}须恰好一条“对我们”")
            if sec == "工具与做法" and len(c["us"]) > 1:
                problems.append(f"《{label}》工具与做法最多一条“对我们”")
            if sec == "行业速递" and c["us"]:
                problems.append(f"《{label}》行业速递不写“对我们”")
            needs_date = sec != "工具与做法"
            has_date = bool(DATE_SUFFIX_RE.search(c["title"] or ""))
            if needs_date and not has_date:
                problems.append(f"《{label}》标题须以（M月D日）结尾")
            if not needs_date and has_date:
                problems.append(f"《{label}》工具与做法标题不带日期")
            if c["cred"] in ("g", "y") and sec != "工具与做法" and len(c["links"]) < 2:
                problems.append(f"《{label}》精编条目须两个独立来源（单一来源请标 cred=r 并在正文注明“单一”）")
            if c["cred"] == "r" and "单一" not in " ".join(c["body"] + c["us"]):
                problems.append(f"《{label}》cred=r 的精编条目须在正文注明“单一官方来源”")
        elif c["layer"] == 2:
            limit = BODY_LIMITS["跟踪"]
            if sec != TRACK_CHIP:
                problems.append(f"《{label}》跟踪层板块标签须为「跟踪」")
            if c["us"]:
                problems.append(f"《{label}》跟踪层不写“对我们”")
            if report_date:
                d = title_date(c["title"] or "", report_date)
                ws = window_start(report_date)
                if d is None:
                    problems.append(f"《{label}》跟踪条目标题须以（M月D日）结尾，写明原始事件日期")
                elif not (report_date - timedelta(days=TRACK_LOOKBACK_DAYS) <= d < ws):
                    problems.append(f"《{label}》跟踪条目日期 {d} 须早于本期窗口 {ws} 且在 {TRACK_LOOKBACK_DAYS} 天内")
        else:
            limit = BODY_LIMITS["雷达"]
            if not c["cat"] or sec != c["cat"]:
                problems.append(f"《{label}》雷达类别标签须与 cat 属性一致")
            elif c["cat"] not in SIGNAL_CATS:
                problems.append(f"《{label}》雷达类别「{c['cat']}」不在八类白名单内")
            if not chip_of(c, "from"):
                problems.append(f"《{label}》雷达条目须带来源名标签")
            if c["us"]:
                problems.append(f"《{label}》雷达不写“对我们”")
        if body_len > limit:
            problems.append(f"《{label}》正文 {body_len} 字，超过 {limit} 字")
        for u in c["us"]:
            if _visible_len(u) > US_LIMIT:
                problems.append(f"《{label}》“对我们”超过 {US_LIMIT} 字")
    add(not problems, f"逐卡规则通过（{len(cards)} 张）" if not problems else "逐卡问题：" + "；".join(problems[:8])
        + (f" 等 {len(problems)} 项" if len(problems) > 8 else ""))

    # 精编分区顺序与条数
    order = [CORE_SECTIONS.index(card_section(c)) for c in core if card_section(c) in CORE_SECTIONS]
    add(order == sorted(order), "精编板块顺序为 今日要紧→金风相关→工具与做法→行业速递" if order == sorted(order)
        else "精编板块顺序错误（应为 今日要紧→金风相关→工具与做法→行业速递）")
    count_problems = []
    for sec in CORE_SECTIONS:
        n = sum(1 for c in core if card_section(c) == sec)
        lo, hi = COUNT_LIMITS[sec]
        if not lo <= n <= hi:
            count_problems.append(f"{sec} {n} 条（应 {lo}-{hi}）")
    if not any(card_section(c) == "金风相关" for c in core):
        if not any(NO_ENERGY_LINE in s for _n, s in doc["layer_free_lines"][1]):
            count_problems.append(f"无金风相关卡片时须写「{NO_ENERGY_LINE}」")
    if not any(card_section(c) == "工具与做法" for c in core):
        if not any(NO_TOOLS_LINE in s for _n, s in doc["layer_free_lines"][1]):
            count_problems.append(f"无工具与做法卡片时须写「{NO_TOOLS_LINE}」")
    for name, group in (("跟踪", track), ("雷达", sigs)):
        lo, hi = COUNT_LIMITS[name]
        if not lo <= len(group) <= hi:
            count_problems.append(f"{name} {len(group)} 条（应 {lo}-{hi}）")
    add(not count_problems, "各板块条数在范围内" if not count_problems else "条数问题：" + "；".join(count_problems))
    return out


def check_against_search_log(raw: str, search_log: Dict[str, Any]) -> List[Tuple[bool, str]]:
    """跟踪层 / 雷达层条目必须能在检索清单中按标题对上 TRACKING / SIGNAL 候选，且来源 URL 被覆盖。"""
    doc = parse(raw)
    cands = search_log.get("candidates") or []

    def norm(t: str) -> str:
        t = re.sub(r"[（(][^）)]*[）)]\s*$", "", t or "")
        return re.sub(r"[\s，。；：、“”\"'·—\-!！?？,.:;]", "", t).lower()

    by_title: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for c in cands:
        by_title[(str(c.get("decision")), norm(c.get("title", "")))] = c
    out: List[Tuple[bool, str]] = []
    for layer, decision, name in ((2, "TRACKING", "跟踪"), (3, "SIGNAL", "雷达")):
        bad = []
        for card in (c for c in doc["cards"] if c["layer"] == layer):
            cand = by_title.get((decision, norm(card["title"] or "")))
            if not cand:
                bad.append(f"《{(card['title'] or '')[:20]}》检索清单无对应 {decision} 候选")
                continue
            urls = set([cand.get("source_url")] + list(cand.get("source_urls") or []))
            missing = [u for _t, u in card["links"] if u not in urls]
            if missing:
                bad.append(f"《{(card['title'] or '')[:20]}》来源未登记在候选中：{missing[0]}")
        out.append((not bad, f"V5 {name}层条目与检索清单绑定" if not bad else f"V5 {name}层绑定问题：" + "；".join(bad[:5])))
    return out


# ---------------------------------------------------------------- V4 适配

def to_v4(raw: str) -> str:
    """把 V5 精编层转为 V4 结构（只含精编 + 社区今日 + 今日一问 + 原尾部），供旧门禁与下游复用。"""
    doc = parse(raw)
    lines = raw.splitlines()
    tail = "\n".join(lines[doc["tail_start"] - 1:]) if doc["tail_start"] else ""
    core = [c for c in doc["cards"] if c["layer"] == 1]
    out: List[str] = [doc["h1"] or "", ""]
    if doc["mainline"]:
        out += [doc["mainline"], ""]
    for cl in doc["cover_lines"]:
        out += [cl, ""]
    out += ["---", ""]

    def links_line(c: Dict[str, Any]) -> str:
        return " · ".join(f"[{t}]({u})" for t, u in c["links"])

    for sec in CORE_SECTIONS:
        out += [f"## {sec}", ""]
        group = [c for c in core if card_section(c) == sec]
        if sec == "行业速递":
            for c in group:
                out.append(f"- {CRED_EMOJI.get(c['cred'], '🟡')} **{c['title']}：** {' '.join(c['body'])}")
            out += ["", f'[details="来源｜{len(group)} 条速递"]']
            for c in group:
                short = re.sub(r"[（(]\d{1,2}月\d{1,2}日[^）)]*[）)]\s*$", "", c["title"] or "")
                out.append(f"- {short}：{links_line(c)}")
            out += ["[/details]", "", "---", ""]
            continue
        if not group and sec == "金风相关":
            out += [NO_ENERGY_LINE, "", "---", ""]
            continue
        if not group and sec == "工具与做法":
            out += [NO_TOOLS_LINE, "", "---", ""]
            continue
        for c in group:
            out += [f"### {c['title']}", "", "\n\n".join(c["body"]), ""]
            for u in c["us"]:
                out += [f"**对我们：** {u}", ""]
            label = "来源｜官方文档" if sec == "工具与做法" else CRED_DETAILS_V4.get(c["cred"], CRED_DETAILS_V4["y"])
            out += [f'[details="{label}"]', f"- 来源：{links_line(c)}", "[/details]", ""]
        out += ["---", ""]
    for name in TAIL_SECTIONS:
        body = "\n".join(doc["tail_sections"].get(name, [])).strip()
        out += [name, "", body, "", "---", ""]
    out.append(tail)
    return "\n".join(out).rstrip() + "\n"


def all_titles_and_urls(raw: str) -> Tuple[List[str], List[str]]:
    """跨期去重用：三层全部条目的标题与来源 URL。"""
    doc = parse(raw)
    titles = [c["title"] for c in doc["cards"] if c["title"]]
    urls: List[str] = []
    for c in doc["cards"]:
        for _t, u in c["links"]:
            if u not in urls:
                urls.append(u)
    return titles, urls
