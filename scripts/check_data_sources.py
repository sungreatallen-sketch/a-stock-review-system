"""数据源健康检查：THS 是行情主源，MCP 是消息/兜底源。
输出机器可读 JSON；任一源异常都给出明确 status，不伪造可用性。
"""
import asyncio
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check_ths():
    try:
        from app.ths_client import get_ths_client
        client = get_ths_client()
        end = date.today()
        start = end - timedelta(days=8)
        days = client.trading_days(start, end)
        if not days:
            return {"status": "FAIL", "detail": "交易日历为空"}
        return {"status": "OK", "detail": f"最近交易日 {days[-1]}", "days": days}
    except Exception as e:
        return {"status": "FAIL", "detail": str(e)[:240]}


def check_mcp():
    try:
        from app.mcp_client import McpClient
        from app.config import load_config
        cfg = load_config()["mcp"]
        client = McpClient(cfg["proxy_url"], cfg.get("token", ""), cfg["workbuddy_log_dir"])
        tools = asyncio.run(asyncio.wait_for(client.list_tools(force=True), 8))
        return {"status": "OK", "url": client.url, "tool_count": len(tools or {})}
    except Exception as e:
        # MCP SDK 常把单个连接错误包成 TaskGroup/ExceptionGroup；
        # 只显示 "unhandled errors" 对运维没有价值。
        root = e
        if hasattr(e, "exceptions") and e.exceptions:
            root = e.exceptions[0]
        return {"status": "FAIL", "detail": f"{type(root).__name__}: {root}"[:240]}


def check_tdx_direct():
    try:
        from app.tdx_mcp_client import TdxMcpClient
        tools = TdxMcpClient().list_tools(force=True)
        if not tools:
            return {"status": "FAIL", "detail": "工具清单为空"}
        return {"status": "OK", "tool_count": len(tools), "detail": "通达信OAuth直连"}
    except Exception as e:
        return {"status": "FAIL", "detail": f"{type(e).__name__}: {e}"[:240]}


if __name__ == "__main__":
    result = {
        "checked_at": date.today().isoformat(),
        "ths": check_ths(),
        "mcp": check_mcp(),
        "tdx_direct": check_tdx_direct(),
    }
    print(json.dumps(result, ensure_ascii=False))
