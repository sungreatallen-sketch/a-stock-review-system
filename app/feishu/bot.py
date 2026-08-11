"""飞书机器人：手动触发命令入口 + 结果卡片下发
需要用户提供：FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_SEND_TO（见 .env）
"""
import json
import logging
import threading

import httpx

from ..config import load_config

log = logging.getLogger("feishu")
cfg = load_config()["feishu"]

BASE = "https://open.feishu.cn/open-apis"


class FeishuBot:
    def __init__(self):
        self.app_id = cfg.get("app_id", "")
        self.app_secret = cfg.get("app_secret", "")
        self.send_to = cfg.get("send_to", "")
        self._token = None

    @property
    def ready(self):
        return bool(self.app_id and self.app_secret)

    def get_token(self) -> str:
        if self._token:
            return self._token
        r = httpx.post(f"{BASE}/auth/v3/tenant_access_token/internal",
                       json={"app_id": self.app_id, "app_secret": self.app_secret}, timeout=15)
        j = r.json()
        if j.get("code") != 0:
            raise RuntimeError(f"飞书 token 获取失败: {j}")
        self._token = j["tenant_access_token"]
        return self._token

    def send_card(self, content: dict, receive_id: str = None):
        rid = receive_id or self.send_to
        if not self.ready or not rid:
            log.warning("飞书未配置（app_id/app_secret/send_to），跳过发送")
            return False
        r = httpx.post(f"{BASE}/im/v1/messages?receive_id_type=chat_id",
                       headers={"Authorization": f"Bearer {self.get_token()}"},
                       json={"receive_id": rid, "msg_type": "interactive", "content": json.dumps(content, ensure_ascii=False)},
                       timeout=15)
        j = r.json()
        if j.get("code") != 0:
            log.error("飞书发送失败: %s", j)
            return False
        return True

    def send_review_card(self, date_str: str, summary: str, link: str, targets: list = None):
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": f"A股收盘复盘 · {date_str}"},
                       "template": "red"},
            "elements": [
                {"tag": "markdown", "content": summary},
                *([{"tag": "markdown",
                    "content": "**次日标的预测**\n" + "\n".join(
                        f"{i+1}. {t.get('名称','')}（{t.get('代码','')}）—— {t.get('逻辑','')}"
                        for i, t in enumerate(targets))}
                   ] if targets else []),
                {"tag": "action", "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "打开可视化报告"},
                     "type": "primary", "url": link}]},
            ],
        }
        return self.send_card(card)


def handle_event(payload: dict):
    """飞书事件回调：im.message.receive_v1 -> 触发复盘/预测"""
    from ..collector import Collector
    from ..report_builder import build_report
    from ..storage import Storage
    from ..html_report import render_html
    from ..config import paths as get_paths

    schema = payload.get("schema") or {}
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    event = payload.get("event") or {}
    msg = event.get("message") or {}
    text = ""
    content = msg.get("content") or "{}"
    try:
        content = json.loads(content)
        text = content.get("text", "")
    except Exception:
        text = str(content)
    chat_id = (event.get("chat_id") or (msg.get("chat") or {}).get("chat_id") or "")

    bot = FeishuBot()
    if not bot.ready:
        log.warning("收到命令但飞书未配置，忽略：%s", text)
        return {}

    def run():
        try:
            c = Collector()
            raw = c.collect()
            report = build_report(raw)
            p = get_paths()
            st = Storage(p["data"], p["reports"])
            st.save_report(report)
            html = render_html(report)
            (p["reports"] / f"{report['date']}.html").write_text(html, encoding="utf-8")
            link = f"http://{LAN_IP}:{cfg_port()}/report/{report['date']}"
            mi = report["market_index"]
            emo = report["emotion"]
            summary = (
                f"**指数**：上证 {mi.get('上证指数',{}).get('收盘价')} "
                f"({mi.get('上证指数',{}).get('涨跌幅%')}%) ｜ 创业板 {mi.get('创业板指',{}).get('收盘价')} "
                f"({mi.get('创业板指',{}).get('涨跌幅%')}%)\n"
                f"**情绪**：涨停 {emo.get('涨停数量')} / 跌停 {emo.get('跌停数量')} / 炸板 {emo.get('炸板数量')} / 最高连板 {emo.get('最高连板')}"
            )
            bot.send_review_card(report["date"], summary, link)
            log.info("复盘完成并已推送: %s", report["date"])
        except Exception as e:
            log.exception("复盘失败")
            try:
                bot.send_card({"config": {"wide_screen_mode": True},
                               "header": {"title": {"tag": "plain_text", "content": "复盘失败"},
                                          "template": "red"},
                               "elements": [{"tag": "markdown", "content": f"执行出错：{e}"}]},
                              receive_id=chat_id or None)
            except Exception:
                pass

    threading.Thread(target=run, daemon=True).start()
    return {}


def LAN_IP():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def cfg_port():
    return int(load_config()["web"].get("port", 8787))
