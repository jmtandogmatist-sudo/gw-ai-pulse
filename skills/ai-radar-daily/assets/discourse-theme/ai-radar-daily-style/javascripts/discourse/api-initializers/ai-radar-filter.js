import { apiInitializer } from "discourse/lib/api";

// 1) [wrap=air-mix]：按本帖卡片的 data-cred 统计，生成可信度分布色条
// 2) [wrap=air-radar]：生成类别按钮与“隐藏单一来源”开关
// 只读页面 DOM，不发请求；没有 air-* 区块的帖子不受影响。
export default apiInitializer((api) => {
  api.decorateCookedElement(
    (elem) => {
      elem.querySelectorAll('[data-wrap="air-mix"]').forEach((mix) => {
        if (mix.dataset.airReady) {
          return;
        }
        const cards = elem.querySelectorAll(
          '[data-wrap="air-card"][data-cred], [data-wrap="air-sig"][data-cred]'
        );
        if (!cards.length) {
          return;
        }
        mix.dataset.airReady = "1";
        const counts = { g: 0, y: 0, r: 0 };
        cards.forEach((c) => {
          if (c.dataset.cred in counts) {
            counts[c.dataset.cred] += 1;
          }
        });
        const bar = document.createElement("div");
        bar.className = "air-mixbar";
        bar.setAttribute("aria-hidden", "true");
        ["g", "y", "r"].forEach((k) => {
          if (counts[k]) {
            const seg = document.createElement("i");
            seg.className = k;
            seg.style.flex = String(counts[k]);
            bar.append(seg);
          }
        });
        mix.prepend(bar);
      });

      elem.querySelectorAll('[data-wrap="air-radar"]').forEach((radar) => {
        if (radar.dataset.airReady) {
          return;
        }
        const items = [...radar.querySelectorAll('[data-wrap="air-sig"]')];
        if (!items.length) {
          return;
        }
        radar.dataset.airReady = "1";

        let currentCat = "*";
        let hideSingle = false;
        const apply = () => {
          items.forEach((item) => {
            const catOk = currentCat === "*" || item.dataset.cat === currentCat;
            const credOk = !(hideSingle && item.dataset.cred === "r");
            item.hidden = !(catOk && credOk);
          });
        };

        const counts = new Map();
        items.forEach((item) => {
          const cat = item.dataset.cat || "其他";
          counts.set(cat, (counts.get(cat) || 0) + 1);
        });

        const bar = document.createElement("div");
        bar.className = "air-filter";
        const makeButton = (label, cat) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "btn btn-small btn-default";
          button.textContent = label;
          button.addEventListener("click", () => {
            currentCat = cat;
            bar.querySelectorAll("button").forEach((b) =>
              b.classList.toggle("is-active", b === button)
            );
            apply();
          });
          return button;
        };
        const all = makeButton(`全部 ${items.length}`, "*");
        all.classList.add("is-active");
        bar.append(all);
        counts.forEach((n, cat) => bar.append(makeButton(`${cat} ${n}`, cat)));

        const label = document.createElement("label");
        const toggle = document.createElement("input");
        toggle.type = "checkbox";
        toggle.addEventListener("change", () => {
          hideSingle = toggle.checked;
          apply();
        });
        label.append(toggle, document.createTextNode("隐藏单一来源"));
        bar.append(label);
        radar.prepend(bar);
      });
    },
    { id: "ai-radar-filter" }
  );
});
