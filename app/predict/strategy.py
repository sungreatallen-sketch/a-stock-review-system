"""策略：基础打分 → 取前N → K线量比过滤（放量剔除）→ 最终 Top n
回测发现：量比（当日量/5日均量）高分位次日开盘大幅低走（avg -2.07%，胜率9.3%）
"""
import logging

log = logging.getLogger("strategy")

MAX_VOL_RATIO = 2.0   # 量比超过该值视为"放巨量"，剔除（回测最优参数）
TOP_PRE = 10          # 先取基础打分前 N 做量比检查


def _kline_points(resp):
    return ((resp or {}).get("data") or {}).get("points") or []


def compute_vol_ratio(kline_resp: dict, day_t: str):
    """计算指定日期的量比（当日量 / 前5日均量）"""
    pts = _kline_points(kline_resp)
    by_day = {p["time"]: p for p in pts}
    pt = by_day.get(day_t)
    if not pt or not pt.get("volume"):
        return None
    vols = [by_day[x]["volume"] for x in sorted(by_day) if x < day_t and by_day[x].get("volume")][-5:]
    if not vols or sum(vols) <= 0:
        return None
    return pt["volume"] / (sum(vols) / len(vols))


class Strategy:
    """T日收盘前选股策略：基础规则打分 + 量比过滤"""

    def __init__(self, mcp, base_score_fn, kline_lookup):
        self.mcp = mcp
        self.base_score_fn = base_score_fn
        self.kline_lookup = kline_lookup   # fn(ticker, end_date) -> kline resp

    def select(self, pool: dict, day_t: str, day_t1: str, top_n: int = 3) -> list:
        pre = self.base_score_fn(pool, top_n=TOP_PRE)
        kept = []
        for pick in pre:
            # 涨停股过滤：涨停板无法买入，实盘无意义
            if pick.get("limit_status") == "涨停":
                log.info("涨停过滤: %s(%s) 当日涨停，无法买入", pick.get("name"), pick.get("ticker"))
                continue
            resp = self.kline_lookup(pick["ticker"], day_t1)
            vr = compute_vol_ratio(resp, day_t)
            if vr is None:
                kept.append(pick)          # 无K线数据不剔除（兜底）
                continue
            if vr > MAX_VOL_RATIO:
                continue                   # 放巨量，剔除
            pick = dict(pick)
            pick["vol_ratio"] = round(vr, 2)
            pick["factors"] = dict(pick.get("factors") or {})
            pick["factors"]["量比过滤"] = f"量比{vr:.1f} 通过"
            kept.append(pick)
        kept.sort(key=lambda x: x.get("score") or 0, reverse=True)
        return kept[:top_n]
