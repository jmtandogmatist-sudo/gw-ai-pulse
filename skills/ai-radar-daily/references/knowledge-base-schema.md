# AI 雷达日报知识库结构化存储规范

## 1. 目标与边界

本规范把每期日报拆成可追溯、可修订、可按日期/类型/主体/行业检索的五张逻辑表：`Issues`、`Events`、`Sources`、`Judgments`、`Runs`。本地 JSON knowledge package 是当前可信交换格式；飞书 Base 仅是远端承载层。在知识库节点或 Base ACL 未开通前，不创建远端资源、不伪造同步成功。

设计原则：

- **事实、来源、判断分离**：事件事实写入 `Events`，来源证据写入 `Sources`，编辑判断和复核结论写入 `Judgments`。
- **稳定主键优先**：所有同步以本规范主键查重，再决定 create/update；禁止仅按标题或 Base `record_id` 判断同一实体。
- **完整历史存库、日报只展示变化**：日报正文只呈现本期新增或状态发生变化的事件/判断；知识库保留首次出现、历次修订、来源和运行审计信息，不因日报未重复展示而删除历史。
- **发布后可审计**：已发布期次不覆盖旧修订；同一 `issue_id` 的修订通过 `revision_version` 递增并保留修订原因。远端实现若不能保留多版本，至少在 `Runs` 中保存每次 package SHA 与修订元数据。
- **最小权限与显式写入**：同步默认 dry-run；只有配置完整、`enabled=true` 且显式 `--confirm` 时允许以 user 身份写入。

## 2. 命名与类型约定

- 日期：ISO 8601 `YYYY-MM-DD`。
- 时间：带时区 ISO 8601 datetime，例如 `2026-09-03T10:00:00+08:00`。
- 枚举：字符串；飞书中建议建单选字段。
- 多值：JSON 中为数组；飞书中可映射为多选或文本 JSON，首版以可逆为准。
- JSON：结构化对象；飞书不支持对象时保存为规范化 JSON 文本。
- SHA：小写 64 位 SHA-256。
- `lens` 固定为 `价值层移动`、`部署边界`、`能力阈值`、`业务距离`、`N/A` 之一。**V4 起正文不再输出透镜标签，新期次一律写 `N/A`**；四透镜取值仅保留给 ≤2026-10-21 的历史期次。
- `source_level` 建议使用 `T0`、`T1`、`T2`、`T3`、`T4`、`UNKNOWN`。

## 3. Issues（期次）

一行代表一个日报逻辑期次。修订仍使用同一 `issue_id`，由 `revision_version` 和 `package_sha256` 区分版本。

| 主键 | 字段名 | 类型 | 必填 | 说明 |
|---|---|---:|:---:|---|
| 是 | `issue_id` | string | 是 | 调用方显式提供的稳定期次 ID；建议 `airadar-YYYY-MM-DD` |
| 否 | `report_date` | date | 是 | 日报归属日期 |
| 否 | `weekday` | string | 是 | 中文周几，如“星期四” |
| 否 | `timezone` | string | 是 | IANA 时区，如 `Asia/Shanghai` |
| 否 | `search_window` | string | 是 | 检索窗口，如工作日 `d2`、周一 `d4` |
| 否 | `title` | string | 是 | 日报标题 |
| 否 | `summary` | string | 否 | 导语或期次摘要 |
| 否 | `publish_status` | enum | 是 | `DRAFT` / `SELF_CHECKED` / `PUBLISHED` / `REVISED` / `FAILED` |
| 否 | `topic_id` | string | 否 | Discourse topic ID |
| 否 | `post_id` | string | 否 | Discourse 主帖 ID；不得与 topic ID 混用 |
| 否 | `forum_url` | url | 否 | 论坛链接 |
| 否 | `lark_status` | enum | 是 | `NOT_CONFIGURED` / `SYNC_PENDING` / `SYNCED` / `FAILED` |
| 否 | `self_check_sha256` | string | 是 | 自查报告绑定的日报正文 SHA |
| 否 | `rule_version` | string | 是 | 生成/校验规则版本，如 `V3.1` |
| 否 | `revision_version` | integer | 是 | 从 1 开始递增 |
| 否 | `revision_reason` | string | 否 | 首版可写 `INITIAL`；修订须写明原因 |
| 否 | `package_sha256` | string | 是 | 对 knowledge package 业务内容计算的 SHA |
| 否 | `created_at` | datetime | 是 | 本地包生成时间 |
| 否 | `updated_at` | datetime | 是 | 当前修订更新时间 |

**去重键**：`issue_id`。版本审计键：`issue_id + revision_version + package_sha256`。

## 4. Events（事件）

一行代表一个可跨期复用的事件、知识卡、Builder 信号、工作实践或深度聚焦主题。

| 主键 | 字段名 | 类型 | 必填 | 说明 |
|---|---|---:|:---:|---|
| 是 | `event_id` | string | 是 | `event_date + 规范化标题` 的稳定哈希；无事件日期时使用板块类型 + 规范化标题 |
| 否 | `issue_id` | string | 是 | 首次/本次收录期次；关联 `Issues.issue_id` |
| 否 | `first_seen_issue_id` | string | 是 | 首次入库期次；跨期更新时保持不变 |
| 否 | `report_date` | date | 是 | 本次日报日期 |
| 否 | `section_type` | enum | 是 | 板块类型（V4.1）：今日要紧、金风相关、工具与做法、行业速递；历史期次保留旧值（今日大事件、AI×能源风电、Builder信号、AI工作实践、深度聚焦） |
| 否 | `event_type` | enum | 是 | 新闻事件、产业事件、研究进展、业务知识卡、Builder信号、工作实践、深度主题等 |
| 否 | `title` | string | 是 | 日报条目标题，去除标题日期包装后仍保留原文 |
| 否 | `normalized_title` | string | 是 | 用于稳定 ID 和辅助去重的规范化标题 |
| 否 | `subjects` | string[] | 否 | 主体/机构/产品；可来自检索候选元数据 |
| 否 | `regions` | string[] | 否 | 国家或地区 |
| 否 | `industries` | string[] | 否 | 行业标签，如 AI、能源、风电、芯片 |
| 否 | `lens` | enum | 是 | 四条常设透镜之一；不适用时为 `N/A` |
| 否 | `importance` | number/string | 否 | 重要度，保留上游数值或枚举口径 |
| 否 | `confidence` | number/string | 否 | 置信度，保留上游数值或枚举口径 |
| 否 | `fact_summary` | string | 是 | 只写可由来源支撑的事实摘要 |
| 否 | `editorial_judgment` | string | 否 | 与事实分离的条目级编辑判断 |
| 否 | `event_date` | date | 否 | 事件发生日期；不得用报道日期替代 |
| 否 | `source_ids` | string[] | 是 | 关联 `Sources.source_id`；至少一个 |
| 否 | `semantic_review_status` | enum | 是 | `PASS` / `FAIL` / `NOT_APPLICABLE` |
| 否 | `semantic_review_facts` | string[] | 否 | 语义复核确认的事实项 |
| 否 | `change_type` | enum | 是 | `NEW` / `UPDATED` / `UNCHANGED` / `RETRACTED` |
| 否 | `revision_version` | integer | 是 | 事件在本期包中的修订版本 |
| 否 | `revision_reason` | string | 否 | 变更或撤回原因 |

**去重键**：首选 `event_id`；辅助冲突检测键为 `event_date + normalized_title`。同一事件跨期继续演进时复用 `event_id`，将本期变化记为 `UPDATED`，完整演进由历次 package/Run 留存。

## 5. Sources（来源）

一行代表一个规范化 URL。相同 URL 在同一 package 内只保留一行，并通过 `event_ids` 关联多个事件。

| 主键 | 字段名 | 类型 | 必填 | 说明 |
|---|---|---:|:---:|---|
| 是 | `source_id` | string | 是 | 规范化 URL 的稳定 SHA 派生 ID |
| 否 | `issue_id` | string | 是 | 本次采集期次；关联 `Issues.issue_id` |
| 否 | `event_ids` | string[] | 是 | 使用该来源的 `Events.event_id` 列表 |
| 否 | `source_url` | url | 是 | 原始来源 URL |
| 否 | `normalized_url` | url | 是 | 去 fragment、去常见跟踪参数、主机名小写后的 URL |
| 否 | `source_name` | string | 否 | 来源名称 |
| 否 | `source_level` | enum | 是 | `T0`—`T4` 或 `UNKNOWN` |
| 否 | `source_excerpt` | string | 否 | 与事件相关的来源摘录；不可用模型改写冒充原文 |
| 否 | `published_at` | datetime/date | 否 | 来源发布时间，不等同于事件日期 |
| 否 | `language` | string | 否 | 来源语言 |
| 否 | `semantic_review_bound` | boolean | 是 | 是否出现在匹配条目的语义复核 `source_urls` 中 |
| 否 | `retrieved_at` | datetime | 否 | 检索或读取时间 |
| 否 | `content_sha256` | string | 否 | 有原文快照时记录内容 SHA |
| 否 | `status` | enum | 是 | `ACTIVE` / `BROKEN` / `REPLACED` / `RETRACTED` |

**去重键**：`normalized_url`（对应主键 `source_id`）。不要按媒体名去重，也不要把转载和原始页合并为同一来源。

## 6. Judgments（判断验证）

一行代表一个可审计的编辑判断、语义复核结果或后续关注台账项。

| 主键 | 字段名 | 类型 | 必填 | 说明 |
|---|---|---:|:---:|---|
| 是 | `judgment_id` | string | 是 | `issue_id + judgment_type + 关联对象 + 内容` 的稳定哈希 ID |
| 否 | `issue_id` | string | 是 | 关联 `Issues.issue_id` |
| 否 | `event_id` | string | 否 | 条目级判断时关联 `Events.event_id` |
| 否 | `source_ids` | string[] | 是 | 支撑该判断的来源；纯流程判断可为空数组 |
| 否 | `judgment_type` | enum | 是 | `EDITOR_RANKING` / `DAILY_THESIS` / `SEMANTIC_REVIEW` / `FOLLOW_UP` |
| 否 | `lens` | enum | 是 | 四条常设透镜或 `N/A` |
| 否 | `statement` | string | 是 | 编辑判断、复核结论或关注项 |
| 否 | `status` | enum | 是 | `PASS` / `FAIL` / `TRACKING` / `HIT` / `NOT_DUE` / `MISSED` |
| 否 | `confidence` | number/string | 否 | 判断置信度 |
| 否 | `proposed_date` | date | 否 | 后续关注提出期 |
| 否 | `review_result` | string | 否 | 台账回看结果 |
| 否 | `facts_checked` | string[] | 否 | 语义复核确认项 |
| 否 | `reviewed_at` | datetime | 否 | 复核时间 |
| 否 | `rule_version` | string | 是 | 生成判断所用规则版本 |
| 否 | `revision_version` | integer | 是 | 判断修订版本 |
| 否 | `revision_reason` | string | 否 | 判断变更原因 |

**去重键**：`judgment_id`。后续关注的长期辅助键为 `normalized statement + proposed_date`，状态变化不得新建另一个逻辑关注项。

## 7. Runs（运行记录）

一行代表一次本地构包或同步运行，保存输入指纹和结构化结果，不存 token/key。

| 主键 | 字段名 | 类型 | 必填 | 说明 |
|---|---|---:|:---:|---|
| 是 | `run_id` | string | 是 | 由 issue、输入 SHA 和运行类型派生，或采用可信 run_state ID |
| 否 | `issue_id` | string | 是 | 关联 `Issues.issue_id` |
| 否 | `run_type` | enum | 是 | `BUILD_PACKAGE` / `SYNC_DRY_RUN` / `SYNC_LARK_BASE` |
| 否 | `started_at` | datetime | 否 | 运行开始时间 |
| 否 | `finished_at` | datetime | 是 | 运行结束时间 |
| 否 | `status` | enum | 是 | `PASS` / `FAIL` / `SYNC_PENDING` / `SYNCED` |
| 否 | `timezone` | string | 是 | 运行时区 |
| 否 | `input_sha256` | JSON | 是 | 日报、检索清单、语义复核、自查报告及可选状态文件的 SHA 映射 |
| 否 | `self_check_sha256` | string | 是 | 自查报告声明的日报 SHA |
| 否 | `package_sha256` | string | 是 | 本次 package 业务内容 SHA |
| 否 | `rule_version` | string | 是 | 规则版本 |
| 否 | `revision_version` | integer | 是 | 对应期次修订版本 |
| 否 | `revision_reason` | string | 否 | 本次运行对应修订原因 |
| 否 | `sync_status` | enum | 是 | `NOT_ATTEMPTED` / `SYNC_PENDING` / `DRY_RUN_READY` / `SYNCED` / `FAILED` |
| 否 | `error_summary` | string | 否 | 失败摘要；不得写入凭据 |
| 否 | `counts` | JSON | 是 | 五表记录数等统计 |

**去重键**：`run_id`。同一输入重复构包应得到相同 `run_id`；真正重跑且输入变化会生成新 ID。

## 7b. Candidates（候选全集）

一行代表一条进入当期候选池的资讯（**含未入选条目**）。**V3.9.0 起这是候选全集的唯一留档位置**——日报正文不再发布「全部资讯（候选全集）」模块，正文也不挂该表链接；`sync_candidates_base.py` 负责同步，`self_check` 对正文出现该模块判 FAIL（历史期次豁免见 `report-template.md` 规则 11）。

| 主键 | 字段名 | 类型 | 必填 | 说明 |
|---|---|---:|:---:|---|
| 是 | `candidate_id` | string | 是 | 候选唯一标识，由 issue 与规范 URL 派生 |
| 否 | `issue_id` | string | 是 | 关联 `Issues.issue_id` |
| 否 | `title` | string | 是 | 候选事件标题 |
| 否 | `decision` | enum | 是 | **单选白名单仅 `SELECTED` / `REJECTED`**；`REJECTED_STALE` 等细分状态由 `normalize_decision()` 归一后写入，禁止直写枚举外的值（会触发 lark-cli `800030005 not_found`） |
| 否 | `source_name` | string | 是 | 来源媒体/机构**单数**字段；与 `source_url` 成对，供 preflight 校验 |
| 否 | `source_url` | string | 是 | 主来源 URL（**单数字段**，非数组），须为浏览器直开可读页 |
| 否 | `source_urls` | JSON | 否 | 多来源 URL 数组 |
| 否 | `event_date` | date | 否 | 事件发生日期，用于窗口门禁核验 |
| 否 | `source_tier` | enum | 否 | T0 官方一手 / T1 央媒 / T2 权威财经科技 / T3 行业垂直 / T4 聚合自媒体（仅作线索） |
| 否 | `reason` | string | 否 | 入选或淘汰理由摘要 |
| 否 | `published` | boolean | 否 | 是否已随某期发布；由 `run.py settle` 结算写入，防止跨期重复发布 |
| 否 | `published_issue_id` | string | 否 | 实际发布所在期次（跨日累积出刊时可能不等于采集期次） |

**去重键**：`candidate_id`。跨期去重与累积池结算均以规范 URL 为事实依据。

## 8. 关系

```text
Issues.issue_id 1 ── N Events.issue_id
Issues.issue_id 1 ── N Judgments.judgment_id
Issues.issue_id 1 ── N Runs.run_id
Issues.issue_id 1 ── N Candidates.issue_id
Events.event_id N ── N Sources.source_id
Events.event_id 1 ── N Judgments.judgment_id
Candidates.candidate_id N ── N Events.event_id   （SELECTED 候选经事件表落库）
```

JSON package 中多对多关系使用 `Events.source_ids` 与 `Sources.event_ids` 双向保存，并要求构包时校验双向一致。飞书 Base 首版可把这些 ID 数组保存为多行文本/JSON；待 ACL 与表结构稳定后再升级为关联字段，稳定 ID 不变。

## 9. 生命周期与冷热策略

1. **草稿期**：构包只接受日期一致、证据非空、自查 `PASS` 且 SHA 匹配的输入。生成 `publish_status=SELF_CHECKED` 的本地包。
2. **发布期**：从可选 `publish_state` 写入 `topic_id`、`post_id`、论坛链接和发布状态；不得把 topic ID 当作 post ID。
3. **修订期**：保持 `issue_id`，递增 `revision_version`，必填 `revision_reason`；事件根据事实变化标记 `UPDATED` 或 `RETRACTED`。
4. **同步期**：配置未完成或禁用时写 `SYNC_PENDING`；dry-run 只生成计划；显式确认并成功写入后才标记 `SYNCED`。
5. **归档期**：不物理删除历史期次、判断和运行记录。来源失效时改为 `BROKEN` 或 `RETRACTED`，保留 URL 和引用关系。

冷热分层建议：

- **热数据（近 90 天）**：Issues、活跃 Events、进行中的 FOLLOW_UP、近期 Sources、所有失败/待同步 Runs；保留在 Base 主视图，支持日常检索和回看。
- **温数据（91—365 天）**：完整结构化记录仍在线，默认视图隐藏；按月/季度视图查询。
- **冷数据（超过 365 天）**：不可变 JSON package 与 SHA 清单归档到受控文件存储；Base 可只保留索引、最终状态和归档位置，但不得删除审计键与修订链。
- **大文本**：来源全文/快照不直接塞入 Base；Base 保存摘录、SHA 和受控存储引用。日报 Markdown 是展示产物，不是唯一事实存储。

## 10. 本地包与同步配置契约

knowledge package 顶层固定包含五表（**V3.9.0 起 `package` 阶段只构建本地包、不触远端**）：

```json
{
  "schema_version": "1.0.0",
  "issues": [],
  "events": [],
  "sources": [],
  "judgments": [],
  "runs": []
}
```

Candidates 表**不在五表本地包内**，由 `sync_candidates_base.py` 在远端同步阶段（stage 8C）单独写入。远端同步的硬前置是**所有飞书目标群均已 SENT**（`run.py sync-knowledge` 门禁），顺序固定为 论坛 → 本地构包 → 飞书推送 → 知识库同步。

`config/runtime.json` 的同步配置建议如下；真实 token 只写运行环境配置，不进入源码或知识包：

```json
{
  "knowledge_sync": {
    "enabled": false,
    "base_token": "",
    "table_ids": {
      "issues": "",
      "events": "",
      "sources": "",
      "judgments": "",
      "runs": "",
      "candidates": ""
    }
  }
}
```

同步顺序固定为 `issues → events → sources → judgments → runs`。每表先按稳定主键精确查询，再把不存在的记录批量 create、已存在的记录批量 update；单批最多 200 条，同一表串行。任何配置缺失、ACL 错误、CLI 失败或结果无法可靠解析时均不得报告完成。