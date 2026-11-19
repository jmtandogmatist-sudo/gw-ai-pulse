import { apiInitializer } from "discourse/lib/api";
import AiPulseHome from "../components/ai-pulse-home";

export default apiInitializer("1.8.0", (api) => {
  // feed_topic_id = 0 表示关闭：不注册任何东西，首页保持原样
  if (!Number(settings.feed_topic_id)) {
    return;
  }
  api.renderInOutlet(settings.outlet_name || "discovery-list-container-top", AiPulseHome);
});
