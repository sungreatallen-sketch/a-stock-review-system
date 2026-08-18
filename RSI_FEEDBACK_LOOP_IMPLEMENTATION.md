# RSI Feedback Loop 实现文档

> 版本：Phase 2 ｜ 日期：2026-08-18 ｜ 状态：已完成

---

## 1. Prediction 数据结构

来源：`predictions` 表（SQLite），存储为 JSON 字符串。

```json
{
  "date": "2026-08-13",                         // 预测日 T
  "strategy": "7-10日强势板块+量比+消息面+LLM研判",
  "market_view": "指数小幅回调，医疗板块走强",
  "top_sectors": ["白银", "贵金属"],
  "targets": [
    {
      "code": "300143",
      "name": "盈康生命",
      "reason": "评分72.4为候选最高...",
      "risk": "当日涨幅较大，次日可能冲高回落",
      "confidence": "中",
      "sentiment_score": 2,
      "参考买入价(收盘)": 7.92,
      "量比": 1.7,
      "stop_loss": "买入价-5%",
      "sell_target": "买入价+3%"
    }
  ]
}
```

## 2. Settlement 数据结构

来源：`prediction_results` 表（SQLite），由 `Tracker.settle()` 写入。

```python
{
    "date": "2026-08-13",           # 预测日 T
    "target_code": "300143",
    "target_name": "盈康生命",
    "buy_price": 7.92,              # T 日收盘价（昨收 = 买入价）
    "sell_price": 8.4,              # T+1 收盘价（同 sell_close）
    "sell_close": 8.4,              # T+1 收盘价（主口径）
    "ret": 6.06,                    # 收益率 %（同 ret_close）
    "ret_close": 6.06,              # 收益率 %（昨收→今收）
    "status": "settled",
    "created_at": "2026-08-14T15:30:06"
}
```

## 3. EvaluationResult 数据结构

由 `AShareEvaluator.evaluate_prediction()` 生成，符合 RSI Framework `EvaluationResult` 接口。

```python
EvaluationResult(
    task_id="pred_2026-08-13_300143",    # prediction_id
    execution_id="eval_pred_2026-08-13_300143",
    success=True,
    score=0.75,                          # 0-1 综合得分
    metrics={
        "hit": 1.0,                      # 命中=1.0, 未命中=0.0
        "return_rate": 6.06,             # 收益率 %
        "buy_price": 7.92,               # T 日收盘
        "sell_close": 8.4,               # T+1 收盘
        "correctness": 1.0,              # 命中指标
        "quality": 0.75,                 # 质量指标
    },
    failures=[],                         # 未命中时: [{"type": "miss", "return_pct": -1.85}]
    evidence=[
        {"type": "price", "buy": 7.92, "sell": 8.4, "return_pct": 6.06},
        {"type": "strategy", "description": "7-10日强势板块+..."},
        {"type": "reason", "text": "评分72.4为候选最高..."},
        {"type": "risk", "text": "当日涨幅较大..."},
    ],
    evaluator_version="2.0.0",
    metadata={
        "stock_code": "300143",
        "stock_name": "盈康生命",
        "prediction_date": "2026-08-13",
        "evaluation_date": "2026-08-14",    # T+1（跳过周末）
        "confidence": "中",
        "market_view": "指数小幅回调...",
        "top_sectors": ["白银", "贵金属"],
        "口径": "昨收买→今收卖（收盘-收盘）",
    },
)
```

## 4. Prediction → Evaluation 映射

| Prediction 字段 | Settlement 字段 | EvaluationResult 字段 | 说明 |
|---|---|---|---|
| predictions.date | — | task_id, metadata.prediction_date | 预测日 T |
| — | prediction_results.date | 同上 | 一致 |
| targets[].code | target_code | metadata.stock_code | 股票代码 |
| targets[].name | target_name | metadata.stock_name | 股票名称 |
| targets[].参考买入价(收盘) | buy_price | metrics.buy_price | T 日收盘价 |
| — | sell_close | metrics.sell_close | T+1 收盘价 |
| — | ret_close | metrics.return_rate, score | 收益率 |
| — | — | metrics.hit | 推导: ret_close > 0 |
| strategy | — | evidence[1] | 策略描述 |
| targets[].reason | — | evidence[2] | 推荐理由 |
| targets[].risk | — | evidence[3] | 风险提示 |
| market_view | — | metadata.market_view | 市场判断 |
| top_sectors | — | metadata.top_sectors | 强势板块 |
| targets[].confidence | — | metadata.confidence | 置信度 |

## 5. Evaluation → Experience 映射

`EvaluationResult` 转换为 `MemoryEntry` 写入 RSI Memory：

```python
MemoryEntry(
    id="pred_2026-08-13_300143",         # = prediction_id
    memory_type=MemoryType.EPISODIC,
    content={
        "type": "evaluation_experience",
        "prediction": {                   # 原始预测信息
            "date", "code", "name", "buy_price",
            "strategy", "reason", "risk", "confidence"
        },
        "market_context": {               # 市场环境
            "market_view", "top_sectors"
        },
        "evaluation": {                   # 评估结果
            "score", "hit", "return_rate"
        },
        "actual_result": {                # 真实市场结果
            "sell_close", "return_rate", "hit", "evaluation_date"
        },
    },
    metadata={"source": "rsi_evaluator", "version": "2.0"},
    relevance_score=0.8 (hit) / 0.4 (miss),
)
```

## 6. Memory 存储方式

| 层级 | 实现 | 说明 |
|---|---|---|
| 第一阶段（当前） | 内存 Dict + recommendation_history.json 只读初始化 | 运行期间不丢失，重启后从 JSON 重建历史 + SQLite 重建 Experience |
| 第二阶段（预留） | SQLite PersistentMemory | 接口已预留，不修改现有 Tracker |

存储分布：
- `ASHRMemoryBackend._entries`（内存）
  - 来自 `recommendation_history.json`：type="historical_record"，readonly
  - 来自 `rsi_evaluator`：type="evaluation_experience"

持久化保障：
- 原始数据永远在 `prediction_results` 表和 `recommendation_history.json`
- Memory 仅读取，不修改原始文件
- 系统重启后 `process_all_settled()` 可从 SQLite 重建全部 Experience

## 7. 时间边界

```
T日（预测日）:
  可用数据：T日及之前的市场数据
  输出：Prediction（3只标的 + 买卖计划）

T+1日（评估日）:
  可用数据：T+1日开盘/最高/最低/收盘
  操作：Tracker.settle() → prediction_results → AShareEvaluator → Memory

严格约束：
  - T日预测只能使用T日收盘前数据 ✅
  - T+1数据只进入 Evaluation，不进入 Prediction ✅
  - Evaluation 在 settle 之后自动触发 ✅
```

## 8. 去重机制

| 层级 | 去重方式 | 说明 |
|---|---|---|
| prediction_results | `DELETE + INSERT`（settle 函数内） | 同日重复 settle 以最后一次为准 |
| Memory Experience | `prediction_id` 键去重 | `store_experience()` 检查 `_entries` 已存在则返回 False |
| 批量处理 | `process_all_settled()` 自动跳过已存在 | 重复运行 safe |

## 9. Event 触发方式

```
方案：Application Adapter 层触发（不修改 RSI Framework Core）

Tracker.settle_pending()
  → workflow.run_review() 返回 report（含 _settle 信息）
    → rsi_app._trigger_feedback(report)
      → rsi_feedback.settle_feedback(pred_date, data_dir)
        → AShareEvaluator.evaluate_prediction()
          → Memory.store_experience()

触发点：rsi_app.handle_command() 成功返回后
失败处理：catch Exception → log.warning，不阻塞主流程
```

未使用 RSI Framework EventBus（因为 settle 在 workflow 内部同步执行，EventBus 是 async）。
未来如果需要，可在 settle 后异步发布 `EventType.EVALUATION_COMPLETED`。

## 10. 测试结果

| 测试项 | 结果 | 说明 |
|---|---|---|
| Test 1: Prediction → EvaluationResult | ✅ 43/43 | 9 条 prediction_results 全部生成 EvaluationResult |
| Test 2: EvaluationResult → Memory | ✅ | 首次写入成功，包含 prediction/evaluation/actual_result/market_context |
| Test 3: 查询 Memory | ✅ | retrieve 返回结果，含 content 和 relevance_score |
| Test 4: 去重 | ✅ | 重复写入返回 False，条数不变 |
| Test 5: Legacy Flow 回归 | ✅ 8/8 | RSI 开关 false 时完全走原路径 |
| Test 6: RSI Flow 回归 | ✅ | handle_command 正常返回 report dict |
| Test 7: settle_feedback | ✅ | 批量处理 3 日 9 条，stored=8, skipped=1；重复处理 stored=0 |
| Test 8: Memory 统计 | ✅ | total=18 (9 experiences + 9 historical) |

**关键数据验证**：
```
批量处理结果: 3 日, 9 条评估, 8 条新写入, 1 条跳过
Memory 统计: total=18, experiences=9, historical=9
重复处理: stored=0（去重正常）
```

## 文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/rsi_evaluator.py` | 重写 v2 | 单次预测评估 + 批量评估 |
| `app/rsi_memory.py` | 重写 v2 | Experience 存储 + 去重 + 历史加载 |
| `app/rsi_feedback.py` | 新增 | settle → evaluator → memory 桥接 |
| `app/rsi_app.py` | 修改 | 集成 _trigger_feedback |
| `docs/变更与回滚记录.md` | 修改 | C32 记录 |
