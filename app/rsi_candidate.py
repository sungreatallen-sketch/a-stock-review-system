"""RSI Candidate Strategy — Insight → Candidate → Backtest → Benchmark

Phase 4: RSI 第一次从"总结历史"进入"提出策略假设"

核心约束：
  - Candidate 不修改 Production Strategy
  - Candidate 不自动上线
  - Candidate 只存在于 candidate/ 目录
  - 必须经过 Backtest + Benchmark 才能评估
  - 数据泄漏控制：discovery_period / validation_period 分离
  - 样本门槛：trades < MIN_TRADES → insufficient_data
"""
import hashlib
import json
import logging
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("rsi_candidate")

# ── 样本门槛 ──
MIN_TRADES = 10        # backtest 最少交易次数
MIN_BACKTEST_DAYS = 30 # backtest 最少天数

# ── 数据泄漏控制 ──
DISCOVERY_RATIO = 0.6  # discovery period 占总窗口的 60%

# ── Candidate 状态 ──
STATUS_CANDIDATE = "candidate"
STATUS_BACKTESTED = "backtested"
STATUS_VALIDATED = "validated"
STATUS_INSUFFICIENT = "insufficient_data"
STATUS_REJECTED = "rejected"


class CandidateStrategy:
    """Candidate Strategy 模型"""

    def __init__(
        self,
        candidate_id: str,
        created_at: str,
        parent_strategy: str,
        parent_version: str,
        version: str,
        hypothesis: str,
        parameters: Dict[str, Any],
        source_insights: List[str],
        status: str = STATUS_CANDIDATE,
        backtest_result: Optional[Dict[str, Any]] = None,
        benchmark_result: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.candidate_id = candidate_id
        self.created_at = created_at
        self.parent_strategy = parent_strategy
        self.parent_version = parent_version
        self.version = version
        self.hypothesis = hypothesis
        self.parameters = parameters
        self.source_insights = source_insights
        self.status = status
        self.backtest_result = backtest_result
        self.benchmark_result = benchmark_result
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "created_at": self.created_at,
            "parent_strategy": self.parent_strategy,
            "parent_version": self.parent_version,
            "version": self.version,
            "hypothesis": self.hypothesis,
            "parameters": self.parameters,
            "source_insights": self.source_insights,
            "status": self.status,
            "backtest_result": self.backtest_result,
            "benchmark_result": self.benchmark_result,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CandidateStrategy":
        return cls(**{k: v for k, v in d.items()
                      if k in cls.__init__.__code__.co_varnames})


class AShareCandidateGenerator:
    """从 Insight 生成 Candidate Strategy

    规则：
      - 每条 Insight 可能产生 0~N 个 Candidate
      - Candidate 保留 parent_strategy 信息
      - 不覆盖 Production
    """

    # Production Strategy 基线参数
    PRODUCTION_PARAMS = {
        "MAX_VOL_RATIO": 2.0,
        "TOP_PRE": 10,
        "top_n": 3,
        "index_filter": None,
    }
    PRODUCTION_VERSION = "v1.0"

    def generate(self, insights: List[Dict[str, Any]]) -> List[CandidateStrategy]:
        """从 Insight 列表生成 Candidate Strategy

        Args:
            insights: 来自 rsi_insight.py 的 Insight dict 列表

        Returns:
            CandidateStrategy 列表（已去重）
        """
        candidates = []
        seen_ids = set()
        now = datetime.now().isoformat(timespec="seconds")

        for ins in insights:
            ins_id = ins.get("insight_id", "")
            category = ins.get("category", "")
            confidence = ins.get("confidence", "low")
            data_quality = ins.get("data_quality", "insufficient_data")
            sample_count = ins.get("sample_count", 0)

            # 跳过 insufficient_data 的 insight
            if data_quality == "insufficient_data":
                continue

            # 根据 insight 类型生成 candidate
            generated = []

            if category == "confidence_group":
                generated.extend(self._from_confidence_insight(ins, now))

            elif category == "sector_performance":
                generated.extend(self._from_sector_insight(ins, now))

            elif category == "market_condition":
                generated.extend(self._from_market_insight(ins, now))

            elif category == "strategy_pattern":
                generated.extend(self._from_strategy_insight(ins, now))

            for cand in generated:
                if cand.candidate_id not in seen_ids:
                    seen_ids.add(cand.candidate_id)
                    candidates.append(cand)

        log.info("Candidate 生成: %d 条 Insight → %d 个 Candidate", len(insights), len(candidates))
        return candidates

    def _from_confidence_insight(self, ins: Dict, now: str) -> List[CandidateStrategy]:
        """从置信度分组 Insight 生成 Candidate"""
        conf = ins.get("condition", {}).get("confidence", "")
        wr = ins.get("success_rate", 0)
        avg_ret = ins.get("average_return", 0)
        n = ins.get("sample_count", 0)
        ins_id = ins.get("insight_id", "")

        cands = []

        # 如果低置信度表现差，生成一个过滤低置信度的 Candidate
        if conf == "低" and wr < 50 and n >= 3:
            cands.append(self._make_candidate(
                hypothesis=f"过滤低置信度推荐（历史命中率仅 {wr}%）",
                param_changes={"filter_low_confidence": True},
                source_insights=[ins_id],
                now=now,
                suffix="filter-low-conf",
            ))

        # 如果高置信度表现不如中置信度，生成调整置信度权重的 Candidate
        if conf == "高" and wr < 60 and n >= 3:
            cands.append(self._make_candidate(
                hypothesis=f"高置信度推荐历史命中率 {wr}%，可能过度自信，降低置信度权重",
                param_changes={"confidence_weight_adjust": -0.2},
                source_insights=[ins_id],
                now=now,
                suffix="reduce-conf-weight",
            ))

        return cands

    def _from_sector_insight(self, ins: Dict, now: str) -> List[CandidateStrategy]:
        """从板块表现 Insight 生成 Candidate"""
        sector = ins.get("condition", {}).get("top_sector", "")
        wr = ins.get("success_rate", 0)
        avg_ret = ins.get("average_return", 0)
        n = ins.get("sample_count", 0)
        ins_id = ins.get("insight_id", "")

        cands = []

        # 板块加分/减分
        if wr >= 70 and n >= 3:
            cands.append(self._make_candidate(
                hypothesis=f"板块「{sector}」历史表现优异（wr={wr}%, avg={avg_ret}%），给予加分",
                param_changes={"sector_boost": sector, "sector_boost_weight": 5.0},
                source_insights=[ins_id],
                now=now,
                suffix=f"boost-{sector[:4]}",
            ))
        elif wr < 40 and n >= 3:
            cands.append(self._make_candidate(
                hypothesis=f"板块「{sector}」历史表现较差（wr={wr}%, avg={avg_ret}%），给予减分",
                param_changes={"sector_penalty": sector, "sector_penalty_weight": -5.0},
                source_insights=[ins_id],
                now=now,
                suffix=f"penalty-{sector[:4]}",
            ))

        return cands

    def _from_market_insight(self, ins: Dict, now: str) -> List[CandidateStrategy]:
        """从市场条件 Insight 生成 Candidate"""
        cond = ins.get("condition", {}).get("涨停区间", "")
        wr = ins.get("success_rate", 0)
        avg_ret = ins.get("average_return", 0)
        n = ins.get("sample_count", 0)
        ins_id = ins.get("insight_id", "")

        cands = []

        # 涨停家数过多时减仓
        if "high_zt" in cond and wr < 50 and n >= 3:
            cands.append(self._make_candidate(
                hypothesis=f"涨停家数>=80时命中率仅 {wr}%，情绪过热，减少推荐数量",
                param_changes={"high_zt_reduce_top_n": 2},
                source_insights=[ins_id],
                now=now,
                suffix="reduce-hot-market",
            ))

        # 涨停家数适中时正常操作
        if "mid_zt" in cond and wr >= 60 and n >= 3:
            cands.append(self._make_candidate(
                hypothesis=f"涨停50-79家时命中率 {wr}%，情绪适中，维持标准策略",
                param_changes={},
                source_insights=[ins_id],
                now=now,
                suffix="standard-mid-market",
            ))

        return cands

    def _from_strategy_insight(self, ins: Dict, now: str) -> List[CandidateStrategy]:
        """从策略模式 Insight 生成 Candidate"""
        wr = ins.get("success_rate", 0)
        avg_ret = ins.get("average_return", 0)
        n = ins.get("sample_count", 0)
        ins_id = ins.get("insight_id", "")

        cands = []

        # 量比过滤调整
        if "量比" in str(ins.get("related_strategy", "")):
            if avg_ret > 3 and n >= 3:
                cands.append(self._make_candidate(
                    hypothesis=f"量比过滤策略 avg={avg_ret}%，适度放宽量比阈值以纳入更多标的",
                    param_changes={"MAX_VOL_RATIO": 2.5},
                    source_insights=[ins_id],
                    now=now,
                    suffix="relax-vol-ratio",
                ))

        return cands

    def _make_candidate(
        self, hypothesis: str, param_changes: Dict, source_insights: List[str], now: str, suffix: str,
    ) -> CandidateStrategy:
        """构造 CandidateStrategy"""
        # 确定性 ID
        id_str = f"{self.PRODUCTION_VERSION}|{suffix}|{json.dumps(param_changes, sort_keys=True)}"
        cand_id = f"cand_{hashlib.md5(id_str.encode()).hexdigest()[:12]}"

        # 合并参数：Production 基线 + 变更
        params = dict(self.PRODUCTION_PARAMS)
        params.update(param_changes)

        return CandidateStrategy(
            candidate_id=cand_id,
            created_at=now,
            parent_strategy="ashare_scoring_v1",
            parent_version=self.PRODUCTION_VERSION,
            version=f"{self.PRODUCTION_VERSION}-candidate-{suffix}",
            hypothesis=hypothesis,
            parameters=params,
            source_insights=source_insights,
            status=STATUS_CANDIDATE,
            metadata={"generator": "AShareCandidateGenerator", "discovery_ratio": DISCOVERY_RATIO},
        )


# ── Backtest Runner ──

def backtest_candidate(
    candidate: CandidateStrategy,
    cached,
    end_date: str,
    days: int = 60,
) -> Dict[str, Any]:
    """对 Candidate 进行回测

    数据泄漏控制：
      - 总窗口 = days
      - discovery_period = 前 60%
      - validation_period = 后 40%
      - 只用 validation_period 的结果评估 Candidate

    Args:
        candidate: CandidateStrategy
        cached: MCP cached client
        end_date: 回测截止日期
        days: 回测天数

    Returns:
        backtest_result dict
    """
    from app.predict.backtest import Backtest
    from app.predict.scoring import score_pool
    from app.predict.strategy import Strategy, compute_vol_ratio, MAX_VOL_RATIO, TOP_PRE

    bt = Backtest(cached)

    # 根据 candidate.parameters 构建自定义策略
    params = candidate.parameters
    custom_max_vol = params.get("MAX_VOL_RATIO", MAX_VOL_RATIO)
    custom_top_pre = params.get("TOP_PRE", TOP_PRE)
    custom_top_n = params.get("top_n", 3)
    custom_index_filter = params.get("index_filter")

    # 自定义 Strategy（修改量比阈值等）
    def kline_lookup(ticker, end_date):
        return cached.call("tongzhou-fin-research_fin_data__get_kline_series",
                           {"ticker": ticker, "market": "a_stock", "end_date": end_date, "limit": 12})

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

    custom_strat = CustomStrategy(cached, score_pool, kline_lookup)

    # 运行回测
    result = bt.run(
        end_date=end_date,
        days=days,
        top_n=custom_top_n,
        index_filter=custom_index_filter,
        strategy=custom_strat,
    )

    # 数据泄漏控制：分割 discovery / validation
    all_days = sorted(set(t["date"] for t in result.get("trades", [])))
    if len(all_days) >= 2:
        split_idx = int(len(all_days) * DISCOVERY_RATIO)
        validation_days = set(all_days[split_idx:])
        validation_trades = [t for t in result.get("trades", []) if t["date"] in validation_days]
    else:
        validation_days = set(all_days)
        validation_trades = result.get("trades", [])

    # validation period 统计
    val_stats = _compute_stats(validation_trades) if validation_trades else {"count": 0}

    return {
        "candidate_id": candidate.candidate_id,
        "full_result": result.get("stats", {}),
        "validation_period": {
            "days": sorted(validation_days),
            "stats": val_stats,
            "trade_count": len(validation_trades),
        },
        "discovery_period": {
            "days": sorted(set(all_days) - validation_days) if all_days else [],
        },
        "meta": {
            "total_days": days,
            "discovery_ratio": DISCOVERY_RATIO,
            "validation_days": len(validation_days),
        },
    }


def backtest_production(cached, end_date: str, days: int = 60) -> Dict[str, Any]:
    """对 Production Strategy 进行回测（同口径对比）"""
    from app.predict.backtest import Backtest

    bt = Backtest(cached)
    result = bt.run(end_date=end_date, days=days, top_n=3)

    all_days = sorted(set(t["date"] for t in result.get("trades", [])))
    if len(all_days) >= 2:
        split_idx = int(len(all_days) * DISCOVERY_RATIO)
        validation_days = set(all_days[split_idx:])
        validation_trades = [t for t in result.get("trades", []) if t["date"] in validation_days]
    else:
        validation_days = set(all_days)
        validation_trades = result.get("trades", [])

    val_stats = _compute_stats(validation_trades) if validation_trades else {"count": 0}

    return {
        "full_result": result.get("stats", {}),
        "validation_period": {
            "days": sorted(validation_days),
            "stats": val_stats,
            "trade_count": len(validation_trades),
        },
        "meta": {"total_days": days, "discovery_ratio": DISCOVERY_RATIO},
    }


# ── Benchmark ──

def benchmark_candidate(
    candidate_result: Dict[str, Any],
    production_result: Dict[str, Any],
    baseline_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Candidate vs Production vs Baseline 对比

    只比较 validation_period 的结果。
    """
    c_val = candidate_result.get("validation_period", {}).get("stats", {})
    p_val = production_result.get("validation_period", {}).get("stats", {})

    c_count = c_val.get("count", 0)
    p_count = p_val.get("count", 0)

    # 样本不足检查
    if c_count < MIN_TRADES or p_count < MIN_TRADES:
        return {
            "candidate_better": None,
            "confidence": "insufficient_data",
            "sample_count": {"candidate": c_count, "production": p_count},
            "min_trades_required": MIN_TRADES,
            "improvements": [],
            "degradations": [],
            "detail": {},
        }

    c_wr = c_val.get("win_rate", 0)
    p_wr = p_val.get("win_rate", 0)
    c_avg = c_val.get("avg_ret", 0)
    p_avg = p_val.get("avg_ret", 0)
    c_med = c_val.get("median_ret", 0)
    p_med = p_val.get("median_ret", 0)
    c_best = c_val.get("best_ret", 0)
    p_best = p_val.get("best_ret", 0)
    c_worst = c_val.get("worst_ret", 0)
    p_worst = p_val.get("worst_ret", 0)
    c_dd = c_val.get("max_drawdown", 0)
    p_dd = p_val.get("max_drawdown", 0)

    improvements = []
    degradations = []

    if c_wr > p_wr:
        improvements.append({"metric": "win_rate", "candidate": c_wr, "production": p_wr, "delta": round(c_wr - p_wr, 1)})
    elif c_wr < p_wr:
        degradations.append({"metric": "win_rate", "candidate": c_wr, "production": p_wr, "delta": round(c_wr - p_wr, 1)})

    if c_avg > p_avg:
        improvements.append({"metric": "avg_return", "candidate": c_avg, "production": p_avg, "delta": round(c_avg - p_avg, 2)})
    elif c_avg < p_avg:
        degradations.append({"metric": "avg_return", "candidate": c_avg, "production": p_avg, "delta": round(c_avg - p_avg, 2)})

    if c_med > p_med:
        improvements.append({"metric": "median_return", "candidate": c_med, "production": p_med, "delta": round(c_med - p_med, 2)})
    elif c_med < p_med:
        degradations.append({"metric": "median_return", "candidate": c_med, "production": p_med, "delta": round(c_med - p_med, 2)})

    # excess_return
    excess = round(c_avg - p_avg, 2)

    # 判断 candidate 是否更好
    # 条件：avg_return 更高 且 win_rate 不低于 production 超过 10%
    candidate_better = (c_avg > p_avg) and (c_wr >= p_wr - 10)

    # 置信度
    if c_count >= 30 and p_count >= 30:
        confidence = "high"
    elif c_count >= 15 and p_count >= 15:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "candidate_better": candidate_better,
        "confidence": confidence,
        "sample_count": {"candidate": c_count, "production": p_count},
        "improvements": improvements,
        "degradations": degradations,
        "excess_return": excess,
        "detail": {
            "candidate": {"win_rate": c_wr, "avg_ret": c_avg, "median_ret": c_med,
                          "best_ret": c_best, "worst_ret": c_worst, "max_drawdown": c_dd},
            "production": {"win_rate": p_wr, "avg_ret": p_avg, "median_ret": p_med,
                           "best_ret": p_best, "worst_ret": p_worst, "max_drawdown": p_dd},
        },
    }


def _compute_stats(trades: List[Dict]) -> Dict[str, Any]:
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


# ── 持久化 ──

def save_candidate(candidate: CandidateStrategy, data_dir: Path) -> bool:
    """保存 Candidate 到 candidate/ 目录"""
    fp = data_dir.parent / "candidate" / f"{candidate.candidate_id}.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(candidate.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Candidate 已保存: %s → %s", candidate.candidate_id, fp)
    return True


def load_candidates(candidate_dir: Path) -> List[CandidateStrategy]:
    """从 candidate/ 目录加载所有 Candidate"""
    if not candidate_dir.exists():
        return []
    cands = []
    for fp in sorted(candidate_dir.glob("cand_*.json")):
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
            cands.append(CandidateStrategy.from_dict(d))
        except Exception as e:
            log.warning("加载 Candidate 失败 %s: %s", fp.name, e)
    return cands


def load_candidate(candidate_id: str, candidate_dir: Path) -> Optional[CandidateStrategy]:
    """加载单个 Candidate"""
    fp = candidate_dir / f"{candidate_id}.json"
    if not fp.exists():
        return None
    try:
        return CandidateStrategy.from_dict(json.loads(fp.read_text(encoding="utf-8")))
    except Exception:
        return None
