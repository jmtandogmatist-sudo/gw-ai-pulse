# V3.4 复盘与回填清单（阶段 9）

## 1. 定位与触发

- 复盘是**阶段 9**，在阶段 8 之后执行：
  - 正常路径：飞书推送完成、run_status=COMPLETED 后执行**完整复盘**（五维全查）。
  - 阻断路径：当日停于 `WAITING_HUMAN_REVIEW` / `SYNC_PENDING` / `BLOCKED` / `FAILED` 时执行**部分复盘**（时间窗、出刊、质量照常；数据、推送维度如实标注未完成）。
- 复盘是**只读核验**：只读取既有产物（检索清单、自查报告、run-state、publish-state），不修改已验证正文、不重写门禁结果、不重跑发布。
- 复盘 FAIL 不回滚运行、不改变 run_status；如实记录 fail_reason 并列入 `improve`。
- 机器化边界：本清单暂由执行 Agent 手工执行；`run.py recap` 子命令与时间窗比对脚本属独立脚本工作流，落地前不得虚构"已脚本化"。
- **零产物失败的诊断路径（2026-09-09 首验沉淀）**：若期次目录/run-state 完全不存在，先查 `~/.workbuddy/logs/automation.log` 当日 `run start` / `run failed` 记录——平台预检失败（如"连接器均未连接成功：feishu"）发生在写盘之前，产物侧无迹可查，只有调度日志可定位根因。此类失败归因于平台侧，不属于 skill 流程缺陷。

## 2. 预期增量窗口推导（复盘基准）

复盘前先推导本期**预期**的事件日期集合，再与实际产物比对：

| 执行日 | 预期增量窗口（事件日期集合） |
|---|---|
| 周二至周五（report_date=D） | `{D-1, D}` |
| 周一（report_date=D） | `{D-3, D-2, D-1, D}`（上周五至周一） |
| 前序执行点缺失（暂停/中断/节假日跳跑） | `{上次成功执行点日期+1 .. D}`，**顺延覆盖**，不按星期几硬套 |

- 推导依据：`references/retrieval-strategy.md` §2 执行点增量口径；跳跑顺延依据其 §1。
- 上次成功执行点以 run-state（`COMPLETED` 期次）或 work_dir 已发布期次目录为准，不得凭记忆。

## 3. 五维复盘清单

| 维度 | 核验项 | 数据源 | 结论字段 |
|---|---|---|---|
| ⏱️ 时间窗 | 三问核验（§4） | 检索清单 JSON | `recap.window` |
| ✅ 出刊 | 准点触发、G1-G4 门禁结果、论坛发布回读、飞书推送/知识库同步状态 | run-state、publish-state | `recap.publish` |
| 🔍 质量 | 语义复核与脚本自查是否双 PASS；发布后有无人工补正/勘误 | 自查报告、语义复核 JSON | `recap.quality` |
| 📊 数据 | 论坛浏览/回复、群触达（可得则记，不可得标 null，不猜测） | 论坛回读、飞书状态 | `recap.impact` |
| 🧠 改进 | 本期 1 条最值得下期记住的教训或调整；是否需要回填 skill/模板/信源清单 | 复盘综合判断 | `recap.improve` |

## 4. 时间窗核验三问（重点）

### 问 1：检索回看够不够
- 检索清单 `window` 字段（周中 `d2` / 周一 `d4`）是否覆盖预期窗口跨度；跳跑顺延场景下回看参数是否相应加宽。
- `queries` 非空且实际执行；`official_sources_checked` 有真实记录，且**每条 `checked` 均为 `true`**（语义＝已执行核查，非已采信来源；负面结论写在各自的 `latest_update`，见 `retrieval-strategy.md` 检索清单 schema 下的注释）。

### 问 2：事件日期落窗
- 逐条核对 `candidates` 中 `decision=SELECTED` 的 `event_date`：全部落在预期日期集合内。
- 越界者必须满足以下之一，否则记 FAIL：
  - 已标 `REJECTED_STALE`（未进入正文）；
  - 正文按模板标注"跟踪报道"+ 原始日期；
  - `--allow-same-day` 场景（当日已发生事件，有明确依据）。

### 问 3：无漏收无误杀
- `recall_review.status=PASS` 且 `major_missed_events=[]`。
- 抽查 `REJECTED_STALE` 条目：无把预期窗口内的新事件误判为旧闻。
- 对照 `official_sources_checked` 的 `latest_update`：窗口内官方重大更新是否均有对应候选或明确淘汰原因。

### 判定
- 三问全 PASS → `window.pass=true`。
- 任一 FAIL → `window.pass=false` + `fail_reason` 列明是哪一问、哪些条目；需要补扫的建议写入 `improve.backlog`，当期已发布内容不回改（勘误走 `revise` 模式）。

## 5. recap.json 结构

保存路径：`<work_dir>/<issue_id>/recap.json`（与其他产物同目录，原子写入）。

```json
{
  "issue_id": "AI-RADAR-YYYY-MM-DD",
  "report_date": "YYYY-MM-DD",
  "recap_at": "ISO-8601",
  "window": {
    "expected_dates": ["YYYY-MM-DD"],
    "window_basis": "weekday|catchup",
    "freshness": "d2|d4",
    "selected_in_window": 0,
    "selected_out_of_window": 0,
    "stale_rejections": 0,
    "pass": true,
    "fail_reason": "",
    "notes": ""
  },
  "publish": {
    "triggered_on_time": true,
    "gates": {"G1": "PASS", "G2": "PASS", "G3": "PASS", "G4": "PASS|SKIPPED"},
    "forum": "PUBLISHED|PENDING|FAILED",
    "knowledge_sync": "SYNCED|SYNC_PENDING",
    "feishu": "SENT|PENDING|FAILED",
    "pass": true
  },
  "quality": {
    "semantic_review": "PASS",
    "script_self_check": "PASS",
    "post_publish_corrections": 0,
    "pass": true
  },
  "impact": {
    "topic_views": null,
    "replies": null,
    "notes": ""
  },
  "improve": {
    "lesson": "一句话，必填，不得为空泛表述",
    "action": "无 | 具体调整项",
    "backlog": []
  }
}
```

- 所有计数与状态必须来自产物回读，不得凭印象填写；不可得字段用 `null` 并在 `notes` 说明。

## 6. 回填与汇报

- recap.json 写入后，将 `{"recap": {"file": "<绝对路径>", "sha256": "<摘要>", "window_pass": true|false}}` 合并进 run-state（只增改 recap 键，不动其他阶段状态）。
- 完成汇报末尾附 **3 行复盘摘要**：
  1. 今日产出：是否准点出刊、发布/推送/同步状态；
  2. 今日风险：时间窗三问结果、有无存疑项及处理；
  3. 今日改进：1 条教训或调整。
- 周维度：近 5 期 recap.json 可汇总为周复盘素材（题材冷热、时间窗稳定性、运行健康度），供 ai-radar-weekly 或人工周报引用。

## 7. 边界

- 复盘不作为 G1-G4 之外的第五道门禁；不阻断已完成的发布。
- 不因复盘删改已验证产物；发现正文错误时提示用户走 `revise`。
- 影响数据（浏览/回复）只记录、不外显到正文；复盘聚焦"下期怎么更好"，不晒数字。
