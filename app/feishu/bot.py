"""飞书机器人：长连接(WebSocket)模式，手动触发"复盘/预测"命令
参照办公项目(office-agent)已验证的 lark_oapi 长连接方案。
用法: .venv/bin/python -m app.feishu_bot
"""
import json
import logging
import socket
import threading
import time

import os

# 彻底绕过系统代理：clash 对飞书/DeepSeek 路由不稳定，且代理超时会导致飞书长连接重投事件
os.environ.setdefault("NO_PROXY",
                      "open.feishu.cn,*.feishu.cn,larksuite.com,*.larksuite.com,"
                      "api.deepseek.com,127.0.0.1,localhost")
os.environ["no_proxy"] = os.environ["NO_PROXY"]

import requests

# 飞书 API 直连：绕过 macOS 系统代理
requests.Session.trust_env = False

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from ..config import load_config, paths

logger = logging.getLogger("feishu_bot")


def _reply(client: lark.Client, message_id: str, body, msg_type: str, what: str) -> bool:
    # 去重键 = (消息ID, 回复类型)：确认消息(text)与结果卡片(interactive)互不冲突；
    # 仅同类型重复（如重试）会被跳过。
    if _dedup(_REPLIED_MSG, (message_id, msg_type)):
        logger.info("%s已回复过该消息，跳过重复发送", what)
        return True
    req = ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
    last = None
    for attempt in range(1, 3):
        try:
            resp = client.im.v1.message.reply(req)
            if resp.success():
                return True
            last = f"code={resp.code} msg={resp.msg}"
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:120]}"
        logger.warning("%s回复失败(第%d次): %s", what, attempt, last)
        time.sleep(1.0 * attempt)
    logger.error("%s回复最终失败: %s", what, last)
    return False


# 消息级去重：同一消息只处理一次（防飞书长连接重投 / 重试导致的重复）
_PROCESSED_MSG = {}   # message_id -> ts
_REPLIED_MSG = {}     # message_id -> ts


def _dedup(container, key, window: float = 1800.0) -> bool:
    """True=已处理过(应跳过)；首次则记录并返回 False"""
    now = time.time()
    if key in container and now - container[key] < window:
        return True
    container[key] = now
    return False


def reply_text(client: lark.Client, message_id: str, text: str) -> bool:
    body = ReplyMessageRequestBody.builder().content(
        json.dumps({"text": text}, ensure_ascii=False)).msg_type("text").build()
    return _reply(client, message_id, body, "text", "文本")


def reply_card(client: lark.Client, message_id: str, card: dict) -> bool:
    body = ReplyMessageRequestBody.builder().content(
        json.dumps(card, ensure_ascii=False)).msg_type("interactive").build()
    return _reply(client, message_id, body, "interactive", "卡片")


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


def report_link(date_str: str) -> str:
    """用 .local 主机名生成链接（LAN 内稳定，不受 DHCP IP 变化影响）"""
    port = int(load_config()["web"].get("port", 8787))
    host = "localhost"
    try:
        import subprocess
        out = subprocess.run(["scutil", "--get", "LocalHostName"],
                             capture_output=True, text=True, timeout=5)
        h = (out.stdout or "").strip()
        if h:
            host = f"{h}.local"
    except Exception:
        pass
    return f"http://{host}:{port}/report/{date_str}"



def _full_report_and_reply(client: lark.Client, message_id: str, mode: str = "复盘"):
    """统一流程：复盘 + 标的预测 一体，回复合并卡片 + 完整 HTML 报告链接"""
    try:
        reply_text(client, message_id, "收到！正在生成完整报告（收盘复盘 + 次日标的预测），约 20~40 秒…")
        from ..workflow import run_review
        report = run_review(include_prediction=True)
        date_str = report["date"]
        link = report_link(date_str)

        mi = report["market_index"]
        emo = report["emotion"]
        pred = report.get("prediction") or {}
        targets = pred.get("targets") or []
        summary = (
            f"**A股收盘复盘 + 次日标的预测 · {date_str}**\n"
            f"**市场**：上证 {mi.get('上证指数', {}).get('收盘价')} "
            f"({mi.get('上证指数', {}).get('涨跌幅%')}%)｜创业板 "
            f"{mi.get('创业板指', {}).get('收盘价')} ({mi.get('创业板指', {}).get('涨跌幅%')}%)\n"
            f"情绪：涨停 {emo.get('涨停数量')} / 跌停 {emo.get('跌停数量')} / "
            f"炸板 {emo.get('炸板数量')} / 最高连板 {emo.get('最高连板')}"
        )
        if pred.get("market_view"):
            summary += f"\n📌 市场判断：{pred['market_view']}"
        if targets:
            t_lines = ["\n**次日标的（收盘价附近买入，次日开盘卖出）**"]
            for i, t in enumerate(targets, 1):
                buy = t.get("参考买入价(收盘)")
                conf = t.get("confidence") or "中"
                t_lines.append(f"{i}. **{t.get('name')}**（{t.get('code')}）· 置信{conf}"
                               f"｜参考买入 {buy}\n   逻辑：{t.get('reason')}\n   ⚠️风险：{t.get('risk')}")
            summary += "\n" + "\n".join(t_lines)
        summary += "\n⚠️ 仅供研究参考，不构成投资建议。详细图表见完整 HTML 报告。"
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": f"A股复盘 + 标的预测 · {date_str}"},
                       "template": "red"},
            "elements": [
                {"tag": "markdown", "content": summary},
                {"tag": "action", "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "打开完整可视化报告"},
                     "type": "primary", "url": link}]},
                {"tag": "markdown",
                 "content": "数据来源：东财公开数据 + 通达信/同舟/Wind MCP 交叉验证；消息面：同舟 doc_search。"},
            ],
        }
        reply_card(client, message_id, card)
        logger.info("完整报告(%s)已回复: %s", mode, date_str)
    except Exception as e:
        logger.exception("%s失败", mode)
        reply_text(client, message_id, f"{mode}执行失败：{e}\n（查看 Mac 上日志排查）")


def _review_and_reply(client: lark.Client, message_id: str, chat_id: str):
    """复盘：完整报告（复盘+预测一体）"""
    _full_report_and_reply(client, message_id, mode="复盘")


def _predict_and_reply(client: lark.Client, message_id: str):
    """预测：完整报告（复盘+预测一体）"""
    _full_report_and_reply(client, message_id, mode="预测")


def on_message(client: lark.Client, data: P2ImMessageReceiveV1) -> None:
    event = data.event
    if not event or not event.message:
        return
    msg = event.message
    if msg.message_type != "text":
        return
    text = _extract_text(msg.content)
    chat_id = msg.chat_id or ""
    if _dedup(_PROCESSED_MSG, msg.message_id):
        logger.info("消息 %s 已处理过，跳过重复事件", msg.message_id)
        return
    logger.info("收到消息: chat=%s text=%s", chat_id, text[:60])
    # 注意：事件处理必须立即返回（否则飞书长连接会重投事件导致重复），
    # 确认回复与实际工作都放到后台线程执行。
    if any(k in text for k in ("复盘", "收盘", "复盘+")):
        threading.Thread(target=_review_and_reply,
                         args=(client, msg.message_id, chat_id), daemon=True).start()
    elif any(k in text for k in ("结算", "复盘结果", "命中率", "模拟盘")):
        threading.Thread(target=_settle_and_reply,
                         args=(client, msg.message_id), daemon=True).start()
    elif any(k in text for k in ("预测", "标的")):
        threading.Thread(target=_predict_and_reply,
                         args=(client, msg.message_id), daemon=True).start()


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
