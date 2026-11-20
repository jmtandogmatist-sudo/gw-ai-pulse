# 04 今日值得看 4 条 → 5 条（v0.4.8 → v0.4.9）

- 日期：2026-11-20
- 基线：线上「AI 雷达首页 v0.4.8」，theme id **20**，2026-11-20 09:41 抓取
- 新版本：AI 雷达首页 v0.4.9（同一组件原地升级，未新建组件）
- 发布包：dist/discourse-ai-雷达首页-v0.4.9.zip
- 发布包 sha256：见 dist/discourse-ai-雷达首页-v0.4.9.zip.sha256
- 本地工作副本：work/theme19/
- 线上原件备份：_rebind-backup/20261120-theme20-v048/（v047、v048 全字段，含 meta）

## 1. 为什么要改

采集器那边把「今日值得看」的取数上限从 4 提到 5（`render.PICKS_MAX`），
底座帖 JSON 里确实写进了 5 条，但**首页仍然只显示 4 条**：
主题组件 `javascripts/discourse/lib/ai-pulse-feed.js` 里有一道前端截断：

```js
const MAX = { brief: 3, picks: 4, ... };
```

前端是最后一道闸门，采集器改多少都会被它截到 4。所以 v0.4.9 唯一的功能改动就是这一行的值。

## 2. 改了什么

| 文件 | 改动 |
|---|---|
| discourse/lib/ai-pulse-feed.js | `MAX.picks: 4 → 5`（其余一字未动） |
| about.json | name 改为「AI 雷达首页 v0.4.9」 |

组件名与 about.json 里的版本号也同步改到 v0.4.9（避免线上出现「内容已是 0.4.9、名字还写着 0.4.8」）。

## 3. 页面影响

- 04「今日值得看」由 4 条变 5 条；`.ap-picks` 是垂直列表（CSS 无固定列数），第 5 条不需要改样式。
- 其余区块（01/02/03/05/06/07）与 settings.yml 全部不动。
- 02 与 04 是左右两栏，右栏可能比左栏长出约一行——已核对 common.scss 无等高约束，不会破版。

## 4. 上线方式

线上主题字段通过 admin API 原地更新（`PUT /admin/themes/20`，只提交
`theme_fields` 中的 `discourse/lib/ai-pulse-feed.js` 一个字段），随后单独一次 PUT 改
组件名，再一次改 about.json。**未新建组件、未改动父子主题挂载关系**，
因此不需要重新勾选「AI 雷达首页」到 Foundation / Horizon。

注意：把 name + about + theme_fields 放在同一个 PUT 里会让接口返 500
（about 字段单独提交正常），已在 `_rebind-backup` 记录里留痕。

## 5. 验收

1. 线上主题字段读回：`picks: 5`，且与备份原件「只差这一处」；`enabled: true`。
2. 用**线上实际的** ai-pulse-feed.js 解析线上底座帖原始内容 → 04 渲染出 5 条。
3. 匿名（不带 key）拉 `/t/632.json` 的 cooked，JSON 块里 `picks` 长度为 5，第 5 条在页面正文里可搜到。
4. 解压本发布包，用包里的 js 解析同一份底座帖 → 同样 5 条（包与线上一致）。
5. work/theme19 六个文件与线上字段逐字节一致（已在打包前核对）。

## 6. 回滚

`_rebind-backup/20261120-theme20-v048/v048-discourse_lib_ai-pulse-feed.js.txt` 即线上原件，
按同样方式 PUT 回去，并把 name/about 改回 v0.4.8。
