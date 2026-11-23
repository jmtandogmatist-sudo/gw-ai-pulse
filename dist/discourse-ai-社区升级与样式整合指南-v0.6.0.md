# 金风 AI 社区升级与样式整合部署指南（v0.6.0）

- 发布日期：2026-11-21
- 交付包 1：`dist/discourse-ai-雷达首页-v0.6.0.zip`（SHA-256 见同名 `.sha256`）
- 交付包 2：`dist/discourse-ai-社区统一帖子样式-v1.0.0.zip`（SHA-256 见同名 `.sha256`）

---

## 一、8 个样式 `.zip` 整合方案（8 → 3）

此前社区分散安装了 8 个主题组件（且 `AI 情报站话题样式 v3` 与 `v4` 并存），维护繁琐且多套强调色冲突。本次整合后**全站只需保留 3 个职责清晰的组件**：

| 原组件（8 个） | 处置动作 | 整合后归属（3 个） |
|---|---|---|
| `Custom Top Navigation Links` | **保留**（不动） | **① `Custom Top Navigation Links`**（顶部导航栏扩展） |
| `AI 雷达首页 v0.5.2` | **升级替换** | **② `AI 雷达首页 v0.6.0`**（独立看板路由、交互与首页样式） |
| `AI 情报站话题样式 v3` | **停用并删除**（已被 v4 覆盖） | — |
| `AI 情报站话题样式 v4` (`ais-*`) | **停用并删除**（已合并） | **③ `AI 社区统一帖子样式 v1.0.0`**（1 包覆盖 5 类专栏样式 + 历史日报过滤器，全站统一金风蓝 + 风电青色盘） |
| `AI 雷达日报样式 v2` (`air-*` + JS) | **停用并删除**（已合并） | 同上 ③ |
| `AI前沿情报月报·帖子样式` (`monthly-*`) | **停用并删除**（已合并） | 同上 ③ |
| `AI平台更新帖子样式` (`pu-*`) | **停用并删除**（已合并） | 同上 ③ |
| `一个场景彻底拆开 帖子样式` (`case-*`) | **停用并删除**（已合并） | 同上 ③ |

> 所有 5 套帖子样式的 `data-wrap` 命名空间完全隔离（`ais-*` / `air-*` / `monthly-*` / `pu-*` / `case-*`），历史文章 100% 向下兼容；同时修复了深度简报详情页的砖红顶边（统一为金风蓝）与标题字体混排问题。

---

## 二、Discourse 后台 5 分钟配置步骤

### 第一步：将「热门（hot）」设为社区默认第一入口
1. 进入 **后台 → 外观 → 界面与布局**，找到 **Top menu**。
2. 将条目顺序调整为把 **`hot` 放在第一位**（Discourse 自动以 Top menu 第一项作为全站 `/` 默认落地页）：
   ```text
   hot | latest | categories | new | unread
   ```
3. 点击保存。此时访客打开 `https://aicopilot.goldwind.com.cn:3211/` 将默认直接进入高赞员工实践集中的「热门」页，不再一进门就被自动信号帖刷屏。

### 第二步：升级 `AI 雷达首页 v0.6.0`
1. 进入 **后台 → 外观 → 主题与组件 → 组件**。
2. 上传并安装 `dist/discourse-ai-雷达首页-v0.6.0.zip`，挂载到当前主主题，**停用旧版 `v0.5.2`**。
3. 核对关键设置（默认即最佳）：
   - `homepage_paths`：`/latest?radar=1`
   - `hide_topic_list_on_radar_tab`：勾选
   - `compact_banner_on_radar_tab`：勾选（雷达页自动收紧顶部大欢迎语，把首屏留给 01/02）
   - `show_section_nav`：勾选（刊头下显示 01–06 快捷跳转条）
   - `schedule_note`：`每天 08:00 · 12:00 · 16:00 自动采集 · 08:30 深度简报`

### 第三步：部署 `AI 社区统一帖子样式 v1.0.0` 并清理旧组件
1. 上传并安装 `dist/discourse-ai-社区统一帖子样式-v1.0.0.zip`，挂载到同一主主题。
2. **停用（确认正常后再删除）**以下 6 个旧组件：`AI 情报站话题样式 v3`、`v4`、`AI 雷达日报样式 v2`、`AI前沿情报月报·帖子样式`、`AI平台更新帖子样式`、`一个场景彻底拆开 帖子样式`。

### 第四步（推荐运营优化）：隐藏后台机读标签 + 左侧边栏加入口
1. **隐藏机读标签**：进入 **后台 → 标签 → 标签组**，新建标签组 `内部采集标签（仅管理员可见）`，权限设为「仅管理员可见」，将 `auto`、`cred-red`、`cred-yellow`、`cred-green`、`q01`–`q09`、`topic-*`、`dossier` 放入该组。公开页面立刻清爽无机器标签。
2. **左侧边栏常驻入口**：点击左侧边栏「社区」旁编辑铅笔，新增自定义链接 `AI 雷达` → `/latest?radar=1`，方便员工从任意帖子页一键返回雷达。

---

## 三、本机定时任务防休眠更新（针对今日深度简报延迟）

已更新仓库内 `launchd/com.goldwind.gw-pulse.daily-brief.plist`（加入 `/usr/bin/caffeinate -i` 防止运行中途休眠挂断），并在 `02-深度简报/brief/harvester.py` 改为并发检索与 `(connect, read)` 双超时。

在终端执行以下命令同步生效本机 LaunchAgent：

```bash
cp /Users/sr_jm/gw_llm_ai_pulse/launchd/com.goldwind.gw-pulse.daily-brief.plist \
   ~/Library/LaunchAgents/com.goldwind.gw-pulse.daily-brief.plist
launchctl bootout gui/$(id -u)/com.goldwind.gw-pulse.daily-brief 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.goldwind.gw-pulse.daily-brief.plist
```

---

## 四、验收清单

- [ ] 访问 `/` 默认落在 **热门** Tab，且「热门」蓝色高亮。
- [ ] 点击 **AI雷达** 进入 `/latest?radar=1`：**仅「AI雷达」蓝色高亮**（不再 4 个 Tab 全灰），右侧无效的 `类别`/`标签` 下拉已隐藏，顶部欢迎大标题已收紧。
- [ ] 刊头为金风蓝微渐变 + 实时更新状态胶囊 + 快捷锚点条；`01` 移除玫红改为风电青/金风蓝协同配色；`05 社区热帖` 的 `:fire:` 正确显示为 🔥；`06 栏目更新` 为 2×2 对称网格无大片空白。
- [ ] 在 `/latest?radar=1` 点击页头 Logo、**热门**、**最新** 均能顺畅返回，不粘连 `?radar=1`。
- [ ] 打开每日深度简报（如 `/t/932`）、月报、平台更新、场景拆开、历史日报帖，所有卡片排版正常且顶边已统一为金风蓝。
