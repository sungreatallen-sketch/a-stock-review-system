"""自动复盘核心逻辑：确定目标交易日 → 未复盘则执行 → 发飞书
关键：只在"当天已收盘(>=15:30)"时才对当天复盘；凌晨/盘前用最近已收盘交易日
避免凌晨生成"当天预测"（当天还没收盘，数据不完整/错误）
"""
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import paths
from app.utils import is_trading_day, get_latest_trading_day
from scripts.check_data_sources import check_mcp, check_ths, check_tdx_direct
from scripts.send_feishu_alert import send_alert

log = logging.getLogger("auto_review")


def get_target_trade_date():
    """确定目标交易日（最近已收盘的交易日）：
    - 今天已过15:30 且是交易日 → 今天
    - 否则（凌晨/盘前/非交易日）→ 从昨天往前找最近交易日（今天未收盘不算）
    """
    from datetime import timedelta
    now = datetime.now()
    today = now.date()
    from datetime import time as _time
    if now.time() >= _time(15, 30) and is_trading_day(today):
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


def final_report_ready(date_str: str) -> bool:
    """收盘后的正式报告才算终版。
    盘中生成的报告可能缺 T 日收盘参考价，也绝不能当作 16:00 终版推送。"""
    if not report_complete(date_str):
        return False
    targets = ((json.loads((paths()["reports"] / f"{date_str}.json").read_text(encoding="utf-8"))
                .get("prediction") or {}).get("targets") or [])
    if not targets or any(not t.get("参考买入价(收盘)") for t in targets):
        return False
    meta = ((json.loads((paths()["reports"] / f"{date_str}.json").read_text(encoding="utf-8"))
             .get("meta") or {}))
    generated = str(meta.get("generated_at") or "")
    if generated[:10] != date_str:
        return False
    try:
        return datetime.fromisoformat(generated).time() >= datetime.strptime("15:30", "%H:%M").time()
    except Exception:
        return False


def sent_flag(date_str: str) -> str:
    p = paths()
    return str(p["data"] / f"last_review_sent_{date_str}.flag")


def report_sent(date_str: str) -> bool:
    """sent flag 必须绑定当前终版 report 的 generated_at，防止盘中旧版被误认为已终发。"""
    flag = Path(sent_flag(date_str))
    if not flag.exists():
        return False
    try:
        report = json.loads((paths()["reports"] / f"{date_str}.json").read_text(encoding="utf-8"))
        generated = str((report.get("meta") or {}).get("generated_at") or "")
        return bool(generated) and flag.read_text(encoding="utf-8").strip() == generated
    except Exception:
        return False


def _alert_once(kind: str, message: str):
    """同一数据源异常一天只提醒一次，避免半小时轮询刷屏。"""
    fp = paths()["data"] / f"data_source_alert_{datetime.now().strftime('%Y-%m-%d')}.json"
    state = {}
    try:
        if fp.exists():
            state = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    if state.get(kind):
        return
    send_alert(message)
    state[kind] = datetime.now().isoformat(timespec="seconds")
    fp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _settle_pending():
    """发送/生成前先结算，保证已完成 T+2 卖出窗口的标的不漏结。"""
    try:
        from app.workflow import _get_cached_mcp
        from app.predict.track import Tracker
        p = paths()
        tr = Tracker(p["data"])
        results = []
        for _ in range(30):
            result = tr.settle_pending(_get_cached_mcp())
            results.append(result)
            # 正常情况下一次会取最新待结算预测；循环兜住历史漏结。
            if not result.get("settled"):
                break
        log.info("结算结果: %s", results)
    except Exception:
        log.exception("自动结算失败，继续按已有报告发送（报告会标记未结算）")


def run_auto_review():
    target = get_target_trade_date()
    log.info("目标交易日: %s", target)

    # 16:00 才是终版推送时间。15:30 后的 StartInterval 轮询不提前抢跑；
    # 若 16:00 错过，后面任意时间触发仍会补执行。
    now = datetime.now()
    if str(now.date()) == target and now.time() < datetime.strptime("16:00", "%H:%M").time():
        log.info("%s 已收盘但未到 16:00 终版推送时间，等待定时触发", target)
        return

    already_sent = final_report_ready(target) and report_sent(target)
    if not already_sent:
        health = {"ths": check_ths(), "mcp": check_mcp(), "tdx_direct": check_tdx_direct()}
        log.info("数据源健康: ths=%s mcp=%s tdx_direct=%s",
                 health["ths"]["status"], health["mcp"]["status"], health["tdx_direct"]["status"])
        source_ready = (
            health["ths"]["status"] == "OK"
            or health["mcp"]["status"] == "OK"
            or health["tdx_direct"]["status"] == "OK"
        )
        if not source_ready:
            _alert_once(
                "all_sources_down",
                "❌ A股复盘数据源不可用：同花顺API和WorkBuddy MCP都连接失败。\n"
                "自动复盘将在5分钟后重试；若仍失败请打开WorkBuddy检查/重连MCP。"
            )
            time.sleep(300)
            health = {"ths": check_ths(), "mcp": check_mcp(), "tdx_direct": check_tdx_direct()}
            source_ready = (
                health["ths"]["status"] == "OK"
                or health["mcp"]["status"] == "OK"
                or health["tdx_direct"]["status"] == "OK"
            )
            log.info("数据源重试: ths=%s mcp=%s", health["ths"]["status"], health["mcp"]["status"])
            if not source_ready:
                log.error("数据源重试仍失败，跳过自动复盘")
                return
        if health["mcp"]["status"] != "OK":
            if health["tdx_direct"]["status"] == "OK":
                _alert_once(
                    "mcp_down_tdx_ok",
                    "⚠️ WorkBuddy MCP连接失败，已切换通达信OAuth直连。\n"
                    "复盘可继续；方便时请在WorkBuddy里重连MCP以恢复完整兜底链。"
                )
            else:
                _alert_once(
                    "mcp_down",
                    "⚠️ WorkBuddy MCP和通达信直连都失败，自动复盘将使用同花顺API兜底。\n"
                    "如需MCP消息面/交叉验证，请在WorkBuddy里重连MCP。"
                )
        if health["ths"]["status"] != "OK":
            _alert_once(
                "ths_down",
                "⚠️ 同花顺API连接失败，自动复盘将尝试WorkBuddy MCP兜底。"
            )

    # 已有终版报告 → 补发；盘中旧报告必须强制刷新，绝不能重复推送旧预测。
    if final_report_ready(target):
        if not report_sent(target):
            _settle_pending()
            log.info("%s 已复盘但未发送飞书，补发", target)
            proc = subprocess.run([sys.executable, "scripts/send_review.py", "--date", target], check=False)
            if proc.returncode == 0:
                report = json.loads((paths()["reports"] / f"{target}.json").read_text(encoding="utf-8"))
                Path(sent_flag(target)).write_text(
                    str((report.get("meta") or {}).get("generated_at") or ""), encoding="utf-8")
        else:
            log.info("%s 已复盘且已发送，跳过", target)
        return

    # 无终版报告（不存在，或只是盘中旧版）→ 执行完整复盘
    if os.path.exists(sent_flag(target)):
        os.remove(sent_flag(target))
    log.info("%s 无收盘后终版报告，执行完整复盘...", target)
    _settle_pending()
    subprocess.run([sys.executable, "run_cli.py", "review", "--force"], check=False)

    # 发飞书
    if report_complete(target):
        proc = subprocess.run([sys.executable, "scripts/send_review.py", "--date", target], check=False)
        if proc.returncode == 0:
            report = json.loads((paths()["reports"] / f"{target}.json").read_text(encoding="utf-8"))
            Path(sent_flag(target)).write_text(
                str((report.get("meta") or {}).get("generated_at") or ""), encoding="utf-8")
            log.info("%s 自动复盘完成并已发送", target)
        else:
            log.error("%s 自动复盘飞书发送失败，等待下次补发", target)
    else:
        log.error("%s 复盘后报告仍不完整，未发送", target)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_auto_review()
