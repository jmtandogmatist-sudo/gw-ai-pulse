# 规则矩阵（V5.2）

所有门禁和异常报告引用规则 ID。`执行者` 指首要责任层；脚本检查不能替代人工语义验证。

| ID | 规则 | 执行者 | 验证方法 | 失败策略 |
|---|---|---|---|---|
| R01 | 期标识唯一：`issue_id`、`report_date`、标题刊期、产物前缀和状态契约一致；修订沿用原 `issue_id` 并新增 revision。 | 编排器 + 人工复核 | 对照运行契约、文件名、H1、论坛标题和状态快照。 | 阻断；修正标识后从初始化重跑，不发布。 |
| R02 | 证据完整性：每个关键 claim 必须有可解析的 claim-evidence receipt；不得为空或只含搜索摘要。 | 编辑代理 + 人工语义复核 | 按 `evidence-standard.md` 检查 receipt 必填字段、原文摘录和可访问 URL。 | fail closed；删除 claim 或补证据，G1 FAIL。 |
| R03 | 来源绑定：来源必须直接证明对应日期、主体、动作和数字；同一来源不得虚假包装为独立双源。 | 编辑代理 + 人工语义复核 | 逐 claim 打开来源，核对原文位置、独立性、来源级别和引用范围。 | 改写至证据边界或淘汰条目；关键 claim 未绑定则 G1 FAIL。 |
| R04 | 日期口径：区分事件发生、文件签发、生效和报道日期；标题用事件日期；旧闻按规则标记。 | 编辑代理 + `self_check.py` | receipt 日期字段、标题日期和刊发窗口交叉核对。 | 删除、改写或标为跟踪报道；不确定则阻断该条。 |
| R05 | 召回完整：执行 d2/d4、三层检索、官方源扫描（`sources.json` + `fetch_sources.py`）、跨期去重（已见索引 `run.py dedup`）和重大漏报复核。 | 检索代理 + 人工复核 | 检索清单中的 query、官方源记录、候选决策和 `recall_review`。 | 返回检索阶段；`major_missed_events` 非空或虚假扫描时 G2 FAIL。 |
| R06 | 发布幂等：创建前查同刊期主题；更新前由 topic 回读真实首帖 `post_id`；状态不确定时先对账。 | 发布脚本 + 编排器 | 论坛搜索/回读、状态中的 `topic_id/post_id`、正文 SHA 和动作键。 | 禁止盲重试；进入 `NEEDS_RECONCILIATION`，人工确认后处理。 |
| R07 | 人工审查：论坛写入、远端知识库首次/变更写入、飞书发送按阶段分别取得明确确认。 | 编排器 + 人工 | 状态记录确认范围、时间和目标；确认不得跨动作继承。 | 保持等待态；不得执行对应外部写入。 |
| R08 | 状态原子写：状态用同目录临时文件、落盘、原子替换并回读；产物记录绝对路径与 SHA-256。 | 编排器 | JSON schema 校验、回读解析、哈希核对和状态转移检查。 | 停止后续动作；保留临时文件并进入 `BLOCKED`，不得猜测。 |
| R09 | 知识库同步（V3.9.0 拆分）：本地构包 `package` 在论坛回读后即可执行（stage 7，仅本地、不触远端）；远端同步 `sync-knowledge`（stage 8）**必须晚于飞书全部目标群 SENT**。远端未配置/回执非 SYNCED 标 `SYNC_PENDING`，不得伪造成功。 | 编排器 + 知识库同步步骤 | 校验本地 manifest、stage 7 PASS、群级 SENT 记录、远端配置、写入响应和远端回读。 | 保留已验证日报与 package；标 `SYNC_PENDING`，不删除、不回滚论坛与飞书内容；未达飞书前置即阻断（`FEISHU_NOT_ALL_SENT`）。 |
| R09b | 只读预检（V3.9.0）：搜索前执行 `precheck`，检查二维码 6 码、飞书群配置与画像、知识库 Base/schema、服务可达性、凭据键位；只读不写盘。 | 编排器 | 检查项逐条报告键名/存在性；凭据只校验键位、绝不回显值。 | 二维码缺失/群画像不完整/凭据缺失即 BLOCK（exit 2），修完再检索；知识库未配置降级 WARN。 |
| R09c | 资讯清单禁入正文（V3.9.0）：日报正文不得出现「全部资讯（候选全集）」模块或累积出刊行；候选全集仅留知识库记录。累积期改由检索清单 `publication_metadata` 承载。 | 编辑代理 + `self_check.py` | 正文扫描禁用标题与累积行；检索清单校验 metadata 结构。 | 正文出现即 G3 FAIL；删除该段并把累积信息写入检索清单后重跑自查。 |
| R10 | 判断标签可不适用：四透镜仅在证据和事件确实支持时使用，可标 `N/A`；不得硬贴或引入新事实。 | 编辑代理 + 人工复核 | 对照事实层、标签适用条件和反例；检查判断 receipt。 | 移除标签或改为 `N/A`；判断不影响事实层，必要时省略当日论点。 |
| R11 | 自查哈希绑定：论坛发布所用正文必须与 PASS 报告 `SOURCE_SHA256` 一致。 | `self_check.py` + `publish.py` | 重新计算 SHA-256 并比较报告。 | G3 FAIL；重新语义复核和自查。 |
| R12 | 已发布产物只读：除 `revise` 外不得改写；修订保留母稿、前后哈希和回读证据。 | 编排器 + 人工 | 状态 mode、revision、论坛 raw、产物哈希历史。 | 阻断改写；切换到获授权的 `revise` 流程。 |
| R13 | 飞书群级幂等：键为 `topic_id:chat_id`；发送结果逐群串行落盘。 | 推送脚本 + 编排器 | 回读 `feishu-push-state.json`，核对 SENT/FAILED 和 message_id。 | 已 SENT 跳过；不确定先对账，禁止重复发送。 |
| R14 | 凭据隔离：文档、配置、日志、状态和 package 不含 secret；凭据只由外部运行环境提供。 | 全流程 | 静态扫描敏感字段、人工检查配置与日志。 | 立即阻断并清理新产生的泄漏副本；不得在报告中复述 secret。 |
| R15 | 阅读长度（V4.1）：金风相关、工具与做法各模块正文不超过 300 字，行业速递不超过 600 字；标题、来源链接、`[details]` 折叠内文及固定属性说明不计。今日要紧不设板块上限，但每条「对我们」行 ≤90 字。 | 编辑代理 + `self_check.py` | 剔除标题、来源行与 `[details]` 块后计算非空白字符数；判断行单独计数。 | 超限即 G3 FAIL；压缩正文、同步语义复核与哈希后重跑自查。 |
| R16 | V5 三层版式（5.0.0）：`[wrap]` 各占一行、成对闭合、一行不得两个；尾部固定模块在 `air-daily` 外；数字卡与可信度条等于实际卡数；精编按 V4 证据门禁；跟踪日期在 [D-7, 窗口起点)；雷达类别八选一、带来源名、可单源但如实标可信度；跟踪 / 雷达标题与 URL 须绑定检索清单 TRACKING / SIGNAL 候选。 | 编辑代理 + `layout_v5.py`（经 `self_check` / `preflight_check` 调用） | `check_structure` + `check_against_search_log`，再经 `to_v4()` 走原 V4 门禁。 | 任一项失败即 G3 FAIL（preflight 报 `V5_LAYOUT`）；按提示改正文或检索清单后重跑。 |
| R17 | 社区信号源（5.0.0）：`fetch_sources.py` 的 signal 层抓取失败须如实记 ERROR；高热信号未入任何一层须在 `recall_review.notes` 说明。 | 检索代理 | `_源抓取.json` 的 `sources[].status` 与 `signal_items`；检索清单 `recall_review`。 | 不得伪造已检查；遗漏高热事件计入 G2 复核。 |
| R18 | 无残留占位符（5.1.0）：正文不得含“待人工替换”“FORUM_URL_PENDING”“待回填”“TODO”“TBD”。 | `self_check.py` | 全文子串扫描。 | G3 FAIL；补全后重跑。 |
| R19 | 来源链接可达（5.1.0）：正文链接不得落在 `config/link-reachability.json` 的 blocked 名单。 | `self_check.py` + 运营维护名单 | 解析正文（尾部固定模块除外）全部链接的域名。 | G3 FAIL；换成可访问来源，并按 §5.6 如实下调可信度。 |
| R20 | 事件键唯一可比（5.1.0）：候选 `event_key` 须按内容生成，禁止逐日编号。 | `run.py dedup` + `preflight_check.py` | 正则识别 `signal-1`、`track_2` 等。 | `dedup` 直接退出 2；preflight 报 `CANDIDATE_EVENT_KEY_GENERIC`。 |
| R21 | 首页数据与日报一致（5.2.0）：首页 JSON 只从已发布正文生成；剔除公司网络不可达链接；底座帖幂等更新、写后回读；不得用旧期覆盖新期。 | `homepage_feed.py` + `run.py feed` | 正文三层逐卡映射、`validate_feed`、底座帖哈希回读、`issue_date` 比较。 | 仅首页不更新（记 `run-state.homepage_feed`），不阻断构包、推送与同步；`FEED_OLDER_THAN_LIVE` / `FEED_READ_FAILED` 须人工处理。 |
| R22 | 尾部模块顺序（5.2.3）：固定为 能力地图 → 技术交流群 → `[grid]` 6 码 → 免责声明 → 署名两行 → 往期合集；≤2026-11-03 期次按旧形态（署名 → 合集 → 免责声明）豁免，不回溯判 FAIL。 | `self_check.check_tail_order` | 按标记在正文中的出现位置逐个比较，缺项或逆序即报。 | G3 FAIL；按 `references/report-template.md` 尾部顺序重排后重跑。 |
| R23 | 飞书日报链接固定（5.2.1）：推送文案的「阅读全文」与「往期合集」一律为 `https://aicopilot.goldwind.com.cn:3211/latest`；两处真源（`prepare_feishu_push.DAILY_URL` 与画像 `signature.block`）必须一致。payload 仍记真实 `article_url`/`topic_id`。 | `prepare_feishu_push.py` | 生成前比对 `DAILY_URL` 是否在 `signature.block` 内；生成后逐群断言文案含 `DAILY_URL`。 | 任一不满足即 `ValueError`（生成失败，不发半成品）；**不得**因文案是固定链接而放松 `PLACEHOLDER_LINK_BLOCKED`/`FORUM_REQUIRED`。 |

## 门禁映射

- G1 证据门：R02、R03、R04、R10。
- G2 召回门：R05、R17、R20。
- G3 发布门：R01、R07、R08、R11、R12、R14、R15、R16、R18、R19、R22。
- G4 推送门：R06、R07、R08、R09、R13、R14、R23。
- 首页（不属任何门禁、不阻断）：R21。
