#!/bin/bash
# 把 launchd 定时任务装到当前用户下。用法：在工程根目录执行 bash scripts/install_launchd.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents"
mkdir -p "$DEST" "$ROOT/logs"
for f in "$ROOT"/launchd/*.plist; do
  name="$(basename "$f")"
  sed "s#__ROOT__#$ROOT#g" "$f" > "$DEST/$name"
  launchctl bootout "gui/$(id -u)" "$DEST/$name" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$DEST/$name"
  echo "已安装 $name"
done
launchctl list | grep ai-radar || true
