"""自动复盘后发送飞书：读取今日报告，构建卡片+图片，发送到用户飞书
用法: .venv/bin/python scripts/send_review.py [--date YYYY-MM-DD] [--chat oc_xxx]
"""
import argparse
import json
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("NO_PROXY", "open.feishu.cn,*.feishu.cn,larksuite.com,*.larksuite.com,api.deepseek.com,127.0.0.1,localhost")
os.environ["no_proxy"] = os.environ["NO_PROXY"]
import requests
requests.Session.trust_env = False

from app.config import load_config, paths

log = logging.getLogger("send_review")

CHAT_DEFAULT = "oc_62fe4c4edc5797700a96036b5c45599e"  # 用户与机器人对话的 chat_id


def feishu_token() -> str:
    cfg = load_config()
    tok = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                        json={"app_id": cfg["feishu"].get("app_id", ""),
                              "app_secret": cfg["feishu"].get("app_secret", "")}, timeout=15).json()
    return tok.get("tenant_access_token", "") if tok.get("code") == 0 else ""


def send_card(chat_id: str, card: dict) -> bool:
    token = feishu_token()
    if not token:
        log.error("飞书 token 获取失败")
        return False
    r = requests.post("https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                      headers={"Authorization": f"Bearer {token}"},
                      json={"receive_id": chat_id, "msg_type": "interactive",
                            "content": json.dumps(card, ensure_ascii=False)},
                      timeout=15).json()
    if r.get("code") != 0:
        log.error("飞书消息发送失败: %s", r)
        return False
    return True


def build_summary(d: dict) -> str:
    mi = d.get("market_index", {})
    emo = d.get("emotion", {})
    pred = d.get("prediction", {})
    targets = pred.get("targets", [])
    date_str = d.get("date", "")
    sh = mi.get("上证指数", {}) or {}
    cyb = mi.get("创业板指", {}) or {}
    summary = (
        f"**A股收盘复盘 + 次日标的预测 · {date_str}**\n"
        f"**市场**：上证 {sh.get('收盘价')} ({sh.get('涨跌幅%')}%)｜创业板 "
        f"{cyb.get('收盘价')} ({cyb.get('涨跌幅%')}%)\n"
        f"情绪：涨停 {emo.get('涨停数量')} / 跌停 {emo.get('跌停数量')} / "
        f"炸板 {emo.get('炸板数量')} / 最高连板 {emo.get('最高连板')}"
    )
    if pred.get("market_view"):
        summary += f"\n📌 市场判断：{pred['market_view']}"
    # 昨日推荐复盘（优先用 latest_prediction，确保显示昨天的推荐而非前天）
    tracking = d.get("tracking", {})
    stats = tracking.get("stats", {})
    latest_pred = tracking.get("latest_prediction", {})
    recent = stats.get("recent", [])
    # 累计统计
    if stats.get("count"):
        summary += (f"\n📈 **累计命中率**：{stats.get('win_rate')}%"
                    f"（{stats.get('count')} 笔）｜平均 {stats.get('avg_ret')}%")
    # 昨日推荐标的（从 latest_prediction 读取，确保是昨天的推荐）
    pred_targets = latest_pred.get("targets", [])
    pred_date = latest_pred.get("date", "")
    if pred_targets:
        # 检查是否有结算数据
        settled_rows = [r for r in recent if r.get("date") == pred_date]
        if settled_rows:
            # 已结算：显示买卖价格和收益
            wins = sum(1 for r in settled_rows if (r.get("ret") or 0) > 0)
            prev_lines = [f"\n\n📈 **昨日推荐标的（{pred_date}）**"]
            for r in settled_rows:
                s = "+" if (r.get("ret") or 0) >= 0 else ""
                prev_lines.append(
                    f"· {r.get('name')}：买入 {r.get('buy_date') or 'T+1'}收盘 {r.get('buy')} → 卖出 "
                    f"{r.get('sell_date') or 'T+2'}收盘 {r.get('sell_close')}（{s}{r.get('ret')}%）")
            prev_lines.append(f"命中 {wins}/{len(settled_rows)}")
            summary += "\n".join(prev_lines)
        else:
            # 未结算：只显示推荐标的（无收益数据）
            prev_lines = [f"\n\n📈 **昨日推荐标的（{pred_date}，待结算）**"]
            for t in pred_targets:
                buy = t.get("参考买入价(收盘)", "—")
                prev_lines.append(f"· {t.get('name')}（{t.get('code')}）：T日收盘参考 {buy}")
            summary += "\n".join(prev_lines)
    # 今日预测
    if targets:
        t_lines = ["\n**下一执行窗口标的（T+1收盘买入，T+2收盘卖出）**"]
        for i, t in enumerate(targets, 1):
            buy = t.get("参考买入价(收盘)")
            conf = t.get("confidence") or "中"
            stop = t.get("stop_loss")
            sell = t.get("sell_target")
            plan = f"｜止损 {stop}" if stop else ""
            if sell:
                plan += f"｜目标 {sell}"
            t_lines.append(f"{i}. **{t.get('name')}**（{t.get('code')}）· 置信{conf}｜参考买入 {buy}{plan}")
        summary += "\n" + "\n".join(t_lines)
    summary += "\n⚠️ 仅供研究参考，不构成投资建议。"
    return summary


def send_report(date_str: str, chat_id: str) -> bool:
    p = paths()
    fp = p["reports"] / f"{date_str}.json"
    if not fp.exists():
        log.error("报告不存在: %s", fp)
        return False
    with open(fp, encoding="utf-8") as f:
        d = json.load(f)
    summary = build_summary(d)
    links = {}
    # 生成链接
    from app.feishu.bot import report_link
    links = report_link(date_str)
    primary_url = links.get("public") or links.get("ip", "")
    card = {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": f"A股复盘 + 标的预测 · {date_str}"},
                   "template": "red"},
        "elements": [
            {"tag": "markdown", "content": summary},
            {"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "打开完整可视化报告"},
                 "type": "primary", "url": primary_url}]},
            {"tag": "markdown",
             "content": "数据来源：东财公开数据 + 通达信/同舟/Wind MCP 交叉验证；消息面：同舟 doc_search。"},
        ],
    }
    ok = send_card(chat_id, card)
    # 发送报告图片
    try:
        from app.feishu.bot import _send_report_images
        import lark_oapi as lark
        client = lark.Client.builder().app_id(load_config()["feishu"]["app_id"]).app_secret(load_config()["feishu"]["app_secret"]).build()
        _send_report_images(client, chat_id, str(p["reports"] / f"{date_str}.html"))
    except Exception as e:
        log.warning("图片发送失败（不影响卡片）: %s", e)
    log.info("自动复盘报告已发送到飞书: %s (%s)", date_str, chat_id)
    return ok


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from datetime import date
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=str(date.today()))
    ap.add_argument("--chat", default=CHAT_DEFAULT)
    args = ap.parse_args()
    sys.exit(0 if send_report(args.date, args.chat) else 1)
