"""命令行入口：python -m collector <命令>

  run            跑一轮采集（launchd 定时调用）
  run --dry-run  试运行：真实抓取，发帖写到模拟论坛，结果存到 logs/dryrun/
  selftest       第 1 周关卡自检（T07、T08）：读分类、external_id 去重、发帖读回、底座帖可读
  check-config   检查配置，列出需要在论坛上建好的标签
  status         最近运行、待人工条数、连续失败的信源
  weekly-report  上周运行周报，发到飞书
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from .alerts import Alerter
from .config import coverage_summary, coverage_warnings, load_config, validate_config
from .discourse import Discourse, DiscourseError, FakeDiscourse
from .fakes import FakeLLM
from .pipeline import run_round
from .render import read_feed
from .sources.fetchers import CST
from .store import Store
from .tagger import LLMClient


def setup_logging(root: Path) -> None:
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    today = datetime.now(CST).strftime("%Y%m%d")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(logs / f"collector-{today}.log", encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )
    cutoff = time.time() - 30 * 86400
    for f in logs.glob("collector-*.log"):
        if f.stat().st_mtime < cutoff:
            f.unlink()


def _acquire_run_lock(cfg):
    """单实例锁：上一轮还没跑完就不启动新一轮。拿不到锁返回 None。

    为什么需要（2026-11-17 实测）：一次手工轮次卡在网络上 17 分钟还没结束，
    而 launchd 到点会照常再起一个进程。两轮同时跑，各自对底座帖做
    读-改-写，后写覆盖先写，先写那一轮的读回校验就会报「雷达条数不一致」，
    表现成假故障 + 告警。flock 随进程退出自动释放，不会留下僵尸锁。
    只用非阻塞抢锁，不做超时强抢——真卡死的进程应该被人看见，而不是被悄悄跳过。
    """
    path = Path(cfg.root) / "state" / "run.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def cmd_run(args, cfg) -> int:
    modules = [m.strip() for m in args.modules.split(",")] if args.modules else None

    # 非试运行才抢锁：试运行写的是 dryrun.sqlite 和模拟论坛，不碰线上，允许并行。
    lock = None
    if not args.dry_run:
        lock = _acquire_run_lock(cfg)
        if lock is None:
            logging.warning("上一轮采集仍在运行（state/run.lock 被占用），本轮跳过")
            return 0  # 不算失败：launchd 不该为此告警

    if args.dry_run:
        stamp = datetime.now(CST).strftime("%Y%m%d-%H%M%S")
        dump = cfg.root / "logs" / "dryrun" / stamp
        sample = cfg.root / cfg.settings["feed"].get("sample_file", "tests/fixtures/feed_632.md")
        forum = FakeDiscourse(cfg.settings["feed"]["topic_id"],
                              sample.read_text(encoding="utf-8") if sample.exists() else "```json\n{}\n```",
                              dump_dir=dump)
        store = Store(cfg.root / "state" / "dryrun.sqlite")
    else:
        forum = Discourse(cfg)
        store = Store(cfg.root / "state" / "index.sqlite")

    llm = FakeLLM(cfg) if args.fake_llm else LLMClient(cfg)
    alerter = Alerter(cfg, dry_run=args.dry_run)
    rnd = cfg.settings.get("round") or {}

    # gw-ai-pulse（2026-11-16）容错第一层：launchd 配了 RunAtLoad=true，开机就会执行一次。
    #
    # 难点：RunAtLoad 和 StartCalendarInterval 共用同一套 ProgramArguments，
    # 程序无法直接知道这次是「开机触发」还是「到点触发」。若一律走补跑判断，
    # 到点触发时距上次成功可能才 4 小时（<8h 阈值）→ 会被误跳过，定时采集就废了。
    #
    # 解法：先看「当前时刻是否贴近某个计划时间点」。贴近说明是定时触发，直接跑；
    # 不贴近说明是开机触发，才走补跑判断。
    if args.on_load_check and not modules:
        from .catchup import _just_past_slot, seconds_until_next_run, should_catch_up
        schedule = rnd.get("schedule") or {}
        now = datetime.now(CST)
        # 距下个计划点 < 5 分钟，或距上个计划点 < 15 分钟 → 判定为定时触发
        nxt = seconds_until_next_run(schedule, now)
        scheduled_now = (nxt is not None and nxt < 300) or _just_past_slot(schedule, now, within_minutes=15)
        if scheduled_now:
            logging.info("按计划时刻触发，直接采集（不做补跑判断）")
        else:
            go, why = should_catch_up(
                store.last_success_at(), schedule, now,
                after_hours=float(rnd.get("catchup_after_hours", 8)),
                min_gap_minutes=float(rnd.get("catchup_min_gap_minutes", 90)),
            )
            logging.info("开机补跑检查：%s —— %s", "补跑" if go else "跳过", why)
            if not go:
                return 0

    # 容错第三层：全部信源失败多半是断网，隔一段时间重试，最多 N 次。
    attempts = max(1, int(rnd.get("retry_attempts", 1))) if not args.no_retry else 1
    delay = int(rnd.get("retry_delay_seconds", 900))
    rep = None
    for i in range(attempts):
        rep = run_round(cfg, forum, llm, store, alerter, modules=modules)
        if rep.ok or i == attempts - 1:
            break
        # 只在「网络类全失败」时重试；读回不一致、模型全挂这类不算网络问题
        if not _is_network_failure(rep):
            break
        logging.warning("第 %d/%d 次失败（%s），%d 秒后重试", i + 1, attempts, rep.fatal, delay)
        if not args.dry_run:
            time.sleep(delay)

    logging.info("本轮结果：%s", json.dumps(rep.__dict__, ensure_ascii=False))
    if args.dry_run:
        print(f"试运行结果已写到 {dump}")
    return 0 if rep.ok else 1


def _is_network_failure(rep) -> bool:
    """判断本轮失败是否属于网络类（值得重试），而不是逻辑类（重试也没用）。"""
    fatal = rep.fatal or ""
    if "连不上论坛" in fatal:
        return True
    if "所有信源本轮都抓取失败" in fatal:
        return True
    return False


def cmd_topics(args, cfg) -> int:
    """每周五 17:00（16:00 采集之后）更新首页「05 主题追踪」。

    只做一件事：读本地信号索引 → 按主题分组 → 让模型写 judge/change/next → 写进 632 的 topics 字段。
    主题白名单在 config/topics.yaml；没有配置就什么都不做（首页该块不显示）。
    """
    from .pipeline import update_feed_topics

    tcfg = cfg.topics or {}
    items = tcfg.get("topics") or []
    if not items:
        logging.info("config/topics.yaml 没有主题，跳过")
        return 0
    forum = Discourse(cfg)
    store = Store(cfg.root / "state" / "index.sqlite")
    llm = FakeLLM(cfg) if args.fake_llm else LLMClient(cfg)
    sigs = store.recent_signals(
        (datetime.now(CST) - timedelta(days=int(tcfg.get("timeline_days", 30)))).isoformat(timespec="seconds"),
        limit=5000)
    logging.info("近 30 天信号 %d 条，主题 %d 个", len(sigs), len(items))
    res = update_feed_topics(cfg, forum, sigs, llm)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_selftest(args, cfg) -> int:
    forum = Discourse(cfg)
    s = cfg.settings
    ok = True

    def step(name, fn):
        nonlocal ok
        try:
            msg = fn()
            print(f"[通过] {name}{('：' + msg) if msg else ''}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"[失败] {name}：{e}")

    step("读取信号分类", lambda: forum.category(s["discourse"]["signal_category_id"]).get("name", ""))
    ext = "selftest-" + datetime.now(CST).strftime("%Y%m%d%H%M%S")
    raw = "这是采集器自检帖，请管理员确认后删除。\n\n" + ext
    tags = ["auto"]
    holder = {}

    def first_post():
        holder.update(forum.create_topic(f"采集器自检 {ext}", raw, s["discourse"]["signal_category_id"], tags, ext))
        return f"话题 {holder['topic_id']}"

    def second_post():
        try:
            forum.create_topic(f"采集器自检 {ext} 重复", raw, s["discourse"]["signal_category_id"], tags, ext)
        except DiscourseError as e:
            if e.status == 422:
                return "同一 external_id 的第二次发帖被拒绝"
            raise
        raise RuntimeError("同一 external_id 第二次发帖没有被拒绝：去重的第二道保险不可用，告诉我改方案")

    def readback():
        back = forum.post_raw(holder["post_id"])
        t = forum.topic(holder["topic_id"])
        if back.strip() != raw.strip():
            raise RuntimeError("读回正文与发出的不一致")
        if not set(tags) <= set(t.get("tags", [])):
            raise RuntimeError(f"标签没打上：读回 {t.get('tags')}，检查标签权限")
        found = forum.topic_by_external_id(ext)
        if not found or found["id"] != holder["topic_id"]:
            raise RuntimeError("按 external_id 查不到话题")
        return "正文、标签、external_id 查询都一致"

    def feed_readable():
        pid = forum.first_post_id(s["feed"]["topic_id"])
        feed, mode = read_feed(forum.post_raw(pid))
        return f"底座帖可读（{mode}，字段 {', '.join(sorted(feed))}）；本次未修改"

    step("第一次发测试帖", first_post)
    if holder:
        step("external_id 去重", second_post)
        step("读回核对", readback)
    step("读取底座帖", feed_readable)
    print("\n全部通过，第 1 周关卡达成。请在后台删除自检帖。" if ok else "\n有失败项，按提示处理后再跑一次。")
    return 0 if ok else 1


def cmd_check_config(args, cfg) -> int:
    problems = validate_config(cfg)
    for p in problems:
        print("[问题]", p)
    # 2026-11-19：覆盖检查。validate_config 只管「配置自相矛盾」（会阻断运行），
    # 这里管「登记了但抓不到」（只提示）——两者混在一起会让一条注释性配置把整轮采集拦掉。
    for w in coverage_warnings(cfg):
        print("[覆盖]", w)
    cov = coverage_summary(cfg)
    print(f"信源 {cov['registered']} 个（启用 {cov['enabled']} 个，本轮实跑 {cov['runnable']} 个），问题 {len(cfg.questions)} 个")
    if cov["not_runnable"]:
        why = "、".join(f"{k} {v}" for k, v in cov["not_runnable"].items())
        print(f"  未实跑原因：{why}")
    # 2026-11-19：把「实际生效的模型接口」打出来。起因是一次误读——配置注释写着地址的来源，
    # 被当成「运行时去读那个文件」。地址是本地写死的值，打在屏幕上比写在注释里可靠。
    llm = cfg.settings.get("llm") or {}
    print(f"模型接口 {llm.get('base_url')} · model={llm.get('model')} · 密钥走钥匙串 {llm.get('api_key_secret')}")
    # 2026-11-19：前置过滤的状态必须在这里可见——它是「哪些行业源条目压根不会送模型」的开关，
    # 藏在 settings 里不打印，出问题时没人想得到是它挡的。
    pf = cfg.settings.get("prefilter") or {}
    pf_mode = str(pf.get("mode") or "off")
    if pf_mode != "off":
        pf_src = [x["name"] for x in cfg.sources if x.get("module") in set(pf.get("modules") or [])]
        print(f"前置过滤 {pf_mode}（作用于 {len(pf_src)} 个行业源、关键词 {len(pf.get('keywords') or [])} 个）"
              + ("，只统计不拦截" if pf_mode == "shadow" else "，未命中即丢弃"))
    print("需要在论坛上建好的标签：")
    print("  " + " ".join(cfg.all_forum_tags()))
    return 1 if problems else 0


def cmd_status(args, cfg) -> int:
    store = Store(cfg.root / "state" / "index.sqlite")
    since = (datetime.now(CST) - timedelta(days=2)).isoformat()
    for r in store.runs_since(since)[-6:]:
        rep = r["report"]
        # 429 / 模型故障单独露出来（2026-11-19）：那一轮出问题时的现场是
        # 「候选 0、发帖 0、无 fatal」，从这行完全看不出是撞了论坛限额还是模型挂了。
        extra = ""
        if rep.get("rate_limited"):
            extra += f"论坛限速 429×{rep['rate_limited']}  "
        if rep.get("forum_requests"):
            extra += f"论坛请求 {rep['forum_requests']} 次  "
        if rep.get("model_failed"):
            extra += f"模型故障 {rep['model_failed']} 条  "
        # 2026-11-19 P1：白名单补判救回来的条数，用来判断这道闸是否真在起作用。
        if rep.get("keep_bypassed"):
            extra += f"白名单补判 {rep['keep_bypassed']} 条  "
        # 2026-11-19：行业源关键词前置过滤（shadow 期间看「将挡多少 / 误伤多少」）
        if rep.get("prefilter_would_drop"):
            extra += (f"前置过滤({rep.get('prefilter_mode') or 'shadow'}) "
                      f"将挡 {rep['prefilter_would_drop']}/误伤 {rep.get('prefilter_model_kept', 0)}  ")
        # 2026-11-19：今日焦点长文是否出稿、多长、事实几条
        if rep.get("focus_posted"):
            extra += (f"焦点 {rep.get('focus_chars', 0)} 字/{rep.get('focus_claims', 0)} 条事实"
                      + ("（简讯）" if rep.get("focus_degraded") else "") + "  ")
        print(f"{r['started_at']}  {'成功' if r['ok'] else '失败'}  发帖 {rep.get('posted', 0)}  候选 {rep.get('candidates', 0)}  {extra}{rep.get('fatal', '')}")
    print(f"累计信号 {store.count_signals()} 条，待人工 {store.pending_count()} 条")
    # 已停用的信源不再抓取，别让它的历史失败记录一直挂在状态里（同 pipeline 里的过滤）
    enabled = {x["name"] for x in cfg.sources if x.get("enabled", True)}
    for name, n, err in store.failing_sources(1):
        if name in enabled:
            print(f"信源连续失败 {n} 轮：{name}  {err[:80]}")
    return 0


def cmd_weekly(args, cfg) -> int:
    store = Store(cfg.root / "state" / "index.sqlite")
    since = (datetime.now(CST) - timedelta(days=7)).isoformat()
    runs = store.runs_since(since)
    total = len(runs)
    ok = sum(1 for r in runs if r["ok"])
    posted = sum(r["report"].get("posted", 0) for r in runs)
    with_q = 0
    recent = store.recent_signals(since, limit=5000)
    for s in recent:
        if s.questions:
            with_q += 1
    by_domain: dict[str, int] = {}
    for s in recent:
        by_domain[s.domain] = by_domain.get(s.domain, 0) + 1
    lines = [
        f"上周运行周报：计划内 {total} 轮，成功 {ok} 轮（{(ok / total * 100 if total else 0):.0f}%）",
        f"新发信号 {posted} 条；带问题标签 {with_q}/{len(recent)}（{(with_q / len(recent) * 100 if recent else 0):.0f}%）",
        "各领域：" + "、".join(f"{k} {v}" for k, v in sorted(by_domain.items(), key=lambda x: -x[1])),
        f"待人工积压 {store.pending_count()} 条",
    ]
    fail = store.failing_sources(1)
    if fail:
        lines.append("仍在失败的信源：" + "、".join(n for n, _, _ in fail))
    text = "\n".join(lines)
    print(text)
    Alerter(cfg, dry_run=args.dry_run).send(text)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m collector")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--fake-llm", action="store_true", help="不调用模型接口，用模拟模型（只用于离线检查流程）")
    r.add_argument("--modules", help="只跑这些模块，逗号分隔；例如 models（每日快照）")
    r.add_argument("--on-load-check", action="store_true",
                   help="开机自检：只在错过时间点时补跑（launchd RunAtLoad 用）")
    r.add_argument("--no-retry", action="store_true", help="网络失败时不重试")
    tp = sub.add_parser("topics")
    tp.add_argument("--fake-llm", action="store_true")
    sub.add_parser("selftest")
    sub.add_parser("check-config")
    sub.add_parser("status")
    w = sub.add_parser("weekly-report")
    w.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    cfg = load_config()
    setup_logging(cfg.root)
    problems = validate_config(cfg)
    live = (args.cmd == "run" and not args.dry_run) or args.cmd == "selftest"
    if live and not cfg.settings["discourse"].get("signal_category_id"):
        problems.append("settings.yaml 的 discourse.signal_category_id 还没填（T05 建好分类后填）")
    if args.cmd == "run" and not args.fake_llm and "REPLACE" in cfg.settings["llm"]["base_url"]:
        problems.append("settings.yaml 的 llm.base_url 还是占位（等 IT 指定模型接口后填，T01）")
    if problems and args.cmd != "check-config":
        for x in problems:
            print("[配置问题]", x)
        return 2
    return {"run": cmd_run, "topics": cmd_topics, "selftest": cmd_selftest,
            "check-config": cmd_check_config, "status": cmd_status,
            "weekly-report": cmd_weekly}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
