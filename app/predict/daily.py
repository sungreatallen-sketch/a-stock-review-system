"""每日预测（M3 完整版）：
规则候选池(板块+个股+资金+量比过滤) → 消息面扫描 → DeepSeek 综合研判 → Top3
"""
import json
import logging

from .candidate_pool import CandidatePool
from .scoring import score_pool
from .strategy import Strategy, compute_vol_ratio, STRATEGY_VERSION
from ..report_text import EXECUTION_PLAN
from .backtest import Backtest
from .news import NewsScanner
from .judge import judge
from ..config import paths

log = logging.getLogger("daily")


def _kline_lookup_factory(cached):
    """K线查询：THS 优先 → MCP → ego browser 兜底"""
    def lookup(ticker, end_date):
        from ..ths_client import get_ths_client
        ths = get_ths_client()
        # THS 优先（REST API 直连，不经过 WorkBuddy）
        try:
            thscode = f"{ticker}.SH" if ticker.startswith("6") else f"{ticker}.SZ"
            from datetime import date as _d, timedelta as _td
            end = _d.fromisoformat(str(end_date))
            start = end - _td(days=14)
            raw = ths.kline(thscode, start, end)
            if raw:
                points = []
                for it in raw:
                    dt = __import__("datetime").datetime.fromtimestamp(it["date_ms"] / 1000).strftime("%Y-%m-%d")
                    points.append({
                        "time": dt, "open": it.get("open_price"),
                        "high": it.get("high_price"), "low": it.get("low_price"),
                        "close": it.get("close_price"), "volume": it.get("volume"),
                        "amount": it.get("turnover"),
                    })
                if points:
                    log.info("THS K线 %s: %d 条", ticker, len(points))
                    return {"data": {"points": points}}
        except Exception as e:
            log.warning("THS K线 %s 失败: %s", ticker, str(e)[:100])
        # MCP 兜底
        try:
            resp = cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                               {"ticker": ticker, "market": "a_stock", "end_date": end_date, "limit": 12})
            points = ((resp or {}).get("data") or {}).get("points") or []
            if points:
                return resp
        except Exception as e:
            log.warning("MCP K线 %s 失败: %s", ticker, str(e)[:100])
        # MCP 失败：ego browser 兜底
        code = ticker.split(".")[0]
        try:
            from .alt_data import _kline_url, NODE, BASE
            import subprocess, json as _json
            url = _kline_url(code, limit=12)
            script = NODE.replace("BASE_PLACEHOLDER", _json.dumps(BASE)).replace(
                "__JOBS__", _json.dumps([{"label": code, "url": url, "referer": BASE}], ensure_ascii=False))
            proc = subprocess.run(["ego-browser", "nodejs"], input=script,
                                  capture_output=True, text=True, timeout=30,
                                  env={"PATH": "/Users/yage/.local/bin:/usr/bin:/bin:/usr/local/bin"})
            for line in ((proc.stdout or "") + (proc.stderr or "")).splitlines():
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        results = _json.loads(line)
                        raw = results.get(code, "")
                        if raw:
                            j = _json.loads(raw)
                            # ego K线格式: "date,open,close,high,low,volume,amount,amplitude"
                            # 转为 MCP 格式: {"time":..., "open":..., "close":..., ...}
                            klines = (j.get("data") or {}).get("klines") or []
                            points = []
                            for k in klines:
                                parts = k.split(",")
                                if len(parts) >= 7:
                                    points.append({
                                        "time": parts[0],
                                        "open": float(parts[1]),
                                        "close": float(parts[2]),
                                        "high": float(parts[3]),
                                        "low": float(parts[4]),
                                        "volume": float(parts[5]),
                                        "amount": float(parts[6]),
                                    })
                            if points:
                                return {"data": {"points": points}}
                    except Exception:
                        continue
        except Exception as e:
            log.warning("ego K线兜底失败(%s): %s", ticker, str(e)[:100])
        return resp

    return lookup


def _market_context(target_date: str) -> dict:
    """从当日复盘报告取市场环境（存在则用，不存在返回空）"""
    p = paths()
    fp = p["reports"] / f"{target_date}.json"
    if not fp.exists():
        return {}
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
        mi = d.get("market_index") or {}
        emo = d.get("emotion") or {}
        return {
            "上证指数": mi.get("上证指数", {}).get("收盘价"),
            "上证涨跌幅%": mi.get("上证指数", {}).get("涨跌幅%"),
            "创业板指": mi.get("创业板指", {}).get("收盘价"),
            "创业板涨跌幅%": mi.get("创业板指", {}).get("涨跌幅%"),
            "涨停": emo.get("涨停数量"), "跌停": emo.get("跌停数量"),
            "最高连板": emo.get("最高连板"),
        }
    except Exception:
        return {}


def predict(cached, target_date: str = None, use_llm: bool = True) -> dict:
    """生成最新交易日的 Top3 预测"""
    from datetime import date
    bt = Backtest(cached)
    end = target_date or str(date.today())
    trading = bt.trading_days(end, 2)
    t = trading[-1]
    # R11：实际交易日 t 已有预测则直接复用（防止数据源滞后导致覆盖用户已看到的预测）
    # 注意：不能用传入的 target_date 检查，必须用 trading_days 实际算出的 t
    from .track import Tracker
    from ..config import paths as _paths
    _existing = Tracker(_paths()["data"]).get_prediction(t)
    if _existing:
        # R11 锁定标的，但允许只补齐缺失的 T 日参考价；绝不替换标的或重新研判。
        existing_kline_lookup = _kline_lookup_factory(cached)
        price_changed = False
        for pick in _existing.get("targets") or []:
            if pick.get("参考买入价(收盘)"):
                continue
            code = (pick.get("code") or "").split(".")[0]
            resp = existing_kline_lookup(code, t)
            pt = next((p for p in ((resp or {}).get("data") or {}).get("points") or []
                       if p.get("time") == t), None)
            if pt and pt.get("close"):
                pick["参考买入价(收盘)"] = float(pt["close"])
                price_changed = True
        if price_changed:
            log.info("R11: %s 已锁定标的，仅补齐缺失参考价", t)
            try:
                Tracker(_paths()["data"]).record_prediction(_existing)
            except Exception:
                log.exception("R11补价后写回失败")
        for tgt in _existing.get("targets") or []:
            tgt["hold"] = EXECUTION_PLAN
        try:
            Tracker(_paths()["data"]).record_prediction(_existing)
        except Exception:
            log.exception("R11执行窗口写回失败")
        log.info("R11: %s 已有预测，直接复用（不覆盖）", t)
        return _existing
    pool = CandidatePool(cached).build(t)
    kline_lookup = _kline_lookup_factory(cached)
    strat = Strategy(cached, score_pool, kline_lookup)
    top5 = strat.select(pool, t, None, top_n=5)

    # 补齐 K 线细节（量比/买入价）
    for pick in top5:
        resp = kline_lookup(pick["ticker"], t)
        pts = {p["time"]: p for p in ((resp or {}).get("data") or {}).get("points") or []}
        pt = pts.get(t)
        vr = compute_vol_ratio(resp, t) if pt else None
        pick["参考买入价(收盘)"] = pt.get("close") if pt else None
        pick["量比"] = round(vr, 2) if vr else None
        pick["逻辑"] = _build_logic(pick, vr)

    # 消息面（含龙虎榜 R24）
    from .alt_data import recent_lhb
    lhb_map = {}
    try:
        lhb_map = recent_lhb()
    except Exception as e:
        log.warning("龙虎榜获取失败: %s", str(e)[:120])
    scanner = NewsScanner(cached)
    news = {}
    for pick in top5:
        code = pick["ticker"].split(".")[0]
        news[code] = scanner.scan(code, pick["name"], lhb_map=lhb_map)

    # 市场环境（来自当日复盘报告）
    market = _market_context(t)

    # LLM 研判
    llm_result = None
    targets = []
    if use_llm and top5:
        try:
            cands = [{
                "ticker": p["ticker"], "name": p["name"], "industry": p.get("industry"),
                "sector": p.get("sector_name"), "change_ratio": p.get("change_ratio"),
                "amount": p.get("amount"), "turnover_rate": p.get("turnover_rate"),
                "score": p.get("score"), "factors": p.get("factors"), "逻辑": p.get("逻辑"),
            } for p in top5]
            llm_result = judge(cands, news, market)
            targets = llm_result.get("targets") or []
            # LLM 失败/空输出时回退：规则候选前3（保证 R10 3只不缺失）
            if not targets:
                # 降级 market_view：用实际市场环境生成中性判断，不暴露内部错误
                mv = llm_result.get("market_view") or ""
                if mv in ("模型输出解析失败", "Invalid JSON", "") or mv.startswith("{"):
                    mv = _fallback_market_view(market)
                llm_result = llm_result or {}
                llm_result["market_view"] = mv
                targets = [{
                    "code": p["ticker"], "name": p["name"], "reason": p.get("逻辑") or "规则打分排序",
                    "risk": "规则策略推荐（低吸强势板块龙头，控制仓位）", "confidence": "中",
                    "参考买入价(收盘)": p.get("参考买入价(收盘)"), "量比": p.get("量比"),
                    "板块": p.get("sector_name"), "评分明细": p.get("factors"),
                } for p in top5[:3]]
            # 回填买入价 + 买卖计划字段归一化/缺省兜底（R25）
            price_map = {p["ticker"].split(".")[0]: p for p in top5}
            for tgt in targets:
                code = tgt.get("code", "").split(".")[0]
                src = price_map.get(code) or {}
                tgt["参考买入价(收盘)"] = tgt.get("参考买入价(收盘)") or src.get("参考买入价(收盘)")
                tgt["量比"] = tgt.get("量比") or src.get("量比")
                tgt["评分明细"] = src.get("factors")
                tgt["板块"] = src.get("sector_name")
                # 归一化 LLM 可能的键名
                for k in ("stop_loss", "止损", "止损位", "止损价"):
                    if tgt.get(k) and not tgt.get("stop_loss"):
                        tgt["stop_loss"] = tgt[k]
                for k in ("sell_target", "卖出区间", "目标价", "卖出目标"):
                    if tgt.get(k) and not tgt.get("sell_target"):
                        tgt["sell_target"] = tgt[k]
                for k in ("hold", "持仓", "持仓时间"):
                    if tgt.get(k) and not tgt.get("hold"):
                        tgt["hold"] = tgt[k]
                # 参考价是T日收盘价；模拟实盘执行窗口是T+1开盘买入、T+2收盘卖出。
                buy = tgt.get("参考买入价(收盘)")
                if buy:
                    tgt.setdefault("stop_loss", round(buy * 0.97, 2))
                    tgt.setdefault("sell_target", round(buy * 1.03, 2))
                    tgt.setdefault("hold", EXECUTION_PLAN)
        except Exception as e:
            log.exception("LLM 研判失败，回退规则结果")
            llm_result = {"market_view": _fallback_market_view(market), "targets": []}
            targets = [{"code": p["ticker"], "name": p["name"], "reason": p.get("逻辑"),
                        "risk": "规则策略推荐（低吸强势板块龙头，控制仓位）", "confidence": "中",
                        "参考买入价(收盘)": p.get("参考买入价(收盘)"),
                        "量比": p.get("量比")} for p in top5[:3]]

    for tgt in targets:
        # 执行窗口是用户确认的硬约束；历史预测里的旧文案也要统一，避免语义漂移。
        tgt["hold"] = EXECUTION_PLAN

    return {
        "date": t,
        "settlement_rule": EXECUTION_PLAN,
        "strategy": "7-10日强势板块 + 个股强势 + 资金活跃 + 量比<2.0过滤 + 消息面 + LLM研判",
        "strategy_version": STRATEGY_VERSION,
        "filtered_out": strat.filtered,
        "sector_window": pool["meta"].get("sector_window"),
        "market_view": (llm_result or {}).get("market_view"),
        "targets": targets,
        "rule_candidates": [
            {"代码": p["ticker"], "名称": p["name"], "行业": p.get("industry"),
             "参考买入价(收盘)": p.get("参考买入价(收盘)"), "量比": p.get("量比"),
             "评分": p.get("score"), "逻辑": p.get("逻辑")}
            for p in top5
        ],
        "news": news,
        "news_has_lhb": bool(lhb_map),
        "top_sectors": pool["meta"]["top_sectors"],
        "candidate_count": len(pool["candidates"]),
        "raw_llm_output": (llm_result or {}).get("raw_llm_output", ""),
    }


def _fallback_market_view(market: dict) -> str:
    """LLM 失败时，用实际市场环境生成中性市场判断（不编造）"""
    try:
        sh = market.get("上证指数")
        cyb = market.get("创业板指")
        zt = market.get("涨停")
        dt = market.get("跌停")
        parts = []
        if sh is not None:
            parts.append(f"上证指数 {sh}")
        if cyb is not None:
            parts.append(f"创业板指 {cyb}")
        if zt is not None:
            parts.append(f"涨停 {zt} 家")
        if dt is not None:
            parts.append(f"跌停 {dt} 家")
        if parts:
            return "市场概况：" + "，".join(parts) + "（规则策略推荐，仅供参考）"
    except Exception:
        pass
    return "市场数据获取中，建议关注强势板块龙头（规则策略推荐，仅供参考）"


def _build_logic(pick: dict, vol_ratio) -> str:
    parts = []
    if pick.get("sector_name"):
        parts.append(f"所属强势板块「{pick['sector_name']}」")
    if pick.get("change_ratio") is not None:
        parts.append(f"当日涨幅 {pick['change_ratio']:.1f}%")
    parts.append(f"量比 {vol_ratio:.1f}（未放巨量）" if vol_ratio else "量比适中")
    return "；".join(parts)
