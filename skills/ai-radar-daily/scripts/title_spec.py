#!/usr/bin/env python3
"""日报标题规范（title_spec.py，V5.3.2 新增）。

**为什么单独成模块**：标题格式在 5 个脚本、8 处被硬编码正则匹配
（publish / self_check / layout_v5 / prepare_feishu_push / fetch_recent /
build_knowledge_package）。任何一处漏改都会造成"静默错判"而非明确报错：
  - layout_v5 认不出 H1 → V5 版式整段判失败；
  - fetch_recent 过滤不掉自家帖 → 自己的日报被当成社区信号源，跨期去重失效；
  - prepare_feishu_push 的 date_tag 若用宽松数字匹配 → 标题里的「0.28 元」
    会被误当成日期，推送文案日期错位。
故把「什么是合法院日报标题」收敛到本模块，其余脚本一律 import，不再各写正则。

**两种合法格式**（新格式优先，旧格式永久兼容以便历史期次可复验）：
  新：`{钩子} |AI雷达{M}.{D}`      例：`5 次示范就到 95%，机器人不用先攒大数据 |AI雷达11.10`
  旧：`AI 雷达日报｜{M}.{D}`        例：`AI 雷达日报｜11.10`（≤2026-11-10 期次）

**钩子（hook）生成逻辑**（与 SKILL.md §11、report-template.md 规则 1 一致）：
  1. 素材只从本期「今日要紧」段落取，优先 importance 最高 / 段内首位；
  2. 钩子必须含 ≥1 个来自正文的可验证数字（价格、门槛、时长、百分比）；
  3. 句式=结论先行、陈述句；≤HOOK_MAX 字，不用问句、不堆形容词；
  4. 数字须与正文口径一致；厂商自测类数字，正文必须保留限定语，标题不背书；
  5. 禁用营销词表（BANNED_WORDS）——命中即 FAIL，标题不得比正文更绝对。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Dict, List, Optional

# —— 格式常量（改这里即全局生效）——
TAG = "AI雷达"
SUFFIX_SEP = " |"
LEGACY_PREFIX = "AI 雷达日报｜"

HOOK_MIN = 6
HOOK_MAX = 24

# 营销腔 / 过度断言：命中即不允许出现在标题
BANNED_WORDS = (
    "重磅", "震惊", "炸裂", "王炸", "史上最", "颠覆", "彻底改变", "颠覆性",
    "炸场", "杀疯了", "遥遥领先", "封神", "史无前例", "绝杀",
)

NEW_RE = re.compile(r"^(?P<hook>[^|｜]+?)\s*\|\s*AI雷达(?P<m>\d{1,2})\.(?P<d>\d{1,2})$")
LEGACY_RE = re.compile(r"^AI 雷达日报｜(?P<m>\d{1,2})\.(?P<d>\d{1,2})$")
_DATE_DIGIT_RE = re.compile(r"\d")


def format_title(hook: str, report_date: date) -> str:
    """按新格式拼标题：{钩子} |AI雷达{M}.{D}（M/D 不补零，与文件名日期口径一致）。"""
    hook = (hook or "").strip()
    if not hook:
        raise ValueError("标题钩子不能为空")
    return f"{hook}{SUFFIX_SEP}{TAG}{report_date.month}.{report_date.day}"


def legacy_title(report_date: date) -> str:
    """旧格式标题，仅用于历史期次兼容与迁移对照，新期次禁止使用。"""
    return f"{LEGACY_PREFIX}{report_date.month}.{report_date.day}"


def parse(text: str) -> Optional[Dict[str, object]]:
    """解析标题；返回 {hook, month, day, is_legacy}，两种格式都不匹配返回 None。"""
    raw = (text or "").strip()
    if not raw:
        return None
    m = NEW_RE.match(raw)
    if m:
        return {
            "hook": m.group("hook").strip(),
            "month": int(m.group("m")),
            "day": int(m.group("d")),
            "is_legacy": False,
        }
    m = LEGACY_RE.match(raw)
    if m:
        return {
            "hook": "",
            "month": int(m.group("m")),
            "day": int(m.group("d")),
            "is_legacy": True,
        }
    return None


def is_report_title(text: str) -> bool:
    """是否为合法院日报标题——fetch_recent 用它识别"自家帖"（替代原先的
    'AI 雷达日报' in title 子串匹配，后者对新格式必然漏判）。"""
    return parse(text) is not None


def is_h1_line(line: str) -> bool:
    """该行是否为日报 H1（layout_v5 解析入口用）。"""
    stripped = (line or "").strip()
    if not stripped.startswith("# "):
        return False
    return parse(stripped[2:]) is not None


def extract_h1(raw: str) -> Optional[str]:
    """从正文中取出日报 H1 原文（不含 '# '）；找不到返回 None。"""
    for line in (raw or "").splitlines():
        if is_h1_line(line):
            return line.strip()[2:].strip()
    return None


def h1_date(raw: str, reference_date: Optional[date] = None) -> Optional[date]:
    """由 H1 推出日报日期；reference_date 用于补年份（跨年时取最近的合理年份）。"""
    title = extract_h1(raw)
    if not title:
        return None
    parsed = parse(title)
    if not parsed:
        return None
    month, day = int(parsed["month"]), int(parsed["day"])
    if reference_date is None:
        return None
    for year in (reference_date.year, reference_date.year - 1, reference_date.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if abs((candidate - reference_date).days) <= 200:
            return candidate
    return None


def date_tag(title: str, raw: str = "") -> str:
    """取标题中的 `M.D` 日期标签（如 "11.10"）。

    **必须锚定在品牌后缀上**，不能对整串做 `\\d+\\.\\d+` 搜索：
    新格式的钩子里常有价格/版本号（如「0.28 元」「5.2」），宽松匹配会取错。
    """
    for candidate in (title, extract_h1(raw) if raw else None):
        if not candidate:
            continue
        parsed = parse(candidate)
        if parsed:
            return f"{parsed['month']}.{parsed['day']}"
    return ""


def validate_hook(hook: str) -> List[str]:
    """校验钩子是否合规；返回问题列表，空列表=通过。"""
    problems: List[str] = []
    text = (hook or "").strip()
    if not text:
        return ["钩子为空"]
    if len(text) < HOOK_MIN:
        problems.append(f"钩子过短（{len(text)} 字 < {HOOK_MIN}），信息量不足")
    if len(text) > HOOK_MAX:
        problems.append(f"钩子过长（{len(text)} 字 > {HOOK_MAX}），群聊列表会被截断")
    if not _DATE_DIGIT_RE.search(text):
        problems.append("钩子不含数字，缺少可验证的量化落点")
    if any(word in text for word in BANNED_WORDS):
        hit = [w for w in BANNED_WORDS if w in text]
        problems.append("钩子含营销/过度断言词：" + "、".join(hit))
    if "|" in text or "｜" in text:
        problems.append("钩子不得含竖线（会与品牌后缀分隔符混淆）")
    if TAG in text:
        problems.append(f"钩子不得含「{TAG}」（品牌后缀由 format_title 自动追加）")
    if text.endswith(("。", "！", "!", "；", ";", "，")):
        problems.append("钩子结尾不应带标点")
    if text.startswith(("「", "《")):
        problems.append("钩子不应以引号/书名号开头（列表里视觉冗余）")
    return problems


def validate_title(title: str) -> List[str]:
    """校验整条标题（格式 + 日期合法性 + 钩子规则）。"""
    parsed = parse(title)
    if not parsed:
        return ["标题格式不合法：应为「{钩子} |AI雷达M.D」或历史格式「AI 雷达日报｜M.D」"]
    problems: List[str] = []
    try:
        date(2000, int(parsed["month"]), int(parsed["day"]))
    except ValueError:
        problems.append(f"标题日期非法：{parsed['month']}.{parsed['day']}")
    if not parsed["is_legacy"]:
        problems.extend(validate_hook(str(parsed["hook"])))
    return problems


if __name__ == "__main__":  # pragma: no cover - 手工核对用
    import argparse

    ap = argparse.ArgumentParser(description="日报标题规范化工具")
    ap.add_argument("--hook", help="钩子文本；给出则拼成完整标题")
    ap.add_argument("--report-date", help="YYYY-MM-DD，配合 --hook 使用")
    ap.add_argument("--check", help="校验现有标题")
    args = ap.parse_args()

    if args.check:
        bad = validate_title(args.check)
        print("PASS" if not bad else "FAIL: " + "；".join(bad))
    elif args.hook:
        if not args.report_date:
            raise SystemExit("--hook 需要同时给 --report-date")
        y, m, d = (int(x) for x in args.report_date.split("-"))
        bad = validate_hook(args.hook)
        if bad:
            raise SystemExit("钩子不合规：" + "；".join(bad))
        print(format_title(args.hook, date(y, m, d)))
    else:
        ap.print_help()
