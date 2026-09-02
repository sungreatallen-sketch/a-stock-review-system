#!/bin/zsh
set -u
LOG=/Users/yage/ashare-logs/review.log
mkdir -p /Users/yage/ashare-logs
echo "===== $(date '+%Y-%m-%d %H:%M:%S') scheduled review v1.5 =====" >> "$LOG"
if ! /usr/bin/pmset -g batt | /usr/bin/grep -q "AC Power"; then
    echo "$(date '+%H:%M:%S') 未插电，跳过" >> "$LOG"
    exit 0
fi
cd /Users/yage/ashare-project || exit 1
exec /Users/yage/ashare-project/.venv/bin/python /Users/yage/ashare-project/scripts/auto_review.py >> "$LOG" 2>&1
