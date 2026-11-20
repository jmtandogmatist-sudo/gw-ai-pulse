"""标题与摘要的确定性规整。

为什么需要（2026-11-20 生产实例 t/866）：
    模型给的
        title   国家能源局发布《中国虚拟电厂发展报告（2026）》
        summary 国家能源局发布了《中国虚拟电厂发展报告（2026）》。该报告由官方渠道新华网能源频道报道。
    01「今天发生了什么」按「标题 + 摘要首句」机械拼接，于是首页出现
        …（报告）》：国家能源局发布了《…（报告）》。
    同一件事被说了两遍，读者一眼就觉得标题坏了。

这里只做两件确定性的事，不调模型：
    1. 判断某句摘要是不是标题的复述（is_restatement）；
    2. 丢掉摘要开头那些复述标题的句子（drop_restating_lead）。
「标题与原文体裁不符」（把「解读」写成「发布」）不是靠字面比较能修的，交给提示词。
"""
from __future__ import annotations

import difflib
import re

# 比较前去掉的字符：标点、空白、书名号/引号等装饰。留下纯文字再比。
_NOISE = re.compile(r"[\s\u3000《》〈〉「」『』“”‘’\"'·、，,。.：:；;！!？?（）()\[\]【】—\-…~～/／|｜]")

# 首句与标题相似到这个程度就算复述。0.68 是拿生产数据调的：
#   「国家能源局发布《中国虚拟电厂发展报告（2026）》」vs 其摘要首句 → 0.97（该丢）
#   「Qwen相关事件显示自托管AI模型需更强控制」vs「Dark Reading报道指出…」→ 0.31（该留）
_RESTATE_RATIO = 0.68
# 一个是另一个的子串时，短的那个至少要占长的 60%，否则「某厂发布」这种短标题
# 会被任意含这四个字的摘要误伤。
_CONTAIN_RATIO = 0.6

_SENT_SPLIT = re.compile(r"(?<=[。！？!?])")


def _norm(text: str) -> str:
    return _NOISE.sub("", (text or "")).lower()


def is_restatement(title: str, sentence: str, threshold: float = _RESTATE_RATIO) -> bool:
    """这句摘要说的是不是「把标题再说一遍」。"""
    a, b = _norm(title), _norm(sentence)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 8 and len(b) >= 8 and (a in b or b in a):
        short, long = (a, b) if len(a) <= len(b) else (b, a)
        if len(short) / len(long) >= _CONTAIN_RATIO:
            return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split((text or "").strip()) if s.strip()]


def drop_restating_lead(title: str, summary: str, *, keep_if_all: bool = True) -> str:
    """去掉摘要开头复述标题的句子。

    keep_if_all=True 时，如果整段摘要都在复述标题，就原样返回——
    宁可留着让渲染层做去重，也不要把 summary 清空（下游会判「摘要为空」丢信号）。
    """
    sentences = split_sentences(summary)
    if not sentences:
        return ""
    rest = list(sentences)
    while rest and is_restatement(title, rest[0]):
        rest.pop(0)
    if not rest:
        return summary if keep_if_all else ""
    return "".join(rest)


def flatten_book_marks(title: str) -> str:
    """把嵌套的书名号压平：内层《》换成「」，避免出现 《…《…》…》。

    只处理一层嵌套——真实标题里没出现过更深的情况，做得太聪明反而会改坏原文标题。
    """
    text = title or ""
    outer = text.find("《")
    if outer < 0:
        return text
    inner = text.find("《", outer + 1)
    if inner < 0:
        return text
    # 内层的结束号：从内层开始找第一个 》
    inner_end = text.find("》", inner + 1)
    if inner_end < 0:
        return text
    return text[:inner] + "「" + text[inner + 1:inner_end] + "」" + text[inner_end + 1:]
