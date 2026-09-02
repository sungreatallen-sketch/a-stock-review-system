"""模拟盘跟踪：记录每日预测 → T+1收盘买入 → T+2收盘卖出 → 命中率统计"""
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
                       ("ret_close", "ALTER TABLE prediction_results ADD COLUMN ret_close REAL"),
                       ("buy_date", "ALTER TABLE prediction_results ADD COLUMN buy_date TEXT"),
                       ("sell_date", "ALTER TABLE prediction_results ADD COLUMN sell_date TEXT"),
                       ("reference_price", "ALTER TABLE prediction_results ADD COLUMN reference_price REAL")):
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

    def _find_settleable_prediction(self, today: str):
        """找昨天及之前的未结算预测（今天的预测今天不能结算）"""
        conn = self._conn()
        rows = conn.execute(
            "SELECT date, targets FROM predictions WHERE date < ? ORDER BY date DESC",
            (today,)
        ).fetchall()
        conn.close()
        for pred_date, targets in rows:
            conn2 = self._conn()
            settled = conn2.execute(
                "SELECT COUNT(*) FROM prediction_results WHERE date=? AND status='settled'",
                (pred_date,)
            ).fetchone()[0]
            conn2.close()
            if not settled:
                return (pred_date, targets)
        return None

    def get_prediction(self, pred_date: str):
        """按日期读取已保存的预测（R11 同日预测锁定复用用）
        返回 dict（含 date/targets 等），无记录返回 None"""
        conn = self._conn()
        row = conn.execute("SELECT targets FROM predictions WHERE date=?", (pred_date,)).fetchone()
        conn.close()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    # ---------- 结算 ----------
    def settle(self, pred_date: str, buy_prices: dict = None, sell_prices: dict = None,
               buy_date: str = None, sell_date: str = None,
               reference_prices: dict = None) -> dict:
        """pred_date: 推荐日 T。
        真实可执行口径：T+1 收盘买入，T+2 收盘卖出。
        buy_prices/sell_prices 分别是两个执行日的收盘价；reference_prices 只保存 T 日参考价。"""
        buy_prices = buy_prices or {}
        sell_prices = sell_prices or {}
        reference_prices = reference_prices or {}
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
            reference = t.get("参考买入价(收盘)", reference_prices.get(code))
            buy = buy_prices.get(code)
            sell_close = sell_prices.get(code)
            if not buy or not sell_close:
                missing.append(code)
                continue
            ret = round((sell_close / buy - 1) * 100, 2)
            conn.execute(
                "INSERT INTO prediction_results(date, target_code, target_name, buy_price, sell_price, ret, "
                "sell_close, ret_close, buy_date, sell_date, reference_price, status, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pred_date, code, t.get("name"), buy, sell_close, ret, sell_close, ret,
                 buy_date, sell_date, reference, "settled",
                 __import__("datetime").datetime.now().isoformat()))
            saved += 1
        conn.commit()
        conn.close()
        if saved:
            try:
                self.export_history()
            except Exception as e:
                log.warning("数据积累文件更新失败: %s", str(e)[:120])
        return {"date": pred_date, "buy_date": buy_date, "sell_date": sell_date,
                "settled": saved, "missing_close": missing,
                "settlement_rule": "T+1收盘买入→T+2收盘卖出"}

    def settle_pending(self, cached, today: str = None) -> dict:
        """自动结算：找最近一条未结算预测（昨天及之前），若其次日为过去交易日则回填收盘价"""
        from datetime import date as _date
        today_str = today or str(_date.today())
        # 找昨天及之前的未结算预测（今天的预测今天不能结算）
        row = self._find_settleable_prediction(today_str)
        if not row:
            return {"note": "无可结算预测"}
        pred_date = row[0]
        conn = self._conn()

        # 找次日交易日（THS 优先 → MCP → ego 兜底）
        today = today_str
        pts = []
        # THS 优先
        try:
            from ..ths_client import get_ths_client
            from datetime import date as _d, timedelta as _td
            ths = get_ths_client()
            ths_days = ths.trading_days(_d.fromisoformat(pred_date), _d.fromisoformat(today) + _td(days=1))
            if ths_days:
                pts = sorted(ths_days)
                log.info("THS 交易日: %s", pts[-3:] if len(pts) > 3 else pts)
        except Exception as e:
            log.warning("THS 交易日历失败: %s", str(e)[:100])
        # MCP 兜底
        if not pts:
            try:
                from .backtest import INDEX_TICKER
                resp = cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                                   {"ticker": INDEX_TICKER, "market": "index",
                                    "end_date": today, "limit": 12})
                pts = sorted({p["time"] for p in ((resp or {}).get("data") or {}).get("points") or []})
                if pred_date in pts:
                    pts = pts[pts.index(pred_date):]
            except Exception as e:
                log.warning("MCP 交易日历失败，切 ego 兜底: %s", str(e)[:100])
        # ego 兜底
        if not pts:
            from .alt_data import EgoOpenPrices
            from ..config import paths as get_paths
            ego = EgoOpenPrices(get_paths()["data"] / "ego_kline.db")
            pts = ego.fetch_index_dates(today, limit=12)
        if pred_date not in pts:
            return {"date": pred_date, "note": "预测日非交易日"}
        idx = pts.index(pred_date)
        if idx + 1 >= len(pts):
            return {"date": pred_date, "note": f"T+1买入日({today}前)无交易日数据"}
        buy_date = pts[idx + 1]
        if idx + 2 >= len(pts):
            return {"date": pred_date, "buy_date": buy_date,
                    "note": "T+2卖出日尚未出现，暂不结算"}
        sell_date = pts[idx + 2]
        # 只有卖出日已经到来，才能完成一次完整交易评估
        if sell_date > today:
            return {"date": pred_date, "buy_date": buy_date, "sell_date": sell_date,
                    "note": "尚未到T+2卖出日，暂不结算"}
        if buy_date == pred_date or sell_date == buy_date:
            return {"date": pred_date, "note": "执行日数据异常"}

        # 拉取 T+2 卖出日收盘价（MCP 优先，失败自动切 ego/THS 兜底）
        targets = json.loads(row[1]).get("targets") or []
        codes = [(t.get("code") or "").split(".")[0] for t in targets]
        closes = {}
        sell_sources = {}
        mcp_failed = False
        for code in codes:
            # THS是当前行情主源；MCP只作为兜底，避免坏代理优先污染真实收盘价。
            try:
                from ..ths_client import get_ths_client
                from datetime import date as _d
                ths = get_ths_client()
                thscode = f"{code}.SH" if code.startswith("6") else f"{code}.SZ"
                day = _d.fromisoformat(sell_date)
                for it in ths.kline(thscode, day, day):
                    if not it.get("date_ms"):
                        continue
                    item_day = __import__("datetime").datetime.fromtimestamp(
                        it["date_ms"] / 1000).strftime("%Y-%m-%d")
                    if item_day == sell_date and it.get("close_price") is not None:
                        closes[code] = float(it["close_price"])
                        sell_sources[code] = "THS"
                        break
            except Exception as e:
                mcp_failed = True
                log.warning("THS 卖出日K线取 %s 失败: %s", code, str(e)[:120])
            if code in closes:
                continue
            try:
                resp = cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                                   {"ticker": code, "market": "a_stock", "end_date": sell_date, "limit": 6})
                if not isinstance(resp, dict):
                    mcp_failed = True
                else:
                    pt = next((x for x in ((resp or {}).get("data") or {}).get("points") or []
                               if x.get("time") == sell_date), None)
                    if pt and pt.get("close"):
                        closes[code] = float(pt["close"])
                        sell_sources[code] = "MCP"
            except Exception as e:
                mcp_failed = True
                log.warning("MCP 卖出日K线取 %s 失败: %s", code, str(e)[:120])
        sells = closes
        missing_sells = [code for code in codes if code not in sells]
        if missing_sells:
            # 兜底：ego browser 东财K线（含开盘价+收盘价）；只补缺失代码，不覆盖已验证价格
            log.info("卖出日缺 %s，切换 ego browser 兜底", missing_sells)
            from .alt_data import EgoOpenPrices
            from ..config import paths as get_paths
            ego = EgoOpenPrices(get_paths()["data"] / "ego_kline.db")
            _, ego_closes = ego.fetch_with_close(sell_date, missing_sells)
            for code, price in ego_closes.items():
                if price:
                    sells[code] = float(price)
                    sell_sources[code] = "ego"
        if any(code not in sells for code in codes):
            missing = [code for code in codes if code not in sells]
            return {"date": pred_date, "buy_date": buy_date, "sell_date": sell_date,
                    "note": f"卖出日收盘价缺失:{','.join(missing)}"}

        # T+1 买入日收盘价。没有这个价格就不能伪造真实可执行收益。
        buys = {}
        buy_sources = {}
        for code in codes:
            if code not in sells:
                continue
            try:
                from ..ths_client import get_ths_client
                from datetime import date as _d
                ths = get_ths_client()
                thscode = f"{code}.SH" if code.startswith("6") else f"{code}.SZ"
                day = _d.fromisoformat(buy_date)
                for it in ths.kline(thscode, day, day):
                    if not it.get("date_ms"):
                        continue
                    item_day = __import__("datetime").datetime.fromtimestamp(
                        it["date_ms"] / 1000).strftime("%Y-%m-%d")
                    if item_day == buy_date and it.get("close_price") is not None:
                        buys[code] = float(it["close_price"])
                        buy_sources[code] = "THS"
                        break
            except Exception as e:
                log.warning("THS 买入日K线取 %s 失败: %s", code, str(e)[:120])
            if code not in buys:
                try:
                    resp = cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                                       {"ticker": code, "market": "a_stock",
                                        "end_date": buy_date, "limit": 6})
                    pt = next((x for x in ((resp or {}).get("data") or {}).get("points") or []
                               if x.get("time") == buy_date), None)
                    if pt and pt.get("close"):
                        buys[code] = float(pt["close"])
                        buy_sources[code] = "MCP"
                except Exception as e:
                    log.warning("MCP 买入日K线取 %s 失败: %s", code, str(e)[:120])
            if code not in buys:
                try:
                    from .alt_data import EgoOpenPrices
                    from ..config import paths as get_paths
                    _, close = EgoOpenPrices(get_paths()["data"] / "ego_kline.db").fetch_with_close(buy_date, [code])
                    if close.get(code):
                        buys[code] = float(close[code])
                        buy_sources[code] = "ego"
                except Exception as e:
                    log.warning("ego 买入日K线取 %s 失败: %s", code, str(e)[:120])
        if not buys:
            return {"date": pred_date, "buy_date": buy_date, "sell_date": sell_date,
                    "note": "买入日收盘价未获取到"}
        missing_buys = [code for code in codes if code not in buys]
        if missing_buys:
            return {"date": pred_date, "buy_date": buy_date, "sell_date": sell_date,
                    "note": f"买入日收盘价缺失:{','.join(missing_buys)}"}
        res = self.settle(pred_date, buys, sells, buy_date=buy_date, sell_date=sell_date)
        res["sell_date"] = sell_date
        used_sources = set(sell_sources.values()) | set(buy_sources.values())
        if used_sources == {"THS"}:
            res["data_source"] = "同花顺API"
        elif "ego" in used_sources:
            res["data_source"] = "ego浏览器兜底"
        elif "MCP" in used_sources:
            res["data_source"] = "MCP兜底"
        elif mcp_failed:
            res["data_source"] = "部分价格经兜底获取"
        return res

    # ---------- 数据积累文件（后台分析用，前端不展示累计） ----------
    def export_history(self) -> dict:
        """把全部已结算推荐导出到 data/recommendation_history.json（积累+分析）
        含：全部记录、按日汇总、累计统计、分股统计、按月统计"""
        import statistics as _st
        conn = self._conn()
        rows = conn.execute(
            "SELECT date, target_code, target_name, buy_price, sell_price, ret, sell_close, ret_close, "
            "buy_date, sell_date, reference_price, status "
            "FROM prediction_results WHERE status='settled' ORDER BY date, id").fetchall()
        conn.close()
        records = [{"date": r[0], "code": r[1], "name": r[2], "buy": r[3], "sell": r[4],
                    "ret": r[5], "sell_close": r[6], "ret_close": r[7],
                    "buy_date": r[8], "sell_date": r[9], "reference_price": r[10]} for r in rows]
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
                             "sell_close": x["sell_close"], "ret": x["ret"],
                             "buy_date": x.get("buy_date"), "sell_date": x.get("sell_date")} for x in rs],
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
            "sell_close, ret_close, buy_date, sell_date, reference_price, status "
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
                 "sell_close": r[6], "ret_close": r[7],
                 "buy_date": r[8], "sell_date": r[9], "reference_price": r[10], "status": r[11]}
                for r in rows[:15]
            ],
        }
