"""RSI Memory Adapter — Prediction + Evaluation → Experience → Memory

Phase 2: 将 EvaluationResult 写入 RSI Memory，形成 Experience

Experience 结构：
  - prediction（原始预测信息）
  - evaluation（评估结果）
  - actual_result（真实市场结果）
  - market_context（市场环境）

约束：
  - 不修改/覆盖原始 recommendation_history.json
  - 不修改 prediction_results 表
  - 去重：同一 prediction_id 不重复写入
  - 第一阶段内存存储，预留 SQLite 持久化扩展
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from rsi_framework.memory.base import Memory, MemoryEntry, MemoryQuery, MemoryType

log = logging.getLogger("rsi_memory")


class ASHRMemoryBackend(Memory):
    """A 股 Episodic Memory — 存储 Prediction+Evaluation Experience

    特点：
      - 从 recommendation_history.json 只读初始化历史数据
      - 运行期间新增 EvaluationResult 作为 Experience 写入
      - 按 prediction_id 去重
      - 不修改原始文件
    """

    def __init__(self, memory_type: MemoryType = MemoryType.EPISODIC, data_dir: Optional[Path] = None):
        self._type = memory_type
        self._entries: Dict[str, MemoryEntry] = {}
        self._loaded = False
        self._data_dir = data_dir

    @property
    def memory_type(self) -> MemoryType:
        return self._type

    def _ensure_loaded(self):
        """懒加载：从 recommendation_history.json 初始化历史"""
        if self._loaded:
            return
        self._loaded = True

        if not self._data_dir:
            return

        history_path = self._data_dir / "recommendation_history.json"
        if not history_path.exists():
            log.info("recommendation_history.json 不存在，Memory 从空开始")
            return

        try:
            data = json.loads(history_path.read_text(encoding="utf-8"))
            records = data.get("records", [])
            for rec in records:
                entry_id = f"hist_{rec.get('date', '')}_{rec.get('code', '')}"
                # 跳过已通过 EvaluationResult 写入的记录
                if entry_id in self._entries:
                    continue
                entry = MemoryEntry(
                    id=entry_id,
                    memory_type=self._type,
                    content={
                        "type": "historical_record",
                        "prediction": {
                            "date": rec.get("date"),
                            "code": rec.get("code"),
                            "name": rec.get("name"),
                            "buy_price": rec.get("buy"),
                        },
                        "actual_result": {
                            "sell_close": rec.get("sell_close"),
                            "return_rate": rec.get("ret"),
                            "hit": (rec.get("ret") or 0) > 0,
                        },
                    },
                    metadata={"source": "recommendation_history.json", "readonly": True},
                    relevance_score=0.3,
                )
                self._entries[entry_id] = entry
            log.info("Memory 从 recommendation_history.json 加载 %d 条历史记录", len(self._entries))
        except Exception as e:
            log.warning("Memory 加载历史数据失败: %s", e)

    def store_experience(self, eval_data: Dict[str, Any]) -> bool:
        """存储一条 Prediction+Evaluation Experience

        Args:
            eval_data: 来自 evaluate_settled_predictions() 的单条结果

        Returns:
            True 如果新写入，False 如果已存在（去重）
        """
        self._ensure_loaded()
        pred_id = eval_data.get("prediction_id", "")
        if not pred_id:
            return False

        # 去重：已存在则跳过
        if pred_id in self._entries:
            return False

        entry = MemoryEntry(
            id=pred_id,
            memory_type=self._type,
            content={
                "type": "evaluation_experience",
                "prediction": {
                    "date": eval_data.get("prediction_date"),
                    "code": eval_data.get("stock_code"),
                    "name": eval_data.get("stock_name"),
                    "buy_price": eval_data.get("buy_price"),
                    "strategy": eval_data.get("strategy", ""),
                    "reason": eval_data.get("reason", ""),
                    "risk": eval_data.get("risk", ""),
                    "confidence": eval_data.get("confidence", ""),
                },
                "market_context": {
                    "market_view": eval_data.get("market_view", ""),
                    "top_sectors": eval_data.get("top_sectors", []),
                },
                "evaluation": {
                    "score": eval_data.get("score", 0),
                    "hit": eval_data.get("hit", False),
                    "return_rate": eval_data.get("return_rate", 0),
                },
                "actual_result": {
                    "sell_close": eval_data.get("sell_close"),
                    "return_rate": eval_data.get("return_rate"),
                    "hit": eval_data.get("hit", False),
                    "evaluation_date": eval_data.get("evaluation_date"),
                },
            },
            metadata={
                "source": "rsi_evaluator",
                "version": "2.0",
            },
            relevance_score=0.8 if eval_data.get("hit") else 0.4,
        )

        self._entries[pred_id] = entry
        return True

    async def store(self, entry: MemoryEntry) -> bool:
        self._ensure_loaded()
        self._entries[entry.id] = entry
        return True

    async def retrieve(self, query: MemoryQuery) -> List[MemoryEntry]:
        self._ensure_loaded()
        results = list(self._entries.values())

        if query.filters:
            filtered = []
            for entry in results:
                match = True
                for k, v in query.filters.items():
                    # 支持嵌套查询
                    val = _deep_get(entry.content, k)
                    if val != v:
                        match = False
                        break
                if match:
                    filtered.append(entry)
            results = filtered

        results.sort(key=lambda e: e.relevance_score, reverse=True)
        return results[:query.limit]

    async def get(self, entry_id: str) -> Optional[MemoryEntry]:
        self._ensure_loaded()
        return self._entries.get(entry_id)

    async def update(self, entry_id: str, updates: Dict[str, Any]) -> bool:
        self._ensure_loaded()
        if entry_id not in self._entries:
            return False
        self._entries[entry_id].content.update(updates)
        return True

    async def delete(self, entry_id: str) -> bool:
        self._ensure_loaded()
        return self._entries.pop(entry_id, None) is not None

    async def list(self, limit: int = 100, offset: int = 0) -> List[MemoryEntry]:
        self._ensure_loaded()
        entries = sorted(self._entries.values(), key=lambda e: e.timestamp, reverse=True)
        return entries[offset:offset + limit]

    async def count(self) -> int:
        self._ensure_loaded()
        return len(self._entries)

    def get_statistics(self) -> Dict[str, Any]:
        self._ensure_loaded()
        experiences = [e for e in self._entries.values()
                       if e.content.get("type") == "evaluation_experience"]
        historical = [e for e in self._entries.values()
                      if e.content.get("type") == "historical_record"]
        return {
            "total": len(self._entries),
            "experiences": len(experiences),
            "historical_records": len(historical),
            "memory_type": self._type.value,
        }


class ASHRMemoryRepository:
    """A 股 Memory 仓库 — 管理四种记忆类型

    Phase 2: EpisodicMemory 存储 Experience
    预留: SemanticMemory / ProceduralMemory / MetaMemory 供未来使用
    """

    def __init__(self, data_dir: Optional[Path] = None):
        self._data_dir = data_dir
        self._memories: Dict[MemoryType, Memory] = {
            MemoryType.EPISODIC: ASHRMemoryBackend(MemoryType.EPISODIC, data_dir),
            MemoryType.SEMANTIC: ASHRMemoryBackend(MemoryType.SEMANTIC, data_dir),
            MemoryType.PROCEDURAL: ASHRMemoryBackend(MemoryType.PROCEDURAL, data_dir),
            MemoryType.META: ASHRMemoryBackend(MemoryType.META, data_dir),
        }

    @property
    def episodic(self) -> ASHRMemoryBackend:
        return self._memories[MemoryType.EPISODIC]

    def get_memory(self, memory_type: MemoryType) -> Memory:
        return self._memories[memory_type]

    async def store(self, memory_type: MemoryType, entry: MemoryEntry) -> bool:
        return await self._memories[memory_type].store(entry)

    async def retrieve(self, memory_type: MemoryType, query: MemoryQuery) -> List[MemoryEntry]:
        return await self._memories[memory_type].retrieve(query)

    async def search_all(self, query: MemoryQuery) -> Dict[MemoryType, List[MemoryEntry]]:
        results = {}
        for mt, mem in self._memories.items():
            entries = await mem.retrieve(query)
            if entries:
                results[mt] = entries
        return results

    async def get_statistics(self) -> Dict[str, Any]:
        stats = {}
        for mt, mem in self._memories.items():
            if isinstance(mem, ASHRMemoryBackend):
                stats[mt.value] = mem.get_statistics()
            else:
                count = await mem.count()
                stats[mt.value] = {"total": count}
        return {"total_memories": sum(s.get("total", 0) for s in stats.values()), "by_type": stats}

    async def clear_all(self) -> bool:
        for mem in self._memories.values():
            await mem.clear()
        return True


def _deep_get(d: dict, key: str) -> Any:
    """支持嵌套键查询，如 'evaluation.hit'"""
    parts = key.split(".")
    for p in parts:
        if isinstance(d, dict):
            d = d.get(p)
        else:
            return None
    return d
