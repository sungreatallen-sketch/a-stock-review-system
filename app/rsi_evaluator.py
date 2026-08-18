"""RSI Evaluator Adapter — 评价单次预测的真实市场表现

与 Phase 1 的区别：
  Phase 1: 只包装 Tracker.stats()（汇总统计）
  Phase 2: 评价「某一次具体预测到底表现如何」，生成 EvaluationResult

评估口径：昨收买→今收卖（收盘-收盘），与 Tracker.settle() 一致
时间边界：T日推荐 → T+1日市场结果 → EvaluationResult
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from rsi_framework.evaluation.base import (
    Evaluator,
    EvaluationResult,
    EvaluationMetric,
)

log = logging.getLogger("rsi_evaluator")


class AShareEvaluator(Evaluator):
    """A 股单次预测评估器

    输入：Prediction + Market Result（来自 Tracker.settle 的 prediction_results）
    输出：EvaluationResult（RSI Framework 标准格式）
    """

    def __init__(self):
        self._version = "2.0.0"

    @property
    def name(self) -> str:
        return "ashare_prediction_evaluator"

    @property
    def version(self) -> str:
        return self._version

    def evaluate_prediction(
        self,
        pred_date: str,
        target_code: str,
        target_name: str,
        buy_price: float,
        sell_close: float,
        ret_close: float,
        strategy: str = "",
        reason: str = "",
        risk: str = "",
        market_view: str = "",
        confidence: str = "",
        top_sectors: Optional[List[str]] = None,
    ) -> EvaluationResult:
        """评价单次预测（同步方法，不依赖 async）

        Args:
            pred_date: 预测日 T（如 "2026-08-13"）
            target_code: 股票代码
            target_name: 股票名称
            buy_price: T日收盘价（买入价）
            sell_close: T+1日收盘价（卖出价）
            ret_close: 收益率%
            strategy: 策略描述
            reason: 推荐理由
            risk: 风险提示
            market_view: 市场判断
            confidence: 置信度
            top_sectors: 强势板块

        Returns:
            EvaluationResult
        """
        hit = ret_close > 0
        prediction_id = f"pred_{pred_date}_{target_code}"

        # 综合得分：命中(0.5) + 收益幅度(0.5)
        # 命中=0.5，未命中=0；收益部分归一化 [-5%,+5%] → [0,0.5]
        hit_score = 0.5 if hit else 0.0
        ret_score = max(0.0, min(0.5, (ret_close + 5.0) / 20.0))
        score = hit_score + ret_score

        metrics = {
            "hit": 1.0 if hit else 0.0,
            "return_rate": ret_close,
            "buy_price": buy_price,
            "sell_close": sell_close,
            EvaluationMetric.CORRECTNESS.value: 1.0 if hit else 0.0,
            EvaluationMetric.QUALITY.value: score,
        }

        evidence = [
            {"type": "price", "buy": buy_price, "sell": sell_close, "return_pct": ret_close},
            {"type": "strategy", "description": strategy},
            {"type": "reason", "text": reason[:200] if reason else ""},
        ]
        if risk:
            evidence.append({"type": "risk", "text": risk[:200]})

        return EvaluationResult(
            task_id=prediction_id,
            execution_id=f"eval_{prediction_id}",
            success=True,
            score=score,
            metrics=metrics,
            failures=[] if hit else [{"type": "miss", "return_pct": ret_close}],
            evidence=evidence,
            evaluator_version=self._version,
            metadata={
                "stock_code": target_code,
                "stock_name": target_name,
                "prediction_date": pred_date,
                "evaluation_date": _next_trade_day(pred_date),
                "confidence": confidence,
                "market_view": market_view[:100] if market_view else "",
                "top_sectors": top_sectors or [],
                "口径": "昨收买→今收卖（收盘-收盘）",
            },
        )

    async def evaluate(
        self,
        task_id: str,
        execution_id: str,
        result: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> EvaluationResult:
        """RSI Framework 标准接口 — 从 settle 结果生成 EvaluationResult"""
        ctx = context or {}
        return self.evaluate_prediction(
            pred_date=result.get("date", ctx.get("pred_date", "")),
            target_code=result.get("target_code", result.get("code", "")),
            target_name=result.get("target_name", result.get("name", "")),
            buy_price=result.get("buy_price", result.get("buy", 0)),
            sell_close=result.get("sell_close", 0),
            ret_close=result.get("ret_close", result.get("ret", 0)),
            strategy=ctx.get("strategy", ""),
            reason=ctx.get("reason", ""),
            risk=ctx.get("risk", ""),
            market_view=ctx.get("market_view", ""),
            confidence=ctx.get("confidence", ""),
            top_sectors=ctx.get("top_sectors"),
        )

    async def get_metrics(self) -> List[EvaluationMetric]:
        return [EvaluationMetric.CORRECTNESS, EvaluationMetric.QUALITY]


def evaluate_settled_predictions(data_dir) -> List[Dict[str, Any]]:
    """从 SQLite prediction_results + predictions 生成全部 EvaluationResult

    读取：
      - prediction_results（settle 后的真实市场数据）
      - predictions（原始预测，含 strategy/reason/risk/market_context）

    输出：
      - 每条 prediction_result 对应一个 EvaluationResult dict
      - 用于写入 Memory 或外部分析

    不修改任何现有表/文件。
    """
    import sqlite3
    from pathlib import Path

    db_path = Path(data_dir) / "a_share.db"
    conn = sqlite3.connect(db_path)

    # 加载所有已结算结果
    results = conn.execute(
        "SELECT date, target_code, target_name, buy_price, sell_close, ret_close "
        "FROM prediction_results WHERE status='settled' ORDER BY date, id"
    ).fetchall()

    # 加载原始预测（取 strategy/reason/risk 等上下文）
    pred_rows = conn.execute("SELECT date, targets FROM predictions").fetchall()
    pred_map = {}  # (date, code) -> target dict
    for pred_date, targets_json in pred_rows:
        try:
            pred = json.loads(targets_json)
            for t in pred.get("targets", []):
                code = (t.get("code") or "").split(".")[0]
                pred_map[(pred_date, code)] = {
                    "strategy": pred.get("strategy", ""),
                    "market_view": pred.get("market_view", ""),
                    "top_sectors": pred.get("top_sectors", []),
                    "reason": t.get("reason", ""),
                    "risk": t.get("risk", ""),
                    "confidence": t.get("confidence", ""),
                    "sentiment_score": t.get("sentiment_score"),
                }
        except Exception:
            continue

    conn.close()

    evaluator = AShareEvaluator()
    evaluations = []

    for row in results:
        pred_date, code, name, buy, sell_close, ret_close = row
        ctx = pred_map.get((pred_date, code), {})

        ev = evaluator.evaluate_prediction(
            pred_date=pred_date,
            target_code=code,
            target_name=name,
            buy_price=buy or 0,
            sell_close=sell_close or 0,
            ret_close=ret_close or 0,
            strategy=ctx.get("strategy", ""),
            reason=ctx.get("reason", ""),
            risk=ctx.get("risk", ""),
            market_view=ctx.get("market_view", ""),
            confidence=ctx.get("confidence", ""),
            top_sectors=ctx.get("top_sectors"),
        )

        evaluations.append({
            "prediction_id": ev.task_id,
            "stock_code": code,
            "stock_name": name,
            "prediction_date": pred_date,
            "evaluation_date": _next_trade_day(pred_date),
            "buy_price": buy,
            "sell_close": sell_close,
            "return_rate": ret_close,
            "hit": (ret_close or 0) > 0,
            "score": ev.score,
            "strategy": ctx.get("strategy", ""),
            "reason": ctx.get("reason", ""),
            "risk": ctx.get("risk", ""),
            "market_view": ctx.get("market_view", ""),
            "confidence": ctx.get("confidence", ""),
            "top_sectors": ctx.get("top_sectors", []),
            "evaluation_result": ev,
        })

    log.info("评估完成: %d 条 prediction_results → %d 个 EvaluationResult",
             len(results), len(evaluations))
    return evaluations


def _next_trade_day(pred_date: str) -> str:
    """简单推算 T+1（不查交易日历，仅+1天）"""
    from datetime import datetime, timedelta
    try:
        d = datetime.strptime(pred_date, "%Y-%m-%d")
        nd = d + timedelta(days=1)
        # 跳过周末
        while nd.weekday() >= 5:
            nd += timedelta(days=1)
        return nd.strftime("%Y-%m-%d")
    except Exception:
        return ""
