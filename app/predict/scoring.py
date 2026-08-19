"""规则打分 v2：低吸强势 · 不追拥挤（基于回测因子归因）
核心结论（30日样本）：大成交额热门股次日低开、中小成交额强势股次日高开
"""
import logging

log = logging.getLogger("scoring")


def _change_score(cr: float) -> float:
    """当日涨幅 0-30：7~8% 最优；>9% 追高回落；<=0 弱"""
    if cr is None:
        return 8
    if cr <= 0:
        return 6
    if cr <= 2:
        return 8 + cr * 2.0        # 0-2%: 8~12
    if cr <= 5:
        return 12 + (cr - 2) * 3.0  # 2-5%: 12~21
    if cr <= 8:
        return 21 + (cr - 5) * 2.0  # 5-8%: 21~27
    if cr <= 10:
        return 27 - (cr - 8) * 2.5  # 8-10%: 27~22
    return 22 - (cr - 10) * 1.0     # >10%: 追高风险


def _amount_score(amount: float) -> float:
    """成交额 0-25：1~4亿 最优（活跃不拥挤）；>8亿 拥挤扣分"""
    if not amount or amount <= 0:
        return 0
    yi = amount / 1e8
    if yi <= 0.5:
        return 8
    if yi <= 1:
        return 10 + (yi - 0.5) * 14   # 0.5-1亿: 10~17
    if yi <= 4:
        return 19 + (yi - 1) * 2.0    # 1-4亿: 19~25
    if yi <= 8:
        return 25 - (yi - 4) * 2.5    # 4-8亿: 25~15
    return max(4.0, 15 - (yi - 8) * 0.9)  # >8亿: 拥挤


def _turnover_score(turnover: float) -> float:
    """换手率 0-15：5~15% 最佳"""
    if turnover is None:
        return 7
    if 5 <= turnover <= 15:
        return 15
    if 0 < turnover < 5:
        return 6 + turnover * 1.8     # 0-5%: 6~15
    if 15 < turnover <= 30:
        return 15 - (turnover - 15) * 0.6  # 15-30%: 15~6
    return 4


def _sector_score(sector_rank: int, n_sectors: int = 8) -> float:
    """板块 0-20：第1名20分线性衰减；拥挤强板块不再给超高权重"""
    if not sector_rank:
        return 6
    return max(3.0, 20 - (sector_rank - 1) * (17 / (n_sectors - 1)))


def _quality_penalty(stock: dict) -> float:
    p = 0.0
    pe = stock.get("pe_ttm")
    if pe is not None and pe < 0:
        p += 8
    mc = stock.get("market_cap")
    if mc:
        if mc < 3e9:
            p += 5
        if mc > 8e10:
            p += 3
    name = stock.get("name") or ""
    if "ST" in name.upper():
        p += 50
    return p


def score_stock(stock: dict, n_sectors: int = 8) -> dict:
    score = 0.0
    f = {}
    c = _change_score(stock.get("change_ratio"))
    score += c; f["个股强度"] = round(c, 1)
    a = _amount_score(stock.get("amount"))
    score += a; f["资金活跃"] = round(a, 1)
    t = _turnover_score(stock.get("turnover_rate"))
    score += t; f["换手率"] = round(t, 1)
    s = _sector_score(stock.get("sector_rank"), n_sectors)
    score += s; f["板块"] = round(s, 1)
    e = -50.0 if stock.get("limit_status") == "涨停" else 5.0  # 涨停无法买入，大幅扣分
    score += e; f["情绪"] = e
    q = _quality_penalty(stock)
    score -= q; f["质量扣分"] = -round(q, 1)
    return {"score": round(max(0.0, min(100.0, score)), 1), "factors": f}


def score_pool(pool: dict, top_n: int = 3) -> list:
    candidates = pool.get("candidates") or []
    scored = []
    for st in candidates:
        r = score_stock(st)
        scored.append({
            "ticker": st["ticker"], "name": st["name"],
            "industry": st.get("industry"), "sector_name": st.get("sector_name"),
            "sector_rank": st.get("sector_rank"),
            "close": st.get("close"), "change_ratio": st.get("change_ratio"),
            "amount": st.get("amount"), "turnover_rate": st.get("turnover_rate"),
            "market_cap": st.get("market_cap"), "limit_status": st.get("limit_status"),
            "score": r["score"], "factors": r["factors"],
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]
