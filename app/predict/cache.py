"""MCP 调用结果缓存（SQLite），回测多次调用避免重复请求"""
import hashlib
import json
import sqlite3
from pathlib import Path


class MCPCache:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mcp_cache (
                key TEXT PRIMARY KEY,
                result TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
        conn.commit()
        conn.close()

    @staticmethod
    def _key(name: str, args: dict) -> str:
        raw = json.dumps({"n": name, "a": args}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode()).hexdigest()

    def get(self, name: str, args: dict):
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT result FROM mcp_cache WHERE key=?",
                           (self._key(name, args),)).fetchone()
        conn.close()
        return json.loads(row[0]) if row else None

    def set(self, name: str, args: dict, result):
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT OR REPLACE INTO mcp_cache(key, result, created_at) VALUES(?,?,?)",
                     (self._key(name, args), json.dumps(result, ensure_ascii=False, default=str),
                      __import__("datetime").datetime.now().isoformat()))
        conn.commit()
        conn.close()


class CachedMcp:
    """带缓存的 MCP 调用器：call(name, args) 先查缓存，未命中则调 MCP 并缓存"""

    def __init__(self, mcp, cache: MCPCache):
        self.mcp = mcp
        self.cache = cache

    def call(self, name: str, args: dict = None, timeout: float = 90):
        args = args or {}
        hit = self.cache.get(name, args)
        if hit is not None:
            return hit
        from ..mcp_client import parse_mcp_json
        resp = self.mcp.call_tool_sync(name, args, timeout=timeout)
        payload = resp.get("structured")
        if payload is None:
            payload = parse_mcp_json(resp)
        self.cache.set(name, args, payload)
        return payload
