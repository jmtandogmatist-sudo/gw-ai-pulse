"""一轮采集：抓取 → 去重 → 初筛 → 打标签与摘要 → 校验 → 发帖 → 读回 → 刷新底座帖 → 报告。"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import Config, select_sources, skipped_sources
from .credibility import compute_cred
from .dedup import find_duplicate, is_blocked, normalize_url, signal_id
from . import focus
from .discourse import DiscourseError
from .models import Candidate, Signal, SourceRef
from .pubdate import fill_published_at
from .render import (BRIEF_MAX, brief_items, energy_items, focus_lead_item,
                     focus_topic_tags, lead_item, mainline_text,
                     pick_energy, today_label,
                     pick_items, public_topic_body, public_topic_tags,
                     public_topic_title, radar_items, radar_rank, read_feed,
                     topic_body, topic_body_focus, topic_tags, write_feed)
from .sources.fetchers import CST, fetch
from .tagger import tag_candidates
from .watchdog import RoundTimeout, arm as arm_watchdog, clear as clear_watchdog

log = logging.getLogger("collector.pipeline")


@dataclass
class RoundReport:
    started_at: str = ""
    modules: list[str] = field(default_factory=list)
    fetched: dict = field(default_factory=dict)        # 信源名 → 条数
    failed_sources: dict = field(default_factory=dict) # 信源名 → 错误
    # 2026-11-19：登记了但本轮没参与抓取的信源 → 原因（disabled / page / module / schedule=xxx）。
    # modules 只能说明「跑了什么」，这张表才能说明「什么没跑」。
    skipped_sources: dict = field(default_factory=dict)
    candidates: int = 0
    duplicates: int = 0
    screened_out: int = 0   # 超 72h 窗口 / 被屏蔽域名 / relevance 低于门槛，三者合计
    undated_dropped: int = 0   # URL/正文都提不出发布日期，按 undated_policy=drop 丢弃
    undated_kept: int = 0      # 提不出日期但按 undated_policy=keep 放行
    title_duplicates: int = 0  # 标题指纹判为重复（同一事件的不同链接）
    dropped_by_model: int = 0
    # 白名单补判救回来的条数（2026-11-19）：第一遍给 keep=false 的一手来源，
    # 用 FORCE_KEEP_RULE 再问一次后保留的条目数。
    keep_bypassed: int = 0
    # 今日焦点（2026-11-19）：本轮是否出了一篇焦点长文，以及没出/降级的原因。
    focus_posted: int = 0
    focus_chars: int = 0
    focus_claims: int = 0
    focus_degraded: int = 0
    focus_skipped: str = ""
    # 过了模型那关、但 relevance 低于 min_relevance_to_post 的条数。
    # 单独计数是为了能回答「相关度闸门到底挡掉了多少」——它现在混在 screened_out 里，
    # 而 screened_out 同时包含超窗口和被屏蔽域名，看不出闸门本身的分量。
    below_min_relevance: int = 0
    pending: int = 0
    # 模型接口故障（超时 / 返回坏 JSON）导致这一批没能判定。
    # 单独计数、**不进 pending**：这些条目下一轮会被重新抓到并按正常流程判定，
    # 记进人工队列只会把队列灌满噪声（2026-11-19：65 条待人工里 48 条是这个原因）。
    model_failed: int = 0
    merged: int = 0
    posted: int = 0
    published_public: int = 0   # 双重验证通过并额外公开的条数
    over_cap: int = 0
    community_scanned: int = 0  # 扫到的社区帖子数
    community_tagged: int = 0   # 本轮新打上主题标签的帖子数
    llm_calls: int = 0
    query_stats: dict = field(default_factory=dict)  # A/B 层检索词库执行统计
    feed_updated: bool = False
    feed_error: str = ""
    # 长期失败的信源（连续失败 ≥ source_fail_alert_after）：名 → {fails, error}
    # 与 failed_sources 的区别：那个只记本轮，这个记「已经烂了很久但没人处理」。
    chronic_sources: dict = field(default_factory=dict)
    forum_requests: int = 0   # 本轮向论坛发出的请求数（限速前统计，用来判断是否会撞限额）
    rate_limited: int = 0     # 本轮收到 429 的次数
    fatal: str = ""
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.fatal


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(CST)).astimezone(CST)


def _normalize_post(text: str) -> str:
    """读回核对用的宽松归一化。

    Discourse 存 raw 时会做字符规范化，最典型的是剥掉全角空格 U+3000
    （2026-11-06 实测：模板里「~~·~~」读回变「 · 」），导致逐字节比较
    永远不相等、每轮都被判「读回不一致」而中止发帖。
    这里把空白类字符统一成单空格再比较，只放宽空白，不放宽实质内容。
    """
    return re.sub(r"\s+", " ", (text or "").replace("\u3000", " ")).strip()


def run_round(cfg: Config, forum, llm, store, alerter, modules: list[str] | None = None,
              now: datetime | None = None) -> RoundReport:
    t0 = _now(now)
    mono = time.monotonic()
    rep = RoundReport(started_at=t0.isoformat(timespec="seconds"))
    run_id = store.start_run()
    s = cfg.settings
    rnd = s["round"]
    # 采集器拿到的论坛客户端：本轮开始时把请求计数清零，报告里只统计本轮
    if hasattr(forum, "reset_counters"):
        forum.reset_counters()
    # 看门狗（2026-11-19 事故）：不管卡在哪一步，超过 max_seconds 就抛异常，
    # 走下面的 finally —— 写 runs 表、发告警、释放单实例锁。见 collector/watchdog.py。
    arm_watchdog(float(rnd.get("max_seconds", 0) or 0))
    try:
        # 0. 先确认论坛能连、分类存在：连不上就整轮失败，不做任何抓取
        try:
            forum.category(s["discourse"]["signal_category_id"])
        except Exception as e:  # noqa: BLE001
            rep.fatal = f"连不上论坛或找不到信号分类：{e}"
            return rep

        # 1. 抓取
        cands: list[Candidate] = []
        # 三关：启用 → 非 page → 属于本轮 schedule。判据集中在 config.select_sources，
        # 与 check-config 的覆盖检查共用同一份，避免「配置说一套、实际跑一套」。
        sources = select_sources(cfg.sources, modules)
        rep.modules = sorted({x["module"] for x in sources})
        rep.skipped_sources = skipped_sources(cfg.sources, modules)
        newly_failing = []
        for src in sources:
            try:
                got = fetch(src, s["discourse"]["base_url"])
                rep.fetched[src["name"]] = len(got)
                cands.extend(got)
                store.mark_source(src["name"], ok=True)
            except Exception as e:  # noqa: BLE001 —— 单个信源失败只记录
                rep.failed_sources[src["name"]] = str(e)[:200]
                fails = store.mark_source(src["name"], ok=False, error=str(e))
                if fails == rnd.get("source_fail_alert_after", 3):
                    newly_failing.append((src["name"], str(e)[:120]))
        # 1b. 检索词库（A/B 层）：订阅抓不到的盲区靠关键词搜索补
        #     2026-11-16：此前 A/B 层检索词只在 Markdown 里，从未被执行
        qcfg = cfg.queries or {}
        if qcfg.get("enabled", True) and (qcfg.get("queries") or []):
            layers = qcfg.get("layers") or ["A", "B"]
            try:
                from .queries import run_queries
                qstats: dict = {}
                found = run_queries(qcfg, now=t0, layers=layers,
                                    limit_per_query=int(qcfg.get("limit_per_query", 20)),
                                    window_hours=int(rnd.get("window_hours", 72)),
                                    stats=qstats)
                cands.extend(found)
                rep.query_stats = qstats
            except Exception as e:  # noqa: BLE001 —— 检索词库失败不阻断订阅抓取
                rep.failed_sources["检索词库(A/B层)"] = str(e)[:200]

        rep.candidates = len(cands)
        # 分阶段留痕（2026-11-19）：原来一轮只在结束时打一行汇总，
        # 卡住的那一轮日志里除了「按计划时刻触发」什么都没有，无法判断卡在哪一步。
        log.info("① 抓取完成：%d 个信源、%d 条候选（失败 %d），用时 %.0fs",
                 len(sources), len(cands), len(rep.failed_sources), time.monotonic() - mono)

        # 2. 去重 + 3. 初筛
        # gw-ai-pulse（2026-11-16）：此前判断写作 `if c.published_at and ... < window`，
        # published_at 为空时整段短路 —— 零日期条目永不受 72h 窗口约束，导致
        # 2026-10-30 的网信办公告（超窗 17 天）被当成新信号发布。现在先从 URL 补日期。
        window = t0 - timedelta(hours=rnd.get("window_hours", 72))
        undated_policy = rnd.get("undated_policy", "drop")   # drop / keep
        seen, fresh = set(), []
        for c in cands:
            uid = signal_id(c.url)
            if uid in seen or store.known_url(uid):
                rep.duplicates += 1
                continue
            seen.add(uid)

            fill_published_at(c, now=t0)                     # URL 日期戳兜底

            if c.published_at:
                if datetime.fromisoformat(c.published_at) < window:
                    rep.screened_out += 1
                    continue
            elif undated_policy == "drop":
                # 提不出日期的条目无法证明新鲜，默认不入池
                rep.undated_dropped += 1
                continue
            else:
                rep.undated_kept += 1

            if is_blocked(c.url, cfg.blocked_domains) and not rnd.get("keep_blocked_links", False):
                rep.screened_out += 1
                continue
            fresh.append(c)

        log.info("② 去重初筛完成：待判定 %d 条（重复 %d、超窗 %d、无日期丢弃 %d），用时 %.0fs",
                 len(fresh), rep.duplicates, rep.screened_out, rep.undated_dropped,
                 time.monotonic() - mono)

        # 4. 打标签与摘要
        results = tag_candidates(fresh, cfg, llm) if fresh else []

        # gw-ai-pulse（2026-11-19）来源白名单补判（方案 C 最小版）：
        # keep 是唯一实质闸门，而它的判据是「与集团问题是否相关」——
        # 官方一手发布往往没有解读，最容易被判掉（今天 OpenAI/Anthropic 20 条只剩 3 条）。
        # 对被判掉的白名单来源再问一次，只让它补字段、不再重新权衡取舍。
        # 开关与名单在 settings.keep_bypass，默认关闭。
        bypass = cfg.settings.get("keep_bypass") or {}
        if results and bypass.get("enabled"):
            names = set(bypass.get("sources") or [])
            retry_idx = [i for i, (c, r) in enumerate(zip(fresh, results))
                         if not r.keep and not r.error and c.source_name in names]
            if retry_idx:
                try:
                    sub = tag_candidates([fresh[i] for i in retry_idx], cfg, llm, force_keep=True)
                except Exception as e:  # noqa: BLE001 —— 补判失败不影响主流程
                    log.warning("白名单补判失败（忽略）：%s", e)
                    sub = []
                for k, i in enumerate(retry_idx):
                    if k < len(sub) and sub[k].keep and not sub[k].error:
                        results[i] = sub[k]
                        rep.keep_bypassed += 1
                if rep.keep_bypassed:
                    log.info("白名单补判：%d 条被 keep=false 的一手来源重新保留", rep.keep_bypassed)
        rep.llm_calls = getattr(llm, "calls", 0)
        if fresh and results and all(r.error and r.error.startswith("模型调用失败") for r in results):
            rep.fatal = "模型接口本轮全部调用失败：" + results[0].error
            return rep
        log.info("③ 模型判定完成：%d 条待判定（调用 %d 次），用时 %.0fs",
                 len(fresh), rep.llm_calls, time.monotonic() - mono)

        # 5. 组装信号、合并同一事件
        new_by_key: dict[str, Signal] = {}
        url_ids: dict[str, list[str]] = {}
        to_update: dict[str, Signal] = {}
        stamp = t0.isoformat(timespec="seconds")
        for c, r in zip(fresh, results):
            if not r.keep:
                rep.dropped_by_model += 1
                continue
            if r.error:
                # 模型接口故障（超时 / 坏 JSON）不等于「这条信号有问题」：
                # 本轮没判定成功，下一轮它还会被重新抓到并按正常流程判定。
                # 记进 pending 只会让人工队列虚假膨胀（2026-11-19：65 条里 48 条是这个），
                # 真正的待人工应该是「需要人看一眼」的（标题疑似重复、发帖失败）。
                if r.error.startswith("模型调用失败"):
                    rep.model_failed += 1
                    continue
                rep.pending += 1
                store.add_pending(r.error, {"url": c.url, "title": c.title, "source": c.source_name})
                continue
            ref = SourceRef(name=c.source_name, url=c.url, tier=c.source_tier,
                            reachable=not is_blocked(c.url, cfg.blocked_domains))
            uid = signal_id(c.url)
            existing = new_by_key.get(r.event_key) or to_update.get(r.event_key)
            if existing is None:
                stored = store.by_event_key(r.event_key)
                merge_days = rnd.get("merge_window_days", 14)
                if stored and datetime.fromisoformat(stored.discovered_at) >= t0 - timedelta(days=merge_days):
                    existing = stored
            if existing:
                if normalize_url(c.url) not in {normalize_url(x.url) for x in existing.sources}:
                    existing.sources.append(ref)
                existing.cred = compute_cred(existing.sources)
                existing.relevance = max(existing.relevance, r.relevance)
                existing.questions = sorted(set(existing.questions) | set(r.questions))
                url_ids.setdefault(existing.signal_id, []).append(uid)
                if existing.topic_id:
                    to_update[r.event_key] = existing
                rep.merged += 1
                continue
            sig = Signal(signal_id=uid, event_key=r.event_key, title=r.title, summary=r.summary, us=r.us,
                         domain=r.domain, questions=r.questions, topics=r.topics, relevance=r.relevance,
                         cred="red", event_date=r.event_date or (c.published_at or "")[:10] or None,
                         # gw-ai-pulse（2026-11-17）：完整发布时间留给排序用（带时分）。
                         # 模型给的 event_date 只有日期；c.published_at 来自 RSS/GitHub，
                         # 通常带时分。两者都拿不到时为 None，排序退回日期粒度。
                         published_at=c.published_at or None,
                         discovered_at=stamp, sources=[ref])
            sig.cred = compute_cred(sig.sources)
            new_by_key[r.event_key] = sig
            url_ids[uid] = [uid]

        # 6. 校验门槛与上限
        min_rel = rnd.get("min_relevance_to_post", 1)
        queue = sorted((x for x in new_by_key.values() if x.relevance >= min_rel),
                       key=lambda x: (-x.relevance, x.title))
        rep.below_min_relevance = len(new_by_key) - len(queue)
        rep.screened_out += rep.below_min_relevance

        # 6b. 标题指纹去重（2026-11-16）
        # URL 去重挡不住「同一事件的不同链接」：转载站、镜像站、各媒体各发一篇，
        # URL 全不一样。event_key 也靠不住——它带日期，换一天再报同一件事 key 就变了。
        # 这里用标题相似度兜底：与近 N 天已发信号比对。
        #   ≥ duplicate_threshold      → 判为重复，不发帖
        #   ≥ ambiguous_threshold      → 记 pending 待人工，不发帖
        #   < ambiguous_threshold      → 正常发帖
        dup_th = float(rnd.get("title_dedup_threshold", 0.75))
        amb_th = float(rnd.get("title_dedup_ambiguous", 0.60))
        dup_days = int(rnd.get("title_dedup_days", 3))
        title_dups = 0
        if dup_th > 0:
            since = (t0 - timedelta(days=dup_days)).isoformat(timespec="seconds")
            known = store.recent_titles(since)
            kept = []
            for sig in queue:
                hit, sim = find_duplicate(sig.title, known, threshold=dup_th)
                if hit is not None:
                    rep.title_duplicates += 1
                    title_dups += 1
                    log.info("标题判重：%s（%.2f 相似于「%s」）", sig.title[:36], sim, hit[:36])
                    continue
                if amb_th > 0 and sim >= amb_th:
                    rep.pending += 1
                    store.add_pending(f"标题疑似重复（相似度 {sim:.2f}）：{hit or ''}",
                                      {"title": sig.title, "url": sig.sources[0].url if sig.sources else ""})
                    continue
                kept.append(sig)
                known.append(sig.title)      # 同一轮内也互相比对，避免同批重复
            queue = kept

        cap = rnd.get("max_posts_per_round", 30)
        rep.over_cap = max(0, len(queue) - cap)
        queue = queue[:cap]
        log.info("④ 过门槛待发帖 %d 条（低于相关度 %d、标题重复 %d、模型故障 %d、待人工 %d），用时 %.0fs",
                 len(queue), rep.below_min_relevance, rep.title_duplicates, rep.model_failed,
                 rep.pending, time.monotonic() - mono)

        # 6d. 今日焦点（2026-11-19「焦点单独成篇」）：本轮最多一篇，选一条做长文。
        #     放在发帖之前：成稿成功那条信号会切到长文模板；失败则本轮不出焦点，
        #     首页 02 沿用上一轮的焦点（settings.focus 规则 a）。
        if queue and (cfg.settings.get("focus") or {}).get("enabled"):
            try:
                fou = focus.build(queue, cfg, llm, store, t0)
            except Exception as e:                  # noqa: BLE001
                # 焦点是附加步骤：它崩了也只能少一篇长文，不能少一轮发帖
                log.exception("焦点步骤异常，已跳过（本轮按原样发帖）：%s", e)
                rep.focus_skipped = f"焦点步骤异常（已跳过）：{type(e).__name__}"
                fou = None
            if fou is not None:
                rep.focus_skipped = fou.reason
            if fou is not None and fou.sig is not None and fou.article is not None:
                rep.focus_chars = fou.article.chars
                rep.focus_claims = len(fou.article.claims)
                rep.focus_degraded = 1 if fou.degraded else 0
                log.info("⑥ 焦点选定：%s（正文 %d 字、可核对事实 %d 条%s），用时 %.0fs",
                         fou.sig.title[:32], fou.article.chars, len(fou.article.claims),
                         "、简讯版" if fou.degraded else "", time.monotonic() - mono)
            elif fou is not None and fou.reason:
                log.info("⑥ 本轮不出焦点：%s", fou.reason)

        # 7. 发帖与读回
        cat_id = s["discourse"]["signal_category_id"]
        for sig in queue:
            if sig.focus:
                raw, tags = topic_body_focus(sig, cfg), focus_topic_tags(sig, cfg)
            else:
                raw, tags = topic_body(sig, cfg), topic_tags(sig, cfg)
            try:
                res = forum.create_topic(sig.title, raw, cat_id, tags, external_id=sig.signal_id)
            except DiscourseError as e:
                if e.status == 422 and "external" in e.message.lower():
                    store.save_signal(sig, url_ids.get(sig.signal_id, [sig.signal_id]))  # 论坛已有，记下即可
                    rep.duplicates += 1
                    continue
                if e.status == 429:
                    log.warning("触发频率限制，剩余 %d 条留到下一轮", len(queue) - rep.posted)
                    break
                rep.pending += 1
                store.add_pending(f"发帖失败：{e}", {"title": sig.title, "url": sig.sources[0].url})
                continue
            sig.topic_id, sig.post_id = res["topic_id"], res["post_id"]
            back = forum.post_raw(sig.post_id)
            back_tags = set(forum.topic(sig.topic_id).get("tags", []))
            if _normalize_post(back) != _normalize_post(raw) or not set(tags) <= back_tags:
                store.save_signal(sig, url_ids.get(sig.signal_id, [sig.signal_id]))
                missing = sorted(set(tags) - back_tags)
                detail = f"缺少标签 {missing}" if missing else "正文与发出的不一致"
                rep.fatal = f"读回不一致，已停止本轮发帖（话题 {sig.topic_id}，{detail}）"
                break
            store.save_signal(sig, url_ids.get(sig.signal_id, [sig.signal_id]))
            rep.posted += 1
            if sig.focus:
                rep.focus_posted = 1
                # 焦点事件键只在真正发出去之后才记（见 collector/focus.py 的说明）
                focus.remember(store, sig.event_key, t0)
            log.info("　已发帖 %d/%d：%s", rep.posted, len(queue), sig.title[:40])

            # 7b. 双重验证 + 重要 → 额外发一份公开版
            if _should_publish_public(sig, s["discourse"]):
                try:
                    pub = forum.create_topic(
                        public_topic_title(sig), public_topic_body(sig, cfg),
                        s["discourse"]["public_category_id"],
                        public_topic_tags(sig, cfg),
                        external_id=f"{sig.signal_id}-pub",
                    )
                    sig.public_topic_id = pub["topic_id"]
                    store.save_signal(sig, url_ids.get(sig.signal_id, [sig.signal_id]))
                    rep.published_public += 1
                except DiscourseError as e:
                    # 公开失败不影响内部帖，记待人工即可
                    log.warning("公开帖创建失败（话题 %s）：%s", sig.topic_id, e)
                    store.add_pending(f"公开帖创建失败：{e}",
                                      {"topic_id": sig.topic_id, "title": sig.title})

        # 已发话题的合并更新
        for sig in to_update.values():
            try:
                forum.update_post(sig.post_id, topic_body(sig, cfg), "合并新来源")
                forum.update_tags(sig.topic_id, topic_tags(sig, cfg))
            except DiscourseError as e:
                store.add_pending(f"合并更新失败：{e}", {"topic_id": sig.topic_id})
            store.save_signal(sig, url_ids.get(sig.signal_id, []))
        # 未过门槛或超出上限的信号不入库：下一轮若仍在时间窗口内会再次出现

        # 7c. 扫社区帖子打主题标签（原地打标签，不复制成信号）
        # gw-ai-pulse（2026-11-16）：人发的帖子也要计入主题档案的「本周 +N」。
        # 只记进 seen_posts，不生成新话题；失败不影响本轮采集。
        ccfg = s.get("community") or {}
        community_posts = []
        if ccfg.get("enabled", False):
            try:
                from .community import scan_forum, tag_posts
                cats = forum.categories()
                # with_authors=False：论坛分类列表接口不返回 posters，逐条补作者
                # 会让请求数从 5 涨到 55（2026-11-19 撞限额的根因）。
                # 发帖人等首页选出要展示的那几条之后再补，见 update_feed。
                community_posts = scan_forum(
                    forum, cats, s["discourse"]["signal_category_id"],
                    exclude_ids=set(ccfg.get("exclude_ids") or []),
                    per_category=int(ccfg.get("per_category", 8)),
                    exclude_tags=ccfg.get("exclude_tags"),
                    with_authors=False,
                )
                rep.community_scanned = len(community_posts)
                log.info("⑥ 社区扫描完成：%d 条帖子，用时 %.0fs", len(community_posts),
                         time.monotonic() - mono)
                if ccfg.get("tag_posts", True):
                    stat = tag_posts(community_posts, cfg, llm, store, now=t0)
                    rep.community_tagged = stat["newly_tagged"]
            except Exception as e:  # noqa: BLE001
                log.warning("社区帖子扫描失败：%s", e)

        # 8. 刷新底座帖
        if s["feed"].get("enabled", False) and not rep.fatal:
            try:
                update_feed(cfg, forum, store, t0, community_posts=community_posts, llm=llm)
                rep.feed_updated = True
                log.info("⑦ 底座帖已刷新，用时 %.0fs", time.monotonic() - mono)
            except Exception as e:  # noqa: BLE001
                rep.feed_error = str(e)[:300]

        if sources and len(rep.failed_sources) == len(sources):
            rep.fatal = rep.fatal or "所有信源本轮都抓取失败"
        if newly_failing:
            alerter.send("以下信源已连续多轮抓取失败：\n" + "\n".join(f"- {n}：{e}" for n, e in newly_failing))
        # gw-ai-pulse（2026-11-17）：补「长期静默失败」检测。
        #
        # 原来的告警只在 fails == source_fail_alert_after（默认 3）那**一轮**触发一次。
        # 机器之心的 RSS 从上线起就是坏的（连续 14 次、last_ok 恒为 None），
        # 但第 3 次那轮正好没人看日志，之后就再没提过——一个主力 AI 媒体
        # 常年缺席，而系统完全「安静」。这比采集失败更危险：失败至少是响的。
        #
        # 现在改成：连续失败达到阈值的信源每轮都汇总进 rep，并在日志里留痕；
        # 只有**新增**进名单的才推送告警，避免每轮重复轰炸。
        # 只报**启用中**的信源：停用的源不再抓取，但 source_health 里那一行还在，
        # 于是「长期失败」名单永远挂着它（2026-11-19 实测：机器之心 11-17 已停用，
        # 之后每轮仍然被列出来告警，因为连续失败次数冻结在 14 再没变过）。
        enabled_names = {x["name"] for x in cfg.sources if x.get("enabled", True)}
        chronic = [x for x in store.failing_sources(int(rnd.get("source_fail_alert_after", 3)))
                   if x[0] in enabled_names]
        if chronic:
            rep.chronic_sources = {n: {"fails": f, "error": e[:120]} for n, f, e in chronic}
            log.warning("长期失败的信号源 %d 个：%s", len(chronic),
                        "、".join(f"{n}({f}次)" for n, f, _ in chronic))
        return rep
    except RoundTimeout as e:      # 看门狗触发：本轮超时，按失败收尾
        rep.fatal = f"本轮超时被中止：{e}"
        log.error("%s（已跑 %.0f 秒），发布失败告警并释放单实例锁", rep.fatal, time.monotonic() - mono)
        return rep
    finally:
        clear_watchdog()
        if hasattr(forum, "requests_made"):
            rep.forum_requests = int(forum.requests_made)
            rep.rate_limited = int(getattr(forum, "rate_limited", 0))
        rep.seconds = round(time.monotonic() - mono, 1)
        if rep.fatal:
            alerter.send(f"本轮采集失败：{rep.fatal}")
        elif rep.feed_error:
            alerter.send(f"底座帖刷新失败：{rep.feed_error}")
        store.finish_run(run_id, rep.ok, rep.__dict__)


def _should_publish_public(sig: Signal, dcfg: dict) -> bool:
    """双重验证 + 重要 → 公开。

    双重验证：cred 达到 public_min_cred（默认 yellow）。
      green  = 官方来源，或官方 + 独立来源互证
      yellow = 官方单方，或 ≥2 个不同 host
      red    = 单一非官方来源 —— 不算验证过，不公开
    重要：relevance ≥ public_min_relevance（默认 3，即「直接影响业务」）。
    """
    if not dcfg.get("publish_public", False):
        return False
    if not dcfg.get("public_category_id"):
        return False
    order = {"green": 2, "yellow": 1, "red": 0}
    floor = order.get(dcfg.get("public_min_cred", "yellow"), 1)
    if order.get(sig.cred, 0) < floor:
        return False
    return sig.relevance >= int(dcfg.get("public_min_relevance", 3))


def _in_publish_window(sig: Signal, since_iso: str) -> bool:
    """发布日期是否在窗口内。日期未知时放行（由 undated_policy 在入池阶段决定）。"""
    day = (sig.event_date or "")[:10]
    if not day:
        return True
    try:
        return day >= since_iso[:10]
    except (TypeError, ValueError):
        return True


def update_feed(cfg: Config, forum, store, now: datetime,
                community_posts: list | None = None, llm=None) -> int:
    f = cfg.settings["feed"]
    post_id = forum.first_post_id(f["topic_id"])
    raw = forum.post_raw(post_id)
    feed, mode = read_feed(raw)
    since = (now - timedelta(hours=f.get("window_hours", 72))).isoformat(timespec="seconds")
    sigs = [x for x in store.recent_signals(since) if x.topic_id]
    # gw-ai-pulse（2026-11-16）：recent_signals 按 discovered_at 过滤，不看发布日期。
    # 于是一条 10-30 的旧公告只要在今天被采到，就能在 radar 上挂满 72 小时，
    # 用户看到的却像是今天的新闻。这里追加一道按**发布日期**的窗口判断：
    # 发布日期已知且早于窗口的，不展示（话题仍在分类 20，只是不上首页）。
    sigs = [x for x in sigs if _in_publish_window(x, since)]
    # gw-ai-pulse（2026-11-17）：排序改为「日分组 > 组内 信息源:相关度=3:2 > 精确时间」。
    # 旧实现是 (-relevance, -discovered_at)，其中 discovered_at 是**采集时间**，
    # 同一轮采集的所有条目共享同一个时间戳，那个第二关键字实际什么也没排，
    # 整个雷达等于只按相关度排序——37 条里 34 条同为 relevance=2 时顺序基本随机。
    # 新规则详见 render.radar_rank 的注释。
    sigs = sorted(sigs, key=lambda x: radar_rank(x, now))
    items = radar_items(sigs[: f.get("max_items", 60)], cfg, cfg.settings["discourse"]["base_url"])
    feed["radar"] = items            # 只改雷达相关字段，其余字段（简报、焦点、精编……）原样保留
    feed["radar_updated_at"] = now.isoformat(timespec="seconds")
    nxt = next_run_at(cfg, now)
    if nxt:
        feed["radar_next_at"] = nxt.isoformat(timespec="seconds")
    feed["radar_total"] = store.count_signals()

    # 02 右栏「金风 / 能源相关」：从同一批已排好序的信号里按集团问题筛。
    # 不筛的话这一块会跟着分类 18 走，和 06 栏目里的「深度阅读」列重复。
    # 单独 try 包住：这一块出问题也只丢「金风/能源相关」，不能让上面刚写好的雷达
    # 跟着一起写不进去（update_feed 整体失败会让底座帖整篇不刷新）。
    # gw-ai-pulse（2026-11-19）：02 焦点单独成篇后，这条信号已经在 02 顶部完整出现，
    # 02 右栏「金风 / 能源相关」不再重复列它（同一屏两次同一件事，读者会以为系统重了）。
    focus_sig = next((x for x in sigs if getattr(x, "focus", False)), None)
    pool_sigs = [x for x in sigs if x is not focus_sig]
    ecfg = f.get("energy") or {}
    if ecfg.get("enabled", True):
        try:
            pool = pick_energy(
                pool_sigs,
                questions=set(ecfg.get("questions") or ["Q01", "Q02", "Q09"]),
                domains=set(ecfg.get("domains") or ["能源AI"]),
                limit=int(ecfg.get("count", 3)),
            )
            if pool:
                feed["energy"] = energy_items(
                    pool, cfg, cfg.settings["discourse"]["base_url"],
                    limit=int(ecfg.get("count", 3)))
            elif "energy" in feed:
                feed.pop("energy", None)   # 这类信号断了就整块不显示，不留旧内容
        except Exception as e:  # noqa: BLE001
            log.warning("金风/能源相关区块生成失败（不影响雷达）：%s", e)
    # 刊头日期每轮跟着刷新（详见 render.today_label 的注释）
    feed["date"] = today_label(now)
    feed["issue_date"] = now.date().isoformat()

    # gw-ai-pulse（2026-11-16）：01 简报 / 02 焦点同步滚动更新。
    # 这两块原先只由日报 skill 在发布时写一次，全天停在早上那一版；采集器每轮重算，
    # 沿用日报的取舍语义（今日要紧 > 金风相关 > 行业速递）。日报当天发布后仍会覆盖，
    # 采集器下一轮再接手——两份程序写同一字段，但都只写「当日」内容，不会互相打架。
    base = cfg.settings["discourse"]["base_url"].rstrip("/")
    # gw-ai-pulse（2026-11-19）：同源限额。IT之家一轮 27 条、一天 3 轮，不限额时
    # 01 的三条、02 的焦点全是它。阈值在 settings.feed，设 0 即回退旧行为。
    brief_per_src = int(f.get("brief_max_per_source", 0) or 0)
    picks_per_src = int(f.get("picks_max_per_source", 0) or 0)
    if f.get("update_brief", True) and sigs:
        # gw-ai-pulse（2026-11-19）：02 焦点单独处理。
        # 有焦点信号时，02 = 焦点长文的入口卡（导语 + 3 条要点，标题链到话题），
        # 01 简报把这条让出去——否则同一页两处讲同一件事，读者会以为系统重复了。
        brief_pool = [x for x in sigs if x is not focus_sig]
        feed["brief"] = brief_items(brief_pool, BRIEF_MAX, now=now, per_source=brief_per_src)
        lead = (focus_lead_item(focus_sig) if focus_sig is not None
                else lead_item(brief_pool, now=now, per_source=brief_per_src))
        if lead:
            tid = lead.pop("topic_id", None)
            lead["url"] = f"{base}/t/{tid}" if tid else ""
            feed["lead"] = lead
        # 页头主线与「今日值得看」原先也由日报写，现在一并由采集器生成
        ml = mainline_text(sigs, now=now, per_source=brief_per_src)
        if ml:
            feed["mainline"] = ml
        picks = pick_items(sigs, now=now, per_source=picks_per_src)
        if picks:
            feed["picks"] = picks
        elif "picks" in feed:
            feed.pop("picks")       # 没有合适的工具类条目就整块不显示，不留旧内容
        feed["brief_updated_at"] = now.isoformat(timespec="seconds")

    # gw-ai-pulse（2026-11-16）：05 社区今日 + 今日一问。
    # 这两块原先由日报编辑手写（编辑推荐 + 编辑提问）。日报停用后改为读论坛真实帖子：
    #   community —— 近 N 天、非信号分类的帖子，按回复数降序取前几条，who = 发帖人
    #   question  —— 由模型根据这些帖子的实际讨论生成，模型不可用时退回规则提问
    ccfg = cfg.settings.get("community") or {}
    if ccfg.get("enabled", False) and community_posts:
        from .community import build_question, pick_community, resolve_authors
        # 先挑出要展示的几条，**再**补发帖人：topic() 是一次完整请求，
        # 对整池 55 条都补会把 admin API key 限额打满（2026-11-19 事故）。
        picked = pick_community(community_posts,
                                limit=int(ccfg.get("community_count", 5)),
                                days=int(ccfg.get("community_days", 14)),
                                order=str(ccfg.get("community_order", "new")), now=now)
        if picked and forum is not None:
            resolve_authors(forum, picked)      # 最多 community_count 次请求
        # 变量名不能用 items：上面 radar 用它算条数，下面读回校验还要比它。
        # （2026-11-16 实测踩过：这里一覆盖，读回校验就变成「雷达 29 条 vs 社区 5 条」，
        #  每轮都误报「底座帖读回后雷达条数不一致」，底座帖其实写对了。）
        community = [p.to_feed() for p in picked]
        if community:
            feed["community"] = community
        else:
            feed.pop("community", None)      # 近期没人发帖就整块不显示，不留旧内容
        if llm is not None:
            q = build_question(community_posts, llm, limit=int(ccfg.get("question_limit", 8)))
            if q:
                feed["question"] = q
        feed["community_updated_at"] = now.isoformat(timespec="seconds")

    feed["question_list"] = [{"id": q["id"], "short": q.get("short", ""), "text": q["text"]} for q in cfg.questions]

    # gw-ai-pulse（2026-11-16）：日报停用，清理它留下的字段。
    # 采集器本来就不写这些，只是原样保留；不主动删的话旧内容会一直挂在首页上。
    #   curated      03 今日精编       —— 与 radar 内容重叠，整块取消
    #   tracking     03 底部「本周跟踪」 —— 日报产物
    #   daily_url    「去日报下回复」按钮指向 —— 日报没了，链接失效
    #   next_update_at 「下一轮更新」时间 —— 由日报更新节奏定义，不再适用
    for stale in ("curated", "tracking", "daily_url", "next_update_at"):
        feed.pop(stale, None)
    # gw-ai-pulse（2026-11-16）：updated_at 原先只有日报写，日报停用后它永远停在
    # 最后一期日报的时间（09:28），而内容其实已经刷新过多次——刊头显示的「X 更新」
    # 与事实不符。这里让它跟着每轮刷新走，与 brief_updated_at 同源。
    feed["updated_at"] = now.isoformat(timespec="seconds")
    new_raw = write_feed(raw, feed, mode)
    forum.update_post(post_id, new_raw, "采集器刷新简报与信号雷达")
    log.info("底座帖写入完成：radar %d 条、正文 %d 字符",
             len(feed.get("radar") or []), len(new_raw))
    check, _ = read_feed(forum.post_raw(post_id))
    # gw-ai-pulse（2026-11-17）：比对基准必须是 write_feed **实际写下去的**条数，
    # 不能是收缩前的 items。write_feed 在超预算时会把 feed["radar"] 二分收缩
    # （见 render._fit_feed），拿原始 items 比就会误判成「读回不一致」——
    # 2026-11-17 15:40 实际踩到：收缩 40→37，写是成功的，却报了失败并触发告警。
    written = len(feed.get("radar") or [])
    if len(check.get("radar", [])) != written:
        raise RuntimeError(f"底座帖读回后雷达条数不一致（写入 {written}，读回 {len(check.get('radar', []))}）")
    return written


def dossier_ids(cfg: Config) -> dict:
    """主题标签 → 主题档案帖 id（state/topic_dossiers.json，由 backfill_dossiers.py 回填）。

    为什么放在 state 而不是 config/topics.yaml：运行期只读 01-采集器/ 下的代码与配置，
    建帖回填属于运行期写操作，落 state/ 才不会破坏这条规矩。
    """
    path = cfg.root / "state" / "topic_dossiers.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for k, v in (data or {}).items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def _norm_body(s: str) -> str:
    """读回比对：只归一空白（Discourse 会把全角空格 U+3000 压成半角），其余逐字比。"""
    return re.sub(r"[\s\u3000]+", " ", s or "").strip()


def _state_path(cfg: Config):
    return cfg.root / "state" / "topic_dossiers.json"


def save_dossier_ids(cfg: Config, ids: dict) -> None:
    path = _state_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ids, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _cst_day(iso: str):
    """论坛 created_at 是 UTC（...Z），换成北京时间再取日期，避免跨零点错一天。"""
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(CST).date()
    except (TypeError, ValueError):
        return None


def extra_tag_signals(cfg: Config, forum, items: list[dict], signals: list[Signal],
                      now: datetime, tcfg: dict) -> list[Signal]:
    """把论坛上带「额外标签」的帖子转成主题素材（2026-11-19）。

    为什么需要：主题「集团 AI 能力上新」的真正内容在社区——同事发帖时打
    「集团ai能力上新 / ai智能体开发平台 / 版本更新 …」，而采集器的信号索引里
    只有它自己抓的 RSS，标签是 topic-internal，于是这个主题长期显示「本周无新信号」。
    这里按标签回读论坛，把人工帖并进素材；分类 20（信号）排除，避免和本地索引重复。

    规则：标签不存在（404）→ 跳过；帖子时间按 created_at（北京时间）落在 timeline 窗口内才算。
    """
    base = cfg.settings["discourse"]["base_url"].rstrip("/")
    try:
        signal_cat = int(cfg.settings["discourse"].get("signal_category_id") or 0)
    except (TypeError, ValueError):
        signal_cat = 0
    days = int(tcfg.get("timeline_days", 30))
    floor = (now - timedelta(days=days - 1)).date()
    seen_topic_ids = {s.topic_id for s in signals if s.topic_id}
    out: list[Signal] = []
    for tp in items:
        for tag in tp.get("extra_tags") or []:
            try:
                rows = forum.tag_topics(tag)
            except DiscourseError as e:      # 网络/权限问题不该让整轮周更失败
                log.warning("读论坛标签 %s 失败（%s），跳过", tag, e)
                continue
            kept = 0
            for t in rows:
                tid = t.get("id")
                if not tid or tid in seen_topic_ids:
                    continue
                if signal_cat and int(t.get("category_id") or 0) == signal_cat:
                    continue
                day = _cst_day(t.get("created_at"))
                if day is None or day < floor:
                    continue
                seen_topic_ids.add(tid)
                kept += 1
                out.append(Signal(
                    signal_id=f"forum-{tid}", event_key=f"forum-{tid}",
                    title=str(t.get("title") or ""), summary="", us="", domain="内部",
                    questions=list(tp.get("questions") or []), topics=[tp["name"]],
                    relevance=2, cred="", review="human",
                    event_date=day.isoformat(),
                    discovered_at=str(t.get("created_at") or ""),
                    sources=[SourceRef(name="社区", url=f"{base}/t/{tid}", tier="community")],
                    topic_id=tid,
                ))
            if kept:
                log.info("主题「%s」按标签 %s 补入 %d 条社区帖", tp.get("name"), tag, kept)
    return out


def update_dossiers(cfg: Config, forum, materials, judgments: dict, now: datetime,
                    tcfg: dict) -> list[dict]:
    """每周把主题档案帖与 632 同步更新（2026-11-19）。

    与 632 同一份 judgments：档案里的当前判断/本周变化/观察点必须和首页卡片逐字一致。
    有 id 就更新、没有就建帖（external_id=dossier-<tag> 防重复），新 id 落 state。
    """
    from .topics import render_dossier, parse_machine

    base = cfg.settings["discourse"]["base_url"].rstrip("/")
    cat = int(tcfg.get("dossier_category_id", 15))
    tags = list(tcfg.get("dossier_tags") or ["dossier"])
    timeline_limit = int(tcfg.get("timeline_limit", 20))
    history_limit = int(tcfg.get("history_limit", 12))
    week_end = now.date().isoformat()
    ids = dossier_ids(cfg)
    results: list[dict] = []
    for m in materials:
        j = judgments.get(m.name)
        if not j:
            results.append({"name": m.name, "status": "SKIP_NO_JUDGMENT"})
            continue
        tid = int(m.dossier_topic_id or ids.get(m.tag) or 0)
        old_raw, history = "", []
        if tid:
            try:
                old_raw = forum.post_raw(forum.first_post_id(tid))
                history = parse_machine(old_raw).get("history") or []
            except DiscourseError:
                old_raw, tid = "", 0          # 帖被删了：当作没有，重新建
        history = [{"date": week_end, "judge": j["judge"].strip()}] + \
                  [h for h in history if h.get("date") != week_end]
        body = render_dossier(m, j, week_end, base, history,
                              timeline_limit=timeline_limit, history_limit=history_limit)
        try:
            if tid and old_raw:
                if _norm_body(old_raw) == _norm_body(body):
                    status = "NOOP"
                else:
                    forum.update_post(forum.first_post_id(tid), body, f"主题档案 {week_end} 周更")
                    status = "UPDATED"
            elif tid:
                forum.update_post(forum.first_post_id(tid), body, f"主题档案 {week_end} 周更")
                status = "UPDATED"
            else:
                title = f"主题档案｜{m.name}"
                res = forum.create_topic(title, body, cat, tags, f"dossier-{m.tag}")
                tid = int(res["topic_id"])
                status = "CREATED"
                ids[m.tag] = tid
                save_dossier_ids(cfg, ids)
            m.dossier_topic_id = tid
            back = forum.post_raw(forum.first_post_id(tid))
            if _norm_body(back) != _norm_body(body):
                status = "READBACK_MISMATCH"
            results.append({"name": m.name, "topic_id": tid, "status": status})
        except Exception as e:  # noqa: BLE001
            log.warning("主题档案「%s」更新失败：%s", m.name, e)
            results.append({"name": m.name, "topic_id": tid, "status": f"ERROR: {e}"})
    return results


def update_feed_topics(cfg: Config, forum, signals, llm, now: datetime | None = None) -> dict:
    """刷新 632 的 topics 字段（首页 05 主题追踪）。每周五 17:00 由 launchd 调用。

    为什么放在采集器而不是保留 skill：skill 的 judge/change/next 是编辑手写的，日报去掉后
    没人写字了。这里让模型写，校验规则照抄 skill（长度上限、必须引用本周信号、
    无信号只能写「本周无新信号。」），保证这道人工关卡的实质——具体、可证伪——不变。
    """
    from .topics import collect_material, feed_topics, write_judgments

    now = _now(now)
    tcfg = cfg.topics or {}
    items = tcfg.get("topics") or []
    if not items:
        return {"topics": 0, "reason": "config/topics.yaml 没有主题"}
    # 2026-11-19：主题可配置 extra_tags（论坛标签）。「集团 AI 能力上新」靠它
    # 收社区里的人工上新帖——这些帖子不在采集器的信号索引里。
    extra = extra_tag_signals(cfg, forum, items, list(signals), now, tcfg)
    if extra:
        signals = list(signals) + extra
    materials = collect_material(
        signals, items, now=now,
        lookback_days=int(tcfg.get("lookback_days", 7)),
        timeline_days=int(tcfg.get("timeline_days", 30)),
    )
    # gw-ai-pulse（2026-11-19）：主题档案帖的 id 存在 state/topic_dossiers.json
    # （由 scripts/backfill_dossiers.py 建帖后回填）。不读这份映射的话，weekly 任务
    # 每次都会把 topics[].url 覆盖回「最新一条信号帖」——刚建好的档案链接每周五失效一次。
    ids = dossier_ids(cfg)
    for m in materials:
        if not m.dossier_topic_id and ids.get(m.tag):
            m.dossier_topic_id = ids[m.tag]
    judgments = write_judgments(materials, llm)
    # 2026-11-19：档案帖与 632 用同一份 judgments，一起写。关掉开关就回到
    # 「只更新首页字段、url 退回信号帖」的旧行为（可回滚）。
    dossier_results = []
    if (tcfg.get("dossiers") or {}).get("enabled", True):
        dossier_results = update_dossiers(cfg, forum, materials, judgments, now, tcfg)
    topics = feed_topics(materials, judgments, base_url=cfg.settings["discourse"]["base_url"],
                         max_on_home=int(tcfg.get("max_topics_on_home", 5)))

    f = cfg.settings["feed"]
    post_id = forum.first_post_id(f["topic_id"])
    raw = forum.post_raw(post_id)
    feed, mode = read_feed(raw)
    old = feed.get("topics") or []
    feed["topics"] = topics
    feed["topics_updated_at"] = now.isoformat(timespec="seconds")
    forum.update_post(post_id, write_feed(raw, feed, mode), "主题档案：每周判断更新")
    check, _ = read_feed(forum.post_raw(post_id))
    if len(check.get("topics") or []) != len(topics):
        raise RuntimeError("底座帖读回后主题条数不一致")
    return {"topics": len(topics), "previous": len(old),
            "week": {t["name"]: t["week"] for t in topics}, "updated_at": feed["topics_updated_at"],
            "extra_posts": len(extra), "dossiers": dossier_results}


def next_run_at(cfg: Config, now: datetime) -> datetime | None:
    """按 settings.round.schedule 算下一轮采集时间（北京时间）。"""
    sch = cfg.settings["round"].get("schedule") or {}
    now = now.astimezone(CST)
    for add in range(0, 8):
        day = (now + timedelta(days=add)).date()
        times = sch.get("weekends" if day.weekday() >= 5 else "weekdays") or []
        for t in sorted(times):
            hh, mm = (int(x) for x in t.split(":"))
            cand = datetime(day.year, day.month, day.day, hh, mm, tzinfo=CST)
            if cand > now:
                return cand
    return None
