# gw-ai-pulse · 金风 AI 情报站（全新环境）

> **这是一个从零重建的干净运行环境。** 与旧 WorkBuddy 环境、与 `GWAIPulse/uat-runtime` **完全隔离**。
> 目标：每日自动采集 AI 信息 → 发布到 AI 社区（`https://aicopilot.goldwind.com.cn:3211`）→
> 首页「AI 雷达」区块按既定样式聚合展示（读话题 632 第 1 楼的 `air-pulse/1` JSON）。

建立时间：2026-11-13
旧自动化 `automation-1787192486737` 已停止（用户确认）。

---

## 目录结构

```
gw-ai-pulse/
├── skills/                          ← skill 代码本体（运行期只读）
│   ├── ai-radar-daily/              （日报：检索 → 成稿 → 门禁 → 发布 → 首页）
│   ├── ai-tech-radar/               （技术雷达：写底座帖 stance）
│   └── ai-topic-dossier/            （主题档案：写底座帖 topics）
├── 01-采集器/                        ← 采集器自包含副本（含 config / tests / launchd 模板）
├── work/                            ← ★ 唯一数据目录
│   ├── ai-radar-daily/.env          ← 凭据（600）
│   ├── ai-tech-radar/
│   └── ai-topic-dossier/
├── .venv/                           ← Python 3.12.13
├── rebind_paths.py                  ← 路径重绑定（plan/apply/verify）
├── _rebind-backup/<时间戳>/          ← 每次路径改写的原件 + manifest.json
├── _archive/                        ← 迁移前的源环境快照（tar.gz）
├── launchd/                         ← 定时任务 plist（Label: com.goldwind.gw-pulse.*）
├── logs/                            ← 定时任务日志
└── README.md
```

## 四条硬规则

1. **只有 `work/` 会写数据**；`skills/` 与 `01-采集器/` 是代码，运行期只读。
2. **不读** `~/.workbuddy/skills/`、**不读** `~/WorkBuddy/`、**不读** `~/Desktop/GWAIPulse/uat-runtime/`。
3. 密钥只走 `work/ai-radar-daily/.env`（600 权限）+ macOS 钥匙串 `service=gw-ai-pulse`。
4. 话题 632 只由两个进程写，各写各的字段、互不覆盖：
   - 采集器 → `radar` / `radar_updated_at` / `radar_next_at` / `radar_total` / `question_list`
   - 日报 → `brief` / `lead` / `picks` / `curated` / `tracking` / `community` / `question` / `mainline` 等
   - 主题档案 → `topics`；技术雷达 → `stance`
   - 日报侧 `homepage_feed.merge_with_live()` 保证「底座帖有 `radar_updated_at` 就沿用采集器的 radar」。

---

## 正式环境绑定值

| 项 | 值 | 所在文件 |
|---|---|---|
| 论坛 base_url | `https://aicopilot.goldwind.com.cn:3211` | `ai-radar-daily/config/runtime.json` · `01-采集器/config/settings.yaml` |
| 发布分类（趋势与情报） | **15** | `runtime.json` → `forum.category_id` |
| 「信号」分类 | **20** | `runtime.json` → `signal_library.category_id` · 采集器 `signal_category_id` |
| 首页底座帖 | **632** | `runtime.json` → `homepage_feed.topic_id` · 采集器 `feed.topic_id` |
| API 账号 | `U8570`（管理员，待换 `ai-radar` 机器人） | `.env` · 采集器 `api_username` |
| work_dir | `.../work/ai-radar-daily` | `runtime.json` → `work_dir` |
| env_file | `.../work/ai-radar-daily/.env` | `runtime.json` → `paths.env_file` |

> 与 UAT 环境的差异只有四处：base_url（`10.12.3.232:8088` → 生产）、分类 17/16 → **15/20**、
> 底座帖 208 → **632**。其余口径一致。

---

## 日常命令

```bash
ROOT=/Users/sr_jm/Desktop/GlodWind/gw-ai-pulse
PY=$ROOT/.venv/bin/python

# 环境预检
$PY $ROOT/skills/ai-radar-daily/scripts/run.py precheck \
  --report-date "$(date +%F)" --env-file $ROOT/work/ai-radar-daily/.env

# 首页数据（只写本地，不联网）
$PY $ROOT/skills/ai-radar-daily/scripts/run.py feed --report-date "$(date +%F)"

# 首页数据（写入话题 632）
$PY $ROOT/skills/ai-radar-daily/scripts/run.py feed --report-date "$(date +%F)" --confirm

# 采集器（试运行，不出网）
$PY -m collector run --dry-run --fake-llm   # 需在 01-采集器/ 下执行

# 测试
$PY -m pytest $ROOT/skills/ai-radar-daily/tests -q
```

---

## 当前状态

- [x] **P0** 备份 / 骨架 / 克隆代码 / 路径重绑定 / 断链校验
- [x] **P1** 凭据与预检（`.env` 600、钥匙串 `service=gw-ai-pulse`、precheck 无阻断）
- [ ] **P2** 打通「采集器 → 632 → 首页」
- [ ] **P3** 切自动化（launchd `com.goldwind.gw-pulse.*`）

## 与旧环境的差异（刻意为之，非缺陷）

| 项 | 旧环境 | gw-ai-pulse | 原因 |
|---|---|---|---|
| `feishu_push.enabled` | true | **false** | 只发 AI 社区论坛，不再推生产群 |
| 阶段 5 飞书推送 | 执行 | **SKIPPED** | 同上；代码保留，置 true 即恢复 |
| 阶段 8 前置 | 要求飞书全 SENT | **不再校验** | 否则每次归档都被卡死在 SYNC_PENDING |
| `knowledge_sync.enabled` | true | **false** | 知识库降级为「离线归档」，不参与主链路 |
| precheck 二维码/群配置/群画像 | blocker | **warn** | 推送关闭后不再是发布前置 |
| 节假日日历 | `~/.workbuddy/skills/calendar-cn` | **`assets/calendar-cn/`（内嵌）** | 断链，避免静默退化为「仅按星期」 |
| 采集器钥匙串服务 | `ai-radar-collector` | **`gw-ai-pulse`** | 两套并存，互不影响，便于回滚 |

> 门禁代码**没有删**，只是默认关闭：`runtime.json` 的 `feishu_push.enabled=true`
> 或环境变量 `AI_RADAR_FEISHU_PUSH=1` 即可恢复旧行为。相应的 3 个门禁测试已改为
> `patch.object(run, "_feishu_push_enabled", return_value=True)`，改动逻辑等价性仍被覆盖。

## 排障速查（2026-11-12 事故复盘）

| 现象 | 先查什么 | 结论 |
|---|---|---|
| 首页没更新 | 直接开 `/raw/632/1` 看 `issue_date` | 旧的 → 服务端没写；新的但页面没变 → 前端缓存 |
| 页面内容像旧的 | `sessionStorage` 里 `ap-home:c:feed:632/1`（5 分钟 TTL）与 `ap-home:blocked_until`（网关熔断 10 分钟） | 强制刷新或新开标签页 |
| 接口返回 418「访问被拦截」 | 响应头 `X-Hwwaf-Attack-Id` | CloudWAF 封出口 IP，等待冷却，与密钥无关 |

## 排障速查（2026-11-19 事故复盘：采集卡死 + 首页停更）

现象：08:00 那一轮跑了 43 分钟没退出，首页停在昨天；`runs` 表那一行 `report` 为空；
日志里只有「按计划时刻触发」，之后什么都没有。完整复盘见
[docs/2026-11-19-采集卡死与首页停更.md](docs/2026-11-19-采集卡死与首页停更.md)。

| 现象 | 先查什么 | 怎么看 |
|---|---|---|
| 首页没更新 | `python -m collector status` 最后一行 | 看到「论坛限速 429×N」→ 撞了 admin API key 限额；「模型故障 N 条」→ 模型接口超时 |
| 首页没更新且某轮几十分钟没结束 | `pgrep -fl 'collector run'` | 有进程就 `sample <pid> 1`：停在 `time.sleep` 且无 socket = 睡眠重试堆叠 |
| 两轮连续被跳过 | 上一轮是否仍占着 `state/run.lock` | `lsof state/run.lock`；锁随进程退出自动释放，`pkill -f 'collector run'` 即可 |
| 报告 fatal 出现「本轮超时被中止」 | 触发了 20 分钟看门狗 | 那轮不发帖、不刷底座帖，等下一轮；12:00 / 16:00 不受影响 |
| 待人工积压暴涨 | `select reason,count(*) from pending group by reason` | 模型超时不再入队（记 `model_failed`）；剩下的多是「标题疑似重复」 |

三个关键数字（都在 `config/settings.yaml`）：

```yaml
discourse:
  min_request_interval: 1.0       # 相邻请求最小间隔（秒）
  max_requests_per_minute: 40     # 每分钟上限，论坛 admin API key 有硬限额
round:
  max_seconds: 1200               # 本轮硬超时，超了强制收尾并放锁
```
## 首页内容取舍：两道闸（2026-11-19）

内容「为什么进来的」由入口闸决定，不再事后补救。两道闸都在 `01-采集器/config/settings.yaml`，
都能一键回退，细节见 [docs/2026-11-19-P0P1修复记录.md](docs/2026-11-19-P0P1修复记录.md)。

```yaml
keep_bypass:            # 第一遍被 keep=false 的一手来源，再问一次（名单=人工判断谁是一手）
  enabled: true
  sources: ["OpenAI / Codex", "国家网信办", "GWEC 全球风能理事会", ...]
feed:
  brief_max_per_source: 1   # 01/02 每站最多几条；0=不限（旧行为）
  picks_max_per_source: 2   # 01 四格每站最多几条；0=不限
```

限额按**站点**分桶而不是按源名：IT之家 · AI 与 IT之家 · 算力都是 ithome.com，
DSH 插件生态 / DSH 主仓库 / GitHub Trending 都是 github.com，按源名限等于给同一个站开好几张额度。

- 补判只放宽 `keep`，不放宽 `relevance`：`round.min_relevance_to_post` 仍是发帖门槛。
- 日志里看到「白名单补判：N 条」就是这道闸生效；想确认救回来的是哪几条，看 `runs` 表 report 的 `keep_bypassed`。

## 今日焦点：单独成篇（2026-11-19）

02 焦点原先就是 01 的第一条，素材只有 RSS 摘要（实测 105 字），前端 `ap-lead` 也只画一段，
结构上不可能写出第二句话。现在焦点单独处理：每轮最多挑一条信号，抓原文正文 → 模型抽
「事实 + 原文引文」→ **代码逐字校验引文** → 按固定骨架成稿 → 发一篇长文话题；首页 02 只放
导语 + 3 条要点，标题链到话题。方案与验收标准见
[docs/2026-11-19-今日焦点长文方案.md](docs/2026-11-19-今日焦点长文方案.md)。

```yaml
focus:
  enabled: true
  recency_hours: 24       # 只从 24 小时内的事件里挑
  claims_min: 4           # 逐字校验通过的事实少于 4 条 → 出「简讯版」
  degraded_claims_min: 2  # 少于 2 条 → 本轮不出焦点（绝不让模型硬写）
  min_body_chars: 200     # 抽出来的正文太短 = 抽取失败，换下一条候选/下一个来源
```

三条规矩值得记住：

- **规则 a：没有新焦点就不换题**。选题只从本轮新信号里挑，挑不出来就不动 02，上一轮的焦点留在页面上。
- **事实必须能逐字回到原文**。`verify_claims()` 拿引文去正文里做逐字比对（全角半角、空白都归一），
  对不上的直接删——这是「不能是一句话」和「不能编」的调和点。
- **发帖成功才占事件键**。焦点事件键写 `focus_history`（state 表）是在发帖成功之后，
  否则 429 一次就把这个事件 7 天内全锁死。

实操工具：

```bash
cd 01-采集器
../.venv/bin/python scripts/focus_pilot.py 866                       # 只生成、打印成稿，不动论坛
../.venv/bin/python scripts/focus_pilot.py 866 --apply               # 写回该话题 + 加 focus 标签 + 刷新 632
../.venv/bin/python scripts/focus_pilot.py 866 --apply \
  --source-url https://www.nea.gov.cn/... --source-name 国家能源局     # 顺带补一条可抓的官方原文
```

已知限制：**新华网正文是 JS 渲染的**（HTML 里没有 p 标签），直接抓只能拿到导航栏；openai.com
对脚本请求直接 403。所以 `focus.candidate_urls()` 会按来源顺序逐个试，抓到正文的那条来源自动排到
「来源」首位；一条信号所有来源都抓不到，这条就不做焦点。

再有一层自学习：每轮把「哪个站点抓得到正文」记进 state 表（`focus_host_stats`），
选题时**可抓性排在一手/相关度之前**——抓不到正文就出不了稿，不这么排每轮都会在同一批 JS 渲染页上
白花 3 次超时。站点改版后抓到一次，排序自动恢复。这条其实也是实测逼出来的：第一次 dry-run 三条
候选全部抓不到（新华网 142 字导航栏、openai.com 403、openai.com 403），结果是「本轮不出焦点」。

