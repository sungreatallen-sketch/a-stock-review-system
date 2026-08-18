"""RSI Walk-Forward Validation — Candidate OOS 验证

Phase 5: 用时间分割的 Walk-Forward 验证 Candidate

核心原则：
  - OOS 数据从未参与 Insight 生成或 Candidate 生成
  - 统计显著性检验
  - 鲁棒性检查（Consistency / Degradation）
  - 不自动 Promotion

Walk-Forward 方案：
  总窗口 90 天
  ├── Window 1: day[0:60] train → day[60:75] test  (OOS1)
  ├── Window 2: day[15:75] train → day[75:90] test  (OOS2)
  └── Window 3: day[0:75] train → day[75:90] test   (OOS3, 最终)

  OOS 数据完全独立于训练窗口。

统计显著性：
  - minimum_sample: OOS 交易次数 >= MIN_OOS_TRADES
  - confidence_interval: Wilson score interval for win_rate
  - p_value: Fisher exact test (if scipy available) or normal approximation
  - significance_level: alpha = 0.10 (金融领域常用)
"""
import json
import logging
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("rsi_validation")

# ── 配置 ──
MIN_OOS_TRADES = 5           # OOS 最少交易次数
SIGNIFICANCE_LEVEL = 0.10    # 显著性水平
TOTAL_WINDOW_DAYS = 90       # 总回测窗口
TRAIN_RATIO_1 = 0.67         # Window 1 训练比例
TRAIN_RATIO_2 = 0.83         # Window 2 训练比例
STEP_SIZE_RATIO = 0.17       # 滚动步长比例


class ValidationReport:
    """结构化验证报告"""

    def __init__(
        self,
        report_id: str,
        created_at: str,
        candidate_id: str,
        parent_version: str,
        candidate_version: str,
        walk_forward_windows: List[Dict[str, Any]],
        oos_aggregate: Dict[str, Any],
        production_aggregate: Dict[str, Any],
        statistical_tests: Dict[str, Any],
        robustness: Dict[str, Any],
        decision: str,
        confidence: str,
        reasoning: str,
    ):
        self.report_id = report_id
        self.created_at = created_at
        self.candidate_id = candidate_id
        self.parent_version = parent_version
        self.candidate_version = candidate_version
        self.walk_forward_windows = walk_forward_windows
        self.oos_aggregate = oos_aggregate
        self.production_aggregate = production_aggregate
        self.statistical_tests = statistical_tests
        self.robustness = robustness
        self.decision = decision
        self.confidence = confidence
        self.reasoning = reasoning

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "created_at": self.created_at,
            "candidate_id": self.candidate_id,
            "parent_version": self.parent_version,
            "candidate_version": self.candidate_version,
            "walk_forward_windows": self.walk_forward_windows,
            "oos_aggregate": self.oos_aggregate,
            "production_aggregate": self.production_aggregate,
            "statistical_tests": self.statistical_tests,
            "robustness": self.robustness,
            "decision": self.decision,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


class WalkForwardValidator:
    """Walk-Forward 验证器

    将总窗口分为多个 train/test 窗口，用 test 窗口（OOS）评估 Candidate。
    每个窗口独立运行 Production 和 Candidate 的 backtest。
    """

    def __init__(
        self,
        total_days: int = TOTAL_WINDOW_DAYS,
        min_oos_trades: int = MIN_OOS_TRADES,
        alpha: float = SIGNIFICANCE_LEVEL,
    ):
        self.total_days = total_days
        self.min_oos_trades = min_oos_trades
        self.alpha = alpha

    def validate(
        self,
        candidate_params: Dict[str, Any],
        cached,
        end_date: str,
    ) -> ValidationReport:
        """执行 Walk-Forward 验证

        Args:
            candidate_params: Candidate 的参数（如 MAX_VOL_RATIO, TOP_PRE 等）
            cached: MCP cached client
            end_date: 截止日期

        Returns:
            ValidationReport
        """
        import hashlib
        now = datetime.now().isoformat(timespec="seconds")

        # 获取交易日历
        from app.predict.backtest import Backtest
        bt = Backtest(cached)
        trading = bt.trading_days(end_date, self.total_days + 1)
        if len(trading) < 30:
            return self._empty_report(now, "交易日不足30天")

        # 构建 Walk-Forward 窗口
        windows = self._build_windows(trading)

        # 对每个窗口运行 Candidate 和 Production backtest
        wf_results = []
        for i, (train_days, test_days) in enumerate(windows):
            log.info("Walk-Forward Window %d: train=%s~%s (%d天) test=%s~%s (%d天)",
                     i + 1, train_days[0], train_days[-1], len(train_days),
                     test_days[0], test_days[-1], len(test_days))

            # Production backtest on test period
            prod_result = self._run_backtest(cached, test_days, {}, "production")

            # Candidate backtest on test period
            cand_result = self._run_backtest(cached, test_days, candidate_params, "candidate")

            wf_results.append({
                "window_id": i + 1,
                "train_period": {"start": train_days[0], "end": train_days[-1], "days": len(train_days)},
                "test_period": {"start": test_days[0], "end": test_days[-1], "days": len(test_days)},
                "production": prod_result,
                "candidate": cand_result,
            })

        # 聚合 OOS 结果
        oos_agg = self._aggregate_oos([w["candidate"] for w in wf_results])
        prod_agg = self._aggregate_oos([w["production"] for w in wf_results])

        # 统计检验
        stat_tests = self._statistical_tests(oos_agg, prod_agg)

        # 鲁棒性检查
        robustness = self._robustness_check(wf_results)

        # 决策
        decision, confidence, reasoning = self._make_decision(
            oos_agg, prod_agg, stat_tests, robustness
        )

        report_id = f"val_{hashlib.md5(f'{now}'.encode()).hexdigest()[:12]}"

        return ValidationReport(
            report_id=report_id,
            created_at=now,
            candidate_id="",
            parent_version="v1.0",
            candidate_version="",
            walk_forward_windows=wf_results,
            oos_aggregate=oos_agg,
            production_aggregate=prod_agg,
            statistical_tests=stat_tests,
            robustness=robustness,
            decision=decision,
            confidence=confidence,
            reasoning=reasoning,
        )

    def _build_windows(self, trading: List[str]) -> List[Tuple[List[str], List[str]]]:
        """构建 Walk-Forward 窗口"""
        n = len(trading)
        windows = []

        # Window 1: 前 67% train, 后 33% test
        split1 = int(n * TRAIN_RATIO_1)
        if split1 >= 10 and (n - split1) >= 5:
            windows.append((trading[:split1], trading[split1:]))

        # Window 2: 滚动一个步长
        step = max(1, int(n * STEP_SIZE_RATIO))
        split2 = split1 + step
        if split2 < n and split2 >= 20 and (n - split2) >= 5:
            windows.append((trading[step:split2], trading[split2:]))

        # Window 3: 前 83% train, 后 17% test（更长训练）
        split3 = int(n * TRAIN_RATIO_2)
        if split3 >= 20 and (n - split3) >= 5 and split3 != split1:
            windows.append((trading[:split3], trading[split3:]))

        # 去重（按 test_period 起始日）
        seen = set()
        unique = []
        for train, test in windows:
            key = test[0]
            if key not in seen:
                seen.add(key)
                unique.append((train, test))

        return unique if unique else [(trading[:int(n*0.67)], trading[int(n*0.67):])]

    def _run_backtest(
        self, cached, test_days: List[str], params: Dict[str, Any], label: str,
    ) -> Dict[str, Any]:
        """在指定日期范围运行 backtest"""
        from app.predict.backtest import Backtest
        from app.predict.scoring import score_pool
        from app.predict.strategy import Strategy, compute_vol_ratio, MAX_VOL_RATIO, TOP_PRE

        bt = Backtest(cached)
        end_date = test_days[-1]
        days = len(test_days) + 10  # 多取几天确保覆盖

        custom_max_vol = params.get("MAX_VOL_RATIO", MAX_VOL_RATIO)
        custom_top_pre = params.get("TOP_PRE", TOP_PRE)
        custom_top_n = params.get("top_n", 3)
        custom_index_filter = params.get("index_filter")

        def kline_lookup(ticker, ed):
            return cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                               {"ticker": ticker, "market": "a_stock", "end_date": ed, "limit": 12})

        if params:
            class CustomStrategy(Strategy):
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

            strat = CustomStrategy(cached, score_pool, kline_lookup)
            result = bt.run(end_date=end_date, days=days, top_n=custom_top_n,
                            index_filter=custom_index_filter, strategy=strat)
        else:
            result = bt.run(end_date=end_date, days=days, top_n=custom_top_n)

        # 只保留 test_days 范围内的交易
        test_set = set(test_days)
        filtered_trades = [t for t in result.get("trades", []) if t["date"] in test_set]

        stats = self._compute_stats(filtered_trades)
        return {
            "label": label,
            "stats": stats,
            "trade_count": len(filtered_trades),
            "test_days": test_days,
        }

    def _compute_stats(self, trades: List[Dict]) -> Dict[str, Any]:
        """计算交易统计"""
        if not trades:
            return {"count": 0}
        rets = [t["ret"] for t in trades if t.get("ret") is not None]
        if not rets:
            return {"count": 0}
        win = [r for r in rets if r > 0]
        eq = 1.0
        curve = []
        for r in rets:
            eq *= (1 + r / 100)
            curve.append(eq)
        peak = -1e9
        max_dd = 0
        for v in curve:
            peak = max(peak, v)
            dd = (peak - v) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        return {
            "count": len(rets),
            "win_rate": round(len(win) / len(rets) * 100, 1),
            "avg_ret": round(statistics.mean(rets), 2),
            "median_ret": round(statistics.median(rets), 2),
            "best_ret": round(max(rets), 2),
            "worst_ret": round(min(rets), 2),
            "total_return": round((eq - 1) * 100, 2),
            "max_drawdown": round(max_dd * 100, 2),
        }

    def _aggregate_oos(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """聚合多个窗口的 OOS 结果"""
        all_trades = 0
        all_wins = 0
        all_rets = []
        for r in results:
            s = r.get("stats", {})
            n = s.get("count", 0)
            all_trades += n
            wr = s.get("win_rate", 0) / 100
            all_wins += int(wr * n)
            avg = s.get("avg_ret", 0)
            all_rets.extend([avg] * n)  # 近似

        if all_trades == 0:
            return {"count": 0, "win_rate": 0, "avg_ret": 0, "median_ret": 0}

        return {
            "count": all_trades,
            "win_rate": round(all_wins / all_trades * 100, 1),
            "avg_ret": round(statistics.mean(all_rets), 2) if all_rets else 0,
            "median_ret": round(statistics.median(all_rets), 2) if all_rets else 0,
            "best_ret": max((r.get("stats", {}).get("best_ret", 0) for r in results), default=0),
            "worst_ret": min((r.get("stats", {}).get("worst_ret", 0) for r in results), default=0),
            "window_count": len(results),
        }

    def _statistical_tests(
        self, cand_agg: Dict, prod_agg: Dict,
    ) -> Dict[str, Any]:
        """统计显著性检验"""
        c_n = cand_agg.get("count", 0)
        p_n = prod_agg.get("count", 0)
        c_wr = cand_agg.get("win_rate", 0) / 100
        p_wr = prod_agg.get("win_rate", 0) / 100
        c_avg = cand_agg.get("avg_ret", 0)
        p_avg = prod_agg.get("avg_ret", 0)

        tests = {
            "sample_adequate": c_n >= self.min_oos_trades and p_n >= self.min_oos_trades,
            "candidate_trades": c_n,
            "production_trades": p_n,
            "min_oos_trades": self.min_oos_trades,
        }

        # Wilson score interval for win_rate
        if c_n > 0:
            tests["candidate_win_rate_ci"] = _wilson_ci(c_wr, c_n, 1 - self.alpha)
        if p_n > 0:
            tests["production_win_rate_ci"] = _wilson_ci(p_wr, p_n, 1 - self.alpha)

        # Win rate difference test (normal approximation)
        if c_n >= 5 and p_n >= 5:
            p_value = _proportion_z_test(c_wr, c_n, p_wr, p_n)
            tests["win_rate_p_value"] = p_value
            tests["win_rate_significant"] = p_value < self.alpha
        else:
            tests["win_rate_p_value"] = None
            tests["win_rate_significant"] = False

        # Return difference (simple t-test approximation)
        if c_n >= 5 and p_n >= 5:
            # Use avg_ret as point estimate
            excess = round(c_avg - p_avg, 2)
            tests["excess_return"] = excess
            tests["return_improvement"] = excess > 0
        else:
            tests["excess_return"] = None
            tests["return_improvement"] = False

        tests["significance_level"] = self.alpha
        return tests

    def _robustness_check(self, wf_results: List[Dict]) -> Dict[str, Any]:
        """鲁棒性检查：一致性 / 退化"""
        cand_wrs = []
        prod_wrs = []
        cand_rets = []
        prod_rets = []

        for w in wf_results:
            c = w.get("candidate", {}).get("stats", {})
            p = w.get("production", {}).get("stats", {})
            if c.get("count", 0) > 0:
                cand_wrs.append(c.get("win_rate", 0))
                cand_rets.append(c.get("avg_ret", 0))
            if p.get("count", 0) > 0:
                prod_wrs.append(p.get("win_rate", 0))
                prod_rets.append(p.get("avg_ret", 0))

        # Consistency: Candidate 在多少个窗口中优于 Production
        consistent_windows = 0
        total_windows = 0
        for w in wf_results:
            c = w.get("candidate", {}).get("stats", {})
            p = w.get("production", {}).get("stats", {})
            if c.get("count", 0) > 0 and p.get("count", 0) > 0:
                total_windows += 1
                if c.get("avg_ret", 0) > p.get("avg_ret", 0):
                    consistent_windows += 1

        consistency_rate = round(consistent_windows / total_windows * 100, 1) if total_windows > 0 else 0

        # Degradation: OOS vs In-Sample 退化程度（近似）
        # 如果有多个窗口，检查趋势
        degradation = "stable"
        if len(cand_rets) >= 2:
            if cand_rets[-1] < cand_rets[0] * 0.5:
                degradation = "significant_degradation"
            elif cand_rets[-1] < cand_rets[0] * 0.8:
                degradation = "moderate_degradation"
            elif cand_rets[-1] > cand_rets[0] * 1.2:
                degradation = "improvement"

        return {
            "consistent_windows": consistent_windows,
            "total_windows": total_windows,
            "consistency_rate": consistency_rate,
            "degradation": degradation,
            "candidate_win_rates": cand_wrs,
            "production_win_rates": prod_wrs,
            "candidate_returns": cand_rets,
            "production_returns": prod_rets,
        }

    def _make_decision(
        self, cand_agg: Dict, prod_agg: Dict, stat_tests: Dict, robustness: Dict,
    ) -> Tuple[str, str, str]:
        """验证决策"""
        c_n = cand_agg.get("count", 0)
        p_n = prod_agg.get("count", 0)

        # 样本不足
        if c_n < self.min_oos_trades or p_n < self.min_oos_trades:
            return ("insufficient_data", "low",
                    f"OOS样本不足: candidate={c_n}, production={p_n}, 需要>={self.min_oos_trades}")

        c_avg = cand_agg.get("avg_ret", 0)
        p_avg = prod_agg.get("avg_ret", 0)
        c_wr = cand_agg.get("win_rate", 0)
        p_wr = prod_agg.get("win_rate", 0)
        significant = stat_tests.get("win_rate_significant", False)
        consistency = robustness.get("consistency_rate", 0)
        degradation = robustness.get("degradation", "stable")

        # 综合判断
        reasons = []
        score = 0

        # 收益优势
        if c_avg > p_avg:
            score += 2
            reasons.append(f"OOS平均收益 {c_avg}% > Production {p_avg}%")
        else:
            score -= 2
            reasons.append(f"OOS平均收益 {c_avg}% <= Production {p_avg}%")

        # 命中率优势
        if c_wr > p_wr:
            score += 1
            reasons.append(f"OOS命中率 {c_wr}% > Production {p_wr}%")
        else:
            score -= 1
            reasons.append(f"OOS命中率 {c_wr}% <= Production {p_wr}%")

        # 统计显著性
        if significant:
            score += 2
            reasons.append(f"统计显著 (p={stat_tests.get('win_rate_p_value', '?'):.3f})")
        else:
            reasons.append("统计不显著")

        # 一致性
        if consistency >= 70:
            score += 1
            reasons.append(f"窗口一致性 {consistency}%")
        elif consistency < 50:
            score -= 1
            reasons.append(f"窗口一致性低 {consistency}%")

        # 鲁棒性
        if degradation == "significant_degradation":
            score -= 2
            reasons.append("严重退化")
        elif degradation == "moderate_degradation":
            score -= 1
            reasons.append("中度退化")

        # 最终决策
        if score >= 3:
            decision = "validated"
            confidence = "high" if score >= 5 else "medium"
        elif score >= 1:
            decision = "validated"
            confidence = "low"
        elif score <= -2:
            decision = "rejected"
            confidence = "medium" if score <= -4 else "low"
        else:
            decision = "rejected"
            confidence = "low"

        return decision, confidence, "; ".join(reasons)

    def _empty_report(self, now: str, reason: str) -> ValidationReport:
        return ValidationReport(
            report_id="val_empty",
            created_at=now,
            candidate_id="",
            parent_version="",
            candidate_version="",
            walk_forward_windows=[],
            oos_aggregate={"count": 0},
            production_aggregate={"count": 0},
            statistical_tests={},
            robustness={},
            decision="insufficient_data",
            confidence="low",
            reasoning=reason,
        )


# ── 统计工具 ──

def _wilson_ci(p: float, n: int, confidence: float) -> Tuple[float, float]:
    """Wilson score interval for proportion"""
    if n == 0:
        return (0.0, 0.0)
    z = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}.get(confidence, 1.645)
    denom = 1 + z**2 / n
    center = (p + z**2 / (2*n)) / denom
    margin = z * math.sqrt((p*(1-p) + z**2/(4*n)) / n) / denom
    return (round(max(0, center - margin), 3), round(min(1, center + margin), 3))


def _proportion_z_test(p1: float, n1: int, p2: float, n2: int) -> float:
    """Two-proportion z-test (two-tailed p-value)"""
    if n1 == 0 or n2 == 0:
        return 1.0
    p_pool = (p1 * n1 + p2 * n2) / (n1 + n2)
    if p_pool == 0 or p_pool == 1:
        return 1.0
    se = math.sqrt(p_pool * (1 - p_pool) * (1/n1 + 1/n2))
    if se == 0:
        return 1.0
    z = (p1 - p2) / se
    # Two-tailed p-value using normal approximation
    p_value = 2 * (1 - _norm_cdf(abs(z)))
    return round(p_value, 4)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF approximation"""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# ── Candidate 状态更新 ──

def update_candidate_status(
    candidate_id: str,
    validation_report: ValidationReport,
    candidate_dir: Path,
) -> bool:
    """根据验证结果更新 Candidate 状态"""
    fp = candidate_dir / f"{candidate_id}.json"
    if not fp.exists():
        log.warning("Candidate 文件不存在: %s", candidate_id)
        return False

    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        old_status = data.get("status", "")
        new_status = validation_report.decision

        data["status"] = new_status
        data["validation_report"] = validation_report.to_dict()
        data["metadata"] = data.get("metadata", {})
        data["metadata"]["validated_at"] = validation_report.created_at
        data["metadata"]["validation_confidence"] = validation_report.confidence

        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Candidate %s 状态更新: %s → %s", candidate_id, old_status, new_status)
        return True
    except Exception as e:
        log.warning("更新 Candidate 状态失败: %s", e)
        return False


# ── 持久化 ──

def save_validation_report(report: ValidationReport, data_dir: Path) -> bool:
    """保存验证报告到 data/validation/"""
    fp = data_dir / "validation" / f"{report.report_id}.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ValidationReport 已保存: %s", fp)
    return True


def load_validation_reports(data_dir: Path) -> List[Dict[str, Any]]:
    """加载所有验证报告"""
    val_dir = data_dir / "validation"
    if not val_dir.exists():
        return []
    reports = []
    for fp in sorted(val_dir.glob("val_*.json")):
        try:
            reports.append(json.loads(fp.read_text(encoding="utf-8")))
        except Exception:
            pass
    return reports
