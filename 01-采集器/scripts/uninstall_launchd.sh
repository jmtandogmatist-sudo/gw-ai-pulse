#!/bin/bash
# 停掉并移除全部定时任务（一键停用）
set -euo pipefail
for f in "$HOME"/Library/LaunchAgents/com.goldwind.ai-radar.*.plist; do
  [ -e "$f" ] || continue
  launchctl bootout "gui/$(id -u)" "$f" 2>/dev/null || true
  rm -f "$f"
  echo "已移除 $(basename "$f")"
done
