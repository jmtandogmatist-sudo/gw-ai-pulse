# V3.2 运行契约与状态机

## 1. 单一运行契约

```json
{
  "issue_id": "AI-RADAR-2026-09-03",
  "report_date": "2026-09-03",
  "timezone": "Asia/Shanghai",
  "skill_dir": "/Users/sr_jm/gw_llm_ai_pulse/skills/ai-radar-daily",
  "work_dir": "/Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily",
  "env_file": "/Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/.env",
  "mode": "normal"
}
```

| 字段 | 约束 |
|---|---|
| `issue_id` | 必填、稳定、文件系统安全；推荐 `AI-RADAR-YYYY-MM-DD`。修订沿用原值。 |
| `report_date` | 必填 ISO 日期；表示刊期，不自动取系统日期覆盖。 |
| `timezone` | 固定 `Asia/Shanghai`；所有工作日和窗口判断以此为准。 |
| `skill_dir` | 必填绝对路径；必须含 `SKILL.md`、`references/`、`scripts/`、`config/runtime.json`。 |
| `work_dir` | 必填绝对稳定路径；不得使用会话日期临时目录。 |
| `env_file` | 只记录路径，不验证或读取内容；不存在时外部写入阶段阻断。 |
| `mode` | `normal`、`backfill`、`revise`、`resume` 之一。 |

契约一旦写入状态文件，本次运行不得静默变更。确需变更时创建显式 revision 事件并重新过门禁。

## 2. 路径布局

```text
<work_dir>/
├── <issue_id>/
│   ├── run-contract.json
│   ├── run-state.json
│   ├── AI雷达日报_YYYY-MM-DD.md
│   ├── AI雷达日报_YYYY-MM-DD_检索清单.json
│   ├── AI雷达日报_YYYY-MM-DD_证据回执.json
│   ├── AI雷达日报_YYYY-MM-DD_语义复核.json
│   ├── AI雷达日报_YYYY-MM-DD_自查报告.md
│   ├── knowledge-package/
│   │   ├── package.json
│   │   ├── upsert-plan.json
│   │   └── sync-result.json
│   └── errors.jsonl
├── 判断台账.json
└── feishu-push-state.json
```

兼容既有脚本需要在 `<work_dir>` 根目录取文件时，可使用明确绝对路径；状态中仍记录真实路径和哈希。不得移动或覆盖未核验的历史产物。

## 3. 结构化状态 schema

```json
{
  "schema_version": "3.2.0",
  "issue_id": "AI-RADAR-2026-09-03",
  "revision": 0,
  "contract": {
    "report_date": "2026-09-03",
    "timezone": "Asia/Shanghai",
    "skill_dir": "/absolute/path",
    "work_dir": "/absolute/path",
    "env_file": "/absolute/path/.env",
    "mode": "normal"
  },
  "run_status": "RUNNING",
  "current_stage": 1,
  "stages": {
    "1": {"status": "PASS", "started_at": "ISO-8601", "finished_at": "ISO-8601"},
    "2": {"status": "PENDING"},
    "3": {"status": "PENDING"},
    "4": {"status": "PENDING"},
    "5": {"status": "PENDING"},
    "6": {"status": "PENDING"},
    "7": {"status": "PENDING"},
    "8": {"status": "PENDING"}
  },
  "artifacts": {
    "report": {"path": "/absolute/file", "sha256": "64hex", "status": "VERIFIED"}
  },
  "gates": {
    "G1": {"status": "PENDING", "rule_ids": ["R02", "R03", "R04", "R10"]},
    "G2": {"status": "PENDING", "rule_ids": ["R05"]},
    "G3": {"status": "PENDING", "rule_ids": ["R01", "R07", "R08", "R11", "R12", "R14"]},
    "G4": {"status": "PENDING", "rule_ids": ["R06", "R07", "R08", "R09", "R13", "R14"]}
  },
  "forum": {
    "status": "NOT_STARTED",
    "action": null,
    "topic_id": null,
    "post_id": null,
    "url": null,
    "verified_sha256": null
  },
  "knowledge_sync": {
    "status": "NOT_STARTED",
    "package_path": null,
    "remote_ref": null,
    "last_error": null
  },
  "feishu": {
    "review_status": "NOT_REQUESTED",
    "groups": {}
  },
  "confirmations": [],
  "errors": [],
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

枚举：

- 阶段：`PENDING|RUNNING|PASS|FAIL|SKIPPED|BLOCKED`。
- 运行：`RUNNING|WAITING_FORUM_CONFIRMATION|STAGE6_PUBLISHED|PACKAGED|SYNC_PENDING|NEEDS_RECONCILIATION|BLOCKED|FAILED|COMPLETED`。
- 论坛：`NOT_STARTED|READY|WRITING|VERIFYING|VERIFIED|UNCERTAIN|FAILED`。
- 知识库：`NOT_STARTED|PACKAGED|SYNC_PENDING|SYNCING|SYNCED|FAILED`（`PACKAGED` 仅表示本地包已就绪，**不等于**远端成功）。
- 人工审查：`NOT_REQUESTED|PENDING|APPROVED|CHANGES_REQUESTED`。
- 群状态：`PENDING|SENDING|SENT|FAILED|SKIPPED`。
- 推送草稿：`READY_PLACEHOLDER`（论坛确认前预生成、链接为占位符）。

## 4. 原子写要求

1. 在目标同目录生成 `run-state.json.tmp`。
2. 写入完整 JSON、刷新并 `fsync`。
3. 原子替换为 `run-state.json`。
4. 回读并验证 schema、`issue_id`、revision 和 `updated_at`。
5. 状态写失败时停止外部动作；不得只把进度留在对话中。

## 5. 模式输入与状态转移

### normal

输入：当期 `report_date`；应为北京时间工作日。若自动触发落在周末，写 `SKIPPED` 终态并结束。

```text
INITIALIZED → PRECHECKED → RETRIEVED → EVIDENCE_VERIFIED → DRAFTED → CHECKED
→ WAITING_FORUM_CONFIRMATION → FORUM_VERIFIED → PACKAGED
→ FEISHU_SENT → SYNCED → COMPLETED
```

外部可见动作顺序固定为「论坛 → 飞书 → 知识库」（V3.9.0）：

- `PRECHECKED`：搜索前只读预检（二维码/群配置/知识库/服务/凭据键位），只读不写盘。
- `PACKAGED`（stage 7）：**仅本地构包**，可早于飞书推送完成；此时远端知识库状态为 `NOT_STARTED`。
- `FEISHU_SENT`（push 后）：三群全部 SENT 才进入；任一 FAILED 则 `PUSH_FAILED`。
- `SYNCED`（stage 8，`sync-knowledge`）：**硬前置**——论坛 `PUBLISHED` + 所有飞书目标群 `SENT`；
  未达前置写 `BLOCKED`（`FEISHU_NOT_ALL_SENT`），远端失败写 `SYNC_PENDING`，**不得伪造 `SYNCED`**。

占位链接门：`push-draft` 可在 `WAITING_FORUM_CONFIRMATION` 阶段用占位符生成三群草稿；
`push` 发送前校验 `article_url/topic_id` 非占位符且 payload 无占位 token，否则 fail closed。

### backfill

输入：人工明确给出的历史 `report_date`、补发原因和外部动作授权范围。正文、标题和状态必须保留历史刊期；检索窗口以历史时间口径执行并注明补发。

```text
INITIALIZED → BACKFILL_AUTHORIZED → RETRIEVED → ... → COMPLETED
```

缺少明确历史刊期或补发授权时 `BLOCKED`。

### revise

输入：原 `issue_id`、`topic_id`、修订原因、允许修改范围。先回读论坛原帖，取得真实首帖 `post_id` 和 raw，建立 revision 快照；不得整批换稿。

```text
INITIALIZED → SOURCE_RECONCILED → REVISION_DRAFTED → CHECKED
→ WAITING_FORUM_CONFIRMATION → FORUM_VERIFIED → PACKAGED
→ FEISHU_SENT → SYNCED → COMPLETED
```

每次修订 `revision += 1`，保留旧版哈希。既有飞书消息不可被当作已自动修订。

### resume

输入：已有 `issue_id`；其余字段必须来自状态快照并与本次环境一致。逐项验证产物 SHA 和外部回读。

```text
LOAD_STATE → VERIFY_ARTIFACTS → RECONCILE_EXTERNAL_STATE → NEXT_PENDING_STAGE
```

- 上次停在纯本地阶段且哈希一致：从首个 `PENDING/FAIL` 阶段继续。
- 上次停在 `WRITING/VERIFYING/SENDING/SYNCING`：进入 `NEEDS_RECONCILIATION`，先回读外部系统。
- 哈希不一致：`BLOCKED`，要求人工判断采用哪个版本。
- 已 `COMPLETED`：默认只读，不重复执行。
