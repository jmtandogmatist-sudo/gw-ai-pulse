import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import radar  # noqa: E402
from forum import ForumError  # noqa: E402

FEED = {"schema": "air-pulse/1", "brief": [1], "radar": [2], "topics": [{"name": "功率预测 AI", "judge": "判断", "url": "/t/2001"}]}

# UAT 改造：底座帖 ID 从配置读取，不再硬编码旧社区的 632。
# 硬编码会让测试在换社区后仍指向旧 ID，掩盖真实配置错误。
FEED_TOPIC_ID = json.loads((HERE.parent / "config" / "radar.json").read_text(encoding="utf-8"))["forum"]["feed_topic_id"]


class FakeForum:
    def __init__(self):
        self.cfg = {"url": "https://f.example.com"}
        self.posts = {FEED_TOPIC_ID: {"id": 1, "raw": "```json\n" + json.dumps(FEED, ensure_ascii=False) + "\n```"}}
        self.next = 3000

    def _req(self, method, path):
        if "page=0" in path:
            return {"topic_list": {"topics": [
                {"id": 1, "title": "Q1 信号甲", "created_at": "2027-01-10T08:00:00+08:00", "tags": ["energy-ai", "q01", "topic-power-forecast", "cred-green", "reviewed"]},
                {"id": 2, "title": "Q1 信号乙", "created_at": "2027-02-01T08:00:00+08:00", "tags": ["security", "q07", "cred-red", "auto"]},
                {"id": 3, "title": "上季度", "created_at": "2026-12-20T08:00:00+08:00", "tags": ["q03"]},
            ]}}
        return {"topic_list": {"topics": []}}

    def first_post(self, tid):
        return self.posts[tid]

    def create_topic(self, title, raw, category, tags):
        tid = self.next
        self.next += 1
        self.posts[tid] = {"id": tid + 10000, "raw": raw, "title": title}
        return {"topic_id": tid, "id": tid + 10000}

    def update_post(self, post_id, raw, reason):
        for p in self.posts.values():
            if p["id"] == post_id:
                p["raw"] = raw


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c = json.loads(radar.CONFIG.read_text(encoding="utf-8"))
    c["forum"]["signal_category_id"] = 42
    c["work_dir"] = str(tmp_path)
    p = tmp_path / "radar.json"
    p.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(radar, "CONFIG", p)
    return c


def entry(name, ring, quad="工具与平台", internal=True, why="理由写清楚"):
    ev = [{"type": "internal", "ref": "交付审核项目", "note": "生产使用中"}] if internal else [{"type": "signal", "ref": "1001", "note": "论文"}]
    return {"name": name, "quadrant": quad, "ring": ring, "why": why, "evidence": ev}


def test_quarter_parsing():
    assert radar.parse_quarter("2027q1")[:2] == (radar.date(2027, 1, 1), radar.date(2027, 4, 1))
    assert radar.parse_quarter("2027Q4")[1] == radar.date(2028, 1, 1)
    assert radar.parse_quarter("2027Q2")[2] == "2027 年第二季度"


def test_collect_counts_only_this_quarter(cfg):
    mat = radar.collect(FakeForum(), cfg, "2027Q1")
    assert mat["signals_total"] == 2 and mat["signals_reviewed"] == 1
    assert mat["by_question"] == {"Q01": 1, "Q07": 1}
    assert mat["by_topic"] == {"topic-power-forecast": 1}
    assert mat["dossier_judgments"][0]["name"] == "功率预测 AI"


def test_validate_rules(cfg):
    ok = {"entries": [entry("AI 智能体开发平台", "采纳"), entry("外部 MCP 工具接入", "评估", internal=False),
                      entry("未评审的第三方智能体仓库", "暂缓", internal=False)]}
    errs, warns = radar.validate(ok, cfg)
    assert errs == [] and any("建议" in w for w in warns)
    bad = {"entries": [entry("某工具", "试用", internal=False), entry("某工具", "乱写"),
                       dict(entry("空理由", "评估", internal=False), why=""), dict(entry("x", "评估"), evidence=[])]}
    errs, _ = radar.validate(bad, cfg)
    text = "\n".join(errs)
    assert "集团内使用证据" in text and "ring 必须是" in text and "重复" in text and "没有理由" in text and "没有证据" in text


def test_render_and_publish_then_movement(cfg):
    f = FakeForum()
    mat = radar.collect(f, cfg, "2027Q1")
    ents = {"entries": [entry("AI 智能体开发平台", "采纳"), entry("Office AI 助手", "试用"),
                        entry("未评审的第三方智能体仓库", "暂缓", internal=False)]}
    res = radar.publish(f, cfg, mat, ents)
    feed = json.loads(f.posts[FEED_TOPIC_ID]["raw"].split("```json\n")[1].split("\n```")[0])
    assert feed["brief"] == [1] and feed["radar"] == [2] and feed["topics"][0]["name"] == "功率预测 AI"   # 只动 stance
    assert [r["name"] for r in feed["stance"]["rings"]] == ["采纳", "试用", "暂缓"]
    assert feed["stance"]["url"] == f"/t/{res['topic_id']}" and feed["stance"]["rings"][0]["items"][0]["moved"] == "新"
    with pytest.raises(ForumError):
        radar.publish(f, cfg, mat, ents)                       # 同一季度不能发两次
    # 下一季度：Office 助手升到采纳，第三方仓库移出
    cfg2 = json.loads(radar.CONFIG.read_text(encoding="utf-8"))
    mat2 = dict(mat, quarter="2027Q2", label="2027 年第二季度")
    ents2 = {"entries": [entry("AI 智能体开发平台", "采纳"), entry("Office AI 助手", "采纳")]}
    raw, stance = radar.render_post(mat2, ents2, cfg2)
    assert "↑ **Office AI 助手**：试用 → 采纳" in raw
    assert "移出 **未评审的第三方智能体仓库**" in raw
    assert stance["rings"][0]["items"][1]["moved"] == "↑"


def test_template_carries_previous_entries(cfg):
    f = FakeForum()
    mat = radar.collect(f, cfg, "2027Q1")
    radar.publish(f, cfg, mat, {"entries": [entry("AI 智能体开发平台", "采纳")]})
    cfg2 = json.loads(radar.CONFIG.read_text(encoding="utf-8"))
    mat2 = radar.collect(f, cfg2, "2027Q2")
    t = radar.template(mat2)
    assert t["entries"][0]["name"] == "AI 智能体开发平台" and t["entries"][0]["_上期"].startswith("采纳")
