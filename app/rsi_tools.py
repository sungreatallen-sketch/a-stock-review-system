"""RSI Tool Adapter — 将现有数据采集函数封装为 RSI EnhancedTool

本阶段为可选增强层：将现有数据采集能力暴露给 RSI Framework，
为未来 RSI 学习/优化预留接口。不改变现有调用链。
"""
import logging
from typing import Any, Dict, List, Optional

from rsi_framework.tools.enhanced_base import (
    EnhancedTool,
    EnhancedToolConfig,
    ToolMetadata,
    ToolCategory,
    ToolPermission,
    ToolParameter,
    ToolExecutionContext,
)
from rsi_framework.core.types import ActionResult

log = logging.getLogger("rsi_tools")


class CollectDataTool(EnhancedTool):
    """数据采集 Tool — 封装 Collector.collect()"""

    def __init__(self):
        config = EnhancedToolConfig(
            metadata=ToolMetadata(
                name="ashare_collect_data",
                description="采集A股收盘数据：市场指数、情绪、板块、资金流向。"
                            "优先ego-browser抓取东财公开数据，不足时MCP兜底。",
                category=ToolCategory.DATA_PROCESSING,
                tags=["a-share", "collector", "ego", "mcp"],
                permissions=[ToolPermission.EXECUTE],
                timeout=120,
            ),
            parameters=[
                ToolParameter(name="force", type="boolean", description="是否强制重新采集", required=False),
            ],
        )
        super().__init__(config)

    async def execute(
        self,
        arguments: Dict[str, Any],
        context: Optional[ToolExecutionContext] = None,
    ) -> ActionResult:
        try:
            from .collector import Collector
            data = Collector().collect()
            return ActionResult(
                action_id="collect_data",
                agent_name="tool",
                tool_name=self.name,
                success=bool(data),
                output=data,
                metadata={"keys": list(data.keys()) if isinstance(data, dict) else []},
            )
        except Exception as e:
            log.exception("CollectDataTool 失败: %s", e)
            return ActionResult(
                action_id="collect_data",
                agent_name="tool",
                tool_name=self.name,
                success=False,
                error=str(e),
            )


class McpQueryTool(EnhancedTool):
    """MCP 数据查询 Tool — 封装 McpClient.call()"""

    def __init__(self):
        config = EnhancedToolConfig(
            metadata=ToolMetadata(
                name="ashare_mcp_query",
                description="通过WorkBuddy MCP代理查询金融数据（通达信/同舟/Wind）。",
                category=ToolCategory.EXTERNAL_API,
                tags=["a-share", "mcp", "workbuddy", "finance"],
                permissions=[ToolPermission.EXECUTE],
                timeout=30,
                cost_per_call=0.001,  # MCP 调用可能有成本
            ),
            parameters=[
                ToolParameter(name="tool_name", type="string", description="MCP工具名", required=True),
                ToolParameter(name="arguments", type="object", description="MCP工具参数", required=True),
            ],
        )
        super().__init__(config)

    async def execute(
        self,
        arguments: Dict[str, Any],
        context: Optional[ToolExecutionContext] = None,
    ) -> ActionResult:
        try:
            from .workflow import _get_cached_mcp
            cached = _get_cached_mcp()
            tool_name = arguments["tool_name"]
            tool_args = arguments["arguments"]
            result = cached.call(tool_name, tool_args)
            return ActionResult(
                action_id="mcp_query",
                agent_name="tool",
                tool_name=self.name,
                success=result is not None,
                output=result,
                metadata={"mcp_tool": tool_name},
            )
        except Exception as e:
            log.exception("McpQueryTool 失败: %s", e)
            return ActionResult(
                action_id="mcp_query",
                agent_name="tool",
                tool_name=self.name,
                success=False,
                error=str(e),
            )


def register_tools(framework) -> None:
    """将所有 A 股 Tools 注册到 RSI Framework"""
    from rsi_framework.tools.enhanced_registry import get_enhanced_tool_registry
    registry = get_enhanced_tool_registry()

    tools = [CollectDataTool(), McpQueryTool()]
    for tool in tools:
        registry.register(tool)
        log.info("RSI Tool 已注册: %s", tool.name)

    log.info("RSI Tools 注册完成: 共 %d 个", len(tools))
