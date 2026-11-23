import { apiInitializer } from "discourse/lib/api";
import DiscourseURL from "discourse/lib/url";
import AiPulseHome from "../components/ai-pulse-home";
import { matchHomepageUrl } from "../lib/ai-pulse-route";

const BODY_TAB_CLASS = "ap-radar-tab-active";
const BODY_HIDE_LIST_CLASS = "ap-radar-tab-hide-list";
const BODY_COMPACT_BANNER_CLASS = "ap-radar-compact-banner";

export default apiInitializer("1.8.0", (api) => {
  // feed_topic_id = 0 表示关闭：不注册任何东西，首页保持原样
  if (!Number(settings.feed_topic_id)) {
    return;
  }

  // v0.5.2：向发现页路由注册自定义查询参数（默认 radar），使 /latest?radar=1 在 SPA 跳转与 F5 刷新下都能保留
  const paramName = String(settings.radar_query_param ?? "radar").trim();
  if (paramName && typeof api.addDiscoveryQueryParam === "function") {
    api.addDiscoveryQueryParam(paramName, {
      replace: false,
      refreshModel: false,
    });
  }

  const router = api.container.lookup("service:router");

  const getCurrentUrl = () => {
    if (router?.currentURL) {
      return router.currentURL;
    }
    if (typeof window !== "undefined") {
      return `${window.location.pathname}${window.location.search}`;
    }
    return "/";
  };

  const clearControllerParam = () => {
    if (!paramName) {
      return;
    }
    try {
      const ctrl = api.container.lookup("controller:discovery/list");
      if (ctrl && ctrl.get?.(paramName) != null) {
        ctrl.set(paramName, null);
      }
    } catch {
      // 不同 Discourse 版本控制器名若有差异，静默跳过
    }
  };

  const syncTabBodyClass = () => {
    if (typeof document === "undefined") {
      return;
    }
    const { isTabMode } = matchHomepageUrl(getCurrentUrl(), settings.homepage_paths);
    const hideList = settings.hide_topic_list_on_radar_tab !== false;
    const compactBanner = settings.compact_banner_on_radar_tab !== false;
    document.body.classList.toggle(BODY_TAB_CLASS, Boolean(isTabMode));
    document.body.classList.toggle(BODY_HIDE_LIST_CLASS, Boolean(isTabMode && hideList));
    document.body.classList.toggle(
      BODY_COMPACT_BANNER_CLASS,
      Boolean(isTabMode && compactBanner)
    );

    // 切离雷达标签页后清理 controller 上的 sticky 参数，避免从【热门】回点【最新】时误回雷达页
    if (!isTabMode) {
      clearControllerParam();
    }
  };

  // 初始化时立即同步一次（覆盖直接访问 /latest?radar=1 或 F5 刷新）
  syncTabBodyClass();

  if (router?.on) {
    router.on("routeDidChange", syncTabBodyClass);
  }
  api.onPageChange(syncTabBodyClass);

  // 在 /latest?radar=1 下点击导航栏【最新】或页头 Logo 返回 /latest 或 / 时，显式清除 radar 参数
  if (typeof document !== "undefined" && paramName) {
    document.addEventListener(
      "click",
      (event) => {
        if (
          event.defaultPrevented ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey
        ) {
          return;
        }
        if (!matchHomepageUrl(getCurrentUrl(), settings.homepage_paths).isTabMode) {
          return;
        }
        const link = event.target?.closest?.("a[href]");
        if (!link) {
          return;
        }
        const inNavOrLogo = Boolean(
          link.closest(
            "#navigation-bar, .nav-pills, .d-header .title, .d-header .home-logo-wrapper-outlet"
          )
        );
        if (!inNavOrLogo) {
          return;
        }
        let parsed;
        try {
          parsed = new URL(link.getAttribute("href"), window.location.origin);
        } catch {
          return;
        }
        if (parsed.origin !== window.location.origin || parsed.searchParams.has(paramName)) {
          return;
        }
        const targetPath = parsed.pathname.replace(/\/+$/, "") || "/";
        if (targetPath === "/latest" || targetPath === "/hot" || targetPath === "/") {
          event.preventDefault();
          event.stopPropagation();
          clearControllerParam();
          if (targetPath === "/latest" && router?.transitionTo) {
            try {
              router.transitionTo("discovery.latest", {
                queryParams: { [paramName]: null },
              });
              return;
            } catch {
              // 退回 URL 跳转
            }
          }
          if (targetPath === "/hot" && router?.transitionTo) {
            try {
              router.transitionTo("discovery.hot", {
                queryParams: { [paramName]: null },
              });
              return;
            } catch {
              // 退回 URL 跳转
            }
          }
          DiscourseURL.routeTo(targetPath);
        }
      },
      true
    );
  }

  api.renderInOutlet(settings.outlet_name || "discovery-list-container-top", AiPulseHome);
});
