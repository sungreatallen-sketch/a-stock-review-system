"""通达信 MCP OAuth 登录、刷新与直连测试。

配置保存在 data/tdx_oauth.json，权限 600。access_token / refresh_token
不会打印到终端。
"""
import argparse
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "tdx_oauth.json"
sys.path.insert(0, str(ROOT))


def load_config():
    if not CONFIG.exists():
        raise SystemExit("缺少 data/tdx_oauth.json，请先完成动态客户端注册")
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(CONFIG, 0o600)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def token_post(cfg: dict, data: dict):
    import requests

    payload = {
        "client_id": cfg["client_id"],
        "resource": cfg["resource"],
        **data,
    }
    session = requests.Session()
    session.trust_env = False
    r = session.post(cfg["token_endpoint"], data=payload, timeout=30)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:500]}
    if r.status_code != 200 or "access_token" not in body:
        safe = {k: v for k, v in body.items() if k not in {"access_token", "refresh_token"}}
        raise RuntimeError(f"token接口失败 http={r.status_code} body={safe}")
    body["obtained_at"] = int(time.time())
    if "expires_in" in body:
        body["expires_at"] = int(time.time()) + int(body["expires_in"])
    return body


class Callback(HTTPServer):
    def __init__(self, expected_state):
        self.expected_state = expected_state
        self.code = None
        self.error = None
        super().__init__(("127.0.0.1", 8789), Handler)

    def wait(self):
        while self.code is None and self.error is None:
            self.handle_request()
        return self.code, self.error


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        server: Callback = self.server
        if parsed.path != "/oauth/tdx/callback":
            self.send_response(404)
            self.end_headers()
            return
        if "error" in qs:
            server.error = qs["error"][0]
        elif "code" in qs and "state" in qs and qs["state"][0] == server.expected_state:
            server.code = qs["code"][0]
        else:
            server.error = "state不匹配或缺少code"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "✅ 授权成功，请回到终端。" if server.code else "❌ 授权失败，请回到终端。"
        self.wfile.write(msg.encode("utf-8"))

    def log_message(self, *args):
        pass


def login(cfg, open_browser=False):
    verifier = b64url(secrets.token_bytes(48))
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(24)
    qs = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "scope": cfg.get("scope", ""),
        "resource": cfg["resource"],
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = f"{cfg['authorization_endpoint']}?{urllib.parse.urlencode(qs)}"
    server = Callback(state)
    print("请在浏览器中完成通达信授权：")
    print(url)
    if open_browser:
        webbrowser.open(url)
    print("\n等待回调 http://127.0.0.1:8789/oauth/tdx/callback ...")
    code, error = server.wait()
    server.server_close()
    if error or not code:
        raise SystemExit(f"授权失败：{error or '未获取code'}")
    tokens = token_post(cfg, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg["redirect_uri"],
        "code_verifier": verifier,
    })
    cfg["tokens"] = tokens
    save_config(cfg)
    print("✅ 授权完成，token已保存")
    print("has_access_token=", bool(tokens.get("access_token")))
    print("has_refresh_token=", bool(tokens.get("refresh_token")))
    print("expires_at=", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(tokens.get("expires_at", 0))))


def refresh(cfg):
    token = cfg.get("tokens", {}).get("refresh_token")
    if not token:
        raise SystemExit("没有refresh_token，请重新执行 login")
    tokens = token_post(cfg, {
        "grant_type": "refresh_token",
        "refresh_token": token,
    })
    if not tokens.get("refresh_token"):
        tokens["refresh_token"] = token
    cfg["tokens"] = tokens
    save_config(cfg)
    print("✅ token已刷新")


def ensure_token(cfg):
    tokens = cfg.get("tokens", {})
    if not tokens.get("access_token"):
        raise SystemExit("没有access_token，请执行 login")
    if tokens.get("expires_at", 0) - 120 <= time.time():
        if tokens.get("refresh_token"):
            refresh(cfg)
            tokens = cfg["tokens"]
        else:
            raise SystemExit("access_token已过期且无refresh_token，请重新 login")
    return tokens["access_token"]


def test(cfg):
    from app.tdx_mcp_client import TdxMcpClient

    client = TdxMcpClient(CONFIG)
    tools = client.list_tools(force=True)
    print("status=OK")
    print("tool_count=", len(tools))
    for name in tools:
        print("tool=", name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["login", "test", "refresh"])
    ap.add_argument("--open", action="store_true", help="自动打开浏览器")
    args = ap.parse_args()
    cfg = load_config()
    if args.command == "login":
        login(cfg, args.open)
    elif args.command == "refresh":
        refresh(cfg)
    else:
        test(cfg)


if __name__ == "__main__":
    main()
