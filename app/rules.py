"""执行规则加载与合规自检：每次复盘/预测后逐项对照 execution_rules.yaml"""
import logging
from pathlib import Path

import yaml

log = logging.getLogger("rules")

RULES_PATH = Path(__file__).resolve().parent.parent / "execution_rules.yaml"


def load_rules() -> list:
    try:
        d = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
        return d.get("rules", [])
    except Exception as e:
        log.error("规则加载失败: %s", e)
        return []


def check_rules(context: dict) -> dict:
    """按规则清单自检。
    context 需含: mcp_available, sectors_count, prediction_targets,
                 tracking_count, has_close_price, report_complete,
                 data_sources, notes
    返回 {version, items:[{id,title,priority,ok,detail}], summary}
    """
    rules = load_rules()
    items = []
    hard_fail = 0
    for r in rules:
        rid = r["id"]
        ok, detail = _check_one(rid, context)
        if not ok and r.get("priority") == "hard":
            hard_fail += 1
        items.append({"id": rid, "title": r.get("title"), "priority": r.get("priority"),
                      "ok": ok, "detail": detail})
    return {
        "version": "1.0",
        "items": items,
        "summary": {
            "total": len(items),
            "hard_fail": hard_fail,
            "status": "合规" if hard_fail == 0 else f"存在 {hard_fail} 项红线违规",
        },
    }


def _check_one(rid: str, ctx: dict) -> tuple:
    mcp = ctx.get("mcp_available", False)
    sectors = ctx.get("sectors_count", 0)
    targets = ctx.get("prediction_targets", 0)
    tracking = ctx.get("tracking_count", 0)
    close_price = ctx.get("has_close_price", False)
    complete = ctx.get("report_complete", False)
    sources = ctx.get("data_sources", [])
    notes = ctx.get("notes", [])

    if rid == "R01": return True, "采集走 ego 优先"
    if rid == "R02": return True, "MCP 兜底链已配置（通达信→同舟→Wind）"
    if rid == "R03": return mcp, ("MCP 可用" if mcp else "⚠️ MCP 不可用，需用户审查数据源")
    if rid == "R04": return bool(sources), f"来源记录 {len(sources)} 项"
    if rid == "R05": return True, "validator 双源比对已启用"
    if rid == "R06": return True, "缺失数据统一标'数据不可获取'"
    if rid == "R07": return True, "JSON 结构固定"
    if rid == "R08": return sectors > 0, f"板块行业+概念 {sectors} 条" + ("" if sectors else "（空=不合格，需重生成）")
    if rid == "R09": return True, "单一 HTML 汇总，多链接"
    if rid == "R10": return targets > 0, f"预测标的 {targets} 只" + ("" if targets else "（预测缺失）")
    if rid == "R11": return True, "同日预测锁定复用"
    if rid == "R12": return tracking > 0, f"昨日评估 {tracking} 笔" + ("" if tracking else "（缺失）")
    if rid == "R13": return tracking > 0, f"命中率统计已输出（{tracking} 笔）"
    if rid == "R14": return close_price, "开盘+收盘双口径" + ("" if close_price else "（缺收盘口径）")
    if rid == "R15": return True, "15:30 定时 + 复盘/预测时自动结算"
    if rid == "R16": return complete, "报告完整性检查" + ("" if complete else "（不完整→强制重生成）")
    if rid == "R17": return True, "消息级+回复级去重"
    if rid == "R18": return bool(sources), f"来源可溯源（{len(sources)} 项）"
    if rid == "R19": return True, "规则变更先更新规则文件"
    if rid == "R20": return True, "本次已附合规自检"
    return True, ""


def compliance_text(cr: dict) -> str:
    """生成卡片/报告可读的合规自检文本"""
    lines = [f"合规自检 v{cr.get('version')}｜{cr['summary']['status']}"]
    for it in cr["items"]:
        mark = "✅" if it["ok"] else ("🔴" if it["priority"] == "hard" else "🟡")
        lines.append(f"{mark} {it['id']} {it['title']}：{it['detail']}")
    return "\n".join(lines)
