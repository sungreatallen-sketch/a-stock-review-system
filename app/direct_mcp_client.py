"""通用OAuth Streamable HTTP MCP直连客户端。

与 TdxMcpClient 保持相同调用契约，但OAuth类型可配置：
- oauth: authorization_code + refresh_token，支持PKCE与client_secret_post/none
- static_bearer: 从环境变量读取静态API Key，不落盘
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

log = logging.getLogger("direct_mcp")


class DirectMcpClient:
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path).expanduser()
        self._session_id: str | None = None
        self._request_id = 0
        self._lock = threading.RLock()
        self._tools_cache: dict[str, dict[str, Any]] | None = None
        self._http = requests.Session()
        self._http.trust_env = False

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raise RuntimeError(f"MCP直连配置不存在: {self.config_path}")
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def _save_config(self, cfg: dict[str, Any]):
        self.config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        self.config_path.chmod(0o600)

    @staticmethod
    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    def _reset_http(self):
        try:
            self._http.close()
        except Exception:
            pass
        self._http = requests.Session()
        self._http.trust_env = False

    def _token_post(self, cfg: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        payload = {"client_id": cfg["client_id"], "resource": cfg["resource"], **data}
        if cfg.get("client_secret") and cfg.get("auth_type", "oauth") == "oauth":
            payload["client_secret"] = cfg["client_secret"]
        response = self._http.post(cfg["token_endpoint"], data=payload, timeout=30)
        try:
            body = response.json()
        except Exception as exc:
            raise RuntimeError(f"token接口返回非JSON http={response.status_code}") from exc
        if response.status_code != 200 or "access_token" not in body:
            safe = {k: v for k, v in body.items() if k not in {"access_token", "refresh_token"}}
            raise RuntimeError(f"token接口失败 http={response.status_code} body={safe}")
        body["obtained_at"] = int(time.time())
        if "expires_in" in body:
            body["expires_at"] = int(time.time()) + int(body["expires_in"])
        return body

    def exchange_code(self, cfg: dict[str, Any], code: str, code_verifier: str) -> dict[str, Any]:
        body = self._token_post(cfg, {
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": cfg["redirect_uri"], "code_verifier": code_verifier,
        })
        cfg["tokens"] = body
        self._save_config(cfg)
        return body

    def refresh(self, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = cfg or self._load_config()
        refresh_token = (cfg.get("tokens") or {}).get("refresh_token")
        if not refresh_token:
            raise RuntimeError("refresh_token缺失，需要重新OAuth授权")
        body = self._token_post(cfg, {
            "grant_type": "refresh_token", "refresh_token": refresh_token,
        })
        if not body.get("refresh_token"):
            body["refresh_token"] = refresh_token
        cfg["tokens"] = body
        self._save_config(cfg)
        return cfg

    def _bearer(self) -> str:
        cfg = self._load_config()
        auth_type = cfg.get("auth_type", "oauth")
        if auth_type == "static_bearer":
            value = os.getenv(cfg.get("api_key_env", ""), "")
            if not value:
                raise RuntimeError(f"缺少环境变量{cfg.get('api_key_env')}，无法直连")
            return value.removeprefix("Bearer ")
        tokens = cfg.get("tokens") or {}
        if not tokens.get("access_token"):
            raise RuntimeError("access_token缺失，需要重新OAuth授权")
        if int(tokens.get("expires_at") or 0) - 120 <= time.time():
            cfg = self.refresh(cfg)
            tokens = cfg["tokens"]
        return tokens["access_token"]

    def _json(self, response: requests.Response) -> dict[str, Any]:
        try:
            text = response.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("MCP返回不是UTF-8编码") from exc
        if not text.strip():
            return {}
        if text.lstrip().startswith("event:") or "\ndata:" in text or text.lstrip().startswith("data:"):
            payloads: list[dict[str, Any]] = []
            data_lines: list[str] = []
            for line in text.splitlines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif line.strip() and data_lines:
                    try: payloads.append(json.loads("\n".join(data_lines)))
                    except Exception: pass
                    data_lines = []
            if data_lines:
                try: payloads.append(json.loads("\n".join(data_lines)))
                except Exception: pass
            for payload in payloads:
                if "result" in payload or "error" in payload or "id" in payload:
                    return payload
            return payloads[0] if payloads else {}
        try: body = json.loads(text)
        except Exception as exc:
            raise RuntimeError(f"MCP返回非JSON: {text[:160]}") from exc
        if not isinstance(body, dict):
            raise RuntimeError(f"MCP返回异常JSON类型: {type(body).__name__}")
        return body

    def _post(self, token: str, payload: dict[str, Any], timeout: float):
        cfg = self._load_config()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        response = self._http.post(cfg["resource"], headers=headers, json=payload,
                                   timeout=timeout, allow_redirects=False)
        if response.status_code == 401: raise PermissionError("MCP授权失败(401)")
        if response.status_code == 404: raise ConnectionError("MCP会话不存在(404)")
        if response.status_code >= 400:
            raise RuntimeError(f"MCP HTTP {response.status_code}: {response.content.decode('utf-8', 'ignore')[:240]}")
        return self._json(response), response.headers.get("mcp-session-id")

    def _rpc(self, method: str, params: dict[str, Any] | None = None,
             notify: bool = False, timeout: float = 35.0) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None: payload["params"] = params
        if not notify:
            self._request_id += 1
            payload["id"] = self._request_id
        body, session_id = self._post(self._bearer(), payload, timeout)
        if session_id: self._session_id = session_id
        if body.get("error"): raise RuntimeError(f"MCP {method}错误: {body['error']}")
        return body.get("result") or {}

    def _initialize(self):
        self._session_id = None
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "ashare-review-agent", "version": "1.7"},
        })
        try:
            self._rpc("notifications/initialized", {}, notify=True)
        except requests.RequestException:
            self._reset_http()

    def _call(self, method: str, params: dict[str, Any], timeout: float):
        with self._lock:
            try:
                if not self._session_id: self._initialize()
                return self._rpc(method, params, timeout=timeout)
            except PermissionError:
                self._reset_http(); self.refresh(); self._initialize()
                return self._rpc(method, params, timeout=timeout)
            except (ConnectionError, RuntimeError, requests.RequestException):
                self._reset_http(); self._initialize()
                return self._rpc(method, params, timeout=timeout)

    def list_tools(self, force: bool = False):
        if self._tools_cache and not force: return self._tools_cache
        result = self._call("tools/list", {}, 35)
        tools = result.get("tools") or []
        self._tools_cache = {x.get("name"): x for x in tools if x.get("name")}
        return self._tools_cache

    @staticmethod
    def _normalize(result: dict[str, Any]):
        items=[]
        for content in result.get("content") or []:
            if not isinstance(content, dict): continue
            if content.get("type") == "text" and content.get("text") is not None: items.append(str(content["text"]))
            elif content.get("data") is not None: items.append(str(content["data"]))
        structured=result.get("structuredContent")
        if structured is None and len(items)==1:
            try:
                parsed=json.loads(items[0])
                if isinstance(parsed,dict): structured=parsed
            except Exception: pass
        return {"isError":bool(result.get("isError",False)),"items":items,"raw":items,"structured":structured}

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None, timeout: float = 90.0):
        result=self._call("tools/call", {"name":name,"arguments":arguments or {}}, timeout)
        normalized=self._normalize(result)
        if normalized["isError"]: raise RuntimeError(f"MCP工具{name}调用失败: {normalized['items'][:1]}")
        return normalized

    async def acall_tool(self, name: str, arguments: dict[str, Any] | None = None, timeout: float = 90.0):
        return self.call_tool(name, arguments, timeout)

    async def alist_tools(self, force: bool = False):
        return self.list_tools(force=force)

    def call_tool_sync(self, name: str, arguments: dict[str, Any] | None = None, timeout: float = 90.0):
        return self.call_tool(name, arguments, timeout)

    def login_url(self, callback_server) -> tuple[str, str]:
        cfg=self._load_config(); verifier=self._b64url(secrets.token_bytes(48))
        challenge=self._b64url(hashlib.sha256(verifier.encode()).digest())
        state=secrets.token_urlsafe(24)
        qs={"response_type":"code","client_id":cfg["client_id"],"redirect_uri":cfg["redirect_uri"],
            "scope":cfg.get("scope",""),"state":state,"code_challenge":challenge,
            "code_challenge_method":"S256"}
        if cfg.get("resource"): qs["resource"]=cfg["resource"]
        return f"{cfg['authorization_endpoint']}?{urlencode(qs)}", verifier
