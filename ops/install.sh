#!/usr/bin/env bash
# 安装每日采集任务（launchd，macOS 自带）。
# 这个脚本只做三件事：渲染 plist、装到 ~/Library/LaunchAgents、注册。
# 卸载见 ops/uninstall.sh。
set -euo pipefail

WS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$WS/.venv/bin/python"
LABEL="com.clauseledger.collect"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ ! -x "$PY" ]]; then
  echo "找不到 $PY，先建虚拟环境：uv venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi

mkdir -p "$WS/data/logs" "$HOME/Library/LaunchAgents"

sed -e "s|__PYTHON__|$PY|g" -e "s|__WORKSPACE__|$WS|g" \
  "$WS/ops/$LABEL.plist.tmpl" > "$DEST"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"

echo "已安装：$DEST"
echo "下次运行：每天 08:30"
echo "立刻跑一次：launchctl kickstart -k gui/$(id -u)/$LABEL"
echo "看日志：tail -f $WS/data/logs/collect.log"
