# Changelog

## 5.3.4 - 2026-11-12

**尾部定稿（用户 11-12 版式为准）+ 二维码必须连续图片行**

- **尾部标准以 2026-11-12 实际发布版为准**（`topic 721`），四项变更：
  1. 能力地图首行文案 `集团AI基础能力支持` → **`AI基础能力支持`**（去「集团」前缀）。
  2. 末行去掉 `**欢迎投稿**：` 加粗前缀，直接以 `欢迎分享发布科普内容…` 开头。
  3. **删除 `[往期合集](…)` 行**——往期入口由论坛分类页 `/c/15-category/15` 与首页承载，正文不再挂。
  4. `self_check.TAIL_ORDER_MARKS` 同步移除「往期合集」标记；旧版 `LEGACY_TAIL_ORDER_MARKS` 保留（历史期只读校验）。
- **飞书 `SIGNATURE_BLOCK` 同步去掉 `**欢迎投稿**：` 前缀**，其余不变（`📚 [往期合集](DAILY_URL)` 保留，属推送引流入口，与正文删除的那行不是同一处）。
- **新增硬规则：二维码区块禁止写成 Markdown 表格、禁止 `\![…\]` 转义**。本期实测：表格形态会让发布脚本 `apply_qr_replacements` 报「正文缺少以下群的二维码引用」，且即便强发也会因每图落进单元格而前台破图；转义则让图片退化为纯文本。模板末尾已写入明确警示。
- 回归：`unittest` **276 项 OK**；`verify.py` 格式类 **11/11 PASS**。

## 5.3.3 - 2026-11-11

**补「能源全产业链 AI」与「风电竞品 AI」两条检索线（用户指出长期缺口）**

- 缺口实证（先量化再动手）：`config/sources.json` 27 条源中 **21 条是 AI 厂商官方阵地**，
  能源侧仅 2 条国家能源局（11-10 才补），**风电竞品 0 条**；最近 9 期检索清单里
  **竞品候选 9 期全 0**、含能源词的候选仅 1–5 条（且多为「AI 数据中心用电」这类反向线索，
  不是能源行业自身用 AI）。用户「怎么没有能源全产业链 / 竞品信息」的感知与数据完全吻合。
- 新增 6 条 `page` 源（均实测可达，`verified` 标注区分）：`cpnn-news` 中国电力报 /
  `xinhua-energy` 新华网能源 / `china5e-news` 中国能源网 / `vestas-news` /
  `siemensgamesa-news` / `gevernova-news`。
- 关键实测（决定源取舍，避免把抓不到的空源塞进清单）：
  - `cpnn.com.cn` 首页静态 HTML 含 **198 条标题、6 条 AI+能源**（国家能源局「人工智能+」能源试点
    项目清单、智能体发展自律公约、矿山「人工智能+」应用攻关、南网「慧诊小助手」智能体等），
    AI 密度约 3%，是本次补源里质量最高的一条。
  - `news.cn/energy` 静态可抓 35 条，承接发改委/能源局发文转载，适合作第三方互证源。
  - `wind.in-en.com`（国际能源网）**纯 JS 渲染**（仅 4KB HTML），静态抓不到列表 → 未纳入。
  - `envision-group.com/cn/news` 同样 JS 渲染（1.4KB）→ 远景未纳入，改由关键词层覆盖。
  - `siemensgamesa.com/newsroom` 为 404，正确路径是 `/en-int/newsroom`（已在 note 写明）。
  - `gevernova.com/news` 是导航聚合页（33 条长标题全是板块入口），**未找到真实新闻列表** →
    标 `verified: false`，抓不到时须记 ERROR，**不得据此判定「GE 无 AI 动态」**。
  - Vestas 新闻页 AI 字面命中近零 → 竞品 AI 多以 `digital solutions` / `condition monitoring`
    题名出现，故竞品阵地**不做 AI 字面硬过滤**，改按语义判读；并明确「竞品长期无候选属正常，
    不得为凑数放宽到非 AI 的公司新闻」。
- `references/retrieval-strategy.md`：A 层新增「能源全产业链 AI」「风电竞品 AI」两组关键词，
  C 层官方源清单同步补两组主体。判定口径：能源侧按**落地价值**（谁在用/规模/指标）而非技术新颖度打分。

**尾部改版：删互动尾缀、换投稿行文案（用户指定）**

- 改动一：往期合集行**只保留链接**，删除其后 `| 💬 有想了解的 AI 话题欢迎留言 | 🤝 业务侧 AI 需求可随时交流`。
  群互动问题并未丢失——三群的差异化提问由 `feishu-group-profiles.json` 的 `question` 字段独立承担，
  论坛正文不再重复。
- 改动二：投稿行改为 `**欢迎投稿**：欢迎分享发布科普内容或者你的心得与技巧到 AI 社区，我们一起交流、探讨！`
  原句「投稿至【谭健明】…送出小礼品」随之取消（指名投稿人与实物激励不再出现在公开正文）。
- 落点四处（缺一处就会各阵地不一致）：`references/report-template.md`、`references/feishu-group-profiles.json`
  的 `signature.block`（实际生效）、`scripts/prepare_feishu_push.py` 的 `SIGNATURE_BLOCK`（兜底）、
  `tests/test_v5_layout.py` 的样例尾部。
- 新增门禁：`self_check` 增 `SUBMIT_LINE` 文案校验 + `TAIL_INTERACTIVE_TOKENS` 反向校验
  （命中旧尾缀即 FAIL），防止照抄旧稿把删掉的互动尾缀带回来。全量测试 276 OK。

## 5.3.2 - 2026-11-10

**追加修复（2026-11-11）：run-state `confirmations` 兼容 dict 与 list**

- 现象：`feed --confirm` 已成功更新底座帖（post 797、回读一致），但随后抛
  `UNHANDLED 'str' object has no attribute 'get'`，`homepage_feed` 状态与确认记录全部丢失，
  `run.py status --brief` 显示 `FEED: None`，与事实相反。
- 根因：`confirmations` 在 V5.3.2 中已是 dict（键=确认类型），但 `feed` / `publish` / `push`
  三处仍按 list 遍历 `any(c.get("type") == ...)`，对 dict 迭代得到字符串键即崩。
  危险点在于崩溃发生在**外部写动作之后**：`push --confirm` 三群已发完再崩，状态会停在
  SENT 之前，极易触发重复推送。
- 修法：新增 `record_confirmation(state, key, payload)`，dict 形态按键 `setdefault`、
  list 形态按 `type` 去重追加，三处调用点统一改走该函数。全量测试 276 OK。

**补政策监管检索线：国家能源局（config 变更，不动流程代码）**

- 起因：用户问「国家能源局这样的网站有没有纳入检索范围」。核查结论——**没有，且是结构性空白**：
  `config/sources.json` 27 条源全为 AI 厂商官方阵地（21 条 T0 + 4 条趋势社区 + 2 条），
  无任何 `.gov.cn`；`retrieval-strategy.md` §3 三层里，能源仅 A 层一条关键词
  `AI 风电 新能源 电力 大模型`，B 层 7 条全为 Agent/运行时。近 5 期正文对
  `gov.cn|能源局|发改委|工信部|能监` 命中 **0**，印证政策线在系统里被完全绕开。
- 风险判断：政策类线索（AI+能源试点、消纳与电力市场规则、算力能耗与绿电、技术出口管制能源口）
  **只有一手源**，媒体转述普遍滞后并丢失适用范围与生效条件，与「宁可少报、不可误报」冲突，
  实质等于系统性放弃该赛道。
- 变更（只改配置与策略词，不改任何脚本逻辑）：
  - `config/sources.json` → `1.3.0`，新增 `nea-news`（国家能源局 · 新闻中心，
    `type=page`，`tier=T1`，`topics=[政策监管,能源行业]`，`verified=true`）。
  - 实测依据：`/xwzx/index.htm` HTTP 200、静态 HTML 含 29 条带日期路径条目
    （`/YYYYMMDD/<hash>/c.html`，覆盖约 8 周）→ 可按日期前缀判窗口，无需渲染；
    4/29 命中 AI·算力·新能源 相邻关键词，其中「国家能源局发文推动煤电与新能源融合发展」
    即政策发文经新闻中心发布的实例。
  - 新增 `nea-policy`（`policy/tz.htm` 通知/公告/解读）但 **`enabled:false`**：三栏目为
    Vue 动态渲染，静态 HTML 仅 1.3KB 无条目，带 UA/Referer 亦无效；启用前提是接入浏览器渲染，
    过渡方案由 `nea-news` 覆盖。
  - `references/retrieval-strategy.md` §3A 增「能源政策」query 两条，并与 `nea-news` **配对**：
    关键词负责发现、官方源负责核实；只有媒体源、无法在 `nea-news` 找到对应发文的政策候选，
    一律 `REJECTED_UNVERIFIED`，不靠转述入稿。
- 门禁影响：**无**。`nea-news` 不在 `REQUIRED_OFFICIAL_SOURCES` 内，preflight 的
  `missing = [name for name in REQUIRED_OFFICIAL_SOURCES ...]` 逻辑不变，G1–G4 强度不变；
  但**检索清单 `official_sources_checked` 应逐期登记其核查结果**（含负面结论写 `latest_update`）。
- 验证：`fetch_sources.py --only nea-news` → `manual_pages:1`、`errors:0`、不污染 `sources` 状态计数；
  `python3 -m unittest discover -s tests -p "test_*.py"` → 见本节末尾合并验证结果。

**同版本追加：日报标题改版为「钩子 \|AI雷达M.D」**

- 起因：用户要求「获取一条最吸引眼球的标题，格式 `xxxx |AI雷达11.10`」，并把它固化为规则。
  原格式 `AI 雷达日报｜11.10` 品牌清晰但零信息量——群里看到标题无法判断是否值得点开。
- 新格式：`{钩子} |AI雷达{M}.{D}`（如 `5 次示范就到 95%，机器人不用先攒大数据 |AI雷达11.10`）。
  钩子逻辑五步写进 `SKILL.md` §5.9：取材只限「今日要紧」→ 选型优先级（反直觉量化门槛 >
  价格落差 > 首次/唯一/最大 > 生态位变化）→ 必含正文可验证数字 → 陈述句 6–24 字 →
  不得比正文更绝对（厂商自测数据不加褒贬、命中营销词表即 FAIL）。
- **风险点（本轮最重要的发现）**：标题格式被硬编码正则匹配在 **5 个脚本、8 处**，
  且失效方式都是「静默错判」而非明确报错：
  1. `layout_v5` 认不出 H1 → V5 版式整段判失败；
  2. `fetch_recent` 用子串 `"AI 雷达日报"` 过滤自家帖 → 自己的日报被当成社区信号源，跨期去重失效；
  3. `prepare_feishu_push` 的 `date_tag` 用宽松 `\d+\.\d+` → 钩子里的「0.28 元」被当成日期；
  4. `build_knowledge_package.validate_dates` 旧正则失配 → 日期一致性校验被静默跳过。
- 解法：新增 `scripts/title_spec.py` 作**单一真源**（格式常量 / 解析 / H1 提取 / 日期锚定 /
  钩子校验 / CLI），其余脚本一律 `import title_spec`，删掉各自正则；`publish` 改为
  「标题从 H1 读 + 校验格式与日期」而非由 `report_date` 拼装。旧格式永久兼容（历史期次可复验）。
- `self_check` 新增 1 项「格式」检查（钩子规则，历史格式自动豁免）→ 自查项 115 → **116**。
- 今日（11-10）已按新格式改名，并同步：`body_sha256` 重算 → self-check 重跑 → knowledge package
  重建（`issue.title` 同步）→ `recap` 正文哈希刷新 → `verify` **PASS**。
  论坛 topic 715 标题属于外部动作，未擅自动手，等确认后 `PUT /t/715.json`。
- 附带修正：`run.py self-check` 收尾会把已发布期次的 `run_status` 写回
  `WAITING_FORUM_CONFIRMATION`（按其"未发布"分支），本次已在 `run-state.notes` 留痕后人工恢复。
- 验证：`tests/test_title_spec.py` 新增 **28 项**；全量
  `python3 -m unittest discover -s tests -p "test_*.py"` → **276 OK**（248 存量 + 28 新增）。

## 5.3.1 - 2026-11-09

**读路径凭据容错（2026-11-09 实证：论坛 API Key 被服务端吊销）**

- 现象：`Api-Key` + `Api-Username: U8570` 对 `/latest.json`、`/search.json`、
  `/session/current.json` 全部 `403 invalid_access`；同一 URL 匿名 GET 返回 200。
  即密钥在服务端失效，而非本地配置问题（键位、长度、64 位 hex 格式均正确）。
- 影响：`run.py plan` 在 `fetch_recent` 处硬失败，整条检索通道随凭据一起停摆。
- 修复（`fetch_recent.curl_json`）：鉴权读取抛错时**降级为匿名只读**重试一次，并向 stderr
  打一行 WARN。仅作用于**读**接口（`/search.json`、`/t/<id>.json`，Discourse 对匿名开放）；
  `publish` / `feed` / `sync-knowledge` 走各自的 `curl_json`，**不受影响、仍 fail closed**，
  故不会因降级而掩盖凭据问题。
- 回归：`python3 -m unittest discover -s tests -p "test_*.py"` → **248 OK**。

## 5.3.0 - 2026-11-04

把「上下文溢出」这一 11-04 实际中断的成因，从**纪律要求**改成**代码闸门**。

**新增脚本（`scripts/`，全部为新增文件，未改既有流程）**

- **`rd.py` 输出闸门**：`head/tail/grep/keys/jq/count/stat/run` 八子命令。单次回显硬上限
  **4,096 字符**，超限自动截断并把全文写入 `<work_dir>/.rd-spill/`，回显只给路径与继续定位的建议；
  每次调用向 stderr 报一行 `[rd] <label> → 回显 N/M 字符`，让上下文预算可见。`run` 子命令单独解析
  argv，避免被调命令的 `-c` / `--flag` 被 argparse 吞掉。
- **`verify.py` 校验固化（verdict-only）**：一次进程内跑完「逐条自查（复用 `self_check.run_checks`）
  + 总装预检（复用 `preflight_check.run_preflight`，含 package dry-run）+ 产物齐备性 + 正文 sha256 +
  封面字节数 + 6 码二维码存在性」，默认只回类别汇总与失败明细（约 24 行）。退出码 0=PASS / 2=FAIL /
  3=输入产物缺失；默认写回 `_自查报告.md`，但当期 `publish-state.status == OK` 时拒绝覆盖（须
  `--force-write`），`--read-only` 绝不写盘。`evidence` 独立回执在 V5 已内嵌进语义复核，列入可选产物。
- **`ctxwatch.py` 水位守卫**：读会话 JSONL 的 `usage.input_tokens`（11-04 峰值 261,382 与复盘数字
  精确吻合，口径可信），输出水位条与建议；三档 `OK`(0) / `WATCH`(≥50%,1) / `CHECKPOINT_NOW`(≥70%,2)。
  只扫文件尾部 4MB 控制自身开销；判定用最近一轮数值，压缩后回落即正确放行。

**编排器**

- `run.py status --brief`：把 run-state 压成 6 行一屏摘要（issue / RUN_STATUS / 阶段 PASS 标记 /
  GATES / FORUM·FEISHU·SYNC·FEED / ERRORS），日常查看不再需要整份 JSON。

**文档**

- `SKILL.md` 新增 **10.7 上下文预算**：四条硬约束（单次产出 ≤4,096 字符 / 校验走固化命令 /
  阶段末必查水位且 ≥70% 强制收尾换会话 / 阶段 2 与阶段 4 之间必须换会话并只经磁盘交接）。
- `SKILL.md` 10.5 状态摘要补 `--brief`，并标注 `run-state.gates` 目前只写 G1/G2/G3、**无 G4** 的缺口
  （避免把「GATES 行没有 G4」误读成 G4 通过）。

**排程口径漂移更正（2026-11-04 溯源）**

- **事实**：检索通道触发时点为**每日 08:30**（`FREQ=DAILY;BYHOUR=8;BYMINUTE=30`，含周末——检索每天
  照常，仅工作日发布）。10:00 是 2026-09-08 确立的**原始**口径，排程在 09-08 与 10-10 之间改为 08:30，
  但下列文档从未同步，形成数周漂移：`SKILL.md` §5 检索/发布解耦表（误记「每天 10:00」）、
  `references/retrieval-strategy.md` §1（小标题与正文均误记，且论证本身建立在 10:00 上）、
  `scripts/self_check.py` `allowed_event_dates` docstring（3 处）。项目长记忆另误记为「**工作日** 10:00」
  ——时点与范围双错。
- **更正**：上述三处活文档已改为 08:30，且不再把具体时点写进论证（改为「执行时点」表述 + 指向真源），
  避免二次漂移。
- **新增防漂移机制**：`config/runtime.json` → `schedule`（声明镜像）+ `run.py precheck` 新增
  「排程声明」检查项，**每次运行打印声明时点与 rrule**，使排程漂移在运行日志里可见。
- **不改写历史**：`CHANGELOG` 中 2026-09-08 那条「每日 10:00 执行」是当时准确的记录，按原则保留原样；
  完整溯源与复盘见 `<work_dir>/.workbuddy/memory/2026-11-04_排程口径漂移复盘.md`。

**背景数据**：11-04 单会话工具产出 755,834 字符 ≈ 236K token（占窗口 90%，Bash 独占 67%，其中
154/219 次 Bash 是 inline python），09:44:37 撞 262,144 上限中断，恢复耗时约 17 分钟。三道闸门
不触碰 WebSearch 结果摘要（不可压缩），只压可完全避免的那条腿。

## 5.2.3 - 2026-11-03

把 11-03 全天踩到的坑落成门禁与文档，重点是把「靠人看」的三类检查改成「脚本拦」。

**新增门禁**
- **R22 尾部模块顺序**（`self_check.check_tail_order`）：旧实现只校验尾部模块**是否存在**，
  11-03 改版把免责声明移到署名之前，顺序变了完全无感。现按「能力地图 → 技术交流群 → `[grid]` 6 码 →
  免责声明 → 署名两行 → 往期合集」逐个定位比对，逆序即 FAIL 并指出谁该在谁之前。
  ≤2026-11-03 期次走旧形态（署名 → 合集 → 免责），且标题/免责各接受新旧两种写法，
  兼容「标题已换、顺序仍旧」的过渡期；已发布历史期按 R12 只读，不回溯判 FAIL。
- **R23 飞书日报链接一致性**（`prepare_feishu_push`）：`DAILY_URL` 与画像 `signature.block` 是两处真源，
  只改一处会出现两个不同链接且无人发现。现生成前比对、生成后逐群断言文案含 `DAILY_URL`，不一致即
  `ValueError` fail closed（不产出半成品 payload）。

**文档**
- SKILL §5.8「官方源『发布阵地』而非『官网首页』」：11-03 漏收 DeepSeek Harness Creator Mode 的根因是
  只登记了 `api-docs.deepseek.com`（无 news 流），真实首发在 GitHub Releases + 官方 X。写入三类阵地
  逐家确认、「阵地缺失」的三个识别信号、跟踪线索复检要求。
- SKILL 阶段 6 补 `NEEDS_RECONCILIATION` 处置：二开站点首帖 `raw` 返回空 → 哈希必不等 → 该状态是常态，
  帖子通常已发出，**禁止直接重发**；给出「拉分类列表 → 逐项核对 → 重建 publish-state → 修订 run-state」
  四步，对账结论须进 recap。
- SKILL §10.6：本机无 pytest，给出 `cd tests && python3 -m unittest discover -s . -p "test_*.py"` 的
  正确跑法与 248 项基线，避免下次再撞 `No module named pytest`。
- `report-template.md` 规则 6：H1 日期明确为 `M.D`（月日都不补零），写明 `11.03` 会触发
  `PUBLISH_BLOCKED`，消掉与 `publish.title_for_date()` 的口径分裂。

**配置**
- `config/sources.json` 补 `deepseek-ai/DeepSeek-Harness` 的 GitHub Releases（24→25 条，已实测
  Releases API 返回 3 个 tag，最新 `dsh-v0.2.1-rc.5`）。

**修复**
- `test_run.TestWorkDirResolution::test_fallback_to_skill_parent` 长期"偶发失败"的真因：用例把
  `_load_work_dir` 打桩返回 `""`，而 `get_work_dir()` 是 `env or _load_work_dir()`，打桩成空串后
  `Path("").resolve()` 落到 cwd，断言本就不成立；此前"通过"全靠别的用例把 `AI_RADAR_WORK_DIR`
  泄漏成临时 work_dir 造成的**假阳性**。现改为验证真实回退分支（patch `RUNTIME_PATH`），并给该类加
  setUp/tearDown 隔离环境变量。
- `test_feishu_push_dedup` 的签名档 fixture 补固定日报链接，让新规则在测试里同样生效。

**验证**：248 项全量 **OK**（首次无遗留失败）；尾部顺序四条分支实测（新形态 PASS / 新形态逆序 FAIL /
旧形态+旧日期 PASS / 旧形态+新日期 FAIL）；链接不一致时 prepare 正确 fail closed。

## 5.2.2 - 2026-11-03

尾部固定模块按用户新版重排（用户提供的原文替换），连带修复三处「尾部起点由 H1 降为 H3」引发的解析失效。

**变更**
- 新尾部（顺序固定）：`---` → `### 集团AI能力地图`（6 平台）→ `### 技术交流群` → `[grid]` 6 码（图片连续成行、无空行）→ `---` → 免责声明 → 双层署名 → 跟踪语 → 投稿 → 往期合集。
  - 能力地图标题层级 `#` → `###`；原 `# 集团AI能力地图` 与标题 H1 抢 H1，现在全文只有首行一个 H1。
  - 群码模块标题去掉「（扫码加入）」后缀。
  - 免责声明 `【AI日报- Agent】` → `【AI日报-Agent】`（去掉连字符两侧空格），且位置前移到署名之前。
  - 「AI基础能力支持」去掉 `|本地大模型库、行业/场景算法库、MCP Server广场、skills库` 副标题；「Agent/工作流搭建平台（Dify）」改名「AI智能体开发平台」。
- 同步点（改文案必须一起动）：`self_check.TAIL_HEADINGS`、`self_check.CONTACT_LINE`、`self_check` 的 `body_part` 切分、`layout_v5.TAIL_START`、`fetch_recent.TAIL_MARKERS`、`build_knowledge_package.section_text` 与 `parse_events` 的 heading 正则、四个测试 fixture、`report-template.md` 规则 1/22。

**修复（H1→H3 连带，均为本次实测暴露）**
- `layout_v5`：尾部判定原为 `s.startswith("# ") and s.startswith(TAIL_START)`，双条件在 `###` 下恒为假 → 报「V5 尾部能力地图缺失或被包进 air-daily 容器」。改为单条件。
- `build_knowledge_package.section_text`：原用 `^#{1,2}\s+` 找板块终点，H3 尾部不再截断 → 尾部被吞进「行业速递」，`parse_events` 把 `### 技术交流群` 当事件 → `PREFLIGHT_INPUT_INVALID：《技术交流群》事实摘要为空`。改为显式以 `TAIL_START` 为界，并在 heading 正则加 `技术交流群`/`集团AI能力地图` 负向断言。
- `self_check` 的 `body_part` 原按 `# 集团AI能力地图` 切分，改 H3 后切不开 → 尾部链接会混入「公司网络不可达」检查。改为 `###`。
- `fetch_recent.TAIL_MARKERS` 补齐 `###` 形态，避免跨期去重把尾部链接算进正文。

**验证**
- 用 11-03 真实正文套用新尾部 + 真实语义复核/检索清单/6 码/封面跑 `self_check` → **PASS**（含 V5 版式、6 码识别、可达性、跨期去重）。
- 全量 248 项测试：除既有 flaky `test_fallback_to_skill_parent` 外全绿。

## 5.2.1 - 2026-11-03

飞书推送里的日报链接固定为站点 `/latest`，不再随期变化。

**变更**
- `prepare_feishu_push.py` 新增 `DAILY_URL = https://aicopilot.goldwind.com.cn:3211/latest`，`ARCHIVE_URL` 与其同值；`build_message` 的「阅读全文」由 `article_url` 改为固定 `DAILY_URL`。
- `references/feishu-group-profiles.json` 顶层 `signature.block` 的「往期合集」链接同步改为 `/latest`（该字段经 `default_sig` 注入每群，是实际生效的签名档；脚本内 `SIGNATURE_BLOCK` 为兜底，两处已同步）。
- 实测 `/latest` 与 `/latest.json` 均返回 200；payload 仍写入真实 `article_url`/`topic_id`，幂等键、审计与 `PLACEHOLDER_LINK_BLOCKED` / `FORUM_REQUIRED` 门禁不受影响（门禁校验的是发布状态字段本身，不依赖文案扫描）。
- 背景：当日 topic URL 每期变化，二开站点偶发跳转异常，用户 2026-11-03 要求统一入口。

**已知测试噪声（非本次引入）**
- `tests/test_run.py::TestWorkDirResolution::test_fallback_to_skill_parent` 全量/单文件运行 FAIL、单方法运行 PASS，属同文件内 `AI_RADAR_WORK_DIR` 环境变量未清理的顺序依赖，与链接改动无关；待单独修。

## 5.2.0 - 2026-11-02

论坛首页「AI 雷达」自动更新：日报发布后，把同一期正文转成结构化数据写进首页数据底座帖。

**新增**
- `scripts/homepage_feed.py`：`build`（V5 正文 → `air-pulse/1` JSON，只写本地）与 `publish`（更新底座帖第 1 楼，写后回读核对哈希）。字段：今日主线、今天发生了什么（3 条）、今日焦点、今日值得看（4 条）、精编、本周跟踪、信号雷达、社区今日、今日一问、下一轮更新时间。
  - 取材只用已发布正文；检索清单只补信号热度，缺清单时从来源标签末尾数字兜底（`Hacker News 866`、`GitHub 今日 +1526★`）。
  - 公司网络不可达链接（`link-reachability.json`）剔除并告警。
  - 「今日值得看」：工具与做法优先，再按热度每类取一条补足，传闻待证不进推荐（各源热度量级不同，单纯按热度会被 GitHub 星数挤满，11-02 实测）。
  - 简报句式：标题 + 首句；标题或首句已有冒号时用句号连接，首句以分号收尾时改为句号（11-02 实测出现“A：B：C”和“…；”）。
  - 「社区今日」同时认模板写法与 11-02 实刊写法 `* **【场景】**：[标题](URL)`。
  - 幂等与防倒退：哈希相同 NOOP；底座帖已是更新一期时拒绝覆盖（`FEED_OLDER_THAN_LIVE`，`--allow-older` 放行）；读不到底座帖原文即停，不盲写。
- `run.py feed`（阶段 6B）：论坛 PUBLISHED 前置；无 `--confirm` 只生成 `AI雷达日报_YYYY-MM-DD_首页数据.json`；`--confirm` 调 `homepage_feed.py publish`，结果记 `run-state.homepage_feed` 与 `confirmations`。失败不改 `run_status`、不阻断后续阶段。`status` 摘要增加 `homepage_feed`。
- `precheck` 增「首页数据」检查（WARN 级）。`config/runtime.json` 增 `homepage_feed`（话题 632、更新时刻 09:30）。
- 规则矩阵 R21；SKILL.md 阶段 6B、确认口径与外部动作顺序（论坛发布 → 首页 → 构包 → 飞书 → 知识库）。

**修复**
- `tests/test_run.py` 原写死 `/Users/sr_jm/Desktop/GlodWind/gw-ai-pulse/skills/ai-radar-daily`：在其他目录测试新版本时实际导入的是已安装的旧版 `run.py`，单独运行该文件还会因找不到模块报错。改为按测试文件定位。

**验证**：新增 `tests/test_homepage_feed.py` 22 项，全量 248 项通过；用论坛实际发布的 11-02 正文（与论坛原文 SHA-256 一致）端到端生成首页数据，6 / 2 / 19 条与正文一致。

## 5.1.0 - 2026-11-02

首个周一（合并周末）实跑后的迭代：把当天暴露的问题全部落成门禁、代码或运行手册。

**新增门禁**
- R18 残留占位符：`self_check` 扫描“待人工替换 / FORUM_URL_PENDING / 待回填 / TODO / TBD”。此前“社区今日”占位行能无阻拦通过自查。
- R19 来源链接可达：新增 `config/link-reachability.json`（freepressjournal.in、kotaku.com、thenextweb.com、webdisclosure.com 已按公司网络实测列入 blocked），正文出现即 FAIL，尾部固定模块不检查。
- R20 事件键：`run.py dedup` 遇逐日编号的 `event_key`（signal-1 等）直接退出 2，`preflight` 报 `CANDIDATE_EVENT_KEY_GENERIC`，避免 11-02 那次 21 条候选被已见索引误判重复。

**飞书取材修复**
- 行业速递是列表项，原 `extract_section_cards` 只认 `###` 标题，速递从未进入头条候选池。新增 `extract_digest_bullets`，候选池上限由 3 扩到 6（`HEADLINE_POOL_SIZE`）。差异化门禁规则不变，仍是“命中画像且互不重复，否则 fail closed”。
- 三群画像关键词补充：智能体群 +扩展 / 插件 / 后门 / 安全 / 供应链；KNS 群 +电力 / 数据共享 / 数据基础设施 / 大模型；本地模型群 +端侧 / 千问 / Qwen / 苹果 / 手机。11-02 实测三群可各得不重复头条。

**文档与运行手册**
- SKILL.md 新增 §5.4 沙箱 / 离线运行手册、§5.5「社区今日」取材（浏览器读 /hot）、§5.6 来源链接可达、§5.7 北京时间日期口径。
- evidence-standard 新增“语义复核字段速查”（claim_type / materiality 枚举、条目级 single_official、回执来源须为正文来源等）。
- rules-matrix 新增 R18-R20；report-template 明确“社区今日”不得留占位。
- `sources.json` 标注 DeepSeek 新闻页 404（待在生产机确认现行地址）。

**测试**：新增 6 项（占位符、不可达域名、尾部不检查、通用事件键、preflight 拦截、飞书候选池），全量 226 项通过。

## 5.0.2 - 2026-11-02

- 尾部署名改为两行：`**出品｜集团数字化中心**` / `**策划整理｜AI应用研究院**`（去掉“·AI运营团队”，两行用行尾两个空格硬换行）。同步修改 `self_check`（SIGN_LINE_1、PRODUCER_LINE）、`prepare_feishu_push`（飞书签名档）、`feishu-group-profiles.json`、报告模板与测试。历史期次正文仍含旧署名，`self_check` 对旧期次不回溯。

## 5.0.1 - 2026-11-02

### 首个周一（合并周末）真实运行暴露的缺口

- **工具与做法为 0 条无法通过自查**：V5 模板允许 0-4 条，但适配后的 V4 自查仍要求至少一条 `###`，周末几乎必然踩到。新增固定空行 `今日暂无新的可直接借鉴的做法。`（`layout_v5.NO_TOOLS_LINE`，与金风相关的空行写法对称）：V5 结构门禁要求无工具卡片时必须写该行，`to_v4()` 输出该行，`self_check` 接受该行并免除“来源链接”要求。
- 新增 2 项测试（缺少空行被拦、写空行全流程通过），全量 220 项通过。
- 检索清单规范补充：信号候选的 `event_key` 必须由内容生成（如链接 slug），不得用 `signal-1`、`track-1` 这类跨日会重复的编号，否则已见索引的事件键查重会把次日候选全部判为重复（11-02 实测 21 条误判）。
- 提醒（未改代码）：`config/sources.json` 中 DeepSeek 新闻页地址 `api-docs.deepseek.com/news/news` 已返回 Page Not Found，需在生产机上换成现行地址。

## 5.0.0 - 2026-10-30

### V5 三层版式 + 社区信号源（用户确认版式后落地）

**版式（经用户确认，取代 V4.2 六板块平铺）**
- 正文改为 `[wrap=air-daily]` 容器 + 三层：第一层 · 精编日报（今日要紧 / 金风相关 / 工具与做法 / 行业速递，证据口径与 V4.2 相同）、第二层 · 本周跟踪（[D-7, 窗口起点) 的已报大事后续）、第三层 · 信号雷达（单一来源可入，如实标可信度，八类）。首屏新增四个数字卡与可信度条。
- 行内标签用 `<span data-air-chip="sec|cred|from">`（10-30 样稿实测：一行两个 `[wrap]` 会被 Discourse 错配）。
- `assets/discourse-theme/ai-radar-daily-style/`：主题组件 v2（卡片、数字卡、可信度条 JS、雷达类别筛选与「隐藏单一来源」开关），管理员安装一次。
- `references/report-template.md` 重写为 V5；V4 规则保留为只读历史段。

**门禁**
- 新增 `scripts/layout_v5.py`：`check_structure()`（wrap 卫生、容器与尾部、层序、数字卡 / 可信度条与实际卡数一致、逐卡来源 / 字数 / 日期 / 类别白名单 / 来源名）、`check_against_search_log()`（跟踪 / 雷达须绑定 TRACKING / SIGNAL 候选）、`to_v4()` 适配层。
- `self_check` / `preflight_check`（报 `V5_LAYOUT`）/ `build_knowledge_package` / `prepare_feishu_push` / `fetch_recent` 均按是否含 `[wrap=air-daily]` 自动分流；精编层转 V4 后复用原门禁，历史期次不受影响。
- 检索清单 decision 新增 `TRACKING`、`SIGNAL`；`settle` 把当期 TRACKING / SIGNAL 一并结算为已发布（不占 30 条精编配额）。
- 飞书推送：今日要紧可为 2 条，不足 3 条时由金风 / 工具 / 速递补足要点；总数不足 3 才拒绝。
- `publish.py`：H1 剥离改为多行模式（V5 的 H1 在容器行之后）。
- 规则矩阵新增 R16（V5 版式）、R17（社区信号源）。`RULESET_VERSION` 保持 V4.1：精编层证据规则未变，V5 版式规则作为附加门禁在同一报告中输出。

**社区信号源**
- `fetch_sources.py` 新增 `hn_top`（Algolia front_page，热度 = points）、`github_trending`（热度 = 今日新增 star）、`hf_trending`（热度 = trendingScore）三种类型；源配置支持 `layer` / `cat` / `keywords: "default"` / `min_heat`；输出 `signal_items` 汇总。
- `config/sources.json` 新增 hn-front（min_heat 80）、arxiv-ai（cs.AI OR cs.CL）、github-trending、hf-trending，均标 `verified:false`，须在生产网络首跑核实。
- AI 关键词 ≤3 字母整词匹配（`ai` 不再命中 `air`）；GitHub 描述 HTML 实体解码。
- `retrieval-strategy.md` 新增 §3D 社区信号层、§3E 三层分流标准。

**测试**
- `tests/test_v5_layout.py`（版式通过、9 项门禁、适配层、飞书补足、去重抽取、V4 不受影响）。
- `tests/test_v5_sources.py`（用 10-29 HN / GitHub Trending 真实页面精简样本测解析、关键词过滤、HF、arXiv 时区、抓取失败记 ERROR）。
- 全量 218 项通过。

## 4.3.0 - 2026-10-30

### 去重留存重建 + 检索自动化（诊断报告 P0/P1 落地）

**P0 · 缺陷修复**
- **跨期去重失效（高危）**：`fetch_recent.extract_news_items()` 仍按 V3 的 `🔹 **标题**` / `来源：` 行解析，V4 正文（`### 标题` + `[details]`）抽出 0 标题 0 URL，10-22 以来 self_check「跨期去重」实为空集比对。现兼容 V4/V3，排除尾部固定模块、社区今日、今日一问。
- **累积池候选撞号**：检索清单 id（NEWS-01）每天从头编号，`_cand_key()` 按 id 匹配会在 settle 时把其他批次同号候选一并标为已发布。新增全局唯一 `uid = report_date::id`，结算按 uid 落账（旧池数据读取时自动补 uid）。
- **「按重要度取前 30」未落地**：池内未存重要度，settle 实为 `pending[:30]` 按入池先后截断，且含 REJECTED 候选；文档写的 `--selected-ids` 未实现。现池条目保存 `importance`，`ranked_pending()` 仅取 SELECTED、按 importance 降序 → 事件日期降序；`settle --selected-ids` 已实现。
- `prune_old` 注释与行为不一致（注释称仅删未发布），注释改为与行为一致。
- 清理 9 个 `.bak-*`、`__pycache__`、`.pytest_cache`、`.verify-cand.manifest.json`、`__MACOSX`。

**P1 · 新增**
- `scripts/seen_index.py`：已见索引（`<work_dir>/seen-index.sqlite`），三级查重 L1 规范化 URL（永久）/ L2 标题 bigram Jaccard≥0.6（30 天）/ L3 事件键（30 天、日期差≤3）；`is_update` 跟踪报道降级为 UPDATE；被淘汰候选同样入库，防止换个说法再次进池。
- `run.py dedup`：入池前查重，命中的 SELECTED 改判 `REJECTED_DUPLICATE`，输出 `_去重报告.json`；`--recent-data` 可并入论坛近期期次。
- `run.py seen-seed`：已见索引冷启动。
- `run.py accumulate`：全部候选写入已见索引；未先跑 dedup 时 WARN。
- `run.py settle`：结算后同步索引 status=published。
- `run.py recover-missed`：缺跑扫描脚本化（exit 5 列出缺失检索日），取代原手工扫描。
- `config/sources.json` + `scripts/fetch_sources.py`：C 层官方源单一清单与确定性抓取（rss/atom/github_release），page 类列入 `manual_pages`；抓取失败如实记 ERROR，不伪造已检查。feed 地址标 `verified:false`，须在生产网络首次运行后核实。
- `tests/test_v43_dedup.py`：18 项回归（V4 解析、uid 撞号、重要度排序、三级查重、回看窗口、dedup/recover-missed 子命令、feed 解析与时区）。

**口径对齐**
- `self_check`：语义复核 `deep_focus` 改为可选（V4 无深度聚焦板块）。
- `retrieval-strategy.md`：C 层改指向 `sources.json`；§5「今日大事件 5-8 条」改为 V4「今日要紧 2-8（建议 3-6）」；§7 跨期去重改为已见索引流程。
- `knowledge-base-schema.md`：新期次 `lens` 一律 `N/A`。
- `SKILL.md`：description 改为触发说明（版本史留 CHANGELOG）；新增 §5.3 每日检索流水线；二维码「5 码」残留改 6 码；无人值守审查材料去掉「六条大事件/深度聚焦」；子命令表补 dedup / seen-seed / recover-missed。
- 删除 `references/discourse-api.md`（历史说明）。
- 版本三处同步：SKILL.md / runtime.json / CHANGELOG = 4.3.0；`RULESET_VERSION` 保持 V4.1（版式与门禁规则未变）。

**验证运行（AI-RADAR-2026-10-30）中追加修复**
- `SKILL.md` 阶段 5 示例给 `self_check.py` 传 `--qr4/5/6`，脚本不接受，照文档执行即 `unrecognized arguments`；示例已更正并补 `--recent-data/--report`。
- `run.py self-check` 恒传 `--cover`，封面缺失时文档承诺的「无封面引用语降级」永远走不通；改为封面存在才传，否则 WARN 并走降级分支。
- `seen_index` L2 标题查重增加包含度（短标题 bigram 落入长标题 ≥80%，短标题 ≥8 个 bigram 才启用）：同一事件「一长一短」两种标题此前被 Jaccard 漏判。
- `config/sources.json`：按实测更新 Microsoft / 月之暗面 / MiniMax / Qwen 地址，标注百度、豆包、混元的直读限制。

**未在本版处理（P2）**：SKILL.md 全量瘦身至 ~150 行、`holiday-batch` 并入开关、知识库 Judgments/Runs 表合并、尾部固定模块单文件化、封面轮换库、Data Explorer 效果回路。

## 4.2.0 - 2026-10-26

### 版式微调：取消首屏图例行 + 行动标签降为可选编辑标记

- **用户 2026-10-26 指定**：① 删除首屏图例行「覆盖 M月D日 至 M月D日公开进展｜行动标签：【需行动】【可借鉴】【了解】｜可信度：🟢…🟡…🔴…」；② `###` 标题不再挂【需行动】/【可借鉴】/【了解】行动标签。
- **`self_check.py`**：「今日要紧均带行动标签」硬校验改为「未使用自创标签即可通过」；删除「首屏图例行强制校验」两条结构断言（历史含该行的期次仍通过）。
- **`prepare_feishu_push.py`**：`ACTION_TAG_PATTERN` 加宽为「剥离任意非群视角的前导 `【…】` 标签」，防止残留标签泄漏进飞书群推送文案；群视角标签【能力复用】/【数据价值】/【部署门槛】保留由跨群泄漏检查兜底。
- **`references/report-template.md`**：文件结构示例去掉图例行与 `### 【…】` 前缀；规则 4（行动标签）改为「可选编辑标记」；规则 24（首屏三行→两行，图例行取消）；V3 对照表更新为「分层改由飞书三群视角标签承担」。
- **`SKILL.md`**：description 与「V4 条目标签与门禁」同步；`version: 4.1.2 → 4.2.0`。
- **`config/runtime.json`**：`version: 4.1.2 → 4.2.0`。
- **规则版本 `RULESET_VERSION` 保持 `V4.1`**：本次仅版式放宽（标签由必填降为可选、取消图例行），不涉门禁逻辑与知识包 schema。
- 影响：`normalize_title` 对行动标签本就是可选前缀，去标签不影响语义/检索绑定与 `package` 校验；`preflight_check.DAILY_ITEM_RE` 早已兼容无标签标题。

## 4.1.2 - 2026-10-23
- **署名口径更正（用户 2026-10-23 指定）**：尾部署名统一为 `**出品｜集团数字化中心·AI运营团队**` / `**策划整理｜AI应用研究院**`。上一版写作「集团数字」系 10-13 期转录偏差，按用户原文更正。
- 同步 **6 处单一真源**：`self_check.SIGN_LINE_1` / `PRODUCER_LINE` / 失败提示文案、`references/report-template.md` 尾部署名行、`tests/test_validation.py` 模板基线、`references/feishu-group-profiles.json` 的 `signature.block`、`prepare_feishu_push.SIGNATURE_BLOCK`（顺带清掉长期遗留的旧署名 `**集团数字化 · AI运营团队 出品**` / `**AI应用研究院 整理报道**`，该 backlog 项就此关闭）。
- 规则版本 `RULESET_VERSION` 不变（仍 `V4.1`）：本次仅文案口径同步，不涉版式与门禁逻辑。


## 4.1.1 - 2026-10-23

### 修复：飞书群推送快讯标题残留内部行动标签

**问题**（AI-RADAR-2026-10-23 出刊时发现）：三群推送的「快讯」条目中，来自**金风相关**板块的标题带着正文内部编辑标记 `【了解】` 一起发出去（如 `• 【了解】金风科技大丰实验中心…`），而来自今日要紧/工具与做法/行业速递的同级条目均无此标记，同一段文案里口径不一致。

**根因**：`extract_push_content()` 对今日要紧走 `NEWS_TITLE_PATTERN`（第 3 组 `###\s+【[^】]+】(.+)` 天然剥离标签），但金风相关、工具与做法、行业速递走 `SECTION_TITLE_PATTERN` + `clean_title()`，而 `clean_title()` 只剥离标题尾部 `（M月D日）`，不剥离前导 `【行动标签】`。V3 版式无行动标签，V4.1 引入后才暴露。

**修复**：新增 `ACTION_TAG_PATTERN = ^【(?:需行动|可借鉴|了解)】\s*`，在 `clean_title()` 首步剥离。只剥离**行动标签**、保留群视角标签（【能力复用】/【数据价值】/【部署门槛】），以免削弱 `send` 前「跨群标签泄漏」检查的信号。

**影响面**：仅出站推送文案；正文、检索清单、语义复核、知识库入库不受影响。

## 4.1.0 - 2026-10-22

### 版式重构：V3「速览 + 大事件 + 四专题」→ V4「六板块 + 行动标签 + 来源折叠」

**背景**：用户于 2026-10-22 提出新版式样例并确认「先用新版式出这一期，再迭代脚本」。当期（AI-RADAR-2026-10-22）已按 V4 出刊、发布论坛 topic 606 并推送三群，脚本与模板随后对齐。旧版式的核心问题：板块按内容类型切分（快讯/大事件/专题），读者要自己判断哪条该动手；来源行与正文挤在一起，长标题撑爆版面。

**变更**：
- `references/report-template.md`：**整篇重写为 V4.1**。六板块固定顺序「今日要紧 / 金风相关 / 工具与做法 / 行业速递 / 社区今日 / 今日一问」；新增行动标签三类（【需行动】【可借鉴】【了解】）、可信度三色、`[details]` 来源折叠、判断行统一为「**对我们：**」（≤90 字）、「今日一问」互动位；附「已废弃的 V3 结构」对照表与「模板 ↔ 脚本常量」对照表。
- `scripts/self_check.py`：
  - `CORE_HEADINGS` 改为六板块；大事件改 `parse_section_items`，条数 `2-8`；新增行动标签正则校验。
  - `MAX_DIGEST_BODY_CHARS = 600`（行业速递）；金风相关/工具与做法仍 300；`feature_body_char_count` 剔除 `[details]` 块。
  - `extract_urls` 增 `in_details` 状态机——来源收进折叠块后仍能抽到条目 URL（否则条目来源数恒为 0）。
  - `RULESET_VERSION` → `V4.1`；二维码缺失文案由硬编码「5 码」改为按 `QR_EXPECTED_COUNT` 生成并补第 6 群名。
  - `normalize_title` 剥离行动标签前缀，使语义复核与跨期去重不受 `【需行动】` 影响；同时修正 V3 遗留缺陷（第二行误用 `title` 而非 `value`，跟踪报道日期从未被剥离）。
- `scripts/build_knowledge_package.py`：`SECTIONS` 改六板块，`section_type` 映射改 `.get(section, section)`。
- `scripts/prepare_feishu_push.py`：`NEWS_TITLE_PATTERN` 增 `###\s+【[^】]+】(.+)` 分支；无深度聚焦时 `deep_topic` 回退取「今日要紧」首条并剥离标签。
- `references/judgment-method.md`：四个透镜降为内部写作框架，正文不再输出；改用行动标签。
- `references/knowledge-base-schema.md`：`section_type` 枚举改 V4 四值，历史期次保留旧值。
- `references/rules-matrix.md`：R15 改为「金风相关/工具与做法 300、行业速递 600、判断行 ≤90」。
- `tests/test_validation.py`：`MINIMAL_MD` 夹具重写为 V4 形态（六板块 + 折叠来源 + 行动标签）；语义复核夹具补第二条 item；300 字用例改到「工具与做法」；深焦违规用例改为跨两条 item 各违规一处。
- `scripts/preflight_check.py`（**收尾阶段发现，高危**）：90 字判断行与板块字数校验此前硬编码 V3 板块名（`今日大事件`/`AI×能源风电`/…）。V4 下 `section_text()` 取不到这些板块即返回空 → 校验被**静默跳过**，等于门禁失效。现改为 `check_daily_interpretation()` + `check_feature_lengths()`：判断行同时认「一句话解读：」与「**对我们：**」，板块改为 金风相关 300 / 工具与做法 300 / 行业速递 600，并保留 V3 板块兼容以支持历史期次。
- `scripts/build_knowledge_package.py`：`normalize_title` 补剥离 `【需行动/可借鉴/了解】` 前缀——正文条目标题带标签后，此前与语义复核、检索清单标题**全部失配**（preflight 报 `SEMANTIC_BINDING_MISSING`）；`RULE_VERSION` → `V4.1`。
- `scripts/publish.py`：`RULESET_VERSION` → `V4.1`（与 self_check / build_knowledge_package 三处对齐，否则 package dry-run 直接阻断发布）。
- `tests/test_preflight.py` / `tests/test_knowledge_package.py` / `tests/test_feishu_push_dedup.py`：夹具全部升级为 V4 版面（今日要紧挂 `### 【标签】`、来源收进 `[details]`、去透镜标签）；`test_b_feishu_push_dedup` 新增 `build_md()` 统一构造器。

**兼容**：历史期次（≤ 2026-10-21）不回溯改写；V3 版式备份在 `self_check.py.bak-20261022-v3`、`preflight_check.py.bak-20261022-v3`、`tests/*.bak-20261022-v3`，回溯核对时以这些文件为准。`self_check` 保留 `_deep_focus` 条件分支，V4 正文无深焦板块时跳过深焦硬门禁。

**运维联动（不改代码但必须做）**：飞书知识库 Base `Hhx5bvXBda6RH1sCVxacRfirn6c` 的 `events.section_type` select 选项原为 V3 五项，写入「今日要紧」会被 API 拒绝并报 `800030005 not_found`（报错不含字段名，只能逐字段二分定位）。已用 `base +field-update` 追加 今日要紧/金风相关/工具与做法/行业速递 四个选项（保留旧值以兼容历史数据）。**下次改板块名时，务必同步枚举类字段选项。**

**验证**：`pytest tests/` 全量 **175 项通过**；2026-10-22 真实期次 `self-check` PASS（events 8 / sources 22 / judgments 8），`preflight` + `package dry-run` PASS，`sync-knowledge` SYNCED（主表 5 张 + 候选表新增 43 行）。

## 3.9.9 - 2026-10-21

### 二维码 5 码扩为 6 码：新增「风衍大模型用户交流群」

**背景**：用户于 2026-10-21 提供金风科技「风衍大模型用户交流群」入群二维码截图（`金风科技-风衍大模型.jpeg`，1372x1392，与既有 5 码同规格），确认**固定加入**日报尾部二维码网格。

**变更**：
- 新增源文件 `群二维码_风衍大模型.jpeg`（work_dir，1372x1392 JPEG）；二维码有效期至 **2027-10-21**，到期前须换新图（模板注释块已登记）。
- `scripts/publish.py`：`QR_GROUP_NAMES` 增第 6 项「风衍大模型用户交流群」；`build_and_publish()` 增 `qr6_path` 形参并纳入 `qr_paths_in_order`；argparse 增 `--qr6` 选项。
- `scripts/self_check.py`：`QR_EXPECTED_COUNT` 5 → 6。
- `scripts/run.py`：`PRECHECK_QR_FILES` 增第 6 项；`cmd_publish` 调用串增 `--qr6`；相关注释 5 码 → 6 码。
- `config/runtime.json`：`precheck.qr_files` 增 `群二维码_风衍大模型.jpeg`。
- `references/report-template.md`：`[grid]` 增第 6 行；短链登记表增序 6（`upload://PENDING_QR6_UPLOAD.jpeg` 占位——首发由 publish.py 上传自动换取真实短链，取得后回填）。
- `tests/test_validation.py`：模板 6 码断言、alt 真实群名断言增「风衍大模型用户交流群」；CLI 签名测试改为 3 位置 + `--qr4/--qr5/--qr6`，旧式全位置参数拒绝用例同步扩为 6 码。
- `SKILL.md` / `config/runtime.json`：version 3.9.8 → **统一 3.9.9**；预检表、自查示例、发布示例、参数签名说明同步。

**顺序约定**：新码固定为**序 6（网格末位）**，不改动既有 1–5 码顺序与已验证短链映射。

**影响**：历史期次正文仍是 5 码、不再重发，不受影响；本期（2026-10-21）起按 6 码出刊。模板新增第 6 行为本地引用（`群二维码_风衍大模型.jpeg`），发布时随其余 5 码一并上传替换。

## 3.9.8 - 2026-10-14

### 修复飞书推送解析器行尾容错漏网（3.9.7 教训 #1 的补漏）+ `official_sources_checked.checked` 语义定稿

**背景**：2026-10-14 期出刊时 `run.py push-draft` 报 `今日大事件不足3条，无法生成动态摘要`（`DRAFT_FAILED`），整条飞书推送链中断。根因与 3.9.7 已修过的完全同类：`prepare_feishu_push.py:18` 的 `NEWS_TITLE_PATTERN` 仍是严格 `🔹\s+\*\*(.+)\*\*)$`，而模板规则 19 要求大事件「🔹 标题」**行尾保留两个空格**做 Markdown 硬换行 → 解析出 0 条。3.9.7 只放宽了 `self_check.py` 的同名正则，未同步该文件。

**变更**：
- `scripts/prepare_feishu_push.py`：`NEWS_TITLE_PATTERN` 行尾加 `\s*$`，与 `self_check.py` 统一容错口径（旧格式完全兼容），并加注释指向教训来源。全脚本再扫一遍行尾严格正则：仅此一处残留，其余 `(.+)$` 型均由 `clean_title()` / `.strip()` 兜底。
- `references/retrieval-strategy.md`：在检索清单 schema 下新增**字段语义注**——`official_sources_checked[].checked` = 「本次已执行核查」，**不是**「已采信为正文来源」；`self_check.py` 的 `malformed_official` 要求**每一条都为 `true`**，任何 `checked=false` 都会判「存在无效记录」而 G2 FAIL；负面结论（不可直读／未采信／窗口内无发布）写进该条 `latest_update`，并在清单顶层用 `official_sources_checked_note` 声明。
- `references/review-recitation.md`：复盘问 1 的核验表述由「`checked=false` 的逐一列出」改为「每条 `checked` 均须为 `true`」，与门禁对齐。
- `SKILL.md` / `config/runtime.json`：version 3.9.7 → **统一 3.9.8**。

**验证**：`tests/` 全套 `Ran 175 tests ... OK`（含 `test_feishu_push_dedup` 26 项与 `test_run`）。本期实际运行印证：修复后 `push-draft` 由 `DRAFT_FAILED` 转为 `PUSH_DRAFT_READY`，三群草稿正常生成并最终 0 重试发送成功。

**影响**：**纯前向修复，不改正文与模板基线**，历史期次不受影响。⚠️ 触发「改正文排版规则」类变更时，必须全脚本 grep 同类行尾正则——只修报错点会漏掉兄弟文件（本条已写入项目长记忆）。

## 3.9.7 - 2026-10-13

### 尾部固定模块文案改版：去表情符号、链接式能力地图、新双层署名

**背景**：用户在 2026-10-13 第 13 期出刊前**直接修改了正文尾部与换行写法**，并要求把改动固化为技能模板基线。改动会与既有逐字匹配常量冲突（`TAIL_HEADINGS` / `SIGN_LINE_1` / `SIGN_LINE_2` / `PRODUCER_LINE`），必须先同步门禁与模板，否则 `self_check` 必然 FAIL。

**变更**：
- `scripts/self_check.py`：
  - `TAIL_HEADINGS`：`# 🔗 集团AI能力地图` → `# 集团AI能力地图`；`### 📲 技术交流群（扫码加入）` → `### 技术交流群（扫码加入）`（去表情符号）。
  - `SIGN_LINE_1` / `PRODUCER_LINE` → `**出品｜集团数字·AI运营团队**`；`SIGN_LINE_2` → `**策划整理｜AI应用研究院**`（旧写法 `**集团数字化 · AI运营团队 出品**` / `**AI应用研究院 整理报道**` 停用）。签名档校验失败提示文案同步更新。
  - `NEWS_TITLE_PATTERN`：`🔹\s+\*\*(.+)\*\*)$` → `🔹\s+\*\*(.+)\*\*)\s*$`，**允许标题行尾空白**。原正则要求 `**` 后紧跟行尾，与规则 19 的「行尾双空格硬换行」直接冲突，实测导致「今日大事件共 0 条」而 G3 FAIL；放宽后旧格式（无行尾空格）仍完全兼容。
- `references/report-template.md`（唯一模板权威）：
  - 文件结构（能力地图块、技术交流群标题与 grid、双层署名行）与格式规则 1 / 11 / 14 同步；
  - 能力地图条目写法由 `- 名称：URL` 改为 `- [名称|说明](URL)`；
  - 群二维码 `[grid]` 内**图与图之间留一个空行**；
  - 新增格式规则 19：大事件「🔹 标题」行与「一句话解读：」行、尾部双层署名行**行尾各保留两个空格**（Discourse 单换行会被并入同段，须硬换行）。
- `tests/test_validation.py`：`test_tail_headings_still_required` 断言同步为新标题。
- `SKILL.md` / `config/runtime.json`：version 3.9.5（SKILL）/ 3.9.6（runtime）→ **统一 3.9.7**（此前两处已漂移一版，本次一并收敛）。

**验证**：`tests/` 全套执行——`test_validation` **56 OK**、`test_preflight` **11 OK**、`test_knowledge_package` **15 OK**、`test_feishu_push_dedup` **26 OK**、`test_holiday_pool` **8 OK**、`test_sync_candidates` **5 OK**；`test_run` 54 项中 **1 项既存环境失败**（`TestWorkDirResolution.test_fallback_to_skill_parent`：断言 work_dir 回退解析以 `ai-radar-daily` 结尾，属目录布局相关的既有问题，与本次文案改版无关）。

**影响**：**历史已发布期次不回溯改写**（旧文案属既定事实）；新文案基线自 2026-10-13 期起生效。⚠️ 署名行按用户原文采用「**集团数字**·AI运营团队」（非旧版「集团数字化」），如需更正须再次确认。

## 3.9.6 - 2026-10-08

### 节假日数据源治理：接入 calendar-cn，消除跨年静默退化风险

**背景**：`run.py` 的 `CHINA_HOLIDAYS` / `CHINA_MAKEUP_WORKDAYS` 以 `MM-DD` 按年分桶，且只录入了 2026 年。中国节假日每年日期都变，这种存法在跨年后会**静默退化为「仅按星期」判定**——2027 年元旦（周五）本为法定休假日却会被判为 `workday`，春节整周也会照常出刊。距该风险触发不足三个月。同时，新增年份必须人工改 `run.py` 硬编码表，维护成本高且易漏。

**变更**：
- `run.py` 新增 `_load_calendar_cn(year)`：优先读外部 `~/.workbuddy/skills/calendar-cn/data/<year>.json`（国务院原文口径，完整 YYYY-MM-DD + 调休补班），展开节假日区间为日期集合，结果进程内缓存；读取或解析异常一律**静默回退**，不影响主流程。
- `china_workday_status()` 改为三级优先级：**calendar-cn 外部数据 > 内置 MM-DD 表 > 仅星期**。四态语义（`off`/`makeup`/`weekend`/`workday`）与判定顺序保持不变。
- 备份：`scripts/run.py.bak-20261008-calendar-cn`（改动前原文件，81,628 字节）。
- `SKILL.md` §6 阶段 1：补充数据源治理说明与年度更新入口。
- `SKILL.md` / `config/runtime.json`：version 3.9.5 → 3.9.6。

**验证**：2026 年数据两源已交叉核对完全一致（元旦 01-01~03、春节 02-15~23、清明 04-04~06、劳动 05-01~05、端午 06-19~21、中秋 09-25~27、国庆 10-01~07；补班 01-04 / 02-14 / 02-28 / 05-09 / 09-20 / 10-10）。`tests/test_run.py` **54 项全部通过**（含 23 项工作日/节假日断言）。

**影响**：2026 年运行行为零变化；2027 年及以后新增年份无需改代码，只需在国务院发布次年安排（通常 11 月上旬）后跑一次 `update_holidays.py`。**待办**：2027 年数据尚未发布，需在 2026 年 11 月中旬执行更新，否则 2027-01-01 起仍会退化。

## 3.9.5 - 2026-09-29

### 规则固化：自动化模式下外部动作仍须人工确认

**背景**：2026-09-29 出刊时，automation system reminder 指示"Do NOT use AskUserQuestion; assume a default"，执行 Agent 将此理解为"默认通过所有确认门"，在同一轮次内连续执行 publish → push → sync-knowledge 三个外部可见动作，未暂停等待人工审查。用户事后指出缺少人工审核通道。

**变更**：
- `SKILL.md` §9「外部动作人工确认」：新增自动化无人值守模式条款——即使 automation 指示不用 AskUserQuestion，外部可见动作（论坛发布/飞书推送/知识库远端同步）仍须暂停；正确做法是在回复中输出审查材料 + 标注「⏸️ 等待确认后执行」+ 结束当前轮次，用户下一条消息视为授权。禁止同一轮次连续执行三个外部动作不暂停。
- `SKILL.md`：version 3.9.4 → 3.9.5。
- `config/runtime.json`：version 同步 3.9.5。

**影响**：后续自动化出刊在 G1-G3 PASS 后不再直接 publish，而是输出审查材料等待用户确认；飞书推送同理。

## 3.9.4 - 2026-09-29

### 规则固化：evidence_receipts 枚举约束与深度聚焦独立节点

**背景**：2026-09-29 出刊自查首次 6 项 FAIL：`materiality` 使用了非法值 `important`（×3）、`claim_type` 使用了非法值 `report`（×1）、深度聚焦缺少独立顶层 `deep_focus` 节点及其 evidence_receipts（×2）。根因是 `references/evidence-standard.md` 已有部分枚举说明（claim_type 无 policy）但未完整列出合法值集合，也未说明深度聚焦 semantic review 的独立节点结构。

**变更**：
- `references/evidence-standard.md` §2：补充 `claim_type` 无 `report`（数据类用 `number`、事件类用 `action`）；`materiality` 只有 `critical`/`supporting` 两值，禁止 `important`/`moderate`。
- `references/evidence-standard.md` §5：新增深度聚焦独立 `deep_focus` 节点结构规范（含 `item_id`、`title`、`source_urls`、`facts_checked`、独立 `evidence_receipts`（全 T0）、`official_sources_only: true`、`link_status_verified: true`、`reviewed_at`），并强调 source_urls 与正文严格一致、receipt 与 items 分开存放。
- `SKILL.md`：version 3.9.3 → 3.9.4。
- `config/runtime.json`：version 同步（若存在 version 字段）。

**影响**：下次出刊生成语义复核时，深度聚焦必须构造独立顶层节点；evidence_receipts 的 materiality 和 claim_type 只能使用合法枚举值，避免自查返工。

## 3.9.3 - 2026-09-28

### 规则固化：缺跑补检恢复，防止停机假期漏检

**背景**：用户指出一处运行漏洞——若假期/周末/工作日因电脑关机、网络中断、WorkBuddy 未启动或自动化未触发，检索通道没有实际运行，候选池就不会产生对应日期批次；下一个工作日恢复运行时，旧逻辑只会发布候选池里已有内容，无法自动发现并补齐停跑期间的新闻缺口。

**变更**：
- `SKILL.md`：新增 V3.9.3「缺跑补检恢复」硬规则。每次恢复运行必须在预检后、当日检索/发布前扫描 `candidates-pool.json` 的 `batches[].report_date`，枚举上次成功检索点之后至当前刊期前一日的缺失日期；缺失日期一律视为 `MISSED_RETRIEVAL`，必须逐日补检并 `accumulate` 入池后才能发布。
- `config/runtime.json`：新增 `accretion_publish.missed_retrieval_recovery` 配置，记录扫描来源、扫描区间、阻断状态和 `publication_metadata` 需写入的恢复字段。
- 自动化任务 `automation-1787192486737`：已更新 prompt，要求先做缺跑扫描；发现缺失日期未补检时停止并汇报 `MISSED_RETRIEVAL_UNRECOVERED`，禁止只发布当天单期。
- 阶段 9 复盘：补充核验 `recovery_reason="missed_runs"` 时 `missed_dates` 是否全部进入候选池，并在 `recap.window.recovery` 留痕。

**影响**：后续若电脑假期未开机或自动化停跑，恢复运行时不再默认只发当天；必须先补齐停跑窗口，再以累积出刊口径发布，并在飞书推送与复盘中显式标注恢复窗口。

## 3.9.2 - 2026-09-24

### 规则固化：今日大事件数量必须可解释

**背景**：2026-09-24 第 23 期发布后，用户追问「今日大事件为什么都是 5 篇、检索出来的是几篇」。复核发现本期检索清单共 16 条候选、9 条 SELECTED，正文采用「5 条今日大事件 + 4 个专题模块」的栏目分流；该处理在模板 5-8 条范围内合规，但缺少明确解释，容易显得像随意固定取 5 条。

**变更**：
- `references/report-template.md`：今日大事件数量规则补充为「5 条只是下限，不是默认值」；合格 SELECTED 候选充足时优先 6-8 条；若只取 5 条，必须在检索清单 `recall_review.notes` 或阶段 9 复盘说明依据。
- `references/retrieval-strategy.md`：新增「今日大事件数量可解释规则」，可解释原因包括候选不足、强来源不足、栏目分流、去重/越窗/弱来源淘汰等。
- `SKILL.md`：阶段 4 成稿规则同步，禁止无依据地固定取 5 条。

**影响**：后续日报不再机械取 5 条；数量选择需可审计、可复盘。若继续使用 5 条，必须给出原因；若合格候选足够，默认向 6-8 条扩展。

## 3.9.1 - 2026-09-24

### 发布后修复：占位符终检误报 + 群专属价值落点改为可选 + 预检字段解耦

**背景**：2026-09-24 第 23 期（topic 559）实战首次跑 V3.9.0 的「草稿占位 → push 回填」链路，暴露一处**每期必然触发的缺陷**；同期用户要求移除群推送文案中的固定价值落点句。

**修复一：占位符终检误报（阻断级）**

- 现象：`run.py push --confirm` 稳定报 `PLACEHOLDER_LINK_BLOCKED`，`leaked=["placeholder"]`，但 payload 的 `article_url`/`topic_id` 均为真实值。
- 根因：原实现 `json.dumps(payload)` 后整串扫 `PLACEHOLDER_TOKENS`，而 V3.9.0 新增的结构性字段名 `placeholder` / `placeholder_url` 自身就含 token `placeholder`，恒命中。
- 修复：抽出 `leaked_placeholder_tokens(payload, article_url, topic_id)`，**只扫描真正外发的内容**——各群 `message`/`main_summary`/`main_title`（跳过 `skip=true` 的群）与 `article_url`/`topic_id`；结构性字段名不参与判定，仍保持 fail closed。
- 回归：`tests/test_run.py::TestPlaceholderLeakScan` 3 例（真实 payload 不误报 / 消息体残留 token 仍阻断 / skipped 群不参与判定）。全量 **175 tests OK**。

**修复二：群专属价值落点（angle_suffix）改为可选（用户指令）**

- 按用户要求移除三群推送文案中固定的「对X更值得关注的是：…」句（属模板自带），并**从 `references/feishu-group-profiles.json` 三处 `angle_suffix` 一并删除**。
- `prepare_feishu_push.py::rewrite_summary_for_profile`：`angle_suffix` 改为可选——未配置时不追加、不报错，仅保留 `angle_label` 前缀差异化；同时修掉正文以「…」结尾时又补句号的「…。」双标点。
- `run.py` 预检（`cmd_precheck`）原先把 `angle_suffix` 列为**必填字段**，已从必填清单移除（否则下期搜索前预检直接 BLOCK）；`angle_label` 唯一性校验保留。
- 测试：`ProfileSummarySuffixTest` 改为「缺 suffix 优雅降级 + 缺 label 仍 fail closed」，并新增短正文无落点时以句号收尾用例。

**影响**：三群差异化仍由 `angle_label` 前缀（能力复用/数据价值/部署门槛）＋ 全局唯一头条＋差异化快讯/提问承担，推送文案更短、不再套用统一话术。

## 3.9.0 - 2026-09-23

### 流程前移与顺序重构：搜索前预检 + 本地/远端解耦 + 草稿占位链接 + 资讯清单退出正文

**背景**：对 2026-09-23 第 22 期做耗时归因——09:40:39→11:34:46 共约 114 分钟。其中可归因的内容生产窗口约 44 分钟、阶段 5 自查约 6 分 40 秒、人工确认等待约 38 分钟；**其余约 20 分钟属可避免返工**。返工根因高度集中：问题都拖到发布前才暴露（二维码映射错位、Base 单选枚举不存在、群画像字段缺失、正文来源与回执不一致等）。据此按用户批准的方案做四项结构改造。

**变更一：搜索前只读预检 `precheck`（新命令）**

- `scripts/run.py` 新增 `cmd_precheck`，把「搜索前就能发现的问题」全部前移。检查项与失败级别：
  - **BLOCK（exit 2）**：二维码 5 码齐备（缺一即 `publish.py` PublishError）、`feishu-groups.json` 可解析且启用群 ≥1、群画像字段完整且 `angle_label` 唯一（差异化门禁 fail closed）、`.env` 可读且 `DISCOURSE_API_KEY`/`DISCOURSE_API_USERNAME` 键位齐备；
  - **WARN（exit 1）**：知识库 Base/schema（允许先出刊后补同步）、论坛 `base_url` 只读探活（401/403 视为可达）。
- **三条铁律**：只读（不写 run-state、不发帖、不发消息、不写 Base）；**凭据只校验键名、绝不回显值**（`_redact_config_probe` 只统计键名存在性与 secret 键数量）；不阻断检索（BLOCK 提示「应先修复再搜索」）。
- 已从工作日拦截与会话互斥豁免（周末也可做环境体检）。

**变更二：本地构包与远端同步解耦（`package` / `sync-knowledge`）**

- 原 `cmd_package` 把「本地 build」与「sync_lark_base + sync_candidates_base」捆在一起，导致本地构包被迫等待、且远端不可用会牵连主流程。
- 现拆为：`package`（stage 7）**仅**构建并校验本地五表包（校验产物存在 + 合法 JSON + 记录 `package_sha256`/`counts`，状态置 `PACKAGED`）；新增 `sync-knowledge`（stage 8）执行远端同步。
- **顺序固定为「先推送、后同步」**：`sync-knowledge` 的**硬前置**是「论坛 `PUBLISHED` + 所有飞书目标群 `SENT`」，任一群非 SENT 即 `FEISHU_NOT_ALL_SENT` 阻断（状态置 `BLOCKED`）。理由：知识库是留档侧，不应早于推送，也避免同步失败反压推送。
- 推送成功后 `run_status=SYNC_PENDING`（**不再是终态**），`sync-knowledge` 成功才落 `RUN_COMPLETED`。远端失败保留本地 package、可幂等重跑。

**变更三：草稿占位链接 + 回填（`push-draft`）**

- 新增 `cmd_push_draft`：论坛确认**之前**即可用占位符 `<FORUM_URL_PENDING>` / `PENDING` 预生成三群飞书草稿，不触网、不发送，输出 `..._飞书推送_草稿.json`。
- `prepare_feishu_push.py` 新增 `--allow-placeholder`（否则占位链接直接报错拒绝）；payload 增记 `placeholder` / `placeholder_url`。
- `cmd_push` **发送入口 fail closed**：`article_url`/`topic_id` 任一为占位符、或 payload 内残留占位 token → `PLACEHOLDER_LINK_BLOCKED` 拒绝发送（新增纯函数 `is_placeholder_url` / `is_placeholder_topic_id`）。
- `cmd_push` 前置由「stage 8 PASS」改为「stage 7 PASS + 论坛 PUBLISHED」，使发送不再依赖远端同步。

**变更四：资讯清单退出正文 + 累积 metadata 迁移**

- **正文禁入**：`self_check.py` 原把 `# 🔗 全部资讯（候选全集）` 当「可选尾部模块」（存在才校验），现改为**出现即 FAIL**（常量 `OPTIONAL_TAIL_HEADINGS` → `FORBIDDEN_TAIL_HEADINGS`）；正文出现「累积出刊」行同样 FAIL。候选全集仍由 `sync_candidates_base.py` 同步知识库留档，只是不回流正文。
- **累积信息换载体**：`prepare_feishu_push.py` 新增 `parse_accumulation_from_metadata` / `load_publication_metadata` / `resolve_accumulation`，按「metadata 参数 → 检索清单 `publication_metadata` → 正文遗留行兜底」解析；原 `parse_accumulation`（正文正则）保留仅作历史兼容。
- 检索清单新增可选 `publication_metadata` 节点（`{"is_accumulated","span","count"}`）。

**配套文档同步**：`references/report-template.md`（规则 1/11）、`references/rules-matrix.md`（R09 重写 + 新增 R09b/R09c）、`references/run-contract.md`（normal/revise 状态转移改为 `... → PACKAGED → FEISHU_SENT → SYNCED → COMPLETED`，枚举补 `PACKAGED`/`READY_PLACEHOLDER`）、`references/feishu-group-profiles.json`（`accumulation.source` 改为检索清单）、`references/knowledge-base-schema.md`（**新增 §7b Candidates 候选全集表**——此前正文承载该链接故未入文档，现为候选全集唯一留档位置；关系图与 §10 同步补 `candidates` 表 ID 与「本地包不含 Candidates」说明）、`config/runtime.json`（版本 + 新增 `precheck`/`publish_sequence` 节点）、`SKILL.md`（阶段 1 增预检表、阶段 7/8 重写、命令表扩至 14 行）。

**收尾修正（同版内，2026-09-23 下午核验时发现并修复）**

- **历史期次豁免**：经核验，2026-09-23 第 22 期正文（当日 11:34 发布）因规则尚在定稿过程中，**实际含 `# 🔗 全部资讯（候选全集）` 模块**。若无豁免，该期日后再跑 `self_check`（如离线复核、`--recent-data` 回溯）会被新门禁误判 FAIL。故新增常量 `self_check.LEGACY_ALL_INFO_STRICT_FROM = "2026-09-24"`：`report_date` 早于该日的期次跳过「资讯清单」与「累积出刊」两项禁入校验并记 PASS（附说明），该日及以后严格校验。**已发布正文一律不回溯改写**。
- **实测验证**：以 09-23 真实产物跑 `self_check.py ... --require-evidence-receipts` → `PASS`（exit 0），确认豁免生效；临界点 09-23 豁免 / 09-24 严格分别单测覆盖。
- **编排器描述去硬编码**：`run.py` argparse 描述原写死 `（V3.2.3，含会话互斥）`，改为不写死版本号（版本以 `config/runtime.json` 为唯一来源），避免随技能迭代漂移。L265 处 `V3.2.3` 为「会话互斥引入版本」的历史标注，保留。
- 新增测试 `AllInfoTailHeadingTests` 三例（常量声明、历史豁免、新刊严格）。

**验证**：全量 pytest **170 passed**；`tests/test_run.py` 中旧用例 `test_package_requires_explicit_synced_receipt` 已按新语义改写为 `test_package_builds_local_only` + 新增 `sync-knowledge` 门禁用例；`tests/test_validation.py` 的 `AllInfoTailHeadingTests` 改为「禁止语义」断言并补历史豁免用例。

**纪律**：2026-09-23 第 22 期已发布日报及其审计产物**全程只读**，本版改造不改动其任何内容；正文历史遗留的资讯清单模块作为已发布事实永久保留。

## 3.8.6 - 2026-09-22

### 修复 publish.py 二维码替换硬编码停留在旧版（发布瞬间抵消 V3.8.4 成果）

**背景**：结合自动化任务提示词做全链路核对时发现——`publish.py` 的二维码替换规则**硬编码为旧版三码结构**：`AI信息交流群`（V3.8.4 已明确废弃）+ `本地大模型用户交流群` + `AI智能体开发平台运营群`，尺寸写死 `400x408` / `400x428`。而模板早已是 5 码、统一 `400x400`。二者冲突。

**实测后果（三项，均已复现）**：
1. **尺寸被打回原形**：第 2、3 码 `400x400` 在发布瞬间被改回 `400x408` / `400x428`，V3.8.4 的门禁成果被脚本抵消；
2. **第 1 张图丢失**：第 1 条规则匹配 `AI信息交流群`，而模板第 1 码是「本地大模型用户交流群」（前缀 `AI` 致不匹配）→ **规则空转，本地上传的 `qr1` 根本没进正文**；
3. **文档示例不可执行**：`SKILL.md` 阶段 6 示例传了 **5 个位置参数**，但脚本只接收 3 个（`qr1_qr2_qr3`）+ 自检报告，照抄执行报 `unrecognized arguments` 直接卡死。

**根因**：V3.8.4 更新文档时**只改了 SKILL.md 的示例文本，未核对 `publish.py` 的真实签名与替换逻辑**，形成「文档说 5 码、脚本做 3 码」的口径分裂。提示词本身写的是「以运行时最新 SKILL.md 为唯一标准」，属自适应设计——**提示词越正确地遵循文档，越会踩中这个坑**。

**变更**：
- `scripts/publish.py`：
  - 新增常量 `QR_GROUP_NAMES`（5 群名，顺序与模板 `[grid]` 严格一致）/ `QR_DISPLAY_SIZE` / `QR_EXPECTED_COUNT`；
  - 新增纯函数 `apply_qr_replacements(raw, qr_urls)`——按群名精确映射替换、强制 `|400x400`、容忍源文旧尺寸写法，**缺任一码即抛 `PublishError` 阻断**（改为 fail-closed，不再静默放过）；
  - 删除原三条硬编码替换规则；`--qr4` 语义修正为「KNS统一数据平台」，新增 `--qr5`「标准化助手交流群」；参数不足即阻断。
- `SKILL.md` 阶段 6：调用示例修正为 **3 位置二维码 + 自检报告 + `--qr4`/`--qr5`**，并显式写明参数签名与固定群序，防止再次回退。
- `tests/test_validation.py`：新增 `QrPublishReplacementTests`（8 用例）——**真实调用 `apply_qr_replacements`**，覆盖 5 码替换、旧群名/旧尺寸不得泄漏、旧尺寸归一、缺码阻断、群名顺序与模板一致、CLI 收 5 码、旧错误用法被 argparse 拒绝。

**变异测试验证**：故意把 `QR_GROUP_NAMES[0]` 改回 `AI信息交流群`，**4 个用例同时失败**，确认测试具备真实杀伤力。

**验证**：140 tests OK（132 + 8）；模板真实数据替换后 5 码群名/尺寸全部正确、无旧值残留。

### 补 `holiday-batch` 模式文档（同批，2026-09-22 复核发现）

**背景**：重写自动化任务提示词时顺带核对，发现 `run.py` 的 `--mode` 白名单含 `holiday-batch`，但 **SKILL.md 正文完全没有说明**（仅 CHANGELOG 3.8.0 提过一句"mode 白名单新增"）。执行时无从得知其语义，只能靠猜。

**实测确认真实行为（2026-09-22）**：全代码库中 `holiday-batch` 仅出现在 argparse `choices` 一处，**无任何独立分支消费它**；`args.mode` 只在 3 处被读（写 run-state / 透传子脚本 / 判断 `== "normal"`）。唯一实际效果是**绕过工作日拦截**（`main()` 拦截条件为 `args.mode == "normal"`）。对比实测：同一法定休假日 `2026-02-17`（春节 `off`），`normal` 返回 `NOT_WORKDAY` 停刊、`holiday-batch` 放行至 `STAGE1_PLANNED`。

**变更**：
- `SKILL.md`：
  - §1 模式说明新增 `holiday-batch` 条目；
  - §6 阶段 1 在中国法定工作日硬校验之后补完整行为说明——**点明这是"例外通道而非常规路径"**、须在汇报中标注该期为非发布日强出、常规运行一律 `normal`；并**如实说明该值目前无独立分支逻辑**（防误解为含额外批次打包行为）；
  - §2 运行契约 `mode:` 枚举、§11 编排器 `--mode` 示例同步补齐 5 个取值。
- `tests/test_validation.py`：新增 `ModeWhitelistDocTests`（4 用例）——**从 run.py 源码提取 choices 反向校验文档**（不硬编码），断言每个 mode 都在 SKILL.md 有说明、契约枚举与脚本一致、`holiday-batch` 有实质行为描述而非仅列名。

**变异测试验证**：故意把契约行改回旧枚举，测试立即失败并指名缺失 `holiday-batch`。

**验证**：144 tests OK（140 + 4）。

## 3.8.5 - 2026-09-22

### 修复二维码门禁「空转」缺陷（V3.8.4 引入的尺寸门禁实际从未生效）

**背景**：为补上第 5 码「标准化助手交流群」的 `upload://` 短链，核查过程中发现一处**真缺陷**：`self_check.py` 用 `name.startswith("群二维码_")` 识别二维码，但正文真实写法是 `![群名|400x400](upload://xxxx.png)`——链接为 `upload://` 短链而非本地文件名。后果：识别结果**恒为空** → 存在性校验恒报失败 → **V3.8.4 新加的尺寸门禁被 `if grid_qr_refs:` 条件跳过，从未真正执行**。这正是 400x408/400x428 混用"长期漏网"的真实原因——不是漏网，是门禁压根没跑。

**为何旧测试全绿却无保护**：`QrDisplaySizeGateTests` 全部是**对 alt 字符串独立做正则**，不调用 `self_check` 的实际逻辑，因此无论门禁是否正确都恒绿。典型「测试与生产数据形态不一致」陷阱。

**变更**：
- `scripts/self_check.py`：**抽取为模块级纯函数** —— `is_qr_ref(alt, link)` / `parse_grid_refs(raw)` / `check_qr_gate(raw, md_dir, results)`，供 main 与测试共用同一份逻辑；识别判据改为三路（本地 `群二维码_*.png` / `upload://` 短链 / alt 带 `|WxH` 标注），保持向后兼容；新增**码数门禁**（固定 5 码，增减须用户确认）。
- `tests/test_validation.py`：新增 `QrGateEndToEndTests`（6 用例）——**真实调用 `check_qr_gate`**，覆盖 `upload://` 形态、尺寸混用端到端拦截、本地文件名兼容、码数异常、缺失尺寸标注、真实模板回归。
- `references/report-template.md`：注释块登记「短链 ↔ 群名 ↔ 来源环境」对照表 + 生产就绪清单。
- `SKILL.md`：新增「短链环境绑定」说明。

**短链环境核查结论（2026-09-22 实测）**：

| 序 | 群名 | 来源环境 | 生产站可达性 |
|---|---|---|---|
| 1–4 | 本地大模型/AI智能体运营群/语义本体构建/KNS统一数据平台 | 生产 | ✅ 200 |
| 5 | 标准化助手交流群 | **测试站** | ❌ 404 |

→ 序 5 短链仅在测试站有效；**生产发布前须在生产站重新上传换取生产短链**（用户 2026-09-22 决定：暂不传生产，改为模板显式标注）。

**未臆测声明**：生产站线上图与本地源文件 `群二维码_*.png` 体积量级不一致，**无法证明一对一对应**，故对照表**不登记"源文件名"列**，避免把猜测固化为事实。

**变异测试验证**：故意回退识别逻辑（移除 `upload://` 分支）后，端到端测试立即失败（`AssertionError: [] is not true`），确认新测试**真有杀伤力**而非假绿。

**验证**：132 tests OK（126 + 6）。

## 3.8.4 - 2026-09-22

### 二维码 alt 名规范化（语义本体构建交流群 / KNS统一数据平台）

**背景**：用户要求把 `金风科技-ontology.png` 加入模板、命名为「语义本体构建交流群」。**核查发现该文件与模板已有的 `群二维码_金风科技.png` SHA-256 完全一致（同一张图）**——经用户确认，实际意图是把该码的 **alt 显示名换成群的真实名称**，而非新增第 6 个码。

**变更**：
- `references/report-template.md`：
  - 第 3 张 alt「金风科技」→「**语义本体构建交流群**」；第 4 张 alt「金风科技-KNS」→「**KNS统一数据平台**」（一并规范，消除「金风科技」前缀歧义）；
  - 码数维持 **5 码**；尺寸维持统一 `|400x400`；
  - 格式规则 14 增补「alt 名一律用群的真实名称（与 `feishu-groups.json` 一致），禁用『金风科技-xx』式前缀」。
- `SKILL.md`：5 码清单同步更新为新群名。
- `tests/test_validation.py`：新增 `test_template_alt_names_use_real_group_names`（断言 5 个真实群名齐全 + 旧泛称不得残留），原 `test_template_has_five_qr_codes_all_400` 移除冗余断言。

**踩坑（重要）**：`AskUserQuestion` 的正确用法是**先核查事实再提问**——本次先算出 SHA-256 发现是同一图，才避免了一次错误的「新增第 6 个重复码」。

**验证**：126 tests OK（125 + 1）；模板回归断言通过。

### 新增「标准化助手交流群」二维码 + 二维码显示尺寸统一门禁

**背景**：用户要求在技术交流群模板中加入「标准化助手交流群」二维码（源文件 737x753 JPEG），并把显示尺寸统一为 400。核查发现两处存量问题：①正文声明的显示尺寸不一致（400x408 / 400x428 / 400x400 三样混用），致论坛 grid 内四张码高度不齐；②`self_check` 只校验二维码文件存在与被 grid 包裹，**不校验显示尺寸**——该不一致长期漏网。

**变更**：
- 新增资源 `群二维码_标准化助手.png`：由 `20260922-150808.jpeg` 转换，重采样至既有统一规格 **1372x1392 PNG**（与其余 4 码实物一致）。
- `references/report-template.md`：
  - 二维码块由 4 码扩为 **5 码**（本地大模型 / AI智能体运营群 / 金风科技 / 金风科技-KNS / 标准化助手），顺序固定；
  - **全部显示尺寸统一为 `|400x400`**（修正原 400x408 / 400x428）；
  - 格式规则 11 同步「4码→5码」；规则 14 增补尺寸统一与顺序约束。
- `scripts/self_check.py`：
  - 二维码解析改为同时捕获 alt 与路径（`re.findall(r"!\[([^\]]*)\]\(([^)]+)\)")`）；
  - **新增尺寸门禁**：grid 内每个二维码的 alt 尺寸标注必须为 `400x400`（兼容全角 `×`），否则 FAIL 并列出不合规项；
  - 报错文案「4 码」→「5 码」。
- `tests/test_validation.py`：新增 `QrDisplaySizeGateTests`（6 用例），覆盖 400x400 通过、旧混用尺寸拦截、缺标注拦截、全角乘号兼容、非正方形拦截，以及**模板文件本身必须 5 码且尺寸全为 400x400**（直接读模板回归）。

**设计说明**：
- 实物规格与显示规格分离管理——**实物统一 1372x1392 PNG**（源文件规格），**显示统一 400x400**（Discourse `|WxH` 参数）。二者不一致是正常的，前者保证源文件质量，后者保证论坛排版整齐。
- 「标准化助手」的 `upload://` 短链为占位符 `REPLACE_WITH_STANDARDIZED_ASSISTANT_QR.png`，首次发布前需上传 Discourse 换取真实短链。
- `sips` 的 `-Z` 按长边缩放会改变边长，精确重采样须用 `--resampleHeightWidth H W`。

**验证**：125 tests OK（119 + 6）；三处版本一致 = 3.8.4；尺寸校验逻辑对 6 组样例（含旧模板真实的不一致值）判定全部正确；模板文件回归测试通过。

## 3.8.3 - 2026-09-21

### 累积出刊的飞书推送标注（正文解析 → 推送同步）

**背景**：长假后首期若仍按日常口径推送（"今日最值关注"），会把多日累积误说成单日，口径失实。用户选择方案 A：推送文案按累积出刊显式标注累积期。

**变更**：
- `references/report-template.md`：把累积期说明规范为**固定可解析句式**——`> 📅 **累积出刊**：本期为 **M/D–M/D**（共 **N** 条）`，位于「全部资讯」段内、链接行之前；日常单期不出现。格式规则 11 同步更新（句式固定不得改写，飞书推送依赖它）。
- `scripts/prepare_feishu_push.py`：
  - 新增 `ACCUMULATION_LINE_PATTERN` 与 `parse_accumulation(raw)`：从日报正文解析累积期（日报正文是唯一权威来源），解析失败按日常单期处理、不阻断推送。
  - `build_message()` 新增 `accumulation` 参数：累积出刊时标题追加「·累积出刊」（`【AI雷达｜9.21·累积出刊】`），并在标题下插入一行 `📅 本期为 9/19–9/20 累积出刊（共 30 条），非单日快报`。
  - payload/输出新增 `accumulated` / `accumulation_span` / `accumulation_count` 字段，`prepare` 打印出刊形态。
- `config/runtime.json` / `references/feishu-group-profiles.json`：新增 `push_format.accumulation` 配置段（`title_prefix_accumulated`、`note_line`、`source`）。
- `scripts/self_check.py`：新增 `ACCUMULATION_LINE_PATTERN`；「全部资讯」段内若出现累积行，校验 ①位于段内且链接行之前 ②句式合规；正文提及"累积出刊"但句式不符固定格式 → FAIL。
- 新增测试：`tests/test_feishu_push_dedup.py` 的 `AccumulationPushTest`（4 用例）、`tests/test_validation.py` 的 `AccumulationLinePatternTests`（4 用例）。

**设计取舍**：累积期取自**正文解析**而非额外参数传递。理由：正文是唯一权威，推送与论坛口径天然一致；且无需改动 run.py 的调用链。句式在自查阶段被强制校验，防止"解析不到→静默按单期推送"这一失效路径。

**验证**：119 tests OK（111+8）。解析兼容 `**加粗**`/纯文本、`–`/`~`/`-` 三种连接符；日常单期不误标。

## 3.8.2 - 2026-09-21

### 「全资讯链接」写入日报模板尾部（可选模块 + 防死链校验）

**背景**：V3.8.1 已把候选全集同步到知识库，但日报正文尚未挂链接。本期把链接写入模板固定尾部，并保证历史日报兼容。

**变更**：
- `references/report-template.md`：新增固定尾部模块 `# 🔗 全部资讯（候选全集）`，位于「集团AI能力地图」之前；格式规则 1、11 更新（含累积期标注、同步失败整段删除的要求）。
- `scripts/self_check.py`：新增 `OPTIONAL_TAIL_HEADINGS` / `ALL_INFO_TAIL_HEADING` / `ALL_INFO_TABLE_URL`。
  - 「全部资讯」**不进强制 TAIL_HEADINGS**（否则所有历史日报会因缺该标题而 FAIL）；
  - 改为**条件校验**：仅当正文出现该标题时，校验 ①位于集团AI能力地图之前 ②链接等于候选表 URL，否则 FAIL（防死链）。
- `tests/test_validation.py`：新增 `AllInfoTailHeadingTests`（2 用例）锁死"可选模块"契约。

**验证**：111 tests OK。真实场景验证：①09-18 历史日报（无该模块）跑 self_check，结构校验**未因缺该标题 FAIL**；②注入正确模块 → 两项校验均 PASS；③故意写错链接 → `[FAIL] 「全部资讯」链接缺失或错误…不可留死链`。

## 3.8.1 - 2026-09-21

### 候选全集同步到飞书知识库（「全资讯链接」落地）

**背景**：V3.8.0 定义了"附全资讯链接"，但链接承载尚未落地。用户确认：新建 Candidates 表承载候选全集（含未入选的 REJECTED），由 AI 自动建表。

**变更**：
- 飞书知识库新增第 6 张表 **「候选全集 Candidates」**（`tbl4RS4qEuDf2B0E`，base `Hhx5bvXBda6RH1sCVxacRfirn6c`），12 字段：候选ID/期次/检索日/标题/事件日期/来源名称/来源链接/分类/采选结果(SELECTED|REJECTED)/是否进入精编/发布时间/写入时间。
- 新增 `scripts/sync_candidates_base.py`：把累积池全集写入 Candidates 表；以「候选ID」为主键幂等去重；分批 200；dry-run 支持；失败如实上报。
- `run.py cmd_package` 在 stage 8 后追加候选同步（stage 8.5），失败**不阻断**主流程，降级为链接暂缺；`run-state.candidates_sync` 记录状态。
- `config/runtime.json`：`knowledge_sync.table_ids` 新增 `candidates`；版本 3.8.0→3.8.1。
- 新增 `tests/test_sync_candidates.py`（5 用例），覆盖 build_rows 契约（全集含 REJECTED、select 为 list、checkbox 为 bool）。

**踩坑**：
- `lark-cli base +table-create` 的字段用字符串类型名（`text`/`select`/`checkbox`），select 选项是顶层 `options`（非嵌套 `property`），字段名键为 `name`。
- `lark-cli --output` 只允许写**当前工作目录**，且会同时产出 `.manifest.json`；复跑前须清理并加 `--overwrite`，否则 `failed_precondition`。

**验证**：109 tests OK（104+5）。真实写入：2 条测试数据落库并回读确认（SELECTED/REJECTED、精编标识正确）；幂等复跑 `existing=2,written=0,skipped=2`；测试数据已清理，表恢复空表。

## 3.8.0 - 2026-09-21

### 检索/发布解耦 + 假日累积出刊（每日恒定检索，工作日统一发布）

**背景**：原 V3.7.0 的 `china_workday_status` 只管"是否停刊"，但检索与发布仍耦合在 normal 流程里，非工作日既不发布也不沉淀候选。用户要求：每日恒定检索（不看日历），发布触发唯一依据=是否工作日；非工作日候选累积，到工作日统一发布（取前 30，附全资讯链接）。

**变更**：
- 新增 `scripts/holiday_pool.py`：候选累积池（`<work_dir>/candidates-pool.json`），提供 `load/save/append_candidates/pending_candidates/settle_pool/prune_old/pool_summary`；原子写盘，`MAX_BATCH_SIZE=30`。
- `run.py` 新增 `is_publishable_day()`（`workday`/`makeup` 视为可发布日）与 `accumulation_due()`（判断是否存在跨日累积待发布）。
- `run.py` 新增子命令 `accumulate`（检索候选入池，**豁免工作日拦截与会话互斥**，因属检索侧恒定执行）与 `settle`（工作日发布后结算，取前 30 上限，需 `--confirm`）。
- `run.py` mode 白名单新增 `holiday-batch`。
- `config/runtime.json` 新增 `accretion_publish` 配置块；版本从 3.6.2 修正为 3.8.0（修复此前 SKILL/CHANGELOG 3.7.0 而 runtime 停 3.6.2 的三处漂移）。
- 修复 `prune_old` 参照点缺陷：必须以**真实当天**为参照，严禁传 `report_date`（否则历史累积被误删）。新增 `_today_local()`。
- 新增 `tests/test_holiday_pool.py`（8 用例），含 `test_prune_does_not_drop_recent_accumulation` 回归。

**验证**：103 tests OK（原 95 + 新 8）。冒烟：09-19(周六)候选入池并判 `NOT_WORKDAY` 不发布；跨日累积→工作日 `ACCUMULATED_PUBLISH`；45 条→取前 30→剩 15；不足 30 全量；`settle` 无 `--confirm` 被拒。

### 发布后修复（2026-09-21，09-21 期出刊实测）

**背景**：09-21 期阶段8三群差异化头条分配首次触发 fail-closed：本期5条大事件（阶跃Step5/OpenAI融资/特朗普AI部队/Qwen-Image-2.1/智谱GLM）几乎全为大模型/融资性质，KNS『数据价值』群关键词命中全 miss（keyword_score=0），`assign_headlines` 无解抛 ValueError，差异化门禁一度阻断推送。

**变更**：`scripts/prepare_feishu_push.py` 的 `prepare()` 在 `assign_headlines` 外增加 try/except 兜底：无解时把快讯候选（能源/Builder/实践业务板块）并入头条候选池重试，让无命中群可从契合业务板块取头条，而非硬塞无关大事件。**保留 fail-closed 语义**——若并入业务板块后仍无解依然阻断（`test_required_profile_match_fails_closed` 仍 PASS）。

**验证**：`tests/test_feishu_push_dedup.py` 8 用例全过（含 fail-closed 阻断约束未破坏）。

## 3.7.0 - 2026-09-20

### 中国法定工作日硬校验（节假日 + 调休补班脚本级铁闸）

**背景**：调度层 `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR` 只看星期几，不知道中国法定节假日与调休补班。此前"周末自动结束"依赖 LLM 常识判断（软约束），法定节假日落在工作周（如春节/国庆/劳动节）仍会照常出刊、调休补班的周六周日不会出刊，两类高频场景全部裸奔。

**变更**：
- `run.py` 新增 `CHINA_HOLIDAYS` / `CHINA_MAKEUP_WORKDAYS` 按年份分桶表（2026 数据已内置，来源国办发明电〔2025〕7号），语义区分"法定休假日（停刊）"与"周末调休补班（出刊）"。
- `run.py` 新增 `china_workday_status()` 判定函数，返回四态：`off`（休假日）/`makeup`（补班）/`weekend`（普通周末）/`workday`（工作日）。
- `run.py main()` 在 `validate_report_date()` 之后、会话互斥之前接入（normal 模式且非 status 命令）：`off`/`weekend` 触发 `NOT_WORKDAY`，dry-run 返回 0（不写盘）、非 dry-run 返回 4 阻断后续阶段，不执行任何搜索/写稿/外部动作。
- 新增年份须人工录入表；未录入年份回退"仅星期"判定，避免跨年错配。
- `tests/test_run.py` 新增 `TestChinaWorkdayGuard`，覆盖四态全部真实日期（元旦/春节/清明/劳动/端午/中秋/国庆 + 全部调休补班日）。

**验证**：39 tests OK；冒烟：09-18（周五）放行、05-09（劳动节补班周六）放行、09-19（普通周六）与 10-01（国庆休假日）触发 `NOT_WORKDAY` 停刊。

## 3.6.2 - 2026-09-18

### 发布前总装预检增强（P1：状态回填 + knowledge package dry-run + 格式前置）

**背景**：V3.6.1 已能拦截正文/语义复核/检索清单三方来源不一致，但失败摘要尚未写入 run-state，且知识包构建风险仍需等 self_check 后或 publish 后才暴露；90 字、300 字、重点官方源缺项等高频格式问题也希望提前集中呈现。

**变更**：
- `preflight_check.py` 新增重点官方源扫描检查（openai/anthropic/google/meta/microsoft/xai/deepseek/qwen/bytedance/智谱/月之暗面/minimax/百度/腾讯）。
- `preflight_check.py` 新增正文轻量 lint：今日大事件一句话解读 ≤90 字，AI×能源风电 / Builder信号 / AI工作实践 / 深度聚焦正文 ≤300 字。
- `preflight_check.py` 新增 `--package-dry-run`：复用 `build_knowledge_package.build_package()` 做本地不写盘构包绑定预检；可使用 SELF_CHECKED 占位 publish-state，不访问论坛/飞书/Base。
- `run.py self-check`：preflight PASS → self_check PASS 后，自动追加 package dry-run；任一失败均 G3 FAIL。
- `run.py publish`：论坛写入前再次执行 preflight + package dry-run，防止 PASS 后产物漂移。
- `run.py` 将 preflight 结果摘要写入 `run-state.json.preflight`（status/report/exit_code/error_count/event_count/errors_preview/package_dry_run/sha256），便于自动化失败消息直接定位。
- 测试扩展至 87 tests OK，新增覆盖官方源缺项、90 字、300 字、package dry-run PASS/FAIL、run-state preflight 回填与 package dry-run 阻断。

**验证**：09-18 真实产物新版 preflight PASS，package dry-run 输出 1 issue / 9 events / 18 sources / 9 judgments / 1 run；真实 self_check 仍 PASS。

## 3.6.1 - 2026-09-18

### 发布前总装预检（P0：多产物一致性前置门禁）

**背景**：09-18 出刊阶段2/3 优化已生效（弱源往返为 0），但总耗时仍未明显下降，瓶颈转移到阶段5/7：自查返工暴露跟踪报道格式、候选池 schema、深度聚焦 T0 等问题；论坛发布后构建知识包又发现小米 MiMo 条目的正文来源、语义复核 source_urls、检索清单 source_urls 不一致。

**变更**：
- 新增 `scripts/preflight_check.py`：发布前总装质检，只读本地产物，不联网、不写外部系统。
- `run.py self-check` 先执行 preflight，失败即 G3 FAIL，阻断 self_check 与论坛发布。
- `run.py publish` 在论坛写入前再次执行 preflight，防止 self-check PASS 后正文/JSON 被改。
- 预检覆盖：正文 Markdown 标题/来源 URL 与 `semantic_review.source_urls`、`evidence_receipts.sources.url` 严格一致；正文条目必须被 search_log 的 SELECTED 候选覆盖；候选池必填字段、decision 白名单、日期字段、SELECTED 弱源；跟踪报道标题必须为 `（跟踪报道·M月D日）`。
- 新增 `tests/test_preflight.py`，覆盖 09-18 小米 MiMo 来源不一致复现、候选 source_urls 不完整、非法 decision、跟踪报道格式、SELECTED 弱源。
- `tests/test_run.py` 补 self-check/publish 前置 preflight 阻断用例。

**验证**：81 tests OK；09-18 真实产物 preflight PASS；模拟小米 MiMo semantic 缺 X 来源时 preflight FAIL（SOURCE_URLS_MISMATCH + RECEIPT_SOURCE_MISMATCH），可在论坛发布前拦截。

## 3.6.0 - 2026-09-17

### 全流程耗时优化（P0-② + 根因修复 + 计时细化）

**背景**：09-17 出刊全链路约 110-120 分钟，阶段2+3（检索/证据核验/封面/组稿）占 78 分钟，其中人机往返纠错 30-40 分钟。根因是弱来源未在源头拦截、引号/claim_type 等一致性依赖构包时才暴露。多期 run-state 显示阶段2/3 与阶段5 呈"跷跷板"（09-07 为 8/73min，09-17 为 78/11min），总纠错工作量守恒，故优先从源头消除工作量而非搬运。

**1. P0-② 候选池 SELECTED 弱来源硬门禁（新增）**
- `self_check.py` 新增 `candidate_weak_source_refs()`：SELECTED 候选的 `source_url` 或任一个 `source_urls` 命中弱来源（公众号/聚合号）即 `召回门禁` FAIL。
- 从源头拦截，避免弱源拖到正文/自查阶段才返工。本期真实案例：金开新能 toutiao、AgentGit 163.com/dy 两条弱源本会在阶段2 即被拦。
- 新增 4 个单元测试（`tests/test_validation.py::WeakSourceGateTests`）。

**2. 根因修复：normalize_title 引号一致性（build_knowledge_package）**
- `build_knowledge_package.py:65` 的 `normalize_title` 漏剥 ASCII 直引号 `"`（只剥弯引号 `“”‘’`），而 `self_check.py:99` 两者都剥 → 两处归一化不一致。
- 正文标题（ASCII 直引号）与语义复核标题（弯引号）在构包时因 key 不匹配导致"标题无法绑定"FAIL；自查阶段却因 self_check 归一化通过而遗漏。
- 已统一 build 与 self_check 的 `normalize_title` 字符集，消除构包绑定往返。

**3. claim_type / materiality 错误信息增强（self_check）**
- `validate_evidence_receipts`：枚举校验早已存在，但失败信息笼统；现附加明细（缺字段/claim_type 非法/materiality 非法等），Agent 定位更快。不改变失败条数（并入统一错误），69 测试无回归。

**4. P2-⑥ 阶段耗时明细（timings，可选）**
- 检索清单可带 `timings` 节点（`basis`∈d2/d4 + 子节点耗时），self_check 校验 basis 合法性（缺省不阻断）。
- 09-17 检索清单已回填 timings：机器检索 12 + 证据核验 18 + 封面 7 + 人机往返纠错 35 = 78min，作为后续排期真实基准。

**验证**：73 tests OK（69 + 4 新增）；用 09-17 检索清单实测新门禁拦截 AgentGit 163/dy 弱源；候选池已规整为纯强源。

## 3.5.8 - 2026-09-17

### 飞书推送摘要截断修复（避免关键数据被砍）

**背景**：09-17 日报生成三群差异化推送时，`extract_paragraph` 用默认 70 字符截取大事件一句话解读，把含关键数据的句子提早在字符中间砍断（如"新模型接入50-100行代码"→"新模型接入…"、"每吉瓦…7.33亿美元"被截、"本地Opus4.6"被截），发往千人群的摘要信息不完整。

**根因**：两级截断——`extract_paragraph` 固定 70 字先截解读（而大事件解读最多 90 字含标签）；`truncate` 再按 130 字机械断词，导致句子中间硬切。

**变更**：
- `extract_paragraph(block, limit=70)` 增加 limit 参数；大事件/能源/Builder/实践摘要提取改传 `limit=130`，保留关键数据交给 rewrite 阶段统一截断。
- `truncate(text, limit)` 改为句子边界截断：优先在最近的句号/叹号/问号/分号处截，无则回退逗号/冒号/顿号/空格，再有才硬截，避免机械断词。
- 推送测试新增截断边界用例，8 项全过。

## 3.5.7 - 2026-09-16

### KNS 群画像补齐 CRM 业务数据关键词

**背景**：09-16 日报有 Salesforce in Claude 的客户、机会、销售管线数据闭环，但 KNS 画像仅覆盖经营/财务/供应链/能源数据，导致三群唯一头条分配被误判为无匹配并阻断推送。

**变更**：
- KNS 画像补充 `客户/销售/机会/管线/CRM` 关键词，仍保留 `require_headline_match=true`，不放宽 fail-closed 门禁。
- 新增 09-16 真实选题形态回归测试，要求三群头条唯一、KNS 命中 Salesforce 业务数据选题且各群匹配分均大于零。

## 3.5.6 - 2026-09-16

### 发布与知识库状态真值加固

**背景**：本期真实发布中发现两条假成功路径：论坛发布回执已成功但 `run-state.forum` 仍为 `PENDING`；飞书 Base 写后立即回读受异步可见性影响，底层返回 `NEEDS_RECONCILIATION`，编排器却仅看退出码而误标 `SYNCED`。

**变更**：
- `run.py publish` 仅在 `publish-state.json` 明确含 `OK/topic_id/post_id/url` 时写入 `forum=PUBLISHED`，并记录本次用户论坛发布确认。
- `sync_lark_base.py` 对写后回读最多执行 3 次只读核验；不重复创建记录。确认模式下未取得 `SYNCED` 回执时返回非零。
- `run.py package` 同时校验同步回执内容；仅明确 `SYNCED` 才进入 `WAITING_HUMAN_REVIEW`，否则保持 `SYNC_PENDING`。
- 阶段从 FAIL 恢复为 PASS 时清除旧错误残留。

**验证**：全量回归 68/68 通过；本期五表稳定主键最终全部回读成功。

### 四个扩展模块增加 300 字阅读门禁

**背景**：AI×能源风电、Builder信号、AI工作实践、深度聚焦曾出现多段长文，移动端阅读负担过重。

**变更**：
- 四个模块正文统一不超过 300 字；标题、来源链接和固定属性说明不计入字数，优先压缩到 1-2 个短段。
- `self_check.py` 新增长度硬门禁，任一模块超限即自查 FAIL；同步增加单元测试。
- `report-template.md`、`rules-matrix.md` 与 `SKILL.md` 对齐新规则。

## 3.5.5 - 2026-09-15

### 三群差异化从“选题去重”升级为“群画像匹配＋专属视角改写”

**背景**：09-15 三群推送虽做到头条/快讯不重复，但 KNS 群因关键词无命中而兜底选中微软治理新闻；KNS 与本地模型群又直接复用日报原文的 `【部署边界】`，导致“内容不同、视角相同”。

**变更**（`prepare_feishu_push.py` + `feishu-group-profiles.json`）：
- 头条改用“标题权重2＋摘要权重1”的画像关键词评分，并对三个群做全局最优的一对一分配，避免按群顺序贪心抢走后续群的唯一匹配项。
- 各群默认 `require_headline_match=true`；无命中候选直接阻断草稿生成，不再按日报顺序硬凑。
- 群画像新增唯一 `angle_label` 与 `angle_suffix`：智能体群 `能力复用`、KNS 群 `数据价值`、本地模型群 `部署门槛`。
- 生成主摘要时先移除日报原判断标签，再加群专属标签和价值落点；发送前校验标签唯一、专属标签存在、无跨群标签混入。
- 扩充 KNS 关键词到电网/调度/告警/复电/分析/决策，扩充本地模型关键词到参数/推理/成本/SWE，避免群画像过窄。

**验证**：
- 专项测试增至 7 个，覆盖头条/快讯去重、专属标签替换、无匹配 fail closed、全局分配保留唯一匹配项，全部通过；全量回归 61/61 通过。
- 用 09-15 真实日报只读演练：A=SWE-2 `【能力复用】`、B=南方电网 `【数据价值】`、C=OM-1 `【部署门槛】`；不触发发送、不改已发记录。

**边界**：已发送的 09-15 消息不撤回、不补发；新规则从下一次自动化生成草稿起生效。

## 3.5.4 - 2026-09-14

### self_check 加固：一句话解读字数 + SELECTED 候选落窗硬门禁

**背景**（09-14 复盘 backlog）：
1. 「一句话解读≤90字」此前无脚本硬门禁，靠人工发现（09-14 曾一度 215-268 字）。
2. SELECTED 候选 event_date 落窗只隐含在大事件标题时校层，Builder/能源/实践板块候选越界不会被拦截（09-14 Builder Mantis 09-10 越界即漏网，window_pass=false）。

**变更**（`scripts/self_check.py`）：
- 今日大事件条目循环新增「一句话解读字数」检查：一行「一句话解读：」后全文（含【透镜标签】）去空白后 ≤90 字，超限即 FAIL。
- 召回门禁新增「SELECTED 候选落窗」检查：遍历 `candidates`，对 decision=SELECTED 且无「跟踪报道」标记、event_date 可解析的候选，断言其事件日期落在 `allowed_event_dates()` 窗口内；超出即 FAIL 并列出 id/标题/event。覆盖大事件/能源/Builder/实践全部板块。跟踪报道（旧事件）不在此判定，改由正文侧 check_event_date 复核原始日期。

**验证**：
- `python3 -m py_compile scripts/self_check.py` 通过。
- 用 09-14 真实产物复跑：新增门禁正确揪出 Builder Mantis event=2026-09-10 越界（自查 FAIL，98过1挂），证明"若当时有该门禁则不会漏网"。
- 已发布期（topic 514）不回改；自查报告保留新口径 FAIL 作为审计证据，run-state.audit_notes 已注明"新门禁回扫、历史越界、修订走 revise"。
- 顺带修复过期测试：`tests/test_run.py` 的 `test_reject_fake_future`/`test_valid_current_date`/`test_future_backfill_grace` 原硬编码 2026-09 具体日期，会随时间漂移（09-14 起 09-10 已是过去合法日期被误断言）。改为基于 `datetime.now(Asia/Shanghai)` 动态推算 offset 天，杜绝日期漂移。

## 3.5.3 - 2026-09-14

### 修复 run.py push「伪完成」（09-14 事故收尾）

**背景**：09-14「确认推送飞书」后 `run.py push --confirm` 只调 prepare 生成草稿，把 article_url/topic_id 写成 `0/0` 覆盖了草稿，随后直接置 `review_status=CONFIRMED_BY_USER`、`run_status=RUN_COMPLETED`——**未真正发送也没有群级 SENT 回执**，造成"状态显示全链路完成但消息未发"的伪完成。

**变更**（`scripts/run.py` + `config/runtime.json`）：
- `cmd_push` 改为从 `publish-state.json` 读取**真实** article_url/topic_id（缺失或为 "0" 即 fail closed，禁止发送）。
- 逐群调用 `lark-cli --profile <profile> --as user im +messages-send --markdown ... --idempotency-key ...` 真正发送，并解析 message_id。
- 逐群 `prepare_feishu_push.py record <status>` 写盘，`run-state.feishu.groups` 记录 SENT/FAILED 与 message_id；发送间隔使用幂等 key 防重试重复。
- 任一群失败 → `run_status=PUSH_FAILED`（已加入 TERMINAL_STATES），如实记录，不伪完成。
- `runtime.json.paths` 新增 `feishu_push_profile`/`feishu_push_identity`（默认 profile=`cli_aa942f2aba38dccd`、identity=user），可被环境变量 `AI_RADAR_FEISHU_PROFILE`/`AI_RADAR_FEISHU_IDENTITY` 覆盖。

**验证**：`python3 -m py_compile scripts/run.py` 通过；`tests/test_feishu_push_dedup.py` 4 用例全过。
**说明**：本次 09-14 三群已手工逐群发送成功（message_id 已记录），修复后未对已发期重跑 push，避免重复发群。

## 3.5.2 - 2026-09-14

### 三群快讯真正差异化（第 15 期发布前修复）

**背景**：09-14 三群差异化草稿中，头条与互动问题已按群画像区分，但「快讯」三群重复为 OpenAI + NVIDIA。根因：3.5.1 只保证快讯不使用全体群头条，没有维护跨群快讯占用池；且快讯候选仅来自今日大事件，在 5 条大事件、3 条已作头条时只剩 2 条可选，必然重复。

**变更**（`scripts/prepare_feishu_push.py`）：
- 新增 `extract_section_cards()` 与 `append_unique_candidate()`：快讯候选从「今日大事件」扩展到 AI×能源、Builder信号、AI工作实践板块，保留日报顺序并去重。
- 新增 `keyword_score()` 与 `select_flash_titles()`：快讯按群画像关键词优先选择，并维护全局 `used_flash_titles`，候选足够时三群快讯互不重复。
- `build_message()` 增加 `flash_candidates` / `flash_titles` 参数；快讯由 `prepare()` 统一分配，独立调用时保留兜底逻辑。
- 候选不足时宁可某群少于 2 条快讯，不强行重复。

**验证**：
- `python3 -m py_compile scripts/prepare_feishu_push.py` 通过。
- `tests/test_feishu_push_dedup.py` 新增「候选足够时三群快讯互不重复」用例；4 个用例全过。
- 重新生成 2026-09-14 真实飞书草稿：A/B/C 三群头条不同，6 条快讯跨群全部不重复。

**边界**：仅改飞书草稿生成逻辑；不改已发布论坛正文；飞书仍需用户二次确认后发送。

## 3.5.1 - 2026-09-11

### 跨群头条/快讯去重（第 14 期复盘修复）

**背景**：09-11 三群差异化推送后用户发现「AI智能体开发平台运营群」与「本地大模型用户交流群」推送内容近似相同（均为 DeepSeek 头条 + 同组快讯）。根因：旧 `build_message` 每个群独立按 `headline_keywords` 选头条，A 与 C 群关键词都含 `DeepSeek`，命中同日首条大事件即撞车；快讯取「头条之后的顺位」也一字不差。属**脚本级缺陷**，每天都会在多头条关键词重叠时复发。

**变更**（`scripts/prepare_feishu_push.py`）：
- 新增 `select_headline()`：头条选择抽成独立函数，优先级＝「本群关键词命中且未被占用 → 全局最早未被占用 → 第 1 条」。
- `prepare` 改为**两遍生成**：
  - 第一遍按群序为每个群分配**互不重复的头条**（全局占用池 `all_headlines`），记录 `headline_indices`。
  - 第二遍统一构造消息，`headline_index` 显式传入（不再重新选头条），`used_headlines=全体群头条集合`，使每群**快讯只取『未被任何群用作头条』**的标题。
- `build_message` 增加 `headline_index` 参数；头条与快讯均不跨群重复。

**验证**：
- 新增 `tests/test_feishu_push_dedup.py`（3 用例：DeepSeek 撞车去重 / KNS 命中专属头条不被挤占 / 快讯不含其他群头条）全过。
- 在 2026-09-11 真实日报上复检：三群头条互不重复（A=DeepSeek / B=最高法 / C=高德世界模型，C 命中本群「世界模型」强匹配项）。
- 既有测试套件 57 用例中唯一失败 `test_run.test_reject_fake_future` 为**过期假想日期**（断言 2026-09-10 为未来，但今日已是 9-11），与本次改动无关。

**边界**：仅改推送文案生成逻辑；群配置/画像/签名不变；已发送的三群不再重发（用户已确认维持现状）。

## 3.5.0 - 2026-09-11

### 三群差异化飞书推送（群画像驱动）

**背景**：日报飞书推送此前对所有群生成同一份【今日重点】摘要；用户明确要求三群差异化——「不要把整份日报塞进群里，群消息只让人快速看懂『与群的关系』，详细交链接；三群头条/提问/落点各不相同；保留统一完整签名档」。

**变更**：
- 新增 `references/feishu-group-profiles.json`：三群差异化画像（A 智能体 / B KNS / C 本地模型），含群专属 emoji、头条关键词、互动问题、落点角度、完整签名档。
- `references/feishu-groups.json` 补齐第三群 **KNS统一数据平台**（`oc_52da4aa933ef08de47ec321717039e6b`）。
- `scripts/prepare_feishu_push.py` 改写为按群生成「一主讯息＋两快讯＋一问题＋阅读全文＋统一签名档」；头条按群 `headline_keywords` 从当日大事件自动挑选（未命中回退首条），支持 `--profiles`。
- `scripts/run.py::cmd_push` 改为从 runtime.json 读 groups/profiles 路径并透传 `--profiles`（原 `--groups auto` 无效）。
- `config/runtime.json` 新增 `paths.feishu_group_profiles`，版本升至 3.5.0。
- `SKILL.md` 阶段 8、reference 路由表同步更新为三群差异化。

**边界**：本轮只改推送链路的群配置与文案生成逻辑；论坛日报正文模板 `report-template.md` 不变。发送仍须人工确认（外部可见动作门禁不变）。

### 日报文末模板对齐最新发布（用户指定签名文案，2026-09-11）

**背景**：日报模板 `references/report-template.md` 的文末仍为旧版（`# 🔗 AI应用研究院-技术支持平台` 2平台、3码含旧"AI信息交流群"、单层出品签名），而最新日报 508（9.10）已实际采用 5平台能力地图 + 新4码，签名套用科普 509 同源的双层署名，模板与发布脱节。

**变更**：
- `references/report-template.md` 文末块整体升级：
  - 平台导航：`# 🔗 AI研究院-技术支持平台`(2平台) → `# 🔗 集团AI能力地图`(5平台：算法市场/Dify/语义本体/AI数字员工中心/KNS)。
  - 技术交流群：旧 3码(含旧 AI信息交流群 `6eiXQdDWg`) → 新4码（日报专用，本地大模型/AI智能体运营群/金风科技/金风科技-KNS）。
  - 免责声明：保留日报口径 `【AI日报- Agent】自动整理报道`（含 " AI日报- Agent" 空格）。
  - 签名档：按用户 2026-09-11 指定改为与科普 509 一致的双层署名——`**集团数字化 · AI运营团队 出品**` / `**AI应用研究院 整理报道**` / 跟踪语 / `**欢迎投稿**…小礼品🎁` / `[往期合集](c/15) | 💬 … | 🤝 …`。
- 格式规则 §1、§10 同步更新。
- 记忆 `~/.workbuddy/MEMORY.md` 日报类补「日报文末标准」（含日报专用 4 二维码 upload 链接）。
- 版本说明：仅模板文件与记忆更新，`SKILL.md`/`config/runtime.json` 无独立版本号字段，故不改动整体版本号（CHANGELOG 条目按 3.4.1 记档以便追溯）。

**边界**：本次只改模板定义与记忆，未回改已发布的历史日报（9.8/9.9 保持旧文末，9.10 起新文末）。

### 补充：self_check.py 门禁同步新文末（用户反馈，2026-09-11）

09-11 第 14 期出刊时用户指出文末仍非最新模板，已在正文套用新文末（4码 grid + 双层签名），并**同步升级 `scripts/self_check.py` 门禁**，否则自检 FAIL：
- `CONTACT_LINE`：移除旧「如有疑问或赋能需求，可联系【谭健明】」句，与模板免责对齐（联系动作由签名档"欢迎投稿"承担）。
- 新增 `SIGN_LINE_1`/`SIGN_LINE_2`，出品行校验从单行 `PRODUCER_LINE` 改为「双层签名档完整」（`**集团数字化 · AI运营团队 出品**` + `**AI应用研究院 整理报道**` + 往期合集）。
- 二维码资源校验：废弃「固定 3 码槽位 qr1/2/3 硬查」，改为解析正文 `[grid]` 内实际引用的 `群二维码_*.png` 并断言文件存在（工作目录残留的 `AI信息交流.png` 不再参与判定）。
- 修一处正则解包 bug（`re.findall` 单捕获组误解包为两个）。
- 后续期次自检自动适用新门禁；第 14 期正文 + 语义复核 body_sha256 已同步并复验 0 FAIL PASS。

## 3.4.0 - 2026-09-09

### 新增阶段 9：复盘与回填（用户指令，2026-09-09）

**背景**：用户要求 AI 日报自动化任务结束后进行专业总结性复盘，且必须核验"检索时间范围是否符合预期"。原八阶段止步于阶段 8 飞书推送，无复盘环节；时间窗设计虽已采用执行点增量口径（V3.3.0），但缺"执行后核验窗口符合预期"的动作与可比对产物。

**变更**：
- 新增 `references/review-recitation.md`：五维复盘清单（时间窗/出刊/质量/数据/改进）、时间窗核验三问（①检索回看覆盖 ②SELECTED 事件 event_date 落在预期增量窗口 ③无漏收无误杀）、`recap.json` 结构定义、3 行复盘摘要规范。
- `SKILL.md`：八阶段→九阶段；新增阶段 9（触发时机、只读核验边界、recap 键合并进 run-state、3 行摘要要求）；reference 路由表加复盘条目；版本 3.4.0。
- `config/runtime.json` version 同步 3.4.0（三处一致落版）。
- 阶段 9 暂由执行 Agent 手工执行；`run.py recap` 子命令与时间窗机器比对脚本列入独立脚本工作流（本版未改 scripts/）。
- 自动化（automation-1787192486737）prompt 同步：引用 V3.4.0、九阶段，收尾增加复盘与 3 行摘要要求。

**边界**：复盘不是第五道门禁，不阻断已完成发布、不修改已验证正文；发现错误走 `revise`。

## 3.3.1 - 2026-09-08

### 假日期污染修复 + 出刊失败根因处置

**背景**：今日（09-08）10:00 自动化出刊失败，排查发现工作目录反复出现假日期残留目录/文件（`AI-RADAR-2099-03-04` 空目录、`AI雷达日报_2099-12-31_近期去重.json`），且始终无法生成正确的 `AI-RADAR-2026-09-08` 期次。

**根因（三层）**：
1. **测试套件污染真实工作目录（主因）**：`tests/test_run.py` 中多个用例（`TestGateEnforcement`、`TestIdempotency`）直接以 `dry_run=False` 调用 `cmd_plan`/写盘，且 `test_plan_increments_revision` 完全不清理 —— 每次运行测试都会在真实 work_dir 留下 `AI-RADAR-2099-*` 假日期目录（昨天的 `2099-12-31`、今天的 `2099-03-04` 即由此而来）。这些残留污染了出刊日期锚点，使新刊无法落到正确日期。
2. **测试用例耦合真实产物**：`test_status_9_04_shows_all_passes` 依赖真实 `09-04` 期产物，非纯单元测试。
3. **编排器缺少日期防呆**：`run.py` 对 `report_date` 无异常值校验，假日期可直接进入 plan/mkdir。

**修复**：
- `scripts/run.py` 新增 `validate_report_date()` 防呆护栏，在 `main()` 解析参数后、会话互斥前执行：拒绝格式错误、年份 <2020（防残留）、>今日+1（防假未来日期）；违规→ `INVALID_REPORT_DATE` exit 2，不再创建任何目录。保留 future-backfill 的 today+1 余量。
- `tests/test_run.py` 增加**测试隔离**：模块级将 `AI_RADAR_WORK_DIR` 指向临时目录（后缀 `-ai-radar-daily` 兼容命名断言），`tearDownModule` 自动清理；所有真实写盘的假日期测试不再触碰真实工作目录。
- `test_status_9_04_shows_all_passes` 改为在隔离目录内构造全 PASS 状态，不再依赖真实 09-04 产物。
- 清理既有假日期残留：删除 `AI-RADAR-2099-03-04` 空目录、`AI雷达日报_2099-12-31_近期去重.json`。
- 新增 5 项 `TestReportDateGuard` 护栏测试。

**回归**：54 项测试全过（validation 16 + run 25 + knowledge_package 13），py_compile 通过，测试后真实工作目录零污染。

## 3.3.0 - 2026-09-08

### 组织更名与推送标题（用户指令）

- **组织名统一更名「AI应用研究院」**（原「AI研究院」），覆盖全部模板与产物生成链路：
  - `references/report-template.md`（技术支持平台标题/平台链接/免责声明/出品行/格式规则引用）
  - `scripts/self_check.py`（FIXED 板块行、CONTACT_LINE、PRODUCER_LINE 前缀、报错文案）
  - `scripts/prepare_feishu_push.py`（签名档「📡 AI应用研究院 出品」）
  - `tests/test_validation.py`（测试夹具）
  - 项目根 `飞书推送消息模板.md`（日报模板全文/规则/示例/科普复用模板）
  - `ai-radar-weekly/references/report-standard.md`（周报模板技术支持平台/联系行）
- **飞书推送消息标题精简**：去掉研究院前缀，`【AI应用研究院 · AI雷达日报｜M.DD】` → `【AI雷达日报｜M.DD】`；原因是结尾签名档已含「AI应用研究院 出品」，前缀重复且标题过长。同步应用于日报与科普/业务实践复用模板，`prepare_feishu_push.py build_message` 逻辑更新。
- 历史已发布期次（2026-09-07 及更早）保持原名不改。
- `SKILL.md` version 升 3.3.0，`config/runtime.json` version 同步 3.3.0（修复此前 3.2.3 未同步的漂移）。
- 回归：49 项单测全过（validation 16 + run 20 + knowledge_package 13），py_compile 通过。

### 检索时间窗改「执行点增量口径」（方案 A，2026-09-08 用户指令）

- **口径纠正**：原「自然日口径」只允许收前一日（周一收 3 天），导致每日 10:00 执行无法纳入「上一次执行之后」的新数据，丧失实时性。
- **核心变更**：正文收录窗口从「自然日」改为「上一执行点之后 → 当前执行点」的增量，映射为日期集合：
  - 周二至周五：`{report_date-1, report_date}`（含执行当日）
  - 周一：`{report_date-3 .. report_date}`（上周五至周一，跨周末）
- **代码落地**：`scripts/self_check.py` 的 `allowed_event_dates` 周一清空 `offset in (0..3)`、周中 `(0,1)`；模块 docstring 与 `allow_same_day_ok` 报错文案同步更新。
- **去重窗口对齐**：`scripts/fetch_recent.py` 的 `periods` 缺省改为按报告日期自适应（周一=4，其余=2）。
- **策略文档**：`references/retrieval-strategy.md` 改写「为什么 10:00 运行」+「2. 正文时间窗」章节，明确增量窗口表格；search-log schema 的 window 注释更新。
- **测试**：`tests/test_validation.py` 新增周一跨周末包含与周中含当日 2 项测试，替换原 monday allow-same-day 测试；补 `timedelta` 导入。
- **回归验证**：真实 9-07（周一）期在 9-04/9-05 事件命中新窗口、9-03 跟踪报道走 `is_followup` 分支不被误伤；self_check 仅因组织更名（旧名出刊）失败，属预期，非日期门禁回归。

## 3.2.3 - 2026-09-07

### 补记：脚本级待办落地（此前 SKILL.md 已升 3.2.3，CHANGELOG 漏记）

- **会话互斥**：`run.py` 新增 `--session-id`/`--takeover`；run-state 写 `owner_session`/`heartbeat`；其他会话在心跳 10 分钟新鲜期内执行状态变更命令 → `PARALLEL_SESSION_BLOCKED`（exit 3）。
- **封面字节数门禁**：`self_check.py` 封面 <500KB 判 FAIL（真实 ImageGen 封面 1.7-2.2MB，程序化卡片 162KB）。
- **深度聚焦官方源硬门禁**：原门禁只查语义复核自声明 `official_sources_only`，改为媒体域名黑名单（40+）+ 回执来源全 T0 双重硬校验。
- **publish.py 状态合并**：新增 `merge_state_file`，更新/异常路径均合并保留既有字段（topic_id/url/title/created_at/reconciliation），不再覆写为存根。
- 复盘文档 `AI-RADAR-2026-09-07_问题复盘.md` 待办表全部标记完成。

## 3.2.2 - 2026-09-04

### 统一编排器（run.py）

- 新增 `scripts/run.py`，将八阶段流水线收编为 7 个可独立调用的子命令：`plan` / `retrieval` / `review` / `self-check` / `publish` / `package` / `push`，外加全跑入口 `all` 与状态查询 `status`。
- **状态机内化**：stage 推进、门禁写入、双确认门（WAITING_FORUM_CONFIRMATION / WAITING_HUMAN_REVIEW）由 run.py 维护；Agent 调用 run.py 推进，不再手写编排。
- **schema_version 动态同步**：run.py 启动时从 `config/runtime.json` 读 `version` 写入 `run-state.json`；修复 9-04 run-state 中硬编码 3.2.0 的漂移（9-05 起新建期次自动写 3.2.2）。
- **work_dir 三级 fallback**：`AI_RADAR_WORK_DIR` 环境变量 > `runtime.json.work_dir` > `skill_dir/../ai-radar-daily` 默认；硬编码路径被消除。
- **外部动作门**：`publish` / `push` 子命令未带 `--confirm` 直接退出；`--dry-run` 全程不写盘。
- 新增 `tests/test_run.py`（20 项）：覆盖状态机、门禁阻断、幂等、dry-run、schema_version 动态、双确认门、CLI 结构、status 摘要；与原有 28 项合并后 **48/48 全过**。
- `SKILL.md` 升 3.2.2，新增「10. 统一编排器（run.py）」章节（七子命令表 + 调用契约 + 三条强制规则 + 断点续跑 + status 摘要）。
- V3.2.1 9-04 实战首跑全链路全绿（门禁 G1=G2=G3=G4=PASS、论坛 topic_id 492、五表 SYNCED、飞书双群 SENT、零 errors），run.py 与既有产物完全兼容。

### 安全待办

### 知识库远端打通

- 目标飞书知识库为个人租户（qdv30zar2p.feishu.cn），与金风租户应用身份跨租户不可达（131006）；通过 lark-cli 多 profile 机制新增 `personal` profile 绑定个人租户自建应用，以 bot 身份完成接入。
- 在 Wiki 节点「AI雷达｜日报」下创建多维表格「AI雷达日报·结构化知识库」（base_token Hhx5bvXBda6RH1sCVxacRfirn6c），按 `references/knowledge-base-schema.md` 建齐五表：期次 Issues / 事件 Events / 来源 Sources / 判断验证 Judgments / 运行记录 Runs；枚举建成单选、日期建成 datetime、URL 带样式。
- `sync_lark_base.py` 适配真实环境：支持 `knowledge_sync.profile`/`identity` 配置；datetime 字段 ISO→毫秒时间戳转换；写入丢弃 None 字段；兼容 `+record-list` 的 manifest 返回契约（无 ok 字段）。
- `config/runtime.json` 的 `knowledge_sync.enabled=true` 并写入真实 base_token/table_ids；9-03 期知识包已真实回灌（1 期/12 事件/26 来源/12 判断/1 运行），写后回读稳定主键零缺失，状态 SYNCED。
- 统一 `SKILL.md` 与 `config/runtime.json` 版本标识为 3.2.1；补全知识包示例的 `--publish-state` / `--run-state` 参数，并将发布脚本 `--state-file` 指向独立 `publish-state.json`，避免发布结果覆盖总运行状态。
- 回归：28 项测试全过。

### 安全待办

- 个人租户 App Secret 曾在对话中明文传递，建议稳定运行一段时间后在开放平台轮换；个人 profile token 过期需重新授权。

## 3.2.0 - 2026-09-03

### 重构

- 将 `SKILL.md` 从 V3.1 全量操作手册改为可冷启动的路由与硬约束，统一正常、补发、修订和恢复模式。
- 引入单一运行契约、八阶段流程、P0 安全边界、四道门禁、外部动作分阶段人工确认和原子状态要求。
- 将详细规则拆分到 `references/rules-matrix.md`、`run-contract.md`、`evidence-standard.md`、`exception-policy.md`、`judgment-method.md`、`retrieval-strategy.md`，按阶段加载。
- 保留 `references/report-template.md` 为模板与固定尾部的唯一权威定义；保留 `references/feishu-groups.json` 为群配置唯一来源。
- 增加论坛回读后、飞书人工审查前的知识库同步步骤；远端未配置或失败时保留本地 knowledge package 并标记 `SYNC_PENDING`。
- 新增无秘密的 `config/runtime.json`，集中非敏感运行默认值、论坛元数据、绝对路径和知识库同步开关。
- 明确 claim-evidence receipt、事实—来源绑定、空证据 fail closed、脚本能力边界，以及事件日程台账与编辑判断验证台账分离。
- 文档与配置重构不修改 `scripts/`。

### 已知阻塞

- 知识库远端目标、Base/Table 标识尚未配置，且外部 Wiki/Base ACL 尚未确认；默认 `knowledge_sync.enabled=false`。当前只能生成本地 knowledge package，状态必须为 `SYNC_PENDING`。
- 飞书群推送仍依赖外部 Connector 与目标群 ACL；不可用时保留推送草稿并按群记录失败，不得伪造发送成功。
- 论坛、知识库和飞书的外部写入均需要本次运行的明确人工确认；历史授权不自动沿用。

## 2026-10-08 累积出刊窗口修复
- `preflight_check.py::expected_dates_for`：支持按检索清单 `publication_metadata.missed_dates`（`recovery_reason=="missed_runs"`）扩展允许窗口，累积出刊不再把所有补检日误判为越窗并强制「跟踪报道」前缀。
- `self_check.py::allowed_event_dates`：支持环境变量 `AI_RADAR_MISSED_DATES`（逗号/空格分隔日期）扩展 allowed 集合，与 preflight 口径对齐。
- 背景：缺跑补检累积出刊（V3.9.3）场景下，两个脚本都只按 d2/d4 硬算窗口，与 `references/retrieval-strategy.md` §2「未执行日窗口顺延覆盖上次执行至今」的口径冲突。
- `self_check.py::allowed_event_dates`：新增 `extra_dates` 参数，`run_checks` 自动读取检索清单 `publication_metadata.missed_dates` 扩展允许窗口，与 `preflight_check.py` 口径一致。此前依赖 `AI_RADAR_MISSED_DATES` 环境变量，累积出刊时若 Agent 忘设会重现「补检日全部越窗」误判。已验证不带环境变量亦可 PASS。
