import Component from "@glimmer/component";
import { tracked } from "@glimmer/tracking";
import { fn } from "@ember/helper";
import { on } from "@ember/modifier";
import { action } from "@ember/object";
import { service } from "@ember/service";
import { htmlSafe } from "@ember/template";
import DiscourseURL from "discourse/lib/url";
import {
  formatTime,
  hoursSince,
  loadCategoryTopics,
  loadFeed,
  loadHotTopics,
  parseColumns,
} from "../lib/ai-pulse-feed";

const DAILY_TITLE_RE = /^AI 雷达日报/;

// 左侧大编号
const Gutter = <template>
  <div class="ap-gutter" aria-hidden="true">
    <span class="ap-num">{{@num}}</span>
    <span class="ap-gutter__line"></span>
    <span class="ap-gutter__en">{{@en}}</span>
  </div>
</template>;

// 去掉空白、标点与日期后缀，用于判断首屏是否已出现同一条
function norm(t) {
  return String(t || "")
    .replace(/[（(]\d{1,2}月\d{1,2}日[^）)]*[）)]/g, "")
    .replace(/[\s，。、：:；;！!？?「」“”"'·]/g, "");
}

// 精编 / 跟踪卡片
const Card = <template>
  <article class={{@card.cls}}>
    <div class="ap-chips">
      <span class="ap-chip ap-chip--sec">{{@card.section}}</span>
      {{#if @card.hot}}<span class="ap-chip ap-chip--hot">与金风直接相关</span>{{/if}}
      <span class="ap-chip ap-chip--cred ap-chip--{{@card.cred}}">{{@card.credLabel}}</span>
    </div>
    <h4 class="ap-card__title">
      {{#if @card.topicUrl}}
        <a href={{@card.topicUrl}}>{{@card.title}}</a>
      {{else}}
        {{@card.title}}
      {{/if}}
    </h4>
    {{#if @card.summary}}
      <p class="ap-card__body">{{@card.summary}}</p>
    {{/if}}
    {{#if @card.us}}
      <p class="ap-card__us"><strong>对我们：</strong>{{@card.us}}</p>
    {{/if}}
    {{#if @card.sources.length}}
      <p class="ap-card__src">
        {{#each @card.sources as |src|}}
          <a href={{src.url}} target="_blank" rel="noopener noreferrer">{{src.label}} ↗</a>
        {{/each}}
      </p>
    {{/if}}
  </article>
</template>;

// 信号雷达卡片（v0.3：标题点进站内信号话题；原文链接单独放；带审核状态、问题标签、相关度、对金风）
const Signal = <template>
  <article class={{@sig.cls}}>
    <div class="ap-chips">
      <span class="ap-chip ap-chip--sec">{{@sig.cat}}</span>
      <span class="ap-chip ap-chip--cred ap-chip--{{@sig.cred}}">{{@sig.credLabel}}</span>
      <span class="ap-chip ap-chip--review ap-chip--{{@sig.review}}">{{@sig.reviewLabel}}</span>
      {{#each @sig.questions as |question|}}<span class="ap-chip ap-chip--q">{{question}}</span>{{/each}}
      {{#if @sig.relDots}}<span class="ap-rel" title="对金风的相关度">相关度 {{@sig.relDots}}</span>{{/if}}
    </div>
    <h4 class="ap-sig__title">
      {{#if @sig.topicUrl}}
        <a href={{@sig.topicUrl}}>{{@sig.title}}</a>
      {{else if @sig.url}}
        <a href={{@sig.url}} target="_blank" rel="noopener noreferrer">{{@sig.title}}</a>
      {{else}}
        {{@sig.title}}
      {{/if}}
    </h4>
    {{#if @sig.summary}}<p class="ap-sig__body">{{@sig.summary}}</p>{{/if}}
    {{#if @sig.us}}<p class="ap-sig__us"><strong>对金风：</strong>{{@sig.us}}</p>{{/if}}
    <p class="ap-sig__meta">
      {{#if @sig.whenLabel}}<span class="ap-when" title="发布时间">{{@sig.whenLabel}}</span>{{/if}}
      {{#each @sig.tags as |topicItem|}}<span class="ap-tag">{{topicItem}}</span>{{/each}}
      {{#if @sig.from}}<span>{{@sig.from}}</span>{{/if}}
      {{#if @sig.topicUrl}}{{#if @sig.url}}<a class="ap-host" href={{@sig.url}} target="_blank" rel="noopener noreferrer">原文 ↗</a>{{/if}}{{else}}{{#if @sig.host}}<span class="ap-host">{{@sig.host}}</span>{{/if}}{{/if}}
    </p>
  </article>
</template>;

// 论坛话题列表（右侧栏、栏目、热帖共用）
const TopicList = <template>
  <ul class="ap-topics {{if @thumbs 'ap-topics--thumbs'}}">
    {{#each @topics as |topicItem|}}
      <li>
        {{#if @thumbs}}
          {{#if topicItem.image}}
            <img class="ap-topics__img" src={{topicItem.image}} alt="" loading="lazy" />
          {{else}}
            <span class="ap-topics__img ap-topics__img--empty"></span>
          {{/if}}
        {{/if}}
        <div>
          <a class="ap-topics__title" href={{topicItem.url}}>{{topicItem.title}}</a>
          <span class="ap-topics__meta">{{topicItem.day}}{{#if topicItem.views}} · {{topicItem.views}} 阅读{{/if}}</span>
        </div>
      </li>
    {{/each}}
  </ul>
</template>;

export default class AiPulseHome extends Component {
  @service router;

  @tracked feed = null;
  loaded = false; // 只防重复加载，不参与渲染

  // v0.4.2：不再依赖 @ember/render-modifiers（新版 Discourse 可能不再提供），构造时直接加载
  constructor() {
    super(...arguments);
    this.load();
  }
  @tracked side = null;
  @tracked columns = [];
  @tracked hot = [];
  @tracked activeCat = "";
  @tracked activeQ = "";
  @tracked hideSingle = false;
  @tracked expanded = false;
  @tracked curatedOpen = false;
  @tracked trackingOpen = false;
  @tracked mainlineOpen = false;
  @tracked radarOpen = !(
    settings.collapse_radar_on_mobile &&
    typeof window !== "undefined" &&
    window.matchMedia?.("(max-width: 700px)").matches
  );

  get shouldShow() {
    const path = (this.router.currentURL || "/").split("?")[0].split("#")[0];
    const allowed = String(settings.homepage_paths || "/,/latest")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    return allowed.includes(path);
  }

  @action
  async load() {
    if (this.loaded) {
      return;
    }
    this.loaded = true;
    const limit = Number(settings.topics_per_column) || 3;
    const feedId = Number(settings.feed_topic_id);
    const sideCol = parseColumns(settings.side_category)[0] || null;
    const cols = parseColumns(settings.column_categories);
    const hotCount = Number(settings.hot_count) || 0;

    // v0.4.1：先取底座帖并立即渲染；其余列表依次取（不再一次并发 8 个请求），
    // 任一失败只隐藏对应区块；网关拦截时后续请求全部跳过
    const settle = async (fn) => {
      try {
        return { status: "fulfilled", value: await fn() };
      } catch (reason) {
        return { status: "rejected", reason };
      }
    };
    const feed = await settle(() => loadFeed(feedId, Number(settings.feed_post_number) || 1));
    this.feed = feed.status === "fulfilled" ? feed.value : null;
    const side = sideCol ? await settle(() => loadCategoryTopics(sideCol.id, { limit })) : { status: "fulfilled", value: [] };
    const lists = [];
    for (const c of cols) {
      lists.push(await settle(() => loadCategoryTopics(c.id, { limit })));
    }
    const colLists = { status: "fulfilled", value: lists };
    const hot = hotCount
      ? await settle(() => loadHotTopics({ limit: hotCount, excludeIds: [feedId], excludeTitle: DAILY_TITLE_RE }))
      : { status: "fulfilled", value: [] };
    this.side =
      sideCol && side.status === "fulfilled" && side.value.length
        ? { ...sideCol, topics: side.value, moreUrl: `/c/${sideCol.id}` }
        : null;
    this.columns = cols
      .map((c, i) => ({
        ...c,
        topics: colLists.value[i]?.status === "fulfilled" ? colLists.value[i].value : [],
        moreUrl: `/c/${c.id}`,
      }))
      .filter((c) => c.topics.length);
    this.hot = hot.status === "fulfilled" ? hot.value : [];
  }

  // ---- 版面 ----

  get hasAny() {
    return this.feed || this.side || this.columns.length || this.hot.length;
  }

  // 只给实际出现的分区编号，缺哪块编号不跳
  get nums() {
    const f = this.feed;
    const keys = [];
    if (f?.brief.length || f?.picks.length) {
      keys.push("brief");
    }
    if (f?.lead || this.side) {
      keys.push("lead");
    }
    if (f?.curated.length || f?.tracking.length) {
      keys.push("curated");
    }
    if (f?.radar.length) {
      keys.push("radar");
    }
    if (f?.topics.length && settings.show_topics !== false) {
      keys.push("topics");
    }
    if (f?.stance && settings.show_stance !== false) {
      keys.push("stance");
    }
    if (f?.community.length || f?.question || this.hot.length) {
      keys.push("community");
    }
    if (this.columns.length) {
      keys.push("columns");
    }
    const out = {};
    keys.forEach((k, i) => (out[k] = String(i + 1).padStart(2, "0")));
    return out;
  }

  get brandTitle() {
    return settings.brand_title || "AI 雷达";
  }
  get brandSub() {
    return settings.brand_subtitle || "";
  }
  get scheduleNote() {
    return settings.schedule_note || "";
  }
  get showSearch() {
    // v0.4 默认关闭：论坛页头已有搜索
    return settings.show_search === true;
  }

  // ---- v0.4：精编折叠 ----
  get curatedFoldAt() {
    const n = Number(settings.curated_initial_count);
    return Number.isInteger(n) && n > 0 ? n : 3;
  }

  // 首屏默认显示的精编：先去掉上面简报、值得看已出现的，再取前 N 条
  get curatedShown() {
    const f = this.feed;
    if (!f) {
      return [];
    }
    if (this.curatedOpen) {
      return f.curatedRest;
    }
    const seen = [...f.brief.map((b) => b.text), ...f.picks.map((p) => p.title)].map(norm);
    const fresh = f.curatedRest.filter((c) => !seen.some((t) => t.startsWith(norm(c.title).slice(0, 12))));
    return (fresh.length ? fresh : f.curatedRest).slice(0, this.curatedFoldAt);
  }

  get curatedHidden() {
    return this.feed ? this.feed.curatedRest.length - this.curatedShown.length : 0;
  }

  get mainlineCls() {
    return this.mainlineOpen ? "ap-mainline is-open" : "ap-mainline";
  }

  // 两行约 90 个汉字，超过才给「展开」
  get mainlineLong() {
    return this.mainlineOpen || (this.feed?.mainline || "").length > 90;
  }

  @action
  toggleCurated() {
    this.curatedOpen = !this.curatedOpen;
  }

  @action
  toggleTracking() {
    this.trackingOpen = !this.trackingOpen;
  }

  @action
  toggleMainline() {
    this.mainlineOpen = !this.mainlineOpen;
  }

  get updatedText() {
    return formatTime(this.feed?.updatedAt);
  }
  get nextText() {
    return formatTime(this.feed?.nextAt);
  }

  // 有“下一轮时间”时：超过它 6 小时仍未更新才提示；否则按 stale_hours 兜底
  get staleHours() {
    const f = this.feed;
    if (!f?.updatedAt) {
      return 0;
    }
    const now = Date.now();
    const late = f.nextAt ? now > f.nextAt + 6 * 3600000 : hoursSince(f.updatedAt, now) > Number(settings.stale_hours || 36);
    return late ? Math.floor(hoursSince(f.updatedAt, now)) : 0;
  }

  // 信号雷达的过期提示：采集器写了下一轮时间时，超过它 grace 小时仍未刷新才提示
  get radarStaleHours() {
    const f = this.feed;
    if (!f?.radarUpdatedAt) {
      return 0;
    }
    const now = Date.now();
    const grace = Number(settings.radar_stale_grace_hours || 2) * 3600000;
    const late = f.radarNextAt
      ? now > f.radarNextAt + grace
      : hoursSince(f.radarUpdatedAt, now) > Number(settings.radar_stale_hours || 16);
    return late ? Math.floor(hoursSince(f.radarUpdatedAt, now)) : 0;
  }

  get radarUpdatedText() {
    return formatTime(this.feed?.radarUpdatedAt);
  }

  get barG() {
    return htmlSafe(`width:${this.feed.mix.g}%`);
  }
  get barY() {
    return htmlSafe(`width:${this.feed.mix.y}%`);
  }
  get barR() {
    return htmlSafe(`width:${this.feed.mix.r}%`);
  }

  get leadTwoCol() {
    return this.feed?.lead && this.side ? "ap-sec__body ap-two ap-two--lead" : "ap-sec__body";
  }
  get briefTwoCol() {
    return this.feed?.brief.length && this.feed?.picks.length ? "ap-sec__body ap-two" : "ap-sec__body";
  }
  get communityTwoCol() {
    const f = this.feed;
    return (f?.community.length || f?.question) && this.hot.length ? "ap-sec__body ap-two" : "ap-sec__body";
  }

  // ---- 雷达筛选 ----

  get chips() {
    const f = this.feed;
    const mk = (value, name, count) => ({
      value,
      name,
      count,
      pressed: this.activeCat === value ? "true" : "false",
      cls: this.activeCat === value ? "ap-filter is-on" : "ap-filter",
    });
    return [mk("", "全部", f.radar.length), ...f.cats.map((c) => mk(c.name, c.name, c.count))];
  }

  get qChips() {
    const f = this.feed;
    if (!f.qs.length) {
      return [];
    }
    const mk = (value, name, count, title) => ({
      value,
      name,
      count,
      title,
      pressed: this.activeQ === value ? "true" : "false",
      cls: this.activeQ === value ? "ap-filter ap-filter--q is-on" : "ap-filter ap-filter--q",
    });
    return [
      mk("", "全部问题", f.radar.length, ""),
      ...f.qs.map((q) => mk(q.id, q.short ? `${q.id} ${q.short}` : q.id, q.count, q.text)),
    ];
  }

  get filteredRadar() {
    return this.feed.radar.filter(
      (s) =>
        (!this.activeCat || s.cat === this.activeCat) &&
        (!this.activeQ || s.questions.includes(this.activeQ)) &&
        (!this.hideSingle || s.cred !== "r")
    );
  }

  get visibleRadar() {
    const n = Number(settings.radar_initial_count) || 12;
    return this.expanded ? this.filteredRadar : this.filteredRadar.slice(0, n);
  }

  get hiddenCount() {
    return this.filteredRadar.length - this.visibleRadar.length;
  }

  get singleToggleCls() {
    return this.hideSingle ? "ap-filter ap-filter--toggle is-on" : "ap-filter ap-filter--toggle";
  }

  get radarToggleText() {
    return this.radarOpen ? "收起" : `展开 ${this.feed.radar.length} 条`;
  }

  @action
  pickCat(value) {
    this.activeCat = value;
    this.expanded = false;
  }

  @action
  pickQ(value) {
    this.activeQ = value;
    this.expanded = false;
  }

  @action
  toggleSingle() {
    this.hideSingle = !this.hideSingle;
    this.expanded = false;
  }

  @action
  expandAll() {
    this.expanded = true;
  }

  @action
  toggleRadar() {
    this.radarOpen = !this.radarOpen;
  }

  @action
  search(event) {
    event.preventDefault();
    const q = String(new FormData(event.target).get("q") || "").trim();
    if (q) {
      DiscourseURL.routeTo(`/search?q=${encodeURIComponent(q)}`);
    }
  }

  <template>
    <div class="ap-root">
      {{#if this.shouldShow}}
        {{#if this.hasAny}}
          <section class="ap-home" aria-label={{this.brandTitle}}>
            {{! v0.4：刊头压成一行；可信度分布移到精编标题旁；搜索默认关闭（论坛页头已有） }}
            <header class="ap-mast">
              <h2 class="ap-mast__title" title={{this.brandSub}}>{{this.brandTitle}}</h2>
              {{#if this.feed}}
                <p class="ap-mast__meta">
                  <span>{{this.feed.date}}</span>
                  {{#if this.updatedText}}<span>{{this.updatedText}} 更新</span>{{/if}}
                  {{#if this.feed.curatedCount}}<span><b>{{this.feed.curatedCount}}</b> 条精编</span>{{/if}}
                  {{#if this.feed.tracking.length}}<span><b>{{this.feed.tracking.length}}</b> 条跟踪</span>{{/if}}
                  {{#if this.feed.radar.length}}<span><b>{{this.feed.radar.length}}</b> 条信号</span>{{/if}}
                </p>
              {{/if}}
            </header>

            {{#if this.showSearch}}
              <form class="ap-search" role="search" {{on "submit" this.search}}>
                <input name="q" type="search" placeholder="搜索社区：例如 Claude Code、交付清单、一招一式…" aria-label="搜索社区" />
                <button type="submit">搜索</button>
              </form>
            {{/if}}

            {{#if this.staleHours}}
              <p class="ap-stale">数据已 {{this.staleHours}} 小时未更新，内容可能已过期。</p>
            {{/if}}

            {{#if this.feed.mainline}}
              <div class={{this.mainlineCls}}>
                <p><span>今日主线</span>{{this.feed.mainline}}</p>
                {{#if this.mainlineLong}}
                  <button type="button" class="ap-more" aria-expanded={{if this.mainlineOpen "true" "false"}} {{on "click" this.toggleMainline}}>{{if this.mainlineOpen "收起" "展开"}}</button>
                {{/if}}
              </div>
            {{/if}}

            {{! 01 今天发生了什么 + 今日值得看 }}
            {{#if this.nums.brief}}
              <div class="ap-sec ap-sec--hot">
                <Gutter @num={{this.nums.brief}} @en="THE BRIEF" />
                <div class={{this.briefTwoCol}}>
                  {{#if this.feed.brief.length}}
                    <div class="ap-col">
                      <h3 class="ap-kick ap-kick--hot">今天发生了什么 <small>1 分钟读懂</small></h3>
                      <div class="ap-brief">
                        {{#each this.feed.brief as |briefItem|}}
                          <div class="ap-brief__item">
                            <p class="ap-brief__text">{{briefItem.text}}</p>
                            {{#if briefItem.url}}
                              <p class="ap-brief__src"><span>来源</span><a href={{briefItem.url}} target="_blank" rel="noopener noreferrer">{{if briefItem.source briefItem.source "原文"}}</a></p>
                            {{/if}}
                          </div>
                        {{/each}}
                      </div>
                    </div>
                  {{/if}}
                  {{#if this.feed.picks.length}}
                    <div class="ap-col">
                      <h3 class="ap-kick">今日值得看</h3>
                      <ol class="ap-picks">
                        {{#each this.feed.picks as |pick|}}
                          <li>
                            <span class="ap-picks__num">{{pick.num}}</span>
                            <div>
                              <a class="ap-picks__title" href={{pick.url}} target={{if pick.external "_blank"}} rel="noopener noreferrer">{{pick.title}}</a>
                              <p class="ap-picks__sum">{{#if pick.kind}}<span class="ap-chip">{{pick.kind}}</span> {{/if}}{{pick.summary}}</p>
                            </div>
                          </li>
                        {{/each}}
                      </ol>
                    </div>
                  {{/if}}
                </div>
              </div>
            {{/if}}

            {{! 02 今日焦点 + 深度阅读 }}
            {{#if this.nums.lead}}
              <div class="ap-sec">
                <Gutter @num={{this.nums.lead}} @en="TODAY'S LEAD" />
                <div class={{this.leadTwoCol}}>
                  {{#if this.feed.lead}}
                    <article class="ap-lead">
                      <h3 class="ap-kick ap-kick--blue">今日焦点</h3>
                      <h2 class="ap-lead__title">
                        {{#if this.feed.lead.url}}<a href={{this.feed.lead.url}}>{{this.feed.lead.title}}</a>{{else}}{{this.feed.lead.title}}{{/if}}
                      </h2>
                      {{#if this.feed.lead.summary}}<p class="ap-lead__body">{{this.feed.lead.summary}}</p>{{/if}}
                      {{#if this.feed.lead.us}}<p class="ap-lead__us"><strong>对我们：</strong>{{this.feed.lead.us}}</p>{{/if}}
                      <p class="ap-lead__foot">
                        <span class="ap-chip ap-chip--cred ap-chip--{{this.feed.lead.cred}}">{{this.feed.lead.credLabel}}</span>
                        {{#each this.feed.lead.sources as |src|}}
                          <a href={{src.url}} target="_blank" rel="noopener noreferrer">{{src.label}} ↗</a>
                        {{/each}}

                      </p>
                    </article>
                  {{/if}}
                  {{#if this.side}}
                    <aside class="ap-side">
                      <h3 class="ap-kick ap-kick--blue">{{this.side.label}}</h3>
                      <TopicList @topics={{this.side.topics}} />
                      <a class="ap-go" href={{this.side.moreUrl}}>更多{{this.side.label}} →</a>
                    </aside>
                  {{/if}}
                </div>
              </div>
            {{/if}}

            {{! 03 精编 + 本周跟踪 }}
            {{#if this.nums.curated}}
              <div class="ap-sec">
                <Gutter @num={{this.nums.curated}} @en="CURATED" />
                <div class="ap-sec__body">
                  {{#if this.feed.curatedRest.length}}
                    <h3 class="ap-kick">
                      精编
                      <small>按与金风相关度排序 · 🟢 {{this.feed.counts.g}} 🟡 {{this.feed.counts.y}} 🔴 {{this.feed.counts.r}}</small>
                    </h3>
                    <div class="ap-grid">
                      {{#each this.curatedShown as |cardItem|}}
                        <Card @card={{cardItem}} />
                      {{/each}}
                    </div>
                    {{#if this.curatedHidden}}
                      <button type="button" class="ap-fold" aria-expanded="false" {{on "click" this.toggleCurated}}>展开其余 {{this.curatedHidden}} 条精编 ▾</button>
                    {{else if this.curatedOpen}}
                      <button type="button" class="ap-fold" aria-expanded="true" {{on "click" this.toggleCurated}}>收起 ▴</button>
                    {{/if}}
                  {{/if}}
                  {{#if this.feed.tracking.length}}
                    <button type="button" class="ap-fold ap-fold--sub" aria-expanded={{if this.trackingOpen "true" "false"}} {{on "click" this.toggleTracking}}>
                      本周跟踪 {{this.feed.tracking.length}} 条 {{if this.trackingOpen "▴" "▸"}}
                    </button>
                    {{#if this.trackingOpen}}
                      <ul class="ap-track">
                        {{#each this.feed.tracking as |cardItem|}}
                          <li class="ap-track__item ap-track__item--{{cardItem.cred}}">
                            <span class="ap-track__title">{{cardItem.title}}</span>
                            <span class="ap-track__body">{{cardItem.summary}}</span>
                            {{#each cardItem.sources as |src|}}<a href={{src.url}} target="_blank" rel="noopener noreferrer">{{src.label}} ↗</a>{{/each}}
                          </li>
                        {{/each}}
                      </ul>
                    {{/if}}
                  {{/if}}
                </div>
              </div>
            {{/if}}

            {{! 04 信号雷达 }}
            {{#if this.nums.radar}}
              <div class="ap-sec">
                <Gutter @num={{this.nums.radar}} @en="RADAR" />
                <div class="ap-sec__body">
                  <h3 class="ap-kick">
                    信号雷达 <small>{{this.feed.radar.length}} 条</small>
                    <button type="button" class="ap-link" {{on "click" this.toggleRadar}}>{{this.radarToggleText}}</button>
                  </h3>
                  {{#if this.radarOpen}}
                    <p class="ap-note">
                      采集器每轮自动更新{{#if this.radarUpdatedText}}，本轮 {{this.radarUpdatedText}}{{/if}}；按对金风的相关度排序。「未审」为机器采集、尚未人工确认，摘要以原文为准。
                    </p>
                    {{#if this.radarStaleHours}}
                      <p class="ap-stale">信号雷达已 {{this.radarStaleHours}} 小时未刷新，采集可能暂停。</p>
                    {{/if}}
                    <div class="ap-toolbar">
                      {{#each this.chips as |chip|}}
                        <button type="button" class={{chip.cls}} aria-pressed={{chip.pressed}} {{on "click" (fn this.pickCat chip.value)}}>{{chip.name}} <em>{{chip.count}}</em></button>
                      {{/each}}
                      <button type="button" class={{this.singleToggleCls}} {{on "click" this.toggleSingle}}>隐藏单一来源</button>
                    </div>
                    {{#if this.qChips.length}}
                      <div class="ap-toolbar ap-toolbar--q">
                        {{#each this.qChips as |chip|}}
                          <button type="button" class={{chip.cls}} title={{chip.title}} aria-pressed={{chip.pressed}} {{on "click" (fn this.pickQ chip.value)}}>{{chip.name}} <em>{{chip.count}}</em></button>
                        {{/each}}
                      </div>
                    {{/if}}
                    <div class="ap-grid ap-grid--sig">
                      {{#each this.visibleRadar as |src|}}
                        <Signal @sig={{src}} />
                      {{/each}}
                    </div>
                    {{#if this.hiddenCount}}
                      <button type="button" class="ap-more" {{on "click" this.expandAll}}>展开其余 {{this.hiddenCount}} 条</button>
                    {{/if}}
                  {{/if}}
                </div>
              </div>
            {{/if}}

            {{! 主题追踪（主题档案 skill 每周五写入） }}
            {{#if this.nums.topics}}
              <div class="ap-sec">
                <Gutter @num={{this.nums.topics}} @en="TOPICS" />
                <div class="ap-sec__body">
                  <h3 class="ap-kick">主题追踪 <small>每周五更新判断 · 点主题看完整时间线</small></h3>
                  <div class="ap-topicgrid">
                    {{#each this.feed.topics as |topicItem|}}
                      <article class="ap-topic">
                        <header class="ap-topic__head">
                          <h4 class="ap-topic__name">{{#if topicItem.url}}<a href={{topicItem.url}}>{{topicItem.name}}</a>{{else}}{{topicItem.name}}{{/if}}</h4>
                          {{#if topicItem.week}}<span class="ap-topic__week">本周 +{{topicItem.week}}</span>{{/if}}
                        </header>
                        {{#if topicItem.questions.length}}<p class="ap-topic__q">{{#each topicItem.questions as |question|}}<span class="ap-chip ap-chip--q">{{question}}</span>{{/each}}</p>{{/if}}
                        {{#if topicItem.judge}}<p class="ap-topic__judge"><strong>当前判断</strong>{{topicItem.judge}}</p>{{/if}}
                        {{#if topicItem.change}}<p class="ap-topic__change">本周变化：{{topicItem.change}}</p>{{/if}}
                        {{#if topicItem.next}}<p class="ap-topic__next">下个观察点：{{topicItem.next}}</p>{{/if}}
                      </article>
                    {{/each}}
                  </div>
                </div>
              </div>
            {{/if}}

            {{! 集团技术雷达（技术雷达 skill 每季度写入） }}
            {{#if this.nums.stance}}
              <div class="ap-sec">
                <Gutter @num={{this.nums.stance}} @en="STANCE" />
                <div class="ap-sec__body">
                  <h3 class="ap-kick">
                    集团技术雷达 {{#if this.feed.stance.issue}}<small>{{this.feed.stance.issue}}</small>{{/if}}
                    {{#if this.feed.stance.url}}<a class="ap-link" href={{this.feed.stance.url}}>看完整雷达 →</a>{{/if}}
                  </h3>
                  <div class="ap-rings">
                    {{#each this.feed.stance.rings as |ringItem|}}
                      <div class={{ringItem.cls}}>
                        <div class="ap-ring__head"><span class="ap-ring__name">{{ringItem.name}}</span>{{#if ringItem.rule}}<span class="ap-ring__rule">{{ringItem.rule}}</span>{{/if}}</div>
                        <ul class="ap-ring__items">
                          {{#each ringItem.items as |it|}}
                            <li>
                              <span class="ap-ring__item">{{#if it.url}}<a href={{it.url}}>{{it.name}}</a>{{else}}{{it.name}}{{/if}}{{#if it.moved}}<em class="ap-ring__moved">{{it.moved}}</em>{{/if}}</span>
                              {{#if it.why}}<span class="ap-ring__why">{{it.why}}</span>{{/if}}
                            </li>
                          {{/each}}
                        </ul>
                      </div>
                    {{/each}}
                  </div>
                </div>
              </div>
            {{/if}}

            {{! 05 社区：今日推荐 + 今日一问 + 热帖 }}
            {{#if this.nums.community}}
              <div class="ap-sec">
                <Gutter @num={{this.nums.community}} @en="COMMUNITY" />
                <div class={{this.communityTwoCol}}>
                  {{#if this.feed.community.length}}
                    <div class="ap-col">
                      <h3 class="ap-kick">社区今日 <small>社区帖</small></h3>
                      <ul class="ap-recs">
                        {{#each this.feed.community as |cardItem|}}
                          <li>
                            {{#if cardItem.who}}<span class="ap-recs__who">{{cardItem.who}}</span>{{/if}}
                            <a href={{cardItem.url}}>{{cardItem.title}}</a>
                          </li>
                        {{/each}}
                      </ul>
                      {{#if this.feed.question}}
                        <div class="ap-ask">
                          <span class="ap-ask__label">今日一问</span>
                          <p>{{this.feed.question}}</p>

                        </div>
                      {{/if}}
                    </div>
                  {{/if}}
                  {{#if this.hot.length}}
                    <div class="ap-col">
                      <h3 class="ap-kick">社区热帖</h3>
                      <TopicList @topics={{this.hot}} />
                    </div>
                  {{/if}}
                </div>
              </div>
            {{/if}}

            {{! 06 栏目更新 }}
            {{#if this.nums.columns}}
              <div class="ap-sec">
                <Gutter @num={{this.nums.columns}} @en="COLUMNS" />
                <div class="ap-sec__body">
                  <h3 class="ap-kick">栏目更新</h3>
                  <div class="ap-columns">
                    {{#each this.columns as |col|}}
                      <div class="ap-column">
                        <h4 class="ap-column__title"><a href={{col.moreUrl}}>{{col.label}}</a></h4>
                        <TopicList @topics={{col.topics}} @thumbs={{true}} />
                      </div>
                    {{/each}}
                  </div>
                </div>
              </div>
            {{/if}}
          </section>
        {{/if}}
      {{/if}}
    </div>
  </template>
}
