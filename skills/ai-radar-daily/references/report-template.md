# 日报模板（V5 三层版式，2026-10-30 起）

> V5 把一期日报分成三层：**第一层 · 精编日报**（沿用 V4 证据门禁）、**第二层 · 本周跟踪**（窗口前 7 天已报大事的后续）、**第三层 · 信号雷达**（社区与单一来源信号，标清可信度）。
> 正文是 Discourse 原生 Markdown + `[wrap=air-*]` 区块 + `<span data-air-chip>` 行内标签，由主题组件「AI 雷达日报样式 v2」（`assets/discourse-theme/`）渲染成卡片、数字卡、可信度条和雷达筛选器。未装组件时正文仍可读（降级为普通段落）。
> 门禁：`layout_v5.check_structure()` 校验版式与三层规则，再由 `layout_v5.to_v4()` 把精编层转成 V4 结构交给 `self_check` / `preflight_check` / 飞书推送 / 知识库构包，**精编层的证据口径与 V4.2 完全一致**。
> 历史期次（V4，≤ 2026-10-29）不回溯改写，脚本按是否含 `[wrap=air-daily]` 自动分流。

## 文件结构

```
[wrap=air-daily]

# {钩子} |AI雷达{M}.{D}

> 今日主线：一句话点出当天最该注意的一件事，带具体数字或动作。

![AI雷达日报封面](AI雷达日报_YYYY-MM-DD_封面.png)

[wrap=air-stats]
[wrap=air-stat]
**8**
精编（双源核验）
[/wrap]
[wrap=air-stat]
**5**
本周跟踪
[/wrap]
[wrap=air-stat]
**20**
信号雷达
[/wrap]
[wrap=air-stat]
**33**
合计
[/wrap]
[/wrap]

[wrap=air-mix]
🟢 官方 + 独立 7　🟡 当事方 / 媒体 10　🔴 单一来源 16　（全部 33 条的可信度分布）
[/wrap]

## 第一层 · 精编日报

[wrap=air-lede]
每条两个独立来源，事件日期落在 M/D–M/D，带一句“对我们”。
[/wrap]

[wrap=air-card cred=g]
<span data-air-chip="sec">今日要紧</span> <span data-air-chip="cred" data-cred="g">🟢 官方 + 独立</span>

#### 标题（M月D日）

正文一段，直叙事实与数字，≤160 字。

[wrap=air-us]
**对我们：** 一句话说明对内部团队的实际含义，≤90 字。
[/wrap]

[wrap=air-src]
[openai.com ↗](URL) · [media.com ↗](URL)
[/wrap]
[/wrap]

<!-- 今日要紧 2-8 张卡 → 金风相关 0-2 张（无则写一行 NO_ENERGY_LINE）→ 工具与做法 0-4 → 行业速递 3-10 -->

今日暂无经双源核验的金风相关重要消息。

[wrap=air-card cred=r]
<span data-air-chip="sec">行业速递</span> <span data-air-chip="cred" data-cred="r">🔴 单一来源</span>

#### 标题（M月D日）

一句话事实；单一官方来源，尚无独立验证。

[wrap=air-src]
[sec.gov ↗](URL)
[/wrap]
[/wrap]

## 第二层 · 本周跟踪

[wrap=air-lede]
窗口前 7 天内已报的大事，今天有后续或仍在发酵，一行带过。
[/wrap]

[wrap=air-card cred=g]
<span data-air-chip="sec">跟踪</span> <span data-air-chip="cred" data-cred="g">🟢 官方 + 独立</span>

#### 标题（M月D日）

一两句后续，≤100 字。

[wrap=air-src]
[deepmind.google ↗](URL)
[/wrap]
[/wrap]

## 第三层 · 信号雷达

[wrap=air-lede]
社区热度与单一来源信号，未经双源核验，按类别筛选；🔴 条目只作线索，不作结论。
[/wrap]

[wrap=air-radar]

[wrap=air-sig cat="开源与模型" cred=g]
<span data-air-chip="sec">开源与模型</span> <span data-air-chip="cred" data-cred="g">🟢 官方 + 独立</span> <span data-air-chip="from">Hacker News 805</span>

**标题（不强制日期）**

一两句：是什么、热度或数字从哪来，≤100 字。

[wrap=air-src]
[mistral.ai ↗](URL)
[/wrap]
[/wrap]

[/wrap]

## 社区今日

- **要做设备原理图的同事：** [帖子原标题](URL)

## 今日一问

一句提问，回复一个数字或一句话即可。

[/wrap]

---
### 集团AI能力地图
- [AI基础能力支持](https://aiplatform.goldwind.com.cn/portal/algorithmMarket)
- [AI智能体开发平台](https://keconsole.goldwind.com)
- [风衍-风电行业智能倍增器](https://fengyan.goldwind.com)
- [语义本体构建及应用平台](http://10.12.20.24:38087/ontology/web/index.html#/admin/tenants)
- [AI数字员工共享中心](https://acnrie67tlx1.feishuapp.com/app/app_17bdqvk24hx)
- [KNS统一数据平台](https://kns.goldwind.com/)
### 技术交流群
[grid]
![本地大模型用户交流群|400x400](upload://onqFmqGan75qPrqeDL0jLJVPdXR.png)
![AI智能体开发平台运营群|400x400](upload://jKV9fBCLNz08SUhjcSKtrmiRUuG.png)
![语义本体构建交流群|400x400](upload://yqIcRbvnSJmL6ni9hX7yPbZ1rTN.png)
![KNS统一数据平台|400x400](upload://pjmuSZ09UjIJLmXl3GW496AV8OD.png)
![标准化助手交流群|400x400](upload://zki0MKU5tCanUWYmNnwU4KWKh5E.jpeg)
![风衍大模型用户交流群|400x400](upload://dLty0KnxSeACCvXacs3O0pHKpg6.jpeg)
[/grid]
---
> 以上内容为【AI日报-Agent】自动整理报道，仅供内部信息参考。涉及产品选型、生产部署、设备诊断和安全决策时，应结合正式文件、业务数据与专业人员意见复核。AI不替代责任主体的最终判断。
**出品｜集团数字化中心**
**策划整理｜AI应用研究院**
我们跟踪 AI 前沿、沉淀金风业务实践、帮业务部门把 AI 用起来
欢迎分享发布科普内容或者你的心得与技巧到 AI 社区，我们一起交流、探讨！
```

## 格式规则

### A. 容器与语法（Discourse 渲染约束）

1. 全文正文（H1 到「今日一问」）包在 `[wrap=air-daily]` … `[/wrap]` 内；**尾部固定模块（`### 集团AI能力地图` 起）必须在容器外**，并以 `---` 隔开。全文只有首行 H1 标题，尾部两个模块均为 H3（V5.2.2 起）。
2. `[wrap=…]` 与 `[/wrap]` **各占一行**、成对闭合；**一行不得出现两个 `[wrap=`**（Discourse 会错配，10-30 样稿实测）。行内标签一律用 `<span data-air-chip="sec|cred|from">`，可信度标签带 `data-cred="g|y|r"`。
3. 卡片的 `cred` 属性与 cred 标签的 `data-cred` 必须一致；信号卡 `cat` 属性与 sec 标签文字一致，且必须有一个 `from` 标签写来源名（带热度时写数字，如 `Hacker News 805`、`GitHub 今日 +1930★`）。
4. 卡片标题：精编与跟踪用 `#### `，信号用 `**粗体**`。来源一律放进 `[wrap=air-src]`，链接文字写 `域名 ↗`，多个用 ` · ` 分隔。
5. 帖子内不能写内联 style 或脚本；样式只靠主题组件。不得出现 `<script>`、`style=`。

### B. 首屏

6. 首屏顺序：H1 `# {钩子} |AI雷达{M}.{D}`（**月、日都不补零**，如 `11.3`；旧格式 `AI 雷达日报｜M.D` 仅兼容 ≤2026-11-10 期次；V5.3.2 起标题规则见 `SKILL.md` §5.9 与 `scripts/title_spec.py`）→ `> 今日主线：…` → 封面图（缺图时改写固定降级行 `> AI 雷达日报 · 每日洞察 AI 赋能风电`，即 `self_check.FALLBACK_COVER`，编排器记 `COVER_MISSING_FALLBACK`）→ `air-stats` 四个数字卡 → `air-mix` 可信度条。
7. 数字卡固定四个：精编 / 本周跟踪 / 信号雷达 / 合计，数字必须等于实际卡片数（`check_structure` 逐项核对）。
8. `air-mix` 三个数字分别等于全部卡片（三层合计）中 g / y / r 的数量。可信度条与雷达筛选按钮由组件 JS 生成，正文不写。

### C. 第一层 · 精编日报（证据口径同 V4.2）

9. 四个分区按序出现：今日要紧（2-8）→ 金风相关（0-2；为 0 时写 `今日暂无经双源核验的金风相关重要消息。`）→ 工具与做法（0-4；为 0 时写 `今日暂无新的可直接借鉴的做法。`，V5.0.1 起）→ 行业速递（3-10）。两条固定空行各占一行、放在对应位置，不加 wrap。分区由 sec 标签区分，不另起 `##` 标题。
10. 正文字数（去空白，不含标题、标签行、对我们、来源）：今日要紧 / 金风相关 ≤160，工具与做法 ≤120，行业速递 ≤100。
11. 「对我们」：今日要紧、金风相关每张卡必须且仅一条，工具与做法可选，行业速递不写；≤90 字。
12. 标题日期：除工具与做法外，标题末尾带 `（M月D日）`，日期落在刊发窗口内。
13. 来源：🟢 / 🟡 的非工具卡至少两个独立来源链接；🔴 卡正文须写明「单一（官方）来源」。弱来源（公众号、百家号、搜狐号、网易号、头条、快资讯、知乎专栏等）不得作为来源。
14. 每张精编卡在检索清单中对应一条 `SELECTED` 候选，语义复核与 claim-evidence receipt 要求不变。

### D. 第二层 · 本周跟踪

15. 0-8 张卡，sec 标签固定为 `跟踪`；只收**事件日期在 [D-7, 窗口起点) 内**、此前已报或已入池的大事的后续。日期写不准（如「10月中旬」）的不得进本层。
16. 正文 ≤100 字，不写「对我们」；至少一个来源。检索清单对应候选 `decision: "TRACKING"`，标题与 URL 须一致。

### E. 第三层 · 信号雷达

17. 0-40 张信号卡，全部放在一个 `[wrap=air-radar]` 内。允许单一来源，但必须如实标可信度：🔴 单一来源 / 🟡 当事方或媒体 / 🟢 官方 + 独立。黑名单弱来源同样禁入。
18. 类别 `cat` 只取八类：新品与工具 / 开源与模型 / 前沿论文 / 行业动态 / 安全与研究 / 能源与金风 / 社区热议 / 传闻待证。
19. 检索清单对应候选 `decision: "SIGNAL"`，带 `cat`、`heat`（可空）、`discovered_at`；标题与 URL 须与正文一致。来自 `fetch_sources.py` 的 `layer: "signal"` 条目是主要输入。
20. 正文 ≤100 字；只写「是什么、热度或数字从哪来」，不下结论、不写「对我们」。

### F. 尾部与其他

21. 「社区今日」须是 2-3 条真实帖子（取材见 SKILL.md §5.5），带“谁该点开”的场景标签，**不得留占位行**；来源链接须公司网络可打开（SKILL.md §5.6）。「社区今日」「今日一问」在三层之后、`air-daily` 容器之内，顺序固定；规则同 V4（社区帖用原标题，一个问题）。
22. 尾部固定模块（**2026-11-12 用户定稿，最新标准**）：`[/wrap]` → `---` → `### 集团AI能力地图`（6 平台，首行文案为「AI基础能力支持」）→ `### 技术交流群` → `[grid]` 6 码（图片连续成行、不留空行）→ `---` → 免责声明 → 双层署名 → 跟踪语 → 社区交流邀请。**顺序不可调**：免责声明在署名之前；能力地图为 H3（全文只有首行一个 H1）。
    - 末行固定文案：`欢迎分享发布科普内容或者你的心得与技巧到 AI 社区，我们一起交流、探讨！`——**不带** `**欢迎投稿**：` 加粗前缀（2026-11-12 取消）。
    - **往期合集行已删除**，正文尾部不再挂 `[往期合集](…)`；往期入口由论坛分类页（`/c/15-category/15`）与首页承载。尾部亦不得出现 `| 💬 留言 | 🤝 需求` 之类互动尾缀。
    - 二维码区块**必须是连续图片行**：`[grid]` + 6 行 `![群名|400x400](upload://…)` + `[/grid]`，中间不留空行、**不得写成 Markdown 表格**、**不得转义为 `\![…\]`**——表格会把每张图拆进单元格导致前台破图，转义会让图片退化为纯文本。
23. 禁止自创模板外模块；新增或删除层、分区须先经用户确认。累积出刊信息只写检索清单 `publication_metadata`，正文不写。
24. 最终输出 `AI雷达日报_YYYY-MM-DD.md`，封面 `AI雷达日报_YYYY-MM-DD_封面.png`，同目录。
25. 飞书推送只取精编层：今日要紧不足 3 条时，`prepare_feishu_push` 依次从金风相关、工具与做法、行业速递补足 3 条要点；跟踪与雷达不进飞书。
26. 知识库构包只收精编层为事件（Events）；跟踪与雷达候选随检索清单留档，结算时一并标记已发布，避免次日重复检索。

## V4 历史版式（≤ 2026-10-29，只读）

V4 为六个 `##` 板块（今日要紧 / 金风相关 / 工具与做法 / 行业速递 / 社区今日 / 今日一问），条目 `### 标题（M月D日）` + `**对我们：**` + `[details="🟢 来源｜…"]` 折叠块；金风相关、工具与做法各 ≤300 字，行业速递 ≤600 字。旧期次修订（`revise`）沿用原版式，不转 V5。完整 V4 规则见 CHANGELOG 4.1.0–4.2.x。

## 已废弃的 V3 结构（不得再用）

| V3 模块 | 处置 | 替代 |
|---|---|---|
| `## 一分钟速览` | 删除 | 「今日一问」+ 各条「对我们」行承担导览作用 |
| `## 今日大事件`（`🔹 **标题**`） | 删除 | `## 今日要紧`（`### 标题`） |
| 透镜标签【价值层移动】【部署边界】【能力阈值】【业务距离】 | 停用 | 飞书三群视角标签【能力复用】【数据价值】【部署门槛】（正文不再使用行动标签分层；行动标签【需行动】【可借鉴】【了解】降为可选编辑标记） |
| `## AI×能源风电` | 改名 | `## 金风相关` |
| `## Builder信号` / `## AI工作实践` | 合并 | `## 工具与做法` |
| `## 深度聚焦` | 删除（V4 无此板块；`self_check` 保留 `_deep_focus` 兼容分支用于历史期次） | 深度内容并入「今日要紧」正文 |
| 「来源：A / B」行 | 删除 | `[details]` 折叠块 |
| 行尾双空格硬换行 | 仅尾部署名行保留 | `###` 标题无需 |

## 二维码短链登记表（换图时按序对应，避免错位）

| 序 | 群名（alt） | upload:// 短链 | 来源环境 |
|---|---|---|---|
| 1 | 本地大模型用户交流群 | `upload://onqFmqGan75qPrqeDL0jLJVPdXR.png` | 生产（2026-09-22 实测 200） |
| 2 | AI智能体开发平台运营群 | `upload://jKV9fBCLNz08SUhjcSKtrmiRUuG.png` | 生产（同上） |
| 3 | 语义本体构建交流群 | `upload://yqIcRbvnSJmL6ni9hX7yPbZ1rTN.png` | 生产（同上） |
| 4 | KNS统一数据平台 | `upload://pjmuSZ09UjIJLmXl3GW496AV8OD.png` | 生产（同上） |
| 5 | 标准化助手交流群 | `upload://zki0MKU5tCanUWYmNnwU4KWKh5E.jpeg` | 测试站资源，生产发布前须重新上传换链 |
| 6 | 风衍大模型用户交流群 | `upload://dLty0KnxSeACCvXacs3O0pHKpg6.jpeg` | 生产（2026-10-21 首发实测 200） |

- 短链是**环境绑定**的：生产与测试的 upload 互不可达，序 5 在生产站会 404，生产发布前必须重传。
- 短链后缀 `.png` / `.jpeg` 不统一是 Discourse 图片管线所致，不影响渲染，勿手改。
- 第 6 张（风衍大模型）截图有效期至 **2027-10-21**，到期前须换新图并更新本表。

## 模板 ↔ 脚本常量对照（改文案须同步）

| 常量 | 位置 |
|---|---|
| `CORE_HEADINGS`（V4 适配后板块） | `scripts/self_check.py` |
| `LAYER_HEADINGS` / `TAIL_SECTIONS` / `COUNT_LIMITS` / `BODY_LIMITS` / `US_LIMIT` / `TRACK_LOOKBACK_DAYS` / `NO_ENERGY_LINE` | `scripts/layout_v5.py` |
| 卡片样式（`air-*` 选择器、`data-air-chip`） | `assets/discourse-theme/ai-radar-daily-style/common/common.scss` |
| `QR_EXPECTED_COUNT` = 6 | `scripts/self_check.py` |
| `QR_GROUP_NAMES`（6 群顺序） | `scripts/publish.py` |
| `SECTIONS`（知识库板块映射） | `scripts/build_knowledge_package.py` |
| `MAX_FEATURE_BODY_CHARS` = 300 / `MAX_DIGEST_BODY_CHARS` = 600 | `scripts/self_check.py` |
| `CONTACT_LINE` / `PRODUCER_LINE` | `scripts/self_check.py` 与 `scripts/prepare_feishu_push.py` |
| `precheck.qr_files`（6 个源文件） | `config/runtime.json` |
