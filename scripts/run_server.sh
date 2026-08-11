#!/bin/zsh
# 复盘报告 Web 服务（手机同 Wi-Fi 访问）
cd "$(dirname "$0")/.."
exec .venv/bin/python -m uvicorn app.server:app --host 0.0.0.0 --port 8787 --log-level warning
