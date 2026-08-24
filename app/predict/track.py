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
        # 迁移：补充收盘价字段（次日收盘）
        cols = {r[1] for r in conn.execute("PRAGMA table_info(prediction_results)")}
        for c, sql in (("sell_close", "ALTER TABLE prediction_results ADD COLUMN sell_close REAL"),
                       ("ret_close", "ALTER TABLE prediction_results ADD COLUMN ret_close REAL")):
            if c not in cols:
                conn.execute(sql)
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
    def settle(self, pred_date: str, open_prices: dict = None, close_prices: dict = None) -> dict:
        """pred_date: 预测日 T；按【昨收买→今收卖】收盘-收盘口径评估（用户确认 v1.4）
        close_prices: {code: T+1 收盘价} 主口径；open_prices 仅作参考不再用于命中率"""
        close_prices = close_prices or {}
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
            buy = t.get("参考买入价(收盘)")   # 昨收
            sell_close = close_prices.get(code)  # 今收
            if not buy or not sell_close:
                missing.append(code)
                continue
            ret = round((sell_close / buy - 1) * 100, 2)   # 命中率按 今收/昨收
            conn.execute(
                "INSERT INTO prediction_results(date, target_code, target_name, buy_price, sell_price, ret, sell_close, ret_close, status, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (pred_date, code, t.get("name"), buy, sell_close, ret, sell_close, ret, "settled",
                 __import__("datetime").datetime.now().isoformat()))
            saved += 1
        conn.commit()
        conn.close()
        if saved:
            try:
                self.export_history()
            except Exception as e:
                log.warning("数据积累文件更新失败: %s", str(e)[:120])
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

        # 找次日交易日（MCP 失败用 ego 兜底）
        from .backtest import INDEX_TICKER
        today = today or str(date.today())
        pts = []
        try:
            resp = cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                               {"ticker": INDEX_TICKER, "market": "index",
                                "start_date": pred_date, "end_date": today, "limit": 12})
            pts = sorted({p["time"] for p in ((resp or {}).get("data") or {}).get("points") or []})
        except Exception as e:
            log.warning("MCP 交易日历失败，切 ego 兜底: %s", str(e)[:100])
        if not pts:
            from .alt_data import EgoOpenPrices
            from ..config import paths as get_paths
            ego = EgoOpenPrices(get_paths()["data"] / "ego_kline.db")
            pts = ego.fetch_index_dates(today, limit=12)
        if pred_date not in pts:
            return {"date": pred_date, "note": "预测日非交易日"}
        idx = pts.index(pred_date)
        if idx + 1 >= len(pts):
            return {"date": pred_date, "note": f"次日({today}前)无交易日数据，尚未到结算时间"}
        sell_date = pts[idx + 1]
        # 若 sell_date 是未来日期，说明今天还没到
        # 如果 sell_date 是未来日期，说明今天还没到
        if sell_date > today:
            return {"date": pred_date, "sell_date": sell_date, "note": "尚未到次日，暂不结算"}
        # 如果 sell_date 等于 pred_date，说明数据异常
        if sell_date == pred_date:
            return {"date": pred_date, "note": "预测日与结算日相同，数据异常"}

        # 拉取各标的次日开盘价（MCP 优先，失败自动切 ego 浏览器兜底）
        targets = json.loads(row[1]).get("targets") or []
        codes = [(t.get("code") or "").split(".")[0] for t in targets]
        opens, closes = {}, {}
        mcp_failed = False
        for code in codes:
            try:
                resp = cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                                   {"ticker": code, "market": "a_stock", "end_date": sell_date, "limit": 6})
                # 类型检查：resp 可能是字符串（MCP 返回异常）
                if not isinstance(resp, dict):
                    log.warning("MCP 返回非 dict 类型: %s", type(resp).__name__)
                    mcp_failed = True
                    continue
                pt = next((x for x in ((resp or {}).get("data") or {}).get("points") or []
                           if x.get("time") == sell_date), None)
                if pt and pt.get("open"):
                    opens[code] = pt["open"]
                if pt and pt.get("close"):
                    closes[code] = pt["close"]
            except Exception as e:
                mcp_failed = True
                log.warning("MCP 取 %s 行情失败: %s", code, str(e)[:120])
        if not opens and mcp_failed:
            # 兜底：ego browser 东财K线（仅开盘价，收盘价尽力而为）
            log.info("MCP 不可用，切换到 ego browser 获取行情")
            from .alt_data import EgoOpenPrices
            from ..config import paths as get_paths
            ego = EgoOpenPrices(get_paths()["data"] / "ego_kline.db")
            ego_opens = ego.fetch(sell_date, codes)
            opens.update(ego_opens)
        if not opens:
            return {"date": pred_date, "sell_date": sell_date, "note": "次日开盘价未获取到"}
        res = self.settle(pred_date, opens, closes)
        res["sell_date"] = sell_date
        if mcp_failed:
            res["data_source"] = "ego浏览器兜底"
        return res

    # ---------- 数据积累文件（后台分析用，前端不展示累计） ----------
    def export_history(self) -> dict:
        """把全部已结算推荐导出到 data/recommendation_history.json（积累+分析）
        含：全部记录、按日汇总、累计统计、分股统计、按月统计"""
        import statistics as _st
        conn = self._conn()
        rows = conn.execute(
            "SELECT date, target_code, target_name, buy_price, sell_price, ret, sell_close, ret_close, status "
            "FROM prediction_results WHERE status='settled' ORDER BY date, id").fetchall()
        conn.close()
        records = [{"date": r[0], "code": r[1], "name": r[2], "buy": r[3], "sell": r[4],
                    "ret": r[5], "sell_close": r[6], "ret_close": r[7]} for r in rows]
        # 按日汇总
        daily = {}
        for r in records:
            daily.setdefault(r["date"], []).append(r)
        daily_summary = []
        for d in sorted(daily):
            rs = daily[d]
            rets = [x["ret"] for x in rs]
            daily_summary.append({
                "date": d, "count": len(rs), "hit": sum(1 for x in rets if x > 0),
                "win_rate": round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1),
                "avg_ret": round(_st.mean(rets), 2),
                "targets": [{"name": x["name"], "code": x["code"], "buy": x["buy"],
                             "sell_close": x["sell_close"], "ret": x["ret"]} for x in rs],
            })
        # 累计统计
        rets_all = [r["ret"] for r in records]
        cumulative = {"count": len(rets_all),
                      "win_rate": round(sum(1 for x in rets_all if x > 0) / len(rets_all) * 100, 1) if rets_all else None,
                      "avg_ret": round(_st.mean(rets_all), 2) if rets_all else None,
                      "median_ret": round(_st.median(rets_all), 2) if rets_all else None,
                      "best": round(max(rets_all), 2) if rets_all else None,
                      "worst": round(min(rets_all), 2) if rets_all else None}
        # 分股统计
        by_stock = {}
        for r in records:
            b = by_stock.setdefault(r["code"], {"name": r["name"], "count": 0, "rets": []})
            b["count"] += 1
            b["rets"].append(r["ret"])
        for code, b in by_stock.items():
            rs = b.pop("rets")
            b["win_rate"] = round(sum(1 for x in rs if x > 0) / len(rs) * 100, 1)
            b["avg_ret"] = round(_st.mean(rs), 2)
        # 按月统计
        by_month = {}
        for r in records:
            m = r["date"][:7]
            by_month.setdefault(m, []).append(r["ret"])
        month_summary = {m: {"count": len(v), "win_rate": round(sum(1 for x in v if x > 0) / len(v) * 100, 1),
                             "avg_ret": round(_st.mean(v), 2)} for m, v in sorted(by_month.items())}
        out = {
            "meta": {"version": "1.0", "updated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                     "note": "后台数据积累文件：全部已结算推荐记录与分析；前端报告只展示最近一天，不展示本文件累计"},
            "cumulative": cumulative,
            "by_month": month_summary,
            "by_stock": by_stock,
            "daily": daily_summary,
            "records": records,
        }
        fp = self.db_path.parent / "recommendation_history.json"
        fp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("推荐历史数据积累已更新: %d 条 -> %s", len(records), fp)
        return out

    # ---------- 统计 ----------
    def stats(self, days: int = 30) -> dict:
        conn = self._conn()
        rows = conn.execute(
            "SELECT date, target_code, target_name, buy_price, sell_price, ret, "
            "sell_close, ret_close, status "
            "FROM prediction_results WHERE status='settled' ORDER BY date DESC LIMIT ?",
            (days * 3,)).fetchall()
        conn.close()
        if not rows:
            return {"count": 0, "note": "暂无已结算预测"}
        rets = [r[5] for r in rows if r[5] is not None]
        wins = [r for r in rets if r > 0]
        import statistics
        closes = [r[7] for r in rows if r[7] is not None]
        return {
            "count": len(rets),
            "win_rate": round(len(wins) / len(rets) * 100, 1),
            "avg_ret": round(statistics.mean(rets), 2),
            "median_ret": round(statistics.median(rets), 2),
            "best": round(max(rets), 2),
            "worst": round(min(rets), 2),
            "avg_ret_close": round(statistics.mean(closes), 2) if closes else None,
            "recent": [
                {"date": r[0], "name": r[2], "buy": r[3], "sell": r[4], "ret": r[5],
                 "sell_close": r[6], "ret_close": r[7], "status": r[8]}
                for r in rows[:15]
            ],
        }
