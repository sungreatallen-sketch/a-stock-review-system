"""DeepSeek 综合研判：基于候选池(评分/逻辑) + 消息面，输出 Top3 与风险
硬约束：模型只能基于喂入的真实数据推理，禁止编造任何数据
"""
import json
import logging

from .llm import deepseek_chat, safe_json
from ..config import paths

log = logging.getLogger("judge")

SYSTEM = (
    "你是一名严谨的A股短线交易研究员，负责在每日收盘后从候选标的中选出次日最值得关注的3只标的。\n"
    "【铁律】\n"
    "1. 只能使用用户提供的数据和消息面信息，禁止编造、猜测或引用未提供的数据。\n"
    "2. 对无法确认的事项明确写\"不确定\"或\"无数据\"，绝不编造。\n"
    "3. 策略为超短T+1：今日收盘价附近买入，次日开盘后卖出。\n"
    "4. 每个标的必须给出买卖计划：参考买点、止损位、卖出区间、建议持仓时长。\n"
    "5. 买卖计划只能基于提供的真实数据推理，止损/目标价位要有依据（如跌破量比确认位、前低等），禁止凭空编造。\n"
    "6. 输出必须是合法 JSON。\n"
)


def build_user(candidates: list, news: dict, market: dict = None) -> str:
    lines = []
    if market:
        lines.append(f"【市场环境】{json.dumps(market, ensure_ascii=False)}")
    lines.append("【候选标的（已含规则评分与逻辑）】")
    for i, c in enumerate(candidates, 1):
        lines.append(f"{i}. {json.dumps(c, ensure_ascii=False)}")
    lines.append("【消息面（近5日新闻/公告/事件，情绪为关键词初筛，仅作参考）】")
    for tk, info in news.items():
        items = info.get("items") or []
        if not items:
            lines.append(f"- {tk}（{info.get('name')}）：无检索到消息")
            continue
        lines.append(f"- {tk}（{info.get('name')}）{info.get('summary')}")
        for it in items[:4]:
            lines.append(f"    [{it['sentiment']}][{it['type']}][{it.get('date')}]({it.get('source')}) {it['title']}")
    lines.append(
        "\n请基于以上真实数据，选出3只次日最值得关注的标的，输出JSON格式：\n"
        '{"market_view":"一句话市场判断",'
        '"targets":[{"code":"代码","name":"名称","reason":"买入逻辑(只引用提供的真实数据)",'
        '"risk":"主要风险点(没有就说无明确风险)",'
        '"buy_point":"参考买点(基于提供的收盘价等数据)",'
        '"stop_loss":"止损位(明确价位,有依据)",'
        '"sell_target":"卖出区间/目标(明确价位)",'
        '"hold":"建议持仓时长",'
        '"sentiment_score":-5到5的整数(消息面评分,0为中性,无数据记0),"confidence":"高/中/低"}]}'
    )
    return "\n".join(lines)


def judge(candidates: list, news: dict, market: dict = None,
          effort: str = "high", max_tokens: int = 5000) -> dict:
    """调用 DeepSeek 研判，返回 {market_view, targets:[...], raw}"""
    user = build_user(candidates, news, market)
    text = deepseek_chat(SYSTEM, user, json_mode=True, max_tokens=max_tokens, effort=effort)
    parsed = safe_json(text)
    if not parsed:
        log.error("研判解析失败")
        return {"market_view": "模型输出解析失败", "targets": [], "raw": text}
    parsed["raw"] = text
    # 只保留候选池内存在的标的（防模型幻觉）
    valid_codes = {c["ticker"] for c in candidates}
    targets = []
    for t in parsed.get("targets") or []:
        code = (t.get("code") or "").split(".")[0]
        if code and any(c["ticker"].split(".")[0] == code for c in candidates):
            targets.append(t)
    parsed["targets"] = targets[:3]
    return parsed
