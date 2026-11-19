"""把指定话题改写成「今日焦点」长文（试运行 / 正式应用）。

用法：
    ../.venv/bin/python scripts/focus_pilot.py 866           # 只生成并打印，不写论坛
    ../.venv/bin/python scripts/focus_pilot.py 866 --apply   # 写回话题正文 + 加 focus 标签 + 刷新 632

存在的意义：新上线的焦点链路第一次跑真实模型时，需要一个人能先读到成稿，
所以 dry-run 是默认行为；--apply 才真正动论坛（只改这一篇 + 刷新底座帖，不发新帖）。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collector import focus                                    # noqa: E402
from collector.config import load_config                       # noqa: E402
from collector.discourse import Discourse                      # noqa: E402
from collector.models import SourceRef                          # noqa: E402
from collector.pipeline import update_feed                     # noqa: E402
from collector.render import focus_topic_tags, topic_body_focus  # noqa: E402
from collector.sources.fetchers import CST                      # noqa: E402
from collector.store import Store                               # noqa: E402
from collector.tagger import LLMClient                          # noqa: E402


def _opt(name: str) -> str:
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return ""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    topic_id = int(sys.argv[1])
    apply = "--apply" in sys.argv

    cfg = load_config(ROOT)
    store = Store(ROOT / "state" / "index.sqlite")
    forum = Discourse(cfg)
    llm = LLMClient(cfg)
    fc = cfg.settings.get("focus") or {}

    since = (datetime.now(CST) - timedelta(days=7)).isoformat()
    sig = next((x for x in store.recent_signals(since, limit=2000) if x.topic_id == topic_id), None)
    if sig is None:
        print(f"本地库里没有话题 {topic_id} 对应的信号；先 rebuild-index 或换一个话题。")
        return 1
    if sig.post_id is None:
        sig.post_id = forum.topic(topic_id)["post_stream"]["posts"][0]["id"]
    # --source-url/--source-name：给这条信号补一条来源（例如新华网正文是 JS 渲染的，
    # 但同一条事件在能源局官网有静态原文）。真实链路里这一步由「合并更新」完成。
    extra = _opt("--source-url")
    if extra and all(s.url != extra for s in sig.sources):
        sig.sources.append(SourceRef(name=_opt("--source-name") or "官方原文",
                                     url=extra, tier="official", reachable=True))
        print(f"补充来源：{sig.sources[-1].name} {extra}")
    print(f"信号：{sig.title}")
    print(f"来源：{[(s.name, s.url) for s in sig.sources]}")

    print("\n① 抓正文（按来源顺序试，首个抓到正文的来源会排到首位）")
    text, used = focus.fetch_signal_body(sig, cfg, store=store)
    if text is None:
        print("   ✗ 所有来源都抓不到正文（可能是 JS 渲染页），这条不能做焦点。")
        return 1
    print(f"   ✓ 用「{used.name}」抓到正文 {len(text)} 字：{used.url}")
    print("   ---- 正文前 300 字 ----")
    print("   " + text[:300].replace("\n", "\n   "))

    print("\n② 抽事实（模型 #1）")
    facts = focus.extract_facts(sig, text, llm, cfg)
    claims, numbers = focus.verify_claims(facts, text)
    raw_claims = len(facts.get("claims") or [])
    print(f"   模型给了 {raw_claims} 条，逐字校验通过 {len(claims)} 条、数字 {len(numbers)} 个")
    for c in claims:
        print(f"   · {c['text']}")

    degraded = len(claims) < int(fc.get("claims_min", 4))
    print("\n③ 成稿（模型 #2）")
    art = focus.write_article(sig, claims, numbers, str(facts.get("what_changed") or ""), llm, cfg)
    art.claims = claims
    print(f"\n导语：{art.deck}")
    print(f"\n发生了什么：{art.sections['what']}")
    print("\n关键数据：" + "；".join(art.sections.get('data') or []))
    print(f"\n对金风意味着什么：{art.sections.get('impact')}")
    print(f"\n还有什么不确定：{art.sections.get('open')}")
    print(f"\n要点：{' · '.join(art.points)}")
    print(f"\n正文 {art.chars} 字，阅读约 {art.minutes} 分钟，{'简讯版' if degraded else '标准'}")

    if not apply:
        print("\n（dry-run：没动论坛。确认后加 --apply 写回话题并刷新底座帖）")
        return 0

    focus.mark_focus(sig, art, degraded)
    body = topic_body_focus(sig, cfg)
    forum.update_post(sig.post_id, body, "焦点长文改写")
    forum.update_tags(topic_id, focus_topic_tags(sig, cfg))
    store.save_signal(sig, [sig.signal_id])
    focus.remember(store, sig.event_key, datetime.now(CST))
    print(f"\n④ 已写回话题 {topic_id}（正文 {len(body)} 字符），标签 {focus_topic_tags(sig, cfg)}")

    n = update_feed(cfg, forum, store, datetime.now(CST))
    print(f"⑤ 底座帖已刷新：radar {n} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
