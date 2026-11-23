// AI 雷达首页 v0.5.2：路由与独立标签页匹配工具（供 initializer 与组件共用）

export function normalizePath(rawPath) {
  const path = String(rawPath || "/").trim();
  if (!path || path === "/") {
    return "/";
  }
  const withSlash = path.startsWith("/") ? path : `/${path}`;
  return withSlash.replace(/\/+$/, "") || "/";
}

export function splitUrl(rawUrl) {
  const noHash = String(rawUrl || "/").split("#")[0];
  const qIdx = noHash.indexOf("?");
  if (qIdx === -1) {
    return {
      path: normalizePath(noHash),
      params: new URLSearchParams(),
      hasQuery: false,
    };
  }
  const pathPart = noHash.slice(0, qIdx);
  const queryPart = noHash.slice(qIdx + 1);
  return {
    path: pathPart ? normalizePath(pathPart) : "",
    params: new URLSearchParams(queryPart),
    hasQuery: queryPart.length > 0,
  };
}

/**
 * 判断当前 URL 是否命中 homepage_paths 规则。
 *
 * 规则支持英文逗号分隔，每项可为：
 * - 带查询参数的独立标签页路径，如 `/latest?radar=1`（或 `?radar=1`）：
 *   命中时返回 `{ matched: true, isTabMode: true }`；
 *   当规则路径是 `/latest` 时，也兼容 `/?radar=1`（Discourse 根路径别名）。
 * - 不带查询参数的普通路径，如 `/` 或 `/latest`：
 *   仅按 pathname 精确匹配，命中时返回 `{ matched: true, isTabMode: false }`。
 */
export function matchHomepageUrl(currentUrl, configuredPaths) {
  const cur = splitUrl(currentUrl || "/");
  const rules = String(configuredPaths ?? "/latest?radar=1")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  let matchedPlain = false;

  for (const rule of rules) {
    const r = splitUrl(rule);
    if (r.hasQuery) {
      if (r.path) {
        const pathOk = cur.path === r.path || (r.path === "/latest" && cur.path === "/");
        if (!pathOk) {
          continue;
        }
      }
      let paramsOk = true;
      for (const [k, expected] of r.params.entries()) {
        if (!cur.params.has(k)) {
          paramsOk = false;
          break;
        }
        const actual = cur.params.get(k);
        if (expected === "") {
          if (actual === "0" || actual === "false") {
            paramsOk = false;
            break;
          }
        } else if (actual !== expected) {
          paramsOk = false;
          break;
        }
      }
      if (paramsOk) {
        return { matched: true, isTabMode: true };
      }
    } else if (cur.path === r.path) {
      matchedPlain = true;
    }
  }

  return { matched: matchedPlain, isTabMode: false };
}
