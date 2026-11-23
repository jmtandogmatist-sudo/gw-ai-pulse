"""命令行入口：python -m brief <check-config | run | publish>"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from .composer import compose_brief
from .config import BriefConfig, get_secret, load_config
from .harvester import CST, Lead, harvest, load_recent_history
from .publisher import DiscoursePublisher
from .render import build_home_card, render_topic_markdown, topic_title
from .verifier import enrich_cn_mirrors, verify_leads


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def cmd_check_config(cfg: BriefConfig) -> int:
    print(f"根目录：{cfg.root}")
    print(f"发布目标：{cfg.discourse.get('base_url')} | 分类 {cfg.discourse.get('category_id')} | 底座帖 {cfg.discourse.get('feed_topic_id')}")
    print(f"检索配置：{cfg.search.get('provider')} · {len(cfg.search.get('queries') or [])} 组查询 · depth={cfg.search.get('search_depth')}")
    p = cfg.llm.get("primary") or {}
    fb = cfg.llm.get("fallback") or {}
    print(f"成稿引擎：主 {p.get('provider')}/{p.get('model')} · 备 {fb.get('provider')}/{fb.get('model')}")
    missing = []
    for name in (
        cfg.discourse.get("api_key_secret", "discourse-api-key"),
        cfg.search.get("api_key_secret", "tavily-api-key"),
        p.get("api_key_secret", "gemini-api-key"),
        fb.get("api_key_secret", "llm-api-key"),
    ):
        try:
            val = get_secret(name)
            print(f"  [OK] 密钥 {name}（长度 {len(val)}）")
        except Exception as e:  # noqa: BLE001
            print(f"  [MISSING] 密钥 {name}：{e}")
            missing.append(name)
    return 1 if missing else 0


def cmd_run(args, cfg: BriefConfig) -> int:
    now = datetime.now(CST)
    rdate = date.fromisoformat(args.date) if args.date else now.date()

    hist_days = int(cfg.verifier.get("history_dedup_days", 2))
    used_urls, covered_topics = load_recent_history(cfg.root, rdate, days=hist_days)
    if used_urls:
        logging.info("〇 跨期去重加载：近 %d 期已引用 %d 条 URL、%d 个已报选题",
                     hist_days, len(used_urls), len(covered_topics))

    logging.info("① 捕手检索开始（%s）...", rdate.isoformat())
    leads, hmeta = harvest(cfg, now, exclude_urls=used_urls)
    logging.info("① 捕手完成：Tavily %d + 快轨 %d（跨期剔重 %d）→ 去重后 %d 条",
                 hmeta["tavily_raw"], hmeta["collector_raw"], hmeta["history_skipped"], hmeta["deduped"])

    logging.info("② 信源硬审计开始（T0-T4 分级 + 并发 URL 活性检验）...")
    verified, vmeta = verify_leads(leads, cfg, rdate)
    logging.info("② 审计完成：保留 %d 条（剔除超旧 %d、死链 %d、标追踪 %d）| 层级 %s",
                 vmeta["verified_count"], vmeta["stale_dropped"], vmeta["dead_dropped"],
                 vmeta["tracking_marked"], vmeta["by_tier"])
    if not verified:
        logging.error("无有效可引用素材，中止生成")
        return 1

    logging.info("③ 专家成稿开始（主 %s，备 %s）...",
                 (cfg.llm.get("primary") or {}).get("model"),
                 (cfg.llm.get("fallback") or {}).get("model"))
    brief, audit = compose_brief(verified, rdate, cfg, covered_topics=covered_topics)
    logging.info("③ 成稿完成：引擎=%s | 引用素材 %d 条 | 拦截未知编号 %d 处",
                 audit["engine"], audit["used_sources"], len(audit["unknown_source_ids_removed"]))

    lead_by_id = {ld.id: ld for ld in verified}
    cn_stats = enrich_cn_mirrors(brief, lead_by_id, cfg)
    logging.info("③b 中文直连镜像补全：查询 %d 条、命中 %d 条", cn_stats["queried"], cn_stats["matched"])
    audit["cn_mirrors"] = cn_stats
    title = topic_title(rdate, brief)
    md = render_topic_markdown(rdate, brief, lead_by_id)
    card = build_home_card(rdate, brief, base_url=cfg.discourse.get("base_url", ""))

    out_dir = cfg.root / "output" / rdate.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = {
        "date": rdate.isoformat(),
        "title": title,
        "brief": brief,
        "leads": [ld.to_dict() for ld in verified],
        "home_card": card,
        "stats": {"harvest": hmeta, "verify": vmeta, "audit": audit},
    }
    (out_dir / "brief.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "brief.md").write_text(f"# {title}\n\n{md}\n", encoding="utf-8")
    logging.info("草稿已写入：%s", out_dir)

    if not args.confirm:
        print(f"[DRY-RUN] 未发布论坛。请检查草稿：{out_dir / 'brief.md'}")
        return 0

    return _publish_bundle(bundle, md, cfg)


def _publish_bundle(bundle: dict, md: str, cfg: BriefConfig) -> int:
    rdate = date.fromisoformat(bundle["date"])
    pub = DiscoursePublisher(cfg)
    ext_id = f"daily-brief-{rdate.isoformat()}"
    res = pub.create_brief_topic(bundle["title"], md, external_id=ext_id)
    tid = res["topic_id"]
    logging.info("④ 发帖成功：话题 %d（post %d）", tid, res["post_id"])
    card = build_home_card(rdate, bundle["brief"], topic_id=tid, base_url=cfg.discourse.get("base_url", ""))
    fres = pub.update_feed_daily_brief(card, reason=f"更新每日深度简报 {rdate.isoformat()}")
    logging.info("④ 底座帖 632 已更新 daily_brief（正文 %d 字符，radar %d 条）", fres["chars"], fres["radar_count"])
    print(f"[PUBLISHED] {cfg.discourse['base_url'].rstrip('/')}/t/{tid}")
    return 0


def cmd_publish(args, cfg: BriefConfig) -> int:
    p = Path(args.draft)
    bundle = json.loads(p.read_text(encoding="utf-8"))
    rdate = date.fromisoformat(bundle["date"])
    lead_by_id = {d["id"]: Lead(**d) for d in bundle["leads"]}
    md = render_topic_markdown(rdate, bundle["brief"], lead_by_id)
    if not args.confirm:
        print(f"[PLAN] 将发布：{bundle['title']}（加 --confirm 正式写入论坛与 632）")
        return 0
    return _publish_bundle(bundle, md, cfg)


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    ap = argparse.ArgumentParser(description="02-深度简报 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-config", help="检查配置与钥匙串密钥")

    p_run = sub.add_parser("run", help="抓取 → 审计 → 成稿（默认试运行，加 --confirm 正式发布）")
    p_run.add_argument("--date", help="简报日期 YYYY-MM-DD（默认今天）")
    p_run.add_argument("--confirm", action="store_true", help="正式发帖到分类 15 并写入 632.daily_brief")

    p_pub = sub.add_parser("publish", help="发布已有草稿 brief.json")
    p_pub.add_argument("--draft", required=True, help="brief.json 路径")
    p_pub.add_argument("--confirm", action="store_true", help="确认正式写入论坛与 632")

    args = ap.parse_args(argv)
    cfg = load_config()
    if args.cmd == "check-config":
        return cmd_check_config(cfg)
    if args.cmd == "run":
        return cmd_run(args, cfg)
    if args.cmd == "publish":
        return cmd_publish(args, cfg)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
