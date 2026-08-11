"""每日预测：基于最新交易日构建候选池 → 量比过滤策略 → 输出 Top 3 标的
（M2 规则版：板块强度 + 个股强度 + 资金活跃 + 量比过滤）
"""
import json
import logging
from datetime import date

from .candidate_pool import CandidatePool
from .scoring import score_pool
from .strategy import Strategy, compute_vol_ratio
from .backtest import Backtest

log = logging.getLogger("daily")


def _kline_lookup_factory(cached):
    def lookup(ticker, end_date):
        return cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                           {"ticker": ticker, "market": "a_stock", "end_date": end_date, "limit": 12})
    return lookup


def predict(cached, target_date: str = None) -> dict:
    """生成最新交易日的 Top3 预测（收盘前买入 / 次日开盘卖出）"""
    bt = Backtest(cached)
    end = target_date or str(date.today())
    trading = bt.trading_days(end, 2)
    t = trading[-1]
    pool = CandidatePool(cached).build(t)
    kline_lookup = _kline_lookup_factory(cached)
    strat = Strategy(cached, score_pool, kline_lookup)
    # 取前 5（含量比过滤后可能不足 3）
    picks = strat.select(pool, t, None, top_n=5)
    # 补充买卖价与量比明细
    out_picks = []
    for pick in picks[:3]:
        resp = kline_lookup(pick["ticker"], t)
        pts = {p["time"]: p for p in ((resp or {}).get("data") or {}).get("points") or []}
        pt = pts.get(t)
        vr = compute_vol_ratio(resp, t) if pt else None
        out_picks.append({
            "代码": pick["ticker"], "名称": pick["name"],
            "行业": pick.get("industry"), "板块": pick.get("sector_name"),
            "参考买入价(收盘)": pt.get("close") if pt else None,
            "涨跌幅%": pick.get("change_ratio"),
            "量比": round(vr, 2) if vr else None,
            "综合评分": pick.get("score"),
            "评分明细": pick.get("factors"),
            "逻辑": _build_logic(pick, vr),
        })
    return {
        "date": t,
        "next_date": trading[-2] if len(trading) >= 2 else None,
        "strategy": "板块5日资金流 + 个股强势 + 资金活跃 + 量比<2.0过滤",
        "targets": out_picks,
        "top_sectors": pool["meta"]["top_sectors"],
        "candidate_count": len(pool["candidates"]),
    }


def _build_logic(pick: dict, vol_ratio) -> str:
    parts = []
    f = pick.get("factors") or {}
    if pick.get("sector_name"):
        parts.append(f"所属强势板块「{pick['sector_name']}」")
    if pick.get("change_ratio") is not None:
        parts.append(f"当日涨幅 {pick['change_ratio']:.1f}%")
    if vol_ratio is not None:
        parts.append(f"量比 {vol_ratio:.1f}（未放巨量）")
    else:
        parts.append("量比适中")
    return "；".join(parts)
