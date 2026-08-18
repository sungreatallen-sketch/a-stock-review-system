"""RSI Agent 实现 — A 股复盘预测 Agent

AShareAgent 继承 RSI Framework 的 EnhancedAgent，内部委托给现有 workflow。
业务逻辑完全不改，只做接口适配。
"""
import logging
from typing import Any, Dict, List

from rsi_framework.agents.enhanced_base import (
    EnhancedAgent,
    EnhancedAgentContext,
    AgentMetadata,
    AgentCapability,
)
from rsi_framework.core.types import ActionResult, AgentStatus
from rsi_framework.task.models import Task

log = logging.getLogger("rsi_agent")


class AShareAgent(EnhancedAgent):
    """A 股复盘预测 Agent — RSI Framework EnhancedAgent 实现

    核心策略：
      run() 内部调用现有 workflow.run_review()，完全不修改业务逻辑。
      RSI Framework 负责 Task 调度和生命周期管理。
    """

    def __init__(self):
        metadata = AgentMetadata(
            name="ashare_review",
            description="A股收盘复盘与次日标的预测 Agent。"
                        "采集市场数据(ego+MCP)、交叉验证、候选池打分、"
                        "LLM研判、生成复盘报告与次日3只标的预测。",
            capabilities=[AgentCapability.ANALYSIS, AgentCapability.RESEARCH],
            version="1.0.0",
            author="ashare-project",
            tags=["a-share", "stock", "review", "prediction", "feishu"],
            max_concurrent_tasks=1,
            priority=8,
        )
        super().__init__(metadata)

    async def plan(self, task: Task) -> List[Dict[str, Any]]:
        """生成执行计划"""
        mode = task.context.get("mode", "复盘")
        steps = [
            {"step": 1, "action": "collect_data", "desc": "采集A股收盘数据(ego-browser + MCP)"},
            {"step": 2, "action": "validate_data", "desc": "双源交叉验证"},
            {"step": 3, "action": "build_report", "desc": "组装复盘报告JSON"},
        ]
        if mode in ("复盘", "预测"):
            steps.extend([
                {"step": 4, "action": "settle_previous", "desc": "自动结算上期预测"},
                {"step": 5, "action": "predict_next_day", "desc": "候选池→打分→消息面→LLM研判→Top3"},
                {"step": 6, "action": "record_prediction", "desc": "记录本期预测到模拟盘"},
            ])
        steps.append({"step": len(steps) + 1, "action": "generate_report", "desc": "生成HTML + 合规自检"})
        return steps

    async def run(self, context: EnhancedAgentContext) -> ActionResult:
        """执行 A 股复盘任务 — 委托给现有 workflow"""
        task = context.task
        mode = task.context.get("mode", "复盘")
        legacy_func = task.context.get("legacy_func")
        legacy_kwargs = task.context.get("legacy_kwargs", {})

        log.info("AShareAgent.run: mode=%s, func=%s", mode,
                 legacy_func.__name__ if legacy_func else "None")

        try:
            # 委托给现有 workflow（业务逻辑零修改）
            if legacy_func:
                report = legacy_func(**legacy_kwargs)
            else:
                from .workflow import run_review
                report = run_review(include_prediction=True)

            if not report:
                return ActionResult(
                    action_id=f"ashare_{mode}_empty",
                    agent_name=self.name,
                    success=False,
                    error="workflow 返回空报告",
                )

            log.info("AShareAgent 完成: date=%s, mode=%s",
                     report.get("date", "?"), mode)

            return ActionResult(
                action_id=f"ashare_{mode}_{report.get('date', 'unknown')}",
                agent_name=self.name,
                success=True,
                output=report,  # 直接返回 report dict，RSI 框架会将其作为 final_output
                metadata={
                    "mode": mode,
                    "date": report.get("date"),
                    "has_prediction": bool(report.get("prediction", {}).get("targets")),
                    "source_count": len(report.get("source", [])),
                },
            )

        except Exception as e:
            log.exception("AShareAgent 执行失败: %s", e)
            return ActionResult(
                action_id=f"ashare_{mode}_error",
                agent_name=self.name,
                success=False,
                error=str(e),
            )

    def matches_task(self, task: Task) -> float:
        """覆写匹配逻辑：支持中文关键词匹配"""
        goal = task.goal.lower()
        mode = task.context.get("mode", "")

        score = 0.0
        # 中文关键词匹配
        cn_keywords = ["复盘", "收盘", "预测", "标的", "a股", "股票", "结算"]
        for kw in cn_keywords:
            if kw in goal:
                score += 0.3
        # mode 匹配
        if mode in ("复盘", "预测", "结算"):
            score += 0.5
        # 英文关键词兜底
        en_keywords = ["review", "predict", "stock", "analyze", "market"]
        for kw in en_keywords:
            if kw in goal:
                score += 0.2
        return min(score, 1.0)

    async def validate(self, task: Task) -> bool:
        """验证是否能处理该任务"""
        mode = task.context.get("mode", "")
        return mode in ("复盘", "预测", "结算")
