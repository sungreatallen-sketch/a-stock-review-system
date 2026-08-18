# RSI Candidate Strategy 实现文档

> 版本：Phase 4 ｜ 日期：2026-08-18 ｜ 状态：已完成

---

## 1. Insight → Candidate 规则

`AShareCandidateGenerator.generate(insights)` 对每条 Insight 按类型生成 Candidate：

| Insight 类型 | 生成条件 | Candidate 假设 |
|---|---|---|
| `confidence_group` | 低置信度 wr<50% | 过滤低置信度推荐 |
| `confidence_group` | 高置信度 wr<60% | 降低置信度权重 |
| `sector_performance` | 板块 wr>=70% | 板块加分 |
| `sector_performance` | 板块 wr<40% | 板块减分 |
| `market_condition` | 涨停>=80家 wr<50% | 情绪过热减少推荐 |
| `market_condition` | 涨停50-79家 wr>=60% | 维持标准策略 |
| `strategy_pattern` | 量比策略 avg>3% | 放宽量比阈值 |

**跳过条件**：`data_quality == "insufficient_data"` 的 Insight 不生成 Candidate。

## 2. Candidate 数据结构

```json
{
    "candidate_id": "cand_e501363b5722",
    "created_at": "2026-08-18T15:30:00",
    "parent_strategy": "ashare_scoring_v1",
    "parent_version": "v1.0",
    "version": "v1.0-candidate-reduce-hot-market",
    "hypothesis": "涨停家数>=80时命中率仅33.3%，情绪过热，减少推荐数量",
    "parameters": {
        "MAX_VOL_RATIO": 2.0,
        "TOP_PRE": 10,
        "top_n": 3,
        "index_filter": null,
        "high_zt_reduce_top_n": 2
    },
    "source_insights": ["ins_abc123"],
    "status": "candidate",
    "backtest_result": null,
    "benchmark_result": null,
    "metadata": {"generator": "AShareCandidateGenerator", "discovery_ratio": 0.6}
}
```

## 3. Strategy Version

```
Production:  v1.0  (MAX_VOL_RATIO=2.0, TOP_PRE=10, top_n=3)
Candidate:   v1.0-candidate-<suffix>
```

Candidate 始终保留 `parent_version = "v1.0"`，绝不覆盖 Production。

## 4. Backtest 接入方式

复用现有 `app/predict/backtest.py` 的 `Backtest.run()`：

```python
# Production Backtest
bt.run(end_date, days=60, top_n=3)  # 默认策略

# Candidate Backtest
bt.run(end_date, days=60, top_n=custom_top_n, index_filter=custom_index_filter,
       strategy=custom_strategy)  # 自定义策略
```

Candidate 通过注入自定义 `Strategy` 对象修改参数（如 `MAX_VOL_RATIO`），不修改 Production 代码。

## 5. Benchmark 方法

`benchmark_candidate(candidate_result, production_result)`:

| 指标 | 说明 |
|---|---|
| win_rate | 命中率 |
| avg_return | 平均收益 |
| median_return | 中位收益 |
| best_ret | 最大单笔收益 |
| worst_ret | 最大单笔亏损 |
| max_drawdown | 最大回撤 |
| excess_return | Candidate vs Production 超额收益 |

**判断标准**：`candidate_better = (c_avg > p_avg) and (c_wr >= p_wr - 10)`

**三层对比**：
1. Candidate vs Production（必须）
2. Candidate vs Baseline（可选）
3. Candidate vs Discovery Period 自身（隐含在 validation 分离中）

## 6. 数据泄漏控制

```
总回测窗口: 60 天
├── Discovery Period: 前 60% (36 天) — 不用于评估
└── Validation Period: 后 40% (24 天) — 仅此部分用于 Benchmark

原则：
  - Insight 来自 Phase 3 的历史分析
  - Backtest 使用 validation_period 的结果
  - Discovery/Validation 无时间重叠
```

## 7. 样本门槛

| 条件 | 处理 |
|---|---|
| Insight `data_quality == "insufficient_data"` | 不生成 Candidate |
| Benchmark `count < MIN_TRADES(10)` | `candidate_better = None`, `confidence = "insufficient_data"` |
| Benchmark `count < 15` | `confidence = "low"` |
| Benchmark `count 15-29` | `confidence = "medium"` |
| Benchmark `count >= 30` | `confidence = "high"` |

## 8. Candidate 状态

```
candidate      → 初始状态（刚从 Insight 生成）
backtested     → 已完成回测
validated      → 通过 Benchmark 验证
insufficient_data → 样本不足
rejected       → Benchmark 不通过

暂不实现: promoted（禁止自动上线）
```

## 9. 测试结果

| 测试项 | 结果 |
|---|---|
| Test 1: Insight → Candidate | ✅ 2 个 Candidate 生成 |
| Test 2: 保留 parent strategy | ✅ parent_version="v1.0" |
| Test 3: 不修改 Production | ✅ MAX_VOL_RATIO=2.0, TOP_PRE=10 不变 |
| Test 4: Candidate Backtest | ✅ 含 validation_period |
| Test 5: Production Backtest | ✅ 同口径 |
| Test 6: Benchmark | ✅ candidate_better=True, improvements 3 项, excess=+0.8% |
| Test 7: 样本不足 | ✅ candidate_better=None, confidence=insufficient_data |
| Test 8: 数据周期 | ✅ DISCOVERY_RATIO=0.6, 无重叠 |
| Test 9: 去重 | ✅ 重复生成 ID 一致, 持久化去重 |
| Test 10: Legacy Flow | ✅ 8/8 |
| Test 11: Phase 2 | ✅ |
| Test 12: Phase 3 | ✅ |

**生成的 Candidate（基于当前 9 条 Experience）**：
```
cand_e501363b5722 | v1.0-candidate-reduce-hot-market
  涨停>=80家时命中率仅33.3%，减少推荐 top_n=2

cand_b00cbdcd7a6f | v1.0-candidate-standard-mid-market
  涨停50-79家时命中率66.7%，维持标准策略
```

## 10. 当前限制

| 限制 | 说明 | 未来改进 |
|---|---|---|
| 真实回测未执行 | 测试用 mock backtest（需要 MCP 连接） | 连接 MCP 后运行真实回测 |
| Candidate 参数有限 | 目前只支持 MAX_VOL_RATIO/TOP_PRE/top_n/index_filter | 扩展评分权重参数 |
| 无 OOS 真实验证 | validation_period 基于时间分割 | 未来可用 walk-forward 验证 |
| 样本量小 | 9 条 Experience → 2 个 Candidate | 积累更多数据 |
| 不支持评分权重调整 | scoring.py 权重硬编码 | 需要重构为可注入权重 |

## 文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/rsi_candidate.py` | **新增** | Candidate 生成/回测/基准/持久化 |
| `candidate/` | **新增目录** | Candidate JSON 存储目录 |
