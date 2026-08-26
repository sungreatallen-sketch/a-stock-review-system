"""自动复盘核心逻辑：确定目标交易日 → 未复盘则执行 → 发飞书
关键：只在"当天已收盘(>=15:30)"时才对当天复盘；凌晨/盘前用最近已收盘交易日
避免凌晨生成"当天预测"（当天还没收盘，数据不完整/错误）
"""
import json
import logging
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import paths
from app.utils import is_trading_day, get_latest_trading_day

log = logging.getLogger("auto_review")


def get_target_trade_date():
    """确定目标交易日（最近已收盘的交易日）：
    - 今天已过15:30 且是交易日 → 今天
    - 否则（凌晨/盘前/非交易日）→ 从昨天往前找最近交易日（今天未收盘不算）
    """
    from datetime import timedelta
    now = datetime.now()
    today = now.date()
    if now.hour >= 15 and now.minute >= 30 and is_trading_day(today):
        return today.strftime("%Y-%m-%d")
    # 今天未收盘，从昨天往前找最近已收盘交易日
    d = today - timedelta(days=1)
    for _ in range(10):
        if is_trading_day(d):
            return d.strftime("%Y-%m-%d")
        d = d - timedelta(days=1)
    return today.strftime("%Y-%m-%d")  # 兜底


def report_complete(date_str: str) -> bool:
    """检查报告是否存在且预测完整"""
    p = paths()
    fp = p["reports"] / f"{date_str}.json"
    if not fp.exists():
        return False
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
        pred = d.get("prediction", {})
        return bool(pred.get("status") == "M3完整版" and pred.get("targets"))
    except Exception:
        return False


def sent_flag(date_str: str) -> str:
    p = paths()
    return str(p["data"] / f"last_review_sent_{date_str}.flag")


def run_auto_review():
    target = get_target_trade_date()
    log.info("目标交易日: %s", target)

    # 已复盘 → 未发送则补发，否则跳过
    if report_complete(target):
        if not os.path.exists(sent_flag(target)):
            log.info("%s 已复盘但未发送飞书，补发", target)
            subprocess.run([sys.executable, "scripts/send_review.py", "--date", target], check=False)
            open(sent_flag(target), "w").close()
        else:
            log.info("%s 已复盘且已发送，跳过", target)
        return

    # 未复盘 → 执行完整复盘
    log.info("%s 未复盘，执行完整复盘...", target)
    subprocess.run([sys.executable, "run_cli.py", "review", "--force"], check=False)

    # 发飞书
    if report_complete(target):
        subprocess.run([sys.executable, "scripts/send_review.py", "--date", target], check=False)
        open(sent_flag(target), "w").close()
        log.info("%s 自动复盘完成并已发送", target)
    else:
        log.error("%s 复盘后报告仍不完整，未发送", target)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_auto_review()
