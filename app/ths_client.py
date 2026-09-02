"""同花顺金融数据 API 客户端
REST API 直连，不经过 WorkBuddy 代理，解决 catalog 过滤问题。
文档：https://fuyao.aicubes.cn/docs/
"""
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from .config import load_config

log = logging.getLogger("ths_client")


def _ts(d: date) -> int:
    """date → 毫秒 Unix 时间戳"""
    return int(datetime(d.year, d.month, d.day).timestamp() * 1000)


class THSClient:
    """同花顺金融数据 API 客户端"""

    def __init__(self, api_key: str = "", base_url: str = ""):
        cfg = load_config().get("ths", {})
        self.api_key = api_key or cfg.get("api_key", "")
        self.base_url = base_url or cfg.get("base_url", "https://fuyao.aicubes.cn")
        self._session = requests.Session()
        self._session.headers.update({
            "X-api-key": self.api_key,
            "Content-Type": "application/json",
        })
        self._cache: Dict[str, Any] = {}

    def _get(self, path: str, params: Dict = None) -> Dict:
        url = f"{self.base_url}{path}"
        last_error = None
        for attempt in range(2):
            try:
                resp = self._session.get(url, params=params, timeout=30)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(1.2)
                    continue
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != 0:
                    log.warning("THS API 错误: %s %s → %s", path, params, data.get("message"))
                    return {}
                return data.get("data") or {}
            except Exception as e:
                last_error = e
                if attempt == 0:
                    time.sleep(0.8)
        log.warning("THS API 请求失败: %s → %s", path, str(last_error)[:200])
        return {}

    # ── 交易日历 ──
    def trading_days(self, begin: date, end: date) -> List[str]:
        """获取交易日列表 (YYYYMMDD)"""
        data = self._get("/api/a-share/calendar/trading-days", {
            "begin_date": begin.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
        })
        items = data.get("item") or []
        # 某些网关版本会忽略日期参数；这里必须在客户端强制收敛到请求区间。
        begin_s, end_s = begin.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        return [
            datetime.fromtimestamp(it["date_ms"] / 1000).strftime("%Y-%m-%d")
            for it in items if it.get("date_ms")
            and begin_s <= datetime.fromtimestamp(it["date_ms"] / 1000).strftime("%Y-%m-%d") <= end_s
        ]

    def is_trading_day(self, d: date) -> bool:
        """检查是否是交易日"""
        # 周末直接排除
        if d.weekday() >= 5:
            return False
        # 查 API 确认
        days = self.trading_days(d, d)
        return d.strftime("%Y-%m-%d") in days

    def get_latest_trading_day(self, d: date = None) -> date:
        """获取最近已收盘的交易日"""
        d = d or date.today()
        # 今天过15:30且交易日 → 今天；否则从昨天往前找
        now = datetime.now()
        if now.hour >= 15 and now.minute >= 30 and self.is_trading_day(d):
            return d
        # 从昨天往前找
        for i in range(1, 15):
            candidate = d - timedelta(days=i)
            if self.is_trading_day(candidate):
                return candidate
        return d

    # ── 行情快照 ──
    def snapshot(self, thscode: str = "") -> Dict:
        """获取标的最新快照（thscode 为空则返回全市场）"""
        params = {}
        if thscode:
            params["thscode"] = thscode
        data = self._get("/api/a-share/prices/snapshot", params)
        items = data.get("item") or []
        if thscode:
            # API 返回全市场，需按 thscode 过滤
            for it in items:
                if it.get("thscode") == thscode:
                    return it
            return {}
        return items[0] if items else {}

    def snapshot_all(self) -> List[Dict]:
        """获取全市场快照"""
        data = self._get("/api/a-share/prices/snapshot")
        return data.get("item") or []

    def get_closing_price(self, thscode: str, d: date = None) -> Optional[float]:
        """获取指定日期的收盘价（快照）"""
        snap = self.snapshot(thscode)
        if not snap:
            return None
        # 快照的 last_price 就是收盘价（收盘后）
        return snap.get("last_price")

    # ── K 线历史 ──
    def kline(self, thscode: str, start: date, end: date, interval: str = "1d") -> List[Dict]:
        """获取历史 K 线"""
        data = self._get("/api/a-share/prices/historical", {
            "thscode": thscode,
            "interval": interval,
            "start": _ts(start),
            "end": _ts(end),
        })
        return data.get("item") or []

    def get_index_kline(self, thscode: str, end: date, limit: int = 30) -> List[Dict]:
        """获取指数 K 线（用于交易日判断等）"""
        start = end - timedelta(days=limit * 2)
        return self.kline(thscode, start, end)

    # ── 标的搜索 ──
    def search_ticker(self, keyword: str) -> List[Dict]:
        """搜索标的"""
        data = self._get("/api/meta/tickers/search", {"q": keyword})
        return data.get("item") or []

    def resolve_thscode(self, ticker: str) -> str:
        """代码 → thscode（如 600354 → 600354.SH）"""
        results = self.search_ticker(ticker)
        if results:
            return results[0].get("thscode", "")
        # 猜测：6开头上海，其他深圳
        if ticker.startswith("6"):
            return f"{ticker}.SH"
        return f"{ticker}.SZ"

    # ── 异动/涨跌停 ──
    def anomaly_list(self, trade_date: str) -> List[Dict]:
        """获取异动原因列表"""
        data = self._get("/api/a-share/special-data/anomaly-analysis-list", {
            "date": trade_date,
        })
        return data.get("item") or []

    def anomaly_stock(self, thscode: str) -> List[Dict]:
        """查询个股异动原因"""
        data = self._get("/api/a-share/special-data/anomaly-analysis-stock", {
            "thscode": thscode,
        })
        return data.get("item") or []

    # ── 龙虎榜 ──
    def dragon_tiger(self, trade_date: str) -> List[Dict]:
        """获取龙虎榜数据"""
        data = self._get("/api/a-share/special-data/dragon-tiger-list", {
            "date": trade_date,
        })
        return data.get("item") or []

    # ── 财务数据 ──
    def income_statement(self, thscode: str, period: str = "annual") -> List[Dict]:
        """获取利润表"""
        data = self._get("/api/a-share/financials/income-statements", {
            "thscode": thscode,
            "period": period,
        })
        return data.get("item") or []

    # ── 指数/板块 ──
    def ths_index_list(self) -> List[Dict]:
        """获取同花顺指数列表"""
        data = self._get("/api/a-share-index/catalog/ths-index-list")
        return data.get("item") or []

    def ths_index_snapshot(self, thscode: str) -> Dict:
        """获取指数快照"""
        data = self._get("/api/a-share-index/prices/snapshot", {"thscodes": thscode})
        items = data.get("item") or []
        for it in items:
            if it.get("thscode") == thscode:
                return it
        return items[0] if items else {}



    # ── 涨跌停数据 ──
    def limit_up_pool(self, trade_date: str) -> List[Dict]:
        """获取涨停股票池（含连板信息）
        trade_date: YYYY-MM-DD 或 YYYYMMDD 格式"""
        d = trade_date.replace("-", "")
        data = self._get("/api/a-share/special-data/limit-up-pool", {"date": d})
        return data.get("item") or []

    def limit_down_pool(self, trade_date: str) -> List[Dict]:
        """获取跌停股票池"""
        d = trade_date.replace("-", "")
        data = self._get("/api/a-share/special-data/limit-down-pool", {"date": d})
        return data.get("item") or []

    def limit_break_pool(self, trade_date: str) -> List[Dict]:
        """获取炸板股票池"""
        d = trade_date.replace("-", "")
        data = self._get("/api/a-share/special-data/limit-break-pool", {"date": d})
        return data.get("item") or []

    def limit_up_ladder(self) -> List[Dict]:
        """获取近30个交易日连板天梯"""
        data = self._get("/api/a-share/special-data/limit-up-ladder")
        return data.get("item") or []

    # ── 热榜数据 ──
    def hot_stock_list(self, period: str = "24h") -> List[Dict]:
        """获取热股榜 Top30
        period: '24h' 或 '1h'"""
        data = self._get("/api/a-share/special-data/hot-stock-list", {"period": period})
        return data.get("item") or []

    def skyrocket_list(self, period: str = "24h") -> List[Dict]:
        """获取飙升榜 Top30"""
        data = self._get("/api/a-share/special-data/skyrocket-list", {"period": period})
        return data.get("item") or []

    # ── 个股异动原因 ──
    def anomaly_analysis_list(self, tag_codes: str = "") -> List[Dict]:
        """获取当日个股异动原因列表
        tag_codes: 逗号分隔，如 'LIMIT_UP,SHARP_RISE'"""
        params = {}
        if tag_codes:
            params["tag_codes"] = tag_codes
        data = self._get("/api/a-share/special-data/anomaly-analysis-list", params)
        return data.get("item") or []

    # ── 龙虎榜 ──
    def dragon_tiger_list(self, trade_date: str, list_type: str = "all") -> List[Dict]:
        """获取龙虎榜
        trade_date: YYYY-MM-DD 或 YYYYMMDD
        list_type: 'all' / 'institution' / 'hot_money'"""
        d = trade_date.replace("-", "")
        data = self._get("/api/a-share/special-data/dragon-tiger-list", {
            "date": d, "type": list_type
        })
        return data.get("item") or []

    # ── 指数数据（增强） ──
    def index_snapshot(self, thscodes: str) -> List[Dict]:
        """批量获取指数快照
        thscodes: 逗号分隔的指数代码，如 '000001.SH,399001.SZ'"""
        data = self._get("/api/a-share-index/prices/snapshot", {"thscodes": thscodes})
        return data.get("item") or []

    def index_kline(self, thscode: str, start: date = None, end: date = None,
                    interval: str = "1d", adjust: int = 0) -> List[Dict]:
        """获取指数历史 K 线"""
        end = end or date.today()
        start = start or (end - timedelta(days=30))
        params = {
            "thscode": thscode,
            "interval": interval,
            "adjust": adjust,
            "start": _ts(start),
            "end": _ts(end),
        }
        data = self._get("/api/a-share-index/prices/historical", params)
        return data.get("item") or []

    def ths_index_list(self, tag: str = "industry") -> List[Dict]:
        """获取同花顺指数/板块列表
        tag: 'industry'(行业) / 'cn_concept'(概念) / 'region'(地域)"""
        data = self._get("/api/a-share-index/catalog/ths-index-list", {"tag": tag})
        return data.get("item") or []

    def index_constituents(self, thscode: str) -> List[Dict]:
        """获取指数/板块成分股"""
        data = self._get("/api/a-share-index/constituents/ths-stock-list", {"thscode": thscode})
        return data.get("item") or []

    # ── 全市场快照（带筛选） ──
    def snapshot_paged(self, limit: int = 200, offset: int = 0) -> List[Dict]:
        """分页获取全市场快照"""
        data = self._get("/api/a-share/prices/snapshot", {"limit": limit, "offset": offset})
        return data.get("item") or []

    # ── 多股快照 ──
    def snapshot_batch(self, thscodes: str) -> List[Dict]:
        """批量获取多只股票快照
        thscodes: 逗号分隔，如 '600519.SH,000001.SZ'"""
        data = self._get("/api/a-share/prices/snapshot", {"thscodes": thscodes})
        return data.get("item") or []

# 单例
_ths_client: Optional[THSClient] = None

def get_ths_client() -> THSClient:
    global _ths_client
    if _ths_client is None:
        _ths_client = THSClient()
    return _ths_client

# ── 辅助函数 ──
from datetime import timedelta

def thscode_for_index(name: str) -> str:
    """指数名称 → thscode"""
    mapping = {
        "上证指数": "000001.SH",
        "深证成指": "399001.SZ",
        "创业板指": "399006.SZ",
        "科创50": "000688.SH",
    }
    return mapping.get(name, "")
