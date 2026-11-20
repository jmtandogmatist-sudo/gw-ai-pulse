"""01/02 的同源限额与「可信度优先于日期」排序（2026-11-19）。"""
from datetime import datetime

from collector.models import Signal, SourceRef
from collector.render import (_brief_rank, _diverse, brief_items, lead_item,
                              pick_items, topic_body)

FIXED_NOW = datetime(2026, 11, 19, 12, 0)


def mk(title, *, source="IT之家", cred="red", tier="media", relevance=2,
       domain="选型", event_date="2026-11-19", tm="09:00", topics=None, url=None):
    return Signal(
        signal_id=title[:8], event_key="k-" + title[:6], title=title, summary="摘要第一句。第二句。",
        us="对金风判断", domain=domain, questions=["Q03"], topics=topics or [],
        relevance=relevance, cred=cred, event_date=event_date,
        discovered_at=f"{event_date}T{tm}:00+08:00",
        sources=[SourceRef(name=source, url=url or f"https://{source}.example/{title[:4]}", tier=tier)],
        topic_id=700,
    )


# ---------- 同源限额 ----------

def test_brief_caps_one_item_per_source():
    """01 三条必须来自三个不同信源——这是「首页全是 IT之家」的直接解药。"""
    sigs = [mk(f"IT之家第{i}条") for i in range(1, 4)] + [mk("安全媒体一条", source="DarkReading")]
    got = brief_items(sigs, per_source=1, now=FIXED_NOW)
    assert len(got) == 3
    assert [g["source"] for g in got] == ["IT之家", "DarkReading", "IT之家"]


def test_lead_is_always_the_first_brief():
    """02 焦点必须等于 01 的第一条，否则同一页出现两套「今天最重要」。"""
    sigs = [mk("甲媒体头条", source="甲"), mk("乙媒体头条", source="乙"), mk("丙媒体头条", source="丙")]
    brief = brief_items(sigs, per_source=1, now=FIXED_NOW)
    lead = lead_item(sigs, per_source=1, now=FIXED_NOW)
    assert lead["title"] in brief[0]["text"]


def test_single_source_day_still_fills_three():
    """全天只有一家在发稿时也不能开天窗：放宽到 3 条。"""
    sigs = [mk(f"同一家第{i}条") for i in range(5)]
    got = brief_items(sigs, per_source=1, now=FIXED_NOW)
    assert len(got) == 3


def test_diverse_limit_zero_means_unlimited():
    sigs = [mk(f"同一家第{i}条") for i in range(4)]
    assert len(_diverse(sigs, 3, 0)) == 3
    assert len(_diverse(sigs, 3, 1)) == 3


def test_picks_caps_two_per_source():
    """今日值得看：一家的插件仓库不能刷满整块（实测有一轮 3 条同一个 GitHub 主题）。

    2026-11-20 条数 4 → 5（PICKS_MAX），断言跟着改成五格。
    """
    sigs = [mk(f"某插件{i}", source="DSH 插件生态", domain="工具做法") for i in range(4)]
    sigs += [mk("NGINX 五个严重漏洞", source="Infosecurity", domain="工具做法"),
             mk("某框架发布新版本", source="InfoQ", domain="工具做法"),
             mk("某开源工具开源", source="开源中国", domain="工具做法")]
    got = pick_items(sigs, per_source=2, now=FIXED_NOW)
    hosts = [g["url"].split("/")[2] for g in got]
    assert len(got) == 5                                 # 五格
    assert hosts.count("DSH 插件生态.example") == 2      # 限到 2 条
    assert len(set(hosts)) == 4                          # 五格来自四个信源


def test_same_host_different_source_names_share_one_quota():
    """同一个站被拆成多个源名，只能共用一个额度。

    实测：IT之家 · AI / IT之家 · 算力 都在 ithome.com；DSH 插件生态 / DSH 主仓库 /
    GitHub Trending 都在 github.com。按源名限等于给同一个站开了好几张额度，
    04「今日值得看」四格里三个 github 仓库就是这么来的。
    """
    sigs = [mk("上手指南甲", source="IT之家 · AI", domain="工具做法",
               url="https://www.ithome.com/1/a.htm"),
            mk("上手指南乙", source="IT之家 · 算力", domain="工具做法",
               url="https://www.ithome.com/1/b.htm"),
            mk("插件仓库甲", source="DSH 插件生态", domain="工具做法", url="https://github.com/a/x"),
            mk("插件仓库乙", source="DSH 主仓库", domain="工具做法", url="https://github.com/b/y")]
    got = pick_items(sigs, per_source=2, now=FIXED_NOW)
    hosts = [g["url"].split("/")[2] for g in got]
    assert len(hosts) == 4
    assert hosts.count("www.ithome.com") == 2            # 两个子源合用一个额度
    assert hosts.count("github.com") == 2


# ---------- 可信度优先于日期 ----------

def test_official_yellow_beats_newer_media_red():
    """官方一手（yellow）发布得早，也要压过当天更晚的二手转载（red）。"""
    official = mk("OpenAI 官方发布新模型", source="OpenAI", cred="yellow", tier="official", tm="08:00")
    media = mk("媒体转载同一件事", source="某媒体", cred="red", tier="media", tm="20:00")
    assert _brief_rank(official, FIXED_NOW) < _brief_rank(media, FIXED_NOW)


def test_yesterday_official_still_loses_to_today_media():
    """但不能退回老问题：昨天的官方稿不该压住今天的新料（tier 层仍管时效）。"""
    yesterday = mk("昨天的官方稿", source="OpenAI", cred="yellow", tier="official",
                   event_date="2026-11-18", tm="08:00")
    today = mk("今天的媒体稿", source="某媒体", cred="red", tier="media", tm="08:00")
    assert _brief_rank(today, FIXED_NOW) < _brief_rank(yesterday, FIXED_NOW)


# ---------- 「对金风」折叠 ----------

def test_us_is_rendered_as_collapsible_block():
    """分类 20 已对全员开放，模型研判必须以折叠块出现，并标明「非公司结论」。"""
    sig = mk("某条信号")
    body = topic_body(sig, _cfg())
    assert "[details=\"AI 研判（自动生成，非公司结论）\"]" in body
    assert "[wrap=ais-us]" not in body
    assert sig.us in body


def _cfg():
    from collector.config import load_config
    return load_config()
