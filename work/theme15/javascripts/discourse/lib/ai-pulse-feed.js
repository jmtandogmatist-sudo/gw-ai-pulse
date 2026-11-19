// AI 雷达首页 · 数据读取与清洗（纯函数，不依赖 Discourse，可在 Node 下单测）
//
// 两路数据：
// 1) 数据底座帖：第一楼里放一个 ```json 代码块，schema = "air-pulse/1"。
//    v0.3 起由三方共同写入、各管各的字段：
//    - 采集器（ai-radar-collector）每轮写 radar / radar_updated_at / radar_next_at / radar_total / question_list
//    - 每日精选 skill 每天写 brief / lead / picks / curated / tracking / community / question / mainline 等
//    - 主题档案 skill 每周写 topics；技术雷达 skill 每季度写 stance
// 2) 论坛自身：按分类取最新话题（/c/{id}/l/latest.json）、热门（/hot.json），发帖即上首页。
// 所有字段都当作不可信输入处理：长度截断、枚举校验、链接只放行 http(s) 与站内相对路径。

export const SCHEMA = "air-pulse/1";

export const CRED = {
  g: { key: "g", label: "🟢 官方 + 独立" },
  y: { key: "y", label: "🟡 当事方 / 媒体" },
  r: { key: "r", label: "🔴 单一来源" },
};

const LIMITS = {
  title: 120,
  summary: 320,
  us: 200,
  label: 40,
  tag: 20,
  brief: 160,
  section: 16,
  cat: 12,
  from: 40,
  pick: 120,
  who: 30,
};
const MAX = {
  brief: 3,
  picks: 4,
  curated: 40,
  tracking: 20,
  radar: 120,
  sources: 4,
  tags: 4,
  community: 5,
  questions: 4,
  questionList: 12,
  topics: 6,
  rings: 4,
  ringItems: 8,
};

const Q_RE = /^Q\d{2}$/;
const RING_ORDER = ["采纳", "试用", "评估", "暂缓"];

export function clip(value, max) {
  if (value === null || value === undefined) {
    return "";
  }
  const s = String(value).replace(/\s+/g, " ").trim();
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

// 只放行 http(s) 与以单个 / 开头的站内路径；其余（javascript:、data:、//host）一律丢弃
export function safeUrl(u) {
  if (typeof u !== "string") {
    return "";
  }
  const s = u.trim();
  if (/^https?:\/\/[^\s]+$/i.test(s)) {
    return s;
  }
  if (/^\/(?!\/)[^\s]*$/.test(s)) {
    return s;
  }
  return "";
}

export function hostOf(u) {
  const m = /^https?:\/\/([^/?#]+)/i.exec(u || "");
  return m ? m[1].replace(/^www\./, "") : "";
}

function credOf(v) {
  return CRED[v] ? v : "r"; // 不认识的可信度一律按最保守的"单一来源"处理
}

function normSources(list) {
  if (!Array.isArray(list)) {
    return [];
  }
  const out = [];
  for (const s of list.slice(0, MAX.sources)) {
    const url = safeUrl(s?.url);
    if (!url) {
      continue;
    }
    out.push({ url, label: clip(s?.label, LIMITS.label) || hostOf(url) || "来源" });
  }
  return out;
}

function normCard(raw, kind) {
  const title = clip(raw?.title, LIMITS.title);
  if (!title) {
    return null;
  }
  const cred = credOf(raw?.cred);
  return {
    kind,
    cred,
    credLabel: CRED[cred].label,
    cls: `ap-card ap-card--${cred}`,
    section:
      clip(raw?.section, LIMITS.section) ||
      (kind === "tracking" ? "跟踪" : "精编"),
    title,
    summary: clip(raw?.summary, LIMITS.summary),
    us: kind === "curated" ? clip(raw?.us, LIMITS.us) : "",
    sources: normSources(raw?.sources),
    topicUrl: safeUrl(raw?.topic_url),
    // v0.4：对金风的相关度（日报 skill 5.3.1 起写入；旧数据为 null，保持原顺序）
    relevance: relevanceOf(raw?.relevance),
    hot: relevanceOf(raw?.relevance) === 3,
  };
}

function normSignal(raw) {
  const title = clip(raw?.title, LIMITS.title);
  if (!title) {
    return null;
  }
  const cred = credOf(raw?.cred);
  const url = safeUrl(raw?.url);
  return {
    cred,
    credLabel: CRED[cred].label,
    cls: `ap-sig ap-sig--${cred}`,
    cat: clip(raw?.cat, LIMITS.cat) || "其他",
    from: clip(raw?.from, LIMITS.from),
    title,
    summary: clip(raw?.summary, LIMITS.summary),
    url,
    host: hostOf(url),
    tags: Array.isArray(raw?.tags)
      ? raw.tags
          .slice(0, MAX.tags)
          .map((t) => clip(t, LIMITS.tag))
          .filter(Boolean)
      : [],
    topicUrl: safeUrl(raw?.topic_url),
    // v0.3 新增（采集器写入；旧数据没有这些字段时按“未审、无问题标签”处理）
    review: raw?.review === "reviewed" ? "reviewed" : "auto",
    reviewLabel: raw?.review === "reviewed" ? "已审" : "未审",
    questions: Array.isArray(raw?.questions)
      ? raw.questions.map((q) => String(q).toUpperCase()).filter((q) => Q_RE.test(q)).slice(0, MAX.questions)
      : [],
    us: clip(raw?.us, LIMITS.us),
    relevance: relevanceOf(raw?.relevance),
    // v0.4.4：信号本身的发布日期（采集器 2026-11-16 起写入）。
    // 旧数据没有该字段时退回发现时间，避免整块留空。
    publishedAt: parseWhen(raw?.published_at ?? raw?.discovered_at),
  };
}

// 把 ISO 字符串 / 毫秒数统一成毫秒时间戳；解析不了返回 null
function parseWhen(v) {
  if (v === null || v === undefined || v === "") {
    return null;
  }
  if (typeof v === "number") {
    return Number.isFinite(v) ? v : null;
  }
  const ms = Date.parse(String(v));
  return Number.isNaN(ms) ? null : ms;
}

function relevanceOf(v) {
  const n = Number(v);
  return Number.isInteger(n) && n >= 0 && n <= 3 ? n : null;
}

function dots(n) {
  return n === null ? "" : "●".repeat(n) + "○".repeat(3 - n);
}

// 主题追踪（主题档案 skill 写入 feed.topics）
function normTopic(raw) {
  const name = clip(raw?.name, 20);
  if (!name) {
    return null;
  }
  const week = Number(raw?.week);
  return {
    name,
    judge: clip(raw?.judge, 160),
    change: clip(raw?.change, 120),
    next: clip(raw?.next, 80),
    week: Number.isInteger(week) && week >= 0 ? week : null,
    questions: Array.isArray(raw?.questions)
      ? raw.questions.map((q) => String(q).toUpperCase()).filter((q) => Q_RE.test(q)).slice(0, MAX.questions)
      : [],
    url: safeUrl(raw?.url),
  };
}

// 集团技术雷达（技术雷达 skill 写入 feed.stance）
function normStance(raw) {
  if (!raw || typeof raw !== "object" || !Array.isArray(raw.rings)) {
    return null;
  }
  const rings = RING_ORDER.map((name) => {
    const r = raw.rings.find((x) => x?.name === name);
    const items = (Array.isArray(r?.items) ? r.items : [])
      .slice(0, MAX.ringItems)
      .map((it) => {
        const n = clip(it?.name, 40);
        return n ? { name: n, why: clip(it?.why, 80), url: safeUrl(it?.url), moved: clip(it?.moved, 8) } : null;
      })
      .filter(Boolean);
    return { name, rule: clip(r?.rule, 24), items, cls: `ap-ring ap-ring--${RING_ORDER.indexOf(name)}` };
  }).filter((r) => r.items.length);
  if (!rings.length) {
    return null;
  }
  return { issue: clip(raw.issue, 24), url: safeUrl(raw.url), rings };
}

function timeOrNull(v) {
  if (!v) {
    return null;
  }
  const t = Date.parse(v);
  return Number.isNaN(t) ? null : t;
}

function pad(n) {
  return String(n).padStart(2, "0");
}

// 清洗整份 feed；格式不对返回 null（调用方据此整块不渲染，不影响首页其他内容）
export function normalizeFeed(obj) {
  if (!obj || typeof obj !== "object" || obj.schema !== SCHEMA) {
    return null;
  }
  const brief = (Array.isArray(obj.brief) ? obj.brief : [])
    .slice(0, MAX.brief)
    .map((b) => {
      const text = clip(typeof b === "string" ? b : b?.text, LIMITS.brief);
      if (!text) {
        return null;
      }
      const url = typeof b === "object" ? safeUrl(b?.url) : "";
      return { text, url, source: typeof b === "object" ? clip(b?.source, LIMITS.label) || hostOf(url) : "" };
    })
    .filter(Boolean);

  const curated = (Array.isArray(obj.curated) ? obj.curated : [])
    .slice(0, MAX.curated)
    .map((c) => normCard(c, "curated"))
    .filter(Boolean);
  const tracking = (Array.isArray(obj.tracking) ? obj.tracking : [])
    .slice(0, MAX.tracking)
    .map((c) => normCard(c, "tracking"))
    .filter(Boolean);
  const radar = (Array.isArray(obj.radar) ? obj.radar : [])
    .slice(0, MAX.radar)
    .map(normSignal)
    .filter(Boolean);

  // v0.4.5：日报停用后 curated/tracking 不再产出，判断「有没有内容」不能只看这两块，
  // 否则只要 radar 之外没有旧字段就会误判成空数据、整页不渲染。
  // 注意：picks / community 在本行之后才归一化，这里只能看原始对象，
  // 直接用变量会撞上 TDZ（Cannot access before initialization）。
  const hasAnyBlock =
    curated.length || tracking.length || radar.length || brief.length ||
    (Array.isArray(obj.picks) && obj.picks.length) ||
    (Array.isArray(obj.community) && obj.community.length) ||
    Array.isArray(obj.topics) || obj.stance;
  if (!hasAnyBlock) {
    return null;
  }

  // 精编按 section 分组，保持首次出现的顺序（v0.1 兼容字段）
  const groups = [];
  for (const c of curated) {
    let g = groups.find((x) => x.name === c.section);
    if (!g) {
      g = { name: c.section, cards: [] };
      groups.push(g);
    }
    g.cards.push(c);
  }

  // 今日焦点（5.2.0 起由 skill 写入；旧数据退回第一张精编卡）
  let lead = null;
  const rawLead = obj.lead && typeof obj.lead === "object" ? obj.lead : null;
  const leadSrc = rawLead ? normCard({ ...rawLead, section: "今日焦点" }, "curated") : curated[0] || null;
  if (leadSrc) {
    lead = {
      ...leadSrc,
      title: leadSrc.title.replace(/[（(]\d{1,2}月\d{1,2}日[^）)]*[）)]\s*$/, ""),
      url: safeUrl(rawLead?.url) || safeUrl(obj.daily_url) || leadSrc.topicUrl,
    };
  }

  // 精编网格里去掉已作为“今日焦点”展示的那条，避免同屏重复
  const stripDate = (t) => String(t || "").replace(/[（(]\d{1,2}月\d{1,2}日[^）)]*[）)]\s*$/, "");
  // v0.4：按相关度稳定排序（日报 skill 已排过序时不改变顺序；旧数据没有相关度时保持原顺序）
  const curatedRest = (lead ? curated.filter((c) => stripDate(c.title) !== lead.title) : curated)
    .map((c, i) => ({ c, i }))
    .sort((a, b) => (b.c.relevance ?? -1) - (a.c.relevance ?? -1) || a.i - b.i)
    .map((x) => x.c);

  const picks = (Array.isArray(obj.picks) ? obj.picks : [])
    .map((p) => {
      const url = safeUrl(p?.url);
      const title = clip(p?.title, LIMITS.pick);
      return url && title
        ? { kind: clip(p?.kind, LIMITS.cat), title, summary: clip(p?.summary, 100), url, external: /^https?:/i.test(url) }
        : null;
    })
    .filter(Boolean)
    .slice(0, MAX.picks)
    .map((p, i) => ({ ...p, num: pad(i + 1) }));

  const community = (Array.isArray(obj.community) ? obj.community : [])
    .map((c) => {
      const url = safeUrl(c?.url);
      const title = clip(c?.title, LIMITS.title);
      return url && title ? { who: clip(c?.who, LIMITS.who), title, url } : null;
    })
    .filter(Boolean)
    .slice(0, MAX.community);

  // 统计一律由客户端按清洗后的数据重算，不信任 feed 里自带的数字
  const counts = { g: 0, y: 0, r: 0 };
  for (const x of [...curated, ...tracking, ...radar]) {
    counts[x.cred] += 1;
  }
  const total = counts.g + counts.y + counts.r;
  const pct = (n) => (total ? Math.round((n / total) * 1000) / 10 : 0);

  const catMap = new Map();
  const qMap = new Map();
  for (const s of radar) {
    catMap.set(s.cat, (catMap.get(s.cat) || 0) + 1);
    for (const q of s.questions) {
      qMap.set(q, (qMap.get(q) || 0) + 1);
    }
    s.relDots = dots(s.relevance);
    s.whenLabel = relDayLabel(s.publishedAt);
  }
  const cats = [...catMap.entries()].map(([name, count]) => ({ name, count }));

  // 问题清单：采集器把 questions.yaml 的编号与短名写进 question_list；没有时只显示编号
  const qLabels = new Map();
  for (const q of (Array.isArray(obj.question_list) ? obj.question_list : []).slice(0, MAX.questionList)) {
    const id = String(q?.id || "").toUpperCase();
    if (Q_RE.test(id)) {
      qLabels.set(id, { short: clip(q?.short, 10), text: clip(q?.text, 120) });
    }
  }
  const qs = [...qMap.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([id, count]) => ({ id, count, short: qLabels.get(id)?.short || "", text: qLabels.get(id)?.text || id }));

  const topics = (Array.isArray(obj.topics) ? obj.topics : []).slice(0, MAX.topics).map(normTopic).filter(Boolean);
  const stance = normStance(obj.stance);
  const radarTotal = Number(obj.radar_total);

  return {
    date: clip(obj.date, 24),
    issueDate: /^\d{4}-\d{2}-\d{2}$/.test(obj.issue_date || "") ? obj.issue_date : "",
    updatedAt: timeOrNull(obj.updated_at),
    nextAt: timeOrNull(obj.next_update_at),
    dailyUrl: safeUrl(obj.daily_url),
    mainline: clip(obj.mainline, 160),
    question: clip(obj.question, 160),
    brief,
    lead,
    picks,
    community,
    groups,
    curated,
    curatedRest,
    curatedCount: curated.length,
    tracking,
    radar,
    cats,
    qs,
    topics,
    stance,
    radarUpdatedAt: timeOrNull(obj.radar_updated_at),
    radarNextAt: timeOrNull(obj.radar_next_at),
    radarTotal: Number.isInteger(radarTotal) && radarTotal >= 0 ? radarTotal : null,
    reviewedCount: radar.filter((x) => x.review === "reviewed").length,
    counts,
    total,
    mix: {
      g: pct(counts.g),
      y: pct(counts.y),
      r: Math.max(0, Math.round((100 - pct(counts.g) - pct(counts.y)) * 10) / 10),
    },
  };
}

// ---- 论坛话题列表（栏目区块与热帖） ----

const ABOUT_RE = /^关于[“"「].+[”"」]类别$/;

export function formatDay(ms) {
  if (!ms) {
    return "";
  }
  const d = new Date(ms);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

// /c/{id}/l/latest.json、/hot.json 的 topic_list → 首页条目
export function normalizeTopics(json, { limit = 3, excludeIds = [], excludeTitle = null } = {}) {
  const topics = json?.topic_list?.topics;
  if (!Array.isArray(topics)) {
    return [];
  }
  const out = [];
  for (const t of topics) {
    if (out.length >= limit) {
      break;
    }
    const id = Number(t?.id);
    const title = clip(t?.fancy_title ? decodeEntities(t.fancy_title) : t?.title, LIMITS.title);
    if (!id || !title || ABOUT_RE.test(title) || t?.visible === false || excludeIds.includes(id)) {
      continue;
    }
    if (excludeTitle && excludeTitle.test(title)) {
      continue;
    }
    const slug = /^[\w-]+$/.test(t?.slug || "") ? t.slug : "topic";
    const created = timeOrNull(t?.created_at);
    out.push({
      id,
      title,
      url: `/t/${slug}/${id}`,
      image: safeUrl(t?.image_url),
      day: formatDay(created),
      views: Number.isFinite(t?.views) ? t.views : null,
      replies: Number.isFinite(t?.posts_count) ? Math.max(0, t.posts_count - 1) : null,
      categoryId: Number(t?.category_id) || null,
    });
  }
  return out;
}

function decodeEntities(s) {
  return String(s)
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

// 设置项 "18:深度阅读,19:一招一式" → [{id:18,label:"深度阅读"}]
export function parseColumns(setting) {
  return String(setting || "")
    .split(/[,，]/)
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => {
      const [id, ...rest] = x.split(/[:：]/);
      return { id: Number(id), label: clip(rest.join(":"), 12) };
    })
    .filter((c) => Number.isInteger(c.id) && c.id > 0 && c.label);
}

// ---- 底座帖解析 ----

// 从帖子原文里取第一个代码块；容忍：围栏可带或不带 json 标记；结尾围栏可以紧跟在 } 后面而不另起一行
export function extractJson(raw) {
  if (typeof raw !== "string") {
    return null;
  }
  const m = /```[a-z]*[ \t]*\r?\n([\s\S]*?)\s*```/i.exec(raw);
  const text = m ? m[1] : raw.trim();
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export function pad2(n) {
  return String(n).padStart(2, "0");
}

export function formatTime(ms) {
  if (!ms) {
    return "";
  }
  const d = new Date(ms);
  return `${d.getMonth() + 1}月${d.getDate()}日 ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

export function hoursSince(ms, now = Date.now()) {
  return ms ? (now - ms) / 3600000 : null;
}

// v0.4.4：把发布日期渲染成「今天 / 昨天 / N 天前 / 具体日期」。
// 采集器写入 published_at（信号本身发布日期），缺失时退回 discovered_at（采集时间）。
export function relDayLabel(ms, now = Date.now()) {
  if (!ms) {
    return "";
  }
  const d = new Date(ms);
  const n = new Date(now);
  const day0 = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const day1 = new Date(n.getFullYear(), n.getMonth(), n.getDate()).getTime();
  const days = Math.round((day1 - day0) / 86400000);
  if (days <= 0) {
    return "今天";
  }
  if (days === 1) {
    return "昨天";
  }
  if (days < 7) {
    return `${days} 天前`;
  }
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

// ---- 以下依赖浏览器环境 ----

const CACHE_MS = 5 * 60 * 1000;
const BLOCK_MS = 10 * 60 * 1000;
const SS_PREFIX = "ap-home:";
const cache = new Map();

// v0.4.1：网关拦截（418/429/403）后 10 分钟内不再发请求，避免越拦越多
function ssGet(k) {
  try {
    return JSON.parse(window.sessionStorage.getItem(SS_PREFIX + k) || "null");
  } catch {
    return null;
  }
}
function ssSet(k, v) {
  try {
    window.sessionStorage.setItem(SS_PREFIX + k, JSON.stringify(v));
  } catch {
    // 存储不可用时只用内存缓存
  }
}

export function isBlocked() {
  const until = ssGet("blocked_until");
  return typeof until === "number" && Date.now() < until;
}

function noteStatus(status) {
  if (status === 418 || status === 429 || status === 403) {
    ssSet("blocked_until", Date.now() + BLOCK_MS);
  }
}

async function guardedFetch(url, init) {
  if (isBlocked()) {
    throw new Error("blocked");
  }
  const r = await fetch(url, init);
  if (!r.ok) {
    noteStatus(r.status);
  }
  return r;
}

// 内存 + 会话缓存：刷新页面 5 分钟内不重复请求；被拦截期间用旧缓存（不论新旧）兜底
async function cached(key, loader) {
  const hit = cache.get(key) || ssGet("c:" + key);
  if (hit && Date.now() - hit.at < CACHE_MS) {
    return hit.value;
  }
  try {
    const value = await loader();
    const entry = { at: Date.now(), value };
    cache.set(key, entry);
    ssSet("c:" + key, entry);
    return value;
  } catch (e) {
    if (hit) {
      return hit.value;
    }
    throw e;
  }
}

async function getJson(url) {
  const r = await guardedFetch(url, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!r.ok) {
    throw new Error(`http ${r.status}`);
  }
  return r.json();
}

async function fetchRaw(topicId, postNumber) {
  // 优先 /raw（直接拿 Markdown 原文）；失败再退到 /t/{id}.json 解析 cooked
  try {
    const r = await guardedFetch(`/raw/${topicId}/${postNumber}`, {
      credentials: "same-origin",
      cache: "no-cache",
    });
    if (r.ok) {
      return await r.text();
    }
  } catch {
    // 走兜底
  }
  if (isBlocked()) {
    throw new Error("blocked");
  }
  const data = await getJson(`/t/${topicId}.json`);
  const post =
    (data?.post_stream?.posts || []).find((p) => p.post_number === postNumber) ||
    data?.post_stream?.posts?.[0];
  const doc = new DOMParser().parseFromString(post?.cooked || "", "text/html");
  return doc.querySelector("pre code")?.textContent || "";
}

export function loadFeed(topicId, postNumber = 1) {
  return cached(`feed:${topicId}/${postNumber}`, async () =>
    normalizeFeed(extractJson(await fetchRaw(topicId, postNumber)))
  );
}

export function loadCategoryTopics(categoryId, opts) {
  return cached(`cat:${categoryId}:${opts?.limit}`, async () =>
    normalizeTopics(await getJson(`/c/${categoryId}/l/latest.json`), opts)
  );
}

export function loadHotTopics(opts) {
  return cached(`hot:${opts?.limit}`, async () => {
    try {
      return normalizeTopics(await getJson("/hot.json"), opts);
    } catch (e) {
      if (isBlocked()) {
        throw e;
      }
      return normalizeTopics(await getJson("/top.json?period=weekly"), opts);
    }
  });
}
