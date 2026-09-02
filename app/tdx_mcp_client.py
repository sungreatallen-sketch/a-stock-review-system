"""通达信 MCP 专用轻量客户端。

通达信 streamable-http 服务端会在 initialize 后返回短连接 SSE，并在后续请求
中要求 mcp-session-id。现有通用 MCP SDK 对这个行为不稳定，因此这里直接使用
JSON-RPC，不修改 RSI Framework，也不修改通用 WorkBuddy 客户端。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("tdx_mcp")


class TdxMcpClient:
    """通达信 MCP 客户端，返回结构与 McpClient.call_tool() 保持兼容。"""

    def __init__(self, config_path: str | Path | None = None):
        if config_path is None:
            config_path = Path(__file__).resolve().parent.parent / "data" / "tdx_oauth.json"
        self.config_path = Path(config_path).expanduser()
        self._session_id: str | None = None
        self._request_id = 0
        self._lock = threading.RLock()
        self._tools_cache: dict[str, dict[str, Any]] | None = None
        self._http = requests.Session()
        self._http.trust_env = False

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raise RuntimeError(f"通达信OAuth配置不存在: {self.config_path}")
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    @staticmethod
    def _token_post(url: str, data: dict[str, Any]) -> dict[str, Any]:
        session = requests.Session()
        session.trust_env = False
        response = session.post(url, data=data, timeout=30)
        try:
            body = response.json()
        except Exception as exc:
            raise RuntimeError(f"通达信token接口返回非JSON http={response.status_code}") from exc
        if response.status_code != 200 or "access_token" not in body:
            safe = {k: v for k, v in body.items() if k not in {"access_token", "refresh_token"}}
            raise RuntimeError(f"通达信token刷新失败 http={response.status_code} body={safe}")
        body["obtained_at"] = int(time.time())
        if "expires_in" in body:
            body["expires_at"] = int(time.time()) + int(body["expires_in"])
        return body

    def refresh(self, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = cfg or self._load_config()
        refresh_token = (cfg.get("tokens") or {}).get("refresh_token")
        if not refresh_token:
            raise RuntimeError("通达信refresh_token缺失，需要重新OAuth授权")
        body = self._token_post(cfg["token_endpoint"], {
            "client_id": cfg["client_id"],
            "resource": cfg["resource"],
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })
        if not body.get("refresh_token"):
            body["refresh_token"] = refresh_token
        cfg["tokens"] = body
        self.config_path.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.config_path.chmod(0o600)
        log.info("通达信OAuth token已刷新")
        return cfg

    def _ensure_token(self) -> str:
        cfg = self._load_config()
        tokens = cfg.get("tokens") or {}
        if not tokens.get("access_token"):
            raise RuntimeError("通达信access_token缺失，需要重新OAuth授权")
        if int(tokens.get("expires_at") or 0) - 120 <= time.time():
            cfg = self.refresh(cfg)
            tokens = cfg["tokens"]
        return tokens["access_token"]

    @staticmethod
    def _json_from_response(response: requests.Response) -> dict[str, Any]:
        # 必须从原始字节按 UTF-8 解码。response.text 在缺少charset时会按
        # ISO-8859-1解码，中文UTF-8可能被拆出0x0A假换行，破坏SSE数据帧。
        try:
            text = response.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("通达信MCP返回不是UTF-8编码") from exc
        if not text.strip():
            return {}
        if text.lstrip().startswith("event:") or "\ndata:" in text or text.lstrip().startswith("data:"):
            payloads: list[dict[str, Any]] = []
            data_lines: list[str] = []
            for line in text.splitlines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif line.strip() and data_lines:
                    try:
                        payloads.append(json.loads("\n".join(data_lines)))
                    except Exception:
                        pass
                    data_lines = []
            if data_lines:
                try:
                    payloads.append(json.loads("\n".join(data_lines)))
                except Exception:
                    pass
            for payload in payloads:
                if "result" in payload or "error" in payload or "id" in payload:
                    return payload
            return payloads[0] if payloads else {}
        try:
            body = json.loads(text)
        except Exception as exc:
            raise RuntimeError(f"通达信MCP返回非JSON: {text[:160]}") from exc
        if not isinstance(body, dict):
            raise RuntimeError(f"通达信MCP返回异常JSON类型: {type(body).__name__}")
        return body

    def _post(self, token: str, payload: dict[str, Any], timeout: float) -> tuple[dict[str, Any], str | None]:
        cfg = self._load_config()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        # 本地/远端 MCP 均禁止被系统代理或VPN劫持。
        response = self._http.post(
            cfg["resource"], headers=headers, json=payload, timeout=timeout, allow_redirects=False
        )
        if response.status_code == 401:
            raise PermissionError("通达信MCP授权失败(401)")
        if response.status_code == 404:
            raise ConnectionError("通达信MCP会话不存在(404)")
        if response.status_code >= 400:
            raise RuntimeError(f"通达信MCP HTTP {response.status_code}: {response.text[:200]}")
        return self._json_from_response(response), response.headers.get("mcp-session-id")

    def _rpc(self, method: str, params: dict[str, Any] | None = None,
             notify: bool = False, timeout: float = 35.0) -> dict[str, Any]:
        token = self._ensure_token()
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            self._request_id += 1
            payload["id"] = self._request_id
        body, session_id = self._post(token, payload, timeout)
        if session_id:
            self._session_id = session_id
        if body.get("error"):
            raise RuntimeError(f"通达信MCP {method}错误: {body['error']}")
        if not notify:
            return body.get("result") or {}
        return {}

    def _notify_initialized(self):
        # 通知请求可能被服务端短连接提前关闭；没有响应体时这不代表失败。
        try:
            self._rpc("notifications/initialized", {}, notify=True)
        except requests.RequestException as exc:
            log.info("通达信MCP initialized通知连接被关闭，继续建立新连接")
            self._reset_http()

    def _reset_http(self):
        try:
            self._http.close()
        except Exception:
            pass
        self._http = requests.Session()
        self._http.trust_env = False

    def _initialize(self):
        self._session_id = None
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ashare-review-agent", "version": "1.6.1"},
        })
        self._notify_initialized()

    def _call_with_retry(self, method: str, params: dict[str, Any],
                         timeout: float) -> dict[str, Any]:
        with self._lock:
            try:
                if not self._session_id:
                    self._initialize()
                return self._rpc(method, params, timeout=timeout)
            except PermissionError:
                cfg = self.refresh()
                tokens = cfg["tokens"]
                token_backup = tokens.get("access_token")
                # _rpc 每次都会从磁盘重新读取 token；这里只触发刷新，再重试。
                if not token_backup:
                    raise
                self._initialize()
                return self._rpc(method, params, timeout=timeout)
            except (ConnectionError, RuntimeError, requests.RequestException):
                # 会话可能过期或短连接被服务端重置；下一次请求重新建立会话。
                self._reset_http()
                self._initialize()
                return self._rpc(method, params, timeout=timeout)

    def list_tools(self, force: bool = False) -> dict[str, dict[str, Any]]:
        if self._tools_cache and not force:
            return self._tools_cache
        result = self._call_with_retry("tools/list", {}, timeout=35.0)
        tools = result.get("tools") or []
        self._tools_cache = {item.get("name"): item for item in tools if item.get("name")}
        return self._tools_cache

    async def alist_tools(self, force: bool = False) -> dict[str, dict[str, Any]]:
        return self.list_tools(force=force)

    @staticmethod
    def _normalize_result(result: dict[str, Any]) -> dict[str, Any]:
        items: list[str] = []
        for content in result.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") == "text" and content.get("text") is not None:
                items.append(str(content["text"]))
            elif content.get("data") is not None:
                items.append(str(content["data"]))
        structured = result.get("structuredContent")
        if structured is None and len(items) == 1:
            try:
                parsed = json.loads(items[0])
                if isinstance(parsed, dict):
                    structured = parsed
            except Exception:
                pass
        return {
            "isError": bool(result.get("isError", False)),
            "items": items,
            "raw": items,
            "structured": structured,
        }

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None,
                  timeout: float = 90.0) -> dict[str, Any]:
        params = {"name": name, "arguments": arguments or {}}
        result = self._call_with_retry("tools/call", params, timeout=timeout)
        normalized = self._normalize_result(result)
        if normalized["isError"]:
            raise RuntimeError(f"通达信MCP工具{name}调用失败: {normalized['items'][:1]}")
        return normalized

    async def acall_tool(self, name: str, arguments: dict[str, Any] | None = None,
                         timeout: float = 90.0) -> dict[str, Any]:
        return self.call_tool(name, arguments, timeout)

    def call_tool_sync(self, name: str, arguments: dict[str, Any] | None = None,
                       timeout: float = 90.0) -> dict[str, Any]:
        return self.call_tool(name, arguments, timeout)
