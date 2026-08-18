"""RSI Shadow Mode — Candidate 与 Production 真实市场并行验证

Phase 6: Shadow Prediction + Evaluation + Comparison

核心约束：
  - Candidate 绝对不影响 Production
  - Shadow Prediction 标记 prediction_mode="shadow"
  - Production Prediction 标记 prediction_mode="production"
  - 数据源严格区分：live_shadow / production / backtest / validation
  - 禁止自动 Promotion
"""
import json
import logging
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("rsi_shadow")

# ── 配置 ──
SHADOW_MIN_DAYS = 5       # 最少 shadow 天数
SHADOW_MIN_SAMPLES = 10   # 最少 shadow 交易次数

# ── Shadow 状态 ──
SHADOW_TESTING = "shadow_testing"
SHADOW_PASS = "shadow_pass"
SHADOW_FAIL = "shadow_fail"
SHADOW_INSUFFICIENT = "shadow_insufficient_data"


def init_shadow_tables(db_path: Path) -> None:
    """初始化 Shadow Prediction 表（不修改现有表）"""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shadow_prediction_id TEXT UNIQUE NOT NULL,
            candidate_id TEXT NOT NULL,
            production_version TEXT,
            candidate_version TEXT,
            prediction_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            prediction_price REAL,
            prediction_reason TEXT,
            prediction_mode TEXT NOT NULL,
            strategy_version TEXT,
            data_source TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shadow_prediction_id TEXT NOT NULL,
            evaluation_date TEXT,
            actual_price REAL,
            return_rate REAL,
            hit INTEGER,
            evaluation_result TEXT,
            evaluated_at TEXT,
            FOREIGN KEY (shadow_prediction_id) REFERENCES shadow_predictions(shadow_prediction_id)
        )""")
    conn.commit()
    conn.close()
    log.info("Shadow 表已初始化")


class AShareShadowRunner:
    """Shadow Runner — 在相同市场数据下同时运行 Production 和 Candidate

    不修改 Production 逻辑，不修改飞书输出。
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        init_shadow_tables(db_path)

    def run_shadow(
        self,
        cached,
        candidate_params: Dict[str, Any],
        candidate_id: str,
        candidate_version: str = "",
        target_date: str = None,
    ) -> Dict[str, Any]:
        """运行 Shadow Prediction

        使用与 Production 相同的市场数据，但用 Candidate 参数选股。
        结果标记为 prediction_mode="shadow"，不发送到飞书。

        Args:
            cached: MCP cached client
            candidate_params: Candidate 的参数
            candidate_id: Candidate ID
            candidate_version: Candidate 版本
            target_date: 目标日期（默认今天）

        Returns:
            {"predictions": [...], "date": str, "candidate_id": str}
        """
        from datetime import date as dt_date
        from app.predict.backtest import Backtest
        from app.predict.candidate_pool import CandidatePool
        from app.predict.scoring import score_pool
        from app.predict.strategy import Strategy, compute_vol_ratio, MAX_VOL_RATIO, TOP_PRE

        today = target_date or str(dt_date.today())
        bt = Backtest(cached)
        trading = bt.trading_days(today, 2)
        if len(trading) < 1:
            log.warning("无交易日数据")
            return {"predictions": [], "date": today, "candidate_id": candidate_id}
        t = trading[-1]

        # 使用 Candidate 参数构建自定义策略
        custom_max_vol = candidate_params.get("MAX_VOL_RATIO", MAX_VOL_RATIO)
        custom_top_pre = candidate_params.get("TOP_PRE", TOP_PRE)
        custom_top_n = candidate_params.get("top_n", 3)

        def kline_lookup(ticker, end_date):
            return cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                               {"ticker": ticker, "market": "a_stock", "end_date": end_date, "limit": 12})

        class ShadowStrategy(Strategy):
            def select(self, pool, day_t, day_t1, top_n=3):
                pre = self.base_score_fn(pool, top_n=custom_top_pre)
                kept = []
                for pick in pre:
                    resp = self.kline_lookup(pick["ticker"], day_t1)
                    vr = compute_vol_ratio(resp, day_t)
                    if vr is None:
                        kept.append(pick)
                        continue
                    if vr > custom_max_vol:
                        continue
                    pick = dict(pick)
                    pick["vol_ratio"] = round(vr, 2)
                    pick["factors"] = dict(pick.get("factors") or {})
                    pick["factors"]["量比过滤"] = f"量比{vr:.1f} 通过"
                    kept.append(pick)
                kept.sort(key=lambda x: x.get("score") or 0, reverse=True)
                return kept[:custom_top_n]

        shadow_strat = ShadowStrategy(cached, score_pool, kline_lookup)

        # 构建候选池（与 Production 相同数据源）
        pool = CandidatePool(cached).build(t)
        top = shadow_strat.select(pool, t, trading[0] if len(trading) > 1 else t, top_n=custom_top_n)

        # 补齐 K 线数据
        for pick in top:
            resp = kline_lookup(pick["ticker"], t)
            pts = {p["time"]: p for p in ((resp or {}).get("data") or {}).get("points") or []}
            pt = pts.get(t)
            vr = compute_vol_ratio(resp, t) if pt else None
            pick["参考买入价(收盘)"] = pt.get("close") if pt else None
            pick["量比"] = round(vr, 2) if vr else None

        # 持久化 Shadow Predictions
        predictions = []
        now = datetime.now().isoformat(timespec="seconds")
        conn = sqlite3.connect(self.db_path)

        for pick in top:
            code = (pick.get("ticker") or "").split(".")[0]
            sp_id = f"shadow_{today}_{code}_{candidate_id[:8]}"
            pred = {
                "shadow_prediction_id": sp_id,
                "candidate_id": candidate_id,
                "production_version": "v1.0",
                "candidate_version": candidate_version,
                "prediction_date": today,
                "stock_code": code,
                "stock_name": pick.get("name", ""),
                "prediction_price": pick.get("参考买入价(收盘)"),
                "prediction_reason": pick.get("factors", {}),
                "prediction_mode": "shadow",
                "strategy_version": candidate_version,
                "data_source": "live_shadow",
                "created_at": now,
            }
            predictions.append(pred)

            try:
                conn.execute(
                    "INSERT OR IGNORE INTO shadow_predictions"
                    "(shadow_prediction_id, candidate_id, production_version, candidate_version, "
                    "prediction_date, stock_code, stock_name, prediction_price, prediction_reason, "
                    "prediction_mode, strategy_version, data_source, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sp_id, candidate_id, "v1.0", candidate_version,
                     today, code, pick.get("name", ""), pick.get("参考买入价(收盘)"),
                     json.dumps(pick.get("factors", {}), ensure_ascii=False),
                     "shadow", candidate_version, "live_shadow", now))
            except Exception as e:
                log.warning("Shadow prediction 持久化失败: %s", e)

        conn.commit()
        conn.close()

        log.info("Shadow Prediction: %s %d 只候选 (candidate=%s)", today, len(predictions), candidate_id[:12])
        return {"predictions": predictions, "date": today, "candidate_id": candidate_id}

    def record_production(
        self,
        production_targets: List[Dict[str, Any]],
        prediction_date: str,
    ) -> int:
        """将 Production 预测也记录到 shadow_predictions 表（便于对比）

        Args:
            production_targets: Production 的 targets 列表
            prediction_date: 预测日

        Returns:
            记录条数
        """
        now = datetime.now().isoformat(timespec="seconds")
        conn = sqlite3.connect(self.db_path)
        count = 0
        for t in production_targets:
            code = (t.get("code") or "").split(".")[0]
            sp_id = f"prod_{prediction_date}_{code}"
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO shadow_predictions"
                    "(shadow_prediction_id, candidate_id, production_version, candidate_version, "
                    "prediction_date, stock_code, stock_name, prediction_price, prediction_reason, "
                    "prediction_mode, strategy_version, data_source, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sp_id, "production", "v1.0", "v1.0",
                     prediction_date, code, t.get("name", ""),
                     t.get("参考买入价(收盘)"),
                     json.dumps({"reason": t.get("reason", "")}, ensure_ascii=False),
                     "production", "v1.0", "production", now))
                count += 1
            except Exception as e:
                log.warning("Production shadow 记录失败: %s", e)
        conn.commit()
        conn.close()
        return count


class AShareShadowEvaluator:
    """Shadow Evaluator — 用真实市场结果评估 Shadow Predictions"""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def evaluate_pending(self, cached, target_date: str = None) -> Dict[str, Any]:
        """评估所有未结算的 Shadow Predictions

        使用真实市场 T+1 收盘价评估。

        Returns:
            {"evaluated": int, "production_results": [...], "shadow_results": [...]}
        """
        from datetime import date as dt_date
        from app.predict.backtest import Backtest

        today = target_date or str(dt_date.today())
        conn = sqlite3.connect(self.db_path)

        # 找到所有未评估的 predictions
        rows = conn.execute(
            "SELECT sp.shadow_prediction_id, sp.prediction_date, sp.stock_code, "
            "sp.stock_name, sp.prediction_price, sp.prediction_mode, sp.candidate_id "
            "FROM shadow_predictions sp "
            "LEFT JOIN shadow_results sr ON sp.shadow_prediction_id = sr.shadow_prediction_id "
            "WHERE sr.id IS NULL "
            "ORDER BY sp.prediction_date"
        ).fetchall()

        if not rows:
            conn.close()
            return {"evaluated": 0, "production_results": [], "shadow_results": []}

        # 获取 T+1 收盘价
        bt = Backtest(cached)
        production_results = []
        shadow_results = []

        for row in rows:
            sp_id, pred_date, code, name, buy_price, mode, cand_id = row
            if not buy_price:
                continue

            # 获取 T+1 收盘价
            try:
                trading = bt.trading_days(pred_date, 3)
                if len(trading) < 2:
                    continue
                t1 = trading[-1] if trading[-1] != pred_date else trading[-2] if len(trading) > 1 else None
                if not t1 or t1 <= pred_date:
                    continue

                resp = cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                                   {"ticker": code, "market": "a_stock", "end_date": t1, "limit": 5})
                pts = {p["time"]: p for p in ((resp or {}).get("data") or {}).get("points") or []}
                pt_t1 = pts.get(t1)
                if not pt_t1:
                    continue
                sell_close = pt_t1.get("close")
                if not sell_close:
                    continue

                ret = round((sell_close / buy_price - 1) * 100, 2)
                hit = ret > 0

                now = datetime.now().isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO shadow_results"
                    "(shadow_prediction_id, evaluation_date, actual_price, return_rate, hit, "
                    "evaluation_result, evaluated_at) VALUES(?,?,?,?,?,?,?)",
                    (sp_id, t1, sell_close, ret, 1 if hit else 0,
                     json.dumps({"return_rate": ret, "hit": hit}), now))

                result = {
                    "shadow_prediction_id": sp_id,
                    "prediction_date": pred_date,
                    "stock_code": code,
                    "stock_name": name,
                    "buy_price": buy_price,
                    "actual_price": sell_close,
                    "return_rate": ret,
                    "hit": hit,
                    "prediction_mode": mode,
                    "candidate_id": cand_id,
                    "evaluation_date": t1,
                }

                if mode == "production":
                    production_results.append(result)
                else:
                    shadow_results.append(result)

            except Exception as e:
                log.warning("Shadow 评估失败 %s: %s", sp_id, e)

        conn.commit()
        conn.close()

        log.info("Shadow Evaluation: %d production + %d shadow 结果",
                 len(production_results), len(shadow_results))
        return {
            "evaluated": len(production_results) + len(shadow_results),
            "production_results": production_results,
            "shadow_results": shadow_results,
        }


class AShareShadowComparator:
    """Shadow Comparator — Production vs Candidate 真实市场对比"""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def compare(self, candidate_id: str = None) -> Dict[str, Any]:
        """对比 Production vs Candidate 的真实市场表现

        Args:
            candidate_id: 指定 Candidate（None 则对比全部）

        Returns:
            ComparisonResult dict
        """
        conn = sqlite3.connect(self.db_path)

        # 获取所有已评估结果
        query = (
            "SELECT sp.prediction_mode, sp.candidate_id, sp.prediction_date, "
            "sp.stock_code, sp.stock_name, sp.prediction_price, "
            "sr.actual_price, sr.return_rate, sr.hit, sr.evaluation_date "
            "FROM shadow_predictions sp "
            "JOIN shadow_results sr ON sp.shadow_prediction_id = sr.shadow_prediction_id "
            "ORDER BY sp.prediction_date"
        )
        rows = conn.execute(query).fetchall()
        conn.close()

        production = []
        shadow = []
        for r in rows:
            mode, cid, pred_date, code, name, buy, sell, ret, hit, ev_date = r
            entry = {
                "prediction_date": pred_date, "stock_code": code, "stock_name": name,
                "buy_price": buy, "actual_price": sell, "return_rate": ret,
                "hit": bool(hit), "evaluation_date": ev_date,
            }
            if mode == "production":
                production.append(entry)
            elif mode == "shadow":
                if candidate_id is None or cid == candidate_id:
                    shadow.append(entry)

        prod_stats = self._compute_stats(production)
        shadow_stats = self._compute_stats(shadow)

        # 按日对比
        daily_comparison = self._daily_compare(production, shadow)

        # 样本充足性
        sufficient = (prod_stats.get("count", 0) >= SHADOW_MIN_SAMPLES and
                      shadow_stats.get("count", 0) >= SHADOW_MIN_SAMPLES)

        # 判断
        if not sufficient:
            decision = SHADOW_INSUFFICIENT
            confidence = "low"
        elif shadow_stats.get("avg_ret", 0) > prod_stats.get("avg_ret", 0):
            decision = SHADOW_PASS
            confidence = "medium" if shadow_stats.get("count", 0) >= 20 else "low"
        else:
            decision = SHADOW_FAIL
            confidence = "medium" if shadow_stats.get("count", 0) >= 20 else "low"

        return {
            "decision": decision,
            "confidence": confidence,
            "sample_count": {
                "production": prod_stats.get("count", 0),
                "shadow": shadow_stats.get("count", 0),
                "min_required": SHADOW_MIN_SAMPLES,
            },
            "production_stats": prod_stats,
            "shadow_stats": shadow_stats,
            "excess_return": round(
                shadow_stats.get("avg_ret", 0) - prod_stats.get("avg_ret", 0), 2
            ) if shadow_stats.get("count") and prod_stats.get("count") else None,
            "candidate_better_rate": self._candidate_better_rate(daily_comparison),
            "daily_comparison": daily_comparison,
            "data_source": "live_shadow",
            "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _compute_stats(self, entries: List[Dict]) -> Dict[str, Any]:
        if not entries:
            return {"count": 0}
        rets = [e["return_rate"] for e in entries if e.get("return_rate") is not None]
        if not rets:
            return {"count": 0}
        wins = [r for r in rets if r > 0]
        return {
            "count": len(rets),
            "win_rate": round(len(wins) / len(rets) * 100, 1),
            "avg_ret": round(statistics.mean(rets), 2),
            "median_ret": round(statistics.median(rets), 2),
            "best_ret": round(max(rets), 2),
            "worst_ret": round(min(rets), 2),
        }

    def _daily_compare(self, production: List, shadow: List) -> List[Dict]:
        """按日对比"""
        prod_by_date = {}
        for e in production:
            prod_by_date.setdefault(e["prediction_date"], []).append(e)

        shadow_by_date = {}
        for e in shadow:
            shadow_by_date.setdefault(e["prediction_date"], []).append(e)

        all_dates = sorted(set(list(prod_by_date.keys()) + list(shadow_by_date.keys())))
        result = []
        for d in all_dates:
            p_rets = [e["return_rate"] for e in prod_by_date.get(d, []) if e.get("return_rate") is not None]
            s_rets = [e["return_rate"] for e in shadow_by_date.get(d, []) if e.get("return_rate") is not None]
            p_avg = round(statistics.mean(p_rets), 2) if p_rets else None
            s_avg = round(statistics.mean(s_rets), 2) if s_rets else None
            result.append({
                "date": d,
                "production_count": len(p_rets),
                "shadow_count": len(s_rets),
                "production_avg_ret": p_avg,
                "shadow_avg_ret": s_avg,
                "candidate_won": s_avg > p_avg if s_avg is not None and p_avg is not None else None,
            })
        return result

    def _candidate_better_rate(self, daily: List[Dict]) -> Optional[float]:
        """Candidate 在多少天优于 Production"""
        compared = [d for d in daily if d.get("candidate_won") is not None]
        if not compared:
            return None
        won = sum(1 for d in compared if d["candidate_won"])
        return round(won / len(compared) * 100, 1)


def update_shadow_status(candidate_id: str, comparison: Dict, candidate_dir: Path) -> bool:
    """根据 Shadow 对比结果更新 Candidate 状态"""
    fp = candidate_dir / f"{candidate_id}.json"
    if not fp.exists():
        return False
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        decision = comparison.get("decision", SHADOW_INSUFFICIENT)
        data["status"] = decision
        data["shadow_comparison"] = comparison
        data["metadata"] = data.get("metadata", {})
        data["metadata"]["shadow_evaluated_at"] = comparison.get("evaluated_at")
        data["metadata"]["shadow_confidence"] = comparison.get("confidence")
        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Candidate %s shadow 状态更新: → %s", candidate_id[:12], decision)
        return True
    except Exception as e:
        log.warning("Shadow 状态更新失败: %s", e)
        return False
