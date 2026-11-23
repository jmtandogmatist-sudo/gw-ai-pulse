"""02-深度简报 离线单元测试（覆盖捕手、source-verifier 硬审计、成稿解析、渲染与 632 卡片）。"""
import json
from datetime import date, datetime

from brief.composer import compose_brief
from brief.config import load_config
from brief.harvester import CST, Lead, harvest, load_recent_history
from brief.render import build_home_card, render_topic_markdown, topic_title
from brief.verifier import (audit_brief_sources, classify_tier, enrich_cn_mirrors,
                            is_cn_direct, sanitize_markdown_links, verify_leads)


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


def _sample_leads():
    return [
        Lead(
            id="S01", category="ai_frontier", query_label="前沿实验室",
            title="Anthropic raises $81B in record IPO",
            url="https://techcrunch.com/2026/11/19/anthropic-ipo",
            publisher="TechCrunch", published_date="2026-11-19",
            snippet="Anthropic priced its IPO at $83/share, raising $81.3B at a $1.5T valuation.",
            origin="tavily", tier="T3",
        ),
        Lead(
            id="S02", category="wind_industry", query_label="海风工程",
            title="US offshore wind exceeds 1,600 MW while 16.5 GW leases relinquished",
            url="https://www.offshorewind.biz/2026/11/19/us-offshore-wind",
            publisher="Offshore Wind", published_date="2026-11-19",
            snippet="Five US offshore wind farms now total over 1,600 MW, while 12 leases totaling 16.5 GW were bought back.",
            origin="tavily", tier="T3",
        ),
        Lead(
            id="S03", category="trade_policy", query_label="碳税政策",
            title="UK-EU summit targets ETS linkage and CBAM steel/aluminium exemption",
            url="https://www.gov.uk/government/news/uk-eu-summit-package",
            publisher="GOV.UK", published_date="2026-11-19",
            snippet="UK government estimates ETS linkage brings 3.8B GBP annual gains and exempts 6.5B GBP exports from CBAM.",
            origin="tavily", tier="T0",
        ),
        Lead(
            id="S04", category="ai_domestic", query_label="国产开源",
            title="Tencent-Hunyuan Hy4 open-sourced on GitHub",
            url="https://github.com/Tencent-Hunyuan/Hy4",
            publisher="GitHub", published_date="2026-11-20",
            snippet="Tencent Hunyuan Hy4 MoE model (770B total, 49B active) open-sourced under Apache 2.0.",
            origin="collector", tier="T2",
        ),
    ]


def test_tier_classification_and_link_sanitization():
    cfg = load_config()
    assert classify_tier("https://www.gov.uk/government/news/x", cfg) == "T0"
    assert classify_tier("https://www.gwec.net/report", cfg) == "T1"
    assert classify_tier("https://github.com/Tencent-Hunyuan/Hy4", cfg) == "T2"
    assert classify_tier("https://www.offshorewind.biz/2026/11/19/a", cfg) == "T3"
    assert classify_tier("https://unknown-vendor-blog.example/pr", cfg) == "T4"

    fixed = sanitize_markdown_links("详情见 [Guardian](https://a.com)(https://b.com) 和 [源]( \"https://c.com\" )")
    assert "[Guardian](https://a.com) / [相关来源](https://b.com)" in fixed
    assert "[源](https://c.com)" in fixed


def test_verify_leads_filters_dead_and_stale_and_marks_tracking():
    cfg = load_config()
    leads = [
        Lead("S01", "ai_frontier", "q", "今日新闻", "https://techcrunch.com/a", "TechCrunch", "2026-11-19", "body", "tavily"),
        Lead("S02", "wind_industry", "q", "3天前跟进", "https://www.offshorewind.biz/b", "Offshore Wind", "2026-11-17", "body", "tavily"),
        Lead("S03", "wind_industry", "q", "两周前旧闻", "https://www.offshorewind.biz/old", "Offshore Wind", "2026-11-01", "body", "tavily"),
        Lead("S04", "trade_policy", "q", "404死链", "https://www.gov.uk/dead", "GOV.UK", "2026-11-19", "body", "tavily"),
    ]
    def fake_check(u):
        return (False, 404) if "dead" in u else (True, 200)

    out, rep = verify_leads(leads, cfg, date(2026, 11, 20), check_fn=fake_check)
    assert rep["stale_dropped"] == 1
    assert rep["dead_dropped"] == 1
    assert rep["tracking_marked"] == 1
    assert len(out) == 2
    assert out[0].title == "今日新闻" and out[0].tracking is False
    assert out[1].title == "3天前跟进" and out[1].tracking is True


def test_compose_audit_and_render_brief():
    cfg = load_config()
    leads = _sample_leads()
    fake_llm_output = json.dumps({
        "features": [
            {
                "title": "Anthropic 813 亿美元 IPO 定价登顶，公开市场检验 AI 算力闭环",
                "lead": "11 月 19 日，Anthropic 以每股 83 美元定价登陆纳斯达克，募资 813 亿美元、估值达 1.5 万亿美元。",
                "source_ids": ["S01", "S99"],  # S99 为编造编号，必须被审计剔除
                "points": [
                    {"heading": "估值与募资规模", "body": "发行价高于区间上限，成为史上最大规模前沿 AI 上市案例。", "source_ids": ["S01"]},
                    {"heading": "行业影响", "body": "公开市场首次按季度审视前沿实验室营收与算力支出平衡。", "source_ids": ["S01"]},
                    {"heading": "开源生态对照", "body": "同日开源阵营持续下探部署门槛，腾讯混元 Hy4 开放商用权重。", "source_ids": ["S04"]},
                ],
            },
            {
                "title": "美国海风装机破 1.6 GW 与 16.5 GW 租约退回并存",
                "lead": "美国海上风电一边实现 5 个项目满发突破 1,600 MW，一边因买断和解退回 12 块租约。",
                "source_ids": ["S02"],
                "points": [
                    {"heading": "并网与退约分化", "body": "已建项目进入稳定发电期，但超 16.5 GW 规划储备被取消。", "source_ids": ["S02"]},
                    {"heading": "欧洲碳壁垒联动", "body": "英欧推进 ETS 联网与 CBAM 豁免谈判，抬高出口碳足迹门槛。", "source_ids": ["S03"]},
                    {"heading": "供应链启示", "body": "风电整机与工程企业重心加速向非美及本地化合规市场倾斜。", "source_ids": ["S02", "S03"]},
                ],
            },
        ],
        "sections": [
            {
                "title": "人工智能与前沿科技",
                "items": [
                    {"heading": "国产大模型与开源生态动态核查", "body": "腾讯混元 Hy4（770B/49B 激活）以 Apache 2.0 正式开源；其余主流国产基座过去 24h 无新权重发布。", "source_ids": ["S04"], "t4_caveat": False},
                ],
            },
            {
                "title": "全球能源贸易与碳壁垒政策",
                "items": [
                    {"heading": "英欧推进 ETS 联网与 CBAM 豁免", "body": "英国测算 ETS 联网每年带来 38 亿英镑收益并覆盖 65 亿英镑钢铝出口豁免。", "source_ids": ["S03"], "t4_caveat": False},
                ],
            },
            {
                "title": "全球风能工程与产业",
                "items": [
                    {"heading": "美国海风装机突破 1,600 MW", "body": "5 个全容量运营项目并网，但 12 块租约合计 16.5 GW 被收回。", "source_ids": ["S02"], "t4_caveat": False},
                ],
            },
        ],
        "insights": [
            {"heading": "AI 资本与开源双线重塑行业格局", "body": "头部闭源实验室迈入公开财报审视期，开源商用模型持续降低企业部署门槛。"},
            {"heading": "全球风电增长重心加速向非美与合规市场迁移", "body": "美国海风退约与欧洲碳税联网并行，倒逼整机与供应链强化本地化与碳足迹能力。"},
        ],
    }, ensure_ascii=False)

    rdate = date(2026, 11, 20)
    brief, audit = compose_brief(leads, rdate, cfg, call_fn=lambda _s, _u: fake_llm_output)
    assert "feature#1:S99" in audit["unknown_source_ids_removed"]
    assert audit["used_sources"] == 4

    lead_by_id = {x.id: x for x in leads}
    md = render_topic_markdown(rdate, brief, lead_by_id)
    assert "[wrap=ais-brief]" in md
    assert "[wrap=ais-brief-insights]" in md
    assert "[wrap=ais-brief-feat]" in md
    assert "[wrap=ais-brief-news]" in md
    assert "[wrap=ais-brief-sec kind=ai]" in md
    assert "[wrap=ais-brief-sec kind=trade]" in md
    assert "[wrap=ais-brief-sec kind=wind]" in md
    # 趋势洞察必须排在主打深度报道前面
    assert md.index("[wrap=ais-brief-insights]") < md.index("[wrap=ais-brief-feat]")
    assert "[TechCrunch](https://techcrunch.com/2026/11/19/anthropic-ipo)" in md
    assert "[GOV.UK](https://www.gov.uk/government/news/uk-eu-summit-package)" in md
    assert "S99" not in md

    title = topic_title(rdate, brief)
    assert title.startswith("每日深度简报（2026-11-20）：")

    card = build_home_card(rdate, brief, topic_id=999, base_url="https://aicopilot.goldwind.com.cn:3211")
    assert card["topic_id"] == 999
    assert card["url"] == "https://aicopilot.goldwind.com.cn:3211/t/999"
    assert len(card["insights"]) == 2
    assert card["insights"][0]["heading"] and card["insights"][0]["body"]
    assert len(card["feature_titles"]) == 2
    assert len(json.dumps(card, ensure_ascii=False)) < 1400


def test_harvest_dedups_tavily_and_skips_noise_hosts():
    cfg = load_config()
    cfg.raw["collector_sqlite"]["enabled"] = False
    cfg.raw["search"]["queries"] = [cfg.raw["search"]["queries"][0]]

    def fake_post(_url, _payload):
        return _Resp(200, {"results": [
            {"title": "Good News", "url": "https://techcrunch.com/2026/11/19/good?utm_source=x",
             "published_date": "Thu, 19 Nov 2026 08:00:00 GMT", "content": "Detailed content here."},
            {"title": "Duplicate News", "url": "https://techcrunch.com/2026/11/19/good",
             "published_date": "Thu, 19 Nov 2026 08:00:00 GMT", "content": "Duplicate."},
            {"title": "Noise Facebook", "url": "https://www.facebook.com/post/123",
             "published_date": "Thu, 19 Nov 2026 08:00:00 GMT", "content": "Social noise."},
        ]})

    leads, meta = harvest(cfg, datetime(2026, 11, 20, 14, 0, tzinfo=CST), api_key="fake", post_fn=fake_post)
    assert len(leads) == 1
    assert leads[0].id == "S01"
    assert leads[0].published_date == "2026-11-19"
    assert meta["deduped"] == 1


def test_overseas_tagging_and_cn_mirror_enrichment():
    cfg = load_config()
    assert is_cn_direct("https://www.ithome.com/1/029/674.htm", cfg) is True
    assert is_cn_direct("https://www.nea.gov.cn/20261117/x.html", cfg) is True
    assert is_cn_direct("https://aicopilot.goldwind.com.cn:3211/t/908", cfg) is True
    assert is_cn_direct("https://openai.com/index/a", cfg) is False
    assert is_cn_direct("https://www.cnbc.com/2026/11/18/a.html", cfg) is False

    ld = Lead("S01", "ai_frontier", "q", "Anthropic IPO", "https://www.cnbc.com/a", "CNBC",
              "2026-11-19", "body", "tavily", tier="T3", needs_overseas=True)
    ld_comm = Lead("S02", "ai_domestic", "q", "Gemini Tasks", "https://blog.google/a", "Google Blog",
                   "2026-11-19", "body", "collector", tier="T2", needs_overseas=True,
                   community_url="https://aicopilot.goldwind.com.cn:3211/t/908")
    lead_by_id = {"S01": ld, "S02": ld_comm}
    brief = {
        "features": [{"title": "Anthropic IPO 创纪录", "lead": "导语", "source_ids": ["S01"], "points": []}],
        "sections": [{"title": "AI", "items": [
            {"heading": "Gemini 任务功能", "body": "正文", "source_ids": ["S02"]},
        ]}],
        "insights": [{"heading": "洞察", "body": "内容"}],
    }

    def fake_post(_u, _pl):
        return _Resp(200, {"results": [
            {"title": "无关公司2023年年度报告", "url": "https://www.cls.cn/detail/bad"},
            {"title": "Anthropic IPO 创纪录募资超千亿美元", "url": "https://www.cls.cn/detail/123456"},
        ]})

    stats = enrich_cn_mirrors(brief, lead_by_id, cfg, api_key="fake",
                              post_fn=fake_post, check_fn=lambda _u: (True, 200))
    # S02 已有社区站内信号链接，不重复查；只查 S01
    assert stats == {"queried": 1, "matched": 1}
    md = render_topic_markdown(date(2026, 11, 20), brief, lead_by_id)
    assert "[财联社](https://www.cls.cn/detail/123456)、[CNBC](https://www.cnbc.com/a)〔需外网〕" in md
    assert "[社区信号](https://aicopilot.goldwind.com.cn:3211/t/908)、[Google Blog](https://blog.google/a)〔需外网〕" in md


def test_cross_day_history_dedup(tmp_path):
    yday_dir = tmp_path / "output" / "2026-11-19"
    yday_dir.mkdir(parents=True)
    (yday_dir / "brief.json").write_text(json.dumps({
        "date": "2026-11-19",
        "leads": [{"id": "S01", "url": "https://techcrunch.com/2026/11/19/anthropic-ipo?utm_source=x"}],
        "brief": {
            "features": [{"title": "Anthropic 1000亿美元IPO", "source_ids": ["S01"], "points": []}],
            "sections": [],
            "insights": [],
        },
    }, ensure_ascii=False), encoding="utf-8")

    urls, topics = load_recent_history(tmp_path, date(2026, 11, 20), days=2)
    assert "https://techcrunch.com/2026/11/19/anthropic-ipo" in urls
    assert any("Anthropic" in t for t in topics)


def test_phase1_blocks_tavily_wiki_google_redirect_and_old_ym_urls():
    """2026-11-23 回归：拦截 tavily 百科页、google 跳转链、/2026/08/ 跨月旧文及无日期非权威页。"""
    cfg = load_config()
    cfg.raw["collector_sqlite"]["enabled"] = False
    cfg.raw["search"]["queries"] = [cfg.raw["search"]["queries"][0]]

    def fake_post(_url, _payload):
        return _Resp(200, {"results": [
            {"title": "NVIDIA", "url": "https://tavily.com/knowledge/company/TXRhn2TKUD7t",
             "published_date": "", "content": "Static company profile."},
            {"title": "Google Redirect", "url": "https://www.google.com/url?q=CAES123",
             "published_date": "2026-11-22", "content": "Opaque redirect."},
            {"title": "Old August Wind Story",
             "url": "https://energynow.com/2026/08/trump-refunds-to-cancel-offshore-wind",
             "published_date": "2026-11-21", "content": "Old August article with stale crawl date."},
            {"title": "Undated Blog", "url": "https://random-blog.example/ai-post",
             "published_date": "", "content": "No publish date at all."},
            {"title": "Fresh Real News", "url": "https://techcrunch.com/2026/11/22/fresh-ai",
             "published_date": "Sun, 22 Nov 2026 10:00:00 GMT", "content": "Real news today."},
        ]})

    leads, _meta = harvest(cfg, datetime(2026, 11, 23, 8, 30, tzinfo=CST), api_key="fake", post_fn=fake_post)
    urls = [x.url for x in leads]
    assert not any("tavily.com" in u for u in urls)
    assert not any("google.com/url" in u for u in urls)
    # 跨月旧文 URL 的日期应优先采信 /2026/08/（2026-08-28），随后被 verifier 拦截
    aug = [x for x in leads if "energynow.com" in x.url]
    assert len(aug) == 1 and aug[0].published_date == "2026-08-28"

    verified, rep = verify_leads(leads, cfg, date(2026, 11, 23), check_fn=lambda _u: (True, 200))
    assert rep["stale_dropped"] == 1
    assert rep["undated_dropped"] == 1
    assert [x.title for x in verified] == ["Fresh Real News"]


def test_verify_leads_enforces_strict_72h_and_allows_t0_undated():
    cfg = load_config()
    leads = [
        Lead("S01", "ai_frontier", "q", "今日新闻", "https://techcrunch.com/a", "TechCrunch", "2026-11-22", "body", "tavily"),
        Lead("S02", "ai_frontier", "q", "4天前旧闻", "https://techcrunch.com/old4", "TechCrunch", "2026-11-19", "body", "tavily"),
        Lead("S03", "trade_policy", "q", "T0官方无日期", "https://www.gov.uk/guidance/cbam", "GOV.UK", "", "body", "tavily"),
        Lead("S04", "ai_frontier", "q", "T4无日期百科", "https://unknown.example/wiki", "Unknown", "", "body", "tavily"),
    ]
    out, rep = verify_leads(leads, cfg, date(2026, 11, 23), check_fn=lambda _u: (True, 200))
    assert rep["stale_dropped"] == 1        # 4 天前严格淘汰
    assert rep["undated_dropped"] == 1      # T4 无日期淘汰
    titles = [x.title for x in out]
    assert "今日新闻" in titles
    assert "T0官方无日期" in titles
    assert "4天前旧闻" not in titles
    assert "T4无日期百科" not in titles
