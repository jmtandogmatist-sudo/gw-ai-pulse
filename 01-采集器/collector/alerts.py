"""飞书自定义机器人告警。没配置 webhook 时只写日志。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import sys
import time

import requests

from .config import Config, get_secret

log = logging.getLogger("collector.alerts")


class Alerter:
    def __init__(self, cfg: Config, dry_run: bool = False):
        a = cfg.settings.get("alerts", {})
        self.enabled = a.get("enabled", True) and not dry_run
        self.webhook_name = a.get("webhook_secret", "feishu-webhook")
        self.sign_name = a.get("sign_secret")  # 机器人开了签名校验时填钥匙串里的条目名
        self.prefix = a.get("prefix", "【AI 雷达采集器】")
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        msg = self.prefix + text
        self.sent.append(msg)
        log.warning("告警：%s", msg)
        if not self.enabled:
            return
        # gw-ai-pulse（2026-11-17）：原先没有 webhook 时只 log.error，
        # 而 launchd 的 .err 文件长期是 0 字节，等于告警静默——出事没人知道。
        # 现在把「配置缺失」和「发送失败」都显式打到 stderr，至少能在日志里看见。
        try:
            url = get_secret(self.webhook_name)
        except Exception as e:  # noqa: BLE001
            print(f"[alerts] 未配置 webhook（钥匙串 {self.webhook_name}），"
                  f"本条告警仅记录日志：{e}", file=sys.stderr)
            return
        try:
            body = {"msg_type": "text", "content": {"text": msg}}
            if self.sign_name:
                ts = str(int(time.time()))
                key = f"{ts}\n{get_secret(self.sign_name)}".encode("utf-8")
                body["timestamp"] = ts
                body["sign"] = base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode()
            r = requests.post(url, json=body, timeout=10)
            if r.status_code >= 400 or (r.text or "").strip() not in ("", '{"code":0,"msg":"success","data":{}'):
                print(f"[alerts] 飞书返回异常 {r.status_code}: {r.text[:200]}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 —— 告警失败不能拖垮主流程
            print(f"[alerts] 告警发送失败：{e}", file=sys.stderr)
            log.error("告警发送失败：%s", e)
