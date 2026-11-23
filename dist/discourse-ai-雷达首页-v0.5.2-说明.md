# AI 雷达独立标签页（v0.5.1 → v0.5.2）

- 日期：2026-11-20
- 基线：`dist/discourse-ai-雷达首页-v0.5.1.zip`
- 发布包：`dist/discourse-ai-雷达首页-v0.5.2.zip`
- 校验：`dist/discourse-ai-雷达首页-v0.5.2.zip.sha256`

## 1. 为什么不能只在 Nav Links 里填 `/ai-radar`

`Discourse-nav-links-component` 是前端主题组件，只负责在顶部 `#navigation-bar`（【热门】【最新】旁边）增加链接按钮，**无法在 Discourse 后端注册新页面路由**；直接填 `/ai-radar` 点击或刷新会报 404。

v0.5.2 采用 Discourse 原生发现页路由 + 注册查询参数的方案（`/latest?radar=1`）：

1. 通过官方 API `api.addDiscoveryQueryParam("radar", { replace: false, refreshModel: false })` 合法注册 `radar` 参数，防止被 Ember 路由剥离，且切换标签不重复拉后端帖子接口；
2. 服务端直访、F5 刷新、分享链接到同事均返回 200（永不 404），且顶部 `#navigation-bar` 保持可见，随时可在【热门】【最新】【AI雷达】之间切换；
3. 进入 `/latest?radar=1` 时自动给 `body` 挂 `ap-radar-tab-active` 与 `ap-radar-tab-hide-list`：隐藏下方原本的帖子列表，并取消【最新】标签的重复高亮，只保留【AI雷达】高亮；
4. 在 `/latest?radar=1` 下点击【最新】或页头 Logo 时显式清理 `radar` 参数，避免 Ember sticky queryParam 导致回不去【最新】；
5. 组件改为**按需加载**：访问默认首页 `/`、【热门】、【最新】时不发 AI 雷达的 8–10 个数据请求，首次点进【AI雷达】才加载并缓存，后续来回切换秒开。

## 2. 改动清单

| 文件 | 改动 |
|---|---|
| `javascripts/discourse/lib/ai-pulse-route.js` | 新增：URL 与查询参数匹配工具 `matchHomepageUrl()`（支持 `/latest?radar=1` 独立标签页模式与传统 `/,/latest` 模式） |
| `javascripts/discourse/api-initializers/ai-pulse-home.js` | 注册 `radar` 发现页查询参数；路由切换同步 body 类；清理 sticky 参数与拦截回点【最新】/Logo |
| `javascripts/discourse/components/ai-pulse-home.gjs` | `shouldShow` 改用 `matchHomepageUrl()`；改为按需加载；新增首次加载占位提示 `正在加载 AI 雷达…` |
| `common/common.scss` | 新增 `.ap-home--loading`、`body.ap-radar-tab-active`（取消【最新】重复高亮）、`body.ap-radar-tab-hide-list`（隐藏下方原生帖子列表） |
| `settings.yml` | `homepage_paths` 默认值改为 `/latest?radar=1`；新增 `radar_query_param`（默认 `radar`）与 `hide_topic_list_on_radar_tab`（默认 `true`） |
| `about.json` | 版本名更新为 `AI 雷达首页 v0.5.2` |

## 3. 后台部署与配置步骤

### 第一步：上传并启用 `AI 雷达首页 v0.5.2`

1. 进入 Discourse 后台：**外观 → 主题与组件 → 组件**。
2. 点 **安装 → 从设备上传**，选择 `dist/discourse-ai-雷达首页-v0.5.2.zip`（或覆盖更新现有组件）。
3. 勾选挂载到当前使用的主题（如 Foundation / Horizon），确认停用旧版 `v0.5.1`（避免两个版本同时启用重复渲染）。
4. 核对组件设置（默认即生效）：
   - `homepage_paths`：`/latest?radar=1`
   - `radar_query_param`：`radar`
   - `hide_topic_list_on_radar_tab`：`true`（勾选）
   - 若升级覆盖旧组件后 `homepage_paths` 仍保留旧值 `/,/latest`，手动改成 `/latest?radar=1` 即可。

### 第二步：配置 `Discourse-nav-links-component`（Custom Top Navigation Links）

1. 打开已安装的 **Custom Top Navigation Links** 组件设置，确保已挂载到同一个主主题。
2. 在 `Nav links` 设置项中添加一行（英文分号 `;` 分隔：`名称;悬停提示;链接`）：

   ```text
   AI雷达;每日洞察 AI 赋能风电;/latest?radar=1
   ```

   > 若你安装的新版组件界面是可视化表单（而非分号文本），按字段填入即可：
   > - Name / 名称：`AI雷达`
   > - Description / 描述：`每日洞察 AI 赋能风电`
   > - URL / 链接：`/latest?radar=1`

3. 保持 `Hide default links` 与 `Hide dropdowns` 为关闭状态，使【热门】【最新】与【AI雷达】并排显示。

## 4. 验收清单

- [ ] 打开论坛默认首页 `/`：只显示正常社区帖子列表，不出现 AI 雷达看板。
- [ ] 顶部导航栏在【热门】【最新】旁边出现【AI雷达】标签。
- [ ] 点击【AI雷达】：地址变为 `/latest?radar=1`，只显示 AI 雷达看板，下方无多余帖子列表，且仅【AI雷达】高亮（【最新】不高亮）。
- [ ] 在 `/latest?radar=1` 按 F5 刷新页面：仍正常显示 AI 雷达，不 404、不闪出旧帖子列表。
- [ ] 点击【最新】或页头 Logo：立刻回到正常【最新】帖子列表，且【最新】恢复高亮。
- [ ] 切到【热门】再切回【最新】：保持正常帖子列表，不会误跳回 AI 雷达。

## 5. 一键回退

如需临时恢复“首页直接展示 AI 雷达”，无需重新安装包，直接在后台把 `AI 雷达首页 v0.5.2` 的 `homepage_paths` 改回 `/,/latest` 即可。
