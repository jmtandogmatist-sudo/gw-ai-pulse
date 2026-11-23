// AI 雷达首页 v0.6.1（2026-11-23）：删除 05 社区精选与 06 栏目更新模块；今日值得看展示发布日期；信号雷达首屏预览按信源限流。
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
  loadTagTopics,
  pad2,
  parseColumns,
  parseTags,
} from "../lib/ai-pulse-feed";
import { matchHomepageUrl } from "../lib/ai-pulse-route";

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

// 信号雷达卡片（v0.6.0：头部减法降噪——左边框+精简信源圆点表达可信度，仅对已审展示「✓ 已核验」，去掉满屏「未审」噪音）
const Signal = <template>
  <article class={{@sig.cls}}>
    <div class="ap-chips">
      <span class="ap-chip ap-chip--sec">{{@sig.cat}}</span>
      <span class="ap-chip ap-chip--cred ap-chip--{{@sig.cred}}" title={{@sig.credLabel}}>
        <i class="ap-dot ap-dot--{{@sig.cred}}" aria-hidden="true"></i>{{@sig.credShort}}
      </span>
      {{#if @sig.isReviewed}}<span class="ap-chip ap-chip--reviewed">✓ 已核验</span>{{/if}}
      {{#if @sig.relDots}}<span class="ap-rel" title="对金风的相关度">{{@sig.relDots}}</span>{{/if}}
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
  @tracked loading = false;
  @tracked navTick = 0;
  loaded = false; // 只防重复加载，不参与渲染

  // v0.5.2：按需加载——只在真正命中的页面（如 /latest?radar=1）才拉取底座与栏目数据，避免普通首页多发请求
  constructor() {
    super(...arguments);
    this._onRouteChange = () => {
      this.navTick += 1;
      if (this.shouldShow && !this.loaded) {
        this.load();
      }
    };
    this.router?.on?.("routeDidChange", this._onRouteChange);
    if (this.shouldShow) {
      this.load();
    }
  }

  willDestroy() {
    super.willDestroy(...arguments);
    this.router?.off?.("routeDidChange", this._onRouteChange);
  }
  @tracked side = null;
  // v0.4.6：「按主题看」——标签驱动的跨分类聚合
  @tracked themeRows = [];
  // v0.4.5：activeCat 随上层领域筛选一并移除（见模板里 ap-toolbar 处的注释）
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
    // 依赖 navTick，确保仅查询参数变更（refreshModel: false）时也立即重算
    void this.navTick;
    const url =
      this.router?.currentURL ||
      (typeof window !== "undefined"
        ? `${window.location.pathname}${window.location.search}`
        : "/");
    return matchHomepageUrl(url, settings.homepage_paths).matched;
  }

  @action
  async load() {
    if (this.loaded) {
      return;
    }
    this.loaded = true;
    this.loading = true;
    try {
    const limit = Number(settings.topics_per_column) || 3;
    const feedId = Number(settings.feed_topic_id);
    // v0.4.7：右栏默认走采集器筛好的能源向信号（feed.energy），不再读论坛分类。
    const sideIsEnergy = String(settings.side_source || "energy") !== "category";
    const sideCol = sideIsEnergy ? null : parseColumns(settings.side_category)[0] || null;
    const tagCols = settings.show_themes === false ? [] : parseTags(settings.theme_tags);

    const seen = new Set();
    if (feedId) {
      seen.add(feedId);
    }
    const mark = (list) => {
      for (const item of list || []) {
        if (item?.id) {
          seen.add(Number(item.id));
        }
      }
    };
    const excluded = () => Array.from(seen);

    // v0.6.1：已删除 05 社区精选与 06 栏目更新，只加载底座帖及启用的可选扩展块，省去多余论坛请求
    const settle = async (fn) => {
      try {
        return { status: "fulfilled", value: await fn() };
      } catch (reason) {
        return { status: "rejected", reason };
      }
    };
    const feed = await settle(() => loadFeed(feedId, Number(settings.feed_post_number) || 1));
    this.feed = feed.status === "fulfilled" ? feed.value : null;

    const side = sideCol
      ? await settle(() => loadCategoryTopics(sideCol.id, { limit, excludeIds: excluded() }))
      : { status: "fulfilled", value: [] };
    mark(side.status === "fulfilled" ? side.value : []);

    const rows = [];
    for (const t of tagCols) {
      const r = await settle(() => loadTagTopics(t.name, { limit, excludeIds: excluded() }));
      const topics = r.status === "fulfilled" ? r.value : [];
      mark(topics);
      rows.push({
        name: t.name,
        label: t.label,
        topics,
        moreUrl: `/tag/${encodeURIComponent(t.name)}`,
      });
    }

    this.side =
      sideCol && side.status === "fulfilled" && side.value.length
        ? { ...sideCol, topics: side.value, moreUrl: `/c/${sideCol.id}` }
        : null;
    this.themeRows = rows.filter((r) => r.topics.length);
    } finally {
      this.loading = false;
    }
  }

  // ---- 版面 ----

  get hasAny() {
    return this.feed || this.side || this.themeRows.length;
  }

  // 只给实际出现的分区编号，缺哪块编号不跳
  get nums() {
    const f = this.feed;
    const keys = [];
    if (f?.dailyBrief || f?.brief.length || f?.picks.length) {
      keys.push("brief");
    }
    if (f?.lead || this.hasSideCol) {
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
    if (this.themeRows.length && settings.show_themes !== false) {
      keys.push("themes");
    }
    const out = {};
    keys.forEach((k, i) => (out[k] = String(i + 1).padStart(2, "0")));
    return out;
  }

  get brandTitle() {
    return settings.brand_title || "AI 雷达";
  }
  get brandSub() {
    return settings.brand_subtitle || "3 分钟读懂 AI 前沿 × 风电能源";
  }
  get scheduleNote() {
    return settings.schedule_note || "每天 08:00 · 12:00 · 16:00 自动采集 · 08:30 深度简报";
  }
  get showSearch() {
    // v0.4 默认关闭：论坛页头已有搜索
    return settings.show_search === true;
  }
  get showSectionNav() {
    return settings.show_section_nav !== false && this.sectionNavItems.length >= 2;
  }
  get feedbackUrl() {
    return String(settings.feedback_url ?? "/c/2").trim();
  }
  get mixTooltip() {
    const c = this.feed?.counts;
    return c ? `信源构成：官方源 ${c.g} · 媒体源 ${c.y} · 单一源 ${c.r}` : "";
  }

  // v0.6.0：动态简报按钮文案——避免 08:30 当期未出前把昨天的简报写成「今日」
  get dailyBriefCta() {
    const db = this.feed?.dailyBrief;
    if (!db) {
      return "阅读深度简报 →";
    }
    const raw = String(db.date || "").trim();
    const now = new Date();
    const todayIso = `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`;
    const todayMd = `${now.getMonth() + 1}月${now.getDate()}日`;
    const mIso = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(raw);
    const shortMd = mIso ? `${Number(mIso[2])}月${Number(mIso[3])}日` : raw;
    if (!raw || raw === todayIso || raw === todayMd) {
      return "阅读今日完整深度简报 →";
    }
    return `阅读 ${shortMd} 深度简报（今日期 08:30 更新）→`;
  }

  // v0.6.1：刊头下方快捷锚点目录（已移除 05 社区精选与 06 栏目更新）
  get sectionNavItems() {
    const n = this.nums;
    const defs = [
      ["brief", "趋势洞察"],
      ["lead", "技术深读"],
      ["curated", "精编"],
      ["radar", "信号雷达"],
      ["topics", "主题追踪"],
      ["stance", "技术雷达"],
      ["themes", "按主题看"],
    ];
    return defs
      .filter(([k]) => Boolean(n[k]))
      .map(([k, label]) => ({ id: `ap-sec-${k}`, num: n[k], label }));
  }

  @action
  jumpToSection(id, event) {
    event?.preventDefault?.();
    if (typeof document === "undefined") {
      return;
    }
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
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

  // v0.4.7：右栏「金风 / 能源相关」
  get sideIsEnergy() {
    return String(settings.side_source || "energy") !== "category";
  }
  get sideLabel() {
    return String(settings.side_label || "金风 / 能源相关");
  }
  get energyItems() {
    return this.sideIsEnergy ? this.feed?.energy || [] : [];
  }
  // 这 3 条是「从雷达里按能源视角挑出来的」，雷达本身仍完整保留它们。
  // 与其偷偷去重让雷达缺条，不如把关系写明白——参考站也是这么做的
  // （它会在信号块标注「含上方导读精选 4 条」）。
  get energyNote() {
    return String(settings.energy_note ?? "从下方信号雷达按能源视角挑选");
  }
  get hasSideCol() {
    return Boolean(this.side || this.energyItems.length);
  }

  get leadTwoCol() {
    return this.feed?.lead && this.hasSideCol ? "ap-sec__body ap-two ap-two--lead" : "ap-sec__body";
  }
  get briefTwoCol() {
    return (this.feed?.dailyBrief || this.feed?.brief.length) && this.feed?.picks.length
      ? "ap-sec__body ap-two"
      : "ap-sec__body";
  }

  // ---- 雷达筛选 ----

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
      mk("", "全部", f.radar.length, ""),
      ...f.qs.map((q) => mk(q.id, q.short || q.id, q.count, q.text)),
    ];
  }

  // v0.4.5：信号雷达排序（与采集器 collector/render.py 的 radar_rank 同一套规则）
  //
  // 用户要的排序：时间最新(权重5) > 信息源级别(3) > 相关度(2)。
  // 为什么不用加权求和：实测「昨天 绿标+相关3」=8.33 会压过「今天 红标+相关2」=6.33，
  // 因为 3+2=5 的合计权重正好抵消一天的时效差，用户看到的仍然是昨天的内容。
  // 所以把「时间权重5」落实成**日粒度分组优先**（今天所有条目永远在前），
  // 3:2 用在同一天内部的排序上——这样两个权重的 1.5 倍关系才真正体现出来。
  //
  // 组内分数 = 3 × 信息源级别 + 2 × (相关度/3)，绿1.0 / 黄0.5 / 红0.0。
  // 因为相关度最低档也是 2（min_relevance_to_post=2），实际效果是
  // 绿>黄>红 优先，同级别内相关度3 优先于相关度2。
  get sortedRadar() {
    const SRC = { g: 1, y: 0.5, r: 0 };
    const now = new Date();
    const dayKey = (d) =>
      `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
    const today = dayKey(now);
    const yday = new Date(now.getTime() - 86400000);

    // 分桶：今天=0，昨天=1，其余按「距今天数」；没有有效时间的一律沉底
    const bucket = (ms) => {
      if (!ms) {
        return Number.MAX_SAFE_INTEGER;
      }
      const d = new Date(ms);
      if (dayKey(d) === today) {
        return 0;
      }
      if (dayKey(d) === dayKey(yday)) {
        return 1;
      }
      return 2 + Math.floor((now.getTime() - ms) / 86400000);
    };

    return [...this.feed.radar].sort((a, b) => {
      const ba = bucket(a.publishedAt);
      const bb = bucket(b.publishedAt);
      if (ba !== bb) {
        return ba - bb;
      }
      const sa = 3 * (SRC[a.cred] ?? 0) + (2 * (a.relevance || 0)) / 3;
      const sb = 3 * (SRC[b.cred] ?? 0) + (2 * (b.relevance || 0)) / 3;
      if (sa !== sb) {
        return sb - sa;
      }
      // 同档再按精确时间新的在前（采集器补齐带时分的发布时间后才会真正起作用）
      if ((b.publishedAt || 0) !== (a.publishedAt || 0)) {
        return (b.publishedAt || 0) - (a.publishedAt || 0);
      }
      // 最后手段：按标题码点序。**不能**用 localeCompare("zh")——
      // 那会按拼音排，而与采集器侧 Python 的码点序不一致，两处排序在「完全并列」
      // 的组里会给出不同顺序（实测 38 条里 25 条不一致）。虽然只影响并列项，
      // 但会让每轮刷新的顺序看起来在无规律跳动。两边统一用码点序。
      return a.title < b.title ? -1 : a.title > b.title ? 1 : 0;
    });
  }

  get filteredRadar() {
    return this.sortedRadar.filter(
      (s) =>
        (!this.activeQ || s.questions.includes(this.activeQ)) &&
        (!this.hideSingle || s.cred !== "r")
    );
  }

  get visibleRadar() {
    const n = Number(settings.radar_initial_count) || 6;
    const list = this.filteredRadar;
    if (this.expanded) {
      return list;
    }
    // v0.6.1：未筛选且未展开的默认 6 条预览：
    //   1. 优先呈现 01/02 未曝光过的新信号，减少滑屏重复感；
    //   2. 同一站点最多占 2 张预览卡，避免周末/单日单一媒体刷满首屏；
    // 展开或按议题筛选时仍展示完整全量池，不做删减。
    if (!this.activeQ && !this.hideSingle && list.length > n) {
      const f = this.feed;
      const headKeys = new Set();
      const addKey = (t) => {
        const k = norm(t).slice(0, 14);
        if (k.length >= 6) {
          headKeys.add(k);
        }
      };
      if (f?.lead?.title) {
        addKey(f.lead.title);
      }
      for (const p of f?.picks || []) {
        addKey(p.title);
      }
      const ordered = [];
      const dup = [];
      for (const s of list) {
        const k = norm(s.title).slice(0, 14);
        if (headKeys.size && k.length >= 6 && headKeys.has(k)) {
          dup.push(s);
        } else {
          ordered.push(s);
        }
      }
      const pool = [...ordered, ...dup];
      const hostKey = (s) => {
        try {
          const h = new URL(s.url || "").hostname.toLowerCase();
          if (h) {
            return h.replace(/^www\./, "");
          }
        } catch {}
        return String(s.from || "").trim().toLowerCase();
      };
      const picked = [];
      const overflow = [];
      const counts = new Map();
      const perSourceCap = 2;
      for (const s of pool) {
        const key = hostKey(s);
        const cnt = counts.get(key) || 0;
        if (key && cnt >= perSourceCap) {
          overflow.push(s);
        } else {
          picked.push(s);
          if (key) {
            counts.set(key, cnt + 1);
          }
          if (picked.length >= n) {
            return picked;
          }
        }
      }
      return [...picked, ...overflow].slice(0, n);
    }
    return list.slice(0, n);
  }

  get canCollapseRadar() {
    const n = Number(settings.radar_initial_count) || 6;
    return this.expanded && this.filteredRadar.length > n;
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
  collapseAll() {
    this.expanded = false;
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
            {{! v0.6.0：刊头升级为科技微渐变 Hero 栏，含副标题、实时状态胶囊、信源分布细条与快捷锚点条 }}
            <header class="ap-mast">
              <div class="ap-mast__brand">
                <h2 class="ap-mast__title">{{this.brandTitle}}</h2>
                {{#if this.brandSub}}<span class="ap-mast__tagline">{{this.brandSub}}</span>{{/if}}
              </div>
              {{#if this.feed}}
                <div class="ap-mast__meta">
                  <span>{{this.feed.date}}</span>
                  {{#if this.updatedText}}
                    <span class="ap-mast__live" title={{this.scheduleNote}}>
                      <i class="ap-live-dot" aria-hidden="true"></i>{{this.updatedText}} 更新
                    </span>
                  {{/if}}
                  {{#if this.feed.curatedCount}}<span><b>{{this.feed.curatedCount}}</b> 条精编</span>{{/if}}
                  {{#if this.feed.tracking.length}}<span><b>{{this.feed.tracking.length}}</b> 条跟踪</span>{{/if}}
                  {{#if this.feed.radar.length}}
                    <span title={{this.mixTooltip}}><b>{{this.feed.radar.length}}</b> 条信号</span>
                  {{/if}}
                </div>
              {{/if}}
            </header>

            {{#if this.showSectionNav}}
              <nav class="ap-secnav" aria-label="板块快捷导航">
                {{#each this.sectionNavItems as |item|}}
                  <a class="ap-secnav__item" href="#{{item.id}}" {{on "click" (fn this.jumpToSection item.id)}}>
                    <em>{{item.num}}</em>{{item.label}}
                  </a>
                {{/each}}
              </nav>
            {{/if}}

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
              <div id="ap-sec-brief" class="ap-sec ap-sec--hot">
                <Gutter @num={{this.nums.brief}} @en="THE BRIEF" />
                <div class={{this.briefTwoCol}}>
                  {{#if this.feed.dailyBrief}}
                    <div class="ap-col ap-dbrief">
                      <h3 class="ap-kick ap-kick--hot">
                        {{this.feed.dailyBrief.title}}
                        <small>深度简报 · AI 前沿 × 能源贸易 × 全球风电</small>
                      </h3>
                      <div class="ap-dbrief__list">
                        {{#each this.feed.dailyBrief.insights as |ins|}}
                          <article class="ap-dbrief__insight">
                            <div class="ap-dbrief__head">
                              <span class="ap-dbrief__num">{{ins.num}}</span>
                              {{#if ins.heading}}
                                <h4 class="ap-dbrief__title">
                                  {{#if this.feed.dailyBrief.url}}
                                    <a href={{this.feed.dailyBrief.url}}>{{ins.heading}}</a>
                                  {{else}}
                                    {{ins.heading}}
                                  {{/if}}
                                </h4>
                              {{/if}}
                            </div>
                            {{#if ins.body}}<p class="ap-dbrief__lead">{{ins.body}}</p>{{/if}}
                          </article>
                        {{/each}}
                      </div>
                      {{#if this.feed.dailyBrief.featureTitles.length}}
                        <p class="ap-dbrief__feats">
                          <span>本期主打：</span>
                          {{#each this.feed.dailyBrief.featureTitles as |ft|}}
                            <em>{{ft}}</em>
                          {{/each}}
                        </p>
                      {{/if}}
                      {{#if this.feed.dailyBrief.url}}
                        <a class="ap-go ap-dbrief__go" href={{this.feed.dailyBrief.url}}>{{this.dailyBriefCta}}</a>
                      {{/if}}
                    </div>
                  {{else if this.feed.brief.length}}
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
                              <p class="ap-picks__sum">{{#if pick.kind}}<span class="ap-chip">{{pick.kind}}</span> {{/if}}{{#if pick.when}}<span class="ap-picks__when">{{pick.when}}</span> {{/if}}{{pick.summary}}</p>
                            </div>
                          </li>
                        {{/each}}
                      </ol>
                    </div>
                  {{/if}}
                </div>
              </div>
            {{/if}}

            {{! 02 今日焦点 + 金风 / 能源相关
                v0.4.7：右栏原先读分类 18「深度阅读」，与 06 栏目更新里的深度阅读列重复，
                改成采集器按 Q01/Q02/Q09 筛出的能源向信号（side_source=category 可切回旧行为） }}
            {{#if this.nums.lead}}
              <div id="ap-sec-lead" class="ap-sec">
                <Gutter @num={{this.nums.lead}} @en="TODAY'S LEAD" />
                <div class={{this.leadTwoCol}}>
                  {{#if this.feed.lead}}
                    <article class="ap-lead">
                      <h3 class="ap-kick ap-kick--blue">技术深读</h3>
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
                  {{#if this.energyItems.length}}
                    <aside class="ap-side ap-side--energy">
                      <h3 class="ap-kick ap-kick--blue">{{this.sideLabel}}</h3>
                      <ul class="ap-recs">
                        {{#each this.energyItems as |item|}}
                          <li>
                            {{#if item.topicUrl}}
                              <a href={{item.topicUrl}}>{{item.title}}</a>
                            {{else if item.url}}
                              <a href={{item.url}} target="_blank" rel="noopener noreferrer">{{item.title}}</a>
                            {{else}}
                              {{item.title}}
                            {{/if}}
                            <span class="ap-recs__meta">{{item.when}}{{#if item.from}} · {{item.from}}{{/if}}</span>
                          </li>
                        {{/each}}
                      </ul>
                      {{#if this.energyNote}}<p class="ap-side__note">{{this.energyNote}}</p>{{/if}}
                    </aside>
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
              <div id="ap-sec-curated" class="ap-sec">
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
              <div id="ap-sec-radar" class="ap-sec">
                <Gutter @num={{this.nums.radar}} @en="RADAR" />
                <div class="ap-sec__body">
                  <h3 class="ap-kick">
                    信号雷达 <small>{{this.feed.radar.length}} 条</small>
                    <button type="button" class="ap-link" {{on "click" this.toggleRadar}}>{{this.radarToggleText}}</button>
                  </h3>
                  {{#if this.radarOpen}}
                    <p class="ap-note">
                      自动采集每轮更新{{#if this.radarUpdatedText}}（本轮 {{this.radarUpdatedText}}）{{/if}}；按发布时间新旧优先，同日按信源级别与相关度排序。左侧绿/黄/红竖条代表官方/媒体/单一来源，未带「✓ 已核验」者为机器自动摘要，请以原文为准。
                    </p>
                    {{#if this.radarStaleHours}}
                      <p class="ap-stale">信号雷达已 {{this.radarStaleHours}} 小时未刷新，采集可能暂停。</p>
                    {{/if}}
                    {{! v0.4.5：原先有上下两层筛选（上=领域 chips，下=议题 qChips），
                         两者是独立打标维度（s.cat vs s.questions），但模型给标签时二者高度重合
                         （选型5↔Q03模型选型5、工具做法10↔Q04工具做法12、安全10↔Q07安全13），
                         等于把同一批内容用两套口径各列了一遍。删掉上层保留议题层：
                         议题是业务侧预设的 Q01–Q09，信息量更大；领域名在卡片左上角本来就有。 }}
                    <div class="ap-toolbar ap-toolbar--q">
                      {{#each this.qChips as |chip|}}
                        <button type="button" class={{chip.cls}} title={{chip.title}} aria-pressed={{chip.pressed}} {{on "click" (fn this.pickQ chip.value)}}>{{chip.name}} <em>{{chip.count}}</em></button>
                      {{/each}}
                      <button type="button" class={{this.singleToggleCls}} {{on "click" this.toggleSingle}}>隐藏单一来源</button>
                    </div>
                    <div class="ap-grid ap-grid--sig">
                      {{#each this.visibleRadar as |src|}}
                        <Signal @sig={{src}} />
                      {{/each}}
                    </div>
                    {{#if this.hiddenCount}}
                      <button type="button" class="ap-more" {{on "click" this.expandAll}}>展开其余 {{this.hiddenCount}} 条 ▾</button>
                    {{else if this.canCollapseRadar}}
                      <button type="button" class="ap-more" {{on "click" this.collapseAll}}>收起至两排 ▴</button>
                    {{/if}}
                  {{/if}}
                </div>
              </div>
            {{/if}}

            {{! 主题追踪（主题档案 skill 每周五写入） }}
            {{#if this.nums.topics}}
              <div id="ap-sec-topics" class="ap-sec">
                <Gutter @num={{this.nums.topics}} @en="TOPICS" />
                <div class="ap-sec__body">
                  <h3 class="ap-kick">主题追踪 <small>每周五更新判断 · 点主题看完整时间线</small></h3>
                  <div class="ap-topicgrid">
                    {{#each this.feed.topics as |topicItem|}}
                      <article class="ap-topic">
                        <header class="ap-topic__head">
                          <h4 class="ap-topic__name">{{#if topicItem.url}}<a href={{topicItem.url}}>{{topicItem.name}}</a>{{else}}{{topicItem.name}}{{/if}}</h4>
                          {{#if topicItem.week}}<span class="ap-topic__week ap-topic__week--{{topicItem.weekTier}}">本周 +{{topicItem.week}}</span>{{/if}}
                        </header>
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
              <div id="ap-sec-stance" class="ap-sec">
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

            {{! v0.6.1：已删除 05 社区精选与 06 栏目更新，AI 雷达页聚焦 01-04 情报看板 }}

            {{! 按主题看（默认关闭，见 settings.show_themes） }}
            {{#if this.nums.themes}}
              <div id="ap-sec-themes" class="ap-sec">
                <Gutter @num={{this.nums.themes}} @en="BY TOPIC" />
                <div class="ap-sec__body">
                  <h3 class="ap-kick">按主题看</h3>
                  <div class="ap-columns">
                    {{#each this.themeRows as |row|}}
                      <div class="ap-column">
                        <h4 class="ap-column__title"><a href={{row.moreUrl}}>{{row.label}}</a></h4>
                        <TopicList @topics={{row.topics}} />
                        <a class="ap-go" href={{row.moreUrl}}>全部 →</a>
                      </div>
                    {{/each}}
                  </div>
                </div>
              </div>
            {{/if}}

            <footer class="ap-footer">
              <span class="ap-footer__note">{{this.scheduleNote}}</span>
              {{#if this.feedbackUrl}}
                <a class="ap-footer__link" href={{this.feedbackUrl}}>推荐信源 / 反馈建议 →</a>
              {{/if}}
            </footer>
          </section>
        {{else if this.loading}}
          <section class="ap-home ap-home--loading" aria-label={{this.brandTitle}} aria-busy="true">
            <div class="ap-skel ap-skel--head"></div>
            <div class="ap-skel ap-skel--bar"></div>
            <div class="ap-skel-grid">
              <div class="ap-skel ap-skel--card"></div>
              <div class="ap-skel ap-skel--card"></div>
            </div>
            <p class="ap-loading">正在加载 {{this.brandTitle}}…</p>
          </section>
        {{/if}}
      {{/if}}
    </div>
  </template>
}
