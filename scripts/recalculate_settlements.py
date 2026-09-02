"""按真实可执行口径重算全部历史预测。

规则：推荐日 T 只保留参考价；评估价必须为 T+1 收盘买入价和 T+2 收盘卖出价。
所有价格逐笔从同花顺历史 K 线重新拉取，禁止沿用旧口径计算出的收益。

默认 dry-run：只核对价格与执行日，不改数据库。
确认无误后执行：.venv/bin/python scripts/recalculate_settlements.py --apply
"""
import argparse
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _ths_code(code: str) -> str:
    return f"{code}.SH" if code.startswith("6") else f"{code}.SZ"


def _close_on(ths, code: str, day: str):
    if not day:
        return None
    wanted = datetime.fromisoformat(day)
    items = ths.kline(_ths_code(code), wanted.date(), wanted.date())
    for item in items:
        if not item.get("date_ms"):
            continue
        item_day = datetime.fromtimestamp(item["date_ms"] / 1000)
        if item_day.strftime("%Y-%m-%d") == day and item.get("close_price") is not None:
            return float(item["close_price"])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="确认后重写历史结算记录")
    args = ap.parse_args()

    from app.config import paths
    from app.predict.track import Tracker
    from app.ths_client import get_ths_client

    data_dir = paths()["data"]
    tr = Tracker(data_dir)
    ths = get_ths_client()
    conn = sqlite3.connect(tr.db_path)
    rows = conn.execute("SELECT date, targets FROM predictions ORDER BY date").fetchall()
    conn.close()
    if not rows:
        print("没有可重算的预测")
        return

    begin = date.fromisoformat(rows[0][0])
    end = date.today()
    trade_days = ths.trading_days(begin, end)
    print("交易日:", trade_days)

    plan = []
    for pred_date, targets_json in rows:
        targets = (json.loads(targets_json).get("targets") or [])
        codes = [(t.get("code") or "").split(".")[0] for t in targets]
        item = {"prediction_date": pred_date, "buy_date": None, "sell_date": None,
                "rows": [], "eligible": False, "reason": ""}
        if pred_date not in trade_days:
            item["reason"] = "预测日不在交易日"
            plan.append(item)
            continue
        idx = trade_days.index(pred_date)
        if idx + 1 >= len(trade_days):
            item["reason"] = "T+1买入日未出现"
            plan.append(item)
            continue
        buy_date = trade_days[idx + 1]
        if idx + 2 >= len(trade_days):
            item["reason"] = "T+2卖出日未出现"
            item["buy_date"] = buy_date
            plan.append(item)
            continue
        sell_date = trade_days[idx + 2]
        item.update({"buy_date": buy_date, "sell_date": sell_date})
        complete = True
        for code in codes:
            buy = _close_on(ths, code, buy_date)
            sell = _close_on(ths, code, sell_date)
            item["rows"].append({"code": code, "buy": buy, "sell": sell})
            if buy is None or sell is None:
                complete = False
        item["eligible"] = complete
        if not complete:
            item["reason"] = "历史K线缺失；不伪造"
        plan.append(item)

    for item in plan:
        rows_text = " | ".join(
            f"{r['code']} {r['buy']}→{r['sell']}" for r in item["rows"]
        ) or "-"
        print(f"{item['prediction_date']}: buy={item['buy_date']} sell={item['sell_date']} "
              f"eligible={item['eligible']} {rows_text} {item['reason']}")

    if not args.apply:
        print(f"\nDry-run 完成：{sum(x['eligible'] for x in plan)} 个交易日可重算")
        return

    backup = data_dir / f"a_share_backup_before_recalc_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    src = sqlite3.connect(tr.db_path)
    dst = sqlite3.connect(backup)
    src.backup(dst)
    dst.close()
    src.close()
    print(f"已备份数据库: {backup}")

    settled_dates = []
    for item in plan:
        if not item["eligible"]:
            continue
        buys = {r["code"]: r["buy"] for r in item["rows"]}
        sells = {r["code"]: r["sell"] for r in item["rows"]}
        result = tr.settle(
            item["prediction_date"], buys, sells,
            buy_date=item["buy_date"], sell_date=item["sell_date"],
        )
        if result.get("settled"):
            settled_dates.append(item["prediction_date"])
    tr.export_history()
    output = {
        "meta": {
            "version": "2.0",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "settlement_rule": "T+1收盘买入→T+2收盘卖出",
            "price_source": "同花顺历史K线",
            "backup": str(backup),
        },
        "settled_dates": settled_dates,
        "plan": plan,
    }
    fp = data_dir / "recalc_results.json"
    fp.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"重算完成：{len(settled_dates)} 个交易日，结果已写入 {fp}")


if __name__ == "__main__":
    main()
