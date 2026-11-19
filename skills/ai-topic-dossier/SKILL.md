---
name: ai-topic-dossier
description: 金风 AI 情报站《主题档案》周更：每周五按主题汇总采集器写入论坛「信号」分类的信号，编辑写出当前判断、本周变化、下个观察点，更新论坛档案帖和首页「主题追踪」区块，并维护主题标签白名单。用户说「更新主题档案」「这周的主题判断」「主题追踪」「加一个主题」「主题标签白名单」时使用。不用于日报、月报和技术雷达。
version: 1.0.0
---

# 主题档案（周更）

## 它做什么

情报站的信号按主题标签（如 `topic-power-forecast`）归档。本 skill 每周五为每个主题做一件事：根据近 7 天的新信号，更新「当前判断 / 本周变化 / 下个观察点」三行，连同近 30 天的信号时间线写进论坛档案帖《主题档案｜主题名》，再把判断摘要写进首页底座帖的 `topics` 字段。首页「主题追踪」区块（组件 v0.3）读这个字段显示。

## 底线

- **判断只建立在信号上。** 每条判断至少引用 1 条本周信号（`evidence` 填信号话题 ID）；本周没有新信号的主题，「本周变化」写「本周无新信号。」，判断可以沿用上期。不引入信号库里没有的事实。
- **标明不确定性。** 引用的信号是 🔴 单一来源或「未审」时，判断里要写出「据单一来源」「待核实」一类限定。
- **区分事实和判断。** 「当前判断」是编辑观点，要能被下周的信号推翻；推翻时在「本周变化」里写明。
- **外部写入要确认。** `publish` 会新建或修改论坛档案帖、修改首页底座帖，必须先给用户看 `render` 的预览，用户确认后才加 `--confirm`。
- **只改自己的字段。** 写底座帖时只改 `topics`，其余字段（采集器的 `radar`、日报的简报焦点、技术雷达的 `stance`）原样保留；读不到或格式不对就停。

## 流程

```bash
S=<本 skill 目录>/scripts; D=YYYY-MM-DD   # 本周五
python3 $S/dossier.py collect  --week-end $D --env-file <.env>          # 读论坛 → 素材 JSON（只读）
python3 $S/dossier.py template --material <work_dir>/主题档案_${D}_素材.json   # 生成判断模板
#   编辑：逐个主题填写 <work_dir>/主题档案_${D}_判断.json 的 judge / change / next / evidence
python3 $S/dossier.py render   --material ... --judgments ...            # 校验 + 生成预览（只写本地）
#   把预览给用户看，确认后：
python3 $S/dossier.py publish  --material ... --judgments ... --env-file <.env> --confirm
```

### 写判断的要求

| 字段 | 长度 | 写什么 |
| --- | --- | --- |
| `judge` 当前判断 | ≤ 160 字 | 这个主题现在走到哪一步、对金风意味着什么。一到两句，能被证伪 |
| `change` 本周变化 | ≤ 120 字 | 和上期判断比，本周新信号带来了什么变化；没有就写「本周无新信号。」 |
| `next` 下个观察点 | ≤ 80 字 | 下周或下个月具体看什么事件、数据或发布，看到了就能更新判断 |
| `evidence` | ≥ 1 条（本周有信号时） | 支撑判断的信号话题 ID，必须在近 30 天信号里 |

模板里以 `_` 开头的字段（本周信号清单、上期判断）是给编辑参考的，发布时忽略。

`render` 会拦下这些问题：字段为空或超长、含占位符（TODO、待补、[示例]）、本周有信号却没引用、本周有信号却写「本周无新信号」、引用了不在近 30 天的信号、有主题漏填。

## 主题白名单

主题清单在 `config/topics.json`。`tag` 必须和采集器 `config/tags.yaml` 的 `topics` 一致，采集器只会给信号打白名单里的主题标签。

- **加主题**：在 `topics.json` 加一项（`name`、`tag` 用 `topic-` 开头的小写英文、`questions`、`on_home`），运行 `dossier.py whitelist`，把输出贴进采集器的 `tags.yaml`，并在论坛上建好这个标签。
- **首页显示**：`on_home: true` 的主题按顺序上首页，最多 `max_topics_on_home`（默认 5）个。
- **停用主题**：`on_home` 改为 false。档案帖保留，不删除。
- 判断主题是否该加：同一类信号连续两周每周 ≥ 3 条，且对应集团问题清单里的某个问题。

## 配置

`config/topics.json`：

- `forum.signal_category_id`：「信号」分类 ID（和采集器一致），首次使用前必须填。
- `forum.dossier_category_id`：档案帖所在分类，默认 15（趋势与情报）。
- `forum.feed_topic_id`：首页底座帖，默认 632。
- `dossier_topic_id`：首次发布后自动回填，不要手改。

凭据与 ai-radar-daily 共用 `.env`（`DISCOURSE_URL`、`DISCOURSE_API_KEY`、`DISCOURSE_API_USER`）。账号要能在档案分类发帖、编辑底座帖。

## 失败处理

- `collect` 读不到论坛：如实告诉用户，不用旧素材凑数。
- `publish` 回读不一致：结果里该主题标 `READBACK_MISMATCH`，请用户打开该档案帖核对；底座帖写入失败时整份报错，首页保持上周内容。
- 中途失败可以重跑：已建好的档案帖话题 ID 已经回填，不会重复建帖。
