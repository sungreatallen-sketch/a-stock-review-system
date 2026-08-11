#!/bin/zsh
# 启动/重载 复盘系统后台服务（launchd）
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/com.ashare.bot.plist" 2>/dev/null
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/com.ashare.server.plist" 2>/dev/null
echo "✅ 已启动（若提示 already loaded 属正常）"
