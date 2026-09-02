"""结构化复盘日志：每次复盘自动记录关键指标，便于追溯和告警。
日志文件：logs/review_runs.jsonl（每行一条 JSON 记录）

字段：
  timestamp     - 运行时间
  date          - 交易日
  pred_date     - 昨日推荐日期
  targets_count - 推荐标的数量
  sector_count  - 板块排名数量
  index_count   - 市场指数数量
  emotion_ok    - 市场情绪是否完整
  settle_count  - 结算结果数量
  validation    - 校验结果
  error         - 运行错误（如有）
"""
import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("review_logger")

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "review_runs.jsonl"


def log_review_run(report: dict, error: str = None):
    """记录一次复盘运行的结构化摘要"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        tracking = report.get("tracking") or {}
        latest = tracking.get("latest_prediction") or {}
        settle = tracking.get("settle") or {}
        validation = report.get("_validation") or {}
        pred = report.get("prediction") or {}

        entry = {
            "timestamp": datetime.now().isoformat(),
            "date": report.get("date"),
            "pred_date": latest.get("date"),
            "targets_count": len(pred.get("targets") or []),
            "sector_count": len(report.get("sector_rank") or []),
            "index_count": len(report.get("market_index") or {}),
            "emotion_ok": bool((report.get("emotion") or {}).get("涨停数量")),
            "settle_count": len(settle.get("results") or []),
            "validation_passed": validation.get("passed"),
            "validation_errors": validation.get("errors", []),
            "validation_warnings": validation.get("warnings", []),
        }
        if error:
            entry["error"] = error

        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        log.info("复盘日志已记录: date=%s targets=%d sectors=%d validation=%s",
                 entry["date"], entry["targets_count"], entry["sector_count"],
                 "PASS" if entry["validation_passed"] else "FAIL")
    except Exception as e:
        log.warning("结构化日志记录失败: %s", str(e)[:100])
