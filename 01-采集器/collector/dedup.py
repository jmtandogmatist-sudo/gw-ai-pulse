"""去重：URL 规范化、signal_id、event_key 校验、屏蔽域名。"""
from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = re.compile(r"^(utm_.*|spm|from|source|share.*|fbclid|gclid|ref|ref_src)$", re.I)

# 逐日编号式的键会和往期撞号（日报 5.0.0 实跑问题 #2），一律拒绝
GENERIC_KEY = re.compile(r"^(signal|track|item|news|radar|curated|pick)[-_]?\d+$", re.I)
VALID_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{8,96}$")


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+$", "", parts.path) or "/"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not TRACKING_PARAMS.match(k)))
    return urlunsplit((scheme, host, path, query, ""))


def signal_id(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:16]


def normalize_event_key(key: str) -> str:
    """把模型输出的键收敛成规范形式：小写、去首尾空白、点号/下划线/空格转连字符、压缩重复连字符。

    模型写版本号时常带小数点（如 baidu-ernie-5.5-flash），旧实现直接判不合格进「待人工」；
    这里先归一化再校验，既保住「只存规范键」的不变式，又不因标点差异丢掉可用的条目。

    gw-ai-pulse（2026-11-17）：补中文与非法字符的处理。
    模型有时把中文实体名直接拼进键里，例如
        huawei-麒麟-9050-ai-audio-video-2026-11-16
    VALID_KEY 只允许 [a-z0-9-]，这种键 100% 判不合格 → 进 pending → 没有任何出口，
    等于这条信号永久丢失。而「只存规范键」的不变式本身是对的（键要跨轮稳定、可比较），
    所以正确做法不是放宽校验，而是**在归一化阶段就把非 ASCII 消掉**：
      · 中文字符串整体替换为 fixed-width 的 hash 片段，保证同名中文映射到同一个键
        （不能直接删——删掉后 huawei--9050 与 huawei-9050 会撞，且不同中文实体全被抹平）
      · 其余非法标点按连字符处理，最后压缩重复连字符
    这样「规范键」不变式不变，中文实体名也不会再丢条目。
    """
    key = (key or "").strip().lower()
    # 先把每一段连续的非 ASCII 字符替换成稳定短 hash（同段同值，跨轮可复现）
    key = re.sub(r"[^\x00-\x7f]+", lambda m: _cjk_token(m.group(0)), key)
    key = re.sub(r"[^a-z0-9]+", "-", key)
    key = re.sub(r"-{2,}", "-", key).strip("-")
    return key


def _cjk_token(chunk: str) -> str:
    """把一段非 ASCII 文本换成短且稳定的 token；纯标点返回空。"""
    if not re.search(r"[^\W\d_]", chunk, re.UNICODE):
        return ""
    return "x" + hashlib.sha1(chunk.encode("utf-8")).hexdigest()[:10]


def check_event_key(key: str) -> str | None:
    """合格返回 None，不合格返回原因。"""
    if not key:
        return "event_key 为空"
    if GENERIC_KEY.match(key):
        return f"event_key {key} 是编号式的键，会与往期撞号"
    if not VALID_KEY.match(key):
        return f"event_key {key} 格式不对：只允许小写字母、数字和连字符，9–97 个字符"
    return None


def host_of(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def is_blocked(url: str, blocked: list[str]) -> bool:
    host = host_of(url)
    return any(host == d or host.endswith("." + d) for d in blocked)


# ---------- 标题指纹去重（2026-11-16） ----------
#
# 为什么需要：URL 去重挡得住「同一个链接」，挡不住「同一件事的不同链接」。
# 转载站、镜像站、不同媒体各发一篇，URL 全不一样，但内容是同一件事。
# 事件合并（event_key）也靠不住——prompt 要求 key 带日期，换一天再报同一件事
# 的 key 就不同了，跨轮重复因此漏判。
#
# 实测校准（2026-11-16，30 条真实信号）：最高相似度 0.50，0.75 阈值不会误杀。
# 构造用例：同标题微调 1.000、去公司名 0.969、换主体 0.750、中英混用 0.884（加别名后）。

# 中英/缩写别名：让「GWEC 发布宣言」和「全球风能理事会发布宣言」能对上
_TITLE_ALIAS = {
    "gwec": "全球风能理事会",
    "sgre": "西门子歌美飒",
    "vestas": "维斯塔斯",
    "nordex": "恩德",
    "llm": "大模型",
    "api": "接口",
    "ai": "人工智能",
}

# 标题尾部的「|11.16」「· 11月16日」这类日期，不参与比对
_DATE_SUFFIX = re.compile(r"\s*[|·]\s*\d{1,2}[.\-月]\d{1,2}日?\s*$")


def normalize_title(title: str) -> str:
    """标题归一化：去日期后缀 → 转小写 → 展开中英别名 → 只留字母数字与汉字。"""
    t = _DATE_SUFFIX.sub("", title or "").strip().lower()
    for k, v in _TITLE_ALIAS.items():
        t = t.replace(k, v)
    return re.sub(r"[^\w\u4e00-\u9fff]", "", t)


def title_similarity(a: str, b: str) -> float:
    """两个标题的相似度 0~1。两边归一化后为空则返回 0。"""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # 短标题（<4 字）用 SequenceMatcher 容易误判，直接判不相似
    if len(na) < 4 or len(nb) < 4:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def find_duplicate(title: str, candidates: list[str], *, threshold: float = 0.75) -> tuple[str | None, float]:
    """在已有标题里找最相似的。返回 (命中的标题, 相似度)；没有超过阈值的返回 (None, 最高分)。"""
    best_title, best_sim = None, 0.0
    for c in candidates:
        sim = title_similarity(title, c)
        if sim > best_sim:
            best_title, best_sim = c, sim
    if best_sim >= threshold:
        return best_title, best_sim
    return None, best_sim
