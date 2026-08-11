"""命令行入口
用法:
  python run_cli.py review          # 执行完整收盘复盘（采集→验证→JSON→HTML→存库）
  python run_cli.py serve           # 启动报告 Web 服务（手机同 Wi-Fi 访问）
  python run_cli.py report --date 2026-08-11   # 基于已存数据重新生成 HTML
"""
import argparse
import json
import logging
import sys
from datetime import date

sys.path.insert(0, ".")

from app.config import load_config, paths
from app.collector import Collector
from app.report_builder import build_report
from app.storage import Storage
from app.html_report import render_html


def do_review(target: date = None, verbose=False):
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from app.workflow import run_review
    p = paths()
    print(">>> 开始采集数据 + 生成标的预测...")
    report = run_review(include_prediction=True)
    print(f">>> 完成！数据日期: {report['date']}")
    print(f"    JSON: {p['reports'] / (report['date'] + '.json')}")
    print(f"    HTML: {p['reports'] / (report['date'] + '.html')}")
    return report


def do_serve():
    from app.server import start_server
    start_server()


def main():
    ap = argparse.ArgumentParser(description="A股收盘复盘系统")
    sub = ap.add_subparsers(dest="cmd")
    rv = sub.add_parser("review", help="执行复盘")
    rv.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("serve", help="启动 Web 服务")
    bt = sub.add_parser("backtest", help="历史回测（收盘买/次日开盘卖）")
    bt.add_argument("--end", default=str(date.today()), help="回测截止日期 YYYY-MM-DD")
    bt.add_argument("--days", type=int, default=30, help="回测交易日数")
    bt.add_argument("--top", type=int, default=3, help="每日选股数")
    bt.add_argument("--filter", type=float, default=None,
                    help="指数5日涨跌幅过滤阈值（如 0 表示仅指数5日为正才交易）")
    bt.add_argument("--no-vol", action="store_true", help="关闭量比过滤（默认开启量比<2.0）")
    bt.add_argument("-v", "--verbose", action="store_true")
    tk = sub.add_parser("track", help="模拟盘跟踪: record/settle/stats")
    tk.add_argument("action", choices=["record", "settle", "stats"])
    tk.add_argument("--date", default=None)
    tk.add_argument("--days", type=int, default=30)
    pd = sub.add_parser("predict", help="生成今日 Top3 标的预测")
    pd.add_argument("--date", default=None, help="标的日期 YYYY-MM-DD（默认最新交易日）")
    rp = sub.add_parser("report", help="重新生成 HTML")
    rp.add_argument("--date", default=None)
    rp.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.cmd == "backtest":
        from app.predict.backtest import Backtest
        from app.predict.cache import MCPCache, CachedMcp
        from app.mcp_client import McpClient
        from app.config import load_config, paths
        logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        cfg = load_config()
        m = cfg["mcp"]
        mcp = McpClient(m["proxy_url"], m.get("token", ""), m["workbuddy_log_dir"])
        p = paths()
        cache = MCPCache(p["data"] / "mcp_cache.db")
        cached = CachedMcp(mcp, cache)
        bt = Backtest(cached)
        import time
        t0 = time.time()
        print(f">>> 开始回测 {args.days} 个交易日（截止 {args.end}）...")
        strategy = None
        if not args.no_vol:
            from app.predict.strategy import Strategy
            from app.predict.scoring import score_pool
            def kline_lookup(tk, end):
                return cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                                   {"ticker": tk, "market": "a_stock", "end_date": end, "limit": 10})
            strategy = Strategy(cached, score_pool, kline_lookup)
        result = bt.run(args.end, days=args.days, top_n=args.top, index_filter=args.filter,
                        strategy=strategy)
        print(f">>> 完成，耗时 {time.time()-t0:.0f}s")
        import json as _json
        out = p["reports"] / f"backtest_{args.end}.json"
        out.write_text(_json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(_json.dumps(result["stats"], ensure_ascii=False, indent=2))
        print(f"报告: {out}")
    elif args.cmd == "track":
        from app.predict.track import Tracker
        from app.predict.daily import predict as predict_today
        from app.predict.cache import MCPCache, CachedMcp
        from app.mcp_client import McpClient
        from app.config import load_config, paths
        cfg = load_config()
        m = cfg["mcp"]
        mcp = McpClient(m["proxy_url"], m.get("token", ""), m["workbuddy_log_dir"])
        p = paths()
        cached = CachedMcp(mcp, MCPCache(p["data"] / "mcp_cache.db"))
        tr = Tracker(p["data"])
        if args.action == "record":
            result = predict_today(cached, args.date)
            tr.record_prediction(result)
            print(f"已记录 {result['date']} 预测: {[t.get('name') for t in result['targets']]}")
        elif args.action == "settle":
            # 结算最近一次预测：用预测日+1的开盘价
            import sqlite3
            conn = sqlite3.connect(p["data"] / "a_share.db")
            row = conn.execute("SELECT date, targets FROM predictions ORDER BY id DESC LIMIT 1").fetchone()
            conn.close()
            if not row:
                print("无预测记录可结算")
            else:
                pred_date, targets_json = row
                sell_date = args.date or pred_date  # 实盘时传次日日期
                open_prices = {}
                tgt = json.loads(targets_json).get("targets") or []
                for t in tgt:
                    code = (t.get("code") or "").split(".")[0]
                    resp = cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                                       {"ticker": code, "market": "a_stock", "end_date": sell_date, "limit": 5})
                    pts = ((resp or {}).get("data") or {}).get("points") or []
                    pt = next((x for x in pts if x["time"] == sell_date), None)
                    if pt and pt.get("open"):
                        open_prices[code] = pt["open"]
                res = tr.settle(sell_date, open_prices)
                print(f"结算完成: {res}")
        elif args.action == "stats":
            print(json.dumps(tr.stats(args.days), ensure_ascii=False, indent=2))
    elif args.cmd == "predict":
        from app.predict.daily import predict as predict_today
        from app.predict.cache import MCPCache, CachedMcp
        from app.mcp_client import McpClient
        from app.config import load_config, paths
        cfg = load_config()
        m = cfg["mcp"]
        mcp = McpClient(m["proxy_url"], m.get("token", ""), m["workbuddy_log_dir"])
        p = paths()
        cached = CachedMcp(mcp, MCPCache(p["data"] / "mcp_cache.db"))
        print(">>> 生成标的预测（板块+个股+资金+量比过滤）...")
        result = predict_today(cached, args.date)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        out = p["reports"] / f"predict_{result['date']}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已保存: {out}")
    elif args.cmd == "review":
        do_review(verbose=args.verbose if hasattr(args, "verbose") else False)
    elif args.cmd == "serve":
        do_serve()
    elif args.cmd == "report":
        p = paths()
        st = Storage(p["data"], p["reports"])
        d = args.date or str(date.today())
        data = st.load_report(d)
        if not data:
            print(f"未找到 {d} 的报告")
            sys.exit(1)
        html = render_html(data)
        (p["reports"] / f"{d}.html").write_text(html, encoding="utf-8")
        print(f"HTML 已生成: {p['reports'] / (d + '.html')}")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
