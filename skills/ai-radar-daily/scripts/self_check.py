#!/usr/bin/env python3
"""AI 雷达日报发布前质量门禁（V3 版式，2026-09-01 起）。

确定性检查 V3 结构（一分钟速览/今日大事件/AI×能源风电/Builder信号/AI工作实践/深度聚焦）、今日大事件紧凑格式、事实—来源对应（双源或「单一官方来源」标注）、加粗标签空格规范、
来源质量、跟踪报道标识、重复内容、资源和语义复核记录，生成带正文 SHA-256 的自查报告。
publish.py 只接受状态为 PASS 且哈希一致的报告。

用法：
  python3 self_check.py <日报.md> --semantic-review <语义复核.json> \
    --search-log <检索清单.json> --cover <封面.png> \
    --qr1 <二维码1.png> --qr2 <二维码2.png> --qr3 <二维码3.png>

人工修订当日已发布日报（把当日已发生的后续关注事项转正）时，追加 --allow-same-day。
事件日期门禁采用执行点增量口径（2026-09-08 起）：允许收录「上一执行点之后 → 当前执行点」的新闻，
周一允许 {上周五..周一}，周二至周五允许 {昨天,今天}，执行当日默认可收录。
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from common import parse_report_date
import layout_v5  # V5 三层版式解析与 V4 适配
import title_spec  # V5.3.2 标题规范单一真源

RULESET_VERSION = "V4.1"

CORE_HEADINGS = [
    "## 今日要紧",
    "## 金风相关",
    "## 工具与做法",
    "## 行业速递",
    "## 社区今日",
    "## 今日一问",
]
# V4 版式（2026-10-22 起）：板块语义改为 要紧度/相关性/做法/速递/社区/互动。
# V3 版式备份见 self_check.py.bak-20261022-v3，历史期次回溯校验请使用该备份。
FORWARD_HEADING = "## 后续关注"
LEGACY_FORWARD_HEADINGS = ["## 明日关注", "## 下周关注"]
TAIL_HEADINGS = [
    "### 集团AI能力地图",
    "### 技术交流群",
]
# 已停用的正文尾部模块（V3.9.0）：「全部资讯（候选全集）」**禁止**出现在日报正文。
# 依据用户要求——资讯清单只留知识库记录，正文不再发布。出现即 FAIL（见结构校验）。
# 候选全集仍由 sync_candidates_base.py 同步到飞书 Base，但不回流正文。
FORBIDDEN_TAIL_HEADINGS = [
    "# 🔗 全部资讯（候选全集）",
]
ALL_INFO_TAIL_HEADING = "# 🔗 全部资讯（候选全集）"
ALL_INFO_TABLE_URL = "https://qdv30zar2p.feishu.cn/base/Hhx5bvXBda6RH1sCVxacRfirn6c?table=tbl4RS4qEuDf2B0E"
# 历史期次豁免：V3.9.0 于 2026-09-23 定稿，09-23 当期正文（当日 11:34 发布）仍带「全部资讯」
# 模块，属既定事实（已发布、已推送、已入知识库），不得回溯性判 FAIL、更不得改写正文。
# 严格校验自 2026-09-24 期起生效。
LEGACY_ALL_INFO_STRICT_FROM = "2026-09-24"
# 累计出刊行的**正文**写法已停用（V3.9.0）：权威来源迁至检索清单 publication_metadata。
# 保留正则仅用于历史期次回溯比对与测试，不再要求正文出现该行。
ACCUMULATION_LINE_PATTERN = re.compile(
    r"(?m)^>\s*📅\s*\*{0,2}累积出刊\*{0,2}\s*[：:]\s*本期为\s*\*{0,2}"
    r"(?P<span>[0-9]{1,2}/[0-9]{1,2}\s*[–~-]\s*[0-9]{1,2}/[0-9]{1,2})\*{0,2}\s*"
    r"[（(]\s*共\s*\*{0,2}(?P<count>\d+)\*{0,2}\s*条\s*[）)]"
)
# V5.2.2（2026-11-03 用户改版）：署名「【AI日报-Agent】」去掉连字符两侧空格，尾部顺序改为
# 能力地图 → 群码 → 分隔线 → 免责声明 → 署名 → 跟踪语 → 投稿 → 合集（免责声明前移到署名之前）。
CONTACT_LINE = "> 以上内容为【AI日报-Agent】自动整理报道，仅供内部信息参考。涉及产品选型、生产部署、设备诊断和安全决策时，应结合正式文件、业务数据与专业人员意见复核。AI不替代责任主体的最终判断。"
# 尾部双层署名行（2026-10-13 用户改版，V3.9.7 起生效）：
# 署名口径 2026-10-23 用户更正为「集团数字化中心」（4.1.2）；2026-11-02 起去掉「·AI运营团队」，两行分写（5.0.2）；
# 更早写法 `**集团数字化 · AI运营团队 出品**` / `**AI应用研究院 整理报道**` / `**出品｜集团数字·AI运营团队**` 均已停用。
SIGN_LINE_1 = "**出品｜集团数字化中心**"
SIGN_LINE_2 = "**策划整理｜AI应用研究院**"
PRODUCER_LINE = "**出品｜集团数字化中心**"
ARCHIVE_MARK = "往期合集"
# 2026-11-12 用户改版：尾部最后一行定为跟踪语 + 社区交流邀请（不再有「**欢迎投稿**：」加粗前缀，
# 也不再挂「[往期合集](…)」行——往期合集改由论坛分类页承载）。
SUBMIT_LINE = "欢迎分享发布科普内容或者你的心得与技巧到 AI 社区，我们一起交流、探讨！"
TAIL_INTERACTIVE_TOKENS = ("有想了解的 AI 话题欢迎留言", "业务侧 AI 需求可随时交流", "业务侧 AI 需求可随时联系")
PLACEHOLDER_TOKENS = ("待人工替换", "FORUM_URL_PENDING", "待回填", "TODO", "TBD")


def _blocked_domains():
    try:
        cfg = json.loads((Path(__file__).resolve().parent.parent / "config" / "link-reachability.json").read_text(encoding="utf-8"))
        return [str(x.get("domain") if isinstance(x, dict) else x).lower() for x in cfg.get("blocked", [])]
    except Exception:  # 配置缺失不阻断，仅失去该项保护
        return []


def blocked_domain_of(url):
    host = re.sub(r"^https?://", "", url.strip().lower()).split("/")[0].split(":")[0]
    return any(host == d or host.endswith("." + d) for d in _blocked_domains())
DIFY_PLATFORM_URL = "https://keconsole.goldwind.com"
MIN_COVER_BYTES = 500 * 1024  # 封面最小字节数：真实 ImageGen 封面为 1.7-2.2MB，程序化文字卡片约 162KB
MAX_FEATURE_BODY_CHARS = 300  # 长文板块正文上限；标题、来源、固定属性说明不计
MAX_DIGEST_BODY_CHARS = 600  # V4 列表型板块（行业速递）上限：多条快讯并列，按条数而非单条长度约束
# 深度聚焦官方源硬门禁：媒体/聚合域名黑名单。official_sources_only 不能只依赖语义复核自声明（2026-09-07 Silicon Report 混过 PASS 的教训）。
MEDIA_DOMAIN_BLOCKLIST = {
    "siliconreport.com", "tmtpost.com", "ithome.com", "36kr.com", "sohu.com", "163.com",
    "qq.com", "sina.com.cn", "thepaper.cn", "jiemian.com", "caixin.com", "yicai.com",
    "stcn.com", "cs.com.cn", "cnstock.com", "stdaily.com", "infzm.com", "guandian.hk",
    "gigazine.net", "theagenttimes.com", "aiweekly.co", "techcrunch.com", "theverge.com",
    "arstechnica.com", "zdnet.com", "cnet.com", "engadget.com", "wired.com",
    "venturebeat.com", "mashable.com", "cnbeta.com", "fastcompany.com",
    "technologyreview.com", "newatlas.com", "tomshardware.com", "theregister.com",
    "techradar.com", "digitaltrends.com", "aimuseum.se", "sxwbs.com",
}
NEWS_TITLE_PATTERN = re.compile(r"(?m)^(?:###\s+(\d+)\.\s*(.+)|🔹\s+\*\*(.+)\*\*)\s*$")
SECTION_TITLE_PATTERN = re.compile(r"(?m)^###\s+(?!\d+\.\s)(.+)$")
SOURCE_LINE_PATTERN = re.compile(r"^\s*\**\s*来源：")
LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
GLANCE_BULLET_PATTERN = re.compile(r"(?m)^-\s+\*\*")
BOLD_SPACE_PATTERN = re.compile(r"：\*\*(?=[^\s])")
FOLLOWUP_MARK = "跟踪报道"
SINGLE_SOURCE_MARK = "单一官方来源"
WEAK_SOURCE_PATTERNS = [
    r"mp\.weixin\.qq\.com",          # 微信公众号
    r"weixin\.qq\.com",              # 微信域名
    r"baijiahao\.baidu\.com",        # 百家号
    r"sohu\.com/a/",                 # 搜狐号（自媒体池）
    r"163\.com/dy/",                 # 网易号
    r"toutiao\.com",                 # 头条号
    r"kuaibao\.qq\.com",             # 快传号
    r"news\.qq\.com/rain/",          # 腾讯新闻自媒体
    r"zhuanlan\.zhihu\.com",         # 知乎专栏
]


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_title(title):
    # V4.1：先剥离条目标题前的行动标签，使语义复核 / 跨期去重不受【需行动】等前缀影响；
    # 同时修正 V3 遗留缺陷（第二行误用 title 而非 value，导致跟踪报道日期未被剥离）。
    value = re.sub(r"^\s*【(?:需行动|可借鉴|了解)】\s*", "", title)
    value = re.sub(r"[（(]\s*跟踪报道\s*[·．.]?\s*\d{1,2}月\d{1,2}日\s*[）)]", "", value)
    value = re.sub(r"[（(]\s*\d{1,2}月\d{1,2}日[^）)]*[）)]", "", value)
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[，。；：、“”‘’\"·—\-!！?？]", "", value)
    return value.lower()


def normalize_url(url):
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


def report_date_from_path(path):
    match = re.search(r"AI雷达日报_(\d{4}-\d{2}-\d{2})\.md$", Path(path).name)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d").date()


def report_date_from_h1(raw, reference_date=None):
    # V5.3.2：H1 解析统一走 title_spec，兼容新格式「{钩子} |AI雷达M.D」与历史格式
    return title_spec.h1_date(raw, reference_date)


def resolve_report_date(md_path, raw, explicit_report_date=None, timezone="Asia/Shanghai"):
    filename_date = report_date_from_path(md_path)
    if explicit_report_date in (None, "") and filename_date is None:
        parse_report_date(None, timezone)
        return None, None, None
    resolved = parse_report_date(explicit_report_date, timezone, default=filename_date)
    h1_date = report_date_from_h1(raw, resolved)
    return resolved, filename_date, h1_date


def event_date_from_month_day(report_date, month, day):
    """按日报日期推断标题月日所属年份，兼容跨年日报。"""
    try:
        candidate = date(report_date.year, month, day)
    except ValueError:
        return None
    if candidate > report_date:
        try:
            candidate = date(report_date.year - 1, month, day)
        except ValueError:
            return None
    return candidate


def required_headings(md_path):
    report_date = report_date_from_path(md_path)
    return CORE_HEADINGS + TAIL_HEADINGS, FORWARD_HEADING, report_date


def section_text(raw, heading):
    start = raw.find(heading)
    if start < 0:
        return ""
    level = len(heading) - len(heading.lstrip("#"))
    boundary = re.compile(r"(?m)^#{1,%d}\s" % level)
    next_positions = [match.start() for match in boundary.finditer(raw[start + len(heading):])]
    end = start + len(heading) + min(next_positions) if next_positions else len(raw)
    return raw[start:end]


def extract_urls(block):
    urls = []
    in_details = False
    for line in block.splitlines():
        stripped = line.strip()
        # V4 版式：来源收进 [details] 折叠块，块内链接同样计入条目来源
        if stripped.startswith("[details"):
            in_details = True
        if in_details or SOURCE_LINE_PATTERN.match(line):
            urls.extend(url for _, url in LINK_PATTERN.findall(line))
        if stripped.startswith("[/details]"):
            in_details = False
    return urls


def parse_news_items(section):
    """解析 V3 紧凑格式「🔹 **标题**」或兼容旧版「### N. 标题」。"""
    matches = list(NEWS_TITLE_PATTERN.finditer(section))
    items = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        block = section[match.start():end]
        number = int(match.group(1)) if match.group(1) else index + 1
        title = (match.group(2) or match.group(3) or "").strip()
        items.append({
            "number": number,
            "title": title,
            "block": block,
            "urls": extract_urls(block),
        })
    return items


def parse_section_items(section):
    """解析「### 标题」格式的无编号条目（能源风电新闻/知识卡）。"""
    matches = list(SECTION_TITLE_PATTERN.finditer(section))
    items = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        block = section[match.start():end]
        items.append({
            "title": match.group(1).strip(),
            "block": block,
            "urls": extract_urls(block),
        })
    return items


def feature_body_char_count(section):
    """计算长模块正文字符数；标题、来源、固定属性说明和空白不计。"""
    # V4 版式：来源收进 [details] 折叠块，不计入正文字符数
    section = re.sub(r"(?s)\[details[^\]]*\].*?\[/details\]", "", section)
    body_lines = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "---":
            continue
        if stripped.startswith("## ") or stripped.startswith("### "):
            continue
        if SOURCE_LINE_PATTERN.match(stripped):
            continue
        if re.match(r"^>?\s*(?:\*\*)?(?:信号属性|案例属性)：", stripped):
            continue
        body_lines.append(stripped)
    return len(re.sub(r"\s+", "", "".join(body_lines)))


def load_recent_data(path, days, report_date=None, timezone="Asia/Shanghai"): 
    if path:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [item.get("title", "") for item in data if isinstance(item, dict)], [url for item in data if isinstance(item, dict) for url in item.get("urls", [])], None
        return data.get("titles", []), data.get("urls", []), None

    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from fetch_recent import fetch_recent_reports
        titles, urls, fetched = fetch_recent_reports(
            days, report_date=report_date, timezone=timezone
        )
        if fetched == 0 or (not titles and not urls):
            return [], [], "未能从论坛取得近期日报内容，跨期去重无法确认"
        return titles, list(urls), None
    except Exception as exc:
        return [], [], f"拉取近期日报失败：{exc}"


def add_result(results, category, passed, message):
    results.append({"category": category, "passed": bool(passed), "message": message})


# ---------------------------------------------------------------------------
# 尾部模块顺序（V5.2.3）
#
# 背景：旧实现只校验尾部模块**是否存在**，不校验顺序。2026-11-03 用户改版把免责声明
# 移到署名之前、能力地图由 H1 降为 H3，顺序变了旧实现完全无感——存在性检查无法发现
# 「署名在免责声明之前」这类结构性错位。此函数按真实排版顺序逐个定位，任一逆序即 FAIL。
# ---------------------------------------------------------------------------

TAIL_ORDER_MARKS = [
    ("能力地图", ("### 集团AI能力地图",)),
    ("技术交流群", ("### 技术交流群",)),
    ("grid 开始", ("[grid]",)),
    ("grid 结束", ("[/grid]",)),
    ("免责声明", ("> 以上内容为【AI日报-Agent】",)),
    ("署名行 1", ("**出品｜集团数字化中心**",)),
    ("署名行 2", ("**策划整理｜AI应用研究院**",)),
]

# V5.2.2 之前的尾部形态（≤ 2026-11-03 期次，已发布、按 R12 只读，不得回溯判 FAIL）：
# 能力地图为 H1、群码标题带「（扫码加入）」、免责声明在署名之后、署名为 `【AI日报- Agent】`。
# 标题与免责各给两种写法，兼容「标题已换新、顺序仍旧」的过渡形态，避免混合期次误报。
LEGACY_TAIL_ORDER_MARKS = [
    ("能力地图", ("### 集团AI能力地图", "# 集团AI能力地图")),
    ("技术交流群", ("### 技术交流群", "### 技术交流群（扫码加入）")),
    ("grid 开始", ("[grid]",)),
    ("grid 结束", ("[/grid]",)),
    ("署名行 1", ("**出品｜集团数字化中心**",)),
    ("署名行 2", ("**策划整理｜AI应用研究院**",)),
    ("往期合集", ("往期合集",)),
    ("免责声明", ("> 以上内容为【AI日报-Agent】", "> 以上内容为【AI日报- Agent】")),
]
TAIL_ORDER_STRICT_FROM = "2026-11-04"


def _first_index(raw, candidates):
    hits = [raw.find(c) for c in candidates if raw.find(c) >= 0]
    return min(hits) if hits else -1


def check_tail_order(raw, results, report_date=None):
    """尾部固定模块须按顺序出现，缺项或逆序即 FAIL。

    ≤ 2026-11-03 期次按旧形态校验（历史期已发布且只读），2026-11-04 起按 V5.2.2 新形态。
    """
    legacy = bool(report_date) and str(report_date) < TAIL_ORDER_STRICT_FROM
    marks = LEGACY_TAIL_ORDER_MARKS if legacy else TAIL_ORDER_MARKS
    positions = []
    for label, candidates in marks:
        idx = _first_index(raw, candidates)
        if idx < 0:
            add_result(results, "格式", False, f"尾部模块缺失：{label}")
            return
        positions.append((label, idx))
    inversions = [
        (positions[i][0], positions[i + 1][0])
        for i in range(len(positions) - 1)
        if positions[i][1] >= positions[i + 1][1]
    ]
    tail_desc = ("能力地图→技术交流群→群码→署名→往期合集→免责声明" if legacy
                 else "能力地图→技术交流群→群码→免责声明→署名")
    add_result(
        results, "格式", not inversions,
        f"尾部模块顺序正确（{tail_desc}）"
        if not inversions
        else "尾部模块顺序错乱：" + "；".join(f"{a} 必须在 {b} 之前" for a, b in inversions),
    )


# ---------------------------------------------------------------------------
# 技术交流群二维码门禁（V3.8.5 抽取为模块级函数，便于真实数据端到端测试）
#
# 背景：V3.8.4 曾把识别条件写成 `name.startswith("群二维码_")`，但正文真实写法是
# `![群名|400x400](upload://xxxx.png)`——链接为 upload:// 短链而非本地文件名，导致
# 识别结果恒为空、尺寸门禁被条件跳过而**从未生效**（旧测试只对 alt 字符串做正则，
# 不调用本逻辑，故长期全绿却无保护）。此处抽取为纯函数并配端到端测试。
# ---------------------------------------------------------------------------

QR_SIZE_PATTERN = re.compile(r"\|\s*(\d+)\s*[x×]\s*(\d+)\s*$")
QR_EXPECTED_COUNT = 6
QR_EXPECTED_SIZE = ("400", "400")


def is_qr_ref(alt, link):
    """判断 [grid] 内一张图片是否属于「技术交流群二维码」。

    命中任一即视为二维码：
      1. 链接为本地文件名 群二维码_*.png（上传前形态）；
      2. 链接为 upload:// 短链（上传后发布形态，模板实际写法）；
      3. alt 带 `|WxH` 尺寸标注（并排网格的既有约定）。
    """
    link_l = (link or "").strip()
    if link_l.startswith("群二维码_") and link_l.endswith(".png"):
        return True
    if link_l.startswith("upload://"):
        return True
    if QR_SIZE_PATTERN.search(alt or ""):
        return True
    return False


def parse_grid_refs(raw):
    """提取正文 [grid]...[/grid] 块内的全部图片引用，返回 [(alt, link), ...]。"""
    block = re.search(r"\[grid\](.*?)\[/grid\]", raw or "", re.S)
    if not block:
        return []
    return re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", block.group(1))


def check_qr_gate(raw, md_dir, results):
    """二维码门禁：码数必须为 5、显示尺寸必须统一 |400x400、本地引用文件须存在。

    返回识别到的二维码引用列表 [(alt, link), ...]。
    """
    grid_refs = parse_grid_refs(raw)
    qr_refs = [(alt, link) for alt, link in grid_refs if is_qr_ref(alt, link)]

    # gw-ai-pulse（2026-11-13）：日报正文已移除 6 张技术交流群二维码（用户决定，飞书推送同步关闭）。
    # 因此「正文完全没有群码」是**合规状态**，直接放行；一旦出现群码，仍按原标准逐项校验
    # （数量=QR_EXPECTED_COUNT、尺寸统一 400x400、本地文件存在），防止回退到半套结构。
    if not qr_refs:
        add_result(results, "资源", True, "正文未挂技术交流群二维码（gw-ai-pulse 起为标准形态）")
        return []

    # 1) 引用与存在性（upload:// 短链无法本地判定，跳过文件检查）
    local_qr_names = [link for _, link in qr_refs if not link.startswith("upload://")]
    qr_resources_ok = bool(qr_refs)
    if qr_resources_ok:
        for name in set(local_qr_names):
            if not (Path(md_dir) / name).is_file():
                qr_resources_ok = False
                add_result(results, "资源", False, f"正文引用的二维码文件不存在：{name}")
                break
    add_result(results, "资源", qr_resources_ok,
               f"技术交流群二维码 {len(qr_refs)} 码均在 [grid] 内引用" if qr_resources_ok
               else f"技术交流群二维码缺失或未在 [grid] 内引用（模板为 {QR_EXPECTED_COUNT} 码：本地大模型用户交流群/AI智能体开发平台运营群/语义本体构建交流群/KNS统一数据平台/标准化助手交流群/风衍大模型用户交流群）")

    # 2) 尺寸统一门禁（走到这里必然有码）
    if qr_refs:
        bad_sizes = []
        for alt, link in qr_refs:
            size_match = QR_SIZE_PATTERN.search(alt or "")
            if not size_match or (size_match.group(1), size_match.group(2)) != QR_EXPECTED_SIZE:
                shown = size_match.group(0).strip() if size_match else "无尺寸标注"
                bad_sizes.append(f"{link}（{shown}）")
        add_result(results, "资源", not bad_sizes,
                   f"二维码显示尺寸统一为 400x400（{len(qr_refs)} 码）" if not bad_sizes
                   else f"二维码显示尺寸必须统一为 |400x400，以下不合规：{'、'.join(bad_sizes)}")

    # 3) 码数门禁（模板固定 5 码，增减须经用户确认并同步模板与文末标准）
    if grid_refs:
        add_result(results, "资源", len(qr_refs) == QR_EXPECTED_COUNT,
                   f"技术交流群二维码为 {QR_EXPECTED_COUNT} 码（模板标准）" if len(qr_refs) == QR_EXPECTED_COUNT
                   else f"技术交流群二维码应为 {QR_EXPECTED_COUNT} 码，当前识别到 {len(qr_refs)} 码（增减须经用户确认并同步更新模板与文末标准）")

    return qr_refs


def weak_source_matches(url):
    lowered = url.lower()
    return any(re.search(pattern, lowered) for pattern in WEAK_SOURCE_PATTERNS)


def candidate_weak_source_refs(cand):
    """返回候选 source_url + source_urls 中命中的弱来源 URL 列表（去重保序）。

    P0-②（V3.6.0）：SELECTED 候选任一带弱来源即源头拦截，从检索阶段就阻止弱源进入正文。
    """
    pool = []
    primary = cand.get("source_url")
    if isinstance(primary, str) and primary.strip():
        pool.append(primary.strip())
    for u in cand.get("source_urls") or []:
        if isinstance(u, str) and u.strip():
            pool.append(u.strip())
    seen, bad = set(), []
    for u in pool:
        if u in seen:
            continue
        seen.add(u)
        if weak_source_matches(u):
            bad.append(u)
    return bad


def media_domain_matches(url):
    host = urlsplit(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == domain or host.endswith("." + domain) for domain in MEDIA_DOMAIN_BLOCKLIST)


def allowed_event_dates(report_date, allow_same_day, extra_dates=None):
    """按执行点增量口径返回本期允许收录的事件日期集合。

    方案 A（2026-09-08 用户确立）：自动化在每日固定时点执行，
    本期正文收录「上一执行点之后 → 当前执行点之前」的增量新闻，而非自然日。
    **当前执行时点为 08:30**，真源是自动化配置（见 SKILL.md §5）；本 docstring 只记录口径。

    由于正文标题只标注「M月D日」的日粒度、不含具体时刻，增量窗口映射到日期集合为：
    - 周一（跨周末，上次执行为上周五）：允许 {report_date-3, -2, -1, report_date}，
      即「上周五 → 周一」；其中 report_date 是增量右端点（周一执行时点前发布的事件可收录）。
    - 周二至周五（连续每日执行）：允许 {report_date-1, report_date}，
      即「昨天 → 今天」；report_date 为增量窗口右端点（当天执行时点前发布的事件可收录）。

    与旧「自然日口径」的核心差异：把 report_date（执行当日）纳入默认 allowed，
    兑现「拿上一次执行之后到今天的最新数据」的实时性诉求，不再默认排除当天。
    allow_same_day（revise 转正）作为向后兼容保留；执行当日现已是默认允许。
    """
    if report_date is None:
        return None
    if report_date.weekday() == 0:
        allowed = {report_date - timedelta(days=offset) for offset in (0, 1, 2, 3)}
    else:
        allowed = {report_date - timedelta(days=offset) for offset in (0, 1)}
    if allow_same_day:
        allowed.add(report_date)
    # 缺跑补检累积出刊（V3.9.3 / retrieval-strategy §2）：某工作日未执行时，下一次执行的
    # 增量窗口自动顺延覆盖"上次执行至今"的间隔。原先只按 d2/d4 硬算，累积出刊会把补检日
    # 全部误判为越窗并强制加「跟踪报道」前缀，与技能口径冲突。此处按环境变量指向的补检
    # 日期清单扩展允许窗口（由执行 Agent 在累积出刊时设置 AI_RADAR_MISSED_DATES）。
    missed = os.environ.get("AI_RADAR_MISSED_DATES", "")
    if missed:
        for token in re.split(r"[,\s]+", missed.strip()):
            token = token.strip()
            if not token:
                continue
            try:
                allowed.add(date.fromisoformat(token[:10]))
            except (ValueError, TypeError):
                continue
    # 与 preflight_check 口径一致：检索清单自身声明的 missed_dates 直接扩展允许窗口，
    # 不依赖执行 Agent 手工设置环境变量，避免下次缺跑补检时重现误判。
    for extra in extra_dates or ():
        if isinstance(extra, date):
            allowed.add(extra)
    return allowed


def check_event_date(results, title, block, report_date, allowed):
    date_match = re.search(r"(\d{1,2})月(\d{1,2})日", title)
    if not date_match:
        add_result(results, "时效", False, f"《{title}》未标注日期")
        return
    if report_date is None or allowed is None:
        return
    item_date = event_date_from_month_day(report_date, int(date_match.group(1)), int(date_match.group(2)))
    if item_date is None:
        add_result(results, "时效", False, f"《{title}》包含无效日期")
        return
    is_followup = FOLLOWUP_MARK in title
    if item_date in allowed:
        followup_note = "，且标注了跟踪报道" if is_followup else ""
        add_result(results, "时效", True, f"《{title}》事件日期符合刊发口径{followup_note}")
    elif is_followup:
        has_origin_date = bool(re.search(
            rf"跟踪报道\s*[·．.]?\s*\d{{1,2}}月\d{{1,2}}日", title
        )) or "该事件发生于" in block
        add_result(results, "跟踪报道", has_origin_date,
                   f"《{title}》为跟踪报道（事件早于本期刊发口径），已注明事件原始日期"
                   if has_origin_date else f"《{title}》标注了跟踪报道，但缺少事件原始日期（标题须用（跟踪报道·M月D日）并附提示行）")
    else:
        allowed_text = "、".join(day.strftime("%m月%d日").lstrip("0").replace("月0", "月") for day in sorted(allowed))
        item_date_text = item_date.strftime("%m月%d日").lstrip("0").replace("月0", "月") if item_date else "无效日期"
        add_result(results, "时效", False,
                   f"《{title}》事件日期为{item_date_text}，本期仅允许{allowed_text}；旧事件须标注「（跟踪报道）」并注明原始日期，人工修订当日事件转正须加 --allow-same-day")


def check_sources(results, title, block):
    unique_urls = {normalize_url(url) for url in extract_urls(block)}
    single_ok = SINGLE_SOURCE_MARK in block and len(unique_urls) == 1
    add_result(results, "来源", len(unique_urls) >= 2 or single_ok,
               f"《{title}》有 {len(unique_urls)} 个来源（要求至少2个；单一 T0 官方来源须标注「（单一官方来源）」）")
    weak_urls = [url for url in unique_urls if weak_source_matches(url)]
    strong_count = len(unique_urls) - len(weak_urls)
    if weak_urls:
        source_quality_ok = strong_count >= 1
        add_result(results, "来源质量", source_quality_ok,
                   f"《{title}》含 {len(weak_urls)} 个弱来源（公众号/聚合号），另有 {strong_count} 个强来源，弱来源须排在强来源之后"
                   if source_quality_ok else f"《{title}》全部来源均为公众号/聚合号等弱来源，禁止发布")
    else:
        add_result(results, "来源质量", True, f"《{title}》来源均为官网/权威媒体")


def validate_minimal_schema(data, required_fields):
    return isinstance(data, dict) and bool(data) and all(field in data for field in required_fields)


def parse_iso_date(value, allow_null=False):
    if value is None and allow_null:
        return True
    if not isinstance(value, str) or not value.strip():
        return False
    candidate = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
            date.fromisoformat(candidate)
        else:
            datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def date_fields_valid(mapping):
    if not isinstance(mapping, dict):
        return False
    for name, value in mapping.items():
        if name.endswith(("_at", "_date")) and value is not None and not parse_iso_date(value):
            return False
    return True


def validate_evidence_receipts(results, owner, receipts, body_urls, required):
    if receipts is None:
        add_result(results, "证据回执", not required,
                   f"{owner}缺少必需的 evidence_receipts" if required else f"{owner}未提供可选 evidence_receipts")
        return

    receipts_ok = isinstance(receipts, list) and bool(receipts)
    covered_urls = set()
    receipt_detail = ""
    if receipts_ok:
        for receipt in receipts:
            if not isinstance(receipt, dict):
                receipts_ok = False
                break
            sources = receipt.get("sources")
            verification = receipt.get("verification")
            receipt_ok = (
                bool(receipt.get("claim_id"))
                and bool(receipt.get("claim_text"))
                and receipt.get("claim_type") in {"date", "actor", "action", "number", "specification", "status", "quote"}
                and receipt.get("materiality") in {"critical", "supporting"}
                and isinstance(sources, list)
                and bool(sources)
                and isinstance(verification, dict)
                and verification.get("status") == "PASS"
            )
            receipt_ok = receipt_ok and date_fields_valid(receipt)
            if not receipt_ok:
                bad = []
                if not receipt.get("claim_id"):
                    bad.append("缺少 claim_id")
                if not receipt.get("claim_text"):
                    bad.append("缺少 claim_text")
                if receipt.get("claim_type") not in {"date", "actor", "action", "number", "specification", "status", "quote"}:
                    bad.append(f"claim_type={receipt.get('claim_type')!r} 非法（须 ∈ date/actor/action/number/specification/status/quote）")
                if receipt.get("materiality") not in {"critical", "supporting"}:
                    bad.append(f"materiality={receipt.get('materiality')!r} 非法（须 ∈ critical/supporting）")
                if not isinstance(sources, list) or not sources:
                    bad.append("sources 为空或非列表")
                if not isinstance(verification, dict) or verification.get("status") != "PASS":
                    bad.append("verification 状态非 PASS")
                if not date_fields_valid(receipt):
                    bad.append("含无效日期字段")
                receipt_detail = f"（{receipt.get('claim_id')}：{'；'.join(bad)}）"
                receipts_ok = False
                break
            for source in sources:
                source_ok = (
                    isinstance(source, dict)
                    and bool(source.get("url"))
                    and source.get("tier") in {"T0", "T1", "T2", "T3", "T4"}
                    and bool(source.get("locator"))
                    and bool(source.get("verbatim"))
                    and source.get("supports") in {"full", "partial", "contradicts"}
                )
                source_ok = source_ok and date_fields_valid(source)
                if not source_ok:
                    receipts_ok = False
                    break
                source_url = normalize_url(source["url"])
                if source_url not in body_urls:
                    receipts_ok = False
                    break
                covered_urls.add(source_url)
            if not receipts_ok:
                break
    coverage_ok = receipts_ok and covered_urls == body_urls
    add_result(results, "证据回执", coverage_ok,
               f"{owner}证据回执符合 V3.2 schema 且覆盖全部正文来源"
               if coverage_ok else f"{owner}的 evidence_receipts 为空、字段或日期无效、验证未 PASS、来源越界或未覆盖全部正文来源{receipt_detail}")


def run_checks(md_path, semantic_path, search_log_path, cover_path=None, qr1_path=None, qr2_path=None,
               qr3_path=None, recent_days=2, recent_data_path=None, allow_same_day=False,
               report_date=None, timezone="Asia/Shanghai", require_evidence_receipts=False,
               mode="normal", ruleset_version=RULESET_VERSION):
    raw = Path(md_path).read_text(encoding="utf-8")
    results = []
    # V5 三层版式（2026-10-30 起）：原文先过 V5 结构门禁，再把精编层适配为 V4 结构复用下方全部检查
    v5_raw = raw if layout_v5.is_v5(raw) else None
    if v5_raw is not None:
        raw = layout_v5.to_v4(v5_raw)
    mode_ok = mode in {"normal", "backfill", "revise", "resume"}
    add_result(results, "运行模式", mode_ok, f"运行模式为 {mode}" if mode_ok else f"无效运行模式：{mode}")
    allow_same_day_ok = not allow_same_day or mode == "revise"
    add_result(results, "运行模式", allow_same_day_ok,
               "--allow-same-day 仅在 revise 模式使用" if allow_same_day else "执行点增量口径默认含当日（允许收上一执行点之后到本周期的新闻）")
    ruleset_ok = ruleset_version == RULESET_VERSION
    add_result(results, "规则版本", ruleset_ok,
               f"规则版本为 {RULESET_VERSION}" if ruleset_ok else f"不支持的规则版本：{ruleset_version}")
    require_evidence_receipts = require_evidence_receipts or ruleset_version == RULESET_VERSION
    headings = CORE_HEADINGS + TAIL_HEADINGS
    forward_heading = FORWARD_HEADING
    resolved_date, filename_date, h1_date = resolve_report_date(
        md_path, raw, explicit_report_date=report_date, timezone=timezone
    )
    report_date = resolved_date
    if v5_raw is not None:
        for ok, message in layout_v5.check_structure(v5_raw, report_date, WEAK_SOURCE_PATTERNS):
            add_result(results, "V5版式", ok, message)

    date_source_ok = bool(report_date) and (filename_date is not None or bool(report_date))
    add_result(results, "结构", date_source_ok,
               "已解析日报日期" if date_source_ok else "必须通过 --report-date 或 AI雷达日报_YYYY-MM-DD.md 提供日期")
    h1_text = title_spec.extract_h1(raw)
    h1_format_ok = h1_text is not None
    add_result(results, "结构", h1_format_ok,
               "一级标题格式正确" if h1_format_ok else "缺少或错误的日报一级标题（应为「{钩子} |AI雷达M.D」或历史格式「AI 雷达日报｜M.D」）")
    if h1_text is not None:
        # V5.3.2 新增：钩子规则（长度/数字/禁用词/竖线/结尾标点）；历史格式自动豁免
        title_problems = title_spec.validate_title(h1_text)
        add_result(results, "格式", not title_problems,
                   "日报标题合规（钩子 + 品牌后缀）" if not title_problems
                   else "标题不合规：" + "；".join(title_problems))
    filename_consistent = filename_date is None or filename_date == report_date
    add_result(results, "日期", filename_consistent,
               "文件名日期与 report_date 一致" if filename_consistent else "文件名日期与 report_date 不一致")
    h1_consistent = h1_date is not None and h1_date == report_date
    add_result(results, "日期", h1_consistent,
               "正文 H1 日期与 report_date 一致" if h1_consistent else "正文 H1 日期与 report_date 不一致")

    for heading in headings:
        add_result(results, "结构", heading in raw, f"存在标题：{heading}" if heading in raw else f"缺少标题：{heading}")

    # 「全部资讯」模块禁入正文（V3.9.0）：用户明确要求日报正文删除资讯清单，
    # 只在知识库保留候选全集记录。正文出现该模块即 FAIL（不再校验其位置与链接）。
    # 历史期次豁免：report_date 早于 LEGACY_ALL_INFO_STRICT_FROM（即 ≤2026-09-23）的
    # 已发布期次不回溯判 FAIL。
    _strict_from = date.fromisoformat(LEGACY_ALL_INFO_STRICT_FROM)
    legacy_issue = hasattr(report_date, "isoformat") and report_date < _strict_from
    if ALL_INFO_TAIL_HEADING in raw:
        if legacy_issue:
            add_result(results, "结构", True,
                       f"历史期次（{report_date} < {LEGACY_ALL_INFO_STRICT_FROM}）豁免资讯清单禁入校验"
                       "——该期发布时规则尚未生效，正文不得改写")
        else:
            add_result(results, "结构", False,
                       "正文不得出现「全部资讯（候选全集）」模块——资讯清单只留知识库，"
                       "日报正文已停用该尾部模块（V3.9.0）；请删除该段后重跑")
    else:
        add_result(results, "结构", True, "正文未包含资讯清单模块（符合 V3.9.0 规范）")

    # 累积出刊标识（V3.9.0）：权威来源是检索清单 publication_metadata，**不再**写在正文。
    # 正文出现累积出刊行说明仍按旧口径撰写，判 FAIL；metadata 校验在 preflight 侧完成。
    # 历史期次同样豁免（旧期次可能以正文行承载累积标识）。
    if "累积出刊" in raw and not legacy_issue:
        add_result(results, "结构", False,
                   "正文不得再承载累积出刊标识（V3.9.0 起改由检索清单 publication_metadata 承载，"
                   "飞书推送文案从该字段解析，正文不留痕）")

    for legacy in LEGACY_FORWARD_HEADINGS:
        add_result(results, "结构", legacy not in raw,
                   f"未使用旧版前瞻标题「{legacy}」" if legacy not in raw else f"检测到旧版前瞻标题：{legacy}，前瞻板块已停用，不应再使用任何前瞻标题")

    heading_positions = [raw.find(h) for h in headings]
    ordered = all(p >= 0 for p in heading_positions) and heading_positions == sorted(heading_positions)
    add_result(results, "结构", ordered, "各板块顺序正确" if ordered else "板块缺失或顺序错误")

    separators_ok = True
    if ordered:
        pairs = list(zip(headings, headings[1:]))
        for idx, (left, right) in enumerate(zip(heading_positions, heading_positions[1:])):
            if pairs[idx][0] in TAIL_HEADINGS and pairs[idx][1] in TAIL_HEADINGS:
                continue  # 尾部区块（能力地图→群二维码）内部不强制分隔线
            if not re.search(r"(?m)^---\s*$", raw[left:right]):
                separators_ok = False
                break
    else:
        separators_ok = False
    add_result(results, "格式", separators_ok, "板块之间均有分隔线" if separators_ok else "相邻板块之间缺少 --- 分隔线")

    bold_space_hits = re.findall(BOLD_SPACE_PATTERN, raw)
    add_result(results, "格式", not bold_space_hits,
               "加粗标签后均有空格" if not bold_space_hits else f"存在 {len(bold_space_hits)} 处「：**」后紧跟正文，须补空格（规范：**标签：** 正文）")

    glance = section_text(raw, "## 一分钟速览")
    if glance.strip():
        glance_bullets = GLANCE_BULLET_PATTERN.findall(glance)
        add_result(results, "内容", 4 <= len(glance_bullets) <= 6,
                   f"一分钟速览共 {len(glance_bullets)} 条（要求4-6条）")

    daily_items = parse_section_items(section_text(raw, "## 今日要紧"))
    energy_items = parse_section_items(section_text(raw, "## 金风相关"))
    all_items = daily_items + energy_items

    add_result(results, "内容", 2 <= len(daily_items) <= 8,
               f"今日要紧共 {len(daily_items)} 条（要求2-8条）")
    # V4.2（2026-10-26 用户决定）：行动标签由「必填编辑标记」降为「可选编辑标记」。
    # 正文可以完全不写【需行动】/【可借鉴】/【了解】；一旦写了则只允许这三类，不得自创第四类。
    # 分层表达改由飞书三群视角标签（【能力复用】/【数据价值】/【部署门槛】）承担。
    tag_match = re.compile(r"^###\s*【([^】]+)】")
    used_tags = {
        m.group(1) for item in daily_items
        for m in [tag_match.match(item["block"].lstrip())] if m
    }
    unknown_tags = sorted(used_tags - {"需行动", "可借鉴", "了解"})
    add_result(results, "结构", not unknown_tags,
               "今日要紧行动标签为可选编辑标记（V4.2），未使用自创标签"
               if not unknown_tags
               else "今日要紧出现自创行动标签：" + "、".join(f"【{t}】" for t in unknown_tags)
                    + "，仅允许【需行动】【可借鉴】【了解】")

    # V4.2：首屏图例行（覆盖窗口｜行动标签｜可信度）不再强制出现。
    # 历史 V4.1 期次正文含该行，仍可通过；可信度三色口径只在模板说明与 [details] 标题中保留。

    # 缺跑补检（V3.9.3）：允许窗口吸收检索清单 publication_metadata.missed_dates
    missed_window_dates = set()
    try:
        pool_log = json.loads(Path(search_log_path).read_text(encoding="utf-8")) if search_log_path else {}
    except Exception:
        pool_log = {}
    pub_meta = pool_log.get("publication_metadata") or {}
    if pub_meta.get("recovery_reason") == "missed_runs":
        for token in pub_meta.get("missed_dates", []) or []:
            try:
                missed_window_dates.add(date.fromisoformat(str(token)[:10]))
            except (ValueError, TypeError):
                continue
    allowed = allowed_event_dates(report_date, allow_same_day, missed_window_dates)

    for item in daily_items:
        interpretation_count = len(re.findall(r"(?m)^(?:\*\*)?(?:一句话解读|对我们)：(?:\*\*)?\s*\S", item["block"]))
        non_meta_lines = [line.strip() for line in item["block"].splitlines()
                          if line.strip() and line.strip() != "---"
                          and not line.startswith("🔹")
                          and not line.startswith("一句话解读：")
                          and not SOURCE_LINE_PATTERN.match(line)]
        # V4 版式：每条为「标题 + 正文 + 对我们判断 + 可信度 + 折叠来源」，取消紧凑一行式限制
        add_result(results, "结构", interpretation_count == 1,
                   f"《{item['title']}》含一条判断行"
                   if interpretation_count == 1
                   else f"《{item['title']}》须且仅须包含一条「**对我们：** …」判断行（V4 版式）")
        # 一句话解读字数硬门禁（V3.5.3）：一行「一句话解读：」后全文（含【透镜标签】）去掉空白后 ≤90 字，模板 rules 5
        interp_match = re.search(r"(?m)^(?:\*\*)?对我们：(?:\*\*)?\s*(.+)$", item["block"])
        if interp_match:
            interp_len = len(re.sub(r"\s+", "", interp_match.group(1)))
            add_result(results, "内容", interp_len <= 90,
                       f"《{item['title']}》一句话解读 {interp_len} 字（要求≤90字，含【透镜标签】）"
                       if interp_len <= 90 else f"《{item['title']}》一句话解读 {interp_len} 字，超过 90 字上限，须精简（含【透镜标签】）")
        check_event_date(results, item["title"], item["block"], report_date, allowed)
        check_sources(results, item["title"], item["block"])

    for item in energy_items:
        has_date = bool(re.search(r"(\d{1,2})月(\d{1,2})日", item["title"]))
        if has_date:
            check_event_date(results, item["title"], item["block"], report_date, allowed)
            check_sources(results, item["title"], item["block"])
        else:
            unique_urls = {normalize_url(url) for url in item["urls"]}
            strong_urls = [url for url in unique_urls if not weak_source_matches(url)]
            add_result(results, "来源", len(strong_urls) >= 2,
                       f"《{item['title']}》为业务知识卡，有 {len(strong_urls)} 个非弱来源（要求至少2个）"
                       if len(strong_urls) >= 2 else f"《{item['title']}》知识卡非弱来源不足2个，须有权威研究/文献支撑")

    energy_section = section_text(raw, "## 金风相关")
    energy_nonempty = bool(energy_items) or "今日暂无经双源核验的金风相关重要消息。" in energy_section
    add_result(results, "内容", energy_nonempty,
               "金风相关板块非空（新闻或知识卡）" if energy_nonempty else "金风相关板块为空，须用新闻或知识卡补位")

    builder = section_text(raw, "## 工具与做法")
    # V5.0.1：当日确无可借鉴做法时，允许固定空行（与金风相关的空行写法对称）
    builder_empty_ok = layout_v5.NO_TOOLS_LINE in builder and not SECTION_TITLE_PATTERN.search(builder)
    add_result(results, "内容", bool(SECTION_TITLE_PATTERN.search(builder)) or builder_empty_ok,
               f"工具与做法含 {len(SECTION_TITLE_PATTERN.findall(builder))} 条做法"
               if SECTION_TITLE_PATTERN.search(builder)
               else ("工具与做法当日无条目（已写固定空行）" if builder_empty_ok else "工具与做法缺少 ### 条目"))
    add_result(results, "来源", bool(LINK_PATTERN.findall(builder)) or builder_empty_ok,
               "工具与做法含来源链接" if LINK_PATTERN.findall(builder)
               else ("工具与做法当日无条目，无需来源" if builder_empty_ok else "工具与做法缺少来源链接"))

    practice = section_text(raw, "## 行业速递")
    add_result(results, "内容", len(re.findall(r"(?m)^-\s+", practice)) >= 3,
               f"行业速递含 {len(re.findall(r'(?m)^-\s+', practice))} 条快讯（要求≥3条）")
    add_result(results, "来源", bool(LINK_PATTERN.findall(practice)),
               "行业速递含来源链接" if LINK_PATTERN.findall(practice) else "行业速递缺少来源链接")

    feature_sections = {
        "金风相关": (section_text(raw, "## 金风相关"), MAX_FEATURE_BODY_CHARS),
        "工具与做法": (section_text(raw, "## 工具与做法"), MAX_FEATURE_BODY_CHARS),
        "行业速递": (section_text(raw, "## 行业速递"), MAX_DIGEST_BODY_CHARS),
    }
    for feature_name, (feature_section, limit) in feature_sections.items():
        feature_chars = feature_body_char_count(feature_section)
        add_result(
            results,
            "内容",
            feature_chars <= limit,
            f"{feature_name}正文 {feature_chars} 字（要求≤{limit}字；标题、来源和固定属性说明不计）"
            if feature_chars <= limit
            else f"{feature_name}正文 {feature_chars} 字，超过 {limit} 字上限，须精简",
        )

    normalized_titles = [normalize_title(item["title"]) for item in all_items]
    duplicate_titles = sorted({title for title in normalized_titles if normalized_titles.count(title) > 1})
    add_result(results, "去重", not duplicate_titles,
               "日报内部无重复标题" if not duplicate_titles else f"日报内部存在重复标题：{', '.join(duplicate_titles)}")

    item_url_owners = {}
    for item in all_items:
        for url in {normalize_url(value) for value in item["urls"]}:
            item_url_owners.setdefault(url, set()).add(item["title"])
    duplicate_urls = {url: owners for url, owners in item_url_owners.items() if len(owners) > 1}
    add_result(results, "去重", not duplicate_urls,
               "不同新闻之间无重复来源URL" if not duplicate_urls else f"不同新闻复用了 {len(duplicate_urls)} 个来源URL")

    recent_titles, recent_urls, recent_error = load_recent_data(
        recent_data_path, recent_days, report_date=report_date, timezone=timezone
    )
    add_result(results, "跨期去重", recent_error is None,
               f"已取得最近{recent_days}期日报去重数据" if recent_error is None else recent_error)
    recent_title_map = {normalize_title(title): title for title in recent_titles}
    current_title_map = {normalize_title(item["title"]): item["title"] for item in all_items}
    repeated_titles = [current_title_map[key] for key in current_title_map if key in recent_title_map]
    recent_url_set = {normalize_url(url) for url in recent_urls}
    repeated_urls = sorted(set(item_url_owners) & recent_url_set)
    add_result(results, "跨期去重", not repeated_titles,
               "未发现与近期日报相同的标题" if not repeated_titles else f"与近期日报重复标题：{'；'.join(repeated_titles)}")
    add_result(results, "跨期去重", not repeated_urls,
               "未发现与近期日报相同的来源URL" if not repeated_urls else f"与近期日报重复来源URL：{len(repeated_urls)} 个")

    try:
        search_log = json.loads(Path(search_log_path).read_text(encoding="utf-8"))
    except Exception as exc:
        search_log = {}
        add_result(results, "召回门禁", False, f"检索清单无法读取：{exc}")

    search_schema_fields = (
        "report_date", "window", "queries", "official_sources_checked", "candidates", "recall_review"
    )
    search_schema_ok = bool(search_log) and validate_minimal_schema(search_log, search_schema_fields)
    add_result(results, "召回门禁", search_schema_ok,
               "检索清单最小 schema 完整" if search_schema_ok else "检索清单为空或缺少必填字段")

    if search_schema_ok:
        search_dates_ok = date_fields_valid(search_log)
        add_result(results, "召回门禁", search_dates_ok,
                   "检索清单日期字段可解析" if search_dates_ok else "检索清单含无效日期字段")
        expected_date = report_date.isoformat() if report_date else ""
        add_result(results, "召回门禁", search_log.get("report_date") == expected_date,
                   "检索清单日期与日报一致" if search_log.get("report_date") == expected_date else "检索清单日期与日报不一致")
        expected_window = "d4" if report_date and report_date.weekday() == 0 else "d2"
        add_result(results, "召回门禁", search_log.get("window") == expected_window,
                   f"检索窗口为 {expected_window}" if search_log.get("window") == expected_window else f"检索窗口必须为 {expected_window}")

        queries = [str(value).lower() for value in search_log.get("queries", []) if str(value).strip()]
        has_general_query = any("ai" in value or "大模型" in value or "人工智能" in value for value in queries)
        has_agent_query = any(any(term in value for term in ("harness", "runtime", "framework", "app server", "sdk", "执行层", "运行时", "智能体")) for value in queries)
        add_result(results, "召回门禁", has_general_query,
                   "已记录通用 AI 检索词" if has_general_query else "检索清单缺少通用 AI 检索词")
        add_result(results, "召回门禁", has_agent_query,
                   "已记录 Agent 基础设施专项检索词" if has_agent_query else "检索清单缺少 Agent Harness/Runtime/SDK 专项检索")

        required_official_sources = [
            "openai", "anthropic", "google", "meta", "microsoft", "xai",
            "deepseek", "qwen", "bytedance", "智谱", "月之暗面", "minimax", "百度", "腾讯",
        ]
        official_entries = search_log.get("official_sources_checked", [])
        official_names = " ".join(str(item.get("name", "")).lower() for item in official_entries if isinstance(item, dict))
        missing_official = [name for name in required_official_sources if name.lower() not in official_names]
        malformed_official = [
            item for item in official_entries
            if not isinstance(item, dict)
            or item.get("checked") is not True
            or not item.get("name")
            or not item.get("url")
        ]
        add_result(results, "召回门禁", not missing_official and not malformed_official,
                   "重点官方主体均有检查记录" if not missing_official and not malformed_official else f"重点官方源记录缺失或不完整：{', '.join(missing_official) or '存在无效记录'}")

        candidates = search_log.get("candidates", [])
        if v5_raw is not None:
            for ok, message in layout_v5.check_against_search_log(v5_raw, search_log):
                add_result(results, "召回门禁", ok, message)
        valid_decisions = {
            "SELECTED", "TRACKING", "SIGNAL", "REJECTED_DUPLICATE", "REJECTED_STALE",
            "REJECTED_LOW_PRIORITY", "REJECTED_UNVERIFIED",
        }
        candidates_ok = bool(candidates) and all(
            isinstance(item, dict)
            and item.get("title")
            and parse_iso_date(item.get("event_date"))
            and item.get("source_name")
            and item.get("source_url")
            and item.get("decision") in valid_decisions
            and date_fields_valid(item)
            for item in candidates
        )
        add_result(results, "召回门禁", candidates_ok,
                   f"候选池共 {len(candidates)} 条且淘汰原因完整" if candidates_ok else "候选池为空或存在缺少日期、来源、URL、决策的记录")

        # P0-② 候选池 SELECTED 弱来源硬门禁（V3.6.0 起）：SELECTED（待入选正文）候选的主 source_url
        # 或任一个 source_urls 命中弱来源（公众号/聚合号）即 FAIL，从源头拦截，避免拖到正文/自查阶段
        # 才返工替换（2026-09-17 金开新能 toutiao 弱源、AgentGit 163/dy 弱源即此类漏网）。
        if candidates_ok:
            weak_selected = []
            for cand in candidates:
                if cand.get("decision") != "SELECTED":
                    continue
                bad_pool = candidate_weak_source_refs(cand)
                if bad_pool:
                    weak_selected.append(f"{cand.get('id', '?')}《{cand.get('title')}》弱源={bad_pool}")
            add_result(results, "召回门禁", not weak_selected,
                       "SELECTED 候选来源均非弱来源（公众号/聚合号）" if not weak_selected
                       else f"以下 SELECTED 候选含弱来源，须替换为强第三方/官方源或淘汰：{'；'.join(weak_selected)}")

        # 候选落窗硬门禁（V3.5.3）：SELECTED 且已标 event_date 的候选，事件日期须落在本期允许窗口内；
        # 覆盖大事件/能源/Builder/实践全部板块（09-14 Builder Mantis 09-10 越界即此类漏网）。
        # 已带「跟踪报道」标记或事件日期缺失的候选不在此判定（旧事件由正文侧 check_event_date 校验）。
        if candidates_ok and allowed:
            out_of_window = []
            for cand in candidates:
                if cand.get("decision") != "SELECTED":
                    continue
                title = cand.get("title") or ""
                if FOLLOWUP_MARK in title:
                    continue
                ed_value = (cand.get("event_date") or "").strip()
                try:
                    ed = date.fromisoformat(ed_value[:10])
                except (ValueError, TypeError):
                    continue
                if ed not in allowed:
                    out_of_window.append(f"{cand.get('id', '?')}《{title}》event={cand.get('event_date')}")
            add_result(results, "召回门禁", not out_of_window,
                       f"SELECTED 候选事件日期均落在本期窗口内" if not out_of_window
                       else f"以下 SELECTED 候选事件日期超出本期窗口，须改跟踪报道或淘汰：{'；'.join(out_of_window)}")

        recall = search_log.get("recall_review", {})
        recall_ok = (
            recall.get("status") == "PASS"
            and parse_iso_date(recall.get("reviewed_at"))
            and recall.get("major_missed_events") == []
            and bool(recall.get("notes"))
            and date_fields_valid(recall)
        )
        add_result(results, "召回门禁", recall_ok,
                   "重大事件召回复核为 PASS，未发现漏报" if recall_ok else "重大事件召回复核未通过、未完成或仍有漏报")

        # P2-⑥ 阶段耗时明细（V3.6.0 起，可选）：检索清单可带 timings 节点记录阶段2/3 子节点耗时，复盘据此精确归因。
        timings = search_log.get("timings", {})
        if timings:
            timings_basis = timings.get("basis") in {"d2", "d4"}
            timings_ok = bool(timings_basis)
            add_result(results, "召回门禁", timings_ok,
                       "检索清单含阶段耗时明细，basis 合法" if timings_ok else "timings.basis 必须为 d2 或 d4")
        else:
            add_result(results, "召回门禁", True, "检索清单未含阶段耗时明细（可选，不阻断）")

    deep_section = section_text(raw, "## 深度聚焦")
    deep_urls = {normalize_url(url) for _, url in LINK_PATTERN.findall(deep_section)}
    # V4 版式取消独立「深度聚焦」板块（深度判断并入「今日要紧」的编辑判断行）；
    # 板块缺失时跳过深焦硬门禁，存在时仍按原标准校验。
    if deep_section.strip():
        deep_urls = {normalize_url(url) for _, url in LINK_PATTERN.findall(deep_section)}
        add_result(results, "内容", bool(SECTION_TITLE_PATTERN.search(deep_section)),
                   "深度聚焦有标题" if SECTION_TITLE_PATTERN.search(deep_section) else "深度聚焦缺少 ### 标题")
        add_result(results, "来源", len(deep_urls) >= 2,
                   f"深度聚焦有 {len(deep_urls)} 个官方原始来源（要求至少2个）")
        deep_body = re.sub(r"(?m)^(?:##\s+深度聚焦|###\s+.+|\*\*来源：\*\*.+|---)\s*$", "", deep_section)
        deep_paragraphs = [part.strip() for part in re.split(r"\n\s*\n", deep_body) if part.strip()]
        deep_length = len(re.sub(r"\s+", "", deep_body))
        deep_slim = 2 <= len(deep_paragraphs) <= 3 and deep_length <= 850
        add_result(results, "内容", deep_slim,
                   f"深度聚焦为 {len(deep_paragraphs)} 个短段、正文 {deep_length} 字符（要求2-3段且≤850字符）")
        media_deep_urls = sorted({url for _, url in LINK_PATTERN.findall(deep_section) if media_domain_matches(url)})
        add_result(results, "来源", not media_deep_urls,
                   "深度聚焦来源均非媒体/聚合域名（官方源硬门禁）" if not media_deep_urls
                   else f"深度聚焦含媒体/聚合域名，违反仅官方原始页标准：{', '.join(media_deep_urls)}")

    insight_absent = "对金风的启示" not in raw
    add_result(results, "内容边界", insight_absent,
               "全文未输出对金风的启示" if insight_absent else "检测到已暂停的‘对金风的启示’，禁止发布")
    add_result(results, "格式", CONTACT_LINE in raw,
               "固定免责声明与联系人尾段完整" if CONTACT_LINE in raw else "缺少固定免责声明与联系人尾段")
    # V5.1.0：残留占位符（2026-11-02 实测：“社区今日”占位行曾无阻拦地通过自查）
    placeholders = [t for t in PLACEHOLDER_TOKENS if t in raw]
    add_result(results, "格式", not placeholders,
               "正文无残留占位符" if not placeholders else f"正文含未替换的占位内容：{', '.join(placeholders)}")
    # V5.1.0：公司网络打不开的站点（config/link-reachability.json），尾部固定模块不检查
    body_part = raw.split("### 集团AI能力地图")[0]
    blocked_hits = sorted({url for _t, url in LINK_PATTERN.findall(body_part) if blocked_domain_of(url)})
    add_result(results, "来源", not blocked_hits,
               "正文链接均不在公司网络不可达名单内" if not blocked_hits
               else f"正文含公司网络打不开的链接，须换成可访问来源：{', '.join(blocked_hits[:4])}")
    # 2026-11-12 用户改版：署名后只保留跟踪语一句 + 社区交流邀请一句；
    # 「**欢迎投稿**：…」加粗前缀取消，往期合集行整体删除（论坛首页已有入口，正文不再挂）。
    sign_ok = SIGN_LINE_1 in raw and SIGN_LINE_2 in raw
    add_result(results, "格式", sign_ok,
               "双层签名档完整" if sign_ok else "签名档不完整（须为：**出品｜集团数字化中心** / **策划整理｜AI应用研究院**）")
    old_dify_urls = ("https://keconsole.goldwind.com.cn/apps", "https://keconsole.goldwind.com/apps")
    dify_url_ok = DIFY_PLATFORM_URL in raw and not any(url in raw for url in old_dify_urls)
    add_result(results, "格式", dify_url_ok,
               f"Dify 平台链接正确：{DIFY_PLATFORM_URL}" if dify_url_ok else f"Dify 平台链接必须统一为 {DIFY_PLATFORM_URL}")
    # 2026-11-12 用户改版：往期合集行从正文尾部移除，改由论坛分类页/首页承载；
    # 若旧稿照抄带回来，这里不阻断（历史期只读），但模板与新版文案不再产出该行。
    tail_interactive = [t for t in TAIL_INTERACTIVE_TOKENS if t in raw]
    add_result(results, "格式", not tail_interactive,
               "尾部未挂互动尾缀" if not tail_interactive
               else f"尾部不得再挂互动尾缀（2026-11-11 已删）：{'、'.join(tail_interactive)}")
    add_result(results, "格式", SUBMIT_LINE in raw,
               "社区交流邀请行文案正确" if SUBMIT_LINE in raw else f"社区交流邀请行须为固定文案：{SUBMIT_LINE}")
    # gw-ai-pulse：正文已不含群码时，[grid] 不再是必需结构（其他 grid 用法仍须闭合）
    has_grid = "[grid]" in raw and "[/grid]" in raw
    need_grid = "群二维码_" in raw or "[grid]" in raw
    add_result(results, "格式", has_grid or not need_grid,
               "grid 标记闭合" if has_grid
               else ("正文未使用 grid（gw-ai-pulse 起无群码，属标准形态）" if not need_grid
                     else "二维码 grid 标记不完整"))

    # 技术交流群二维码门禁（V3.8.4 引入 / V3.8.5 抽出为 check_qr_gate 并修正识别缺陷）：
    # 码数必须 5、显示尺寸统一 400x400、本地引用文件须存在。详见模块上方 check_qr_gate 注释。
    md_dir = Path(md_path).parent.resolve() if Path(md_path).parent != Path(".") else Path(".").resolve()
    check_qr_gate(raw, md_dir, results)
    check_tail_order(raw, results, report_date)

    if cover_path:
        cover_ok = os.path.isfile(cover_path) and os.path.basename(cover_path) in raw
        cover_message = "封面文件存在且正文已引用" if cover_ok else "封面文件缺失或正文未引用"
        if cover_ok:
            cover_size = os.path.getsize(cover_path)
            if cover_size < MIN_COVER_BYTES:
                cover_ok = False
                cover_message = (
                    f"封面仅 {cover_size // 1024} KB（低于 {MIN_COVER_BYTES // 1024} KB 阈值），"
                    "疑似程序化文字卡片；须使用 ImageGen 1536x1024 风电场主题封面或复用合规历史封面"
                )
    else:
        cover_ok = FALLBACK_COVER in raw
        cover_message = "已使用无封面引用语降级方案" if cover_ok else "未提供封面，也未使用规定的引用语降级方案"
    add_result(results, "资源", cover_ok, cover_message)

    try:
        semantic = json.loads(Path(semantic_path).read_text(encoding="utf-8"))
    except Exception as exc:
        semantic = {}
        add_result(results, "语义复核", False, f"语义复核记录无法读取：{exc}")

    # V4.3：V4 版式无深度聚焦板块，deep_focus 改为可选（历史期次如含该节点仍按原规则校验）
    semantic_schema_fields = ("overall", "recent_dedup_checked", "items")
    semantic_schema_ok = bool(semantic) and validate_minimal_schema(semantic, semantic_schema_fields)
    add_result(results, "语义复核", semantic_schema_ok,
               "语义复核最小 schema 完整" if semantic_schema_ok else "语义复核为空或缺少必填字段")

    if semantic_schema_ok:
        semantic_dates_ok = date_fields_valid(semantic)
        add_result(results, "语义复核", semantic_dates_ok,
                   "语义复核顶层日期字段可解析" if semantic_dates_ok else "语义复核含无效日期字段")
        overall_ok = semantic.get("overall") == "PASS"
        add_result(results, "语义复核", overall_ok,
                   "语义复核总体状态为 PASS" if overall_ok else "语义复核总体状态不是 PASS")
        dedup_attested = semantic.get("recent_dedup_checked") is True
        add_result(results, "语义复核", dedup_attested,
                   "已确认人工语义级跨期去重" if dedup_attested else "未确认人工语义级跨期去重")
        semantic_items = {normalize_title(item.get("title", "")): item for item in semantic.get("items", [])}
        for item in all_items:
            review = semantic_items.get(normalize_title(item["title"]))
            if not review:
                add_result(results, "语义复核", False, f"《{item['title']}》缺少二次复核记录")
                continue
            source_urls = {normalize_url(url) for url in review.get("source_urls", [])}
            body_urls = {normalize_url(url) for url in item["urls"]}
            source_urls_match = source_urls == body_urls
            review_ok = (
                review.get("status") == "PASS"
                and bool(review.get("facts_checked"))
                and date_fields_valid(review)
                and source_urls_match
                and (
                    (review.get("independent_sources") is True and len(source_urls) >= 2)
                    or (review.get("single_official") is True and len(source_urls) == 1)
                )
            )
            add_result(results, "语义复核", review_ok,
                       f"《{item['title']}》关键事实已二次复核且来源与正文严格一致" if review_ok else f"《{item['title']}》二次复核记录不完整、未通过或 source_urls 与正文来源集合不严格相等")
            validate_evidence_receipts(
                results,
                f"《{item['title']}》",
                review.get("evidence_receipts"),
                body_urls,
                require_evidence_receipts,
            )
        deep_review = semantic.get("deep_focus") or {}
        deep_review_urls = {normalize_url(url) for url in deep_review.get("source_urls", [])}
        deep_receipt_sources = [
            source
            for receipt in (deep_review.get("evidence_receipts") or [])
            for source in receipt.get("sources", [])
        ]
        deep_receipts_all_t0 = all(source.get("tier") == "T0" for source in deep_receipt_sources)
        deep_ok = (
            deep_review.get("status") == "PASS"
            and bool(deep_review.get("facts_checked"))
            and date_fields_valid(deep_review)
            and len(deep_review_urls) >= 2
            and deep_review.get("official_sources_only") is True
            and deep_review.get("link_status_verified") is True
            and deep_review_urls == deep_urls
            and deep_receipts_all_t0
        )
        if deep_urls:
            add_result(results, "语义复核", deep_ok,
                       "深度聚焦关键事实已二次复核，正文与复核记录均仅含已验证官方原始链接（含媒体域名黑名单与回执 T0 硬校验）" if deep_ok else "深度聚焦复核未通过：须有事实清单、至少2个官方原始链接、链接可访问声明、正文链接与复核记录一致，且回执来源全部为 T0（官方一手）")
            validate_evidence_receipts(
                results,
                "深度聚焦",
                deep_review.get("evidence_receipts"),
                deep_urls,
                require_evidence_receipts,
            )
        else:
            add_result(results, "语义复核", True,
                       "V4 版式无独立深度聚焦板块，深焦硬门禁不适用（深度判断并入「今日要紧」编辑判断行）")

    return results


FALLBACK_COVER = "> AI 雷达日报 · 每日洞察 AI 赋能风电"


def write_report(report_path, md_path, results, report_date=None, ruleset_version=RULESET_VERSION):
    status = "PASS" if all(item["passed"] for item in results) else "FAIL"
    source_hash = file_sha256(md_path)
    passed_count = sum(item["passed"] for item in results)
    failed = [item for item in results if not item["passed"]]

    lines = [
        f"SELF_CHECK_STATUS: {status}",
        f"REPORT_DATE: {report_date.isoformat() if isinstance(report_date, date) else report_date or ''}",
        f"RULESET_VERSION: {ruleset_version}",
        f"SOURCE_SHA256: {source_hash}",
        f"SOURCE_FILE: {os.path.abspath(md_path)}",
        f"GENERATED_AT: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        f"# AI 雷达日报发布前自查报告",
        "",
        f"- 总体结论：**{'通过，允许发布' if status == 'PASS' else '未通过，禁止发布'}**",
        f"- 检查结果：{passed_count} 项通过，{len(failed)} 项未通过",
        "",
        "## 未通过项",
        "",
    ]
    if failed:
        lines.extend(f"- [{item['category']}] {item['message']}" for item in failed)
    else:
        lines.append("- 无")

    lines.extend(["", "## 全部检查明细", ""])
    for item in results:
        mark = "PASS" if item["passed"] else "FAIL"
        lines.append(f"- [{mark}] [{item['category']}] {item['message']}")

    Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return status, failed


def main():
    parser = argparse.ArgumentParser(description="AI 雷达日报发布前质量门禁（V3 版式）")
    parser.add_argument("md_path")
    parser.add_argument("--semantic-review", required=True)
    parser.add_argument("--search-log", required=True, help="当期检索清单与召回复核 JSON")
    parser.add_argument("--cover")
    parser.add_argument("--qr1", required=True)
    parser.add_argument("--qr2", required=True)
    parser.add_argument("--qr3", required=True)
    parser.add_argument("--recent-days", type=int, default=2)
    parser.add_argument("--recent-data", help="测试或离线复核时使用的近期日报JSON")
    parser.add_argument("--mode", choices=("normal", "backfill", "revise", "resume"), default="normal")
    parser.add_argument("--allow-same-day", action="store_true",
                        help="仅 revise 模式允许标题事件日期等于日报日期")
    parser.add_argument("--report-date", help="刊发日期 YYYY-MM-DD；默认从文件名读取")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--ruleset-version", default=RULESET_VERSION)
    parser.add_argument("--require-evidence-receipts", action="store_true",
                        help="兼容参数；V3.2 下始终要求每条语义复核及深度聚焦提供 evidence_receipts")
    parser.add_argument("--report")
    args = parser.parse_args()

    report_path = args.report or str(Path(args.md_path).with_name(Path(args.md_path).stem + "_自查报告.md"))
    results = run_checks(
        args.md_path,
        args.semantic_review,
        args.search_log,
        cover_path=args.cover,
        qr1_path=args.qr1,
        qr2_path=args.qr2,
        qr3_path=args.qr3,
        recent_days=args.recent_days,
        recent_data_path=args.recent_data,
        allow_same_day=args.allow_same_day,
        report_date=args.report_date,
        timezone=args.timezone,
        require_evidence_receipts=args.require_evidence_receipts,
        mode=args.mode,
        ruleset_version=args.ruleset_version,
    )
    raw = Path(args.md_path).read_text(encoding="utf-8")
    resolved_date, _, _ = resolve_report_date(args.md_path, raw, args.report_date, args.timezone)
    status, failed = write_report(report_path, args.md_path, results, report_date=resolved_date)
    print(f"自查状态：{status}")
    print(f"自查报告：{report_path}")
    if failed:
        for item in failed:
            print(f"  [FAIL] [{item['category']}] {item['message']}")
        sys.exit(1)
    print("全部检查通过，允许进入发布流程。")


if __name__ == "__main__":
    main()
