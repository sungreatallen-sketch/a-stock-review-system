"""H003 独立回测 v2：Top300×400天，参数敏感性网格 + 去重叠 + 显著性检验
原则：所有网格单元如实呈现，不挑最优；不改写假设。
"""
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import paths
from app.predict.alt_data import EgoDailyData, NODE, BASE
from backtests.h003_backtest import (detect, forward_returns, build_universe_bench,
                                     universe_forward, index_forward, market_state)

DATA = paths()["data"] / "h003_data_v3.json"
INDEX = paths()["data"] / "h003_index_v2.json"


def load():
    d = json.loads(DATA.read_text(encoding="utf-8"))
    return d["stocks"], d["klines"]


def fetch_index(force=False):
    for cand in (INDEX, paths()["data"] / "h003_index.json"):
        if cand.exists() and not force:
            try:
                rows = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(rows, list) and rows:
                    return rows
            except Exception:
                pass
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001"
           "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=0&end=20500101&lmt=500")
    ego = EgoDailyData()
    r = {}
    for _ in range(3):
        script = (NODE.replace("BASE_PLACEHOLDER", json.dumps(BASE)).replace("__JOBS__", json.dumps(
            [{"label": "idx", "url": url, "referer": BASE}], ensure_ascii=False)))
        r = ego._run(script)
        if r.get("idx", "").startswith("{"):
            break
        import time as _t
        _t.sleep(5)
    j = json.loads(r.get("idx") or "{}")
    kl = ((j or {}).get("data") or {}).get("klines") or []
    rows = []
    for k in kl:
        p = k.split(",")
        if len(p) >= 6:
            rows.append({"date": p[0], "close": float(p[2]), "high": float(p[3]),
                         "low": float(p[4])})
    rows.sort(key=lambda x: x["date"])
    INDEX.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def t_stat(rets):
    """单样本 t 统计量（excess 均值是否显著非零）"""
    if len(rets) < 2:
        return None, None
    m = statistics.mean(rets)
    sd = statistics.stdev(rets)
    se = sd / math.sqrt(len(rets))
    return (m / se) if se else None, len(rets)


def analyze(klines, imap, umaps, params, variants=("A", "B")):
    """对一组参数跑检测+统计"""
    signals = []
    for code, rows in klines.items():
        for s in detect(rows, vol_th=params["vol_th"], dd_min=params["dd_min"],
                        dd_max=params["dd_max"], window=params["window"]):
            s["code"] = code
            signals.append(s)
    out = {}
    dates_idx = sorted(imap.keys())
    for var in variants:
        sigs = [s for s in signals if s["variant"] == var]
        per_h = {}
        for h in (5, 10, 20):
            rets, ex_uni, ex_idx = [], [], []
            for s in sigs:
                fr = forward_returns(klines[s["code"]], s["signal_idx"], (h,))
                bu = universe_forward(umaps, klines, s["signal_date"], h)
                bi = index_forward(imap, s["signal_date"], h, dates_idx)
                if h in fr and bu is not None and bi is not None:
                    rets.append(fr[h])
                    ex_uni.append(fr[h] - bu)
                    ex_idx.append(fr[h] - bi)
            if rets:
                t, n = t_stat(ex_uni)
                per_h[h] = {
                    "n": len(rets), "avg": round(statistics.mean(rets), 2),
                    "ex_uni": round(statistics.mean(ex_uni), 2),
                    "beat_uni": round(sum(1 for x in ex_uni if x > 0) / len(ex_uni) * 100, 1),
                    "ex_idx": round(statistics.mean(ex_idx), 2),
                    "t_ex_uni": round(t, 2) if t else None,
                }
        out[var] = {"count": len(sigs), "by_horizon": per_h}
    return out


def main():
    stocks, klines = load()
    imap = {r["date"]: r for r in fetch_index()}
    umaps = build_universe_bench(klines)
    print(f"股票数: {len(klines)} | 指数: {len(imap)} 天")
    d0 = min(r[0] for r in klines.values() if r); d1 = max(r[-1][0] for r in klines.values() if r)
    print(f"数据跨度: {d0} ~ {d1}")

    grid = []
    for vol_th in (1.5, 2.0):
        for dd_min in (0.03, 0.05, 0.08):
            for window in (10, 15):
                params = {"vol_th": vol_th, "dd_min": dd_min, "dd_max": 0.20, "window": window}
                r = analyze(klines, imap, umaps, params)
                grid.append({"params": params, "result": r})

    # 打印网格（变体B）
    print("\n========== 参数敏感性网格（变体 B · 超额vs股票池%） ==========")
    print(f"{'量比':>4} {'洗盘%':>5} {'窗口':>4} | {'T5':>7} {'T10':>7} {'T20':>7} | {'n':>4}")
    for g in grid:
        p = g["params"]
        b = g["result"].get("B", {}).get("by_horizon", {})
        row = [b.get(h, {}).get("ex_uni", "-") if b.get(h) else "-" for h in (5, 10, 20)]
        n = g["result"].get("B", {}).get("count", 0)
        print(f"{p['vol_th']:>4} {p['dd_min']*100:>4.0f}% {p['window']:>4} | "
              f"{str(row[0]):>7} {str(row[1]):>7} {str(row[2]):>7} | {n:>4}")

    # 默认参数详细（含去重叠+显著性）
    print("\n========== 默认参数（量比2.0 / 洗盘5% / 窗口15）详细 ==========")
    default = grid[[g["params"]["vol_th"] == 2.0 and g["params"]["dd_min"] == 0.05
                    and g["params"]["window"] == 15 for g in grid].index(True)]
    dres = default["result"]
    for var in ("A", "B"):
        print(f"\n变体 {var}（{dres[var]['count']} 信号）")
        for h, st in dres[var]["by_horizon"].items():
            print(f"  T+{h:>2}: n={st['n']} 平均={st['avg']}% 超额vs池={st['ex_uni']}% "
                  f"池胜率={st['beat_uni']}% 超额vs指数={st['ex_idx']}% t={st['t_ex_uni']}")

    # 保存
    out = {
        "hypothesis": "H003", "version": "v2",
        "universe": f"Top{len(klines)}成交额", "period": f"{d0}~{d1}",
        "grid": grid, "default": default,
    }
    fp = paths()["reports"] / "h003_backtest_v2.json"
    fp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告: {fp}")


if __name__ == "__main__":
    main()
