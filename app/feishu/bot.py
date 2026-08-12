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
    """获取真实局域网 IP：优先默认路由网卡（Wi-Fi/以太网），排除 VPN(utun/tun)虚拟网卡。
    避免用户开 VPN(如 clash TUN) 时取到 VPN 网卡 IP 导致手机打不开。"""
    import subprocess as _sp
    try:
        out = _sp.run(["route", "-n", "get", "default"], capture_output=True, text=True, timeout=5)
        iface = None
        for line in out.stdout.splitlines():
            if "interface:" in line:
                iface = line.split(":", 1)[1].strip()
        if iface and not iface.startswith(("utun", "tun")):
            ip = _sp.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True, timeout=5).stdout.strip()
            if ip:
                return ip
    except Exception:
        pass
    # 兜底：socket 法
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def report_link(date_str: str) -> dict:
    """生成所有可用的报告链接：公网隧道(如有) > .local > 当前IP；并附当前IP便于排查"""
    port = int(load_config()["web"].get("port", 8787))
    ip = lan_ip()
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
    local = f"http://{host}:{port}/report/{date_str}"
    ip_link = f"http://{ip}:{port}/report/{date_str}"
    public = _read_tunnel_url()
    return {"public": public, "local": local, "ip": ip_link, "ip_addr": ip}


def _read_tunnel_url() -> str:
    """从 cloudflared 日志读取当前公网隧道地址（未启用隧道返回空）"""
    try:
        logp = Path("/Users/yage/ashare-logs/tunnel.log")
        if not logp.exists():
            return ""
        content = logp.read_text(encoding="utf-8", errors="ignore")
        for line in reversed(content.splitlines()):
            i = line.find("https://")
            if i >= 0 and "trycloudflare.com" in line:
                url = line[i:].strip().rstrip(".,;)")
                if url.startswith("https://") and " " not in url:
                    return url
    except Exception:
        pass
    return ""



def _full_report_and_reply(client: lark.Client, message_id: str, mode: str = "复盘"):
    """统一流程：复盘 + 标的预测 一体，回复合并卡片 + 完整 HTML 报告链接"""
    try:
        reply_text(client, message_id, "收到！正在生成完整报告（收盘复盘 + 次日标的预测），约 20~40 秒…")
        from ..workflow import run_review
        report = run_review(include_prediction=True)
        date_str = report["date"]
        links = report_link(date_str)
        primary_url = links["public"] or links["ip"]

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
        # 顶部醒目：昨日推荐命中率摘要（问题4：保证用户第一眼看到）
        tstats0 = (report.get("tracking") or {}).get("stats") or {}
        if tstats0.get("count"):
            summary += (f"\n📈 **昨日推荐复盘**：命中率 {tstats0.get('win_rate')}%"
                        f"（{tstats0.get('count')} 笔）｜平均 {tstats0.get('avg_ret')}%"
                        f"（昨收→今收）")
        tracking = report.get("tracking") or {}
        tstats = tracking.get("stats") or {}
        rows = (tstats.get("recent") or [])
        if rows:
            # 取最近一个已结算日的推荐明细（昨日推荐复盘）
            latest_date = rows[0].get("date")
            day_rows = [r for r in rows if r.get("date") == latest_date]
            wins = sum(1 for r in day_rows if (r.get("ret") or 0) > 0)
            prev_lines = [f"\n\n📈 **昨日推荐复盘（昨收买 → 今收卖，收盘-收盘）**"]
            for r in day_rows:
                s = "+" if (r.get("ret") or 0) >= 0 else ""
                prev_lines.append(
                    f"· {r.get('name')}：昨收 {r.get('buy')} → 今收 {r.get('sell_close')}（{s}{r.get('ret')}%）")
            prev_lines.append(f"命中 {wins}/{len(day_rows)}｜累计命中率 {tstats.get('win_rate')}%"
                              f"（{tstats.get('count')} 笔）｜平均 {tstats.get('avg_ret')}%")
            summary += "\n".join(prev_lines)
        if targets:
            t_lines = ["\n**次日标的（收盘价附近买入，次日开盘卖出）**"]
            for i, t in enumerate(targets, 1):
                buy = t.get("参考买入价(收盘)")
                conf = t.get("confidence") or "中"
                stop = t.get("stop_loss")
                sell = t.get("sell_target")
                plan = f"｜止损 {stop}" if stop else ""
                if sell:
                    plan += f"｜目标 {sell}"
                t_lines.append(f"{i}. **{t.get('name')}**（{t.get('code')}）· 置信{conf}"
                               f"｜参考买入 {buy}{plan}\n   逻辑：{t.get('reason')}\n   ⚠️风险：{t.get('risk')}")
            summary += "\n" + "\n".join(t_lines)
        summary += "\n⚠️ 仅供研究参考，不构成投资建议。详细图表见完整 HTML 报告。"
        comp = (report.get("compliance") or {}).get("summary") or {}
        if comp.get("hard_fail"):
            fails = [it for it in (report.get("compliance") or {}).get("items", [])
                     if not it.get("ok") and it.get("priority") == "hard"]
            lines = ["\n🧾 **合规自检**：存在红线违规，需关注"]
            for it in fails[:3]:
                lines.append(f"· ❌ {it['id']} {it['title']}：{it['detail']}")
            summary += "\n".join(lines)
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": f"A股复盘 + 标的预测 · {date_str}"},
                       "template": "red"},
            "elements": [
                {"tag": "markdown", "content": summary},
                {"tag": "action", "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "打开完整可视化报告"},
                     "type": "primary", "url": primary_url},
                    {"tag": "button", "text": {"tag": "plain_text", "content": "备用链接(.local)"},
                     "type": "default", "url": links["local"]}]},
                {"tag": "markdown",
                 "content": "数据来源：东财公开数据 + 通达信/同舟/Wind MCP 交叉验证；消息面：同舟 doc_search。\n"
                            "📱 若打不开：请确认手机与 Mac 连同一 Wi-Fi，Mac 当前 IP：**" + links["ip_addr"] + "**；"
                            "或用 Safari 打开：`" + links["ip"] + "`"},
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
    elif any(k in text for k in ("规则", "约束", "合规")):
        from ..rules import load_rules
        rs = load_rules()
        lines = ["**执行规则清单（v1.0，共 %d 条）**" % len(rs)]
        for r in rs:
            mark = "🔴" if r.get("priority") == "hard" else "🟡"
            lines.append(f"{mark} {r['id']} {r['title']}")
        lines.append("\n完整规则见 RULES.md / execution_rules.yaml（Mac 项目根目录）")
        reply_text(client, msg.message_id, "\n".join(lines))
    elif any(k in text for k in ("预测", "标的")):
        threading.Thread(target=_predict_and_reply,
                         args=(client, msg.message_id), daemon=True).start()


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from ..rules import preload_rules
    pre = preload_rules()          # R30：机器人启动时预读执行规则
    if not pre.get("ok"):
        logger.error("R30 违规：规则预读失败，机器人不启动 - %s", pre.get("detail"))
        raise SystemExit("规则文件异常，禁止启动")
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
