"""工具函数：交易日检查、日期处理等"""
from datetime import date, timedelta
import logging

log = logging.getLogger("utils")

# 2026年法定假日（A股休市）
# 注：这里只列出主要假日，实际使用时可能需要更完整的列表
HOLIDAYS_2026 = {
    # 元旦
    date(2026, 1, 1),
    # 春节（1月26日-2月1日）
    date(2026, 1, 26), date(2026, 1, 27), date(2026, 1, 28),
    date(2026, 1, 29), date(2026, 1, 30), date(2026, 1, 31),
    date(2026, 2, 1),
    # 清明节（4月4日-6日）
    date(2026, 4, 4), date(2026, 4, 5), date(2026, 4, 6),
    # 劳动节（5月1日-5日）
    date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3),
    date(2026, 5, 4), date(2026, 5, 5),
    # 端午节（5月31日-6月2日）
    date(2026, 5, 31), date(2026, 6, 1), date(2026, 6, 2),
    # 中秋节（9月19日-21日）
    date(2026, 9, 19), date(2026, 9, 20), date(2026, 9, 21),
    # 国庆节（10月1日-7日）
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3),
    date(2026, 10, 4), date(2026, 10, 5), date(2026, 10, 6),
    date(2026, 10, 7),
}


def is_trading_day(d: date = None) -> bool:
    """检查是否是交易日（排除周末和法定假日）
    
    Args:
        d: 要检查的日期，默认为今天
        
    Returns:
        True 如果是交易日，False 如果是周末或假日
    """
    d = d or date.today()
    
    # 排除周末
    if d.weekday() >= 5:  # 5=周六, 6=周日
        return False
    
    # 排除法定假日
    if d in HOLIDAYS_2026:
        return False
    
    return True


def get_latest_trading_day(d: date = None, max_lookback: int = 10) -> date:
    """获取最近的交易日（向前查找）
    
    Args:
        d: 起始日期，默认为今天
        max_lookback: 最大回溯天数，避免无限循环
        
    Returns:
        最近的交易日
    """
    d = d or date.today()
    for _ in range(max_lookback):
        if is_trading_day(d):
            return d
        d = d - timedelta(days=1)
    
    # 如果找不到交易日，返回原日期并记录警告
    log.warning("在 %d 天内未找到交易日，返回原日期: %s", max_lookback, d)
    return d


def get_next_trading_day(d: date = None, max_lookahead: int = 10) -> date:
    """获取下一个交易日（向后查找）
    
    Args:
        d: 起始日期，默认为今天
        max_lookahead: 最大前瞻天数
        
    Returns:
        下一个交易日
    """
    d = d or date.today()
    for _ in range(max_lookahead):
        d = d + timedelta(days=1)
        if is_trading_day(d):
            return d
    
    log.warning("在 %d 天内未找到下一个交易日，返回原日期: %s", max_lookahead, d)
    return d


def format_trade_date(d: date = None) -> str:
    """格式化交易日期为 YYYYMMDD 字符串"""
    d = d or date.today()
    return d.strftime("%Y%m%d")


def parse_trade_date(date_str: str) -> date:
    """解析 YYYYMMDD 格式的日期字符串"""
    return date.strptime(date_str, "%Y%m%d")
