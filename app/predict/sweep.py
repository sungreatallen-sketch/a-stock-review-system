"""策略扫描：多组打分 × 过滤组合回测对比，选出最优"""
import json
import logging
import time

from .backtest import Backtest
from .scoring import score_pool


def make_v1_no_hot(max_amount=8e8, max_change=9.5):
    """基础打分 + 候选硬过滤（剔除大额拥挤与追高）"""
    def score_fn(pool, top_n):
        pool = dict(pool)
        cands = []
        for c in pool.get("candidates") or []:
            if c.get("amount") and c["amount"] > max_amount:
                continue
            if c.get("change_ratio") is not None and c["change_ratio"] > max_change:
                continue
            cands.append(c)
        pool["candidates"] = cands
        return score_pool(pool, top_n)
    return score_fn


def _setup():
    from .cache import MCPCache, CachedMcp
    from ..mcp_client import McpClient
    from ..config import load_config, paths
    logging.basicConfig(level=logging.WARNING)
    cfg = load_config()
    m = cfg["mcp"]
    mcp = McpClient(m["proxy_url"], m.get("token", ""), m["workbuddy_log_dir"])
    p = paths()
    cached = CachedMcp(mcp, MCPCache(p["data"] / "mcp_cache.db"))
    return Backtest(cached), cached, p


def run_volume_sweep(end_date: str, days: int = 40, top_n: int = 3, vol_max=2.5,
                     index_filter=None) -> dict:
    """基础打分 + K线量比过滤"""
    from .strategy import Strategy, MAX_VOL_RATIO
    bt, cached, p = _setup()

    def kline_lookup(ticker, end):
        return cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                           {"ticker": ticker, "market": "a_stock", "end_date": end, "limit": 10})

    import app.predict.strategy as sm
    old_th = sm.MAX_VOL_RATIO
    sm.MAX_VOL_RATIO = vol_max
    try:
        strat = Strategy(cached, score_pool, kline_lookup)
        t0 = time.time()
        r = bt.run(end_date, days=days, top_n=top_n, strategy=strat, index_filter=index_filter)
    finally:
        sm.MAX_VOL_RATIO = old_th
    s = r["stats"]
    out = {
        "variant": f"量比<{vol_max}" + (f"+指数过滤{index_filter}" if index_filter is not None else ""),
        "count": s.get("count"), "win_rate": s.get("win_rate"),
        "avg_ret": s.get("avg_ret"), "median_ret": s.get("median_ret"),
        "total_return": s.get("total_return"), "max_drawdown": s.get("max_drawdown"),
        "index_avg": (s.get("benchmark") or {}).get("avg_ret"),
        "excess": None if s.get("avg_ret") is None or not s.get("benchmark") else
                  round(s["avg_ret"] - s["benchmark"]["avg_ret"], 2),
        "skipped": s.get("meta", {}).get("skipped"),
        "seconds": round(time.time() - t0, 0),
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    (p["reports"] / f"backtest_vol_{end_date}.json").write_text(
        json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def run_sweep(end_date: str, days: int = 40, top_n: int = 3) -> list:
    bt, cached, p = _setup()
    variants = [
        ("base", None, None),
        ("base_filter0", None, 0.0),
        ("base_no_hot", make_v1_no_hot(), None),
        ("base_no_hot_filter0", make_v1_no_hot(), 0.0),
    ]
    results = []
    for name, vfn, filt in variants:
        t0 = time.time()
        try:
            r = bt.run(end_date, days=days, top_n=top_n, index_filter=filt, score_fn=vfn)
            s = r["stats"]
            results.append({
                "variant": name, "count": s.get("count"),
                "win_rate": s.get("win_rate"), "avg_ret": s.get("avg_ret"),
                "median_ret": s.get("median_ret"), "total_return": s.get("total_return"),
                "max_drawdown": s.get("max_drawdown"),
                "index_avg": (s.get("benchmark") or {}).get("avg_ret"),
                "excess": None if s.get("avg_ret") is None or not s.get("benchmark") else
                          round(s["avg_ret"] - s["benchmark"]["avg_ret"], 2),
                "skipped": s.get("meta", {}).get("skipped"),
                "seconds": round(time.time() - t0, 0),
            })
            print(json.dumps(results[-1], ensure_ascii=False))
        except Exception as e:
            print(f"{name} 失败: {e}")
    results.sort(key=lambda x: (x.get("avg_ret") is not None, x.get("avg_ret") or -999), reverse=True)
    print("\n===== 排序（按平均收益） =====")
    for r in results:
        print(f"{r['variant']:24s} n={r['count']:4d} 胜率={r['win_rate']}% 平均={r['avg_ret']}% "
              f"总收益={r['total_return']}% 超额={r['excess']}% 跳过={r['skipped']}")
    (p["reports"] / f"sweep_{end_date}.json").write_text(
        json.dumps({"end_date": end_date, "days": days, "results": results},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return results


if __name__ == "__main__":
    import sys
    if "--vol" in sys.argv:
        end = sys.argv[2] if len(sys.argv) > 2 else "2026-08-11"
        days = int(sys.argv[3]) if len(sys.argv) > 3 else 40
        vol_max = float(sys.argv[4]) if len(sys.argv) > 4 else 2.5
        filt = float(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] != "None" else None
        run_volume_sweep(end, days, vol_max=vol_max, index_filter=filt)
    else:
        run_sweep(sys.argv[1] if len(sys.argv) > 1 else "2026-08-11",
                  int(sys.argv[2]) if len(sys.argv) > 2 else 40)
