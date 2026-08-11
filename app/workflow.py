"""统一工作流：收盘复盘 + 次日标的预测（M1+M2）"""
import json
import logging

from .collector import Collector
from .report_builder import build_report
from .storage import Storage
from .html_report import render_html
from .config import load_config, paths

log = logging.getLogger("workflow")


def _get_cached_mcp():
    from .mcp_client import McpClient
    from .predict.cache import MCPCache, CachedMcp
    cfg = load_config()
    m = cfg["mcp"]
    mcp = McpClient(m["proxy_url"], m.get("token", ""), m["workbuddy_log_dir"])
    p = paths()
    return CachedMcp(mcp, MCPCache(p["data"] / "mcp_cache.db"))


def run_review(include_prediction: bool = True, auto_track: bool = True) -> dict:
    """执行完整复盘（采集→验证→报告），可选附标的预测
    auto_track: 先自动结算上期预测，再记录本期预测（模拟盘）"""
    p = paths()
    st = Storage(p["data"], p["reports"])
    settle_result = None
    report = build_report(Collector().collect())
    if include_prediction:
        try:
            cached = _get_cached_mcp()
            from .predict.track import Tracker
            tr = Tracker(p["data"])
            if auto_track:
                settle_result = tr.settle_pending(cached)
            from .predict.daily import predict as predict_today
            pred = predict_today(cached)
            if auto_track:
                tr.record_prediction(pred)
            report["prediction"] = {
                "status": "M3完整版",
                "date": pred["date"],
                "strategy": pred["strategy"],
                "market_view": pred.get("market_view"),
                "targets": pred["targets"],
                "top_sectors": pred["top_sectors"],
                "settle": settle_result,
            }
        except Exception as e:
            log.warning("预测生成失败，报告仍包含复盘: %s", str(e)[:150])
            report["prediction"] = {"status": "预测生成失败", "error": str(e)[:200], "targets": []}
    st.save_report(report)
    (p["reports"] / f"{report['date']}.html").write_text(render_html(report), encoding="utf-8")
    report["_settle"] = settle_result
    return report
