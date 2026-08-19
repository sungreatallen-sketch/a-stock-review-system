"""每日预测（M3 完整版）：
规则候选池(板块+个股+资金+量比过滤) → 消息面扫描 → DeepSeek 综合研判 → Top3
"""
import json
import logging

from .candidate_pool import CandidatePool
from .scoring import score_pool
from .strategy import Strategy, compute_vol_ratio, STRATEGY_VERSION
from .backtest import Backtest
from .news import NewsScanner
from .judge import judge
from ..config import paths

log = logging.getLogger("daily")


def _kline_lookup_factory(cached):
    def lookup(ticker, end_date):
        return cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                           {"ticker": ticker, "market": "a_stock", "end_date": end_date, "limit": 12})
    return lookup


def _market_context(target_date: str) -> dict:
    """从当日复盘报告取市场环境（存在则用，不存在返回空）"""
    p = paths()
    fp = p["reports"] / f"{target_date}.json"
    if not fp.exists():
        return {}
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
        mi = d.get("market_index") or {}
        emo = d.get("emotion") or {}
        return {
            "上证指数": mi.get("上证指数", {}).get("收盘价"),
            "上证涨跌幅%": mi.get("上证指数", {}).get("涨跌幅%"),
            "创业板指": mi.get("创业板指", {}).get("收盘价"),
            "创业板涨跌幅%": mi.get("创业板指", {}).get("涨跌幅%"),
            "涨停": emo.get("涨停数量"), "跌停": emo.get("跌停数量"),
            "最高连板": emo.get("最高连板"),
        }
    except Exception:
        return {}


def predict(cached, target_date: str = None, use_llm: bool = True) -> dict:
    """生成最新交易日的 Top3 预测"""
    from datetime import date
    bt = Backtest(cached)
    end = target_date or str(date.today())
    trading = bt.trading_days(end, 2)
    t = trading[-1]
    pool = CandidatePool(cached).build(t)
    kline_lookup = _kline_lookup_factory(cached)
    strat = Strategy(cached, score_pool, kline_lookup)
    top5 = strat.select(pool, t, None, top_n=5)

    # 补齐 K 线细节（量比/买入价）
    for pick in top5:
        resp = kline_lookup(pick["ticker"], t)
        pts = {p["time"]: p for p in ((resp or {}).get("data") or {}).get("points") or []}
        pt = pts.get(t)
        vr = compute_vol_ratio(resp, t) if pt else None
        pick["参考买入价(收盘)"] = pt.get("close") if pt else None
        pick["量比"] = round(vr, 2) if vr else None
        pick["逻辑"] = _build_logic(pick, vr)

    # 消息面（含龙虎榜 R24）
    from .alt_data import recent_lhb
    lhb_map = {}
    try:
        lhb_map = recent_lhb()
    except Exception as e:
        log.warning("龙虎榜获取失败: %s", str(e)[:120])
    scanner = NewsScanner(cached)
    news = {}
    for pick in top5:
        code = pick["ticker"].split(".")[0]
        news[code] = scanner.scan(code, pick["name"], lhb_map=lhb_map)

    # 市场环境（来自当日复盘报告）
    market = _market_context(t)

    # LLM 研判
    llm_result = None
    targets = []
    if use_llm and top5:
        try:
            cands = [{
                "ticker": p["ticker"], "name": p["name"], "industry": p.get("industry"),
                "sector": p.get("sector_name"), "change_ratio": p.get("change_ratio"),
                "amount": p.get("amount"), "turnover_rate": p.get("turnover_rate"),
                "score": p.get("score"), "factors": p.get("factors"), "逻辑": p.get("逻辑"),
            } for p in top5]
            llm_result = judge(cands, news, market)
            targets = llm_result.get("targets") or []
            # LLM 失败/空输出时回退：规则候选前3（保证 R10 3只不缺失）
            if not targets:
                targets = [{
                    "code": p["ticker"], "name": p["name"], "reason": p.get("逻辑") or "规则打分排序",
                    "risk": "LLM研判失败，基于规则候选回退", "confidence": "中",
                    "参考买入价(收盘)": p.get("参考买入价(收盘)"), "量比": p.get("量比"),
                    "板块": p.get("sector_name"), "评分明细": p.get("factors"),
                } for p in top5[:3]]
            # 回填买入价 + 买卖计划字段归一化/缺省兜底（R25）
            price_map = {p["ticker"].split(".")[0]: p for p in top5}
            for tgt in targets:
                code = tgt.get("code", "").split(".")[0]
                src = price_map.get(code) or {}
                tgt["参考买入价(收盘)"] = tgt.get("参考买入价(收盘)") or src.get("参考买入价(收盘)")
                tgt["量比"] = tgt.get("量比") or src.get("量比")
                tgt["评分明细"] = src.get("factors")
                tgt["板块"] = src.get("sector_name")
                # 归一化 LLM 可能的键名
                for k in ("stop_loss", "止损", "止损位", "止损价"):
                    if tgt.get(k) and not tgt.get("stop_loss"):
                        tgt["stop_loss"] = tgt[k]
                for k in ("sell_target", "卖出区间", "目标价", "卖出目标"):
                    if tgt.get(k) and not tgt.get("sell_target"):
                        tgt["sell_target"] = tgt[k]
                for k in ("hold", "持仓", "持仓时间"):
                    if tgt.get(k) and not tgt.get("hold"):
                        tgt["hold"] = tgt[k]
                # 缺省兜底：基于真实买入价（-3% 止损 / +3% 目标 / T+1）
                buy = tgt.get("参考买入价(收盘)")
                if buy:
                    tgt.setdefault("stop_loss", round(buy * 0.97, 2))
                    tgt.setdefault("sell_target", round(buy * 1.03, 2))
                    tgt.setdefault("hold", "T+1（次日开盘卖出）")
        except Exception as e:
            log.exception("LLM 研判失败，回退规则结果")
            targets = [{"code": p["ticker"], "name": p["name"], "reason": p.get("逻辑"),
                        "risk": "无", "confidence": "中", "参考买入价(收盘)": p.get("参考买入价(收盘)"),
                        "量比": p.get("量比")} for p in top5[:3]]

    return {
        "date": t,
        "strategy": "7-10日强势板块 + 个股强势 + 资金活跃 + 量比<2.0过滤 + 消息面 + LLM研判",
        "strategy_version": STRATEGY_VERSION,
        "filtered_out": strat.filtered,
        "sector_window": pool["meta"].get("sector_window"),
        "market_view": (llm_result or {}).get("market_view"),
        "targets": targets,
        "rule_candidates": [
            {"代码": p["ticker"], "名称": p["name"], "行业": p.get("industry"),
             "参考买入价(收盘)": p.get("参考买入价(收盘)"), "量比": p.get("量比"),
             "评分": p.get("score"), "逻辑": p.get("逻辑")}
            for p in top5
        ],
        "news": news,
        "news_has_lhb": bool(lhb_map),
        "top_sectors": pool["meta"]["top_sectors"],
        "candidate_count": len(pool["candidates"]),
    }


def _build_logic(pick: dict, vol_ratio) -> str:
    parts = []
    if pick.get("sector_name"):
        parts.append(f"所属强势板块「{pick['sector_name']}」")
    if pick.get("change_ratio") is not None:
        parts.append(f"当日涨幅 {pick['change_ratio']:.1f}%")
    parts.append(f"量比 {vol_ratio:.1f}（未放巨量）" if vol_ratio else "量比适中")
    return "；".join(parts)
