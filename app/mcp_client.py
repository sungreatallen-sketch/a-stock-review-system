"""WorkBuddy MCP 聚合代理客户端：自动发现 token，按需调用工具"""
import asyncio
import glob
import json
import logging
import os
import re
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

log = logging.getLogger("mcp_client")


def discover_token(log_dir: str) -> str:
    """从 WorkBuddy 最新日志中提取 connector-proxy 的 Bearer token"""
    ld = Path(log_dir).expanduser()
    if not ld.exists():
        return ""
    files = sorted(glob.glob(str(ld / "2026-*" / "*cli_host*.log")), reverse=True)
    marker = re.compile(r'127\.0\.0\.1:5505[0-9]/mcp')
    auth = re.compile(r'"Authorization":"Bearer\s+([A-Za-z0-9_\-\.=]+)"')
    for f in files[:20]:
        try:
            content = Path(f).read_text(encoding="utf-8", errors="ignore")
            m = marker.search(content)
            if m:
                seg = content[m.start():m.start() + 4000]
                a = auth.search(seg)
                if a:
                    return a.group(1)
        except Exception:
            continue
    return ""


class McpClient:
    """封装对 WorkBuddy 聚合 MCP 代理的调用"""

    def __init__(self, url: str, token: str = "", log_dir: str = "~/.workbuddy/logs"):
        self.url = url
        self.token = token or discover_token(log_dir)
        self._tools_cache = None

    @property
    def headers(self):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def _session(self):
        return streamablehttp_client(self.url, headers=self.headers)

    async def list_tools(self, force=False):
        if self._tools_cache and not force:
            return self._tools_cache
        async with (await self._session()) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                self._tools_cache = {t.name: t for t in tools.tools}
                return self._tools_cache

    async def call_tool(self, name: str, arguments: dict = None, timeout: float = 90.0):
        async with (await self._session()) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await asyncio.wait_for(
                    session.call_tool(name, arguments or {}), timeout=timeout
                )
                # 统一返回结构化内容
                items = []
                for c in result.content:
                    if hasattr(c, "text") and c.text:
                        items.append(c.text)
                    elif hasattr(c, "data") and c.data:
                        items.append(str(c.data))
                structured = getattr(result, "structuredContent", None)
                return {"isError": getattr(result, "isError", False),
                        "items": items, "raw": items, "structured": structured}

    def call_tool_sync(self, name: str, arguments: dict = None, timeout: float = 90.0):
        return asyncio.run(self.call_tool(name, arguments, timeout))


def parse_mcp_json(result: dict):
    """把 MCP 工具返回的文本解析成 JSON；失败则原样返回文本列表"""
    texts = result.get("items") or []
    out = []
    for t in texts:
        try:
            out.append(json.loads(t))
        except Exception:
            out.append(t)
    if len(out) == 1:
        return out[0]
    return out
