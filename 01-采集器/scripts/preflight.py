"""只读预检：确认「论坛 / 模型 / 钥匙串 / 底座帖」四路通，不写任何数据。

与 `collector selftest` 的区别：selftest 会**真的发一条自检帖**（还要人工删），
适合验收发帖权限；这个脚本只在配置或网关变更后跑，回答一个更窄的问题——
「下一轮定时采集会不会因为外部依赖挂了而空转」。全部是 GET / 一次 chat 调用。

用法（在 01-采集器 目录下）：
    ../.venv/bin/python scripts/preflight.py
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collector.config import get_secret, load_config, validate_config  # noqa: E402
from collector.discourse import Discourse                              # noqa: E402
from collector.render import read_feed                                # noqa: E402
from collector.tagger import LLMClient                                # noqa: E402

CST = ZoneInfo("Asia/Shanghai")
FAIL = []


def step(name, fn):
    try:
        print(f"[通过] {name}：{fn()}")
        return True
    except Exception as e:  # noqa: BLE001
        FAIL.append(name)
        print(f"[失败] {name}：{type(e).__name__}: {e}")
        return False


def main() -> int:
    cfg = load_config()
    s = cfg.settings
    print(f"配置：{ROOT}\n")

    step("配置自检", lambda: "无问题" if not validate_config(cfg) else f"{len(validate_config(cfg))} 个问题")

    def keys():
        out = []
        for name in (s["discourse"]["api_key_secret"], s["llm"]["api_key_secret"]):
            out.append(f"{name}({len(get_secret(name))} 位)")
        return "、".join(out)

    step("钥匙串", keys)

    forum = Discourse(cfg)

    def cat():
        c = forum.category(s["discourse"]["signal_category_id"])
        return f"分类 {c.get('name')}（read_restricted={c.get('read_restricted')}）"

    step("论坛 · 信号分类可读", cat)

    def feed():
        pid = forum.first_post_id(s["feed"]["topic_id"])
        data, mode = read_feed(forum.post_raw(pid))
        return f"632 schema={data.get('schema')}（{mode}），radar {len(data.get('radar') or [])} 条"

    step("论坛 · 底座帖可读", feed)

    def model():
        t = time.time()
        reply = LLMClient(cfg).chat("你只回一个词。", "回复 OK", timeout=30)
        return f"{s['llm']['base_url']} · {s['llm']['model']} · {time.time() - t:.1f}s · 回「{reply.strip()[:12]}」"

    step("模型 · 一次真实调用", model)

    print()
    if FAIL:
        print(f"有 {len(FAIL)} 项失败：{'、'.join(FAIL)} —— 修好再等定时轮次，否则那一轮会空转。")
        return 1
    print(f"四路全通。下一次定时采集会正常跑（当前 {datetime.now(CST):%H:%M}）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
