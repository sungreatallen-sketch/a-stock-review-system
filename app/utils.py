"""A股统一交易日历。交易所年度休市表为本地权威快照；未知年份不猜测。"""
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import logging

log = logging.getLogger('utils')
MARKET_TZ = ZoneInfo('Asia/Shanghai')
CALENDAR_DIR = Path(__file__).resolve().parents[1] / 'config'

class CalendarUnavailable(RuntimeError):
    pass

def market_now() -> datetime:
    return datetime.now(MARKET_TZ)

@lru_cache(maxsize=8)
def _calendar(year: int):
    fp = CALENDAR_DIR / f'trading_calendar_{year}.json'
    try:
        data = json.loads(fp.read_text(encoding='utf-8'))
        if data.get('year') != year or not data.get('source_url') or not data.get('verified_at'):
            raise ValueError('缺少年份或来源信息')
        if data.get('coverage_start') != f'{year}-01-01' or data.get('coverage_end') != f'{year}-12-31':
            raise ValueError('非完整年度覆盖')
        closed = frozenset(date.fromisoformat(x) for x in data['closed_dates'])
        if not closed or any(d.year != year for d in closed):
            raise ValueError('休市日期无效')
        return closed
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CalendarUnavailable(f'{year}年交易日历缺失或无效，需核对交易所公告并更新 {fp.name}') from exc

def is_trading_day(d: date = None) -> bool:
    d = d or market_now().date()
    closed = _calendar(d.year)
    return d.weekday() < 5 and d not in closed

def get_latest_trading_day(d: date = None, max_lookback: int = 31) -> date:
    """包含起始日，向前寻找交易日；不代表该日已经收盘。"""
    d = d or market_now().date()
    for _ in range(max_lookback):
        if is_trading_day(d): return d
        d -= timedelta(days=1)
    raise CalendarUnavailable(f'{max_lookback}天内未找到交易日，禁止返回未经验证的日期')

def get_next_trading_day(d: date = None, max_lookahead: int = 31) -> date:
    d = d or market_now().date()
    for _ in range(max_lookahead):
        d += timedelta(days=1)
        if is_trading_day(d): return d
    raise CalendarUnavailable(f'{max_lookahead}天内未找到下一交易日')

def get_latest_closed_trading_day(now: datetime = None) -> date:
    now = now or market_now()
    if now.tzinfo is not None: now = now.astimezone(MARKET_TZ)
    d = now.date()
    if now.time() >= time(15, 30) and is_trading_day(d): return d
    return get_latest_trading_day(d - timedelta(days=1))

def trading_days_between(begin: date, end: date) -> list[str]:
    days = []
    while begin <= end:
        if is_trading_day(begin): days.append(begin.isoformat())
        begin += timedelta(days=1)
    return days

def format_trade_date(d: date = None) -> str:
    return (d or market_now().date()).strftime('%Y%m%d')

def parse_trade_date(date_str: str) -> date:
    return datetime.strptime(date_str, '%Y%m%d').date()
