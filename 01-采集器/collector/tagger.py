"""打标签与写摘要：调用模型接口（OpenAI 兼容的 chat/completions），结果逐项按白名单校验。

可信度不在这里算（见 credibility.py）。模型只负责：是否保留、领域、问题、主题、相关度、
中文标题、摘要、「对金风」一句、event_key、事件日期。
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date

import requests

from .config import Config, get_secret
from .dedup import check_event_key, normalize_event_key
from .models import Candidate

# gw-ai-pulse（2026-11-16）：模型推断的 event_date 与信源发布日期允许的最大偏差。
# 超过即认定为编造，改用信源日期。留 1 天容纳「晚上发布、次日早晨采集」的跨日。
_EVENT_DATE_TOLERANCE_DAYS = 1

# 2026-11-19：网关从内网 IP 换到 aicopilot.goldwind.com.cn:3213（同一个 CloudWAF 后面）后，
# 新增了一种失败模式——WAF 对突发请求回 418/429，而不是「模型算错了」。
# 这类失败是瞬时的，睡一会儿重试就能过；但如果整轮都在被拦，重试只会把一轮从 5 分钟拖成 20 分钟。
# 所以：单次调用最多补 2 次（8s、16s），连续 3 次调用都被限流就放弃重试、直接失败，
# 让整轮尽快落到 fatal，而不是拖着看门狗（max_seconds=1200）兜底。
_RATE_STATUS = (418, 429)
_RATE_RETRIES = 2
_RATE_BACKOFF = 8.0
_RATE_STREAK_ABORT = 3
log = logging.getLogger("collector.tagger")

SYSTEM_PROMPT = """你是金风科技集团 AI 情报站的信号编辑。你会收到一批从公开网站抓取的条目，逐条判断并输出 JSON。

金风科技是风电整机与新能源企业。情报站围绕下面这些「集团持续关心的问题」采集信号：
{questions}

对每一条输出一个对象，字段如下：
- idx：输入里的序号
- keep：这条是否值得进情报站。与上述问题都无关、纯营销软文、旧闻重发、招聘、活动预告，keep=false
- domain：从这些领域里选一个：{domains}
- questions：它回答了哪些问题，填问题编号数组，可为空数组
- topics：从这些主题标签里选 0–2 个，没有合适的就空数组：{topics}
- relevance：对金风的相关度，0–3 的整数。3=直接影响集团业务或决策；2=值得相关团队知道；1=行业背景；0=基本无关
- title：中文标题，一句话说清发生了什么，{min_title}–40 个字（论坛要求标题至少 {min_title} 个字），不加书名号以外的修饰
- summary：中文摘要，不超过 3 句、120 字，只写原文里有的事实，不推测、不夸大
- us：一句话说明「为什么值得关注」，不超过 50 字。**这个话题对所有人可见（含未登录访客），按公开可读来写**：
  只写这件事对风电与新能源业务的影响点、值得关注什么（外部视角：成本、技术路线、政策义务、风险）；
  不写集团内部动作（要核查什么、安排谁跟进、申报什么试点）、不写内部团队或项目名、不写未公开的经营打算；
  **主语不要写「集团」「金风」「我们」「我方」，改写「风电企业」「新能源业务」**（帖子是公开的，
  匿名访客不该看到一家公司的内部视角；写行业视角，读者自然会代入）；
  写成一句可以原样发布的话；说不清影响就留空字符串。
- event_key：按内容生成的事件键，格式「主体-动作-日期」，小写拼音或英文加连字符，例如 deepseek-series-b-2026-11-04；不要用序号
- event_date：事件发生日期 YYYY-MM-DD（北京时间）。原文与发布日期都有就用它们；**两者都没有就留空字符串 ""**，绝不许编造或填 2026-01-01 之类的占位日期
- **噪声降级**：股价/市值涨跌、高管人事变动、财报数字、融资传闻、招聘、活动预告、纯观点评论，一律 relevance=0（行情与人事不构成情报）

**输出条数必须与输入条数完全一致**：输入 N 条，就返回 N 个对象，idx 从 0 到 N-1 连续不缺。
不保留的条目也要返回对象并写 keep=false，**不要省略、不要只返回保留的那些、不要返回空数组**。
（2026-11-16 实测：漏写条目会让同批次其余结果一起丢失，整批作废。）

只输出一个 JSON 数组，不要任何解释。"""

# gw-ai-pulse（2026-11-19）：来源白名单的补判提示词（方案 C 最小版）。
# 第一遍判定被 keep=false 的一手来源条目，用这段规则再问一次：模型只需补字段，
# 不再重新权衡「要不要」。是不是真该保留，由 keep_bypass.sources 这份人工白名单负责。
FORCE_KEEP_RULE = """

**本批是来源白名单补判**：下面每一条都来自集团指定的一手来源（官方发布或监管原文），
默认 keep=true，请照常补齐 domain/questions/topics/relevance/title/summary/us/event_key/event_date。
只有明显是招聘、活动预告、纯营销软文时，才给 keep=false。"""


@dataclass
class TagResult:
    keep: bool
    domain: str = ""
    questions: list[str] = None
    topics: list[str] = None
    relevance: int = 0
    title: str = ""
    summary: str = ""
    us: str = ""
    event_key: str = ""
    event_date: str | None = None
    error: str | None = None   # 校验不通过的原因；非空表示进「待人工」


class LLMClient:
    def __init__(self, cfg: Config):
        s = cfg.settings["llm"]
        self.base_url = s["base_url"].rstrip("/")
        self.model = s["model"]
        self.timeout = s.get("timeout_seconds", 90)
        self.key_name = s.get("api_key_secret", "llm-api-key")
        self.calls = 0
        self.rate_streak = 0      # 连续被限流的调用次数，见 _RATE_STREAK_ABORT

    def chat(self, system: str, user: str, timeout: float | None = None,
             max_tokens: int | None = None) -> str:
        """一次模型调用。

        timeout / max_tokens 供「今日焦点」这类长输出场景单独放宽（见 collector/focus.py）：
        默认 45 秒是给 8 条批量判定的，写 600–1000 字中文更慢，用同一档会频繁超时报废整篇稿。
        """
        self.calls += 1
        payload = {"model": self.model, "temperature": 0.1,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {get_secret(self.key_name)}", "Content-Type": "application/json"}
        for attempt in range(_RATE_RETRIES + 1):
            r = requests.post(url, headers=headers, json=payload, timeout=timeout or self.timeout)
            if r.status_code in _RATE_STATUS:
                self.rate_streak += 1
                if self.rate_streak >= _RATE_STREAK_ABORT or attempt == _RATE_RETRIES:
                    log.error("模型接口连续 %d 次被限流（HTTP %s），停止重试", self.rate_streak, r.status_code)
                    r.raise_for_status()
                wait = _RATE_BACKOFF * (attempt + 1)
                log.warning("模型接口返回 HTTP %s（第 %d 次），%.0f 秒后重试", r.status_code, attempt + 1, wait)
                time.sleep(wait)
                continue
            self.rate_streak = 0
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]


def build_system_prompt(cfg: Config, force_keep: bool = False) -> str:
    qs = "\n".join(f"{q['id']} {q['text']}" for q in cfg.signal_questions)
    base = SYSTEM_PROMPT.format(questions=qs, domains="、".join(cfg.domains),
                                topics="、".join(cfg.topic_tags) or "（暂无）",
                                min_title=cfg.settings["discourse"].get("min_title_chars", 15))
    return base + (FORCE_KEEP_RULE if force_keep else "")


def build_user_prompt(batch: list[Candidate]) -> str:
    lines = []
    for i, c in enumerate(batch):
        lines.append(json.dumps({"idx": i, "source": c.source_name, "title": c.title,
                                 "excerpt": c.excerpt[:500], "published_at": c.published_at},
                                ensure_ascii=False))
    return "\n".join(lines)


def parse_json_array(text: str) -> list:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        raise ValueError("模型输出里没有 JSON 数组")
    return json.loads(text[start:end + 1])


def validate_item(obj: dict, cfg: Config, cand: Candidate | None = None) -> TagResult:
    """校验并规整模型输出。cand 用于把 event_date 与信源发布日期对账（可为 None）。"""
    keep = bool(obj.get("keep"))
    if not keep:
        return TagResult(keep=False)
    errs = []
    domain = obj.get("domain", "")
    if domain not in cfg.domains:
        errs.append(f"领域「{domain}」不在白名单")
    questions = [q.strip().upper() for q in (obj.get("questions") or []) if isinstance(q, str)]
    bad_q = [q for q in questions if q not in cfg.question_ids]
    if bad_q:
        errs.append(f"问题编号 {bad_q} 不存在")
    topics = [t for t in (obj.get("topics") or []) if t in cfg.topic_tags][:2]
    try:
        relevance = int(obj.get("relevance", 0))
    except (TypeError, ValueError):
        relevance = -1
    if not 0 <= relevance <= 3:
        errs.append("相关度不在 0–3")
    title = (obj.get("title") or "").strip()
    summary = (obj.get("summary") or "").strip()
    us = (obj.get("us") or "").strip()
    min_title = cfg.settings["discourse"].get("min_title_chars", 15)
    if len(title) < min_title or len(title) > 60:
        errs.append(f"标题长度 {len(title)} 字，应在 {min_title}–60 字之间")
    if not summary or len(summary) > 240:
        errs.append("摘要为空或过长")
    if len(us) > 100:
        errs.append("「对金风」过长")
    if re.search(r"\[(?:TODO|待补|占位)|\{\{|XXX", title + summary + us):
        errs.append("含占位符")
    event_key = normalize_event_key(obj.get("event_key"))
    key_err = check_event_key(event_key)
    if key_err:
        errs.append(key_err)
    event_date = obj.get("event_date")
    if event_date and not re.match(r"^20\d{2}-\d{2}-\d{2}$", str(event_date)):
        event_date = None
    # gw-ai-pulse（2026-11-13）：模型在「原文无发布日期」时会编 2026-01-01 占位日期，
    # 旧实现只校验格式，编的日期完美通过（UAT 实测 18 条里 4 条中招）。
    # 这里把 1 月 1 日视为占位符丢弃——真实事件恰好发生在元旦的概率远低于误填。
    if event_date and str(event_date).endswith("-01-01"):
        event_date = None
    if event_date == "":
        event_date = None  # 空串统一成 None，避免下游 `is not None` 判空失手
    # gw-ai-pulse（2026-11-16）：event_date 与**可信日期**做一致性校验。
    # 可信日期 = 信源的 pubDate（已由 pubdate.fill_published_at 补齐 URL 日期戳）。
    # 模型推断的事件日与已发布日不可能相差超过一天；差得远说明模型在编，
    # 此时以信源日期为准。生产实例：网信办 10-30 的公告被模型标成 11-16。
    pub = getattr(cand, "published_at", None) if cand is not None else None
    if event_date and pub:
        try:
            d_event = date.fromisoformat(str(event_date))
            d_pub = date.fromisoformat(str(pub)[:10])
            if abs((d_event - d_pub).days) > _EVENT_DATE_TOLERANCE_DAYS:
                event_date = d_pub.isoformat()
        except ValueError:
            pass
    # 模型没给 event_date 但有信源日期：直接用信源日期，别留空影响下游排序
    if not event_date and pub:
        try:
            event_date = date.fromisoformat(str(pub)[:10]).isoformat()
        except ValueError:
            pass
    return TagResult(
        keep=True, domain=domain, questions=[q for q in questions if q in cfg.question_ids], topics=topics,
        relevance=max(0, min(3, relevance)), title=title, summary=summary, us=us,
        event_key=event_key, event_date=event_date,
        error="；".join(errs) or None,
    )


def _tag_batch(batch: list[Candidate], system: str, cfg: Config, client) -> dict[int, dict]:
    """调一次模型，返回 {批内 index: 结果对象}。

    2026-11-16：模型对「明显不值得保留」的条目（如未经证实的阴谋论、Show HN 自建项目）
    会退化成只返回 `[]`，而不是按格式返回 keep=false 的对象。实测同一条重复 5 次稳定返回 `[]`，
    纯文本追问则明确回答「不值得」——说明这是**判定意图的正确表达，只是格式不合规**。
    因此把 `[]` 解释为「整批都不保留」，交给 validate_item 走 keep=false 分支，
    而不是当成「模型漏掉了这一条」丢进 pending 人工队列。
    """
    arr = parse_json_array(client.chat(system, build_user_prompt(batch)))
    if arr:
        return {int(o.get("idx", -1)): o for o in arr if isinstance(o, dict)}
    # 批次里有同标题条目时模型更容易整批返回 []：拆成单条重送，保住其余有效条目。
    # 只有「拆开后仍然全部返回 []」才认定整批都不保留。
    keys = [re.sub(r"\s+", "", (c.title or "")).lower() for c in batch]
    if len(keys) != len(set(keys)):
        merged: dict[int, dict] = {}
        for i, item in enumerate(batch):
            one = parse_json_array(client.chat(system, build_user_prompt([item])))
            if one and isinstance(one[0], dict):
                merged[i] = {**one[0], "idx": i}
            else:
                merged[i] = {"idx": i, "keep": False}
        return merged
    return {i: {"idx": i, "keep": False} for i in range(len(batch))}


def tag_candidates(cands: list[Candidate], cfg: Config, client,
                   force_keep: bool = False) -> list[TagResult]:
    """返回与 cands 一一对应的结果。整批调用失败时，该批每条都标 error。

    模型的已知失败模式（2026-11-06 实测）：一个批次里**静默漏掉若干条**，只返回其余部分，
    不报错也不标 keep=false。batch_size=8 时 20 条里能漏 12 条。原实现把漏掉的直接判
    「模型漏掉了这一条」进待人工，等于白丢信号。

    这里改为：先整批调用；若发现漏条，对**缺失的那些**逐个重试（单条成批，实测最可靠）。
    仍拿不到才落到「模型漏掉了这一条」。这样既不牺牲正常批量吞吐，又能救回绝大多数漏条。
    """
    size = cfg.settings["llm"].get("batch_size", 8)
    system = build_system_prompt(cfg, force_keep=force_keep)
    results: list[TagResult] = []
    for i in range(0, len(cands), size):
        batch = cands[i:i + size]
        try:
            by_idx = _tag_batch(batch, system, cfg, client)
        except ValueError as e:
            # 2026-11-19：只在「模型输出格式坏了」时整批重试一次。
            # 实测这类失败与内容无关（JSON 少逗号 → "Expecting ',' delimiter"），
            # 归因那一轮 247 条里因此进待人工 8 条（3.2%），重试一次基本必过。
            # **网络/超时/鉴权类失败不重试**：模型整体挂掉时重试只会把一轮时间翻倍
            # （tests/test_resilience.py::test_model_failure_is_counted_not_pending 守着这条）。
            log.warning("模型输出格式坏了（%s），整批重试一次", e)
            try:
                by_idx = _tag_batch(batch, system, cfg, client)
            except Exception as e2:  # noqa: BLE001
                results.extend(TagResult(keep=True, error=f"模型调用失败：{e2}") for _ in batch)
                continue
        except Exception as e:  # noqa: BLE001 —— 模型或网络异常只影响这一批
            results.extend(TagResult(keep=True, error=f"模型调用失败：{e}") for _ in batch)
            continue
        missing = [j for j in range(len(batch)) if j not in by_idx]
        for j in missing:
            try:
                retry = _tag_batch([batch[j]], system, cfg, client)
            except Exception:  # noqa: BLE001 —— 单条重试失败就放弃，不影响其余
                retry = {}
            # 单条成批时模型返回的 idx 是 0
            if 0 in retry:
                by_idx[j] = retry[0]
        for j in range(len(batch)):
            o = by_idx.get(j)
            results.append(validate_item(o, cfg, batch[j]) if o else TagResult(keep=True, error="模型漏掉了这一条"))
    return results
