"""RSI Feedback Bridge — Tracker.settle → Evaluator → Memory 闭环

职责：
  1. settle 后自动触发 Evaluation
  2. Evaluation 结果写入 Memory（去重）
  3. 发布 RSI EventBus 事件

不修改：
  - Tracker / workflow / predict 等现有模块
  - RSI Framework Core

触发方式：
  - settle_feedback() 在 Tracker.settle_pending() 之后被 rsi_app 调用
  - 也可以手动调用 process_all_settled() 批量处理历史数据
"""
import logging
from typing import Any, Dict, List, Optional
from pathlib import Path

log = logging.getLogger("rsi_feedback")


def settle_feedback(
    pred_date: str,
    data_dir: Path,
    memory_repo=None,
) -> Dict[str, Any]:
    """settle 后触发：对 pred_date 的已结算结果进行 Evaluation → Memory

    Args:
        pred_date: 预测日 T（如 "2026-08-13"）
        data_dir: data 目录路径
        memory_repo: ASHRMemoryRepository 实例（可选，不传则自动创建）

    Returns:
        {"evaluated": int, "stored": int, "skipped": int}
    """
    import sqlite3
    import json

    from .rsi_evaluator import AShareEvaluator
    from .rsi_memory import ASHRMemoryRepository

    if memory_repo is None:
        memory_repo = ASHRMemoryRepository(data_dir=data_dir)

    db_path = data_dir / "a_share.db"
    conn = sqlite3.connect(db_path)

    # 获取该日已结算结果
    rows = conn.execute(
        "SELECT date, target_code, target_name, buy_price, sell_close, ret_close "
        "FROM prediction_results WHERE date=? AND status='settled'",
        (pred_date,)
    ).fetchall()

    if not rows:
        conn.close()
        return {"evaluated": 0, "stored": 0, "skipped": 0, "note": f"无 {pred_date} 已结算记录"}

    # 获取原始预测上下文
    pred_row = conn.execute("SELECT targets FROM predictions WHERE date=?", (pred_date,)).fetchone()
    conn.close()

    pred_context = {}
    if pred_row:
        try:
            pred_data = json.loads(pred_row[0])
            for t in pred_data.get("targets", []):
                code = (t.get("code") or "").split(".")[0]
                pred_context[code] = {
                    "strategy": pred_data.get("strategy", ""),
                    "market_view": pred_data.get("market_view", ""),
                    "top_sectors": pred_data.get("top_sectors", []),
                    "reason": t.get("reason", ""),
                    "risk": t.get("risk", ""),
                    "confidence": t.get("confidence", ""),
                }
        except Exception:
            pass

    evaluator = AShareEvaluator()
    stored = 0
    skipped = 0

    for row in rows:
        date, code, name, buy, sell_close, ret_close = row
        ctx = pred_context.get(code, {})

        eval_result = evaluator.evaluate_prediction(
            pred_date=date,
            target_code=code,
            target_name=name,
            buy_price=buy or 0,
            sell_close=sell_close or 0,
            ret_close=ret_close or 0,
            strategy=ctx.get("strategy", ""),
            reason=ctx.get("reason", ""),
            risk=ctx.get("risk", ""),
            market_view=ctx.get("market_view", ""),
            confidence=ctx.get("confidence", ""),
            top_sectors=ctx.get("top_sectors"),
        )

        # 构造 eval_data 给 Memory
        eval_data = {
            "prediction_id": eval_result.task_id,
            "stock_code": code,
            "stock_name": name,
            "prediction_date": date,
            "evaluation_date": eval_result.metadata.get("evaluation_date", ""),
            "buy_price": buy,
            "sell_close": sell_close,
            "return_rate": ret_close,
            "hit": (ret_close or 0) > 0,
            "score": eval_result.score,
            "strategy": ctx.get("strategy", ""),
            "reason": ctx.get("reason", ""),
            "risk": ctx.get("risk", ""),
            "market_view": ctx.get("market_view", ""),
            "confidence": ctx.get("confidence", ""),
            "top_sectors": ctx.get("top_sectors", []),
        }

        is_new = memory_repo.episodic.store_experience(eval_data)
        if is_new:
            stored += 1
        else:
            skipped += 1

    log.info("Feedback %s: evaluated=%d, stored=%d, skipped=%d",
             pred_date, len(rows), stored, skipped)

    return {"evaluated": len(rows), "stored": stored, "skipped": skipped}


def process_all_settled(data_dir: Path, memory_repo=None) -> Dict[str, Any]:
    """批量处理所有已结算记录 → Evaluation → Memory

    用于首次初始化或补建历史 Experience。
    """
    import sqlite3

    if memory_repo is None:
        from .rsi_memory import ASHRMemoryRepository
        memory_repo = ASHRMemoryRepository(data_dir=data_dir)

    db_path = data_dir / "a_share.db"
    conn = sqlite3.connect(db_path)
    dates = conn.execute(
        "SELECT DISTINCT date FROM prediction_results WHERE status='settled' ORDER BY date"
    ).fetchall()
    conn.close()

    total_evaluated = 0
    total_stored = 0
    total_skipped = 0

    for (pred_date,) in dates:
        result = settle_feedback(pred_date, data_dir, memory_repo)
        total_evaluated += result.get("evaluated", 0)
        total_stored += result.get("stored", 0)
        total_skipped += result.get("skipped", 0)

    log.info("批量处理完成: %d 日, %d 条评估, %d 条新写入, %d 条跳过",
             len(dates), total_evaluated, total_stored, total_skipped)

    return {
        "days": len(dates),
        "evaluated": total_evaluated,
        "stored": total_stored,
        "skipped": total_skipped,
    }
