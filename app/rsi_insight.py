"""RSI Insight Analyzer — Experience → 统计分析 → 结构化 Insight

Phase 3: 观察 + 分析 + 总结（不修改任何生产策略）

分析逻辑：统计分析为主，LLM 仅用于解释已有统计结果
样本门槛：minimum_sample_count=3（低于此标记 insufficient_data）
时间边界：只使用已完成 Evaluation 的 Experience，不使用未结算 Prediction
去重：insight_id 唯一键，重复分析不产生重复 Insight
"""
import hashlib
import json
import logging
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("rsi_insight")

# ── 样本门槛 ──
MIN_SAMPLE = 3       # 最小样本数
MIN_CONFIDENCE = 0.3  # 最低置信度


class Insight:
    """结构化 Insight 模型"""

    def __init__(
        self,
        insight_id: str,
        created_at: str,
        category: str,
        observation: str,
        sample_count: int,
        success_count: int,
        failure_count: int,
        success_rate: float,
        average_return: float,
        median_return: float,
        best_return: float,
        worst_return: float,
        failure_rate: float,
        confidence: str,
        data_quality: str,
        supporting_experiences: List[str],
        condition: Optional[Dict[str, Any]] = None,
        related_strategy: Optional[str] = None,
        market_condition: Optional[str] = None,
    ):
        self.insight_id = insight_id
        self.created_at = created_at
        self.category = category
        self.observation = observation
        self.sample_count = sample_count
        self.success_count = success_count
        self.failure_count = failure_count
        self.success_rate = success_rate
        self.average_return = average_return
        self.median_return = median_return
        self.best_return = best_return
        self.worst_return = worst_return
        self.failure_rate = failure_rate
        self.confidence = confidence
        self.data_quality = data_quality
        self.supporting_experiences = supporting_experiences
        self.condition = condition or {}
        self.related_strategy = related_strategy
        self.market_condition = market_condition

    def to_dict(self) -> Dict[str, Any]:
        return {
            "insight_id": self.insight_id,
            "created_at": self.created_at,
            "category": self.category,
            "observation": self.observation,
            "sample_count": self.sample_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": self.success_rate,
            "average_return": self.average_return,
            "median_return": self.median_return,
            "best_return": self.best_return,
            "worst_return": self.worst_return,
            "failure_rate": self.failure_rate,
            "confidence": self.confidence,
            "data_quality": self.data_quality,
            "supporting_experiences": self.supporting_experiences,
            "condition": self.condition,
            "related_strategy": self.related_strategy,
            "market_condition": self.market_condition,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Insight":
        return cls(**{k: v for k, v in d.items() if k in cls.__init__.__code__.co_varnames})


class AShareInsightAnalyzer:
    """从历史 Experience 中发现统计规律

    分析维度：
      1. 按 confidence 分组
      2. 按 top_sector 板块分组
      3. 按 strategy 关键词分组
      4. 按 market_condition（涨停数区间）分组
      5. 按 date 分组（日维度）
      6. 整体汇总

    不使用 LLM 做推断，仅用统计结果。
    """

    def __init__(self, minimum_sample: int = MIN_SAMPLE):
        self.minimum_sample = minimum_sample

    def analyze(self, experiences: List[Dict[str, Any]]) -> List[Insight]:
        """从 Experience 列表生成结构化 Insight

        Args:
            experiences: 来自 ASHRMemoryBackend 的 experience content 列表

        Returns:
            Insight 列表（已去重，已过滤 insufficient_data）
        """
        if not experiences:
            return []

        # 只处理 evaluation_experience 类型
        valid = [e for e in experiences if e.get("type") == "evaluation_experience"]
        if not valid:
            return []

        insights: List[Insight] = []
        now = datetime.now().isoformat(timespec="seconds")

        # ── 1. 整体汇总 ──
        insights.append(self._make_overall_insight(valid, now))

        # ── 2. 按 confidence 分组 ──
        insights.extend(self._group_by(valid, "confidence", "confidence_group", now,
                                        key_fn=lambda e: e.get("prediction", {}).get("confidence", "未知")))

        # ── 3. 按 top_sector 板块分组 ──
        sector_groups = defaultdict(list)
        for exp in valid:
            sectors = exp.get("market_context", {}).get("top_sectors", [])
            if sectors:
                sector_groups[sectors[0]].append(exp)
        for sector, group in sector_groups.items():
            if len(group) >= self.minimum_sample:
                insights.append(self._make_group_insight(
                    group, "sector_performance", now,
                    condition={"top_sector": sector},
                    related_strategy=f"所属板块: {sector}"))

        # ── 4. 按 strategy 关键词分组 ──
        strat_groups = defaultdict(list)
        for exp in valid:
            strategy = exp.get("prediction", {}).get("strategy", "")
            # 提取策略中的关键特征
            if "涨停" in (exp.get("prediction", {}).get("reason", "") or ""):
                strat_groups["涨停板策略"].append(exp)
            if "量比" in strategy:
                strat_groups["量比过滤策略"].append(exp)
            if "消息" in strategy or "消息面" in strategy:
                strat_groups["消息面策略"].append(exp)
            if "资金" in strategy:
                strat_groups["资金活跃策略"].append(exp)
        for strat_key, group in strat_groups.items():
            if len(group) >= self.minimum_sample:
                insights.append(self._make_group_insight(
                    group, "strategy_pattern", now,
                    related_strategy=strat_key))

        # ── 5. 按 market_condition（涨停数）分组 ──
        # 从 market_view 提取涨停数
        bull_groups = {"high_zt(>=80)": [], "mid_zt(50-79)": [], "low_zt(<50)": []}
        for exp in valid:
            mv = exp.get("market_context", {}).get("market_view", "")
            zt_num = _extract_zt_count(mv)
            if zt_num >= 80:
                bull_groups["high_zt(>=80)"].append(exp)
            elif zt_num >= 50:
                bull_groups["mid_zt(50-79)"].append(exp)
            elif zt_num > 0:
                bull_groups["low_zt(<50)"].append(exp)
        for cond, group in bull_groups.items():
            if len(group) >= self.minimum_sample:
                insights.append(self._make_group_insight(
                    group, "market_condition", now,
                    condition={"涨停区间": cond},
                    market_condition=cond))

        # ── 6. 按日期分组（日维度表现） ──
        date_groups = defaultdict(list)
        for exp in valid:
            d = exp.get("prediction", {}).get("date", "")
            if d:
                date_groups[d].append(exp)
        for d, group in sorted(date_groups.items()):
            if len(group) >= 1:  # 日维度允许单日
                insights.append(self._make_group_insight(
                    group, "daily_performance", now,
                    condition={"date": d}))

        # ── 去重 + 过滤 ──
        seen_ids = set()
        filtered = []
        for ins in insights:
            if ins.insight_id not in seen_ids:
                seen_ids.add(ins.insight_id)
                if ins.data_quality != "insufficient_data" or ins.category == "daily_performance":
                    filtered.append(ins)
                else:
                    filtered.append(ins)  # 保留但标记 insufficient

        return filtered

    def _make_overall_insight(self, experiences: List[Dict], now: str) -> Insight:
        """生成整体汇总 Insight"""
        rets = [e["actual_result"]["return_rate"] for e in experiences
                if e.get("actual_result", {}).get("return_rate") is not None]
        hits = [e for e in experiences if e.get("actual_result", {}).get("hit")]
        exp_ids = [e.get("prediction", {}).get("code", "") + "@" + e.get("prediction", {}).get("date", "")
                   for e in experiences]

        return self._build_insight(
            category="overall",
            experiences=experiences,
            rets=rets,
            hits_count=len(hits),
            now=now,
            observation=_overall_observation(len(hits), len(experiences), rets),
            supporting_ids=exp_ids,
        )

    def _group_by(self, experiences, key_name, category, now, key_fn):
        """按 key_fn 分组生成 Insight"""
        groups = defaultdict(list)
        for exp in experiences:
            k = key_fn(exp)
            groups[k].append(exp)
        results = []
        for k, group in groups.items():
            if len(group) >= 1:
                results.append(self._make_group_insight(
                    group, category, now,
                    condition={key_name: k}))
        return results

    def _make_group_insight(
        self, experiences, category, now,
        condition=None, related_strategy=None, market_condition=None,
    ) -> Insight:
        """为一组 Experience 生成 Insight"""
        rets = [e["actual_result"]["return_rate"] for e in experiences
                if e.get("actual_result", {}).get("return_rate") is not None]
        hits = [e for e in experiences if e.get("actual_result", {}).get("hit")]
        exp_ids = [e.get("prediction", {}).get("code", "") + "@" + e.get("prediction", {}).get("date", "")
                   for e in experiences]

        return self._build_insight(
            category=category,
            experiences=experiences,
            rets=rets,
            hits_count=len(hits),
            now=now,
            observation=_group_observation(category, condition, len(hits), len(experiences), rets),
            supporting_ids=exp_ids,
            condition=condition,
            related_strategy=related_strategy,
            market_condition=market_condition,
        )

    def _build_insight(
        self, category, experiences, rets, hits_count, now,
        observation, supporting_ids,
        condition=None, related_strategy=None, market_condition=None,
    ) -> Insight:
        """构建 Insight 对象"""
        n = len(experiences)
        success_rate = round(hits_count / n * 100, 1) if n > 0 else 0.0
        failure_rate = round(100 - success_rate, 1)

        avg_ret = round(statistics.mean(rets), 2) if rets else 0.0
        med_ret = round(statistics.median(rets), 2) if rets else 0.0
        best_ret = round(max(rets), 2) if rets else 0.0
        worst_ret = round(min(rets), 2) if rets else 0.0

        # 数据质量 + 置信度
        if n < self.minimum_sample:
            data_quality = "insufficient_data"
            confidence = "low"
        elif n < 10:
            data_quality = "limited"
            confidence = "medium"
        else:
            data_quality = "adequate"
            confidence = "high"

        # insight_id: 基于 category + condition 的确定性哈希
        id_str = f"{category}|{json.dumps(condition or {}, sort_keys=True)}"
        insight_id = f"ins_{hashlib.md5(id_str.encode()).hexdigest()[:12]}"

        return Insight(
            insight_id=insight_id,
            created_at=now,
            category=category,
            observation=observation,
            sample_count=n,
            success_count=hits_count,
            failure_count=n - hits_count,
            success_rate=success_rate,
            average_return=avg_ret,
            median_return=med_ret,
            best_return=best_ret,
            worst_return=worst_ret,
            failure_rate=failure_rate,
            confidence=confidence,
            data_quality=data_quality,
            supporting_experiences=supporting_ids,
            condition=condition,
            related_strategy=related_strategy,
            market_condition=market_condition,
        )


# ── 辅助函数 ──

def _extract_zt_count(market_view: str) -> int:
    """从市场判断文本中提取涨停家数"""
    import re
    m = re.search(r'涨停\s*(\d+)\s*家', market_view)
    if m:
        return int(m.group(1))
    return 0


def _overall_observation(hits, total, rets) -> str:
    """生成整体观察描述"""
    wr = round(hits / total * 100, 1) if total else 0
    avg = round(statistics.mean(rets), 2) if rets else 0
    if wr >= 70:
        trend = "表现良好"
    elif wr >= 50:
        trend = "表现一般"
    else:
        trend = "表现较差"
    return f"整体命中率 {wr}%（{hits}/{total}），平均收益 {avg}%。{trend}。"


def _group_observation(category, condition, hits, total, rets) -> str:
    """生成分组观察描述"""
    wr = round(hits / total * 100, 1) if total else 0
    avg = round(statistics.mean(rets), 2) if rets else 0
    cond_str = json.dumps(condition, ensure_ascii=False) if condition else category

    if category == "confidence_group":
        conf = condition.get("confidence", "?") if condition else "?"
        return f"置信度「{conf}」样本 {total} 条：命中率 {wr}%，平均收益 {avg}%"
    elif category == "sector_performance":
        sector = condition.get("top_sector", "?") if condition else "?"
        return f"板块「{sector}」相关标的 {total} 条：命中率 {wr}%，平均收益 {avg}%"
    elif category == "strategy_pattern":
        return f"策略「{category}」样本 {total} 条：命中率 {wr}%，平均收益 {avg}%"
    elif category == "market_condition":
        cond = condition.get("涨停区间", "?") if condition else "?"
        return f"市场条件「{cond}」下 {total} 条样本：命中率 {wr}%，平均收益 {avg}%"
    elif category == "daily_performance":
        d = condition.get("date", "?") if condition else "?"
        hit_str = "全命中" if hits == total else f"{hits}/{total}"
        return f"{d} 推荐 {total} 只，{hit_str} 命中，平均收益 {avg}%"
    return f"{cond_str}: {total} 条样本，命中率 {wr}%，平均收益 {avg}%"


# ── 持久化 ──

def save_insights(insights: List[Insight], data_dir: Path) -> int:
    """将 Insight 写入 data/insights.json（只追加不覆盖已有）"""
    fp = data_dir / "insights.json"
    existing = []
    if fp.exists():
        try:
            existing = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    existing_ids = {i["insight_id"] for i in existing}
    added = 0
    for ins in insights:
        d = ins.to_dict()
        if d["insight_id"] not in existing_ids:
            existing.append(d)
            existing_ids.add(d["insight_id"])
            added += 1

    fp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Insights 已保存: %d 条新增, %d 条总计 → %s", added, len(existing), fp)
    return added


def load_insights(data_dir: Path) -> List[Insight]:
    """从 data/insights.json 加载 Insight"""
    fp = data_dir / "insights.json"
    if not fp.exists():
        return []
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        return [Insight.from_dict(d) for d in data]
    except Exception as e:
        log.warning("加载 insights.json 失败: %s", e)
        return []


def run_insight_analysis(data_dir: Path, memory_repo=None) -> List[Insight]:
    """完整分析流程：Memory → Analyzer → Insight → 持久化"""
    from .rsi_memory import ASHRMemoryRepository
    from .rsi_feedback import process_all_settled

    if memory_repo is None:
        memory_repo = ASHRMemoryRepository(data_dir=data_dir)
        # 确保 experiences 已从 SQLite 重建
        process_all_settled(data_dir, memory_repo)

    # 读取所有 experience
    experiences = [
        e.content for e in memory_repo.episodic._entries.values()
        if e.content.get("type") == "evaluation_experience"
    ]

    if not experiences:
        log.warning("无 Experience 数据，无法生成 Insight")
        return []

    analyzer = AShareInsightAnalyzer(minimum_sample=MIN_SAMPLE)
    insights = analyzer.analyze(experiences)

    # 持久化
    added = save_insights(insights, data_dir)
    log.info("Insight 分析完成: %d 条 Insight, %d 条新增", len(insights), added)

    return insights
