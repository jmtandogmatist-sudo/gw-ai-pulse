# V5.0 检索与召回策略

## 1. 为什么在固定执行点运行（执行点增量口径，V3.3.0 起）

**核心目标：本期正文收录「上一执行点之后 → 当前执行点」的增量新闻，而非按自然日回看。**

自动化的价值在于每日固定时点触发时，能拿到"上一次执行（前一天同一时点）之后到今天同一时点之前"新发生的、尚未被任何期收录的事件——这才是"当天最新数据"的真实含义。

- **当前执行时点为每日 08:30**（`FREQ=DAILY;BYHOUR=8;BYMINUTE=30`，含周末）。**排程真源是自动化配置本身**（见 SKILL.md §5 检索/发布解耦表）；本文档只解释口径，不承担排程真源职责——2026-09-08 至 10 月初曾为 10:00，后改为 08:30，但本文档未同步而长期误记，2026-11-04 更正。
- 检索回看（freshness）略宽于正文口径，用于提高召回；但事件日期核验与跨期去重负责把召回收敛到"上一个执行点之后"的真实增量，**不跨期刊发、不重复**。
- **已知取舍**：08:00–09:30 是国内晨间聚合稿、企业更新和补充报道的集中窗口。执行点右端点为当前执行时点，故当日 08:30 之后的晨间线索会落到**次日期次**；若需当日覆盖完整晨间窗口，须把触发时点后移到 09:30 之后（**未采纳，属口径选择**）。
- 若某工作日未执行（如暂停/中断），下一次执行的增量窗口自动顺延覆盖"上次执行至今"的间隔（例如 9-05/9-06 未跑，9-07 周一窗口即覆盖 9-03 之后到 9-07 的全部事件，避免漏收）。

## 2. 正文时间窗（执行点增量 vs 自然日）

| 执行日 | 上一执行点 | 本期允许收录的事件日期（增量窗口） |
|---|---|---|
| 周二至周五 | 前一天 10:00 | `{昨天, 今天}`（允许 `report_date` 与 `report_date-1`） |
| 周一 | 上周五 10:00 | `{上周五, 周六, 周日, 周一}`（`report_date-3` ~ `report_date`） |

- 正文标题只标注「M月D日」日粒度，不标时刻，故增量窗口映射为上述**日期集合**。与旧「自然日口径（仅收前一日）」的核心差异：**执行当日（report_date）默认纳入允许集合**，兑现"收上次执行后的最新数据"。
- 搜索返回时间不能替代事件日期；逐页核对发生、签发、生效和报道日期。
- 超出上述日期集合的候选标 `REJECTED_STALE`；重大旧事件确需跟踪时按模板标"跟踪报道"与原始日期。
- 跨期去重回看与增量窗口对齐：周二至周五回看最近 2 期，周一回看最近 4 期（`fetch_recent.py` 按报告日期自动推导 `periods`：周一=4，其余=2）。

## 3. 三层检索

### A. 通用主题

动态生成当前月份英文，不硬编码月份。

- 国内外 AI 动态：`AI 人工智能 最新资讯 今日`、`AI news today {current month}`。
- 大模型版本：`大模型 发布 更新 开源`、`LLM release update open source`。
- 企业应用：`AI 应用 案例 落地 企业`、`enterprise AI deployment`。
- 行业趋势：`AI 行业 趋势 融资 算力`、`AI industry trend funding`。
- 能源风电：`AI 风电 新能源 电力 大模型`、`AI wind energy power prediction`。
- 能源全产业链 AI（V5.3.3 新增，2026-11-11 复盘缺口）：`人工智能+ 能源 试点 应用 案例`、`AI 电网 调度 巡检 负荷预测`、`煤矿 油气 数字化 大模型 智能体`、`发电 集团 大模型 智能体 落地`。与 C 层 `cpnn-news` / `xinhua-energy` / `china5e-news` 配对——**只扫标题层**，命中 AI/大模型/智能体/算法/数字化/机器人 才展开正文。能源侧线索普遍是「AI 落地案例」而非「模型发布」，判读时要按落地价值（谁在用、规模多大、指标多少）而非技术新颖度打分。
- 风电竞品 AI（V5.3.3 新增）：`Vestas digital AI`, `Siemens Gamesa AI digital twin`, `GE Vernova AI software`, `远景能源 AI`, `明阳智能 数字化`, `金风科技 数字化 大模型`。**竞品阵地不做 AI 字面硬过滤**：整机厂的 AI 多以 `digital solutions` / `service optimization` / `condition monitoring` / `digital twin` 题名出现，必须先按语义判读再决定是否 AI 相关；另外竞品官网 AI 更新频率本身极低（2026-11-11 实测 Vestas 新闻页 AI 关键词命中近零），**长期无候选属正常，不得为凑数放宽到非 AI 的公司新闻**。
- 能源政策（V5.3.2 新增）：`国家能源局 通知 政策 人工智能 智能化`、`新型电力系统 算力 绿电 政策 发布`。本条与 C 层 `nea-news` 配对使用——关键词负责发现（媒体/转载线索，通常滞后 12–24h 且常丢限定条件），官方源负责核实（一手发文原文、适用范围、生效日期）；只有媒体源、无法在 `nea-news` 找到对应发文的政策类候选，按 `REJECTED_UNVERIFIED` 处理，不靠转述入稿。
- 安全治理：`AI 安全 对齐 漏洞 攻击 监管`、`AI safety alignment vulnerability`。
- 投融资并购：`AI 融资 并购 收购 估值`、`AI funding M&A acquisition`。
- 算力芯片：`AI 芯片 GPU 算力 数据中心 能耗`、`AI chip GPU data center compute`。

### B. Agent 基础设施专项

至少覆盖：

- `AI Agent 框架 执行层 运行时 SDK 开源 发布`
- `Agent Harness Runtime Framework SDK App Server Protocol Sandbox open source release`
- `Codex Claude Agent SDK MCP harness runtime`
- `智能体 工具调用 沙箱 审批 上下文压缩 平台 开源`
- `MCP Model Context Protocol server registry 生态 发布`
- `AI coding agent IDE 插件 CLI 工具 release update`
- `RAG 检索增强 记忆 memory context engineering 框架 更新`

### C. 重点官方源定向扫描

**V4.3 起以 `config/sources.json` 为单一清单**：先跑 `scripts/fetch_sources.py`，rss/atom/github_release 类源由脚本确定性抓取窗口内条目（输出 `AI雷达日报_YYYY-MM-DD_源抓取.json`），`manual_pages` 列出的无订阅接口页面再由执行 Agent 逐一打开。抓取 ERROR 的源不得记为已检查，须改为手工打开或在 `latest_update` 写明不可达。新增/停用官方源只改 `sources.json`，不改本节。

逐一核查最近 2 天的官方开发者博客、产品更新页、文档或仓库 Release：

- OpenAI / Codex
- Anthropic / Claude
- Google DeepMind / Gemini
- Meta AI / Llama
- Microsoft AI
- xAI
- DeepSeek
- Alibaba / Qwen
- ByteDance / 豆包
- 智谱、月之暗面、MiniMax、百度、腾讯混元
- **能源全产业链（V5.3.3 新增）**：国家能源局（`nea-news`/`nea-policy`）、中国电力报（`cpnn-news`）、新华网能源（`xinhua-energy`）、中国能源网（`china5e-news`）
- **风电竞品（V5.3.3 新增）**：Vestas（`vestas-news`）、Siemens Gamesa（`siemensgamesa-news`）、GE Vernova（`gevernova-news`）、远景、明阳

官方社交账号只作线索；关键事实回到官方页面。可选扩展：Cohere、Mistral、Perplexity、Stability AI、阶跃星辰、零一万物、商汤日日新、昆仑万维天工。

## 2.5 日期换算与离线降级（5.1.0）

事件日期按北京时间（Asia/Shanghai）换算，美西通告常差一天；沙箱 / 离线降级流程见 SKILL.md §5.4，来源链接可达规则见 §5.6。

## 3D. 社区信号层（V5.0 新增）

`config/sources.json` 中 `layer: "signal"` 的源由 `fetch_sources.py` 确定性抓取，输出写入 `_源抓取.json` 的 `signal_items`：

| 源 | 类型 | 热度 heat | 过滤 |
|---|---|---|---|
| Hacker News 首页（Algolia API） | `hn_top` | points | AI 关键词 + `min_heat` 80 |
| arXiv cs.AI / cs.CL | `atom` | 无 | 窗口内日期 |
| GitHub Trending（日榜） | `github_trending` | 今日新增 star | AI 关键词；榜单无日期，不做窗口过滤 |
| Hugging Face 模型热榜 | `hf_trending` | trendingScore | 无 |

- AI 关键词表 `AI_KEYWORDS_DEFAULT`；≤3 字母的词按整词匹配（`ai` 不匹配 `air`）。
- 信号条目只是**线索**：执行 Agent 逐条判断去向——
  - 能找到官方页 + 独立报道、事件日期在窗口内 → 升为精编候选 `SELECTED`（证据门禁照旧）；
  - 只有单一来源或社区热度 → `SIGNAL`，按八类之一标 `cat`，带 `heat` 与 `discovered_at`；
  - 与 AI 无关、营销软文、黑名单弱来源 → `REJECTED_*`。
- 召回复核：信号层高热条目（如 HN ≥300）若未进入任何一层，须在 `recall_review.notes` 说明原因；10-30 实测 HN 805 分的 Mistral Large 4 开源权重即被早间 A/B 层漏检。

## 3E. 三层分流标准（V5.0）

| 层 | decision | 收录条件 | 证据要求 |
|---|---|---|---|
| 第一层 · 精编 | `SELECTED` | 事件日期在刊发窗口内 | 与 V4 相同：≥2 独立来源（🔴 单一官方须正文写明）、claim-evidence receipt、语义复核 |
| 第二层 · 本周跟踪 | `TRACKING` | 事件日期在 [D-7, 窗口起点)，此前已报或已入池，今天有新进展或仍在发酵 | ≥1 来源；标题写原始事件日期；日期不确定（「中旬」「近日」）不得进入 |
| 第三层 · 信号雷达 | `SIGNAL` | 单一来源可入；社区热度、预告、传闻、论文 | ≥1 来源，如实标 g/y/r；黑名单弱来源禁入；类别八选一 |

类别白名单（`layout_v5.SIGNAL_CATS`）：新品与工具 / 开源与模型 / 前沿论文 / 行业动态 / 安全与研究 / 能源与金风 / 社区热议 / 传闻待证。

事件键（`event_key`）：跟踪 / 雷达候选须按内容生成（例如 `sig|<链接 slug>`、`mozilla|发布|firefox159修复167漏洞`），禁止 `signal-1`、`track-1` 这类逐日从头编号的键，否则已见索引 L3 会把次日候选判为重复。

候选字段补充：`event_date` 仍为必填——源有日期用事件日期，榜单类（GitHub / HF 热榜）无日期时填发现日并写 `date_basis: "发现日（源无日期）"`；`cat`（SIGNAL 必填）、`heat`（数值或 null）、`discovered_at`（ISO 时间）、`layer`（可选，`core`/`track`/`signal`）。TRACKING 与 SIGNAL 同样写入已见索引并参与查重；`settle` 时与精编一并结算为已发布，次日不再作为新候选出现（如有实质新进展，标 `is_update: true`）。

## 4. 官方源扫描记录

每个重点主体记录：`name`、实际打开的 `url`、`checked`、`checked_at`、`latest_update`、`reason`。无法访问时必须 `checked=false`；不得填写虚假 URL 或从搜索 snippet 推断已检查。

## 5. 周一规则

周末事件仅满足以下之一时保留：

- 重大模型、Agent 产品或关键开源项目正式发布。
- 重大安全事件、行业标准或合规变化。
- 重大投融资、并购或 IPO 进展。
- 明显影响 AI 产业格局的企业战略动作。
- AI×能源风电具有明确工程、产业或商业价值的重要进展。

普通产品宣传、轻量更新、观点评论、重复转载和缺独立强来源的消息不纳入。周一仍遵守总条数上限，不用普通周末资讯凑数。

**今日要紧数量可解释规则（V4/V5 精编层口径，原「今日大事件」规则沿用）**：今日要紧为 2-8 条、建议 3-6 条；其余合格候选分流到金风相关 / 工具与做法 / 行业速递。若今日要紧少于 3 条，必须在检索清单 `recall_review.notes` 或阶段 9 复盘说明依据（候选不足、强来源不足、去重/越窗/弱来源淘汰等）。周一今日要紧与行业速递中刊发窗口内（周五至周日）的**新事件合计 ≥4 条**，「跟踪报道」条目不得超过半数；首轮检索不足时必须做第二轮定向补扫（政策/监管文件、开源与模型发布、能源电力 AI 三个方向各至少一轮），仍不足才可降级并如实记录，禁止用旧事件跟踪报道凑数。

## 6. 检索清单最低结构

```json
{
  "report_date": "YYYY-MM-DD",
  "generated_at": "ISO-8601",
  "window": "d2|d4",  // 搜索 freshness 回看参数（周中 d2/周一 d4）；正文收录以执行点增量窗口为准（见 §2）
  "queries": ["实际执行的完整查询"],
  "official_sources_checked": [
    {"name": "OpenAI / Codex", "checked": true, "url": "https://...", "checked_at": "ISO-8601", "latest_update": "标题或 NONE", "reason": ""}
  ],
  "candidates": [
    {"id": "NEWS-01", "title": "候选", "event_date": "YYYY-MM-DD", "source_urls": ["https://..."], "category": "Agent基础设施", "decision": "SELECTED"}
  ],
  "recall_review": {
    "status": "PASS|FAIL",
    "reviewed_at": "ISO-8601",
    "major_missed_events": [],
    "notes": ""
  },
  "publication_metadata": {
    "is_accumulated": false,   // V3.9.0：累积出刊标识的**唯一权威来源**（正文已不承载该行）
    "span": "",                // 累积期，如 "9/19–9/20"（半角连接符「–」）；日常单期留空
    "count": null              // 累积条数（上限 30）；日常单期 null
  },
  "timings": {}                // 可选：阶段2/3 子节点耗时（供阶段 9 复盘归因）
}
```

> **`official_sources_checked[].checked` 的语义（2026-10-14 明确）**：表示**本次检索已对该官方主体执行核查**，
> **不是**「已采信为正文来源」。`self_check.py` 的 `malformed_official` 要求**每一条都为 `true`**——
> 任何 `checked=false` 都会判「存在无效记录」而 G2 FAIL。凡是「不可直读／未采信正文来源／窗口内无发布」
> 等**负面结论**，一律写进该条的 `latest_update`，并在清单顶层用 `official_sources_checked_note` 声明本语义。
> 是否进入正文以各候选的 `source_urls` 与正文来源行为准。

所有进入验证阶段的候选都保留，淘汰项使用明确原因：`REJECTED_DUPLICATE`、`REJECTED_STALE`、`REJECTED_LOW_PRIORITY`、`REJECTED_UNVERIFIED`。只有重点官方源均有真实记录、`recall_review.status=PASS` 且 `major_missed_events=[]` 时通过 G2。

**`publication_metadata`（V3.9.0 新增）**：承载累积出刊标识，供飞书推送文案解析累积期。
- 日常单期：不设置该节点（或 `is_accumulated=false`）。
- 跨日累积出刊：`is_accumulated=true` + `span`（如 `"9/19–9/20"`）+ `count`（条数）。
- 消费方：`prepare_feishu_push.py` 的 `resolve_accumulation()`（按「metadata 优先 → 正文遗留行兜底」解析）。
- **正文不再写累积行**：`self_check.py` 对正文出现的「全部资讯」模块与「累积出刊」字样一律判 FAIL。

## 7. 跨期去重

**V4.3 起以已见索引（`<work_dir>/seen-index.sqlite`，`scripts/seen_index.py`）为唯一真源**，顺序固定：
检索清单写完 → `run.py dedup`（三级查重，命中的 SELECTED 自动改判 `REJECTED_DUPLICATE`）→ 人工复核去重报告 → `run.py accumulate`（全部候选含 REJECTED 写入索引）→ 发布后 `run.py settle`（标 published）。

| 级别 | 规则 | 回看 |
|---|---|---|
| L1 URL | 规范化 URL 精确匹配（去 fragment/跟踪参数/www/末尾斜杠，http→https） | 永久 |
| L2 标题 | 规范化标题字符 bigram Jaccard ≥ 0.6 | 30 天 |
| L3 事件键 | 候选 `event_key`（`主体|动作|对象`，Agent 填写）相同且事件日期差 ≤ 3 天 | 30 天 |

- 候选应填写 `event_key` 与 `importance`（0-10，供工作日按重要度取前 30）。
- 有新事实的后续报道标 `is_update: true`：L2/L3 命中降级为 UPDATE（标题须写「最新进展」或跟踪报道格式）；L1 命中已发布 URL 仍判重复。
- 论坛近期期次（`fetch_recent.py`，V4.3 已修复 V4 版式解析）作为补充：`run.py dedup --recent-data <近期去重.json>` 会先把它并入索引。首次启用索引时用 `run.py seen-seed` 冷启动。
- 以下语义规则仍由人工复核把关：

- 标题语义为同一公司、产品和核心动作：默认丢弃。
- 来源 URL 完全相同：丢弃。
- 重大后续有新事实：可保留，标题明确“更新”或“最新进展”。
- 旧事件仅媒体延后报道：普通旧闻丢弃；重大跟踪按模板标原始日期。
- 去重拉取失败不再视为可忽略告警；必须用可审计替代方式完成，否则 G2 FAIL。
