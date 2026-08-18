"""RSI Evaluator Adapter — 将现有 Tracker.stats() 包装为 RSI Evaluator 接口

约束：
  - 不修改现有 Tracker
  - 评估口径：昨收买→今收卖（收盘-收盘）
  - 不重构现有结算逻辑
"""
import logging
from typing import Any, Dict, List, Optional

from rsi_framework.evaluation.base import (
    Evaluator,
    EvaluationResult,
    EvaluationMetric,
)

log = logging.getLogger("rsi_evaluator")


class ASHRSEvaluator(Evaluator):
    """A 股预测评估器 — 包装现有 Tracker.stats()

    将 Tracker 的命中率/平均收益等统计转换为 RSI EvaluationResult。
    """

    def __init__(self):
        self._version = "1.0.0"

    @property
    def name(self) -> str:
        return "ashare_prediction_evaluator"

    @property
    def version(self) -> str:
        return self._version

    async def evaluate(
        self,
        task_id: str,
        execution_id: str,
        result: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> EvaluationResult:
        """评估一次预测任务的结果

        Args:
            task_id: RSI Task ID
            execution_id: RSI 执行 ID
            result: 预测结果（来自 workflow.run_review() 的 report dict）
            context: 附加上下文

        Returns:
            EvaluationResult 含命中率、平均收益等指标
        """
        try:
            from .config import paths
            from .predict.track import Tracker

            p = paths()
            tr = Tracker(p["data"])
            stats = tr.stats(days=120)

            count = stats.get("count", 0)
            win_rate = stats.get("win_rate", 0)
            avg_ret = stats.get("avg_ret", 0)

            # 计算综合得分：命中率权重 0.6 + 收益权重 0.4
            score = 0.0
            if count > 0:
                # 命中率归一化到 0-1（50%→0.5，100%→1.0）
                win_score = min(win_rate / 100.0, 1.0) if win_rate else 0.0
                # 平均收益归一化（-5%→0，0%→0.5，5%→1.0）
                ret_score = max(0.0, min(1.0, (avg_ret + 5.0) / 10.0)) if avg_ret is not None else 0.5
                score = win_score * 0.6 + ret_score * 0.4

            metrics = {
                EvaluationMetric.CORRECTNESS.value: win_rate / 100.0 if win_rate else 0.0,
                EvaluationMetric.QUALITY.value: score,
                "total_predictions": count,
                "win_rate": win_rate,
                "avg_return": avg_ret,
            }

            return EvaluationResult(
                task_id=task_id,
                execution_id=execution_id,
                success=count > 0,
                score=score,
                metrics=metrics,
                evaluator_version=self._version,
                metadata={
                    "evaluator": "ashare_tracker_stats",
                    "period_days": 120,
                    "口径": "昨收买→今收卖（收盘-收盘）",
                },
            )

        except Exception as e:
            log.exception("Evaluator 执行失败: %s", e)
            return EvaluationResult(
                task_id=task_id,
                execution_id=execution_id,
                success=False,
                score=0.0,
                metrics={},
                evaluator_version=self._version,
                metadata={"error": str(e)},
            )

    async def get_metrics(self) -> List[EvaluationMetric]:
        return [
            EvaluationMetric.CORRECTNESS,
            EvaluationMetric.QUALITY,
        ]
