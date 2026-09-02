"""WorkBuddy MCP 聚合代理客户端：自动发现 token，按需调用工具"""
import asyncio
import glob
import json
import logging
import socket
import os
import re
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

log = logging.getLogger("mcp_client")


def _no_proxy_httpx_client(headers=None, timeout=None, auth=None):
    """MCP local proxy must never pass through a system/VPN HTTP proxy."""
    import httpx

    kwargs = {"follow_redirects": True, "trust_env": False}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if headers is not None:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)


def discover_proxy(log_dir: str):
    """从 WorkBuddy 最新日志发现 connector-proxy 的 (url, token)。
    收集所有候选端口，逐个验证是否在监听，返回第一个可用者（WorkBuddy 重启端口会变）。"""
    ld = Path(log_dir).expanduser()
    if not ld.exists():
        return "", ""
    files = sorted(glob.glob(str(ld / "2026-*" / "*cli_host*.log")), reverse=True)
    url_marker = re.compile(r'"url"\s*:\s*"http://127\.0\.0\.1:(\d+)/mcp"')
    auth = re.compile(r'"Authorization"\s*:\s*"Bearer\s+([A-Za-z0-9_\-\.=]+)"')
    cands = {}
    for f in files[:20]:
        try:
            content = Path(f).read_text(encoding="utf-8", errors="ignore")
            for m in url_marker.finditer(content):
                seg = content[m.start():m.start() + 5000]
                a = auth.search(seg)
                if a:
                    cands.setdefault(m.group(1), a.group(1))
        except Exception:
            continue
    # 验证端口是否在监听，返回第一个可用
    for port, token in cands.items():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(("127.0.0.1", int(port)))
            s.close()
            return f"http://127.0.0.1:{port}/mcp", token
        except Exception:
            continue
    # 都没有监听时返回空，让调用方保留显式配置的直连 URL。
    # 旧逻辑会退回 stale 端口，看起来像 502，实际是连接不到本地代理。
    return "", ""


class McpClient:
    """封装对 WorkBuddy 聚合 MCP 代理的调用"""

    def __init__(self, url: str, token: str = "", log_dir: str = "~/.workbuddy/logs"):
        self.url = url
        self.token = token
        self._tools_cache = None
        # 自动发现最新代理（WorkBuddy 重启后端口会变；发现到则覆盖）
        d_url, d_token = discover_proxy(log_dir)
        if d_url:
            self.url = d_url
            if d_token:
                self.token = d_token

    @property
    def headers(self):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def _session(self):
        return streamablehttp_client(
            self.url, headers=self.headers, httpx_client_factory=_no_proxy_httpx_client
        )

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
