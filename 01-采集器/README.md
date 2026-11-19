# AI 雷达采集器（ai-radar-collector）v0.2

金风 AI 情报站的定时采集器。每个工作日 07:30、12:30、18:30（周末 10:00）在 Mac mini 上运行一轮：
抓取信源 → 去重 → 初筛 → 调用模型打标签和写摘要 → 校验 → 以「AI 雷达」机器人账号发到论坛「信号」分类 → 读回核对 → 刷新底座帖 632 的信号雷达 → 写运行记录。

对应方案文档《金风 AI 情报站建设方案》的任务 T09、T10、T12、T13、T16、T18，以及 T07、T08 的自检。

**v0.2（2026-11-05）**：迁移日报 skill 5.2.0 的全部 24 个信源（T10，`scripts/migrate_sources.py`），新增 GitHub Release、HN、GitHub Trending、HF 热榜四种抓取方式（解析逻辑与日报一致）；新增能源政策、风电同行、安全、算力、集团内部模块的信源（大多待实测，默认关闭的需要调选择器）；底座帖可信度改用首页组件认的 `g` / `y` / `r`；雷达条目新增 `relevance`，底座帖新增 `radar_next_at`、`question_list`，配合首页组件 v0.3；信号话题正文用 `[wrap=ais-*]` 分块，配合主题组件「AI 情报站话题样式」。

## 目录

```
collector/            代码
  __main__.py         命令行入口
  pipeline.py         一轮采集的 8 个步骤
  sources/fetchers.py 三种抓取方式：rss、listpage、discourse
  tagger.py           调用模型打标签、写摘要，并按白名单校验
  credibility.py      可信度规则（不交给模型判断）
  dedup.py            URL 规范化、signal_id、event_key 校验、屏蔽域名
  discourse.py        论坛接口 + 模拟论坛
  render.py           信号话题正文、标签、底座帖读写
  store.py            本地 SQLite 索引（只是缓存）
  alerts.py           飞书告警
config/               配置：settings / sources / questions / tags / link-reachability
launchd/              定时任务模板
scripts/              安装、卸载定时任务
state/                本地索引（自动生成）
logs/                 日志，保留 30 天（自动生成）
tests/                测试与样例数据
```

## 在 Mac mini 上部署

### 1. 装环境

```bash
cd ~/ai-radar-collector
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q          # 应全部通过
```

### 2. 存密钥（存进钥匙串，不写进文件）

每条命令会提示你粘贴密钥，屏幕上不显示：

```bash
security add-generic-password -s ai-radar-collector -a discourse-api-key -w
security add-generic-password -s ai-radar-collector -a llm-api-key -w
security add-generic-password -s ai-radar-collector -a feishu-webhook -w
```

- `discourse-api-key`：T06 在论坛后台为 `ai-radar` 生成的 API 密钥。
- `llm-api-key`：IT 指定的模型接口密钥。
- `feishu-webhook`：飞书群里「自定义机器人」的 webhook 地址。建议建一个只有你自己的群；机器人开了签名校验的话，再存一条 `feishu-sign`，并在 settings.yaml 里取消 `sign_secret` 的注释。

第一次从定时任务里读钥匙串时，macOS 可能弹窗询问，选「始终允许」。

### 3. 填配置 `config/settings.yaml`

| 项 | 填什么 | 来自 |
| --- | --- | --- |
| `discourse.signal_category_id` | 「信号」分类的数字 ID | T05 |
| `llm.base_url`、`llm.model` | 模型接口地址（OpenAI 兼容）和模型名 | T01，IT 指定 |
| `discourse.min_title_chars` | 与论坛站点设置 `min_topic_title_length` 一致 | 论坛后台查看 |

然后检查：

```bash
.venv/bin/python -m collector check-config
```

它会列出需要在论坛上先建好的标签（领域、问题、可信度、审核状态、主题），把这些标签一次性建好。

### 4. 自检（第 1 周关卡，T07 + T08）

```bash
.venv/bin/python -m collector selftest
```

依次检查：读取信号分类；发一条测试帖；同一个 external_id 再发一次必须被拒；读回正文和标签；读取底座帖（只读不改）。全部「通过」就是第 1 周关卡达成。测试帖在后台手动删除。

### 5. 试运行（不写论坛）

```bash
.venv/bin/python -m collector run --dry-run
```

真实抓取、真实调用模型，但发帖写到模拟论坛，每条信号渲染后的正文存到 `logs/dryrun/<时间>/`，打开看标题、摘要、标签是否靠谱。`--fake-llm` 可以不调用模型，只检查抓取流程。

### 6. 装定时任务

```bash
bash scripts/install_launchd.sh
```

装三个任务：每轮采集、每天 06:00 的 models 快照（T24 加模型行情信源前它什么也不做）、每周一 09:00 的运行周报。一键停用：`bash scripts/uninstall_launchd.sh`。

`round.schedule` 必须和 `launchd/com.goldwind.ai-radar.round.plist` 的时刻一致：首页用它判断“采集是否暂停”。

launchd 的用户级任务只在用户登录时运行：Mac mini 要设成开机自动登录这个账号，并关闭自动睡眠、打开「断电后自动启动」。

## 日常

```bash
.venv/bin/python -m collector status         # 最近几轮、待人工条数、连续失败的信源
.venv/bin/python -m collector weekly-report  # 手动发一次周报
tail -f logs/collector-$(date +%Y%m%d).log
```

- **待人工**：模型输出不合格（领域不在白名单、event_key 是编号、标题长度不对、含占位符）或发帖失败的条目，存在 `state/index.sqlite` 的 pending 表，不会发出去。
- **告警**：整轮失败（连不上论坛、模型接口全挂、所有信源都失败、读回不一致）立即发飞书；单个信源连续 3 轮失败发一次。
- **第 2 周**：`feed.enabled` 保持 `false`，信号只进分类，不上首页。连续 5 个工作日成功率 ≥ 90%、抽查 20 条无明显错误（T17）后改成 `true`。

## 安全设计

- 可信度按来源规则计算：监管机构原文为 🟢；当事方官网单独发布为 🟡，加独立媒体互证为 🟢；两家以上媒体为 🟡；单一非官方来源为 🔴。
- 读回不一致立刻停止本轮发帖并告警。
- 底座帖只改采集器负责的字段（`radar`、`radar_updated_at`、`radar_next_at`、`radar_total`、`question_list`），简报、焦点、精编等其他字段原样保留，`schema` 不改，现有首页组件 v0.2 照常显示。
- 公司网络打不开的域名（`config/link-reachability.yaml`）默认不收录。
- 只读公开网页，只往「信号」分类写；吊销 `ai-radar` 的 API 密钥即可立即停止所有写入。

## 还没做的（按任务拆分）

- **信源实测**：`sources.yaml` 里 `verified: false` 的信源都还没在公司网络实测，第一次 `run --dry-run` 后按日志把能用的改成 `verified: true`，打不开的关掉或换地址。日报原有的 12 个 `page` 类官方页仍由每日精选人工核查，采集器跳过。
- **T15**：能源政策四个站点的列表页选择器要对着真实页面调，调好前保持 `enabled: false`。
- **T22、T24**：风电同行、模型行情、安全、算力模块。
- **T23**：集团内部模块按保密口径启用。
- 底座帖 `schema` 保持 `air-pulse/1`（首页组件 v0.2、v0.3 都只认它），新字段都是可选的，v0.2 组件会忽略它们。

## 需要在 Mac mini 上实测确认的假设

1. 论坛对 `ai-radar` 这类非管理员账号的发帖频率和每日发帖上限。默认两次发帖间隔 16 秒，遇到 429 会等待重试，仍然失败的条目留到下一轮。每天 3 轮、每轮最多 30 条，如果超过论坛的每日上限，需要调整站点设置或降低 `max_posts_per_round`。
2. 话题 `external_id` 字段可用（selftest 会验证）。
3. 底座帖 632 的数据块是 ```` ```json ```` 代码块，或者整个正文就是 JSON。两种写法都支持；其他写法会报错且不修改。
4. 论坛默认不允许重复标题。两家来源的标题完全相同时，后一条会因 422 进入「待人工」。如果这种情况多，可在站点设置里允许重复标题，或者让采集器给标题加上日期。
