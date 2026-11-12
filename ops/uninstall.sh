#!/usr/bin/env bash
# 卸载每日采集任务。
set -euo pipefail
LABEL="com.clauseledger.collect"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "已卸载 $LABEL"
