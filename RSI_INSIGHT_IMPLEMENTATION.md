# RSI Insight 实现文档

> 版本：Phase 3 ｜ 日期：2026-08-18 ｜ 状态：已完成

---

## 1. Experience 数据结构

来源：RSI Memory（Phase 2 写入），从 SQLite `prediction_results` + `predictions` 重建。

```json
{
  "type": "evaluation_experience",
  "prediction": {
    "date": "2026-08-13",
    "code": "300143",
    "name": "盈康生命",
    "buy_price": 7.92,
    "strategy": "7-10日强势板块 + 个股强势 + 资金活跃 + 量比<2.0过滤 + 消息面 + LLM研判",
    "reason": "评分72.4为候选最高...",
    "risk": "当日涨幅较大...",
    "confidence": "高"
  },
  "market_context": {
    "market_view": "指数小幅回调，医疗板块走强，涨停58家...",
    "top_sectors": ["白银", "贵金属"]
  },
  "evaluation": {
    "score": 1.0,
    "hit": true,
    "return_rate": 6.06
  },
  "actual_result": {
    "sell_close": 8.4,
    "return_rate": 6.06,
    "hit": true,
    "evaluation_date": "2026-08-14"
  }
}
```

当前数据：9 条 Experience，跨越 3 个交易日（2026-08-11~13）。

## 2. Insight 数据结构

```python
{
    "insight_id": "ins_a1b2c3d4e5f6",   # 基于 category+condition 的确定性哈希
    "created_at": "2026-08-18T15:30:00",
    "category": "overall",               # 分析维度
    "observation": "整体命中率 66.7%（6/9），平均收益 2.55%。表现一般。",
    "sample_count": 9,
    "success_count": 6,
    "failure_count": 3,
    "success_rate": 66.7,
    "average_return": 2.55,
    "median_return": 1.42,
    "best_return": 10.01,
    "worst_return": -2.87,
    "failure_rate": 33.3,
    "confidence": "medium",              # low / medium / high
    "data_quality": "limited",           # insufficient_data / limited / adequate
    "supporting_experiences": ["300143@2026-08-13", ...],
    "condition": {},                     # 分组条件（如 {"confidence": "高"}）
    "related_strategy": null,            # 关联策略（如 "量比过滤策略"）
    "market_condition": null             # 市场条件（如 "high_zt(>=80)"）
}
```

## 3. 分析逻辑

`AShareInsightAnalyzer.analyze(experiences)` 按以下维度分组统计：

| 维度 | category | 分组依据 | 说明 |
|---|---|---|---|
| 整体汇总 | `overall` | 全部 Experience | 基准参考 |
| 置信度分组 | `confidence_group` | prediction.confidence | 高/中/低 置信度表现对比 |
| 板块分组 | `sector_performance` | market_context.top_sectors[0] | 首位板块的表现 |
| 策略模式 | `strategy_pattern` | prediction.reason 中的关键词 | 涨停板/量比/消息面/资金活跃 |
| 市场条件 | `market_condition` | market_view 中的涨停家数 | >=80 / 50-79 / <50 三档 |
| 日维度 | `daily_performance` | prediction.date | 每日推荐表现 |

每组生成一个 Insight，包含：命中率、平均收益、中位收益、最佳/最差收益、样本数、置信度。

LLM 不参与分析。所有统计基于 Python `statistics` 模块。

## 4. 样本门槛

| 样本数 | data_quality | confidence | 说明 |
|---|---|---|---|
| < 3 | `insufficient_data` | `low` | 样本不足，不生成高置信 Insight |
| 3-9 | `limited` | `medium` | 有限样本，有一定参考价值 |
| >= 10 | `adequate` | `high` | 样本充分 |

`minimum_sample=3`（可通过参数调整）。
样本不足的 Insight 仍会生成并标记 `insufficient_data`，供人工审查。

## 5. 去重机制

| 层级 | 方式 | 说明 |
|---|---|---|
| Insight ID | `insight_id = md5(category + condition_json)[:12]` | 同一 category+condition 永远产生相同 ID |
| 持久化 | `save_insights()` 检查 `existing_ids` | 重复保存不追加 |
| 重复分析 | `analyzer.analyze()` 返回相同 ID 集合 | 幂等 |

## 6. 数据时间边界

```
严格约束：
  - Insight 只使用已完成 Evaluation 的 Experience ✅
  - 未结算的 Prediction 不参与分析 ✅
  - return_rate=None 的 Experience 不影响收益率统计 ✅
  - 所有 Experience 来自 Phase 2 的 Evaluator 输出 ✅

验证：
  - Test 5: 添加 return_rate=None 的 fake_exp → 平均收益率不变 ✅
```

## 7. 测试结果

| 测试项 | 结果 |
|---|---|
| Test 1: Experience → Insight | ✅ 生成 11 条 Insight |
| Test 2: 成功/失败统计正确 | ✅ n=9, hits=6, wr=66.7%, avg=2.55% |
| Test 3: 样本不足 → low confidence | ✅ n=2 → insufficient_data + low |
| Test 4: 去重（重复分析 + 重复持久化） | ✅ ID 集合一致, added=0 |
| Test 5: 未结算不污染 | ✅ 平均收益不变 |
| Test 6: Legacy Flow 8/8 | ✅ |
| Test 7: RSI Flow | ✅ |
| Test 8: Phase 2 回归 | ✅ |

**关键发现**（基于当前 9 条样本，标记为 `limited`）：
- 置信度「中」（7条）：命中率 71.4%，优于「高」（2条，50%）
- 涨停板策略（4条）：命中率 50%，平均收益 3.9%（最高）
- 涨停>=80 家的市场（3条）：命中率仅 33.3%
- 2026-08-13 全命中（3/3），2026-08-12 仅 1/3

## 8. 已知限制

| 限制 | 说明 | 未来改进 |
|---|---|---|
| 样本量小 | 仅 9 条 Experience，3 个交易日 | 积累更多交易日后重新分析 |
| 板块分组取首位 | 只用 top_sectors[0] | 未来可扩展为多板块交叉分析 |
| 策略模式基于关键词 | 用"涨停/量比/消息/资金"简单匹配 | 未来可用结构化策略标签 |
| 暂无 LLM 解释 | 当前只输出统计描述 | 未来可让 LLM 基于统计结果生成深度解读 |
| 不持久化 Experience | 系统重启后需从 SQLite 重建 | 未来可接入 SQLite PersistentMemory |
| insights.json 平铺存储 | 无索引 | 未来可接入数据库 |

## 文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/rsi_insight.py` | **新增** | Insight 模型 + AShareInsightAnalyzer + 持久化 |
| `data/insights.json` | **自动生成** | Insight 持久化文件（由 save_insights 写入） |
