#!/bin/zsh
launchctl bootout gui/$(id -u)/com.ashare.bot 2>/dev/null
launchctl bootout gui/$(id -u)/com.ashare.server 2>/dev/null
echo "已停止"
