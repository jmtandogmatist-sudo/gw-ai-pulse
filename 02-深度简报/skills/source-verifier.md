---
name: source-verifier
description: Verify, validate, and audit information sources, web URLs, citations, and Markdown links for daily briefs.
---
# Source Verifier（信源与链接硬审计规范）

1. **T0–T4 可信度分级**：T0 法规标准 / T1 权威机构基准 / T2 一手技术发布 / T3 知名财经与行业媒体 / T4 厂商自报或未证实宣称（须加注「厂商自报，待第三方验证」）。
2. **链接完整性**：禁止双括号 `[Text](url1)(url2)`、禁止泛型锚文本（如 `link`/`here`）、仅允许 `http(s)://` 规范链接；识别内网域名并显式标注。
3. **URL 活性与防编造**：每条公网引用必须来自实测可访问的真实检索结果，绝不猜测或虚构 URL。
4. **时效窗校验**：提取明确发布日期 `YYYY-MM-DD`，严格区分 24–48h 突发新闻与 `[追踪]` 历史后续报道。
