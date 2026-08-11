"""把采集结果组装成用户约定的 JSON 结构，数值格式化（元->亿）"""
import json


def _yi(v, digits=2):
    """元 -> 亿元"""
    if v is None:
        return None
    try:
        return round(float(v) / 1e8, digits)
    except Exception:
        return None


def _w(v, digits=0):
    """元 -> 万元"""
    if v is None:
        return None
    try:
        return round(float(v) / 1e4, digits)
    except Exception:
        return None


def build_report(collect_result: dict) -> dict:
    """collect_result: Collector.collect() 的输出 -> 用户 JSON 结构"""
    idx = collect_result["market_index"]
    emo = collect_result["emotion"]
    sec = collect_result["sector_rank"]
    cap = collect_result["capital_flow"]

    market_index = {}
    for name, v in idx.items():
        market_index[name] = {
            "收盘价": v.get("close"),
            "涨跌幅%": v.get("pct"),
            "涨跌额": v.get("change"),
            "成交额(亿元)": _yi(v.get("amount")),
            "数据验证": "已双源验证" if v.get("validated") else "未交叉验证",
            "来源": v.get("sources"),
            "备注": v.get("note"),
        }

    emotion = {
        "date": emo.get("date"),
        "涨停数量": emo.get("limit_up"),
        "跌停数量": emo.get("limit_down"),
        "最高连板": emo.get("max_boards"),
        "炸板数量": emo.get("break_count"),
        "连板分布": emo.get("board_dist"),
        "数据验证": "已交叉验证" if emo.get("validated") else "部分单源",
        "来源": emo.get("sources"),
        "备注": emo.get("notes"),
    }

    sector_rank = []
    for typ, rows in (("industry", sec.get("industry") or []), ("concept", sec.get("concept") or [])):
        for r in rows:
            sector_rank.append({
                "类型": "行业" if typ == "industry" else "概念",
                "排名": r.get("rank"),
                "板块": r.get("name"),
                "涨跌幅%": r.get("pct"),
                "主力净流入(亿元)": _yi(r.get("main_inflow")),
                "上涨/下跌家数": f"{r.get('up')}/{r.get('down')}",
                "龙头股": r.get("leader"),
                "龙头涨跌幅%": r.get("leader_pct"),
                "来源": "东财板块行情API",
            })

    mf = cap.get("main_flow") or {}
    hsgt = cap.get("hsgt") or {}
    lhb = cap.get("lhb") or []
    capital_flow = {
        "主力资金": {
            "date": mf.get("date"),
            "上证主力净流入(亿元)": _yi(mf.get("main_net")),
            "上证超大单净流入(亿元)": _yi(mf.get("xlarge_net")),
            "上证大单净流入(亿元)": _yi(mf.get("large_net")),
            "来源": "东财资金流向API（上证指数）",
        },
        "北向资金": {
            "港>沪成交额(亿元)": (hsgt.get("north_turnover") or {}).get("港>沪"),
            "港>深成交额(亿元)": (hsgt.get("north_turnover") or {}).get("港>深"),
            "净买入额": hsgt.get("net_buy", "数据不可获取"),
            "备注": hsgt.get("note"),
            "来源": "东财沪深港通API",
        },
        "龙虎榜": {
            "数据日期": lhb[0]["trade_date"] if lhb else "数据不可获取",
            "明细": [
                {
                    "代码": r.get("code"),
                    "名称": r.get("name"),
                    "收盘价": r.get("close"),
                    "涨跌幅%": r.get("change_pct"),
                    "龙虎榜净买入(万元)": _w(r.get("net_amt")),
                    "上榜原因": r.get("reason"),
                }
                for r in lhb[:10]
            ],
            "来源": "东财龙虎榜数据中心API",
        },
    }

    report = {
        "date": collect_result["date"],
        "market_index": market_index,
        "emotion": emotion,
        "sector_rank": sector_rank,
        "capital_flow": capital_flow,
        "prediction": {
            "status": "M1阶段暂不输出标的预测（M2/M3 实现）",
            "targets": [],
        },
        "source": collect_result["source"],
        "meta": {
            "generated_at": (collect_result.get("_meta") or {}).get("generated_at"),
            "说明": "所有数据均来自真实可追溯来源；标注'数据不可获取'表示该数据源确实无法取得，未做任何编造。",
            "来源网页": {
                "指数/涨停/跌停/炸板": "https://quote.eastmoney.com/ztb/",
                "板块行情": "https://quote.eastmoney.com/center/boardlist.html",
                "沪深港通": "https://data.eastmoney.com/hsgt/index.html",
                "龙虎榜": "https://data.eastmoney.com/longhuzong/",
                "MCP数据源": "WorkBuddy聚合代理（通达信/同舟/Wind）",
            },
        },
    }
    return report
