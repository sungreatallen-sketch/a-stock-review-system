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
    # 涨停股过滤：涨停板无法买入（交易可执行性硬约束）
    if cr is not None and cr >= 9.9:
        return False
    amt = stock.get("amount") or 0
    if amt < MIN_AMOUNT:
        return False
    return True


class CandidatePool:
    def __init__(self, mcp):
        self.mcp = mcp
        from .alt_data import EgoBoardData
        self.ego = EgoBoardData()
        self._sector_window = "10日(ego)"

    @property
    def sector_window(self) -> str:
        return self._sector_window

    def _top_sectors(self, trade_date: str, n: int = TOP_SECTOR_N) -> list:
        """R21：7-10日强势板块——优先东财行业板块10日涨幅（ego），兜底同舟20日资金流"""
        try:
            secs = self.ego.top_sectors_10d(n)
            if secs:
                self._sector_window = "10日(ego)"
                for s in secs:
                    s["industry"] = s["name"]
                return secs
        except Exception as e:
            log.warning("ego 10日板块失败: %s", str(e)[:120])
        # 兜底：同舟 20日资金流（接近7-10日窗口）
        self._sector_window = "20日资金流(同舟兜底)"
        resp = self.mcp.call(
            "tongzhou-fin-research_fin_data__rank_industry_fund_flows",
            {"trade_date": trade_date, "direction": "all", "rank_window": 20, "limit": 31})
        rankings = ((resp or {}).get("data") or {}).get("rankings") or []
        rows = []
        for r in rankings:
            w20 = (r.get("window_metrics") or {}).get("20d") or {}
            rows.append({
                "industry_code": r.get("industry_code"),
                "industry": r.get("industry_name"),
                "main_net_inflow": w20.get("net_inflow_amount") or r.get("main_net_inflow_amount"),
                "change_ratio_20d": w20.get("change_ratio"),
            })
        rows.sort(key=lambda x: (x["main_net_inflow"] or -1e18), reverse=True)
        for i, r in enumerate(rows[:n], 1):
            r["sector_rank"] = i
        return rows[:n]

    def _sector_stocks(self, sector: dict, n: int = PER_SECTOR_N) -> list:
        """板块内强势股：ego 板块成分股优先，同舟 rank_securities 兜底"""
        bk = sector.get("bk") or sector.get("code")
        if bk:
            try:
                rows = self.ego.sector_stocks(bk, n)
                if rows:
                    for r in rows:
                        r["industry"] = sector.get("industry") or sector.get("name")
                    return rows
            except Exception as e:
                log.warning("ego 板块成分股失败(%s): %s", bk, str(e)[:120])
        resp = self.mcp.call(
            "tongzhou-fin-research_fin_data__rank_securities",
            {"trade_date": sector.get("trade_date") or "", "sort_by": "change_ratio",
             "industry": sector.get("industry"), "limit": 15})
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

        # 1) 强势板块内个股（ego 成分股 / 同舟兜底）
        for s in sectors:
            s = dict(s)
            s["trade_date"] = trade_date
            for st in self._sector_stocks(s):
                if not _is_valid(st):
                    continue
                st = dict(st)
                st["sector_rank"] = s.get("sector_rank")
                st["sector_name"] = s.get("industry") or s.get("name")
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
            "top_sectors": [s.get("industry") or s.get("name") for s in sectors],
            "sector_window": self.sector_window,
        }}
