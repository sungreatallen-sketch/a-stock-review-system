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


class ResilientMcpClient:
    """WorkBuddy优先、通达信/同舟/Wind官方MCP直连兜底的统一入口。

    同舟K线是项目现有调用契约。WorkBuddy不可用时，把它转换成通达信tdx_kline，
    并归一化为现有代码已使用的 data.points 格式。其他直连不支持的现有工具
    不会被伪造，会继续显式抛错。
    """

    def __init__(self, workbuddy: McpClient, tdx=None, tongzhou=None, wind=None):
        self.workbuddy = workbuddy
        self.tdx = tdx
        self.tongzhou = tongzhou
        self.wind = wind

    @property
    def url(self):
        return getattr(self.workbuddy, "url", "")

    @staticmethod
    def _split_ticker(ticker: str):
        raw = str(ticker or "").strip()
        code = raw.split(".")[0]
        suffix = raw.split(".", 1)[1].upper() if "." in raw else ""
        return code, suffix

    @classmethod
    def _tdx_kline_args(cls, args: dict):
        ticker = args.get("ticker") or ""
        code, suffix = cls._split_ticker(ticker)
        market = str(args.get("market") or "a_stock")
        if market == "index":
            setcode = "0" if suffix == "SZ" else "1"
        elif code.startswith(("6", "9", "68")):
            setcode = "1"
        else:
            setcode = "0"
        try:
            limit = max(1, min(1000, int(args.get("limit") or 20)))
        except Exception:
            limit = 20
        return {"code": code, "setcode": setcode, "period": "4", "wantNum": limit}

    @staticmethod
    def _normalize_tdx_kline(resp: dict, end_date: str):
        data = resp.get("structured") if isinstance(resp, dict) else None
        rows = (data or {}).get("Rows") if isinstance(data, dict) else None
        points = []
        for row in rows or []:
            raw_day = str(row.get("Data") or "")
            if len(raw_day) != 8 or not raw_day.isdigit():
                continue
            day = f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:8]}"
            if end_date and day > end_date:
                continue

            def num(key):
                try:
                    value = row.get(key)
                    return float(value) if value not in (None, "") else None
                except (TypeError, ValueError):
                    return None

            points.append({
                "time": day,
                "open": num("Open"),
                "high": num("High"),
                "low": num("Low"),
                "close": num("Close"),
                "volume": num("Volume"),
                "amount": num("Amount"),
            })
        return {"data": {"points": points}, "source": "通达信MCP直连"}

    async def list_tools(self, force: bool = False):
        return await self.workbuddy.list_tools(force=force)

    async def call_tool(self, name: str, arguments: dict = None, timeout: float = 90.0):
        try:
            return await self.workbuddy.call_tool(name, arguments, timeout)
        except Exception as wb_error:
            tool = str(name)
            is_tdx_tool = tool.startswith("tdx-connector_")
            is_kline_fallback = tool == "tongzhou-fin-research_fin_data__get_kline_series"
            is_tongzhou_tool = tool.startswith("tongzhou-fin-research_")
            is_wind_tool = tool.startswith("wind-finance_")
            if not (self.tdx or self.tongzhou or self.wind) or not (
                is_tdx_tool or is_tongzhou_tool or is_wind_tool
            ):
                raise
            log.warning("WorkBuddy MCP失败，尝试官方直连: %s", str(wb_error)[:160])
            if is_tdx_tool:
                if not self.tdx:
                    raise RuntimeError("通达信直连未配置") from wb_error
                return await self.tdx.acall_tool(
                    tool.removeprefix("tdx-connector_"), arguments, timeout
                )
            if is_kline_fallback and self.tdx:
                normalized = self._normalize_tdx_kline(
                    self.tdx.call_tool("tdx_kline", self._tdx_kline_args(arguments or {}), timeout),
                    (arguments or {}).get("end_date", "")
                )
                if not normalized["data"]["points"]:
                    raise RuntimeError("通达信直连K线为空，未伪造数据") from wb_error
                return normalized
            if is_tongzhou_tool:
                if not self.tongzhou:
                    raise RuntimeError("同舟直连未配置或未授权") from wb_error
                return await self._direct_call(
                    self.tongzhou, "tongzhou-fin-research_", tool, arguments, timeout
                )
            if is_wind_tool:
                if not self.wind:
                    raise RuntimeError("Wind直连未配置（缺少WIND_API_KEY或OAuth凭据）") from wb_error
                return await self._direct_call(
                    self.wind, "wind-finance_", tool, arguments, timeout
                )
            raise RuntimeError(f"工具{name}没有可用直连兜底") from wb_error

    async def _direct_call(self, client, prefix: str, name: str,
                           arguments: dict, timeout: float):
        direct_name = str(name).removeprefix(prefix)
        available = client.list_tools()
        if direct_name not in available:
            raise RuntimeError(f"直连MCP缺少工具：{direct_name}（不伪造兜底）")
        return await client.acall_tool(direct_name, arguments, timeout)

    def call_tool_sync(self, name: str, arguments: dict = None, timeout: float = 90.0):
        import asyncio
        return asyncio.run(self.call_tool(name, arguments, timeout))


def create_resilient_client():
    """统一工厂：workflow / collector / sweep共用，避免兜底链分散漂移。"""
    from .config import load_config
    from .tdx_mcp_client import TdxMcpClient
    from .direct_mcp_client import DirectMcpClient
    cfg = load_config()["mcp"]
    workbuddy = McpClient(cfg["proxy_url"], cfg.get("token", ""), cfg["workbuddy_log_dir"])
    root = Path(__file__).resolve().parent.parent
    return ResilientMcpClient(
        workbuddy,
        TdxMcpClient(root / "data" / "tdx_oauth.json"),
        DirectMcpClient(root / "data" / "tongzhou_oauth.json"),
        DirectMcpClient(root / "data" / "wind_mcp.json"),
    )


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
