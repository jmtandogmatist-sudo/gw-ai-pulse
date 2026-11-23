---
name: ai-radar-daily
description: 金风集团《AI 雷达日报》的每日检索、去重留存、成稿、自查、论坛发布、飞书三群推送与知识库同步。用户说「跑日报」「出今天的 AI 雷达」「补发/修订某期日报」「日报中断了接着跑」「日报去重/候选池/缺跑补检」时使用；检索每天恒定执行、仅中国法定工作日发布，外部发布与推送必须人工确认。不用于周报、月报或一般 AI 资讯问答。版本历史见 CHANGELOG.md。
version: 5.3.4
agent_created: true
---

# AI 雷达日报 V5.3.3

## 1. 目标与适用场景

以最小上下文启动一次可审计运行；把详细检索、证据、模板、判断和异常规则按阶段加载。
仅在以下场景使用：

- `normal`：工作日正常出刊；**中国法定工作日硬校验（V3.7.0）**——普通周末与法定休假日（含落周中）触发立即结束，调休补班的周末放行出刊，不搜索、不写稿、不执行外部动作（脚本级拦截，非仅 LLM 判断）。
- `backfill`：人工明确指定历史 `report_date` 的补发；不得伪装成当日刊发。
- `revise`：修订已发布期次；以论坛回读版本为母稿，仅修改获授权部分。
- `resume`：从结构化状态恢复；先核验产物、哈希和外部事实，再继续未完成阶段。
- `holiday-batch`（V3.8.6 补文档）：法定休假日强制出刊的例外通道——绕过工作日拦截，使 `off`/`weekend` 当日也能推进完整流程；仅限用户明确要求"假日必须出刊"时使用，详见 §6 阶段 1。

不承担周报、纯资讯问答、未授权批量发布或绕过人工审查的任务。

## 2. 单一运行契约

每次运行先冻结以下六项；定义、校验和模式状态转移见 `references/run-contract.md`。

```yaml
issue_id: "AI-RADAR-YYYY-MM-DD"
report_date: "YYYY-MM-DD"
timezone: "Asia/Shanghai"
skill_dir: "/Users/sr_jm/gw_llm_ai_pulse/skills/ai-radar-daily"
work_dir: "/Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily"
env_file: "/Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/.env"
mode: "normal|backfill|revise|resume|holiday-batch"
```

执行约束：

1. 从 `config/runtime.json` 读取非敏感默认值，再由本次显式输入覆盖。
2. 将 `skill_dir` 与 `work_dir` 规范化为绝对路径；禁止依赖当前目录。
3. 只把 `env_file` 当作凭据入口；不得读取、展示、复制或写入凭据。
4. `issue_id` 在全流程、所有产物和状态记录中保持不变。
5. `report_date` 是刊期，不等于抓取时刻、事件日期或媒体报道日期。
6. 运行开始即创建或回读结构化状态；状态不合法时停止，不猜测进度。

## 3. 冷启动加载顺序

先加载最小集：

1. 本文件。
2. `config/runtime.json`。
3. `references/run-contract.md`。
4. `references/rules-matrix.md`。
5. 与当前阶段对应的引用文件；不要一次性加载全部材料。

## 4. 必须加载的 reference 路由表

| 触发阶段或条件 | 必须加载 | 用途 |
|---|---|---|
| 所有模式启动 | `references/run-contract.md` | 输入、路径、状态 schema、模式转移 |
| 所有门禁判定 | `references/rules-matrix.md` | 规则 ID、执行者、验证和失败策略 |
| 检索与召回 | `references/retrieval-strategy.md` + `config/sources.json` | 10 点口径、d2/d4、三层检索、周一规则；C 层官方源单一清单 |
| 跨期去重与留存 | `scripts/seen_index.py`（retrieval-strategy §7） | 已见索引三级查重（URL/标题/事件键），入池前执行 |
| 候选验证与语义复核 | `references/evidence-standard.md` | T0-T4、claim-evidence receipt、来源绑定 |
| 写判断层或更新台账 | `references/judgment-method.md` | 四透镜、N/A、两类验证台账 |
| 构建或同步知识包 | `references/knowledge-base-schema.md` | 五表结构、稳定主键、版本与同步顺序 |
| 同步候选全集（只留知识库） | `config/runtime.json` 的 `knowledge_sync.table_ids.candidates` | 候选全集表 id（tbl4RS4qEuDf2B0E）；见 `sync_candidates_base.py`（随 sync-knowledge 执行，不回流正文） |
| 组装正文或修订排版 | `references/report-template.md` | 唯一模板权威定义；不得在此文件复制模板尾部 |
| 任一异常或降级 | `references/exception-policy.md` | 阻断、降级、保留产物和恢复规则 |
| 生成飞书草稿或发送 | `references/feishu-groups.json` + `references/feishu-group-profiles.json` | 群名单与差异化画像唯一来源；不得在此文件复制群名单 |

发布只能通过受控脚本与 `env_file` 所代表的外部凭据边界执行（`discourse-api.md` 已于 V4.3 删除）。

## 5. 检索 / 发布解耦与假日累积出刊（V3.8.0）

本技能把"检索"与"发布"彻底解耦成两条独立通道，用户口径（2026-09-21 定稿）：

| 通道 | 触发 | 是否看日历 | 动作 |
|---|---|---|---|
| **检索通道** | 每天 08:30（恒定） | **否** | 按窗口检索候选 10 条，写入累积池 |
| **发布通道** | 判断是否工作日 | **是** | 工作日→统一发布；非工作日→不发布、候选留池 |

规则细则：

1. **检索恒定**：无论是否工作日都检索，候选进 `<work_dir>/candidates-pool.json`。
   对应命令 `run.py accumulate`（豁免工作日拦截与会话互斥，因它属检索侧）。
2. **发布以日历为唯一依据**：`china_workday_status()` 四态中 `workday`/`makeup`（调休补班）为可发布日；`off`/`weekend` 为累积日。
3. **非工作日不发布**：候选留池逐日累积，**不区分"普通休假/长假"**——原设定的长假独立分支已废除。
4. **工作日统一发布**：把池中所有未发布候选（含当日 + 之前累积）按重要度排序，**取前 30 条为上限**（`MAX_BATCH_SIZE=30`），**不足 30 则全量发出**。
5. **候选全集只留知识库（V3.9.0 变更）**：精编正文之外的全部候选（含未入选的 REJECTED）由 `sync_candidates_base.py` 同步到飞书知识库**「候选全集 Candidates」表**（`tbl4RS4qEuDf2B0E`）留档。**正文不再挂「全部资讯」链接、不写累积行**——用户明确要求资讯清单不随日报发布；`self_check` 对正文出现的该模块一律判 FAIL。**历史期次豁免**：严格校验自 `LEGACY_ALL_INFO_STRICT_FROM = 2026-09-24` 起，≤2026-09-23 的已发布期次（含 09-23 当期）含该模块不判 FAIL 且**禁止回溯改写**。累积期标识改由检索清单 `publication_metadata` 承载（见第 5.1 条）。候选表同步随 `sync-knowledge`（stage 8）执行，失败仅记录、不阻断五表。
5.1 **累积期标识新载体（V3.9.0）**：检索清单 `AI雷达日报_YYYY-MM-DD_检索清单.json` 增设 `publication_metadata` 节点（`{"is_accumulated": bool, "span": "M/D–M/D", "count": N}`）；飞书推送脚本 `prepare_feishu_push.py` 的 `resolve_accumulation()` 按「metadata 优先 → 正文遗留行兜底」解析，累积出刊时推送标题追加「·累积出刊」并插入累积期说明行，避免把多日累积说成单日。日常单期不设置该节点。
5.2 **缺跑补检恢复（V3.9.3，停机/断网/调度未触发必走）**：每次恢复运行、尤其是假期或周末后第一个工作日，必须在正式发布前扫描 `<work_dir>/candidates-pool.json` 中 `batches[].report_date` 的最新成功检索日期，并枚举 `last_batch_date + 1` 到 `report_date - 1` 的所有日期；凡未出现在 `batches` 且不晚于当前刊期的日期，一律视为 `MISSED_RETRIEVAL`。对缺失日期必须逐日补检并通过 `run.py accumulate` 写入候选池，随后再执行当日检索和发布。V4.3 起扫描由 `run.py recover-missed` 完成（exit 5 列出缺失日期），逐日补检仍由执行 Agent 完成，并在检索清单 `publication_metadata` 中写入 `{"is_accumulated": true, "span": "M/D–M/D", "count": N, "recovery_reason": "missed_runs", "missed_dates": [...]}`；飞书推送必须标注累积期。禁止在发现缺失日期后只发布当天单期。
6. **门禁不降级**：累积出刊与日常刊同等标准，全程 G1–G4 + 人工确认。
7. **结算落账**：发布后 `run.py settle --confirm` 标记已发布候选、写入 `publishes` 记录，避免重复发布。
8. **池老化**：`prune_old` 以**真实当天**为参照剔除超期批次（默认 45 天）；严禁传 `report_date` 作参照（会误删历史累积）。

判定函数：`is_publishable_day()`、`accumulation_due()`（判断是否存在跨日累积待发布）；`missed_retrieval_dates()`（`run.py recover-missed`）负责扫描缺跑日。

## 5.3 每日检索流水线（V4.3 起；V5.0 增加社区信号层）

```bash
R=/Users/sr_jm/gw_llm_ai_pulse/skills/ai-radar-daily/scripts; D=YYYY-MM-DD
python3 $R/run.py precheck        --report-date $D            # 只读环境体检
python3 $R/run.py recover-missed  --report-date $D            # exit 5 = 有缺跑日，先逐日补检入池
python3 $R/signal_pool.py         --report-date $D            # 5.3.0：读论坛信号库（采集器已抓好、打好标签）→ _源抓取.json
#   exit 3 = 信号库读不到（未启用 / 网络 / 权限）→ 改跑下一行兜底，并在检索清单 notes 写明
python3 $R/fetch_sources.py       --report-date $D            # 兜底：官方源 + 社区信号源（HN/arXiv/GitHub/HF）→ _源抓取.json
#   执行 Agent：A/B 层搜索 + manual_pages 逐页核查 + signal_items 逐条分流（SELECTED / TRACKING / SIGNAL / REJECTED）
#              → 写 _检索清单.json（候选带 importance 0-10、event_key；SIGNAL 带 cat/heat/discovered_at）
python3 $R/fetch_recent.py        --report-date $D --output <work_dir>/AI雷达日报_${D}_近期去重.json
python3 $R/run.py dedup           --report-date $D --recent-data <work_dir>/AI雷达日报_${D}_近期去重.json
python3 $R/run.py accumulate      --report-date $D            # 入池 + 写已见索引（全部候选）
#   工作日继续阶段 3-9；发布回读后：
python3 $R/run.py settle          --report-date $D --confirm  # 精编按重要度取前 30 + 当期 TRACKING/SIGNAL 一并结算，索引标 published
```

- **5.3.0 信号库取材**：`signal_pool.py` 输出的 items 带 `signal_topic_id`、`signal_cred`、`questions`、`summary`、`us`、`event_key`。执行 Agent 采用某条信号进入 SELECTED / TRACKING 时，**把 `signal_topic_id` 和 `event_key` 原样写进该候选**（`signal_topic_id` 供阶段 6C 回填「已审」，`event_key` 供已见索引 L3 查重），并可写 `us`（人改定的「对我们」一句）覆盖机器版本。信号库里的摘要与「对金风」是机器初稿，证据门禁、双源核验、语义复核一律照旧，不因来自信号库而放宽。
- 信号库只覆盖采集器的订阅源；`manual_pages`（无订阅接口的官方页）仍须逐页核查，A/B 层补充搜索只用于信号库没覆盖的缺口与召回复核。
- `dedup` 命中的 SELECTED 自动改判 `REJECTED_DUPLICATE` 并输出 `_去重报告.json`；执行 Agent 须过目报告，误判可把候选改回 SELECTED 并标 `is_update: true`（须有新事实）。
- 首次启用已见索引：先 `fetch_recent.py 10 --output …` 再 `run.py seen-seed --recent-data …` 冷启动。
- `fetch_sources.py` 抓取 ERROR 的源不得记为已检查；改为手工打开或在 `official_sources_checked[].latest_update` 写明不可达。

## 5.4 沙箱 / 离线运行手册（2026-11-02 首个周一实跑总结）

沙箱出网受限时（`fetch_sources.py` 全部 ERROR、论坛 / 飞书不可达），按以下顺序降级，**不得把失败源记为已检查**：

1. **官方源**：用 Firecrawl 逐页核查 `sources.json` 中的页面（query 模式取“最近 5 条 + 日期”），页面读不到日期、跳转、失效的，在 `official_sources_checked[].latest_update` 如实写明“不可直读”。URL 失效要回写 `sources.json`（DeepSeek 新闻页 11-02 已 404）。
2. **社区信号源**：HN（Algolia JSON）、GitHub Trending、HF 热榜、arXiv 用 Firecrawl 抓回后交给 `fetch_sources.py` 的解析函数过滤，不手抄。
3. **近期去重数据**：论坛不可达时，用**真实已发布期次**的正文提取标题与链接合成 `近期去重.json`（`fetch_recent.extract_news_items`）。必须确认该期确已发布；未发布的期次不得充当“已发”，否则会把本该上稿的事件误判重复。
4. **阶段 1**：没有论坛回读时只能手工置 PASS 并在状态里写明原因，仅限离线验证；正式发布前须在生产机重跑 `plan`。
5. **外部动作**：离线运行只产出 md、检索清单、语义复核、自查报告、飞书草稿，论坛发布、飞书推送、知识库同步仍须单独人工确认。

## 5.5 「社区今日」取材（不得留占位）

- 用浏览器打开 `https://aicopilot.goldwind.com.cn:3211/hot`（内置浏览器首次需用户授权站点，高风险站点每次加载 / 操作都要再授权）。
- 在页面内执行 `[...document.querySelectorAll('tr.topic-list-item a.title')]` 取标题与链接；热门页不显示浏览数，按“近两周发布、读者对象互不重叠”选 2-3 篇，标题用帖子原标题，场景标签写“谁该点开”。
- 浏览器不可用时，向用户要帖子标题与链接；**禁止**把“待人工替换”占位行带进自查（V5.1.0 起 `self_check` 会 FAIL）。

## 5.6 来源链接可达（公司网络）

- `config/link-reachability.json` 的 `blocked` 是公司网络实测打不开的站点；正文（尾部固定模块除外）出现即 `self_check` FAIL。新增域名由运营按实测补充。
- 信号雷达 / 行业速递优先选大陆可访问的中文来源；境外一手来源（官方通告等）保留作核验依据，但每张精编卡至少要有一个可打开的链接。
- 找不到可访问来源时不要硬凑：删去只有不可达来源支撑的句子，可信度按剩余来源如实下调（例：官方新闻稿原站不可达，仅剩转载稿 + 媒体，🟢 降 🟡）。

## 5.7 日期口径：按北京时间换算

事件日期以 `Asia/Shanghai` 为准。美西时间发布的通告要换算（Cursor 通告标美西 10-29，北京时间是 10-30 凌晨），以此判断是否落入刊发窗口；`date_basis` 写“披露”并在检索清单 `recall_review.notes` 说明换算过程。

## 5.8 官方源「发布阵地」而非「官网首页」（V5.2.3，2026-11-03 漏检复盘）

**教训**：11-03 漏收 DeepSeek Harness「Creator Mode」，根因是 `config/sources.json` 只给 DeepSeek 登记了
`api-docs.deepseek.com/sitemap.xml`（API 文档站，无 news 流），而其**真实首发阵地是 GitHub Releases + 官方 X**。
官网 / 文档站「没有新内容」不等于「这家没发东西」——查了个不发布的页面，会被误当成已覆盖。

检索阶段对每个主体按**三类阵地**分别确认，缺任一类即在 `official_sources_checked[].latest_update` 写明：

1. **官方博客 / news / research 页**（`type=rss|page`，`sources.json` 已登记）。
2. **GitHub Releases / Tags**（`type=github_release`）——工程型产品（Harness、Codex、Claude Code 等）的
   版本级新功能**优先发在这里**，且带精确日期，比官网早 1–2 天。
3. **官方社媒**（X / 官方公众号）——无订阅接口，靠检索发现；命中时以 GitHub / 博客页为一级证据，
   社媒只作线索，不得单独作为双源中的一源。

**判定「阵地缺失」的信号**（出现即回去补 `sources.json`，不要只在本期清单里手工补）：
- 候选里出现某主体的「发布/上线/推出」类消息，但该主体 `official_sources_checked` 里没有任何一条
  对应日期的官方更新 → 说明登记的是不发布的页面。
- 某主体连续 3 期 `latest_update` 都是「无更新」而同赛道其他家持续有新动作 → 先怀疑阵地错，再下结论。
- `fetch_sources` 报 404 / 跳转到文档首页 / 只返回 sitemap → 该源标记为「阵地无效」，当期即修配置。

**跟踪线索复用**：取近 3 期 SELECTED + `REJECTED_STALE` 的主体名，在本窗按上述三类阵地复检一遍；
命中即按「（跟踪报道·M月D日）」处理。

## 5.9 日报标题规范（V5.3.2，2026-11-10 起）

**格式（新期次一律用新格式）**

```
{钩子} |AI雷达{M}.{D}
```

- 实例：`5 次示范就到 95%，机器人不用先攒大数据 |AI雷达11.10`
- 旧格式 `AI 雷达日报｜M.D` **仅作历史期次兼容**（≤2026-11-10 期次仍可被全部脚本正确解析），新期次不得再用。
- `M.D` 不补零（与文件名口径一致）；分隔符是**一个半角空格 + 半角竖线**，竖线后不留空格。
- 钩子与后缀之间、后缀内部均不得有其它分隔符；钩子内**禁含竖线**与字面量「AI雷达」（否则解析歧义）。

**钩子生成逻辑（五步，按序执行）**

1. **取材**：只从本期「今日要紧」段落取，优先 `importance` 最高或段内首位条目；「金风相关」条目不参与（标题不承担内部宣传）。
2. **选型优先级**：反直觉的量化门槛 > 价格/成本落差 > 首次·唯一·最大 > 生态位变化。四类都不命中的期次，退回「今日主线」抽象成一句陈述。
3. **必须含数字**：钩子里至少 1 个来自正文的可验证数字（门槛次数、时长、价格、百分比）。数字不得四舍五入到失真。
4. **句式**：结论先行、陈述句；`6 ≤ 长度 ≤ 24` 字；不用问句、不堆形容词、结尾不带标点。
5. **不得比正文更绝对**：厂商自测类数字（成功率、基准分）在标题中不追加褒贬限定，正文必须保留「厂商自测」限定语；命中 `title_spec.BANNED_WORDS`（重磅/震惊/颠覆/史上最…）即 FAIL。

**三条硬约束**

- **唯一真源 = 正文 H1**。`publish` 不再由 `report_date` 拼标题，而是读 H1 并校验「格式合规 + H1 日期 == `report_date`」；`layout_v5` / `self_check` / `prepare_feishu_push` / `fetch_recent` / `build_knowledge_package` 全部改为 `import title_spec`，**任何脚本都不许再自写标题正则**。
- **改标题必同步重算**（漏一处即静默错判）：正文 `body_sha256` → 重跑 `self_check` / `verify` → 重建 knowledge package（`package.json` 里存着 issue.title）→ 论坛 topic 标题（`PUT /t/<id>.json`，属外部动作，须人工确认）→ `publish-state` / `run-state.forum` 记录。
- **`date_tag` 必须锚定后缀**：飞书推送的日期标签只能由 `title_spec.date_tag()` 取，禁止对整串做 `\d+\.\d+` 搜索——新格式钩子里的「0.28 元」「5.2」会被误判成日期。

**工具入口**：`python3 scripts/title_spec.py --check "<标题>"` 校验；`--hook "<钩子>" --report-date YYYY-MM-DD` 拼装。
回归测试：`tests/test_title_spec.py`（28 项，覆盖两代格式解析、H1 识别、日期锚定、钩子校验、layout 集成）。

## 6. 九阶段简化流程

### 阶段 1：初始化与恢复判定

- 校验运行契约、模式、北京时间工作日规则和目录可写性。
- **日期防呆护栏（V3.3.1）**：`run.py` 在 `main()` 解析参数后、会话互斥前执行 `validate_report_date()`，拒绝格式错误、年份 <2020（防历史残留）与 >当日+1 的假未来日期（future-backfill 保留当日+1 余量）；违规即 `INVALID_REPORT_DATE` exit 2，且不创建任何目录。本护栏为 2026-09-08 假日期污染修复的一部分——测试套件曾轮次写出 `AI-RADAR-2099-*` 假目录，污染出刊日期锚点并阻断当日正刊。
- **中国法定工作日硬校验（V3.7.0）**：`run.py` 的 `main()` 在 `validate_report_date()` 之后、会话互斥前调用 `china_workday_status()`（normal 模式且非 status 命令）。判定四态：`off`（法定休假日，落周中/周末都停）、`makeup`（周末调休补班，放行出刊）、`weekend`（普通周末，停刊）、`workday`（普通工作日，放行）。`off`/`weekend` 触发 `NOT_WORKDAY`：dry-run 返回 0（不写盘）、非 dry-run 返回 4 阻断后续阶段，且**不执行任何搜索/写稿/外部动作**（2026-09-20 固化，替代原"依赖 LLM 判断周末"的软约束）。节假日/补班表按年份维护在 `run.py` 的 `CHINA_HOLIDAYS`/`CHINA_MAKEUP_WORKDAYS`（2026 数据已内置，来源国办发明电〔2025〕7号）。**数据源治理（V3.9.6，2026-10-08）**：`china_workday_status()` 已改为**优先读外部数据** `~/.workbuddy/skills/calendar-cn/data/<year>.json`（国务院原文口径，完整 YYYY-MM-DD + 调休补班，随年度通知更新），读不到才回退本表——新增年份**不再需要改 run.py**，只需跑 `python3 ~/.workbuddy/skills/calendar-cn/scripts/update_holidays.py`（国务院每年 11 月上旬发布次年安排，建议 11 月中旬执行）。背景：内置表以 `MM-DD` 分桶且仅录 2026 年，跨年后会静默退化为「仅按星期」判定——2027 年元旦本为休假日却会被判为 `workday`；新增年份须人工录入，未录入年份回退"仅星期"判定。
- **`holiday-batch` 模式：法定假日强制出刊逃生舱（V3.8.6 补文档）**。`--mode` 白名单含 `holiday-batch`，其**唯一实际行为是绕过上述工作日拦截**（`main()` 的拦截条件为 `args.mode == "normal"`，非 normal 即跳过），使 `off`/`weekend` 当日也能推进完整九阶段流程。经 2026-09-22 实测确认：同一法定休假日（2026-02-17 春节 `off`），`normal` 返回 `NOT_WORKDAY` 停刊、`holiday-batch` 放行至 `STAGE1_PLANNED`。**使用纪律**：这是**例外通道而非常规路径**——仅当用户明确要求"假日当期必须出刊"时使用，且须在汇报中标注该期为非发布日强出；常规运行一律 `normal`（节假日候选走累积池、工作日统一发布，见 §5）。注意该值目前**无独立分支逻辑**，仅作用于"跳过工作日拦截"这一处，不要误以为它包含额外的批次打包行为。`backfill`/`revise`/`resume` 同样会跳过该拦截（各自另有用途），属既有设计。
- 读取状态文件；`normal/backfill/revise` 不得覆盖同 `issue_id` 的不兼容运行。
- **并行会话检测（V3.2.3）**：run-state 已存在且产物文件在最近 10 分钟内有写入时，判定存在并行会话——不得直接接管写操作；先监控 mtime 等待对方静默（≥5 分钟无写入），接管前必须 ①对照 `report-template.md` 做结构 diff ②重读全部产物 ③逐项核验外部状态（论坛/知识库/飞书），再继续未完成阶段。
- `resume` 逐项回读产物并验证 SHA-256，不以文件存在代替阶段成功。
- **人工接管自动化场景（2026-09-09 固化）**：当某期正刊已由自动化（`owner_session=automation`）推进到 WAITING_FORUM_CONFIRMATION 或之后、因需人工介入而转人工时，一律用 `run.py ... --mode resume --session-id manual` **续跑**，禁止重开 `backfill` 或重跑 `normal`——`backfill` 会当全新刊从头起、覆盖/污染正刊产物与已 PASS 门禁（2026-09-08 normal 被人工 backfill 覆盖即此教训）；`resume` 会先核验 run-state/产物哈希/外部事实，再接着未完成阶段继续。人工接管前须先完成本阶段"并行会话检测"三件套（结构 diff + 重读产物 + 外部核验）。
- 写入 `INITIALIZED`；采用临时文件、`fsync`、原子替换方式更新状态。
- **搜索前只读预检（V3.9.0，耗时优化核心）**：在进入阶段 2 检索**之前**执行 `run.py precheck`，
  把"搜索前就能发现的环境问题"全部前移，避免拖到发布前才发现导致返工（2026-09-23 第 22 期
  114 分钟里约 20 分钟属可避免返工，根因多为发布前才暴露的环境问题）。预检**只读**：不写
  run-state、不发帖、不发消息、不写 Base；**凭据只校验键名，绝不回显值**。检查项：

```bash
python3 /Users/sr_jm/gw_llm_ai_pulse/skills/ai-radar-daily/scripts/run.py precheck \
  --report-date YYYY-MM-DD \
  --env-file /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/.env
```

  | 检查项 | 内容 | 失败级别 |
  |---|---|---|
  | 二维码 6 码 | `群二维码_{本地大模型,Dify平台,金风科技,KNS,标准化助手}.png` + `群二维码_风衍大模型.jpeg` 是否齐备（缺一即 `publish.py` PublishError） | **BLOCK** |
  | 飞书群配置 | `feishu-groups.json` 可解析、启用群 ≥1 | **BLOCK** |
  | 群画像 | `feishu-group-profiles.json` 每群含 `angle_label/question/headline_keywords`（`angle_suffix` 自 V3.9.1 起可选），且 `angle_label` 唯一（差异化门禁 fail closed） | **BLOCK** |
  | 知识库配置 | `base_token` + 六张表 id（issues/events/sources/judgments/runs/candidates）齐备 | WARN（允许先出刊后补同步） |
  | 服务可达 | 论坛 `base_url` 只读探活（401/403 视为可达） | WARN |
  | 凭据键位 | `.env` 可读且含 `DISCOURSE_API_KEY`/`DISCOURSE_API_USERNAME`（**仅报键名**） | **BLOCK** |

  退出码：`0`=PASS / `1`=WARN（可继续但需留意）/ `2`=BLOCK（**应先修复再开始检索**）。
  预检为只读体检，非工作日也可执行（已从工作日拦截豁免）；其摘要不写入 run-state。
- **缺跑扫描（V3.9.3，预检之后、阶段 2 之前必须执行）**：读取 `<work_dir>/candidates-pool.json`，取 `batches[].report_date` 的最大值作为最后成功检索点，枚举其后至 `report_date - 1` 的日期；若存在缺失日期（例如电脑关机、网络中断、自动化未触发），必须先补检这些日期并 `accumulate` 入池，再进入当日检索。若缺失日期覆盖法定休假日/周末，也要补检，因为检索通道不看日历。补检完成后，当期检索清单 `publication_metadata` 必须标记 `is_accumulated=true`、`span`、`count`、`recovery_reason="missed_runs"` 与 `missed_dates`，用于飞书推送和阶段 9 复盘。若无法补检，停止并汇报 `MISSED_RETRIEVAL_UNRECOVERED`，禁止只发当天单期。

### 阶段 2：检索与跨期去重

- **检索恒定执行（V3.8.0）**：无论是否工作日都检索；候选按窗口收进累积池（`run.py accumulate`）。生成完检索清单后立即入池，供发布通道消费。
- 按 `references/retrieval-strategy.md` 执行窗口、三层检索和官方源扫描。
- 周二至周五回看最近 2 期；周一回看最近 4 期。
- 保存检索清单、全部候选、淘汰原因和召回复核结果。
- **弱来源源头拦截（V3.6.0）**：候选定级时即规避弱来源（公众号/聚合号：mp.weixin.qq.com、baijiahao、sohu.com/a、163.com/dy、toutiao、kuaibao.qq.com、news.qq.com/rain、zhuanlan.zhihu 等）。`self_check.py` 的候选池门禁会在筛选时对 `SELECTED` 候选命中弱源即 FAIL；故检索阶段对弱源候选应直接标 `REJECTED` 或补齐强第三方/官方源，不要带入正文，避免拖到阶段 5/自查才返工替换（2026-09-17 金开新能 toutiao、AgentGit 163.com/dy 教训）。
- **阶段耗时明细记录（V3.6.0）**：检索清单顶端可加可选 `timings` 节点记录阶段2/3 子节点耗时（`basis`∈d2/d4 + 机器检索/证据核验/封面/人机往返纠错分钟数），供阶段 9 复盘精确归因；缺省不阻断自查。
- 运行绝对路径示例：

```bash
python3 /Users/sr_jm/gw_llm_ai_pulse/skills/ai-radar-daily/scripts/fetch_recent.py 2 \
  --report-date YYYY-MM-DD --timezone Asia/Shanghai \
  --env-file /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/.env \
  --output /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI雷达日报_YYYY-MM-DD_近期去重.json
```

### 阶段 3：证据核验与编辑判断

- 按 `references/evidence-standard.md` 为每个关键 claim 建立证据 receipt。
- 空证据、来源不能直接支持 claim 或日期无法确定时 fail closed。
- 先冻结事实层，再按 `references/judgment-method.md` 写编辑判断。
- 判断标签允许 `N/A`。**V4 起正文不再使用透镜标签**（【价值层移动】【部署边界】【能力阈值】【业务距离】已停用），编辑判断统一以「**对我们：**」行呈现；禁止为满足版式强贴标签。
- 分别维护事件日程台账和编辑判断验证台账，不得混写。

### 阶段 4：组装正文与资源

- 严格使用 `references/report-template.md`（**V5 三层版式**，2026-10-30 起）；该文件是模板和固定尾部的唯一权威。
- **结构白名单（V5.0）**：`[wrap=air-daily]` 容器内依次为首屏（H1 / 今日主线 / 封面 / 四个数字卡 / 可信度条）→ `## 第一层 · 精编日报`（今日要紧 2-8 → 金风相关 0-2 → 工具与做法 0-4 → 行业速递 3-10）→ `## 第二层 · 本周跟踪`（0-8）→ `## 第三层 · 信号雷达`（0-40，置于 `[wrap=air-radar]` 内）→ `## 社区今日` → `## 今日一问`；尾部固定模块在容器外。禁止自创模块；增删层或分区须先经用户确认。
- **精编层证据口径不变**：与 V4.2 相同（双源、窗口内日期、「对我们」≤90 字、弱来源禁入、语义复核与 receipt）；正文字数改为逐卡上限（要紧 / 金风 160，工具 120，速递 100）。
- **跟踪层**：只收事件日期在 [D-7, 窗口起点) 的已报大事后续；日期写不准的不进本层。**雷达层**：允许单一来源，但如实标可信度、类别八选一、带来源名标签；黑名单弱来源仍禁入。
- **Discourse 语法**：`[wrap]` 各占一行、一行不得两个；行内标签用 `<span data-air-chip="sec|cred|from">`。卡片渲染依赖主题组件 `assets/discourse-theme/ai-radar-daily-style`（管理员安装一次，见该目录与项目文档《AI雷达日报-Discourse样式组件说明》）。
- 输出 `AI雷达日报_YYYY-MM-DD.md`，并保存检索、证据、语义复核和台账产物。
- **封面标准（V3.2.3）**：封面使用 ImageGen 生成的 1536x1024 风电场主题插画（或复用合规历史封面）；禁止程序化文字卡片等非 AI 生成封面；缺图降级为一行说明并记 `COVER_MISSING_FALLBACK`，不得改变事实内容。
- 不在正文输出缺少内部数据支撑的“对金风的启示”或类似建议。
- 门禁实现：`layout_v5.check_structure()` + `check_against_search_log()` 校验版式与三层绑定，`layout_v5.to_v4()` 把精编层转成 V4 结构交给 self_check / preflight / 飞书 / 构包；历史 V4 期次按原规则（`[wrap=air-daily]` 自动分流）。

### 阶段 5：语义复核、发布前总装预检与确定性自查

- 先人工语义复核 claim、日期、来源独立性、旧闻标识、去重和判断边界。
- **发布前总装预检（V3.6.2）**：`run.py self-check` 会先调用 `preflight_check.py`，校验正文 Markdown、语义复核 JSON、检索清单 JSON 三方一致性；检查正文来源 URL 与 `semantic_review.source_urls`、`evidence_receipts.sources.url`、SELECTED 候选 `source_urls` 是否严格覆盖一致，同时前置候选池 schema、重点官方源扫描、SELECTED 弱源、跟踪报道标题格式、「对我们」行 90 字和板块字数上限（金风相关/工具与做法 300 字、行业速递 600 字）。预检 FAIL 即 G3 FAIL，禁止继续自查和论坛发布；self_check PASS 后还会做 knowledge package dry-run，提前发现构包绑定问题。
- 再运行确定性脚本；脚本能力边界见 `references/evidence-standard.md`。
- 绝对路径示例：

```bash
python3 /Users/sr_jm/gw_llm_ai_pulse/skills/ai-radar-daily/scripts/preflight_check.py \
  /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI雷达日报_YYYY-MM-DD.md \
  --semantic-review /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI雷达日报_YYYY-MM-DD_语义复核.json \
  --search-log /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI雷达日报_YYYY-MM-DD_检索清单.json \
  --self-check-report /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI雷达日报_YYYY-MM-DD_自查报告.md \
  --run-state /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI-RADAR-YYYY-MM-DD/run-state.json \
  --package-dry-run \
  --report-date YYYY-MM-DD --timezone Asia/Shanghai \
  --report /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI-RADAR-YYYY-MM-DD/preflight-report.json

python3 /Users/sr_jm/gw_llm_ai_pulse/skills/ai-radar-daily/scripts/self_check.py \
  /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI雷达日报_YYYY-MM-DD.md \
  --semantic-review /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI雷达日报_YYYY-MM-DD_语义复核.json \
  --search-log /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI雷达日报_YYYY-MM-DD_检索清单.json \
  --cover /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI雷达日报_YYYY-MM-DD_封面.png \
  --qr1 /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/群二维码_本地大模型.png \
  --qr2 /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/群二维码_Dify平台.png \
  --qr3 /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/群二维码_金风科技.png \
  --recent-data /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI雷达日报_YYYY-MM-DD_近期去重.json \
  --report-date YYYY-MM-DD --timezone Asia/Shanghai \
  --require-evidence-receipts \
  --report /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI雷达日报_YYYY-MM-DD_自查报告.md
```

> V4.3 更正：`self_check.py` 只接收 `--qr1/--qr2/--qr3`（argparse 占位，6 码齐备由正文 `[grid]` 校验）；旧示例中的 `--qr4/--qr5/--qr6` 会报 `unrecognized arguments`。`--qr4..6` 只属于 `publish.py`。优先用 `run.py self-check`，它已传对参数。

二维码为**6 码**（V3.9.9，自 V3.8.4 的 5 码扩充）：本地大模型用户交流群 / AI智能体开发平台运营群（Dify平台）/ 语义本体构建交流群 / KNS统一数据平台 / 标准化助手交流群 / 风衍大模型用户交流群；实物统一 1372x1392 截图（序 1–5 为 PNG、序 6 为 JPEG），正文显示尺寸统一 `|400x400`，alt 名一律用群的真实名称（`self_check` 已加尺寸门禁）。已废弃的 `群二维码_AI信息交流.png` 不再引用。序 6（风衍大模型）2026-10-21 经用户确认固定加入，二维码有效期至 2027-10-21、到期前须换新图。

**短链环境绑定（V3.8.5 重要）**：模板内 `upload://` 短链中，序 1–4 经 2026-09-22 实测**生产站可达**（200），序 5（标准化助手交流群）当前指向**测试站**（10.12.3.232:8088）、在生产站实测 404。**生产发布前必须在生产站重新上传该图换取生产短链并替换**，否则该码渲染为破图。序 6（风衍大模型）尚无短链——正文用本地引用、`publish.py` 发布时自动上传换取真实短链。短链↔群名↔来源环境对照表见模板注释块。

- 周一追加 `--recent-days 4`；修订当日已发生事件时才可追加 `--allow-same-day`。V3.2 正常、补发、修订与恢复模式均必须传 `--require-evidence-receipts`。
- 正文在 PASS 后有任何变化，立即使旧自查报告失效并重跑。

### 阶段 6：论坛发布与回读

- **发布前置判断（V3.8.0）**：发布前必须确认当日为可发布日（`is_publishable_day()`）；非工作日**禁止发布**，候选留池待工作日统一发布。开工前用 `accumulation_due()` 判断是否为跨日累积出刊——若是，正文候选来自累积池（按重要度取前 30 上限，不足全量），而非仅当日 10 条。
- 仅门禁 G1、G2、G3 通过、`preflight_check.py` 最新报告为 PASS（含 package dry-run PASS），且获得本次外部写入确认后发布；`run.py publish` 会在写入论坛前再次执行 preflight + package dry-run，防止自查 PASS 后正文/JSON 被改。
- 只调用绝对路径脚本，不手工拼接认证请求。**注意参数签名（V3.9.9 扩充）**：`publish.py` 接收 **3 个位置二维码参数**（`qr1_path qr2_path qr3_path`，对应前 3 群）+ **`--qr4` / `--qr5` / `--qr6` 选项**（后 3 群）+ 位置参数 `self_check_report`。把 6 个码全写成位置参数会报 `unrecognized arguments`（旧示例即为此错误，2026-09-22 修正）。二维码固定顺序：本地大模型用户交流群 / AI智能体开发平台运营群 / 语义本体构建交流群 / KNS统一数据平台 / 标准化助手交流群 / 风衍大模型用户交流群，脚本按此群名精确映射并强制 `|400x400`。
- **维护纪律（2026-09-22 教训）**：修改本文档的调用示例时，**必须同步核对脚本的 `argparse` 真实签名**并用真实参数过一遍 parser——文档与脚本口径分裂会形成"看起来对的陷阱"：越严格遵循文档，越会踩中错误。群名清单的单一权威是脚本常量 `publish.QR_GROUP_NAMES`，模板 `[grid]` 与它由测试 `test_qr_group_names_match_template_order` 锁定一致，改任一处须同步另一处。

```bash
python3 /Users/sr_jm/gw_llm_ai_pulse/skills/ai-radar-daily/scripts/publish.py \
  /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI雷达日报_YYYY-MM-DD.md \
  /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI雷达日报_YYYY-MM-DD_封面.png \
  /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/群二维码_本地大模型.png \
  /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/群二维码_Dify平台.png \
  /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/群二维码_金风科技.png \
  /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI雷达日报_YYYY-MM-DD_自查报告.md \
  --qr4 /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/群二维码_KNS.png \
  --qr5 /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/群二维码_标准化助手.png \
  --qr6 /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/群二维码_风衍大模型.jpeg \
  --report-date YYYY-MM-DD --timezone Asia/Shanghai \
  --env-file /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/.env \
  --state-file /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/AI-RADAR-YYYY-MM-DD/publish-state.json
```

- 修订前先回读 topic，取得首帖真实 `post_id`；禁止把 `topic_id` 当 `post_id`。
- 回读确认标题、正文、分类、标签和图片；状态不确定时不得重发。
- **`NEEDS_RECONCILIATION` 是本论坛的常态，不是发布失败（V5.2.3，11-03 复核）**：二开站点的
  `/t/<id>.json` 首帖 `raw` 字段返回空（`cooked` 正常），发布脚本的写后哈希比对必然不相等，于是稳定
  报出该状态。**此时帖子通常已经发出去了，禁止直接重发**（会变成双帖）。正确处置：
  ① 拉 `/c/15-category/15.json` 按标题找到当期 topic，取 `id`；
  ② 拉 `/t/<id>.json` 逐项核对：标题、`category_id=15`、`tags`、图片数（1 封面 + 6 群码）、`####` 标题数、
     `cooked` 长度；
  ③ 全部对上 → 重建 `publish-state.json`（`status: OK`、`topic_id`、`post_id`、`url`、
     `source: "manual_publish_reconciliation"`）并修订 run-state（stage 6 = PASS、`forum.status=PUBLISHED`、
     `confirmations.forum_publish`）；
  ④ 对不上（标题或分类不符、图片缺失）才按「原帖被删除后的重发」流程处理。
  对账结论必须写进 recap，不得只记「回读异常」了事。
- **原帖被删除后的重发（V3.2.3）**：用户删除已发布帖子时，用 `publish.py` **新建**（不带 `--update-post-id`）重新发布，不尝试恢复或更新已删帖；新帖回读验证后，必须按序执行——① 重建 publish-state（新 topic_id/post_id）② 重跑 `package` 知识包同步（旧 URL 会残留在五表中）③ 飞书推送必须用新链接**重新 prepare**（payload 内嵌 URL，旧 payload 作废）。已发送的旧链接消息是否补发/撤回由用户决定。
- **发布后结算累积池（V3.8.0）**：论坛回读通过后，用 `run.py settle --confirm` 结算本次发布的候选（标记 `published`、写 `publishes` 记录），防止下一轮重复发布。若为跨日累积出刊，须在正文/推送中体现"累积期"标识，避免把积压候选表述为当日新闻。
- **候选全集只留知识库（V3.9.0）**：精编正文之外的全部候选（含 REJECTED）由 `sync_candidates_base.py` 同步到飞书知识库候选表（`table_ids.candidates = tbl4RS4qEuDf2B0E`）留档。**正文不再挂「全部资讯」模块**（`self_check` 对正文出现的该模块判 FAIL），`run-state.candidates_sync` 记录同步状态；候选表失败仅 WARN，不影响五表。候选表同步随 `sync-knowledge`（stage 8）执行。
- **累积出刊的推送标注（V3.9.0 改口径）**：跨日累积出刊时，群推送文案必须显式标注累积期，避免把多日积压说成「今日/昨日」，口径失实。**权威来源已从正文迁至检索清单 `publication_metadata`**（`{"is_accumulated","span","count"}`）；`prepare_feishu_push.py` 的 `resolve_accumulation()` 按「metadata 优先 → 正文遗留行兜底」解析，累积出刊时推送标题追加「·累积出刊」并在标题下插入累积期说明；日常单期不设置该节点、不加标注。正文遗留的累积行正则（`parse_accumulation`）仅作历史兼容保留。

```bash
python3 /Users/sr_jm/gw_llm_ai_pulse/skills/ai-radar-daily/scripts/run.py settle \
  --report-date YYYY-MM-DD --confirm \
  --env-file /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/.env
```

### 阶段 6B：论坛首页数据（5.2.0）

论坛首页的「AI 雷达」区块（主题组件「AI 雷达首页」）只读一个**数据底座帖**（`runtime.json` 的 `homepage_feed.topic_id`，现为话题 632）。本阶段把**已发布的当期正文**转成 `air-pulse/1` JSON 写进该帖第 1 楼：

- 取材只用最终正文（G3 PASS、论坛回读通过），不从候选池取；检索清单只补信号热度与发现时间。首页与日报逐条一致。
- 公司网络打不开的链接（`link-reachability.json`）一律剔除；`next_update_at` 取下一个可发布日的 `homepage_feed.update_time`（默认 09:30）。
- 幂等：底座帖内容哈希相同则 NOOP；底座帖已是更新一期时拒绝覆盖（`FEED_OLDER_THAN_LIVE`，修订旧刊时防止把首页倒回去，确需回退加 `--allow-older`）。写入后回读核对哈希。
- 失败只影响首页，不改动 `run_status`，不阻断构包、飞书与知识库；结果记在 `run-state.homepage_feed`。
- **5.3.0 合并写入**：底座帖由四方共同维护——采集器每轮写 `radar` / `radar_updated_at` / `radar_next_at` / `radar_total` / `question_list`，主题档案 skill 每周写 `topics`，技术雷达 skill 每季度写 `stance`，本 skill 只写日报字段。`publish` 先读底座帖再合并（`homepage_feed.merge_with_live`）：底座帖有 `radar_updated_at` 时信号雷达沿用采集器的；`topics`、`stance` 始终保留。

```bash
python3 /Users/sr_jm/gw_llm_ai_pulse/skills/ai-radar-daily/scripts/run.py feed --report-date YYYY-MM-DD            # 只生成本地 _首页数据.json 预览
python3 /Users/sr_jm/gw_llm_ai_pulse/skills/ai-radar-daily/scripts/run.py feed --report-date YYYY-MM-DD --confirm  # 更新首页（外部写入）
```

- 确认口径：论坛发布前向用户展示审查材料时，一并说明“发布后同步更新论坛首页”；用户确认发布即视为同时授权本阶段（首页是论坛内的派生视图，数据与刚发布的正文一致）。用户明确说“先别动首页”时不执行。飞书推送仍须单独确认。
- 账号：复用 `.env` 的论坛凭据；该账号须能编辑底座帖（管理员 / 版主或该帖作者），否则回读失败即停（`FEED_READ_FAILED`），不盲写。

### 阶段 6C：信号库回填「已审」（5.3.0）

日报论坛发布回读通过、首页更新后，把本期 SELECTED / TRACKING 里来自信号库的条目标为已审：话题标签 `auto` → `reviewed`，机器可读块 `review` 改为 `reviewed`，候选带 `us` 时替换「对金风」一段，并在正文末尾追加「已进入 YYYY-MM-DD 每日精选」与本期链接。首页信号雷达随采集器下一轮刷新显示「已审」。

```bash
python3 $R/mark_reviewed.py --report-date $D --checklist <work_dir>/AI雷达日报_${D}_检索清单.json \
  --env-file <.env> --daily-url <本期日报链接> --state-file <work_dir>/<issue_id>/mark_reviewed.json            # 只打印计划
python3 $R/mark_reviewed.py ... --confirm                                                                         # 写论坛并回读
```

- 确认口径同阶段 6B：用户确认论坛发布即视为授权本阶段（只改论坛内信号话题的标签与状态）；用户明确说不改时跳过。
- 失败只影响信号话题的审核标识，不改 `run_status`，不阻断后续阶段；失败条目在复盘中列出。
- 账号须能编辑「AI 雷达」机器人发的话题（管理员 / 版主）。

### 阶段 7：本地知识包构建（V3.9.0：仅本地，不触远端）

- **可执行时机**：论坛回读验证通过后**即可**执行（不必再等人工飞书审查），因为本阶段只做本地
  构建与校验，不产生任何外部可见动作。这使构包与"等待用户确认飞书"并行，压缩串行等待。
- 生成本地五表 knowledge package：Issues / Events / Sources / Judgments / Runs，并记录正文 SHA-256、证据索引、论坛 topic/post ID、修订版本与运行状态。命令：

```bash
python3 /Users/sr_jm/gw_llm_ai_pulse/skills/ai-radar-daily/scripts/run.py package \
  --report-date YYYY-MM-DD --confirm \
  --env-file /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/.env
```

- 成功即 `stage 7 PASS` + `knowledge_sync.status=PACKAGED` + `run_status=PACKAGED`；
  失败即 `stage 7 FAIL` + `run_status=GATE_FAILED`（阻断后续）。
- **本阶段绝不调用 `sync_lark_base.py` / `sync_candidates_base.py`**——远端同步已后移到阶段 8。
- `knowledge_sync.enabled=false` 或远端目标/ACL 未配置时，仍可正常构包，随后阶段 8 标 `SYNC_PENDING`。
- 本地构包失败不得删除或回滚已验证日报内容。

### 阶段 8：飞书推送（人工确认门）与远端知识库同步

本阶段含**两次互不继承的外部动作**，顺序固定为「先飞书、后知识库」。

**8A · 飞书即时草稿（可选，缩短等待）**

- 论坛确认**之前**即可预生成三群草稿：`run.py push-draft`（链接用占位符 `<FORUM_URL_PENDING>`，
  不触网、不发送，输出 `AI雷达日报_YYYY-MM-DD_飞书推送_草稿.json`）。用户确认论坛发布后，
  论坛回读成功即由 8B 用真实链接重新 prepare，**只需回填真实 URL/topic ID**。

**8B · 飞书群推送（外部可见动作，需单独确认）**

- 先向用户提供论坛回读结果、摘要、自查状态、正文 SHA-256、召回结论与本地构包状态。
- 明确等待人工审查；未收到"确认推送"等明确授权时停止（论坛发布确认**不**自动授权飞书发送）。
- 前置：`stage 7 PASS` + 论坛 `PUBLISHED`。**发送入口 fail closed**：`article_url`/`topic_id`
  若为占位符、或**外发内容**（各群 message/摘要/标题 + 链接）残留占位 token，一律拒绝发送（`PLACEHOLDER_LINK_BLOCKED`）。V3.9.1 起终检只扫外发内容，不再扫 payload 结构性字段名（`placeholder`/`placeholder_url` 曾被误判为泄漏）。
- 命令：`run.py push --report-date YYYY-MM-DD --confirm`（内部会按真实链接重新 prepare + 逐群幂等发送）。
- **三群差异化推送（V3.5.5；落点策略 V3.9.1）**：目标群=AI智能体开发平台运营群、KNS统一数据平台、本地大模型用户交流群（群名单见 `feishu-groups.json`）。每个群按 `feishu-group-profiles.json` 中的画像生成「一主讯息＋两快讯＋一问题＋阅读全文＋统一签名档」。差异化必须同时满足：①头条按“标题＋摘要”命中本群关键词且三群不重复，无匹配候选即 fail closed，禁止按日报顺序硬凑无关头条；②日报原始判断标签不得直接复用，须移除后按群改写为唯一视角标签——智能体群 `【能力复用】`、KNS 群 `【数据价值】`、本地模型群 `【部署门槛】`；③**V3.9.1 起不再追加群专属价值落点（`angle_suffix` 改为可选）**，差异化由视角标签前缀＋唯一头条＋差异化快讯/提问承担；若某群画像配置了 `angle_suffix` 则仍会追加，未配置不报错；④互动问题/emoji/快讯按群区分。完整日报不整篇发群，交给阅读全文链接；签名档为完整版（出品方＋研究院＋投稿＋合集＋互动）。
- **V5 取材范围**：飞书只取精编层；今日要紧不足 3 条时，依次从金风相关、工具与做法、行业速递补足 3 条要点（总数不足 3 条才 fail closed）。跟踪与雷达不进飞书。
- **飞书链接固定（V5.2.1）**：三群推送文案中的「阅读全文」与签名档「往期合集」**一律固定为** `https://aicopilot.goldwind.com.cn:3211/latest`，不使用当日 topic URL（每期变化、二开站点偶发跳转异常）。真实 `article_url`/`topic_id` 仍写入 payload 供审计、幂等键与发送门禁使用；**不得**因文案里是固定链接而放松 `PLACEHOLDER_LINK_BLOCKED` 与 `FORUM_REQUIRED` 门禁。单一真源：`prepare_feishu_push.DAILY_URL` + `feishu-group-profiles.json` 的 `signature.block`，改链接须两处同步。
- 发送记录按群串行原子写入；连接器或 ACL 不可用时保留草稿并如实标记失败。
- 三群全部 SENT 后 `run_status=SYNC_PENDING`（**不是终态**），等待 8C。
- **链接变更（V3.2.3）**：论坛链接（topic_id/URL）在本期任何环节发生变化（删帖重发、换帖）后，必须用新链接重新 prepare 并核验无旧链接残留，才可发送；禁止复用内嵌旧 URL 的 payload。

**8C · 远端知识库同步（stage 8，需单独确认）**

- **硬前置门禁**：所有飞书目标群均已 `SENT`（任一群 FAILED/PENDING 即拒绝，`FEISHU_NOT_ALL_SENT`）。
  设计意图：知识库是留档侧，不应早于推送；且避免同步失败反压推送。

```bash
python3 /Users/sr_jm/gw_llm_ai_pulse/skills/ai-radar-daily/scripts/run.py sync-knowledge \
  --report-date YYYY-MM-DD --confirm \
  --env-file /Users/sr_jm/gw_llm_ai_pulse/work/ai-radar-daily/.env
```

- 内部依次执行 `sync_lark_base.py`（五表）与 `sync_candidates_base.py`（候选全集表）。
- 五表回读确认全部稳定主键才标 `SYNCED`；远端未配置/回执非 SYNCED → `stage 8 FAIL` + `run_status=SYNC_PENDING`，
  **保留本地 package**，可重跑 `sync-knowledge`（幂等）。候选表失败仅 WARN，不影响五表与终态。
- 知识库存储失败不得删除或回滚已验证日报与已发送飞书消息；保留错误、重试入口和 `SYNC_PENDING`。
- 全部完成后 `stage 8 PASS` + `knowledge_sync.status=SYNCED` + `run_status=RUN_COMPLETED`。

### 阶段 9：复盘与回填（V3.4.0）

- 触发：阶段 8 完成后（run_status=COMPLETED 做完整复盘；停于 `SYNC_PENDING` / `BLOCKED` / `FAILED` 做部分复盘，推送与数据维度如实标注未完成）。
- 按 `references/review-recitation.md` 执行五维复盘：**时间窗（三问核验）**、出刊、质量、数据、改进；时间窗核验重点比对预期增量窗口（周中 `{D-1,D}`、周一 `{D-3..D}`、跳跑顺延）与检索清单 `window`、候选 `event_date`、`official_sources_checked`、`recall_review` 的实际记录。V3.9.3 起还必须核验 `publication_metadata.recovery_reason == "missed_runs"` 时的 `missed_dates` 是否已全部补检入池；若存在缺失日期未补，`window_pass=false` 并在改进项写明原因。
- **耗时归因（V3.6.0）**：若检索清单含 `timings` 节点，复盘「出刊/改进」维度据此归因阶段2/3 子节点耗时（机器检索 vs 证据核验 vs 人机往返纠错），识别是否仍存在可消除的往返纠错（弱源/格式一致性问题），结论写入 `recap.improve`。
- 产出 `<work_dir>/<issue_id>/recap.json`，并将 `recap` 键（文件路径 + SHA-256 + `window_pass`）合并进 run-state；只增改该键，不动其他阶段状态。
- 完成汇报末尾附 **3 行复盘摘要**：今日产出 / 今日风险（时间窗三问结果）/ 今日改进（≥1 条教训）。
- 复盘为只读核验，不是第五道门禁：不修改已验证正文、不重写门禁结果、不回滚运行；发现正文错误提示用户走 `revise`。机器化（`run.py recap` 子命令、时间窗比对脚本）属独立脚本工作流，未落地前由执行 Agent 手工执行并如实说明。

## 7. P0 安全边界

以下任一违反即停止运行：

- 不读取、不打印、不复制、不写入任何凭据；配置和产物不得包含 secret。
- 不绕过自查、来源核验、人工确认或幂等检查。
- 不把搜索摘要、社交帖子或聚合稿当作关键事实的直接证据。
- 不伪造检索、来源访问、论坛回读、知识库同步或飞书发送成功。
- 不在状态不确定时重复创建主题、重复更新帖子或重复推送。
- 不因异常删除已验证正文、证据、论坛回读结果或审计产物。
- 已发布产物默认只读；修订必须使用 `revise` 并保留前后版本与哈希。
- **模板结构性变更门禁（V3.2.3）**：`references/report-template.md` 的新增/删除模块、板块结构变化必须先取得用户确认并留痕；执行会话不得为容纳当期内容擅自扩展模板（2026-09-07「编辑部排序/当日论点」教训：模板被改但用户从未认可，全部历史期也无此结构）。
- **日期合法性护栏（V3.3.1）**：`report_date` 必须通过 `validate_report_date()`（年份 2020..今日+1、格式合法）；假日期进入 plan/mkdir 属 P0 事故，进程必须拒绝写盘。测试套件一律隔离到临时工作目录，严禁触碰真实 work_dir（2026-09-08 教训）。
- 不修改 `scripts/`；脚本变更属于独立工作流。

## 8. 四道门禁

| 门禁 | 通过条件 | 失败动作 |
|---|---|---|
| G1 证据门 | 每个关键 claim 有合格 receipt；无空证据；日期与来源绑定正确 | 停止成稿或删除该 claim |
| G2 召回门 | 官方源扫描真实完成；重大漏报为空；去重完成 | 返回检索阶段 |
| G3 发布门 | 语义复核 PASS；preflight 总装预检 PASS；package dry-run PASS；脚本自查 PASS；报告 SHA 与正文一致 | 禁止论坛写入 |
| G4 推送门 | 论坛回读通过；stage 7 本地 package 已保存；人工明确确认；群级幂等通过 | 禁止飞书发送 |

规则细目以 `references/rules-matrix.md` 为准；不得用降级策略绕过门禁。

## 9. 外部动作人工确认

- 创建或更新论坛帖子前，展示目标、模式、刊期和待发布文件，取得本次明确确认。
- 论坛首页数据（阶段 6B）随论坛发布确认一并授权，须在发布前的审查材料里写明；用户另有指示时从其指示。
- 远端知识库首次启用、改变目标或覆盖远端条目前，取得明确确认。
- 飞书发送必须在论坛回读和知识库步骤之后再次单独确认；论坛发布确认不自动授权飞书。
- 删除、撤回、覆盖历史内容或改变共享权限必须另行确认。
- 只读检索、生成本地草稿、自查和本地 knowledge package 不要求外部动作确认。
- **自动化无人值守模式（V3.9.5，2026-09-29 教训）**：即使 automation system reminder 指示"Do NOT use AskUserQuestion; assume a default"，外部可见动作（论坛发布、飞书推送、知识库远端同步）**仍须暂停等待人工确认**。正确做法：在回复中输出审查材料（论坛发布前：标题 + 今日主线 + 今日要紧标题清单 + 去重报告摘要；飞书推送前：三群各自的群名/头条/专属标签/摘要），末尾标注「⏸️ 等待确认后执行」，然后**结束当前轮次**。用户的下一条消息（"OK"/"继续"/"确认"等）视为明确授权，再执行对应 `run.py` 子命令。禁止在同一轮次内连续执行 publish → push → sync-knowledge 三个外部动作而不暂停。

## 10. 结构化状态要求

状态文件固定为 `<work_dir>/<issue_id>/run-state.json`，完整 schema 见 `references/run-contract.md`。
必须记录：契约快照、当前阶段、阶段状态、产物绝对路径与 SHA-256、门禁结果、论坛标识、知识库状态、飞书群级状态、错误和时间戳。

只允许合法状态转移；每次写入使用同目录临时文件并原子替换，随后回读解析。
`resume` 遇到 `RUNNING`、外部响应丢失或哈希不一致时进入 `NEEDS_RECONCILIATION`，先回读外部系统，不自动重试写操作。
最终状态只可为 `COMPLETED`、`SYNC_PENDING`、`BLOCKED` 或 `FAILED`；不得用自然语言替代结构化状态。（V3.9.0 起推送后为 `SYNC_PENDING` 待同步，`sync-knowledge` 成功才落 `COMPLETED`；原 `WAITING_HUMAN_REVIEW` 已由「先推送、后同步」顺序取代）

## 11. 统一编排器（run.py）

V3.9.0 起，八阶段流水线由 `scripts/run.py` 收编为可独立调用的子命令（含搜索前 `precheck` 与推送后 `sync-knowledge`）。Agent 不再维护状态机；调用 `run.py` 即可推进阶段、跑门禁、产物、远端同步。

### 10.1 子命令（V3.9.0）

| 子命令 | 对应阶段 | 副作用 | 阻断门 |
|---|---|---|---|
| `precheck` | （搜索前） | **只读**环境预检：二维码 6 码/飞书群/群画像/知识库 Base/服务可达/凭据键位；不写盘、不回显凭据 | 二维码/群配置/画像/凭据 **BLOCK（exit 2）**；知识库未配置 WARN |
| `plan` | 1 | 冻结 run-contract + 调 fetch_recent 去重 | 无 |
| `retrieval` | 2-3 | 标记进入（检索/封面由执行 Agent 完成） | 无 |
| `review` | 4 | 标记进入（语义复核由执行 Agent 完成） | 无 |
| `self-check` | 5 | 先调 preflight_check.py，再调 self_check.py；self_check PASS 后追加 package dry-run；G1/G2/G3 与 preflight 摘要写入 run-state | **Preflight / package dry-run / G3 FAIL → fail closed** |
| `publish` | 6 | 再次调 preflight_check.py + package dry-run，PASS 后调 publish.py 论坛写入 | **WAITING_FORUM_CONFIRMATION → 需 `--confirm`** |
| `package` | 7 | **仅**调 build_knowledge_package 构建本地五表包并校验（不触远端） | 无（失败→`GATE_FAILED`） |
| `push-draft` | （论坛确认前） | 用占位链接预生成三群飞书草稿（不触网、不发送） | 无（stage 5 PASS 前置） |
| `push` | 8B | 用真实链接重新 prepare + 逐群幂等发送飞书群 | **需 `--confirm`**；**占位链接 fail closed**；论坛须 PUBLISHED |
| `sync-knowledge` | 8C | 远端五表 + 候选表同步 | **需 `--confirm`**；**须所有飞书群 SENT（`FEISHU_NOT_ALL_SENT`）** |
| `recover-missed` | 2 前置 | 扫描 candidates-pool 缺失检索日（V4.3 已脚本化，只读） | exit 5 = 有缺失，补检入池前禁止发布 |
| `dedup` | 2 末 | 已见索引三级查重，命中 SELECTED 改判 REJECTED_DUPLICATE，输出去重报告 | 无（结果须人工过目） |
| `seen-seed` | — | 冷启动：论坛近期期次灌入已见索引 | 无 |
| `accumulate` | 2-3 | 把当期检索候选追加进累积池并写入已见索引（**恒定执行、不看日历**，豁免工作日拦截与会话互斥） | 未先跑 dedup 时 WARN |
| `settle` | — | 工作日统一发布后结算累积池（精编按 importance 取前 30 或 `--selected-ids` 指定；V5 起当期 TRACKING / SIGNAL 一并结算；按 uid 落账并同步索引） | **需 `--confirm`** |
| `feed` | 6B | 已发布正文 → 首页 `air-pulse/1` JSON；不带 `--confirm` 只写本地预览，带 `--confirm` 更新底座帖并回读（5.2.0） | 论坛须 PUBLISHED；首页已是更新一期时拒绝覆盖 |
| `all` | 1-5 | 一条龙跑到 WAITING_FORUM_CONFIRMATION | 同 self-check |
| `status` | — | 摘要当前 run-state | 无 |

**外部可见动作顺序（V3.9.0 固定，5.2.0 增首页）**：论坛发布 → 首页数据（6B）→ 本地构包 → 飞书推送 → 远端知识库同步。
知识库同步**必须晚于**飞书三群全部 SENT；本地构包可与"等待飞书确认"并行。

阶段 9（复盘）暂无对应子命令；由执行 Agent 按 `references/review-recitation.md` 手工执行，recap 键合并进 run-state。`recap` 子命令与时间窗机器比对列入独立脚本工作流，未落地前不得声称已脚本化。

### 10.2 调用契约

```bash
python scripts/run.py <command> \
  --report-date YYYY-MM-DD \
  --timezone Asia/Shanghai \
  --env-file <abs path> \
  [--mode normal|backfill|revise|resume|holiday-batch] \
  [--dry-run] \
  [--confirm]   # publish/package/sync-knowledge/push-draft/push 必带
  [--session-id <会话标识>]   # 并行互斥：同一会话的多次调用保持一致；缺省读 AI_RADAR_SESSION_ID
  [--takeover]  # 并行会话 heartbeat 仍新鲜时强制接管（接管前须完成阶段 1 核验）
```

**会话互斥（V3.2.3，2026-09-09 补强）**：run-state 记录 `owner_session` 与 `heartbeat`；其他会话在 heartbeat 10 分钟新鲜期内执行状态变更命令会被 `PARALLEL_SESSION_BLOCKED`（exit 3）阻断。
- **必传 `--session-id`**：自动化固定用 `automation`，人工回复/接管固定用 `manual`（或 `human-<月日>`）。因为 `run.py` 的缺省值就是 `default`，若 automation 与人工都省略，两者均落 `default`，`owner==CURRENT_SESSION` 会直接放行，**互斥形同虚设**（2026-09-08 正因 automation 未传 `--session-id`，normal 被人工 backfill 覆盖）。此坑已由 automation prompt 固化 + 本条约定闭合。
- `--only-if-own`：可加参数限制"仅当本会话是 owner 才执行"，进一步防串写。

### 10.3 三条强制规则

1. **schema_version 动态读**：`run.py` 启动时从 `config/runtime.json` 读 `version`，写入 `run-state.json`；禁止硬编码。版本升级时必须同步三处：`SKILL.md` frontmatter `version`、`config/runtime.json` `version`、`CHANGELOG.md`，三者一致才视为落版（2026-09-08 曾因 SKILL/runtime 停在 3.3.0、仅 CHANGELOG 到 3.3.1 而漂移）。
2. **work_dir 优先级**：`AI_RADAR_WORK_DIR` 环境变量 > `runtime.json.work_dir` > 默认 `skill_dir/../ai-radar-daily`。
3. **--confirm 不可省**：`publish` / `package` / `sync-knowledge` / `push-draft` / `push` 涉及写盘或外部可见动作，未带 `--confirm` 直接退出；`--dry-run` 全程不写盘。

### 10.4 断点续跑

任意子命令可重入；run-state 记录 `current_stage` 与各 stage 状态。`publish` / `package` / `sync-knowledge` / `push` 在错误状态时会拒绝执行；`resume` 模式读取 run-state 自行跳过已完成阶段。

### 10.5 状态摘要

```bash
python scripts/run.py status --report-date YYYY-MM-DD          # 完整 JSON
python scripts/run.py status --report-date YYYY-MM-DD --brief  # 一屏摘要（≤8 行，日常用这个）
```

完整模式返回 issue_id、schema_version、run_status、current_stage、G1/G2/G3/G4 门禁、forum_status、
knowledge_sync、feishu_review、feishu_draft。`--brief` 把同样的信息压成 6 行（issue / RUN_STATUS /
STAGES 逐阶段 PASS 标记 / GATES / FORUM·FEISHU·SYNC·FEED / ERRORS），**日常查看一律用 `--brief`**，
避免整份 run-state 灌进上下文（见 10.7）。

> ⚠️ 缺口：`run-state.gates` 目前只写 G1/G2/G3，**没有 G4**。`--brief` 只显示实际存在的键，
> 不要把「GATES 行没有 G4」误读成 G4 通过——G4 仍须按阶段 6 的人工四项核验后手写。

### 10.6 改脚本/模板后的回归测试（V5.2.3）

本机**没有装 pytest**，`python3 -m pytest tests/` 会直接报 `No module named pytest`。测试是
`unittest` 风格，正确跑法（`tests/` 无 `__init__.py`，须先 cd 进去）：

```bash
cd /Users/sr_jm/gw_llm_ai_pulse/skills/ai-radar-daily/tests && \
  python3 -m unittest discover -s . -p "test_*.py"
```

- 基线：**248 项**。任何改动后跑全量，出现非 0 失败先确认是不是自己引入的。
- 判读：只看 `Ran N tests` / `OK` / `FAILED` 三行，中途的 JSON 是脚本正常输出，不是错误。
- 历史坑：`test_fallback_to_skill_parent` 曾因设了 `AI_RADAR_WORK_DIR=""` 不恢复而污染后续用例
  （单跑 PASS、全量 FAIL），已于 V5.2.3 修；若再出现「只在全量跑时 FAIL」，先查环境变量泄漏。
- **改版式 / 改尾部 / 改模板文案必跑**，且必须同步「模板 ↔ 脚本常量」对照表（见
  `references/report-template.md` 末节）——只改模板不改常量，下一期自查会 FAIL 或静默漏检。

### 10.7 上下文预算（V5.3.0）

**背景**：2026-11-04 单会话工具产出 **755,834 字符 ≈ 236K token**，吃满 262,144 窗口后在 09:44:37
报 `400 maximum context length` 中断，靠磁盘产物才恢复。根因不是「没注意节制」，而是**工具产出
以原始数据形态流回上下文**——Bash 独占 67%，其中 154/219 次是 inline `python3`（125 次重复读文件、
45 次数字符、40 次跑正则）。**纪律治不了，必须下沉为代码闸门**：让危险操作在操作上不可能发生。

**四条硬约束（不依赖自觉）**

| # | 约束 | 执行方式 |
|---|---|---|
| 1 | 单次工具产出 ≤ **4,096 字符** | 一律走 `rd.py`；禁止直接 `cat`、禁止整文件 `python3 -c "print(json.dumps(...))"` |
| 2 | 校验走固化命令，只回判定 | `verify.py` 取代「数字符 + 跑正则 + 反复查 state」的多轮往返（11-04 实测 124 轮 → 1 轮） |
| 3 | 阶段末必查水位 | 每阶段结束调 `ctxwatch.py`；**≥70% 立即收尾当前阶段、落盘交接物、开新会话**，不得启动新检索 |
| 4 | 阶段 2 与阶段 4 之间必须换会话 | 检索会话只交 `_检索清单.json` + `candidates-pool.json`；组稿会话**只读这两个产物**，不继承检索期原始搜索噪声 |

**三个工具**

```bash
# ① 输出闸门 rd.py：截断 + 全文落盘 + stderr 报预算（head/tail/grep/keys/jq/count/stat/run）
python3 scripts/rd.py keys candidates-pool.json        # 497KB JSON → 2,090 字符
python3 scripts/rd.py grep "AI雷达日报_YYYY-MM-DD.md" "^#### "
python3 scripts/rd.py jq   "AI雷达日报_YYYY-MM-DD_检索清单.json" recall_review.status window
python3 scripts/rd.py run  python3 some_probe.py       # 被调命令带 -c/--flag 亦可（argv 单独解析）

# ② 校验固化 verify.py：一次进程内跑完 自查 + 预检 + package dry-run + 产物齐备性
python3 scripts/verify.py --report-date YYYY-MM-DD               # 默认 ~24 行，exit 0=PASS/2=FAIL/3=缺产物
python3 scripts/verify.py --report-date YYYY-MM-DD --read-only   # 审计历史期次，绝不写盘
python3 scripts/verify.py --report-date YYYY-MM-DD --full        # 需要逐条明细时才展开

# ③ 水位守卫 ctxwatch.py
python3 scripts/ctxwatch.py                                     # OK(0) / WATCH(≥50%,1) / CHECKPOINT_NOW(≥70%,2)
```

- `rd.py` 超限全文落 `<work_dir>/.rd-spill/`，回显只给路径；**不要**顺着路径整文件回读，继续用
  `grep` / `keys` / `jq` 精确取数。全局参 `--max-chars` / `--no-spill` / `--quiet`。
- `verify.py` 默认写回 `_自查报告.md`；但当期 `publish-state.status == OK` 时**拒绝覆盖**（须 `--force-write`）。
  `evidence`（`_证据回执.json`）在 V5 已内嵌进语义复核，缺失**不计失败**。
- `ctxwatch.py` 读会话 JSONL 的 `usage.input_tokens`：`PEAK` 仅供追溯，**判定用最近一轮数值**
  （压缩后应回落→正确放行）；会话定位取 `~/.workbuddy/projects/<work_dir 斜杠转横线>/` 下 mtime 最新的 `.jsonl`。
  注意：argparse 用法错误也返回 2，消费退出码时结合 stderr 判断。
- **禁止把 `candidates-pool.json` / `_检索清单.json` / `run-state.json` 整份打印进上下文**——
  这是 11-04 中断的直接成因；需要看结构用 `rd.py keys`，需要看字段用 `rd.py jq`。

> 边界：WebSearch 结果摘要（11-04 为 149,231 字符）是检索的必要输入，**不可压缩**。三道闸门压的是
> 可完全避免的那条腿（Bash / 读文件 / 校验往返）。目标是把水位从 100% 降到 ~40%，不是降到 0。

## 12. 失败与汇报

发生缺口时加载 `references/exception-policy.md`，保留可恢复产物并明确列出：

- 已完成阶段、当前阻断点和对应规则 ID。
- 最后一个可信状态及其时间戳。
- 未执行的外部动作。
- 需要人工确认、权限修复或补充证据的下一步。
- 知识库未完成时明确写 `SYNC_PENDING`，不得把本地 package 表述为远端成功。
