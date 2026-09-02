"""同舟/Wind MCP直连授权、刷新与测试。token不打印。"""
import argparse
import json
import os
import secrets
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.direct_mcp_client import DirectMcpClient

CONFIGS = {
    "tongzhou": ROOT / "data" / "tongzhou_oauth.json",
    "wind": ROOT / "data" / "wind_mcp.json",
}


def load(provider):
    path = CONFIGS[provider]
    if not path.exists():
        raise SystemExit(f"缺少配置：{path}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return cfg, path


def save(path, cfg):
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


class Callback(HTTPServer):
    def __init__(self, provider, expected_state):
        self.provider = provider
        self.expected_state = expected_state
        self.code = None
        self.error = None
        port = 8790 if provider == "tongzhou" else 8791
        super().__init__(("127.0.0.1", port), Handler)

    def wait(self):
        while self.code is None and self.error is None:
            self.handle_request()
        return self.code, self.error


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        server: Callback = self.server
        expected_path = f"/oauth/{server.provider}/callback"
        if parsed.path != expected_path:
            self.send_response(404); self.end_headers(); return
        if "error" in qs:
            server.error = qs["error"][0]
        elif "code" in qs and "state" in qs and qs["state"][0] == server.expected_state:
            server.code = qs["code"][0]
        else:
            server.error = "state不匹配或缺少code"
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "✅ 授权成功，请回到终端。" if server.code else "❌ 授权失败，请回到终端。"
        self.wfile.write(msg.encode())

    def log_message(self, *args): pass


def login(provider, open_browser=False):
    from app.direct_mcp_client import DirectMcpClient
    cfg, path = load(provider)
    client = DirectMcpClient(path)
    url, verifier = client.login_url(cfg)
    state = url.split("state=")[1].split("&")[0]
    server = Callback(provider, state)
    print(f"请在浏览器完成 {provider} 授权：")
    print(url)
    if open_browser: webbrowser.open(url)
    print("\n等待授权回调...")
    code, error = server.wait(); server.server_close()
    if error or not code: raise SystemExit(f"授权失败：{error or '未获取code'}")
    tokens = client.exchange_code(cfg, code, verifier)
    save(path, cfg)
    import time
    print("✅ 授权完成；token已保存")
    print("has_access_token=", bool(tokens.get("access_token")))
    print("has_refresh_token=", bool(tokens.get("refresh_token")))
    print("expires_at=", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(tokens.get("expires_at", 0))))


def refresh(provider):
    from app.direct_mcp_client import DirectMcpClient
    cfg, path = load(provider)
    DirectMcpClient(path).refresh(cfg)
    save(path, cfg); print("✅ token已刷新")


def test(provider):
    from app.direct_mcp_client import DirectMcpClient
    cfg, path = load(provider)
    tools = DirectMcpClient(path).list_tools(force=True)
    print("status=OK")
    print("tool_count=", len(tools))
    for name in tools: print("tool=", name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["login", "refresh", "test"])
    ap.add_argument("provider", choices=["tongzhou", "wind"])
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    if a.command == "login": login(a.provider, a.open)
    elif a.command == "refresh": refresh(a.provider)
    else: test(a.provider)


if __name__ == "__main__":
    main()
