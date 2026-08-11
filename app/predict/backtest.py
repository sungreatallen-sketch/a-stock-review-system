"""回测框架：T 日收盘前选股买入 → T+1 日开盘卖出
数据：同舟 MCP（候选池/打分/K线），结果缓存于 SQLite
"""
import logging
import statistics
from datetime import date, timedelta

from .candidate_pool import CandidatePool
from .scoring import score_pool

log = logging.getLogger("backtest")

INDEX_TICKER = "000001.SH"
MIN_RET = -0.20   # 收益截断（数据异常保护）


def _points(resp: dict) -> list:
    data = (resp or {}).get("data") or {}
    return data.get("points") or []


class Backtest:
    def __init__(self, mcp):
        self.mcp = mcp
        self.pool_builder = CandidatePool(mcp)

    # ---------- 交易日历 ----------
    def trading_days(self, end_date: str, count: int) -> list:
        """从指数日线取最近 count 个交易日（含 end_date 向前）"""
        resp = self.mcp.call(
            "tongzhou-fin-research_fin_data__get_kline_series",
            {"ticker": INDEX_TICKER, "market": "index",
             "end_date": end_date, "limit": count + 5})
        days = [p["time"] for p in _points(resp)]
        days.sort()
        return days[-count:]

    # ---------- 个股 K 线 ----------
    def _kline(self, ticker: str, end_date: str, start_date: str = None, limit: int = 20):
        args = {"ticker": ticker, "market": "a_stock", "end_date": end_date, "limit": limit}
        if start_date:
            args["start_date"] = start_date
        return self.mcp.call("tongzhou-fin-research_fin_data__get_kline_series", args)

    def _buy_sell(self, ticker: str, day_t: str, day_t1: str):
        """返回 (buy_price=T收盘, sell_price=T+1开盘, 一字板?)"""
        resp = self._kline(ticker, day_t1)
        pts = {p["time"]: p for p in _points(resp)}
        pt_t = pts.get(day_t)
        pt_t1 = pts.get(day_t1)
        if not pt_t or not pt_t1:
            return None
        buy = pt_t.get("close")
        sell = pt_t1.get("open")
        if not buy or not sell:
            return None
        # 一字板：T 日 open==close 且 接近涨停（涨跌幅由 prev close 判断较麻烦，用 open==high==low==close 近似）
        sealed = (abs((pt_t.get("open") or 0) - buy) < 1e-6
                  and abs((pt_t.get("high") or 0) - buy) < 1e-6
                  and abs((pt_t.get("low") or 0) - buy) < 1e-6
                  and buy > 0)
        return {"buy": buy, "sell": sell, "sealed": sealed}

    # ---------- 主流程 ----------
    def run(self, end_date: str, days: int = 30, top_n: int = 3,
            index_filter: float = None, score_fn=None, strategy=None) -> dict:
        """index_filter: 指数5日涨跌幅阈值（如 0.0 = 仅当指数5日为正才交易；None = 不过滤）
        score_fn: 自定义打分函数 score_fn(pool, top_n) -> list，默认 score_pool
        strategy: 若提供则用 strategy.select(pool, day_t, day_t1, top_n) 选股"""
        score_fn = score_fn or score_pool
        trading = self.trading_days(end_date, days + 1)
        index_map = self._index_5d_returns(trading)
        trades = []
        daily = []
        errors = 0
        skipped = 0
        for i in range(len(trading) - 1):
            t = trading[i]
            t1 = trading[i + 1]
            if index_filter is not None:
                r5 = index_map.get(t)
                if r5 is None or r5 < index_filter:
                    skipped += 1
                    daily.append({"date": t, "picks": 0, "avg_ret": None, "names": [],
                                  "skipped": f"指数5日涨跌 {r5}% < {index_filter}%" if r5 is not None else "无指数数据"})
                    continue
            try:
                pool = self.pool_builder.build(t)
                if strategy is not None:
                    top = strategy.select(pool, t, t1, top_n=top_n)
                else:
                    top = score_fn(pool, top_n=top_n)
            except Exception as e:
                log.warning("回测 %s 候选池失败: %s", t, str(e)[:150])
                errors += 1
                continue
            day_trades = []
            for pick in top:
                bs = self._buy_sell(pick["ticker"], t, t1)
                if not bs or bs["sealed"]:
                    continue
                ret = bs["sell"] / bs["buy"] - 1
                if ret < MIN_RET:   # 数据异常保护
                    continue
                day_trades.append({
                    "date": t, "next_date": t1,
                    "ticker": pick["ticker"], "name": pick["name"],
                    "industry": pick.get("industry"), "sector": pick.get("sector_name"),
                    "score": pick["score"], "factors": pick["factors"],
                    "change_ratio": pick.get("change_ratio"),
                    "amount": pick.get("amount"),
                    "turnover_rate": pick.get("turnover_rate"),
                    "market_cap": pick.get("market_cap"),
                    "buy": round(bs["buy"], 2), "sell": round(bs["sell"], 2),
                    "ret": round(ret * 100, 2),
                })
            trades.extend(day_trades)
            avg = statistics.mean([x["ret"] for x in day_trades]) if day_trades else None
            daily.append({"date": t, "picks": len(day_trades), "avg_ret": round(avg, 2) if avg is not None else None,
                          "names": [x["name"] for x in day_trades]})

        stats = self._stats(trades, daily)
        stats["benchmark"] = self._index_benchmark(trading)
        stats["meta"] = {"window": f"{trading[0]} ~ {trading[-2]}", "trading_days": len(trading) - 1,
                         "errors": errors, "skipped": skipped, "index_filter": index_filter, "top_n": top_n}
        return {"stats": stats, "trades": trades, "daily": daily}

    def _index_5d_returns(self, trading: list) -> dict:
        """每个交易日 T 的指数 5 日累计涨幅（用于择时过滤）"""
        resp = self.mcp.call(
            "tongzhou-fin-research_fin_data__get_kline_series",
            {"ticker": INDEX_TICKER, "market": "index",
             "start_date": trading[0], "end_date": trading[-1], "limit": len(trading) + 10})
        pts = sorted(_points(resp), key=lambda x: x["time"])
        out = {}
        for i in range(5, len(pts)):
            c5 = pts[i - 5]["close"]
            if c5:
                out[pts[i]["time"]] = round((pts[i]["close"] / c5 - 1) * 100, 2)
        return out

    def _index_benchmark(self, trading: list) -> dict:
        """同口径基准：每个交易日收盘买上证指数、次日开盘卖"""
        resp = self.mcp.call(
            "tongzhou-fin-research_fin_data__get_kline_series",
            {"ticker": INDEX_TICKER, "market": "index",
             "start_date": trading[0], "end_date": trading[-1], "limit": len(trading) + 10})
        pts = {p["time"]: p for p in _points(resp)}
        rets = []
        for i in range(len(trading) - 1):
            a, b = pts.get(trading[i]), pts.get(trading[i + 1])
            if a and b and a.get("close") and b.get("open"):
                rets.append((b["open"] / a["close"] - 1) * 100)
        if not rets:
            return {"count": 0, "note": "无基准数据"}
        eq = 1.0
        for r in rets:
            eq *= (1 + r / 100)
        return {
            "count": len(rets),
            "avg_ret": round(statistics.mean(rets), 2),
            "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
            "total_return": round((eq - 1) * 100, 2),
            "note": "上证指数 收盘买/次日开盘卖 同口径",
        }

    @staticmethod
    def _stats(trades: list, daily: list) -> dict:
        if not trades:
            return {"count": 0, "note": "无交易样本"}
        rets = [t["ret"] for t in trades]
        win = [r for r in rets if r > 0]
        eq = 1.0
        curve = []
        for r in rets:
            eq *= (1 + r / 100)
            curve.append(eq)
        peak = -1e9
        max_dd = 0
        for v in curve:
            peak = max(peak, v)
            dd = (peak - v) / peak
            max_dd = max(max_dd, dd)
        # 每日无重叠对比：直接用 trade 平均 vs 每日等权
        return {
            "count": len(trades),
            "win_rate": round(len(win) / len(rets) * 100, 1),
            "avg_ret": round(statistics.mean(rets), 2),
            "median_ret": round(statistics.median(rets), 2),
            "best_ret": round(max(rets), 2),
            "worst_ret": round(min(rets), 2),
            "total_return": round((eq - 1) * 100, 2),
            "max_drawdown": round(max_dd * 100, 2),
            "avg_ret_per_day": round(statistics.mean([d["avg_ret"] for d in daily if d["avg_ret"] is not None] or [0]), 2),
            "excess_vs_index": None,
        }
