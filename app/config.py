"""配置加载：config/config.yaml + .env 环境变量"""
import os
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def _load_env():
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_load_env()

def load_config() -> dict:
    cfg_path = ROOT / "config" / "config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    # 补充环境变量
    model = cfg.get("model", {})
    model.setdefault("base_url", os.environ.get("DEEPSEEK_BASE_URL", ""))
    model.setdefault("api_key", os.environ.get("DEEPSEEK_API_KEY", ""))
    feishu = cfg.get("feishu", {})
    feishu.setdefault("app_id", os.environ.get("FEISHU_APP_ID", ""))
    feishu.setdefault("app_secret", os.environ.get("FEISHU_APP_SECRET", ""))
    feishu.setdefault("send_to", os.environ.get("FEISHU_SEND_TO", ""))
    mcp = cfg.get("mcp", {})
    mcp.setdefault("token", os.environ.get("WORKBUDDY_MCP_TOKEN", ""))
    return cfg

def paths() -> dict:
    cfg = load_config()
    p = cfg["project"]
    return {
        "data": ROOT / p.get("data_dir", "data"),
        "reports": ROOT / p.get("report_dir", "reports"),
        "logs": ROOT / p.get("log_dir", "logs"),
        "static": ROOT / p.get("static_dir", "static"),
    }
