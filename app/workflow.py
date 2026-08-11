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


def run_review(include_prediction: bool = True) -> dict:
    """执行完整复盘（采集→验证→报告），可选附标的预测"""
    p = paths()
    st = Storage(p["data"], p["reports"])
    report = build_report(Collector().collect())
    if include_prediction:
        try:
            from .predict.daily import predict as predict_today
            pred = predict_today(_get_cached_mcp())
            report["prediction"] = {
                "status": "M3完整版",
                "date": pred["date"],
                "strategy": pred["strategy"],
                "market_view": pred.get("market_view"),
                "targets": pred["targets"],
                "top_sectors": pred["top_sectors"],
            }
        except Exception as e:
            log.warning("预测生成失败，报告仍包含复盘: %s", str(e)[:150])
            report["prediction"] = {"status": "预测生成失败", "error": str(e)[:200], "targets": []}
    st.save_report(report)
    (p["reports"] / f"{report['date']}.html").write_text(render_html(report), encoding="utf-8")
    return report
