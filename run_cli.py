"""命令行入口
用法:
  python run_cli.py review          # 执行完整收盘复盘（采集→验证→JSON→HTML→存库）
  python run_cli.py serve           # 启动报告 Web 服务（手机同 Wi-Fi 访问）
  python run_cli.py report --date 2026-08-11   # 基于已存数据重新生成 HTML
"""
import argparse
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
    cfg = load_config()
    p = paths()
    st = Storage(p["data"], p["reports"])
    print(">>> 开始采集数据（ego browser + MCP 交叉验证）...")
    c = Collector()
    raw = c.collect(target)
    report = build_report(raw)
    st.save_report(report)
    html = render_html(report)
    hp = p["reports"] / f"{report['date']}.html"
    hp.write_text(html, encoding="utf-8")
    print(f">>> 完成！数据日期: {report['date']}")
    print(f"    JSON: {p['reports'] / (report['date'] + '.json')}")
    print(f"    HTML: {hp}")
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
    rp = sub.add_parser("report", help="重新生成 HTML")
    rp.add_argument("--date", default=None)
    rp.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.cmd == "review":
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
