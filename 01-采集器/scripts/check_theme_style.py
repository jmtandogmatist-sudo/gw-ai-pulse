"""检查「AI 情报站话题样式」组件是否在线（2026-11-19 新增）。

背景：2026-11-17 14:39 该组件在后台被停用（disabled_by=U8570），之后每一轮采集照常发帖、
首页照常更新，只有帖子里的卡片静默消失——没有任何一层会报错，两天后才被发现。
本脚本把三件事对一遍，任一不一致就非零退出：

  1. 后台开关：组件的 enabled
  2. 正文标记：最新一篇信号帖的 cooked 里有没有 [wrap=ais-signal] 对应的 div
  3. 实际下发：话题页 HTML 有没有加载这份组件的 CSS（common_theme_<id>_*.css）

用法：
    ../.venv/bin/python scripts/check_theme_style.py
    ../.venv/bin/python scripts/check_theme_style.py --topic 865

退出码：0 正常；1 样式缺失或不一致；2 网络/权限问题（判不了）。
"""
import argparse
import re
import sys

sys.path.insert(0, ".")
import requests

from collector.config import get_secret, load_config

NAME_KEY = "情报站话题样式"
MARK = 'data-wrap="ais-signal"'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", type=int, default=0, help="要实测的话题 ID，默认取信号分类最新一篇")
    args = ap.parse_args()

    cfg = load_config()
    s = cfg.settings["discourse"]
    base = s["base_url"].rstrip("/")
    verify = s.get("verify_tls", True)
    H = {"Api-Username": s["api_username"], "Api-Key": get_secret(s["api_key_secret"])}

    # 1) 后台开关
    r = requests.get(base + "/admin/themes.json", headers=H, timeout=25, verify=verify)
    if r.status_code != 200:
        print("取主题清单失败：HTTP", r.status_code)
        return 2
    themes = r.json().get("themes") or []
    hits = [t for t in themes if NAME_KEY in (t.get("name") or "")]
    if not hits:
        print("未找到名字含「" + NAME_KEY + "」的主题组件")
        return 1
    th = hits[0]
    tid = th["id"]
    enabled = bool(th.get("enabled"))
    print("组件        : id=%d %s | enabled=%s" % (tid, th["name"], enabled))

    # 2) 最新一篇信号帖的正文标记
    topic_id = args.topic
    if not topic_id:
        c = requests.get("%s/c/%s.json" % (base, s["signal_category_id"]), headers=H, timeout=25, verify=verify)
        topics = ((c.json().get("topic_list") or {}).get("topics") or []) if c.status_code == 200 else []
        topic_id = topics[0]["id"] if topics else 0
    if not topic_id:
        print("取不到信号帖（分类为空或无权限）")
        return 2
    t = requests.get("%s/t/%d.json" % (base, topic_id), headers=H, timeout=25, verify=verify)
    if t.status_code != 200:
        print("取话题 %d 失败：HTTP %d" % (topic_id, t.status_code))
        return 2
    posts = (t.json().get("post_stream") or {}).get("posts") or [{}]
    cooked = posts[0].get("cooked") or ""
    has_markup = MARK in cooked
    print("实测话题    : %d | 正文含 ais-signal 块=%s" % (topic_id, has_markup))

    # 3) 页面实际下发
    page = requests.get("%s/t/%d" % (base, topic_id), headers={"User-Agent": "gw-ai-pulse-check"},
                        timeout=25, verify=verify, allow_redirects=True)
    css_loaded = re.search(r"common_theme_%d_[0-9a-f]+\.css" % tid, page.text) is not None
    print("页面样式    : 加载组件 CSS=%s（HTTP %d，%d 字符）" % (css_loaded, page.status_code, len(page.text)))

    if not has_markup:
        print("提示：这篇帖子本身没带 [wrap=ais-signal]，换一篇带标记的（--topic）再测")
    print("")
    if enabled and css_loaded:
        print("判定：样式在线 → 卡片会正常显示")
        return 0
    if not enabled:
        print("判定：样式缺失 → 组件被停用。后台 → 自定义 → 主题 → 组件 → 启用即可；"
              "在此之前所有 ais-* 帖子都退化成纯段落（内容仍可读）")
        return 1
    print("判定：组件启用但页面没加载它的 CSS → 组件可能没挂到当前主题（Foundation/Horizon）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
