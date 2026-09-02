"""候选池：7-10日强势板块 + 板块内强势个股 + 全市场资金活跃标的 + 涨停股
数据：同舟 MCP → THS 兜底
"""
import logging
from ..ths_client import get_ths_client

log = logging.getLogger("candidate_pool")

# 过滤参数
MIN_AMOUNT = 1e8          # 成交额 >= 1 亿
MAX_CHANGE = 21.0         # 涨跌幅过滤异常（新股首日等）
TOP_SECTOR_N = 8          # 强势板块数量
PER_SECTOR_N = 6          # 每板块取前 N
TOP_AMOUNT_N = 25         # 全市场成交额前 N


def _safe_float(v):
    """安全转 float：字符串/None/异常统一返回 None"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except Exception:
        return None


def _clean_rank_row(r: dict) -> dict:
    # 数值字段统一转 float，避免 MCP 返回字符串导致下游崩溃
    return {
        "ticker": r.get("ticker"),
        "name": r.get("security_name"),
        "trade_date": r.get("trade_date"),
        "close": _safe_float(r.get("close")),
        "change_ratio": _safe_float(r.get("change_ratio")),
        "amount": _safe_float(r.get("amount")),
        "turnover_rate": _safe_float(r.get("turnover_rate")),
        "market_cap": _safe_float(r.get("market_cap")),
        "pe_ttm": _safe_float(r.get("pe_ttm")),
        "pb": _safe_float(r.get("pb")),
        "industry": r.get("industry"),
        "board": r.get("board"),
        "limit_status": r.get("limit_status"),
        "prev_close": _safe_float(r.get("prev_close")),
        "volume": _safe_float(r.get("volume")),
    }


def _is_valid(stock: dict) -> bool:
    name = stock.get("name") or ""
    ticker = stock.get("ticker") or ""
    if "ST" in name.upper() or "退" in name:
        return False
    if ticker.startswith("920") or stock.get("board") == "北证":
        return False
    cr = _safe_float(stock.get("change_ratio"))
    if cr is None or abs(cr) > MAX_CHANGE:
        return False
    # 涨停股过滤：涨停板无法买入（交易可执行性硬约束）
    if cr >= 9.9:
        return False
    amt = _safe_float(stock.get("amount"))
    if amt is None or amt < MIN_AMOUNT:
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
        rankings = []
        try:
            self._sector_window = "20日资金流(同舟兜底)"
            resp = self.mcp.call(
                "tongzhou-fin-research_fin_data__rank_industry_fund_flows",
                {"trade_date": trade_date, "direction": "all", "rank_window": 20, "limit": 31})
            rankings = ((resp or {}).get("data") or {}).get("rankings") or []
        except Exception as e:
            log.warning("MCP 板块资金流失败: %s", str(e)[:100])
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
        if rows:
            return rows[:n]
        # THS 兜底：用同花顺行业指数涨幅排名
        log.info("MCP 板块数据为空，使用 THS 行业指数兜底")
        self._sector_window = "THS行业指数"
        ths = get_ths_client()
        try:
            ths_ind = ths.ths_index_list(tag="industry")
            sectors = []
            for idx_item in ths_ind[:30]:
                snap = ths.ths_index_snapshot(idx_item.get("thscode", ""))
                pct = snap.get("price_change_ratio_pct")
                if pct is not None:
                    sectors.append({
                        "industry": idx_item.get("name", ""),
                        "industry_code": idx_item.get("thscode", ""),
                        "change_ratio_20d": pct,
                        "sector_rank": 0,
                    })
            sectors.sort(key=lambda x: (x.get("change_ratio_20d") or -999), reverse=True)
            for i, s in enumerate(sectors[:n], 1):
                s["sector_rank"] = i
            if sectors:
                self._add_source = lambda s: None  # placeholder
                return sectors[:n]
        except Exception as e:
            log.warning("THS 板块兜底失败: %s", str(e)[:100])
        return []

    def _sector_stocks(self, sector: dict, n: int = PER_SECTOR_N) -> list:
        """板块内强势股：ego 板块成分股优先，同舟 rank_securities 兜底"""
        bk = sector.get("bk") or sector.get("code")
        if bk:
            try:
                rows = self.ego.sector_stocks(bk, n)
                if rows:
                    # 检查数据质量：至少有一个有效收盘价
                    has_valid = any(_safe_float(r.get("close")) for r in rows)
                    if has_valid:
                        for r in rows:
                            r["industry"] = sector.get("industry") or sector.get("name")
                        return rows
                    log.info("ego 板块数据无效（无收盘价），尝试 THS 兜底")
            except Exception as e:
                log.warning("ego 板块成分股失败(%s): %s", bk, str(e)[:120])
        rankings = []
        try:
            resp = self.mcp.call(
                "tongzhou-fin-research_fin_data__rank_securities",
                {"trade_date": sector.get("trade_date") or "", "sort_by": "change_ratio",
                 "industry": sector.get("industry"), "limit": 15})
            rankings = ((resp or {}).get("data") or {}).get("rankings") or []
        except Exception as e:
            log.warning("MCP 板块个股失败: %s", str(e)[:100])
        if rankings:
            return [_clean_rank_row(r) for r in rankings[:n]]
        # THS 兜底：用行业指数成分股 + 快照
        ths = get_ths_client()
        industry_name = sector.get("industry") or sector.get("name") or ""
        try:
            # 用行业名称匹配 THS 行业指数
            ths_ind = ths.ths_index_list(tag="industry")
            matched_code = None
            for idx in ths_ind:
                if industry_name and (industry_name in (idx.get("name") or "") or (idx.get("name") or "") in industry_name):
                    matched_code = idx.get("thscode")
                    break
            if not matched_code:
                # 模糊匹配：取第一个包含关键词的
                for idx in ths_ind:
                    name = idx.get("name") or ""
                    if any(kw in name for kw in [industry_name[:2]] if len(industry_name) >= 2):
                        matched_code = idx.get("thscode")
                        break
            if not matched_code:
                return []
            constituents = ths.index_constituents(matched_code)
            if not constituents:
                return []
            # 用K线获取最近交易日数据
            from datetime import date as _d, timedelta as _td
            today = _d.today()
            start = today - _td(days=7)
            rows = []
            for c in constituents[:n*3]:
                tc = c.get("thscode", "")
                ticker = tc.split(".")[0]
                try:
                    kline = ths.kline(tc, start, today)
                    if kline and len(kline) >= 2:
                        last = kline[-1]
                        prev = kline[-2]
                        close = last.get("close_price")
                        prev_close = prev.get("close_price")
                        chg = round((close / prev_close - 1) * 100, 2) if close and prev_close else None
                        rows.append({
                            "ticker": ticker,
                            "name": c.get("stock_name") or c.get("name"),
                            "close": close,
                            "change_ratio": chg,
                            "amount": last.get("turnover"),
                            "volume": last.get("volume"),
                            "industry": sector.get("industry") or sector.get("name"),
                        })
                except Exception:
                    pass
            rows.sort(key=lambda x: (x.get("change_ratio") or -999), reverse=True)
            return rows[:n]
        except Exception as e:
            log.warning("THS 板块成分股兜底失败(%s): %s", industry_code, str(e)[:100])
            return []

    def _top_amount(self, trade_date: str, n: int = TOP_AMOUNT_N) -> list:
        rankings = []
        try:
            resp = self.mcp.call(
                "tongzhou-fin-research_fin_data__rank_securities",
                {"trade_date": trade_date, "sort_by": "amount", "limit": n})
            rankings = ((resp or {}).get("data") or {}).get("rankings") or []
        except Exception as e:
            log.warning("MCP 成交额排名失败: %s", str(e)[:100])
        if rankings:
            return [_clean_rank_row(r) for r in rankings]
        # THS 兜底：全市场快照按成交额排序
        ths = get_ths_client()
        try:
            all_snaps = ths.snapshot_paged(limit=500, offset=0)
            if all_snaps:
                all_snaps.sort(key=lambda x: (x.get("turnover") or 0), reverse=True)
                rows = []
                for s in all_snaps[:n]:
                    ticker = s.get("ticker") or s.get("thscode", "").split(".")[0]
                    rows.append({
                        "ticker": ticker,
                        "name": "",
                        "close": s.get("last_price"),
                        "change_ratio": s.get("price_change_ratio_pct"),
                        "amount": s.get("turnover"),
                    })
                return rows
        except Exception as e:
            log.warning("THS 全市场快照兜底失败: %s", str(e)[:100])
        return []

    def _limit_up(self, trade_date: str, n: int = 50) -> list:
        secs = []
        try:
            resp = self.mcp.call(
                "tongzhou-fin-research_fin_data__screen_stocks",
                {"trade_date": trade_date, "status": "limit_up", "limit": n})
            secs = ((resp or {}).get("data") or {}).get("securities") or []
        except Exception as e:
            log.warning("MCP 涨停筛选失败: %s", str(e)[:100])
        if secs:
            return [_clean_rank_row(r) for r in secs]
        # THS 兜底：涨停池
        ths = get_ths_client()
        try:
            ths_lup = ths.limit_up_pool(trade_date)
            if ths_lup:
                rows = []
                for it in ths_lup:
                    ticker = (it.get("thscode") or "").split(".")[0]
                    rows.append({
                        "ticker": ticker,
                        "name": it.get("stock_name"),
                        "limit_status": "涨停",
                        "change_ratio": it.get("price_change_ratio_pct"),
                        "close": it.get("last_price"),
                        "amount": it.get("turnover"),
                    })
                return rows
        except Exception as e:
            log.warning("THS 涨停池兜底失败: %s", str(e)[:100])
        return []

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
