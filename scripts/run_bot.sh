#!/bin/zsh
# 复盘助手机器人：长连接模式
cd "$(dirname "$0")/.."
caffeinate -i -s &
exec .venv/bin/python -m app.feishu.bot
