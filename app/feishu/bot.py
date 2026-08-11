"""飞书机器人：长连接(WebSocket)模式，手动触发"复盘/预测"命令
参照办公项目(office-agent)已验证的 lark_oapi 长连接方案。
用法: .venv/bin/python -m app.feishu_bot
"""
import json
import logging
import socket
import threading
import time

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from ..config import load_config, paths

logger = logging.getLogger("feishu_bot")


def reply_text(client: lark.Client, message_id: str, text: str) -> bool:
    body = ReplyMessageRequestBody.builder().content(
        json.dumps({"text": text}, ensure_ascii=False)).msg_type("text").build()
    req = ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        logger.error("回复失败: code=%s msg=%s", resp.code, resp.msg)
    return resp.success()


def reply_card(client: lark.Client, message_id: str, card: dict) -> bool:
    body = ReplyMessageRequestBody.builder().content(
        json.dumps(card, ensure_ascii=False)).msg_type("interactive").build()
    req = ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        logger.error("卡片回复失败: code=%s msg=%s", resp.code, resp.msg)
    return resp.success()


def _extract_text(content: str) -> str:
    try:
        return json.loads(content or "{}").get("text", "")
    except Exception:
        return ""


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _review_and_reply(client: lark.Client, message_id: str, chat_id: str):
    """后台执行：采集→验证→生成报告→回复卡片+链接"""
    try:
        from ..collector import Collector
        from ..report_builder import build_report
        from ..storage import Storage
        from ..html_report import render_html

        p = paths()
        st = Storage(p["data"], p["reports"])
        report = build_report(Collector().collect())
        st.save_report(report)
        (p["reports"] / f"{report['date']}.html").write_text(
            render_html(report), encoding="utf-8")

        port = int(load_config()["web"].get("port", 8787))
        link = f"http://{lan_ip()}:{port}/report/{report['date']}"
        mi = report["market_index"]
        emo = report["emotion"]
        summary = (
            f"**A股收盘复盘 · {report['date']}**\n"
            f"上证 {mi.get('上证指数', {}).get('收盘价')} "
            f"({mi.get('上证指数', {}).get('涨跌幅%')}%)｜创业板 "
            f"{mi.get('创业板指', {}).get('收盘价')} ({mi.get('创业板指', {}).get('涨跌幅%')}%)\n"
            f"涨停 {emo.get('涨停数量')} / 跌停 {emo.get('跌停数量')} / "
            f"炸板 {emo.get('炸板数量')} / 最高连板 {emo.get('最高连板')}"
        )
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": f"A股收盘复盘 · {report['date']}"},
                       "template": "red"},
            "elements": [
                {"tag": "markdown", "content": summary},
                {"tag": "action", "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "打开可视化报告"},
                     "type": "primary", "url": link}]},
                {"tag": "markdown",
                 "content": "所有数据均来自真实可追溯来源（东财公开数据 + 通达信/同舟/Wind MCP 交叉验证）。"},
            ],
        }
        reply_card(client, message_id, card)
        logger.info("复盘完成并已回复: %s", report["date"])
    except Exception as e:
        logger.exception("复盘失败")
        reply_text(client, message_id, f"复盘执行失败：{e}\n（查看 Mac 上日志排查）")


def on_message(client: lark.Client, data: P2ImMessageReceiveV1) -> None:
    event = data.event
    if not event or not event.message:
        return
    msg = event.message
    if msg.message_type != "text":
        return
    text = _extract_text(msg.content)
    chat_id = msg.chat_id or ""
    logger.info("收到消息: chat=%s text=%s", chat_id, text[:60])
    if any(k in text for k in ("复盘", "收盘", "复盘+")):
        reply_text(client, msg.message_id, "收到！正在采集今日收盘数据并交叉验证，约 20~40 秒，请稍候…")
        threading.Thread(target=_review_and_reply,
                         args=(client, msg.message_id, chat_id), daemon=True).start()
    elif any(k in text for k in ("预测", "标的")):
        reply_text(client, msg.message_id,
                   "标的预测功能正在开发中（M2 候选池+回测 → M3 消息面+模型研判），"
                   "当前可先发送「复盘」查看今日收盘复盘。")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    app_id = cfg["feishu"].get("app_id", "")
    app_secret = cfg["feishu"].get("app_secret", "")
    if not app_id or not app_secret:
        raise SystemExit("请在 .env 中配置 FEISHU_APP_ID / FEISHU_APP_SECRET")

    client = (lark.Client.builder().app_id(app_id).app_secret(app_secret)
              .log_level(lark.LogLevel.INFO).build())

    def handle(data: P2ImMessageReceiveV1) -> None:
        on_message(client, data)

    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(handle).build())
    ws_client = lark.ws.Client(app_id, app_secret, event_handler=handler,
                               log_level=lark.LogLevel.INFO)
    logger.info("复盘助手已启动，正在连接飞书长连接…")
    ws_client.start()


if __name__ == "__main__":
    main()
