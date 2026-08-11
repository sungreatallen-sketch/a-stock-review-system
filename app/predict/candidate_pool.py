"""候选池：7-10日强势板块 + 板块内强势个股 + 全市场资金活跃标的 + 涨停股
数据：同舟 MCP（rank_industry_fund_flows / rank_securities / screen_stocks）
"""
import logging

log = logging.getLogger("candidate_pool")

# 过滤参数
MIN_AMOUNT = 1e8          # 成交额 >= 1 亿
MAX_CHANGE = 21.0         # 涨跌幅过滤异常（新股首日等）
TOP_SECTOR_N = 8          # 强势板块数量
PER_SECTOR_N = 6          # 每板块取前 N
TOP_AMOUNT_N = 25         # 全市场成交额前 N


def _clean_rank_row(r: dict) -> dict:
    return {
        "ticker": r.get("ticker"),
        "name": r.get("security_name"),
        "trade_date": r.get("trade_date"),
        "close": r.get("close"),
        "change_ratio": r.get("change_ratio"),
        "amount": r.get("amount"),
        "turnover_rate": r.get("turnover_rate"),
        "market_cap": r.get("market_cap"),
        "pe_ttm": r.get("pe_ttm"),
        "pb": r.get("pb"),
        "industry": r.get("industry"),
        "board": r.get("board"),
        "limit_status": r.get("limit_status"),
        "prev_close": r.get("prev_close"),
        "volume": r.get("volume"),
    }


def _is_valid(stock: dict) -> bool:
    name = stock.get("name") or ""
    ticker = stock.get("ticker") or ""
    if "ST" in name.upper() or "退" in name:
        return False
    if ticker.startswith("920") or stock.get("board") == "北证":
        return False
    cr = stock.get("change_ratio")
    if cr is None or abs(cr) > MAX_CHANGE:
        return False
    amt = stock.get("amount") or 0
    if amt < MIN_AMOUNT:
        return False
    return True


class CandidatePool:
    def __init__(self, mcp):
        self.mcp = mcp

    def _top_sectors(self, trade_date: str, n: int = TOP_SECTOR_N) -> list:
        """5日主力净流入排名靠前的申万行业（含5日涨幅）"""
        resp = self.mcp.call(
            "tongzhou-fin-research_fin_data__rank_industry_fund_flows",
            {"trade_date": trade_date, "direction": "all", "rank_window": 5, "limit": 31})
        rankings = ((resp or {}).get("data") or {}).get("rankings") or []
        rows = []
        for r in rankings:
            w5 = (r.get("window_metrics") or {}).get("5d") or {}
            rows.append({
                "industry_code": r.get("industry_code"),
                "industry": r.get("industry_name"),
                "main_net_inflow": w5.get("net_inflow_amount") or r.get("main_net_inflow_amount"),
                "change_ratio_5d": w5.get("change_ratio"),
                "turnover_5d": w5.get("turnover_amount"),
            })
        # 按5日主力净流入排序
        rows.sort(key=lambda x: (x["main_net_inflow"] or -1e18), reverse=True)
        for i, r in enumerate(rows[:n], 1):
            r["sector_rank"] = i
        return rows[:n]

    def _sector_stocks(self, trade_date: str, industry: str, n: int = PER_SECTOR_N) -> list:
        resp = self.mcp.call(
            "tongzhou-fin-research_fin_data__rank_securities",
            {"trade_date": trade_date, "sort_by": "change_ratio", "industry": industry, "limit": 15})
        rankings = ((resp or {}).get("data") or {}).get("rankings") or []
        return [_clean_rank_row(r) for r in rankings[:n]]

    def _top_amount(self, trade_date: str, n: int = TOP_AMOUNT_N) -> list:
        resp = self.mcp.call(
            "tongzhou-fin-research_fin_data__rank_securities",
            {"trade_date": trade_date, "sort_by": "amount", "limit": n})
        rankings = ((resp or {}).get("data") or {}).get("rankings") or []
        return [_clean_rank_row(r) for r in rankings]

    def _limit_up(self, trade_date: str, n: int = 50) -> list:
        resp = self.mcp.call(
            "tongzhou-fin-research_fin_data__screen_stocks",
            {"trade_date": trade_date, "status": "limit_up", "limit": n})
        secs = ((resp or {}).get("data") or {}).get("securities") or []
        return [_clean_rank_row(r) for r in secs]

    def build(self, trade_date: str) -> dict:
        """构建候选池，返回 {candidates, sectors, meta}"""
        sectors = self._top_sectors(trade_date)
        pool = {}
        sector_map = {}

        # 1) 强势板块内个股
        for s in sectors:
            for st in self._sector_stocks(trade_date, s["industry"]):
                if not _is_valid(st):
                    continue
                st = dict(st)
                st["sector_rank"] = s["sector_rank"]
                st["sector_name"] = s["industry"]
                pool[st["ticker"]] = st
                sector_map[st["ticker"]] = s

        # 2) 全市场成交额前 N
        for st in self._top_amount(trade_date):
            if not _is_valid(st):
                continue
            st = dict(st)
            pool.setdefault(st["ticker"], st)

        # 3) 涨停股
        for st in self._limit_up(trade_date):
            if not _is_valid(st):
                continue
            st = dict(st)
            st["limit_status"] = "涨停"
            pool.setdefault(st["ticker"], st)

        candidates = list(pool.values())
        log.info("候选池: 交易日=%s 板块数=%d 候选数=%d", trade_date, len(sectors), len(candidates))
        return {"candidates": candidates, "sectors": sectors, "meta": {
            "trade_date": trade_date,
            "top_sectors": [s["industry"] for s in sectors],
        }}
