"""报告校验器：在发送飞书前拦截结构性错误（防御层）
每个校验规则对应一个历史 bug，新增 bug 时在此补充规则。

规则索引：
  V01 - 昨日标的日期必须 < 今日（ISSUE-027）
  V02 - 已结算标的必须有收益数据（ISSUE-027）
  V03 - 推荐标的数量必须 = 3（R10）
  V04 - 每个推荐标的必须有收盘价
  V05 - 板块排名不能为空
  V06 - 市场指数不能为空
  V07 - 市场情绪不能为空
  V08 - 推荐标的不能是涨停状态（C42）
"""
import logging
from datetime import date as _date

log = logging.getLogger("report_validator")


class ValidationError:
    def __init__(self, rule: str, message: str, severity: str = "error"):
        self.rule = rule
        self.message = message
        self.severity = severity  # error = 阻断, warning = 告警

    def __repr__(self):
        return f"[{self.severity.upper()}] {self.rule}: {self.message}"


def validate_report(report: dict, today: str = None) -> list:
    """校验报告结构完整性，返回错误列表（空 = 通过）"""
    errors = []
    today = today or str(_date.today())

    # V01: 昨日标的日期 < 今日
    tracking = report.get("tracking") or {}
    latest = tracking.get("latest_prediction") or {}
    pred_date = latest.get("date")
    if pred_date:
        if pred_date >= today:
            errors.append(ValidationError(
                "V01",
                f"昨日推荐标的日期({pred_date})不应>=今日({today})",
                "error"
            ))
    else:
        errors.append(ValidationError(
            "V01",
            "昨日推荐标的日期缺失",
            "warning"
        ))

    # V02: 已结算标的必须有收益数据
    settle = tracking.get("settle") or {}
    if settle.get("results"):
        for r in settle["results"]:
            if r.get("ret") is None:
                errors.append(ValidationError(
                    "V02",
                    f"结算标的 {r.get('code')} {r.get('name')} 缺少收益数据",
                    "error"
                ))
    elif pred_date and pred_date < today:
        # 如果昨日有预测但无结算结果，可能是数据问题
        errors.append(ValidationError(
            "V02",
            f"昨日({pred_date})有预测但无结算结果",
            "warning"
        ))

    # V03: 推荐标的数量 = 3
    pred = report.get("prediction") or {}
    targets = pred.get("targets") or []
    if len(targets) != 3:
        errors.append(ValidationError(
            "V03",
            f"推荐标的数量={len(targets)}，期望=3",
            "error"
        ))

    # V04: 每个推荐标的必须有收盘价
    for t in targets:
        code = t.get("code", "")
        buy = t.get("参考买入价(收盘)")
        if not buy:
            errors.append(ValidationError(
                "V04",
                f"推荐标的 {code} {t.get('name', '')} 缺少收盘价",
                "error"
            ))

    # V05: 板块排名不能为空
    sectors = report.get("sector_rank") or []
    if not sectors:
        errors.append(ValidationError("V05", "板块排名为空", "error"))

    # V06: 市场指数不能为空
    index = report.get("market_index") or {}
    if not index:
        errors.append(ValidationError("V06", "市场指数为空", "error"))

    # V07: 市场情绪不能为空
    emotion = report.get("emotion") or {}
    if not emotion:
        errors.append(ValidationError("V07", "市场情绪为空", "error"))

    # V08: 推荐标的不能是涨停状态
    for t in targets:
        if t.get("limit_status") == "涨停":
            errors.append(ValidationError(
                "V08",
                f"推荐标的 {t.get('code')} {t.get('name')} 处于涨停状态，无法买入",
                "error"
            ))

    return errors


def validate_and_block(report: dict, today: str = None) -> tuple:
    """校验并返回 (是否可发送, 错误列表)"""
    errors = validate_report(report, today)
    blocking = [e for e in errors if e.severity == "error"]
    warnings = [e for e in errors if e.severity == "warning"]

    if blocking:
        log.error("报告校验失败，%d 个阻断性错误:", len(blocking))
        for e in blocking:
            log.error("  %s", e)
        return False, errors

    if warnings:
        log.warning("报告校验通过，但有 %d 个警告:", len(warnings))
        for e in warnings:
            log.warning("  %s", e)

    return True, errors
