import json
import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import dossier  # noqa: E402
from forum import ForumError, write_feed_key  # noqa: E402

# UAT 改造：底座帖 ID 从配置读取，不再硬编码旧社区的 632。
FEED_TOPIC_ID = json.loads((HERE.parent / "config" / "topics.json").read_text(encoding="utf-8"))["forum"]["feed_topic_id"]

SIGNAL_RAW = (HERE / "fixtures" / "signal_topic_raw.md").read_text(encoding="utf-8")
FEED_RAW = "> 底座帖\n\n```json\n" + json.dumps({"schema": "air-pulse/1", "brief": [1], "radar": [2]}, ensure_ascii=False) + "\n```\n"


class FakeForum:
    def __init__(self):
        self.cfg = {"url": "https://f.example.com"}
        self.topics = {FEED_TOPIC_ID: {"raw": FEED_RAW, "post_id": 1}}
        self.next = 2000
        self.writes = []
        self.tagged = [
            {"id": 1001, "slug": "mcp", "title": "arXiv：MCP 工具投毒在 Agent 框架中的跨框架测量", "category_id": 42,
             "created_at": "2026-11-05T07:31:00+08:00", "tags": ["security", "topic-agent-security", "cred-red", "auto"]},
            {"id": 1002, "slug": "other-cat", "title": "别的分类", "category_id": 7, "created_at": "2026-11-05T07:31:00+08:00", "tags": []},
            {"id": 990, "slug": "old", "title": "两个月前", "category_id": 42, "created_at": "2026-09-01T00:00:00+08:00", "tags": []},
        ]

    def tag_topics(self, tag, max_pages=5):
        return self.tagged if tag == "topic-agent-security" else []

    def raw(self, tid):
        return SIGNAL_RAW

    def first_post(self, tid):
        t = self.topics[tid]
        return {"id": t["post_id"], "raw": t["raw"]}

    def create_topic(self, title, raw, category, tags):
        tid, pid = self.next, self.next + 10000
        self.next += 1
        self.topics[tid] = {"raw": raw, "post_id": pid, "title": title}
        self.writes.append(("create", title))
        return {"topic_id": tid, "id": pid}

    def update_post(self, post_id, raw, reason):
        for t in self.topics.values():
            if t["post_id"] == post_id:
                t["raw"] = raw
        self.writes.append(("update", post_id))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c = json.loads(dossier.CONFIG.read_text(encoding="utf-8"))
    c["forum"]["signal_category_id"] = 42
    c["work_dir"] = str(tmp_path)
    # UAT 改造：publish 成功后会把档案帖 ID 回填进真实 config/topics.json，
    # 而 FakeForum 里没有这些帖子，collect 读上期判断时会 KeyError。
    # 测试只覆盖「首次创建 → 再次更新」这条路径，这里统一按未发布状态起跑。
    for t in c["topics"]:
        t["dossier_topic_id"] = None
    p = tmp_path / "topics.json"
    p.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(dossier, "CONFIG", p)
    return c


def fill(material, judge="工具投毒已从论文进入实测阶段，接入外部 MCP 前先评审。"):
    j = dossier.template(material)
    for t in j["topics"]:
        t["judge"] = judge
        t["next"] = "主流框架的默认策略是否收紧"
        if t["change"] == "":
            t["change"] = "新增一篇跨框架测量论文。"
    return j


def test_collect_filters_category_and_window(cfg):
    f = FakeForum()
    mat = dossier.collect(f, cfg, date(2026, 11, 6))
    sec = next(t for t in mat["topics"] if t["tag"] == "topic-agent-security")
    assert [s["topic_id"] for s in sec["week_signals"]] == [1001]   # 别的分类、两个月前的都不算
    assert sec["week_signals"][0]["cred"] == "red"
    others = [t for t in mat["topics"] if t["tag"] != "topic-agent-security"]
    assert all(not t["week_signals"] for t in others)


def test_validate_requires_evidence_and_rejects_placeholders(cfg):
    mat = dossier.collect(FakeForum(), cfg, date(2026, 11, 6))
    j = fill(mat)
    assert dossier.validate(mat, j) == []
    sec = next(t for t in j["topics"] if t["name"] == "智能体与 MCP 安全")
    sec["evidence"] = []
    sec["change"] = dossier.NO_SIGNAL_CHANGE
    sec["next"] = "[示例] 待补"
    errs = dossier.validate(mat, j)
    assert any("至少要引用" in e for e in errs)
    assert any("不能写" in e for e in errs)
    assert any("占位符" in e for e in errs)
    sec["evidence"] = [12345]
    assert any("不在近 30 天" in e for e in dossier.validate(mat, j))


def test_publish_creates_then_updates_and_writes_only_topics(cfg):
    f = FakeForum()
    mat = dossier.collect(f, cfg, date(2026, 11, 6))
    j = fill(mat)
    res = dossier.publish(f, cfg, mat, j)
    assert all(d["status"] == "OK" for d in res["dossiers"])
    assert sum(1 for w in f.writes if w[0] == "create") == 4
    saved = json.loads(dossier.CONFIG.read_text(encoding="utf-8"))
    assert all(t["dossier_topic_id"] for t in saved["topics"])      # 首次发布回填话题 ID
    feed = json.loads(f.topics[FEED_TOPIC_ID]["raw"].split("```json\n")[1].split("\n```")[0])
    assert feed["brief"] == [1] and feed["radar"] == [2]             # 其他字段不动
    assert len(feed["topics"]) == 4 and feed["topics"][2]["week"] == 1
    assert feed["topics"][2]["url"].startswith("/t/")
    # 第二周：更新已有帖子，不再新建；历史判断保留
    cfg2 = saved
    mat2 = dossier.collect(f, cfg2, date(2026, 11, 13))
    sec_prev = next(t for t in mat2["topics"] if t["name"] == "智能体与 MCP 安全")["previous"]
    assert sec_prev["judge"].startswith("工具投毒")
    j2 = fill(mat2, judge="第二周的新判断。")
    dossier.publish(f, cfg2, mat2, j2)
    assert sum(1 for w in f.writes if w[0] == "create") == 4
    tid = next(t for t in cfg2["topics"] if t["name"] == "智能体与 MCP 安全")["dossier_topic_id"]
    raw = f.topics[tid]["raw"]
    assert "第二周的新判断" in raw and "**历史判断**" in raw and "工具投毒已从论文" in raw


def test_feed_write_refuses_unknown_format():
    f = FakeForum()
    f.topics[FEED_TOPIC_ID]["raw"] = "没有代码块"
    with pytest.raises(ForumError):
        write_feed_key(f, FEED_TOPIC_ID, "topics", [], "x")
    assert not f.writes


def test_whitelist_matches_collector_format(cfg):
    out = dossier.whitelist(cfg)
    assert "topics:" in out and "  功率预测 AI: topic-power-forecast" in out
