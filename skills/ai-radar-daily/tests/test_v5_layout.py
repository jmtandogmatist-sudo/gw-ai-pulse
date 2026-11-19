#!/usr/bin/env python3
"""V5 三层版式：结构门禁、检索清单绑定、V4 适配与下游（自查 / 预检 / 推送 / 去重）。"""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import fetch_recent  # noqa: E402
import seen_index  # noqa: E402,F401
import layout_v5 as L  # noqa: E402
import preflight_check  # noqa: E402
import prepare_feishu_push  # noqa: E402
import self_check  # noqa: E402

D = date(2026, 10, 30)  # 周五，窗口 {10-29, 10-30}


def chips(sec, cred, frm=None):
    emo = {"g": "🟢 官方 + 独立", "y": "🟡 当事方 / 媒体", "r": "🔴 单一来源"}[cred]
    s = f'<span data-air-chip="sec">{sec}</span> <span data-air-chip="cred" data-cred="{cred}">{emo}</span>'
    if frm:
        s += f' <span data-air-chip="from">{frm}</span>'
    return s


def card(sec, cred, title, body, urls, us=None):
    out = [f"[wrap=air-card cred={cred}]", chips(sec, cred), "", f"#### {title}", "", body, ""]
    if us:
        out += ["[wrap=air-us]", f"**对我们：** {us}", "[/wrap]", ""]
    out += ["[wrap=air-src]", " · ".join(f"[{u.split('/')[2]} ↗]({u})" for u in urls), "[/wrap]", "[/wrap]", ""]
    return "\n".join(out)


def sig(cat, cred, frm, title, body, url):
    return "\n".join([f'[wrap=air-sig cat="{cat}" cred={cred}]', chips(cat, cred, frm), "", f"**{title}**", "",
                      body, "", "[wrap=air-src]", f"[{url.split('/')[2]} ↗]({url})", "[/wrap]", "[/wrap]", ""])


CORE = [
    ("今日要紧", "g", "甲公司发布监测体系（10月29日）", "四层监测，回放可提前 30 小时报警。",
     ["https://official.example/a", "https://media.example/b"], "数字员工也需要行为监测。"),
    ("今日要紧", "y", "乙公司开放第三方智能体（10月29日）", "支持第三方智能体与 IM，数字为当事方自述。",
     ["https://media3.example/c", "https://media4.example/d"], "平台评估要看开放性。"),
    ("工具与做法", "g", "给沙箱出网做双层封堵", "DNS 只放行白名单，并加两道独立封堵。",
     ["https://builder.example/a"], "上线前核查 DNS 隔离。"),
    ("行业速递", "g", "快讯一（10月29日）", "一句话事实。", ["https://practice.example/a", "https://media2.example/c"], None),
    ("行业速递", "g", "快讯二（10月30日）", "一句话事实。", ["https://practice.example/b", "https://media2.example/d"], None),
    ("行业速递", "r", "快讯三（10月29日）", "营收数字；单一官方来源，尚无独立验证。", ["https://sec.example/x"], None),
]
TRACK = [("跟踪", "g", "某模型发布（10月28日）", "官方称最强模型。", ["https://deepmind.example/t"], None)]
SIGS = [("新品与工具", "r", "AI Agents News", "某沙箱发布（beta）", "隔离执行智能体代码。", "https://agg.example/s1"),
        ("行业动态", "y", "澎湃新闻", "某模型权重将开放（预告）", "参数规模三万亿级。", "https://news.example/s2")]


def build_md(core=CORE, track=TRACK, sigs=SIGS, stats=None, legend=None, extra_after_h1=""):
    allc = [c[1] for c in core] + [t[1] for t in track] + [s[1] for s in sigs]
    cnt = {k: allc.count(k) for k in "gyr"}
    stats = stats or [len(core), len(track), len(sigs), len(allc)]
    legend = legend or f"🟢 官方 + 独立 {cnt['g']}　🟡 当事方 / 媒体 {cnt['y']}　🔴 单一来源 {cnt['r']}"
    stat_block = "\n".join(f"[wrap=air-stat]\n**{n}**\n{l}\n[/wrap]" for n, l in zip(stats, ["精编", "本周跟踪", "信号雷达", "合计"]))
    parts = ["[wrap=air-daily]", "", "# AI 雷达日报｜10.30", "", "> 今日主线：测试主线。", "",
             "> AI 雷达日报 · 每日洞察 AI 赋能风电", "", extra_after_h1,
             "[wrap=air-stats]", stat_block, "[/wrap]", "", "[wrap=air-mix]", legend, "[/wrap]", "",
             "## 第一层 · 精编日报", "", "[wrap=air-lede]", "精编说明。", "[/wrap]", ""]
    parts += [card(*c) for c in core if c[0] == "今日要紧"]
    parts += [L.NO_ENERGY_LINE, ""]
    parts += [card(*c) for c in core if c[0] != "今日要紧"]
    parts += ["## 第二层 · 本周跟踪", "", "[wrap=air-lede]", "跟踪说明。", "[/wrap]", ""]
    parts += [card(*t) for t in track]
    parts += ["## 第三层 · 信号雷达", "", "[wrap=air-lede]", "雷达说明。", "[/wrap]", "", "[wrap=air-radar]", ""]
    parts += [sig(*s) for s in sigs]
    parts += ["[/wrap]", "", "## 社区今日", "", "- **做设备的同事：** [帖子](https://community.example/a)", "",
              "## 今日一问", "", "测试问题？", "", "[/wrap]", "", "---", "", TAIL]
    return "\n".join(parts)


TAIL = """### 集团AI能力地图

- [Agent/工作流搭建平台（Dify）](https://keconsole.goldwind.com)

### 技术交流群

[grid]

![本地大模型用户交流群|400x400](qr1.png)

![AI智能体开发平台运营群|400x400](qr2.png)

![语义本体构建交流群|400x400](qr3.png)

![KNS统一数据平台|400x400](qr4.png)

![标准化助手交流群|400x400](qr5.png)

![风衍大模型用户交流群|400x400](qr6.png)

[/grid]

**出品｜集团数字化中心**  
**策划整理｜AI应用研究院**

我们跟踪 AI 前沿、沉淀金风业务实践、帮业务部门把 AI 用起来

**欢迎投稿**：欢迎分享发布科普内容或者你的心得与技巧到 AI 社区，我们一起交流、探讨！

[往期合集](https://aicopilot.goldwind.com.cn:3211/c/15-category/15)

---

> 以上内容为【AI日报-Agent】自动整理报道，仅供内部信息参考。涉及产品选型、生产部署、设备诊断和安全决策时，应结合正式文件、业务数据与专业人员意见复核。AI不替代责任主体的最终判断。
"""


def receipt(cid, urls):
    return {"claim_id": cid, "claim_text": "可核验事实", "claim_type": "action", "materiality": "critical",
            "event_date": "2026-10-29",
            "sources": [{"url": u, "tier": "T0" if i == 0 else "T1", "locator": "第1段", "verbatim": "原文",
                         "supports": "full", "accessed_at": "2026-10-30T09:00:00+08:00", "published_at": None}
                        for i, u in enumerate(urls)],
            "verification": {"status": "PASS"}}


def search_log(core=CORE, track=TRACK, sigs=SIGS):
    names = ["openai", "anthropic", "google", "meta", "microsoft", "xai", "deepseek", "qwen", "bytedance",
             "智谱", "月之暗面", "minimax", "百度", "腾讯"]
    cands = []
    for i, c in enumerate(core, 1):
        cands.append({"id": f"NEWS-{i:02d}", "title": c[2], "event_date": "2026-10-29", "source_name": "x",
                      "source_url": c[4][0], "source_urls": c[4], "decision": "SELECTED"})
    for i, t in enumerate(track, 1):
        cands.append({"id": f"TRK-{i:02d}", "title": t[2], "event_date": "2026-10-28", "source_name": "x",
                      "source_url": t[4][0], "source_urls": t[4], "decision": "TRACKING"})
    for i, s in enumerate(sigs, 1):
        cands.append({"id": f"SIG-{i:02d}", "title": s[3], "event_date": "2026-10-29", "source_name": s[2],
                      "source_url": s[5], "source_urls": [s[5]], "decision": "SIGNAL"})
    return {"report_date": "2026-10-30", "window": "d2", "queries": ["AI news", "agent runtime SDK"],
            "official_sources_checked": [{"name": n, "url": f"https://src.example/{i}", "checked": True}
                                         for i, n in enumerate(names)],
            "candidates": cands,
            "recall_review": {"status": "PASS", "reviewed_at": "2026-10-30T09:00:00+08:00",
                              "major_missed_events": [], "notes": "测试"}}


def semantic(core=CORE):
    items = [{"title": c[2], "status": "PASS", "facts_checked": ["测试"], "independent_sources": len(c[4]) > 1,
              "source_urls": c[4], "evidence_receipts": [receipt(f"C-{i:02d}", c[4])]}
             for i, c in enumerate(core, 1) if c[0] != "行业速递"]
    return {"overall": "PASS", "recent_dedup_checked": True, "items": items}


class V5Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.md = self.root / "AI雷达日报_2026-10-30.md"
        self.recent = self.root / "recent.json"
        self.recent.write_text('{"titles": [], "urls": []}', encoding="utf-8")
        for i in range(1, 7):
            (self.root / f"qr{i}.png").write_bytes(b"\x89PNG placeholder")

    def tearDown(self):
        self.td.cleanup()

    def run_self_check(self, md, log=None, sem=None):
        self.md.write_text(md, encoding="utf-8")
        sp, lp = self.root / "sem.json", self.root / "log.json"
        sp.write_text(json.dumps(sem or semantic(), ensure_ascii=False), encoding="utf-8")
        lp.write_text(json.dumps(log or search_log(), ensure_ascii=False), encoding="utf-8")
        return self_check.run_checks(self.md, sp, lp, recent_data_path=self.recent, report_date="2026-10-30")

    def failures(self, results):
        return [r for r in results if not r["passed"]]


class V5Pass(V5Base):
    def test_full_v5_passes_self_check(self):
        fails = self.failures(self.run_self_check(build_md()))
        self.assertEqual(fails, [], fails)

    def test_full_v5_passes_preflight(self):
        self.md.write_text(build_md(), encoding="utf-8")
        sp, lp = self.root / "sem.json", self.root / "log.json"
        sp.write_text(json.dumps(semantic(), ensure_ascii=False), encoding="utf-8")
        lp.write_text(json.dumps(search_log(), ensure_ascii=False), encoding="utf-8")
        rep = preflight_check.run_preflight(self.md, sp, lp, "2026-10-30")
        self.assertEqual(rep["status"], "PASS", rep.get("errors"))


class V5Gates(V5Base):
    def assert_fail(self, md, text, log=None):
        fails = self.failures(self.run_self_check(md, log=log))
        self.assertTrue(any(text in f["message"] for f in fails), fails)

    def test_two_wraps_on_one_line(self):
        md = build_md().replace(
            '<span data-air-chip="sec">今日要紧</span>', "[wrap=air-sec]今日要紧[/wrap] [wrap=air-x]a[/wrap]", 1)
        self.assert_fail(md, "同一行出现多个 [wrap=]")

    def test_stats_mismatch(self):
        self.assert_fail(build_md(stats=[9, 1, 2, 12]), "统计块")

    def test_legend_mismatch(self):
        self.assert_fail(build_md(legend="🟢 官方 + 独立 1　🟡 当事方 / 媒体 1　🔴 单一来源 1"), "可信度图例")

    def test_track_out_of_window(self):
        track = [("跟踪", "g", "旧闻发布（10月16日）", "两周前的事。", ["https://old.example/t"], None)]
        self.assert_fail(build_md(track=track), "跟踪条目日期", log=search_log(track=track))

    def test_core_single_source_must_be_r(self):
        core = list(CORE)
        core[3] = ("行业速递", "g", "快讯一（10月29日）", "一句话事实。", ["https://practice.example/a"], None)
        self.assert_fail(build_md(core=core), "两个独立来源", log=search_log(core=core))

    def test_signal_must_be_in_search_log(self):
        log = search_log()
        log["candidates"] = [c for c in log["candidates"] if c["decision"] != "SIGNAL"]
        self.assert_fail(build_md(), "检索清单无对应 SIGNAL", log=log)

    def test_signal_weak_source_blocked(self):
        sigs = [("行业动态", "r", "公众号", "某消息", "一句话。", "https://mp.weixin.qq.com/s/abc")]
        self.assert_fail(build_md(sigs=sigs), "弱来源", log=search_log(sigs=sigs))

    def test_no_tools_requires_fixed_line(self):
        core = [c for c in CORE if c[0] != "工具与做法"]
        self.assert_fail(build_md(core=core), "无工具与做法卡片时须写", log=search_log(core=core))

    def test_signal_category_whitelist(self):
        sigs = [("八卦", "r", "某论坛", "某消息", "一句话。", "https://forum.example/s")]
        self.assert_fail(build_md(sigs=sigs), "八类白名单", log=search_log(sigs=sigs))

    def test_tail_inside_daily_wrap(self):
        md = build_md().replace("测试问题？\n\n[/wrap]\n\n---\n\n### 集团AI能力地图", "测试问题？\n\n---\n\n### 集团AI能力地图")
        md = md.replace("> 以上内容为【AI日报-Agent】", "[/wrap]\n\n> 以上内容为【AI日报-Agent】")
        self.assert_fail(md, "尾部能力地图")


class V5NoTools(V5Base):
    """V5.0.1：当日确无可借鉴做法时，用固定空行通过 V5 门禁与适配后的 V4 自查。"""

    def test_empty_tools_with_fixed_line_passes(self):
        core = [c for c in CORE if c[0] != "工具与做法"]
        md = build_md(core=core)
        first_digest = md.index("[wrap=air-card cred=g]\n" + chips("行业速递", "g"))
        md = md[:first_digest] + L.NO_TOOLS_LINE + "\n\n" + md[first_digest:]
        results = self.run_self_check(md, log=search_log(core=core), sem=semantic(core=core))
        self.assertEqual(self.failures(results), [], self.failures(results))
        self.assertIn(L.NO_TOOLS_LINE, L.to_v4(md))


class V51Guards(V5Base):
    """V5.1.0：残留占位符、公司网络不可达链接、事件键、飞书头条候选池。"""

    def test_placeholder_blocks(self):
        md = build_md().replace("测试问题？", "【待人工替换为具体帖子】")
        fails = self.failures(self.run_self_check(md))
        self.assertTrue(any("占位" in f["message"] for f in fails), fails)

    def test_blocked_domain_blocks(self):
        md = build_md().replace("https://official.example/a", "https://kotaku.com/some-article")
        fails = self.failures(self.run_self_check(md))
        self.assertTrue(any("公司网络打不开" in f["message"] for f in fails), fails)

    def test_blocked_domain_in_tail_not_checked(self):
        self.assertFalse(any("公司网络打不开" in f["message"] for f in self.failures(self.run_self_check(build_md()))))

    def test_generic_event_key_detected(self):
        import seen_index
        cands = [{"id": "SIG-01", "event_key": "signal-1"}, {"id": "TRK-01", "event_key": "track_2"},
                 {"id": "N", "event_key": "openai|发布|失准监测体系"}, {"id": "E", "event_key": ""}]
        self.assertEqual([i for i, _ in seen_index.generic_event_keys(cands)], ["SIG-01", "TRK-01"])

    def test_preflight_rejects_generic_event_key(self):
        log = search_log()
        log["candidates"][0]["event_key"] = "news-01"
        self.md.write_text(build_md(), encoding="utf-8")
        sp, lp = self.root / "sem.json", self.root / "log.json"
        sp.write_text(json.dumps(semantic(), ensure_ascii=False), encoding="utf-8")
        lp.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
        rep = preflight_check.run_preflight(self.md, sp, lp, "2026-10-30")
        self.assertTrue(any(e["code"] == "CANDIDATE_EVENT_KEY_GENERIC" for e in rep["errors"]), rep["errors"])

    def test_feishu_headline_pool_wider_than_three(self):
        core = [c for c in CORE if c[0] != "工具与做法"]
        hs = prepare_feishu_push.extract_push_content(L.to_v4(build_md(core=core)))[1]
        self.assertGreaterEqual(len(hs), 5)


class V5Adapter(unittest.TestCase):
    def test_to_v4_shape(self):
        v4 = L.to_v4(build_md())
        for h in ["## 今日要紧", "## 金风相关", "## 工具与做法", "## 行业速递", "## 社区今日", "## 今日一问", "### 集团AI能力地图"]:
            self.assertIn(h, v4)
        self.assertIn("### 甲公司发布监测体系（10月29日）", v4)
        self.assertIn("- 🔴 **快讯三（10月29日）：**", v4)
        self.assertNotIn("[wrap=", v4)
        self.assertNotIn("某沙箱发布", v4)  # 雷达不进 V4 精编

    def test_feishu_extract_from_v5_tops_up_to_three(self):
        title, highlights, flash, energy, deep = prepare_feishu_push.extract_push_content(L.to_v4(build_md()))
        # 今日要紧 2 条 + 工具与做法 1 条 + 速递 3 条，候选池上限 6（V5.1.0 起速递也进池）
        self.assertEqual(len(highlights), 6)
        self.assertEqual(highlights[2][0], "给沙箱出网做双层封堵")
        self.assertIn("快讯一", [h[0] for h in highlights])

    def test_fetch_recent_v5_all_layers(self):
        titles, urls = fetch_recent.extract_news_items(build_md())
        self.assertIn("某沙箱发布（beta）", titles)
        self.assertIn("某模型发布（10月28日）", titles)
        self.assertIn("https://agg.example/s1", urls)

    def test_v4_untouched(self):
        self.assertFalse(L.is_v5("# AI 雷达日报｜10.30\n\n## 今日要紧\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
