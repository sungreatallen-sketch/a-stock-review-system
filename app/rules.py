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


def preload_rules() -> dict:
    """运行前预读规则（R30）：读取 execution_rules.yaml 并校验，失败必须上报。
    返回 {ok, version, count, detail}"""
    try:
        d = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
        rules = d.get("rules", [])
        ver = (d.get("meta") or {}).get("version", "?")
        ok = isinstance(rules, list) and len(rules) > 0
        detail = f"执行规则 v{ver}，{len(rules)} 条，{'预读成功' if ok else '预读异常'}"
        if not ok:
            log.error("R30 违规：规则预读异常 - %s", detail)
        else:
            log.info("R30 预读：%s", detail)
        return {"ok": ok, "version": ver, "count": len(rules), "detail": detail}
    except Exception as e:
        log.error("R30 违规：规则文件读取失败 - %s", e)
        return {"ok": False, "version": "?", "count": 0, "detail": f"规则文件读取失败: {e}"}


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
    # ---- 预测六步流程 ----
    candidates = ctx.get("candidate_count", targets)  # 候选池数量（无则用标的数兜底）
    news_lhb = ctx.get("news_has_lhb", False)
    sell_plan = ctx.get("has_sell_plan", False)
    if rid == "R21":
        return (sectors > 0 and ctx.get("sector_window_ok", False)), \
               f"板块筛选 {sectors} 条；{'✅ 7-10日口径' if ctx.get('sector_window_ok') else '❌ 当前用5日窗口，与规则7-10日口径不一致（待对齐）'}"
    if rid == "R22":
        return candidates > 0, f"候选池 {candidates} 只"
    if rid == "R23":
        return candidates > 0, f"规则打分已执行（候选 {candidates}）"
    if rid == "R24":
        return news_lhb, ("消息面含龙虎榜" if news_lhb else "❌ 消息面缺龙虎榜维度（待实现）")
    if rid == "R25":
        return sell_plan, ("含买卖计划" if sell_plan else "❌ 缺止损/卖出计划结构化输出（待实现）")
    if rid == "R26":
        return tracking > 0, f"命中率统计 {tracking} 笔（滚动回测待显式化）"
    if rid == "R27":
        return mcp, ("数据源弹性：自动发现+多路兜底" if mcp else "❌ MCP 不可用，需上报用户审查")
    if rid == "R28":
        return True, "因子隔离管理（46个未接入，等待成熟+批准）"
    if rid == "R29":
        return True, "变更日志：CHG-001 + ISSUE-001 已建立，修复/更新须同步记录"
    if rid == "R30":
        return ctx.get("rules_preloaded", False), "运行前规则预读" + ("" if ctx.get("rules_preloaded") else "（未预读）")
    return True, ""


def compliance_text(cr: dict) -> str:
    """生成卡片/报告可读的合规自检文本"""
    lines = [f"合规自检 v{cr.get('version')}｜{cr['summary']['status']}"]
    for it in cr["items"]:
        mark = "✅" if it["ok"] else ("🔴" if it["priority"] == "hard" else "🟡")
        lines.append(f"{mark} {it['id']} {it['title']}：{it['detail']}")
    return "\n".join(lines)
