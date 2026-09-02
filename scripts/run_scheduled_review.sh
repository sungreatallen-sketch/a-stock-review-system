#!/bin/zsh
# launchd入口：只负责插电判断；健康检查/告警/补发都在auto_review.py里做。
set -u
LOG=/Users/yage/ashare-logs/review.log
PROJ='/Users/yage/ashare-project'
mkdir -p /Users/yage/ashare-logs
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 定时复盘入口 =====" >> "$LOG"

if ! pmset -g batt | grep -q "AC Power"; then
    echo "$(date '+%H:%M:%S') 未插电，跳过" >> "$LOG"
    exit 0
fi

cd "$PROJ" || exit 1
echo "$(date '+%H:%M:%S') 已插电，交给自动复盘流程" >> "$LOG"
exec .venv/bin/python scripts/auto_review.py >> "$LOG" 2>&1
