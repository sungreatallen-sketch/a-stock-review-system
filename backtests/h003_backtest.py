"""H003 独立回测：资金布局（放量）→ 洗盘（下杀）→ 拉升 两阶段节奏
不修改现有策略代码；全部信号只用当时已发生的数据（无前视）。
"""
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import paths
from app.predict.alt_data import EgoDailyData, _secid, NODE, BASE

# ---------------- 数据加载 ----------------
def load_data():
    p = paths()
    fp = p["data"] / "h003_data.json"
    d = json.loads(fp.read_text(encoding="utf-8"))
    return d["stocks"], d["klines"]


def fetch_index():
    """上证指数日K（secid=1.000001）"""
    p = paths()
    fp = p["data"] / "h003_index.json"
    if fp.exists():
        return json.loads(fp.read_text(encoding="utf-8"))
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001"
           "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=0&end=20500101&lmt=320")
    script = (NODE.replace("BASE_PLACEHOLDER", json.dumps(BASE)).replace("__JOBS__", json.dumps(
        [{"label": "idx", "url": url, "referer": BASE}], ensure_ascii=False)))
    ego = EgoDailyData()
    r = ego._run(script)
    j = json.loads(r.get("idx") or "{}")
    klines = ((j or {}).get("data") or {}).get("klines") or []
    rows = []
    for k in klines:
        p_ = k.split(",")
        if len(p_) >= 6:
            rows.append({"date": p_[0], "open": float(p_[1]), "close": float(p_[2]),
                         "high": float(p_[3]), "low": float(p_[4]), "volume": float(p_[5])})
    rows.sort(key=lambda x: x["date"])
    fp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


# ---------------- 信号检测 ----------------
def detect(rows, vol_th=2.0, dd_min=0.05, dd_max=0.20, window=15, spacing=20):
    """rows: [[date,open,close,high,low,volume,amount], ...] 升序
    返回信号列表 [{spike_idx, spike_date, signal_idx, signal_date, variant}]
    """
    n = len(rows)
    signals = []
    last_spike = -999
    for i in range(6, n - 1):
        # 阶段1：放量异动（相对前5日均量），且前期平静
        v = rows[i][5]
        prev5 = [rows[j][5] for j in range(i - 5, i) if rows[j][5] > 0]
        if not prev5:
            continue
        vol_ratio = v / (sum(prev5) / len(prev5))
        prev_ratios = []
        for j in range(i - 5, i):
            p5 = [rows[k][5] for k in range(j - 5, j) if rows[k][5] > 0]
            prev_ratios.append(rows[j][5] / (sum(p5) / len(p5)) if p5 else 99)
        if vol_ratio < vol_th or max(prev_ratios) >= 1.5:
            continue
        if i - last_spike < spacing:
            continue
        last_spike = i
        spike_close = rows[i][2]

        # 阶段2+3：在 (i, i+window] 内找触发日
        for s in range(i + 1, min(i + window + 1, n - 1)):
            # 洗盘：期间曾跌破 spike_close*(1-dd_min)
            pullback = any(rows[m][4] <= spike_close * (1 - dd_min) for m in range(i + 1, s))
            if not pullback:
                continue
            # 未破位：期间最低 >= spike_close*(1-dd_max)
            min_low = min(rows[m][4] for m in range(i, s))
            if min_low < spike_close * (1 - dd_max):
                break  # 破位，此轮放弃
            # A：放量上涨确认（close>spike_close 且 放量）
            v5 = [rows[j][5] for j in range(s - 5, s) if rows[j][5] > 0]
            vol_ok = (rows[s][5] >= 1.2 * (sum(v5) / len(v5))) if v5 else False
            if rows[s][2] > spike_close and vol_ok:
                signals.append({"spike_idx": i, "spike_date": rows[i][0],
                                "signal_idx": s, "signal_date": rows[s][0], "variant": "A"})
                break
            # B：止跌确认（close 回升到洗盘低点上方且收涨）
            if rows[s][2] > rows[s - 1][2] and rows[s][2] > min(rows[m][4] for m in range(i, s)):
                signals.append({"spike_idx": i, "spike_date": rows[i][0],
                                "signal_idx": s, "signal_date": rows[s][0], "variant": "B"})
                break
    return signals


# ---------------- 前向收益 ----------------
def forward_returns(rows, sig_idx, horizons=(1, 3, 5, 10, 20)):
    base = rows[sig_idx][2]
    out = {}
    for h in horizons:
        idx = sig_idx + h
        if idx < len(rows):
            out[h] = (rows[idx][2] / base - 1) * 100
    return out


def index_map(index_rows):
    return {r["date"]: r for r in index_rows}


def market_state(sig_date, imap):
    """指数收盘 vs MA20 → 'up'/'down'"""
    dates = sorted(imap.keys())
    i = None
    for k, dt in enumerate(dates):
        if dt >= sig_date:
            i = k
            break
    if i is None or i < 20:
        return None
    closes = [imap[dates[j]]["close"] for j in range(i - 20, i)]
    ma20 = sum(closes) / 20
    cur = imap[dates[i]]["close"]
    return "up" if cur >= ma20 else "down"


# ---------------- 汇总 ----------------
def build_universe_bench(klines):
    """每只股票 date->idx 映射；用于计算信号日股票池等权前向收益"""
    maps = {}
    for code, rows in klines.items():
        maps[code] = {r[0]: i for i, r in enumerate(rows)}
    return maps


def universe_forward(umaps, klines, sig_date, h):
    """信号日 sig_date 起，股票池等权持有 h 日的平均收益%"""
    rets = []
    for code, dm in umaps.items():
        i = dm.get(sig_date)
        if i is None:
            continue
        rows = klines[code]
        j = i + h
        if j < len(rows):
            rets.append((rows[j][2] / rows[i][2] - 1) * 100)
    return statistics.mean(rets) if rets else None


def index_forward(imap, sig_date, h, dates=None):
    """上证指数从 sig_date（对齐）起持有 h 个交易日的收益%"""
    dates = dates or sorted(imap.keys())
    try:
        i = dates.index(sig_date)
    except ValueError:
        # 找最近不早于 sig_date 的交易日
        i = None
        for k, dt in enumerate(dates):
            if dt >= sig_date:
                i = k
                break
        if i is None:
            return None
    if i + h >= len(dates):
        return None
    c0 = imap[dates[i]]["close"]
    c1 = imap[dates[i + h]]["close"]
    if not c0:
        return None
    return (c1 / c0 - 1) * 100


def summarize(signals, klines, imap, index_rows, umaps):
    dates = sorted(imap.keys())
    out = {"variants": {}}
    for var in ("A", "B"):
        sigs = [s for s in signals if s["variant"] == var]
        per_h = {}
        for h in (1, 3, 5, 10, 20):
            rets = []
            bench_idx = []
            bench_uni = []
            mkt = {"up": [], "down": []}
            for s in sigs:
                fr = forward_returns(klines[s["code"]], s["signal_idx"], (h,))
                bi = index_forward(imap, s["signal_date"], h, dates)
                bu = universe_forward(umaps, klines, s["signal_date"], h)
                if h in fr and bi is not None and bu is not None:
                    rets.append(fr[h])
                    bench_idx.append(bi)
                    bench_uni.append(bu)
                    st = market_state(s["signal_date"], imap)
                    if st:
                        mkt[st].append(fr[h] - bu)
            if rets:
                per_h[h] = {
                    "count": len(rets),
                    "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                    "avg": round(statistics.mean(rets), 2),
                    "median": round(statistics.median(rets), 2),
                    "best": round(max(rets), 2),
                    "worst": round(min(rets), 2),
                    "index_avg": round(statistics.mean(bench_idx), 2),
                    "excess_vs_index": round(statistics.mean([a - b for a, b in zip(rets, bench_idx)]), 2),
                    "universe_avg": round(statistics.mean(bench_uni), 2),
                    "excess_vs_universe": round(statistics.mean([a - b for a, b in zip(rets, bench_uni)]), 2),
                    "universe_beat_rate": round(sum(1 for a, b in zip(rets, bench_uni) if a > b) / len(rets) * 100, 1),
                    "up_mkt_excess": (round(statistics.mean(mkt["up"]), 2), len(mkt["up"])) if mkt["up"] else None,
                    "down_mkt_excess": (round(statistics.mean(mkt["down"]), 2), len(mkt["down"])) if mkt["down"] else None,
                }
        out["variants"][var] = {"count": len(sigs), "by_horizon": per_h}
    return out


def main():
    stocks, klines = load_data()
    index_rows = fetch_index()
    imap = index_map(index_rows)
    print(f"股票数: {len(klines)} | 指数数据: {len(index_rows)} 天")

    signals = []
    for code, rows in klines.items():
        for s in detect(rows):
            s["code"] = code
            signals.append(s)
    print(f"总信号: A={sum(1 for s in signals if s['variant']=='A')}  B={sum(1 for s in signals if s['variant']=='B')}")

    umaps = build_universe_bench(klines)
    result = summarize(signals, klines, imap, index_rows, umaps)
    # 打印
    for var, v in result["variants"].items():
        print(f"\n===== 变体 {var}（{v['count']} 个信号）=====")
        for h, st in v["by_horizon"].items():
            print(f"  T+{h:>2}: n={st['count']:4d} 胜率={st['win_rate']}% 平均={st['avg']}% "
                  f"超额vs指数={st['excess_vs_index']}% | 超额vs股票池={st['excess_vs_universe']}% "
                  f"(池胜率{st['universe_beat_rate']}%)")
            if st.get("up_mkt_excess"):
                print(f"         多头池超额={st['up_mkt_excess']} | 空头池超额={st['down_mkt_excess']}")

    p = paths()
    out = {
        "hypothesis": "H003",
        "params": {"vol_th": 2.0, "dd_min": 0.05, "dd_max": 0.20, "window": 15, "spacing": 20,
                   "universe": "东财成交额Top100", "period": f"{min(r[0] for r in klines.values() if r)} ~ "
                                                             f"{max(r[-1][0] for r in klines.values() if r)}"},
        "result": result,
    }
    fp = p["reports"] / "h003_backtest.json"
    fp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已保存: {fp}")


if __name__ == "__main__":
    main()
