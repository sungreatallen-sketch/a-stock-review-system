"""统一报告文案，避免历史预测中的旧执行口径继续进入用户界面。"""

EXECUTION_PLAN = "T+1开盘买入，T+2收盘卖出"


def execution_plan_text(_legacy: str | None = None) -> str:
    """返回当前唯一的模拟执行计划；旧文案仅保留在原始数据里。"""
    return EXECUTION_PLAN
