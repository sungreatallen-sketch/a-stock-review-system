"""RSI Application Adapter — A 股项目接入 RSI Framework 的入口层

职责：
  1. 初始化 RSIAgentFramework
  2. 注册 AShareAgent（EnhancedAgent 实现）
  3. 提供 handle_command() 给 bot.py 调用
  4. 管理 Framework 生命周期

设计约束：
  - 不修改 RSI Framework Core
  - 不修改现有 workflow/predict/collector 等业务模块
  - 关闭 rsi.enabled 后完全走 Legacy Flow
"""
import asyncio
import logging
from typing import Any, Dict, Optional

from rsi_framework.framework import RSIAgentFramework
from rsi_framework.task.models import Task
from rsi_framework.core.types import ExecutionResult, ExecutionStatus

log = logging.getLogger("rsi_app")

# 全局单例
_framework: Optional[RSIAgentFramework] = None


def get_framework() -> RSIAgentFramework:
    """获取或初始化 RSI Framework 单例"""
    global _framework
    if _framework is None:
        _framework = RSIAgentFramework(orchestrator_type="multi_agent")

        # 注册 AShareAgent（注册表是全局单例，需防重复）
        try:
            from .rsi_agent import AShareAgent
            _framework.register_enhanced_agent(AShareAgent())
            log.info("RSI Agent 已注册: ashare_review")
        except Exception:
            log.info("RSI Agent ashare_review 已存在，跳过注册")

        # 注册 Tools（可选增强层，不影响主流程）
        try:
            from .rsi_tools import register_tools
            register_tools(_framework)
        except Exception as e:
            log.warning("RSI Tools 注册失败（不影响主流程）: %s", e)

        # 初始化 Memory（从 recommendation_history.json 只读加载）
        try:
            from .rsi_memory import ASHRMemoryRepository
            from rsi_framework.memory.repository import get_memory_repository
            _memory_repo = ASHRMemoryRepository()
            log.info("RSI Memory 初始化完成（recommendation_history.json 加载）")
        except Exception as e:
            log.warning("RSI Memory 初始化失败（不影响主流程）: %s", e)

        # 初始化 Evaluator
        try:
            from .rsi_evaluator import ASHRSEvaluator
            log.info("RSI Evaluator 初始化完成")
        except Exception as e:
            log.warning("RSI Evaluator 初始化失败（不影响主流程）: %s", e)

        log.info("RSI Framework 初始化完成")
    return _framework


def _run_async(coro):
    """在同步上下文中运行 async 协程（兼容 bot.py 的同步线程模型）"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # 已有事件循环（如 Jupyter / uvicorn），用 nest_asyncio 或直接新线程
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


def handle_command(mode: str, report_func, **kwargs) -> Any:
    """统一命令入口 — 供 bot.py 调用

    Args:
        mode: "复盘" / "预测" / "结算" / "规则"
        report_func: 传统 workflow 函数（如 workflow.run_review）
        **kwargs: 传给 report_func 的参数

    Returns:
        与 Legacy Flow 相同的返回值（report dict 或 None）
    """
    # 构造 RSI Task
    task = Task(
        goal=f"A股{mode}任务",
        context={
            "mode": mode,
            "legacy_func": report_func,
            "legacy_kwargs": kwargs,
        },
        constraints=[
            "必须使用现有 workflow 完成复盘和预测",
            "不得修改任何业务逻辑",
            "返回完整报告 dict",
        ],
        metadata={"source": "feishu", "adapter_version": "1.0.0"},
    )

    framework = get_framework()

    log.info("RSI 接入: 提交 Task goal=%s", task.goal)
    try:
        result: ExecutionResult = _run_async(framework.run(task))

        if result.status in (ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL) and result.final_output:
            # RSI Orchestrator 返回的 final_output 是聚合结构：
            # {"message": ..., "results": {"ashare_review": <report_dict>}}
            # 需要提取实际的 report dict
            output = result.final_output
            if isinstance(output, dict) and "results" in output:
                inner = output.get("results", {})
                # 提取第一个 agent 的结果
                for agent_result in inner.values():
                    if isinstance(agent_result, dict) and "date" in agent_result:
                        output = agent_result
                        break
            log.info("RSI Task 完成: status=%s, date=%s",
                     result.status.value, output.get("date", "?") if isinstance(output, dict) else "?")
            return output
        else:
            log.error("RSI Task 失败: status=%s, actions=%d",
                      result.status.value, len(result.actions))
            # 回退到 Legacy Flow
            log.info("RSI 失败，回退到 Legacy Flow")
            return report_func(**kwargs)

    except Exception as e:
        log.exception("RSI 执行异常，回退到 Legacy Flow: %s", e)
        return report_func(**kwargs)
