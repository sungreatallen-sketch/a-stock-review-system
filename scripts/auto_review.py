"""自动复盘：交易日北京时间16:00后处理当日终版；不跨日补发。"""
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
from app.utils import CalendarUnavailable, market_now
from app.review_delivery import (automatic_target, final_report, report_delivered, delivery_lock)
from scripts.check_data_sources import (
    check_direct_mcp, check_mcp, check_ths, check_tdx_direct,
)
from scripts.send_feishu_alert import send_alert

log = logging.getLogger("auto_review")


def get_target_trade_date():
    """自动任务只处理北京时间当天16:00后的交易日，不跨日补发。"""
    return automatic_target(market_now())


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
    try:
        fp = paths()['reports'] / f'{date_str}.json'
        return final_report(json.loads(fp.read_text(encoding='utf-8')), date_str)
    except (OSError, ValueError, TypeError):
        return False


def sent_flag(date_str: str) -> str:
    p = paths()
    return str(p["data"] / f"last_review_sent_{date_str}.flag")


def report_sent(date_str: str) -> bool:
    return report_delivered(paths()['data'], date_str)


def _alert_once(kind: str, message: str):
    """同一数据源异常一天只提醒一次，避免半小时轮询刷屏。"""
    fp = paths()["data"] / f"data_source_alert_{market_now().strftime('%Y-%m-%d')}.json"
    state = {}
    try:
        if fp.exists():
            state = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    if state.get(kind):
        return
    send_alert(message)
    state[kind] = market_now().isoformat(timespec="seconds")
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
    try:
        target = get_target_trade_date()
        if target is None:
            log.info('非交易日或未到北京时间16:00，跳过自动复盘；不补发历史报告')
            return
        with delivery_lock(paths()['data'], blocking=False) as acquired:
            if not acquired:
                log.info('其他复盘/发送任务正在执行，跳过本次轮询')
                return
            # 等待或竞争结束后再确认日期，防止午夜跨日。
            if get_target_trade_date() != target:
                return
            if report_sent(target):
                log.info('%s 已成功发送终版，跳过（报告更新不触发重发）', target)
                return
            _run_today(target)
    except (CalendarUnavailable, ValueError, OSError):
        log.exception('交易日历或发送状态异常，停止自动复盘，需检查配置/记录')


def _run_today(target):
    log.info('目标交易日: %s', target)
    already_sent = final_report_ready(target) and report_sent(target)
    if not already_sent:
        health = {
            "ths": check_ths(),
            "mcp": check_mcp(),
            "tdx_direct": check_tdx_direct(),
            "tongzhou_direct": check_direct_mcp("tongzhou"),
            "wind_direct": check_direct_mcp("wind"),
        }
        log.info("数据源健康: %s", {k: v["status"] for k, v in health.items()})
        source_ready = any(v["status"] == "OK" for v in health.values())
        if not source_ready:
            _alert_once(
                "all_sources_down",
                "❌ A股复盘数据源不可用：同花顺API和WorkBuddy MCP都连接失败。\n"
                "自动复盘将在5分钟后重试；若仍失败请打开WorkBuddy检查/重连MCP。"
            )
            time.sleep(300)
            health = {
                "ths": check_ths(),
                "mcp": check_mcp(),
                "tdx_direct": check_tdx_direct(),
                "tongzhou_direct": check_direct_mcp("tongzhou"),
                "wind_direct": check_direct_mcp("wind"),
            }
            source_ready = any(v["status"] == "OK" for v in health.values())
            log.info("数据源重试: ths=%s mcp=%s", health["ths"]["status"], health["mcp"]["status"])
            if not source_ready:
                log.error("数据源重试仍失败，跳过自动复盘")
                return
        if health["mcp"]["status"] != "OK":
            direct_ok = any(health[k]["status"] == "OK" for k in
                            ("tdx_direct", "tongzhou_direct", "wind_direct"))
            if direct_ok:
                _alert_once(
                    "mcp_down_direct_ok",
                    "⚠️ WorkBuddy MCP连接失败，已切换官方MCP直连兜底。\n"
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

    if get_target_trade_date() != target:
        log.info('等待数据源期间已跨日，取消自动任务')
        return
    if not final_report_ready(target):
        log.info('%s 尚无终版，执行当日复盘', target)
        _settle_pending()
        proc = subprocess.run([sys.executable, 'run_cli.py', 'review', '--force'],
                              cwd=str(Path(__file__).resolve().parents[1]), check=False)
        if proc.returncode != 0:
            log.error('复盘失败，禁止发送旧报告')
            return
    if not final_report_ready(target):
        log.error('%s 日期/终版/预测不合格，禁止发送', target)
        return
    from scripts.send_review import send_report, CHAT_DEFAULT
    if not send_report(target, CHAT_DEFAULT, automatic=True):
        log.error('%s 发送失败，等待当日后续重试', target)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_auto_review()
