"""模拟盘跟踪：记录每日预测 → 自动/手动结算次日开盘 → 命中率统计"""
import json
import logging
import sqlite3
from datetime import date
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
                date TEXT UNIQUE NOT NULL,
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

    # ---------- 记录 ----------
    def record_prediction(self, predict_result: dict) -> bool:
        """按日期 upsert 预测（同日多次预测以后一次为准）"""
        conn = self._conn()
        d = predict_result["date"]
        payload = json.dumps(predict_result, ensure_ascii=False)
        ts = __import__("datetime").datetime.now().isoformat()
        exists = conn.execute("SELECT 1 FROM predictions WHERE date=?", (d,)).fetchone()
        if exists:
            conn.execute("UPDATE predictions SET targets=?, created_at=? WHERE date=?", (payload, ts, d))
        else:
            conn.execute("INSERT INTO predictions(date, targets, created_at) VALUES(?,?,?)", (d, payload, ts))
        conn.commit()
        conn.close()
        log.info("已记录 %s 预测", predict_result["date"])
        return True

    def latest_prediction(self):
        conn = self._conn()
        row = conn.execute("SELECT date, targets FROM predictions ORDER BY date DESC LIMIT 1").fetchone()
        conn.close()
        return row

    # ---------- 结算 ----------
    def settle(self, pred_date: str, open_prices: dict) -> dict:
        """pred_date: 预测日 T；open_prices: {code: T+1 开盘价}"""
        conn = self._conn()
        conn.execute("DELETE FROM prediction_results WHERE date=?", (pred_date,))
        row = conn.execute("SELECT targets FROM predictions WHERE date=?", (pred_date,)).fetchone()
        if not row:
            conn.close()
            return {"date": pred_date, "note": "无该日预测记录"}
        targets = json.loads(row[0]).get("targets") or []
        saved = 0
        missing = []
        for t in targets:
            code = (t.get("code") or "").split(".")[0]
            buy = t.get("参考买入价(收盘)")
            sell = open_prices.get(code)
            if not buy or not sell:
                missing.append(code)
                continue
            ret = round((sell / buy - 1) * 100, 2)
            conn.execute(
                "INSERT INTO prediction_results(date, target_code, target_name, buy_price, sell_price, ret, status, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (pred_date, code, t.get("name"), buy, sell, ret, "settled",
                 __import__("datetime").datetime.now().isoformat()))
            saved += 1
        conn.commit()
        conn.close()
        return {"date": pred_date, "settled": saved, "missing_open": missing}

    def settle_pending(self, cached, today: str = None) -> dict:
        """自动结算：找最近一条未结算预测，若其次日为过去交易日则回填开盘价"""
        row = self.latest_prediction()
        if not row:
            return {"note": "无预测记录"}
        pred_date = row[0]
        # 若该日已结算且无新预测，跳过
        conn = self._conn()
        settled = conn.execute("SELECT COUNT(*) FROM prediction_results WHERE date=? AND status='settled'",
                               (pred_date,)).fetchone()[0]
        conn.close()
        if settled:
            return {"date": pred_date, "note": "该日预测已结算"}

        # 找次日交易日
        from .backtest import Backtest, INDEX_TICKER
        today = today or str(date.today())
        resp = cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                           {"ticker": INDEX_TICKER, "market": "index",
                            "start_date": pred_date, "end_date": today, "limit": 12})
        pts = sorted({p["time"] for p in ((resp or {}).get("data") or {}).get("points") or []})
        if pred_date not in pts:
            return {"date": pred_date, "note": "预测日非交易日"}
        idx = pts.index(pred_date)
        if idx + 1 >= len(pts):
            return {"date": pred_date, "note": f"次日({today}前)无交易日数据，尚未到结算时间"}
        sell_date = pts[idx + 1]
        # 若 sell_date 是未来日期，说明今天还没到
        if sell_date > today:
            return {"date": pred_date, "sell_date": sell_date, "note": "尚未到次日，暂不结算"}

        # 拉取各标的次日开盘价
        targets = json.loads(row[1]).get("targets") or []
        opens = {}
        for t in targets:
            code = (t.get("code") or "").split(".")[0]
            resp = cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                               {"ticker": code, "market": "a_stock", "end_date": sell_date, "limit": 6})
            pt = next((x for x in ((resp or {}).get("data") or {}).get("points") or []
                       if x.get("time") == sell_date), None)
            if pt and pt.get("open"):
                opens[code] = pt["open"]
        if not opens:
            return {"date": pred_date, "sell_date": sell_date, "note": "次日开盘价未获取到"}
        res = self.settle(pred_date, opens)
        res["sell_date"] = sell_date
        return res

    # ---------- 统计 ----------
    def stats(self, days: int = 30) -> dict:
        conn = self._conn()
        rows = conn.execute(
            "SELECT date, target_code, target_name, buy_price, sell_price, ret, status "
            "FROM prediction_results WHERE status='settled' ORDER BY date DESC LIMIT ?",
            (days * 3,)).fetchall()
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
