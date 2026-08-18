"""RSI Memory Adapter — 将现有数据存储包装为 RSI Memory 接口

第一阶段：从 recommendation_history.json 初始化内存 Memory
约束：
  - 不修改/覆盖原始 recommendation_history.json
  - 不丢失历史数据
  - 保留未来持久化扩展能力（预留 SQLite Adapter 接口）
  - 不重构现有 Tracker
"""
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from rsi_framework.memory.base import Memory, MemoryEntry, MemoryQuery, MemoryType
from rsi_framework.memory.repository import MemoryRepository

log = logging.getLogger("rsi_memory")


class ASHRMemoryBackend(Memory):
    """A 股项目 Episodic Memory 后端 — 基于内存，从 JSON 文件初始化

    特点：
      - 只读加载 recommendation_history.json（不修改原文件）
      - 运行期间新增的 memory 仅在内存中
      - 预留 flush_to_disk() 接口供未来持久化
    """

    def __init__(self, memory_type: MemoryType = MemoryType.EPISODIC):
        self._type = memory_type
        self._entries: Dict[str, MemoryEntry] = {}
        self._loaded = False

    @property
    def memory_type(self) -> MemoryType:
        return self._type

    def _ensure_loaded(self):
        """懒加载：首次访问时从 recommendation_history.json 读取"""
        if self._loaded:
            return
        self._loaded = True

        # 只读加载，不修改原文件
        from .config import paths
        history_path = paths()["data"] / "recommendation_history.json"
        if not history_path.exists():
            log.info("recommendation_history.json 不存在，Memory 从空开始")
            return

        try:
            records = json.loads(history_path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                return

            for rec in records:
                entry_id = f"hist_{rec.get('date', '')}_{rec.get('code', '')}"
                entry = MemoryEntry(
                    id=entry_id,
                    memory_type=self._type,
                    content=rec,
                    metadata={"source": "recommendation_history.json", "readonly": True},
                    timestamp=datetime.utcnow(),
                    relevance_score=0.5,
                )
                self._entries[entry_id] = entry

            log.info("Memory 从 recommendation_history.json 加载 %d 条记录", len(self._entries))
        except Exception as e:
            log.warning("Memory 加载 recommendation_history.json 失败: %s", e)

    async def store(self, entry: MemoryEntry) -> bool:
        self._ensure_loaded()
        self._entries[entry.id] = entry
        return True

    async def retrieve(self, query: MemoryQuery) -> List[MemoryEntry]:
        self._ensure_loaded()
        results = list(self._entries.values())

        # 简单过滤
        if query.filters:
            filtered = []
            for entry in results:
                match = True
                for k, v in query.filters.items():
                    if entry.content.get(k) != v:
                        match = False
                        break
                if match:
                    filtered.append(entry)
            results = filtered

        # 按相关性排序
        results.sort(key=lambda e: e.relevance_score, reverse=True)
        return results[:query.limit]

    async def get(self, entry_id: str) -> Optional[MemoryEntry]:
        self._ensure_loaded()
        return self._entries.get(entry_id)

    async def update(self, entry_id: str, updates: Dict[str, Any]) -> bool:
        self._ensure_loaded()
        if entry_id not in self._entries:
            return False
        entry = self._entries[entry_id]
        entry.content.update(updates)
        return True

    async def delete(self, entry_id: str) -> bool:
        self._ensure_loaded()
        return self._entries.pop(entry_id, None) is not None

    async def list(self, limit: int = 100, offset: int = 0) -> List[MemoryEntry]:
        self._ensure_loaded()
        entries = list(self._entries.values())
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return entries[offset:offset + limit]

    async def count(self) -> int:
        self._ensure_loaded()
        return len(self._entries)

    def get_statistics(self) -> Dict[str, Any]:
        """获取 Memory 统计信息"""
        self._ensure_loaded()
        return {
            "total": len(self._entries),
            "memory_type": self._type.value,
            "source": "recommendation_history.json",
        }


class ASHRMemoryRepository(MemoryRepository):
    """A 股项目 Memory 仓库 — 用自定义后端替换默认内存实现

    预留扩展点：
      - Phase 2 可添加 SQLite 持久化后端
      - 不修改 MemoryRepository 基类
    """

    def __init__(self, data_dir: Optional[Path] = None):
        # 不调用 super().__init__()，自定义初始化
        self._memories: Dict[MemoryType, Memory] = {
            MemoryType.EPISODIC: ASHRMemoryBackend(MemoryType.EPISODIC),
            MemoryType.SEMANTIC: ASHRMemoryBackend(MemoryType.SEMANTIC),
            MemoryType.PROCEDURAL: ASHRMemoryBackend(MemoryType.PROCEDURAL),
            MemoryType.META: ASHRMemoryBackend(MemoryType.META),
        }
        self._data_dir = data_dir

    @property
    def episodic(self) -> ASHRMemoryBackend:
        return self._memories[MemoryType.EPISODIC]

    @property
    def semantic(self) -> ASHRMemoryBackend:
        return self._memories[MemoryType.SEMANTIC]

    @property
    def procedural(self) -> ASHRMemoryBackend:
        return self._memories[MemoryType.PROCEDURAL]

    @property
    def meta(self) -> ASHRMemoryBackend:
        return self._memories[MemoryType.META]

    async def get_statistics(self) -> Dict[str, Any]:
        stats = {}
        for mt, mem in self._memories.items():
            if isinstance(mem, ASHRMemoryBackend):
                stats[mt.value] = mem.get_statistics()
            else:
                count = await mem.count()
                stats[mt.value] = {"total": count}
        return {
            "total_memories": sum(s.get("total", 0) for s in stats.values()),
            "by_type": stats,
        }
