# 首页 05 社区改版（v0.4.7 → v0.4.8）

- 日期：2026-11-19
- 基线：线上「AI 雷达首页 v0.4.7」，theme id 19，2026-11-19 01:07 抓取
- 新版本：AI 雷达首页 v0.4.8
- 发布包：dist/discourse-ai-雷达首页-v0.4.8.zip
- 发布包 sha256：91df56a8043621958c727b95d80b6c080671d498913d15eee33d90606bb05af5
- 本地工作副本：work/theme19/
- 线上原件备份：_rebind-backup/20261119-theme19-v047/

## 1. 改了什么

| 05 位置 | 改前 | 改后 |
|---|---|---|
| 左列 | 社区新帖（5 条，feed.community）→ 今日一问 | 社区热帖（5 条，全站近 30 天）→ 今日一问 |
| 右列 | 社区热帖（5 条） | 赛事培训（分类 17，按发帖时间倒序） |

- 删除「社区新帖」列表和「全部社区帖 →」，不再渲染 feed.community。
- 「今日一问」保留在左列下方，继续带「我也说说 →」。
- 「社区热帖」整体左移，标题、数据源、条数不变。
- 右列新增「赛事培训」，默认 5 条，标题后小字「最新发布」，底部「全部赛事培训 →」。
- 05 编号不变；06 栏目更新、07 按主题看等区块不动。

## 2. 文件清单

| Discourse 后台文件 | 本地文件 |
|---|---|
| discourse/components/ai-pulse-home.gjs | work/theme19/javascripts/discourse/components/ai-pulse-home.gjs |
| discourse/lib/ai-pulse-feed.js | work/theme19/javascripts/discourse/lib/ai-pulse-feed.js |
| settings.yml | work/theme19/settings.yml |
| common/common.scss | work/theme19/common/common.scss |
| about.json（仅版本号） | work/theme19/about.json |

discourse/api-initializers/ai-pulse-home.js 未改；about.json 只把版本号从 v0.4.7 改为 v0.4.8，手工粘贴时可不动。

MANIFEST.json 记录工作副本各文件的 sha256；线上原件的哈希在备份目录 manifest.json。

## 3. 关键实现

### 3.1 赛事培训排序

分类 17（赛事培训）里有 10 个话题被置顶。直接请求默认列表会把置顶帖排前面，737 反而看不到。所以必须显式带 order=created：

    /c/17/l/latest.json?order=created

前端调用：

    loadCategoryTopics(eventCol.id, {
      limit: 5,
      order: "created",
      excludeIds: excluded(),
    })

loadCategoryTopics 已支持 order 参数，并把 order 写进缓存 key；其余按活动时间取分类的调用不受影响。

### 3.2 去重顺序

加载顺序：底座帖 → 赛事培训 → 右侧栏 → 社区热帖 → 06 栏目更新 → 07 主题。

- 赛事培训先加载并 mark；
- 社区热帖排除赛事培训已出现的话题，避免 737 左右两列重复；
- 06 栏目更新再排除 05 已出现的话题。

按 2026-11-19 的实时数据，两列应为：

- 赛事培训：737、608、496、458、421
- 社区热帖：789、635、711、644、616

### 3.3 feed.community 不再参与首页

原先把 feed.community 的话题加入 seen，导致它们不会出现在热帖和 06；现在这段逻辑已删除，这些帖子重新参与后面区块的排序。

采集器仍会写 community 字段，前端只是不再展示；本次不改采集器和后端。

## 4. 新增设置项

    event_category: 17:赛事培训
    event_count: 5
    event_note: 最新发布

说明：

- event_category 留空则不显示赛事培训；
- event_count 为 1-10，默认 5；
- event_note 留空则标题后不显示小字；
- 已删除不再使用的 community_category、community_note。

## 5. 部署步骤

线上主题写接口是 404（见 docs/前端组件待应用补丁.md），需要手工操作。

### 方案 A：导入新版本包（推荐）

1. 管理 → 自定义 → 主题 → 组件 → 安装 → 从文件导入 dist/discourse-ai-雷达首页-v0.4.8.zip。
2. 导入后确认新组件「AI 雷达首页 v0.4.8」已启用，并加入当前主题的组件列表。
3. 停用或删除旧的「AI 雷达首页 v0.4.7」组件，否则首页会出现两份 05。
4. 强制刷新前台页面。

### 方案 B：在原组件上手工粘贴

1. 登录论坛管理后台。
2. 自定义 → 主题 → 组件 → 找到「AI 雷达首页 v0.4.7」→ 编辑代码。
3. 逐个打开下列文件，整份替换为 work/theme19/ 下对应文件的内容：
   - discourse/components/ai-pulse-home.gjs
   - discourse/lib/ai-pulse-feed.js
   - settings.yml
   - common/common.scss
4. 保存后回前台，强制刷新（Cmd+Shift+R）；必要时清掉 sessionStorage 里 ap-home: 开头的缓存。
5. 如果后台「设置」页仍显示旧的 community_category/community_note，刷新后应消失；新设置项按 YAML 默认值生效。

## 6. 验收

### 6.1 接口

    curl -skL 'https://aicopilot.goldwind.com.cn:3211/c/17/l/latest.json?order=created&per_page=6'

第一条应为 737（这十支队伍，把 AI 塞进了金风的真业务），再是 608、496、458、421。

### 6.2 页面

- [ ] 05 标题仍是 05 COMMUNITY；
- [ ] 不再出现「社区新帖」「全部社区帖 →」；
- [ ] 左列是「社区热帖」5 条，下方「今日一问」和「我也说说 →」正常；
- [ ] 右列是「赛事培训」5 条，标题后显示「最新发布」，底部「全部赛事培训 →」；
- [ ] 赛事培训按发帖时间从新到旧：737、608、496、458、421；
- [ ] 社区热帖为 789、635、711、644、616，且没有 737；
- [ ] 05 左右两列无重复话题；
- [ ] 06 栏目更新不与 05 重复；
- [ ] 手机端两列上下堆叠，无横向溢出。

### 6.3 空态

- 赛事培训接口失败或为空：05 自动退回单列（社区热帖 + 今日一问占满宽度）；
- 社区热帖为空：今日一问单独在左列，赛事培训仍在右列；
- 今日一问为空：左列只有社区热帖；
- 全部为空：05 整块不渲染。

## 7. 回滚

把 _rebind-backup/20261119-theme19-v047/ 下的四个原件粘回后台即可：

- ai-pulse-home.gjs
- ai-pulse-feed.js
- settings.yml
- common.scss

本次没有改数据库、底座帖 632 或采集器，回滚只涉及主题代码。

## 8. 已知事项

1. 分类 17 里的 608《金风科技数据管理白皮书》会在赛事培训第 2 位出现。这是内容分类问题（审计文档建议移到 15 或 18），前端不特殊过滤；建议在论坛后台改分类。
2. 采集器仍写 feed.community，但首页已不展示；后续可单独决定是否停写。
3. 分类 17 置顶帖较多，order=created 会按真实创建时间排序，不受置顶影响。
4. about.json 已把版本号改为 v0.4.8；如果走方案 B 在原组件上粘贴，后台组件名仍可能显示 v0.4.7，可手动改名。

## 9. 本地校验记录

- gjs 模板：{{#if}} 77/77、{{#each}} 19/19、五个 template 的 HTML 标签闭合均通过；
- ai-pulse-feed.js：node --check 通过；
- loadCategoryTopics(17, {order:"created"}) 实际请求 /c/17/l/latest.json?order=created，缓存 key 为 cat:17:5:created:632；
- 用线上实时 JSON 跑 normalizeTopics：赛事培训为 737、608、496、458、421，社区热帖为 789、635、711、644、616；
- 原始文件与改动文件 diff 已逐段复核，只有本文件第 1 节列出的变更；
- 发布包由原始 v0.4.7 包解压后仅替换 4 个代码文件和 about.json，目录结构与原件一致。
