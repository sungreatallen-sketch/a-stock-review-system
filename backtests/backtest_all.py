"""H001-H016 依次独立回测（跳过H003已完成）
复用 H3 已抓取数据（h003_data.json + h003_data_v2.json 合并，每只股票最长400天）
统一基准：股票池等权前向收益 + 上证指数；含 t 检验；不挑参数、不改写假设。
"""
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import paths
from backtests.h003_backtest import (forward_returns, build_universe_bench,
                                     universe_forward)

def tstat(rets):
    if len(rets) < 2:
        return None
    m = statistics.mean(rets)
    sd = statistics.stdev(rets)
    return m / (sd / math.sqrt(len(rets))) if sd else None

# ---------- 数据 ----------
def load_merged():
    p = paths()["data"]
    d1 = json.load(open(p / "h003_data.json"))
    d2 = json.load(open(p / "h003_data_v2.json"))
    klines = {}
    for c, rows in d1["klines"].items():
        klines.setdefault(c, []).extend(rows)
    for c, rows in d2["klines"].items():
        klines.setdefault(c, []).extend(rows)
    for c in klines:
        klines[c].sort(key=lambda x: x[0])
    return klines

def load_index():
    p = paths()["data"]
    for name in ("h003_index_v2.json", "h003_index.json"):
        f = p / name
        if f.exists():
            try:
                rows = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(rows, list) and rows:
                    return {r["date"]: r for r in rows}
            except Exception:
                pass
    return {}

def fmt(x, nd=2):
    return "-" if x is None else f"{x:.{nd}f}"

def summarize_event(rets, bench, label):
    """rets: 信号收益%列表, bench: 对应股票池基准%列表"""
    n = len(rets)
    if n < 5:
        return {"n": n, "note": "样本过少"}
    ex = [a - b for a, b in zip(rets, bench)]
    return {
        "n": n,
        "avg": round(statistics.mean(rets), 2),
        "ex_pool": round(statistics.mean(ex), 2),
        "beat_pool": round(sum(1 for x in ex if x > 0) / n * 100, 1),
        "t_ex_pool": round(tstat(ex), 2) if tstat(ex) else None,
        "win": round(sum(1 for r in rets if r > 0) / n * 100, 1),
    }

# ---------- 事件型信号 ----------
def sig_H002(rows, i, vol_th=2.0):
    """放量异动：当日量/前5日均量>=2 且前5日平静"""
    if i < 6:
        return False
    v = rows[i][5]
    prev5 = [rows[j][5] for j in range(i - 5, i) if rows[j][5] > 0]
    if not prev5:
        return False
    vr = v / (sum(prev5) / len(prev5))
    if vr < vol_th:
        return False
    for j in range(i - 5, i):
        p5 = [rows[k][5] for k in range(j - 5, j) if rows[k][5] > 0]
        if p5 and rows[j][5] / (sum(p5) / len(p5)) >= 1.5:
            return False
    return True

def sig_H006(rows, i):
    """支撑位止跌：触及前120日低点附近+长下影+未破位"""
    if i < 125:
        return False
    lo = min(rows[j][4] for j in range(i - 120, i - 5))
    if rows[i][4] > lo * 1.02:      # 当日低点须触及支撑±2%
        return False
    hl = rows[i][3] - rows[i][4]
    if hl <= 0:
        return False
    if (rows[i][2] - rows[i][4]) / hl < 0.5:   # 下影>=0.5
        return False
    return rows[i][2] > lo * 0.995             # 未破位

def sig_H007(rows, i):
    """压力位滞涨：触及前120日高点附近+长上影+未突破"""
    if i < 125:
        return False
    hi = max(rows[j][3] for j in range(i - 120, i - 5))
    if rows[i][3] < hi * 0.98:
        return False
    hl = rows[i][3] - rows[i][4]
    if hl <= 0:
        return False
    if (rows[i][3] - rows[i][2]) / hl < 0.5:   # 上影>=0.5
        return False
    return rows[i][2] < hi * 1.005             # 未突破

def sig_H009(rows, i):
    """回光返照：10日涨幅>=20% 且 长上影 或 放量滞涨"""
    if i < 11:
        return False
    r10 = rows[i][2] / rows[i - 10][2] - 1
    if r10 < 0.20:
        return False
    hl = rows[i][3] - rows[i][4]
    body = abs(rows[i][2] - rows[i][1])
    if hl > 0 and (rows[i][3] - rows[i][2]) / hl >= 1.0:
        return True
    v5 = [rows[j][5] for j in range(i - 5, i) if rows[j][5] > 0]
    if v5 and rows[i][5] >= 2 * (sum(v5) / len(v5)) and abs(rows[i][2] / rows[i - 1][2] - 1) < 0.01:
        return True
    return False

def sig_H013(rows, i):
    """死水股（量价近似主力离场）：20日均量<前60日均量50% 且 20日无大波动"""
    if i < 80:
        return False
    v_recent = statistics.mean([rows[j][5] for j in range(i - 20, i) if rows[j][5] > 0])
    v_old = statistics.mean([rows[j][5] for j in range(i - 80, i - 20) if rows[j][5] > 0])
    if not v_old or v_recent >= 0.5 * v_old:
        return False
    maxc = max(abs(rows[j][2] / rows[j - 1][2] - 1) for j in range(i - 20, i))
    return maxc < 0.03

# ---------- 截面型（滚动再平衡） ----------
def panel_test(klines, score_fn, lookback, rebal, horizon, label):
    """score_fn(stock_rows, idx) -> 数值评分（用 idx 之前数据）；按日分组三分位"""
    # 收集所有再平衡日
    all_dates = sorted({r[0] for rows in klines.values() for r in rows})
    rb_dates = [d for k, d in enumerate(all_dates) if k % rebal == 0 and k >= lookback]
    groups = {g: [] for g in ("低", "中", "高")}
    for d in rb_dates:
        obs = []  # (score, ret, pool_ret)
        for code, rows in klines.items():
            dates = [r[0] for r in rows]
            if d not in dates:
                continue
            i = dates.index(d)
            if i < lookback or i + horizon >= len(rows):
                continue
            sc = score_fn(rows, i)
            if sc is None:
                continue
            fr = forward_returns(rows, i, (horizon,))
            if horizon not in fr:
                continue
            obs.append((sc, fr[horizon]))
        if len(obs) < 15:
            continue
        pool = statistics.mean([o[1] for o in obs])
        obs.sort(key=lambda x: x[0])
        n3 = len(obs) // 3
        for g, sl in zip(("低", "中", "高"), (obs[:n3], obs[n3:2 * n3], obs[2 * n3:])):
            if sl:
                groups[g].append(statistics.mean([o[1] - pool for o in sl]))
    out = {}
    for g, ex in groups.items():
        out[g] = {"days": len(ex),
                  "ex_pool": round(statistics.mean(ex), 2) if ex else None,
                  "t": round(tstat(ex), 2) if len(ex) >= 2 and tstat(ex) else None}
    return out

def score_H001(rows, i):
    return statistics.mean([rows[j][6] for j in range(i - 30, i)])  # 30日均成交额

def score_H004(rows, i):
    """反复炒作：过去90日 '5日内涨>=15%后10日内回撤>=10%' 次数"""
    cnt = 0
    for a in range(i - 90, i - 10):
        for b in range(a + 1, min(a + 6, i)):
            if rows[b][2] / rows[a][2] - 1 >= 0.15:
                peak = max(rows[k][2] for k in range(a, b + 1))
                for c in range(b + 1, min(b + 11, i)):
                    if rows[c][2] / peak - 1 <= -0.10:
                        cnt += 1
                        break
                break
    return cnt

def score_H011(rows, i):
    """趋势强度：过去60日收盘对时间的线性回归 R²"""
    xs = list(range(60))
    ys = [rows[i - 60 + k][2] for k in range(60)]
    mx = statistics.mean(xs); my = statistics.mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return (sxy ** 2) / (sxx * syy)

def main():
    klines = load_merged()
    imap = load_index()
    print(f"股票数: {len(klines)} | 指数: {len(imap)} 天")
    d0 = min(r[0] for r in klines.values() if r); d1 = max(r[-1][0] for r in klines.values() if r)
    print(f"数据跨度: {d0} ~ {d1}\n")

    report = {"universe": len(klines), "period": f"{d0}~{d1}"}
    umaps = build_universe_bench(klines)

    # 事件型
    def run_event(sig_fn, horizons, label, spacing=20):
        res = {}
        for code, rows in klines.items():
            last = -999
            for i in range(6, len(rows) - 1):
                if sig_fn(rows, i) and i - last >= spacing:
                    last = i
                    for h in horizons:
                        fr = forward_returns(rows, i, (h,))
                        bu = universe_forward(umaps, klines, rows[i][0], h)
                        if h in fr and bu is not None:
                            res.setdefault(h, []).append((fr[h], bu))
        out = {}
        for h, pairs in res.items():
            rets = [x[0] for x in pairs]; bench = [x[1] for x in pairs]
            out[f"T+{h}"] = summarize_event(rets, bench, label)
        return out

    # H002 放量
    report["H002"] = run_event(sig_H002, (3, 5, 10), "H002")
    # H006 支撑位
    report["H006"] = run_event(sig_H006, (1, 3, 5), "H006", spacing=10)
    # H007 压力位
    report["H007"] = run_event(sig_H007, (1, 3, 5), "H007", spacing=10)
    # H009 回光返照
    report["H009"] = run_event(sig_H009, (1, 3, 5), "H009", spacing=10)
    # H013 死水股
    report["H013"] = run_event(sig_H013, (5, 10, 20), "H013", spacing=10)

    # 截面型
    report["H001"] = panel_test(klines, score_H001, 30, 10, 10, "H001")
    report["H004"] = panel_test(klines, score_H004, 90, 20, 20, "H004")
    report["H011"] = panel_test(klines, score_H011, 60, 20, 20, "H011")

    # 打印
    for hid in ("H001", "H002", "H004", "H006", "H007", "H009", "H011", "H013"):
        print(f"===== {hid} =====")
        print(json.dumps(report[hid], ensure_ascii=False))

    p = paths()
    fp = p["reports"] / "hypotheses_backtest.json"
    fp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告: {fp}")

if __name__ == "__main__":
    main()
