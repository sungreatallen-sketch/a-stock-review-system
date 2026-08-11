"""本地 Web 服务：手机浏览器打开 HTML 复盘报告"""
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from .config import load_config, paths
from .storage import Storage
from .html_report import render_html

log = logging.getLogger("server")
cfg = load_config()
p = paths()
storage = Storage(p["data"], p["reports"])

app = FastAPI(title="A股复盘报告服务")
if p["static"].exists():
    app.mount("/static", StaticFiles(directory=str(p["static"])), name="static")


@app.get("/ip")
def my_ip():
    """返回局域网访问地址（手机同 Wi-Fi 用）"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    port = int(cfg["web"].get("port", 8787))
    return {"lan_ip": ip, "port": port, "report_url": f"http://{ip}:{port}/report/{{date}}"}


@app.get("/", response_class=HTMLResponse)
def index():
    dates = storage.list_dates()
    items = "".join(
        f'<li><a href="/report/{d}">{d}</a> <span style="color:#888">({c[:19]})</span></li>'
        for d, c in dates[:30]
    )
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>A股复盘报告</title></head>
<body style="font-family:-apple-system,sans-serif;background:#0f1115;color:#e8eaed;padding:20px">
<h2>A股收盘复盘报告</h2><ul style="line-height:2">{items or '<li>暂无报告，请先运行复盘</li>'}</ul>
</body></html>"""


@app.get("/report/{date}", response_class=HTMLResponse)
def report_html(date: str):
    data = storage.load_report(date)
    if not data:
        raise HTTPException(404, "报告不存在")
    return HTMLResponse(render_html(data))


@app.get("/api/report/{date}")
def report_json(date: str):
    data = storage.load_report(date)
    if not data:
        raise HTTPException(404, "报告不存在")
    return JSONResponse(data)


@app.post(cfg["feishu"].get("event_path", "/feishu/event"))
async def feishu_event(request: Request):
    payload = await request.json()
    from .feishu.bot import handle_event
    return handle_event(payload)


def start_server():
    import uvicorn
    host = cfg["web"].get("host", "0.0.0.0")
    port = int(cfg["web"].get("port", 8787))
    log.info("启动报告服务 http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning")
