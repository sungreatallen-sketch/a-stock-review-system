"""模拟盘跟踪：记录每日预测 → 次日回填实际开盘价 → 命中率统计"""
import json
import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger("track")


class Tracker:
    def __init__(self, data_dir: Path):
        self.db_path = data_dir / "a_share.db"
        self._init()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init(self):
        conn = self._conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                targets TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prediction_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                target_code TEXT NOT NULL,
                target_name TEXT NOT NULL,
                buy_price REAL,
                sell_price REAL,
                ret REAL,
                status TEXT,
                created_at TEXT NOT NULL
            )""")
        conn.commit()
        conn.close()

    def record_prediction(self, predict_result: dict):
        conn = self._conn()
        conn.execute("INSERT INTO predictions(date, targets, created_at) VALUES(?,?,?)",
                     (predict_result["date"], json.dumps(predict_result, ensure_ascii=False),
                      __import__("datetime").datetime.now().isoformat()))
        conn.commit()
        conn.close()
        log.info("已记录 %s 预测", predict_result["date"])

    def settle(self, trade_date: str, open_prices: dict):
        """trade_date: 卖出日（预测日+1）。open_prices: {code: 开盘价}"""
        conn = self._conn()
        conn.execute("DELETE FROM prediction_results WHERE date=?", (trade_date,))
        row = conn.execute("SELECT targets FROM predictions WHERE date=? ORDER BY id DESC LIMIT 1",
                           (trade_date,)).fetchone()
        if not row:
            conn.close()
            return {"note": f"无 {trade_date} 的预测记录"}
        targets = json.loads(row[0]).get("targets") or []
        saved = 0
        for t in targets:
            code = (t.get("code") or "").split(".")[0]
            buy = t.get("参考买入价(收盘)")
            sell = open_prices.get(code)
            ret = round((sell / buy - 1) * 100, 2) if (buy and sell) else None
            conn.execute(
                "INSERT INTO prediction_results(date, target_code, target_name, buy_price, sell_price, ret, status, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (trade_date, code, t.get("name"), buy, sell, ret,
                 "settled" if ret is not None else "missing_open",
                 __import__("datetime").datetime.now().isoformat()))
            saved += 1
        conn.commit()
        conn.close()
        return {"date": trade_date, "settled": saved}

    def stats(self, days: int = 30) -> dict:
        conn = self._conn()
        rows = conn.execute(
            "SELECT date, target_code, target_name, buy_price, sell_price, ret, status "
            "FROM prediction_results WHERE status='settled' "
            "ORDER BY date DESC LIMIT ?", (days * 3,)).fetchall()
        conn.close()
        if not rows:
            return {"count": 0, "note": "暂无已结算预测"}
        rets = [r[5] for r in rows if r[5] is not None]
        wins = [r for r in rets if r > 0]
        import statistics
        return {
            "count": len(rets),
            "win_rate": round(len(wins) / len(rets) * 100, 1),
            "avg_ret": round(statistics.mean(rets), 2),
            "median_ret": round(statistics.median(rets), 2),
            "best": round(max(rets), 2),
            "worst": round(min(rets), 2),
            "recent": [
                {"date": r[0], "name": r[2], "buy": r[3], "sell": r[4], "ret": r[5], "status": r[6]}
                for r in rows[:15]
            ],
        }
