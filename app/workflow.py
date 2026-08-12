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


def _build_tracking(tr, settle_result) -> dict:
    """推荐跟踪数据：昨日结算 + 累计命中率统计"""
    try:
        stats = tr.stats(days=120)
        return {"settle": settle_result, "stats": stats}
    except Exception as e:
        log.warning("跟踪数据组装失败: %s", str(e)[:100])
        return {"settle": settle_result, "stats": {"count": 0}}


def run_review(include_prediction: bool = True, auto_track: bool = True) -> dict:
    """执行完整复盘（采集→验证→报告），可选附标的预测
    auto_track: 先自动结算上期预测，再记录本期预测（模拟盘）
    同日已生成报告时直接复用（避免重复采集/预测浪费 token）"""
    from datetime import date
    p = paths()
    st = Storage(p["data"], p["reports"])
    settle_result = None
    today = str(date.today())

    # 同日内重复请求：复用已生成报告
    try:
        from .predict.cache import MCPCache, CachedMcp
        from .predict.backtest import Backtest, INDEX_TICKER
        cached = _get_cached_mcp()
        resp = cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                           {"ticker": INDEX_TICKER, "market": "index", "end_date": today, "limit": 2})
        pts = sorted({x["time"] for x in ((resp or {}).get("data") or {}).get("points") or []})
        latest_day = pts[-1] if pts else today
        existing = st.load_report(latest_day)
        gen_day = ((existing or {}).get("meta") or {}).get("generated_at", "")[:10]
        if existing and gen_day == today:
            log.info("今日报告已存在(%s)，直接复用", latest_day)
            if auto_track:
                from .predict.track import Tracker
                tr = Tracker(p["data"])
                settle_result = tr.settle_pending(cached)
                existing["prediction"] = dict(existing.get("prediction") or {})
                existing["prediction"]["settle"] = settle_result
                existing["tracking"] = _build_tracking(tr, settle_result)
                st.save_report(existing)
            existing["_settle"] = settle_result
            return existing
    except Exception as e:
        log.warning("报告复用检查失败，重新生成: %s", str(e)[:120])

    report = build_report(Collector().collect())
    if include_prediction:
        from .predict.track import Tracker
        tr = Tracker(p["data"])
        try:
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
        # 推荐跟踪（昨日结算+累计命中率）无论预测是否成功都嵌入
        if auto_track:
            report["tracking"] = _build_tracking(tr, settle_result)
    st.save_report(report)
    (p["reports"] / f"{report['date']}.html").write_text(render_html(report), encoding="utf-8")
    report["_settle"] = settle_result
    return report
