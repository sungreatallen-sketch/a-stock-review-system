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


def _build_tracking(tr, settle_result, trade_date: str = None) -> dict:
    """推荐跟踪数据：昨日结算 + 累计命中率统计 + 昨日预测（含未结算）
    trade_date: 当前交易日，用于获取"上一个交易日"的预测记录"""
    try:
        from datetime import date as _date
        stats = tr.stats(days=120)
        # 获取"昨日"的预测记录（trade_date 之前的最新预测，不包括 trade_date 本身）
        # 这样确保"昨日推荐标的"显示的是昨天推荐的，而不是今天刚生成的
        today_str = trade_date or str(_date.today())
        latest = tr.latest_prediction()
        latest_pred = None
        pred_date = None
        if latest:
            pred_date = latest[0]
            # 如果最新预测就是今天的，跳过，找昨天的
            if pred_date >= today_str:
                # 从数据库找 today_str 之前的最新预测
                conn = tr._conn()
                row = conn.execute(
                    "SELECT date, targets FROM predictions WHERE date < ? ORDER BY date DESC LIMIT 1",
                    (today_str,)
                ).fetchone()
                conn.close()
                if row:
                    pred_date, targets_json = row
                    import json as _json
                    pred_data = _json.loads(targets_json)
                    latest_pred = {
                        "date": pred_date,
                        "targets": pred_data.get("targets", []),
                    }
            else:
                import json as _json
                pred_data = _json.loads(latest[1])
                latest_pred = {
                    "date": pred_date,
                    "targets": pred_data.get("targets", []),
                }
        # 补充：查询已结算结果（即使 settle_pending 返回"无可结算"，也要读取已结算数据）
        if latest_pred and pred_date:
            try:
                conn = tr._conn()
                rows = conn.execute(
                "SELECT target_code, target_name, buy_price, sell_close, ret_close, "
                "ret, buy_date, sell_date, reference_price, status "
                    "FROM prediction_results WHERE date=? ORDER BY id",
                    (pred_date,)
                ).fetchall()
                conn.close()
                if rows:
                    settle_detail = {
                        "pred_date": pred_date,
                        "results": [
                            {
                                "code": r[0], "name": r[1],
                                "buy_price": r[2], "sell_price": r[3],
                                "ret_close": r[4], "ret": r[5],
                                "status": r[9],
                                "buy_date": r[6], "sell_date": r[7],
                                "reference_price": r[8],
                            } for r in rows
                        ],
                    }
                    # 覆盖 settle_result，显示真实的结算数据
                    settle_result = settle_detail
            except Exception as e:
                log.warning("读取已结算结果失败: %s", str(e)[:100])
        return {"settle": settle_result, "stats": stats, "latest_prediction": latest_pred}
    except Exception as e:
        log.warning("跟踪数据组装失败: %s", str(e)[:100])
        return {"settle": settle_result, "stats": {"count": 0}}


def _report_ok(report: dict) -> bool:
    """报告是否'完整可用'：板块有数据 且 预测有标的（否则视为坏报告，需重生成）"""
    if not report:
        return False
    sr = report.get("sector_rank") or []
    if not sr:
        return False
    pred = report.get("prediction") or {}
    if pred.get("status") != "M3完整版" or not pred.get("targets"):
        return False
    return True


def _mcp_available() -> bool:
    """轻量探测 MCP 代理端口是否可达（不调数据接口）"""
    try:
        from .mcp_client import discover_proxy
        import socket
        url, _ = discover_proxy("~/.workbuddy/logs")
        if not url:
            return False
        port = int(url.rsplit(":", 1)[1].split("/")[0])
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except Exception:
        return False


def _attach_compliance(report: dict, pre: dict = None) -> dict:
    """按执行规则对报告做合规自检并挂到 report['compliance']"""
    try:
        from .rules import check_rules
        tracking = report.get("tracking") or {}
        tstats = tracking.get("stats") or {}
        recent = tstats.get("recent") or []
        has_close = any(r.get("ret_close") is not None for r in recent)
        pred = report.get("prediction") or {}
        targets = pred.get("targets") or []
        has_sell_plan = any((t.get("止损") or t.get("卖出计划") or t.get("卖点")
                             or t.get("stop_loss") or t.get("sell_target")) for t in targets)
        ctx = {
            "rules_preloaded": bool((pre or {}).get("ok", True)),
            "mcp_available": _mcp_available(),
            "source_available": _mcp_available() or bool(report.get("source")),
            "sectors_count": len(report.get("sector_rank") or []),
            "prediction_targets": len(targets),
            "candidate_count": pred.get("candidate_count", len(targets)),
            "sector_window_ok": str(pred.get("sector_window", "")).startswith("10日"),
            "tracking_count": tstats.get("count", 0),
            "has_close_price": has_close,
            "news_has_lhb": bool(pred.get("news_has_lhb", False)),
            "has_sell_plan": has_sell_plan,
            "report_complete": _report_ok(report),
            "data_sources": report.get("source") or [],
            "notes": [],
        }
        report["compliance"] = check_rules(ctx)
    except Exception as e:
        report["compliance"] = {"version": "1.0", "items": [], "summary": {"status": f"自检失败:{str(e)[:80]}"}}
    return report


def run_review(include_prediction: bool = True, auto_track: bool = True, force: bool = False) -> dict:
    """执行完整复盘（采集→验证→报告），可选附标的预测
    auto_track: 先自动结算上期预测，再记录本期预测（模拟盘）
    同日已生成报告且完整时直接复用（避免重复采集/预测浪费 token）；不完整则重生成"""
    from datetime import date
    from .rules import preload_rules
    from .utils import is_trading_day, get_latest_trading_day
    
    # 交易日检查
    today_date = date.today()
    if not is_trading_day(today_date):
        latest_trade = get_latest_trading_day(today_date)
        log.warning("今天 %s 不是交易日，使用最近交易日: %s", today_date, latest_trade)
        # 非交易日时使用最近交易日继续执行（不返回提示）
    
    pre = preload_rules()          # R30：运行前预读执行规则
    p = paths()
    st = Storage(p["data"], p["reports"])
    settle_result = None
    today = str(date.today())

    # 同日内重复请求：仅当报告完整时才复用
    try:
        from .predict.cache import MCPCache, CachedMcp
        from .predict.backtest import Backtest, INDEX_TICKER
        cached = _get_cached_mcp()
        resp = cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                           {"ticker": INDEX_TICKER, "market": "index", "end_date": today, "limit": 2})
        # 类型检查：resp 可能是字符串（MCP 返回异常）
        if isinstance(resp, dict):
            pts = sorted({x["time"] for x in ((resp or {}).get("data") or {}).get("points") or []})
        else:
            log.warning("MCP 返回非 dict 类型: %s", type(resp).__name__)
            pts = []
        latest_day = pts[-1] if pts else today
        existing = st.load_report(latest_day)
        gen_day = ((existing or {}).get("meta") or {}).get("generated_at", "")[:10]
        if existing and gen_day == today and _report_ok(existing) and not force:
            log.info("今日报告已存在且完整(%s)，直接复用", latest_day)
            if auto_track:
                from .predict.track import Tracker
                tr = Tracker(p["data"])
                settle_result = tr.settle_pending(cached)
                existing["prediction"] = dict(existing.get("prediction") or {})
                existing["prediction"]["settle"] = settle_result
                existing["tracking"] = _build_tracking(tr, settle_result, trade_date=existing.get("date"))
                st.save_report(existing)
            existing["_settle"] = settle_result
            return _attach_compliance(existing, pre)
    except Exception as e:
        log.warning("报告复用检查失败，重新生成: %s", str(e)[:120])

    collector_result = Collector().collect()
    report = build_report(collector_result)
    trade_date = report.get("date")  # 使用 collector 找到的交易日
    log.info("collector 找到交易日: %s", trade_date)
    
    if include_prediction:
        from .predict.track import Tracker
        tr = Tracker(p["data"])
        try:
            if auto_track:
                settle_result = tr.settle_pending(cached)
            from .predict.daily import predict as predict_today
            # R11：目标交易日已有预测则复用，不重新生成/覆盖（防止数据源滞后误判覆盖用户已看到的预测）
            pred = tr.get_prediction(trade_date)
            if pred:
                log.info("R11: %s 已有预测，直接复用（不覆盖）", trade_date)
            else:
                pred = predict_today(cached, target_date=trade_date)  # 传入 collector 找到的日期
            if auto_track:
                tr.record_prediction(pred)
            report["prediction"] = {
                "status": "M3完整版",
                "date": pred["date"],
                "strategy": pred["strategy"],
                "strategy_version": pred.get("strategy_version", "v1.0"),
                "filtered_out": pred.get("filtered_out", []),
                "market_view": pred.get("market_view"),
                "targets": pred["targets"],
                "top_sectors": pred["top_sectors"],
                "sector_window": pred.get("sector_window"),
                "news_has_lhb": pred.get("news_has_lhb", False),
                "settle": settle_result,
            }
        except Exception as e:
            log.warning("预测生成失败，报告仍包含复盘: %s", str(e)[:150])
            report["prediction"] = {"status": "预测生成失败", "error": str(e)[:200], "targets": []}
        # 推荐跟踪（昨日结算+累计命中率）无论预测是否成功都嵌入
        if auto_track:
            report["tracking"] = _build_tracking(tr, settle_result, trade_date=report.get("date"))
    report = _attach_compliance(report, pre)
    # === 报告校验（防御层 V01-V08） ===
    from .report_validator import validate_and_block
    can_send, val_errors = validate_and_block(report)
    report["_validation"] = {
        "passed": can_send,
        "errors": [str(e) for e in val_errors if e.severity == "error"],
        "warnings": [str(e) for e in val_errors if e.severity == "warning"],
    }
    if not can_send:
        log.error("报告校验未通过: %s", report["_validation"]["errors"])
    st.save_report(report)
    (p["reports"] / f"{report['date']}.html").write_text(render_html(report), encoding="utf-8")
    report["_settle"] = settle_result
    # === 结构化日志（追溯层） ===
    from .review_logger import log_review_run
    log_review_run(report)
    return report
