"""测试 prepare_feishu_push 的跨群头条/快讯去重（2026-09-11 复盘修复）。"""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import prepare_feishu_push as pf


def highlight(title, summary="示例摘要，用于填充内容。"):
    return (title, summary)


# 三群画像：与生产 feishu-group-profiles.json 一致
PROFILES = {
    "groups": {
        "oc_d613cd95638e14c145bba6c87a7123ad": {
            "label": "A 智能体", "emoji": "📡", "angle_label": "能力复用",
            "angle_suffix": "关注能力能否封装为可复用的智能体、工具或工作流。",
            "require_headline_match": True,
            "headline_keywords": ["智能体", "Agent", "工作流", "工具", "模型", "机器人", "世界模型", "DeepSeek", "OpenAI"],
            "question": "你最希望平台优先提供哪类行业智能体模板？",
        },
        "oc_52da4aa933ef08de47ec321717039e6b": {
            "label": "B KNS", "emoji": "📊", "angle_label": "数据价值",
            "angle_suffix": "关注业务数据能否进入分析、预测和决策闭环。",
            "require_headline_match": True,
            "headline_keywords": ["算电", "数据", "预测", "绿电", "智算", "光储", "交易", "资本开支", "高盛", "收费"],
            "question": "你最希望KNS支撑哪类分析？",
        },
        "oc_0fb9a0a98ee33b0419f1fce967f9c17b": {
            "label": "C 本地模型", "emoji": "🧠", "angle_label": "部署门槛",
            "angle_suffix": "关注参数规模、开源程度、推理成本与部署条件。",
            "require_headline_match": True,
            "headline_keywords": ["端侧", "本地", "部署", "芯片", "处理器", "算力", "模型", "开源", "DeepSeek", "NPU", "Agent"],
            "question": "下期想看哪个？",
        },
    },
    # V5.2.3：签名档必须带固定日报链接（prepare 会校验 DAILY_URL 在 signature.block 内），
    # 测试 fixture 与真实配置同形，避免规则只在生产生效、测试无感。
    "signature": {"block": "————\n**出品｜集团数字化中心**\n"
                           "[往期合集](https://aicopilot.goldwind.com.cn:3211/latest) | 💬 留言"},
}


def build_md(day, headlines):
    """按 V4.1 版式拼日报正文：今日要紧挂行动标签、来源收进 [details] 折叠块。"""
    parts = [f"# AI 雷达日报｜{day}\n\n## 今日要紧\n"]
    for title, summary in headlines:
        parts.append(
            f"\n### 【了解】{title}\n\n{summary}\n\n**对我们：** 示例判断。\n\n"
            '[details="🟢 来源｜官方 + 独立交叉"]\n'
            "- [来源一](https://example.test/a) · [来源二](https://media.test/b)\n[/details]\n"
        )
    parts.append(
        "\n---\n\n## 金风相关\n\n今日暂无经双源核验的金风相关重要消息。\n\n---\n\n"
        "## 工具与做法\n\n今日暂无新增可复用的工具与做法。\n\n---\n\n## 行业速递\n"
    )
    return "".join(parts)


def main_title(message):
    for line in message.splitlines():
        if line.startswith("**今日最值关注："):
            return line.removeprefix("**今日最值关注：").removesuffix("**").strip()
    return ""


def main_summary(message):
    lines = message.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("**今日最值关注：") and index + 1 < len(lines):
            return lines[index + 1]
    return ""


def flash_titles(message):
    out, in_flash = [], False
    for line in message.splitlines():
        if line == "快讯：":
            in_flash = True
            continue
        if in_flash:
            if line.startswith("• "):
                out.append(line[2:])
            elif line.startswith("💬"):
                break
    return out


class FeishuPushDedupTest(unittest.TestCase):

    def _prepare(self, highlights, groups):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            md = tmp / "report.md"
            md.write_text(build_md("9.11", highlights), encoding="utf-8")
            groups_f = tmp / "groups.json"
            groups_f.write_text(json.dumps({"groups": groups}, ensure_ascii=False), encoding="utf-8")
            profiles_f = tmp / "profiles.json"
            profiles_f.write_text(json.dumps(PROFILES, ensure_ascii=False), encoding="utf-8")
            out = tmp / "out.json"
            txt = tmp / "out.txt"
            pf.prepare(args=type("A", (), {
                "md_path": str(md), "groups": str(groups_f), "profiles": str(profiles_f),
                "state": str(tmp / "state.json"), "output": str(out), "message_output": str(txt),
                "article_url": "https://x/t/511", "topic_id": "511", "issue_id": "AI-RADAR-2026-09-11",
                "report_date": "2026-09-11", "profile": None,
            })())
            return json.loads(out.read_text(encoding="utf-8"))

    def test_three_groups_dedup_deepseek_clash(self):
        """复现 2026-09-11 问题：A、C 群均命中 DeepSeek。修复后三群头条互不重复。"""
        hs = [
            highlight("经营数据预测进入决策闭环"),
            highlight("DeepSeek 正式发布 V4.1 Flash"),
            highlight("Anthropic 发布威胁情报报告"),
            highlight("本地开源模型部署取得进展"),
        ]
        groups = [{"chat_id": c, "name": n, "enabled": True}
                  for c, n in [
                      ("oc_d613cd95638e14c145bba6c87a7123ad", "AI智能体开发平台运营群"),
                      ("oc_52da4aa933ef08de47ec321717039e6b", "KNS统一数据平台"),
                      ("oc_0fb9a0a98ee33b0419f1fce967f9c17b", "本地大模型用户交流群"),
                  ]]
        data = self._prepare(hs, groups)
        titles = [main_title(p["message"]) for p in data["payloads"]]
        # A(命中DeepSeek)=idx1, C(命中DeepSeek但被占)→回退到未被占的最早条(最高法idx0),
        # B(无命中)→早未被占条(Anthropic idx2 或依序), 三群各不相同
        self.assertEqual(len(set(titles)), 3, f"三群头条应互不重复: {titles}")
        self.assertEqual(titles[0], "DeepSeek 正式发布 V4.1 Flash")  # A 优先命中

    def test_b_group_picks_kv_headline(self):
        """B(KNS) 命中算电/数据类标题时优先选中，不被 A 挤占。"""
        hs = [
            highlight("DeepSeek 发布 V4.1 Flash"),
            highlight("高盛：算电协同数据进入决策"),
            highlight("本地开源模型部署取得进展"),
            highlight("高德发布 ABot-Earth"),
        ]
        # 注：V4 摘要不再带透镜标签，C 群必须靠标题本身命中「部署/本地」，否则 fail closed。
        groups = [{"chat_id": c, "name": n, "enabled": True} for c, n in [
            ("oc_d613cd95638e14c145bba6c87a7123ad", "A"),
            ("oc_52da4aa933ef08de47ec321717039e6b", "B"),
            ("oc_0fb9a0a98ee33b0419f1fce967f9c17b", "C"),
        ]]
        data = self._prepare(hs, groups)
        titles = [main_title(p["message"]) for p in data["payloads"]]
        # B(命中 算电/数据/高盛)=idx1; A(命中DeepSeek)=idx0; C(无命中)回退非占最早=Anthropic idx2
        self.assertEqual(len(set(titles)), 3, f"三群头条应互不重复: {titles}")
        self.assertIn("算电", titles[1])  # B 应拿到 KNS 专属头条

    def test_flash_not_duplicate_headlines(self):
        """快讯不应再与其他群已占用的头条重复。"""
        hs = [
            highlight("DeepSeek 发布 V4.1 Flash"),
            highlight("经营数据预测进入决策闭环"),
            highlight("本地开源模型部署取得进展"),
            highlight("OpenAI 收紧广告政策"),
        ]
        groups = [{"chat_id": c, "name": n, "enabled": True} for c, n in [
            ("oc_d613cd95638e14c145bba6c87a7123ad", "A"),
            ("oc_52da4aa933ef08de47ec321717039e6b", "B"),
            ("oc_0fb9a0a98ee33b0419f1fce967f9c17b", "C"),
        ]]
        data = self._prepare(hs, groups)
        for p in data["payloads"]:
            mt = main_title(p["message"])
            for ft in flash_titles(p["message"]):
                self.assertNotEqual(ft, mt, "快讯不应等于本群头条")
        # 各群快讯集合互不包含其他群头条
        titles = [main_title(p["message"]) for p in data["payloads"]]
        flashes = [flash_titles(p["message"]) for p in data["payloads"]]
        for i in range(len(titles)):
            for j in range(len(titles)):
                if i != j:
                    self.assertNotIn(titles[j], flashes[i],
                                     f"{i} 群快讯不应包含 {j} 群头条 {titles[j]}")

    def test_flash_dedup_across_groups_when_candidates_enough(self):
        """候选足够时，三群快讯也应跨群互不重复。"""
        hs = [
            highlight("DeepSeek 发布 V4.1 Flash"),
            highlight("高盛：算电协同数据进入决策"),
            highlight("本地芯片模型部署取得进展"),
            highlight("OpenAI 收紧广告政策"),
            highlight("NVIDIA 开源 Nemotron"),
            highlight("Google 开源 TimesFM-3 预测模型"),
            highlight("Mantis 智能体安全框架"),
            highlight("HOP 工作流事务锁"),
            highlight("Astra 机器化编码实验"),
        ]
        groups = [{"chat_id": c, "name": n, "enabled": True} for c, n in [
            ("oc_d613cd95638e14c145bba6c87a7123ad", "A"),
            ("oc_52da4aa933ef08de47ec321717039e6b", "B"),
            ("oc_0fb9a0a98ee33b0419f1fce967f9c17b", "C"),
        ]]
        data = self._prepare(hs, groups)
        all_flashes = [ft for payload in data["payloads"] for ft in flash_titles(payload["message"])]
        self.assertEqual(len(all_flashes), len(set(all_flashes)), f"三群快讯应互不重复: {all_flashes}")

    def test_group_specific_angle_labels_replace_source_tag(self):
        """日报原标签不可直接复用；三群摘要必须换成唯一的画像视角标签。"""
        hs = [
            highlight("智能体工作流模型发布", "事实摘要A。"),
            highlight("经营数据预测进入决策闭环", "事实摘要B。"),
            highlight("本地开源模型推理成本下降", "事实摘要C。"),
        ]
        groups = [{"chat_id": c, "name": n, "enabled": True} for c, n in [
            ("oc_d613cd95638e14c145bba6c87a7123ad", "A"),
            ("oc_52da4aa933ef08de47ec321717039e6b", "B"),
            ("oc_0fb9a0a98ee33b0419f1fce967f9c17b", "C"),
        ]]
        data = self._prepare(hs, groups)
        expected = ["【能力复用】", "【数据价值】", "【部署门槛】"]
        summaries = [main_summary(payload["message"]) for payload in data["payloads"]]
        for payload, summary, tag in zip(data["payloads"], summaries, expected):
            self.assertTrue(summary.startswith(tag), (summary, tag))
            self.assertNotIn("【部署边界】", summary)
            self.assertEqual(payload["angle_label"], tag[1:-1])
            self.assertEqual(payload["main_summary"], summary)
            self.assertGreater(payload["headline_match_score"], 0)
        self.assertEqual(len({re.match(r"^【[^】]+】", s).group(0) for s in summaries}), 3)

    def test_global_assignment_preserves_unique_match(self):
        """全局分配应保留后序群唯一匹配项，不能被前序群贪心抢走。"""
        custom = json.loads(json.dumps(PROFILES, ensure_ascii=False))
        custom["groups"]["oc_d613cd95638e14c145bba6c87a7123ad"]["headline_keywords"] = ["共享", "智能体"]
        custom["groups"]["oc_52da4aa933ef08de47ec321717039e6b"]["headline_keywords"] = ["数据"]
        custom["groups"]["oc_0fb9a0a98ee33b0419f1fce967f9c17b"]["headline_keywords"] = ["共享"]
        highlights = [
            highlight("共享智能体模型"),
            highlight("智能体工作流"),
            highlight("经营数据预测"),
        ]
        profiles = [custom["groups"][chat_id] for chat_id in [
            "oc_d613cd95638e14c145bba6c87a7123ad",
            "oc_52da4aa933ef08de47ec321717039e6b",
            "oc_0fb9a0a98ee33b0419f1fce967f9c17b",
        ]]
        indices = pf.assign_headlines(highlights, profiles)
        self.assertEqual(indices, [1, 2, 0])

    def test_sep16_salesforce_business_data_matches_kns(self):
        """客户/机会/管线等 CRM 数据属于 KNS 业务数据决策画像，三群应可唯一分配。"""
        profiles_path = Path(__file__).resolve().parents[1] / "references" / "feishu-group-profiles.json"
        production = json.loads(profiles_path.read_text(encoding="utf-8"))
        highlights = [
            highlight("飞书发布 8.0 原生融合豆包，推出团队智能体"),
            highlight("阶跃星辰发布 StepAudio 3 系列语音大模型"),
            highlight("Anthropic 推出 Salesforce in Claude 插件，内置 37 项销售技能",
                      "客户、机会与管线数据进入 Claude，并复用既有权限。"),
            highlight("OpenAI 考虑新一轮私募融资"),
            highlight("GitHub 推出 Agentic Workflows 技术预览"),
        ]
        chat_ids = [
            "oc_d613cd95638e14c145bba6c87a7123ad",
            "oc_52da4aa933ef08de47ec321717039e6b",
            "oc_0fb9a0a98ee33b0419f1fce967f9c17b",
        ]
        profiles = [production["groups"][chat_id] for chat_id in chat_ids]
        indices = pf.assign_headlines(highlights, profiles)
        titles = [highlights[index][0] for index in indices]
        self.assertEqual(len(set(titles)), 3)
        self.assertIn("Salesforce", titles[1])
        self.assertTrue(all(pf.keyword_score(highlights[index][0], profile.get("headline_keywords"), highlights[index][1]) > 0
                            for profile, index in zip(profiles, indices)))

    def test_required_profile_match_fails_closed(self):
        """某群无相关候选时必须阻断，禁止按日报顺序硬凑无关头条。"""
        hs = [
            highlight("智能体工作流模型发布"),
            highlight("OpenAI 机器人模型更新"),
            highlight("端侧开源模型部署"),
        ]
        groups = [{"chat_id": c, "name": n, "enabled": True} for c, n in [
            ("oc_d613cd95638e14c145bba6c87a7123ad", "A"),
            ("oc_52da4aa933ef08de47ec321717039e6b", "B"),
            ("oc_0fb9a0a98ee33b0419f1fce967f9c17b", "C"),
        ]]
        with self.assertRaisesRegex(ValueError, "画像.*差异化门禁阻断"):
            self._prepare(hs, groups)

    def test_kns_no_match_can_fallback_to_energy_flash(self):
        """2026-09-21 复盘：5条大事件全为大模型/融资性质时 B(KNS) 数据价值群全 miss，
        此前 fail-closed 阻断推送。修复后能源业务板块可并入头条候选池兜底，B 群取能源头条。"""
        hs = [
            highlight("阶跃星辰发布 Step 5 Preview"),
            highlight("OpenAI 开启 1.2 万亿融资洽谈"),
            highlight("特朗普组建美人工智能部队"),
            highlight("智谱上线 GLM-5.3-FlashX"),
        ]
        # 能源板块（快讯候选，含 KNS 画像关键词「预测/数据」）
        energy = highlight("云鼎科技×华为发布渤中海上风电多场景 AI 功率预测产品",
                           "超短期预测精度提升15%+，准确率96%+，台风提前72h预警，风电功率数据进入预测闭环。")
        builder = highlight("MiniMax Code CLI 以 MIT 协议开源")
        practice = highlight("制造业 AI 重点场景应用普及率达 34.2%")
        groups = [{"chat_id": c, "name": n, "enabled": True} for c, n in [
            ("oc_d613cd95638e14c145bba6c87a7123ad", "A"),
            ("oc_52da4aa933ef08de47ec321717039e6b", "B KNS"),
            ("oc_0fb9a0a98ee33b0419f1fce967f9c17b", "C"),
        ]]
        # 直接构造与 prepare 相同的兜底路径：首轮 assign 抛错→并入 flash 候选重试
        profiles_path = Path(__file__).resolve().parents[1] / "references" / "feishu-group-profiles.json"
        production = json.loads(profiles_path.read_text(encoding="utf-8"))
        chat_ids = [
            "oc_d613cd95638e14c145bba6c87a7123ad",
            "oc_52da4aa933ef08de47ec321717039e6b",
            "oc_0fb9a0a98ee33b0419f1fce967f9c17b",
        ]
        profiles = [production["groups"][chat_id] for chat_id in chat_ids]
        # 首轮：4条大事件，KNS(B) 全 miss
        combined = list(hs)
        try:
            pf.assign_headlines(combined, profiles)
            self.fail("首轮 assign_headlines 应因 KNS 全 miss 失败")
        except ValueError:
            pass
        # 兜底：并入能源/Builder/实践业务板块候选后重试
        for flash in (energy, builder, practice):
            pf.append_unique_candidate(combined, flash[0], flash[1])
        indices = pf.assign_headlines(combined, profiles)
        titles = [combined[i][0] for i in indices]
        self.assertEqual(len(set(titles)), 3, f"三群头条应互不重复: {titles}")
        # B(KNS) 应拿到能源功率预测头条（命中「预测/数据」画像）
        self.assertIn("功率预测", titles[1])


class AccumulationPushTest(unittest.TestCase):
    """累积出刊推送标注（V3.9.0：权威来源改为检索清单 publication_metadata）。

    非工作日不发布、候选逐日累积，到工作日统一出刊（30 为上限）。推送文案须
    显式标注累积期，避免长假后首期把多日累积说成「今日/昨日」，口径失实。
    正文已不再承载累积行（用户要求资讯清单退出正文），故本组用例改为：
      ①metadata 解析（现役权威）②正文遗留行兼容 ③端到端推送标注。
    """

    def _prepare(self, md_tail, search_log=None, allow_placeholder=True):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            md = tmp / "report.md"
            md.write_text(
                build_md("9.21", [
                    ("DeepSeek 发布 V4.1 Flash", "示例摘要甲。"),
                    ("算电协同数据进入决策闭环", "示例摘要乙。"),
                    ("本地开源模型部署取得进展", "示例摘要丙。"),
                    ("Anthropic 发布威胁情报报告", "示例摘要丁。"),
                ]) + md_tail,
                encoding="utf-8")
            groups_f = tmp / "groups.json"
            groups_f.write_text(json.dumps({"groups": [
                {"chat_id": "oc_d613cd95638e14c145bba6c87a7123ad", "name": "A", "enabled": True},
                {"chat_id": "oc_52da4aa933ef08de47ec321717039e6b", "name": "B", "enabled": True},
                {"chat_id": "oc_0fb9a0a98ee33b0419f1fce967f9c17b", "name": "C", "enabled": True},
            ]}, ensure_ascii=False), encoding="utf-8")
            profiles_f = tmp / "profiles.json"
            profiles_f.write_text(json.dumps(PROFILES, ensure_ascii=False), encoding="utf-8")
            search_log_f = None
            if search_log is not None:
                search_log_f = tmp / "search.json"
                search_log_f.write_text(json.dumps(search_log, ensure_ascii=False), encoding="utf-8")
            out = tmp / "out.json"
            pf.prepare(args=type("A", (), {
                "md_path": str(md), "groups": str(groups_f), "profiles": str(profiles_f),
                "state": str(tmp / "state.json"), "output": str(out),
                "message_output": str(tmp / "out.txt"),
                "article_url": "https://x/t/512", "topic_id": "512",
                "issue_id": "AI-RADAR-2026-09-21", "report_date": "2026-09-21", "profile": None,
                "search_log": str(search_log_f) if search_log_f else None,
                "allow_placeholder": allow_placeholder,
            })())
            return json.loads(out.read_text(encoding="utf-8"))

    def test_parse_accumulation_variants(self):
        """正文遗留行多种写法仍能解析（历史期次兼容）。"""
        for text, span, count in [
            ("> 📅 **累积出刊**：本期为 **9/19–9/20**（共 **30** 条）", "9/19–9/20", 30),
            ("> 📅 累积出刊：本期为 10/1–10/7（共 28 条）", "10/1–10/7", 28),
            ("> 📅 **累积出刊**：本期为 **9/19-9/20**（共 **3** 条）", "9/19–9/20", 3),
        ]:
            parsed = pf.parse_accumulation(text)
            self.assertTrue(parsed["is_accumulated"], text)
            self.assertEqual(parsed["span"], span)
            self.assertEqual(parsed["count"], count)

    def test_metadata_is_authoritative_source(self):
        """V3.9.0：检索清单 publication_metadata 为现役权威来源。"""
        meta = {"is_accumulated": True, "span": "9/19-9/20", "count": 30}
        parsed = pf.parse_accumulation_from_metadata(meta)
        self.assertTrue(parsed["is_accumulated"])
        self.assertEqual(parsed["span"], "9/19–9/20")   # 连接符归一
        self.assertEqual(parsed["count"], 30)
        # 嵌套写法同样支持
        nested = pf.parse_accumulation_from_metadata({"publication_metadata": meta})
        self.assertEqual(nested["count"], 30)
        # 非累积
        self.assertFalse(pf.parse_accumulation_from_metadata({"is_accumulated": False})["is_accumulated"])
        self.assertFalse(pf.parse_accumulation_from_metadata(None)["is_accumulated"])

    def test_resolve_prefers_metadata_over_body(self):
        """metadata 与正文并存时以 metadata 为准。"""
        body = "> 📅 **累积出刊**：本期为 **1/1–1/2**（共 **2** 条）"
        resolved = pf.resolve_accumulation(body, None, {"is_accumulated": True, "span": "9/19–9/20", "count": 30})
        self.assertEqual(resolved["count"], 30)
        self.assertEqual(resolved["span"], "9/19–9/20")

    def test_extract_from_search_log_file(self):
        """从检索清单文件读取 publication_metadata（不依赖正文）。"""
        log = {"report_date": "2026-09-21", "publication_metadata": {
            "is_accumulated": True, "span": "9/19–9/20", "count": 30}}
        resolved = pf.resolve_accumulation("", None, None)  # 无 metadata 时按日常
        self.assertFalse(resolved["is_accumulated"])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "s.json"
            p.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
            got = pf.load_publication_metadata(str(p))
            self.assertIsNotNone(got)
            self.assertEqual(got["count"], 30)

    def test_no_accumulation_line_means_daily(self):
        """无累积信息时按日常单期处理，不误标。"""
        parsed = pf.parse_accumulation("本期精编之外的全部候选已同步至知识库：\nhttps://example.com")
        self.assertFalse(parsed["is_accumulated"])
        self.assertEqual(parsed["span"], "")
        self.assertIsNone(parsed["count"])

    def test_accumulated_message_has_marker_and_note(self):
        """累积出刊：标题带·累积出刊，正文含累积期说明行（来源=metadata）。"""
        log = {"publication_metadata": {"is_accumulated": True, "span": "9/19–9/20", "count": 30}}
        data = self._prepare("\n正文结束。\n", search_log=log)
        self.assertTrue(data["accumulation"]["is_accumulated"])
        self.assertEqual(data["accumulation"]["count"], 30)
        for p in data["payloads"]:
            msg = p["message"]
            self.assertIn("【AI雷达｜9.21·累积出刊】", msg, msg)
            self.assertIn("📅 本期为 9/19–9/20 累积出刊（共 30 条）", msg, msg)
            self.assertTrue(p["accumulated"])
            self.assertEqual(p["accumulation_count"], 30)

    def test_daily_message_has_no_accumulation_marker(self):
        """日常单期：标题不带·累积出刊，正文无累积说明行。"""
        data = self._prepare("\n正文结束。\n")
        self.assertFalse(data["accumulation"]["is_accumulated"])
        for p in data["payloads"]:
            msg = p["message"]
            self.assertIn("【AI雷达｜9.21】", msg, msg)
            self.assertNotIn("累积出刊", msg, msg)
            self.assertFalse(p["accumulated"])


class PlaceholderDraftTest(unittest.TestCase):
    """V3.9.0：占位链接草稿（论坛确认前预生成、发送前须回填真实链接）。"""

    def _args(self, tmp, md, groups_f, profiles_f, url, topic, allow):
        return type("A", (), {
            "md_path": str(md), "groups": str(groups_f), "profiles": str(profiles_f),
            "state": str(tmp / "state.json"), "output": str(tmp / "out.json"),
            "message_output": str(tmp / "out.txt"),
            "article_url": url, "topic_id": topic,
            "issue_id": "AI-RADAR-2026-09-21", "report_date": "2026-09-21", "profile": None,
            "search_log": None, "allow_placeholder": allow,
        })()

    def _fixture(self, tmp):
        md = tmp / "report.md"
        md.write_text(
            build_md("9.21", [
                ("DeepSeek 发布 V4.1 Flash", "示例摘要甲。"),
                ("算电协同数据进入决策闭环", "示例摘要乙。"),
                ("本地开源模型部署取得进展", "示例摘要丙。"),
                ("Anthropic 发布威胁情报报告", "示例摘要丁。"),
            ]),
            encoding="utf-8")
        groups_f = tmp / "groups.json"
        groups_f.write_text(json.dumps({"groups": [
            {"chat_id": "oc_d613cd95638e14c145bba6c87a7123ad", "name": "A", "enabled": True},
            {"chat_id": "oc_52da4aa933ef08de47ec321717039e6b", "name": "B", "enabled": True},
            {"chat_id": "oc_0fb9a0a98ee33b0419f1fce967f9c17b", "name": "C", "enabled": True},
        ]}, ensure_ascii=False), encoding="utf-8")
        profiles_f = tmp / "profiles.json"
        profiles_f.write_text(json.dumps(PROFILES, ensure_ascii=False), encoding="utf-8")
        return md, groups_f, profiles_f

    def test_placeholder_blocked_without_flag(self):
        """未加 --allow-placeholder 时，占位链接必须被拒绝。"""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            md, g, p = self._fixture(tmp)
            with self.assertRaises(ValueError) as cm:
                pf.prepare(args=self._args(tmp, md, g, p, "<FORUM_URL_PENDING>", "PENDING", False))
            self.assertIn("占位", str(cm.exception))

    def test_placeholder_allowed_for_draft(self):
        """加 --allow-placeholder 时可出草稿，并标记 placeholder。"""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            md, g, p = self._fixture(tmp)
            pf.prepare(args=self._args(tmp, md, g, p, "<FORUM_URL_PENDING>", "PENDING", True))
            out = json.loads((tmp / "out.json").read_text(encoding="utf-8"))
            self.assertTrue(out["placeholder"])
            self.assertEqual(out["placeholder_url"], "<FORUM_URL_PENDING>")

    def test_real_url_not_flagged_placeholder(self):
        """真实链接不触发占位标记。"""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            md, g, p = self._fixture(tmp)
            pf.prepare(args=self._args(tmp, md, g, p, "https://x/t/512", "512", False))
            out = json.loads((tmp / "out.json").read_text(encoding="utf-8"))
            self.assertFalse(out["placeholder"])


class ProfileSummarySuffixTest(unittest.TestCase):
    """V3.8.7 回归：群专属价值落点不得被截断（2026-09-23 KNS 群实测教训）。"""

    PROFILE = {
        "angle_label": "数据价值",
        "angle_suffix": "对KNS更值得关注的是：业务数据能否进入分析、预测和生产决策闭环。",
    }

    # 与 2026-09-23 能源条目同构：首句很短、关键数据在后续长句里
    LONG_FACTUAL = (
        "在 2026 年度风电场对标中，远景能源有 19 座风场获评 5A 级。"
        "其自研气象大模型在本月的 WeatherBench2 评测中进入第一梯队，"
        "已为全国 1823 个风电场、1216 个光伏场站、共计 332GW 新能源资产提供预测服务；"
        "能源大模型则通过风速、功率、电价三维曲面控制方案响应价格信号。"
        "据披露，其电价预测准确率达 80%—90%，年收益高出市场平均 10%。"
    )

    def test_suffix_survives_oversized_factual(self):
        """正文超长时，群专属落点仍须完整保留（原实现会被整体截掉）。"""
        out = pf.rewrite_summary_for_profile(self.LONG_FACTUAL, self.PROFILE)
        self.assertTrue(out.startswith("【数据价值】"), out)
        self.assertTrue(out.endswith(self.PROFILE["angle_suffix"]), out)

    def test_oversized_result_within_limit(self):
        """整体仍受 130 字上限约束。"""
        out = pf.rewrite_summary_for_profile(self.LONG_FACTUAL, self.PROFILE)
        self.assertLessEqual(len(out), 130, out)

    def test_no_double_punctuation_before_suffix(self):
        """截断后不得出现「…。」双标点。"""
        out = pf.rewrite_summary_for_profile(self.LONG_FACTUAL, self.PROFILE)
        self.assertNotIn("…。", out, out)

    def test_short_factual_keeps_full_structure(self):
        """正文未超长时保持「标签+正文。+落点」结构。"""
        out = pf.rewrite_summary_for_profile("远景能源 19 座风场获评 5A 级", self.PROFILE)
        self.assertEqual(out, "【数据价值】远景能源 19 座风场获评 5A 级。" + self.PROFILE["angle_suffix"])

    def test_missing_suffix_allowed_no_suffix_appended(self):
        """V3.9.1：画像未配置 angle_suffix 时优雅降级——不追加落点、不报错、仍守 130 字。"""
        out = pf.rewrite_summary_for_profile(self.LONG_FACTUAL, {"angle_label": "数据价值"})
        self.assertTrue(out.startswith("【数据价值】"), out)
        self.assertNotIn("更值得关注", out, out)
        self.assertLessEqual(len(out), 130, out)
        self.assertNotIn("…。", out, out)

    def test_short_factual_no_suffix_ends_with_period(self):
        """无落点且正文未超长时，以句号收尾、不出现空落点。"""
        out = pf.rewrite_summary_for_profile("远景能源 19 座风场获评 5A 级", {"angle_label": "数据价值"})
        self.assertEqual(out, "【数据价值】远景能源 19 座风场获评 5A 级。")

    def test_missing_label_fails_closed(self):
        """画像缺 angle_label 时仍须显式报错（fail closed）。"""
        with self.assertRaises(ValueError):
            pf.rewrite_summary_for_profile(self.LONG_FACTUAL, {"angle_suffix": "x"})


if __name__ == "__main__":
    unittest.main(verbosity=2)