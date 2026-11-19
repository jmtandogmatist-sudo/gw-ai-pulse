# V3.2 证据标准

## 1. 信源分级 T0-T4

| 级别 | 定义 | 允许用途 |
|---|---|---|
| T0 | 官方一手材料：企业官网/博客、官方开发文档、GitHub Release、模型卡、交易所或标准发布页 | 关键事实首选；重大条目可单一 T0 入选，但必须显式标注“单一官方来源” |
| T1 | 央媒与同等级一手采访/报道 | 交叉验证主体、日期、动作；不能覆盖原文未支持的技术细节 |
| T2 | 权威财经科技媒体及可靠通讯社 | 交叉验证、交易和行业背景；数字仍优先回到 T0 |
| T3 | 行业垂直媒体、研究机构二手解读 | 补充背景或发现线索；关键 claim 需更强来源 |
| T4 | 聚合号、自媒体、社交帖子、搜索摘要 | 仅作线索，不能作为正文关键事实的直接证据 |

默认要求两个独立的 T0-T2 来源。单一 T0 例外仅适用于官方正式发布且正文严格不超出官方材料的重大条目。两个转载同一稿件不构成双源。

## 2. Claim-evidence receipt schema

每个关键 claim 单独建 receipt，不以“整条新闻有两个链接”替代事实绑定。

```json
{
  "claim_id": "C-001",
  "item_id": "NEWS-01",
  "claim_text": "可独立判断真假的最小事实陈述",
  "claim_type": "date|actor|action|number|specification|status|quote",  "materiality": "critical|supporting",
  "event_date": "YYYY-MM-DD",
  "date_basis": "发生|签发|生效|报道",
  "sources": [
    {
      "source_id": "S-001",
      "tier": "T0",
      "publisher": "发布主体",
      "title": "页面标题",
      "url": "https://...",
      "published_at": "ISO-8601 或 null",
      "accessed_at": "ISO-8601",
      "locator": "章节、段落、表格或页面位置",
      "verbatim": "直接支撑 claim 的原文摘录",
      "supports": "full|partial|contradicts",
      "independence_group": "origin-001"
    }
  ],
  "verification": {
    "status": "PASS|FAIL|UNRESOLVED",
    "independent_source_count": 1,
    "single_official_exception": true,
    "notes": "证据边界、冲突或口径说明"
  }
}
```

必填：`claim_id`、`claim_text`、`claim_type`、`materiality`、至少一个 source、`tier`、`url`、`locator`、`verbatim`、`supports`、`verification.status`。

枚举注意：`claim_type` 无 `policy` 值——政策发布/文件印发类 claim 统一用 `action`（2026-09-07 自查 FAIL 教训）；`claim_type` 无 `report` 值——数据/统计类 claim 用 `number`，事件/行动类用 `action`（2026-09-29 自查 FAIL 教训）。`materiality` 只有 `critical` 和 `supporting` 两值，禁止使用 `important`/`moderate`（2026-09-29 自查 FAIL 教训）。

## 3. 事实与来源精准绑定

1. 把标题和正文拆成最小 claim：主体、动作、日期、数字、版本、状态分别验证。
2. 每个来源只支持其原文明确覆盖的 claim；不得用公司首页或无关新闻作“官方来源”。
3. 为转述链标注同一 `independence_group`；来自同一新闻稿的多个转载只计一个来源。
4. 厂商 Benchmark 标“厂商自报”；个人效率数据标“个人自述”；传闻标明传闻且不得作为编辑部头条的确定事实。
5. 来源冲突时保留冲突记录，优先原始材料；无法解决则删除争议 claim 或淘汰整条。
6. 搜索结果 snippet 只能定位页面，receipt 必须来自打开后的页面内容。
7. 日期必须记录 `date_basis`，不得用报道日期替代事件日期。
8. 来源必须「浏览器直开可读」：SPA/纯前端渲染页面（curl 或浏览器直接打开只有站点框架、无正文，仅搜索引擎可索引）不得作为正文来源，即便其内容真实（2026-09-07 山西晚报教训）。
9. `publisher` 必须与 URL 域名归属一致，写入前核对域名；禁止张冠李戴（如 gigazine.net 属 GIGAZINE，不得标注为 GIZMODO）。
10. 当事人/机构负责人在权威媒体（央媒、头部媒体）的**署名原文**可作为准官方一手来源（independence_group 单列，按发布媒体归组）；机构官网未发布时优先寻找署名原文而非媒体转述（2026-09-07 南方周末杨苹署名原文案例）。

## 4. 空证据 fail closed

以下任一情况将 receipt 置为 `FAIL` 或 `UNRESOLVED`：

- `sources=[]`、URL 为空、原文摘录为空或页面不可访问。
- 来源只部分支持，却把 claim 写成更强结论。
- 关键数字找不到原始出处。
- 两个所谓独立来源属于同一转载链。
- 只有 T4 或无法辨别发布主体。
- 事件日期无法确定且标题需要日期。

`critical` claim 未 PASS 时不得进入正文。`supporting` claim 未 PASS 时删除该句，不得靠“可能”“据悉”保留。宁可少报，不可用占位证据。

## 5. 语义复核输出

语义复核至少记录：正文 SHA-256、每条 item 的 receipt IDs、独立来源数、日期结论、冲突、单一官方例外、最终 PASS/FAIL。修改正文后旧复核立即失效。

**深度聚焦独立节点（V3.9.4，2026-09-29 教训）**：`semantic_review` 顶层须有独立 `deep_focus` 节点（不能复用 `items` 数组内的 receipt），结构：

```json
"deep_focus": {
  "item_id": "FOCUS-01",
  "title": "...",
  "source_urls": ["https://官方URL-1", "https://官方URL-2"],
  "facts_checked": "可验证的关键事实列表",
  "evidence_receipts": [ /* 独立 receipts，来源 tier 全部 T0 */ ],
  "status": "PASS",
  "official_sources_only": true,
  "link_status_verified": true,
  "reviewed_at": "ISO-8601"
}
```

- `source_urls` 必须与正文深度聚焦板块的来源 URL 严格一致（self_check 做集合比对）
- `evidence_receipts` 内的所有 source `tier` 必须为 `T0`（官方一手）
- `official_sources_only` 和 `link_status_verified` 必须显式为 `true`（布尔值，不是字符串）
- 深度聚焦的 receipt 与 items 中的 receipt 分开存放，claim_id 前缀 `FOCUS-XX-XX`

## 6. 确定性脚本与语义验证边界

`self_check.py` 可验证：

- 文件与 JSON 是否存在、可解析。
- 模板章节、条数、固定格式、来源链接形态、日期格式和部分时效规则。
- 检索清单字段、官方源检查记录、召回状态、图片资源和跨期重复线索。
- 语义复核是否声明 PASS、自查报告状态以及正文 SHA-256。

`self_check.py` 不能可靠验证：

- 页面原文是否真的支持 claim。
- 来源是否真正独立、是否同一稿件转载。
- 数字口径、发布日期语义和事件日期是否被正确理解。
- 是否漏掉搜索未召回的重大事件。
- 编辑判断是否合理、透镜是否适用、N/A 是否更诚实。
- 论坛或知识库远端内容是否与本地语义一致。

因此顺序必须是“人工/代理语义核验 → 结构化复核记录 → 确定性脚本”。脚本 PASS 不是事实正确性的替代品。


## 语义复核字段速查（V5.1.0，11-02 实跑踩过的坑）

- 回执 `claim_type` 只能取 `date / actor / action / number / specification / status / quote`；`materiality` 只能取 `critical / supporting`。写成 attribution、scope、major 会整条被判无效。
- 条目只有一个官方来源时，**条目级**要写 `"independent_sources": false, "single_official": true`（不是只在回执里写 `single_official_exception`）。
- 回执的来源必须是正文该条目已列出的链接，且正文每个链接至少被一条回执覆盖；不得为凑数把不支持该结论的来源塞进回执（11-02 曾为“不含 Siri AI”误挂新浪，已更正为只引用 IT之家）。
- 条目 `source_urls` 与正文来源集合须严格相等；换链接时检索清单、语义复核、正文三处同步改，用 `build` 脚本统一生成最稳。
- 检索清单候选的 `event_key` 必须按内容生成；`signal-1`、`track-1`、`news-01` 这类逐日编号会跨日撞号，`run.py dedup` 与 `preflight` 都会拦截。
