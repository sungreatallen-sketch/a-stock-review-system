# RSI Walk-Forward Validation 实现文档

> 版本：Phase 5 ｜ 日期：2026-08-18 ｜ 状态：已完成

---

## 1. Walk-Forward 设计

总窗口 90 天，滚动 3 个窗口：

```
Window 1: train=[day0:day60] (60天) → test=[day60:day90] (30天)
Window 2: train=[day15:day75] (60天) → test=[day75:day90] (15天)
Window 3: train=[day0:day74]  (74天) → test=[day74:day90] (16天)
```

每个窗口：
- train 数据用于回测（但不用于本窗口的评估）
- test 数据（OOS）用于评估 Candidate 和 Production
- OOS 数据从未参与 Insight 生成或 Candidate 生成

## 2. OOS 验证

每个窗口独立运行：
1. Production backtest on test period
2. Candidate backtest on test period
3. 收集 test period 的交易结果

OOS 聚合：合并所有窗口的 test 交易，计算整体统计。

## 3. 统计显著性

| 检验 | 方法 | 说明 |
|---|---|---|
| 命中率置信区间 | Wilson score interval | 90% CI |
| 命中率差异 | Two-proportion z-test | H0: p_cand = p_prod |
| 超额收益 | 点估计 | avg_ret_cand - avg_ret_prod |
| 显著性水平 | α = 0.10 | 金融领域常用 |
| 样本门槛 | OOS trades >= 5 | MIN_OOS_TRADES |

## 4. 鲁棒性检查

| 检查项 | 方法 | 说明 |
|---|---|---|
| 一致性 | Candidate 在多少窗口优于 Production | >=70% 为一致 |
| 退化检测 | 比较不同窗口的收益趋势 | 最后窗口 < 首窗口*50% = 严重退化 |
| 窗口稳定性 | 各窗口 win_rate 波动 | 标准差 |

## 5. 验证决策

综合评分（score）：

| 条件 | 分数 |
|---|---|
| OOS avg_ret > Production | +2 |
| OOS win_rate > Production | +1 |
| 统计显著 (p < 0.10) | +2 |
| 窗口一致性 >= 70% | +1 |
| 严重退化 | -2 |
| 中度退化 | -1 |

决策：
- score >= 3: validated (confidence=high if >=5, medium if >=3)
- score >= 1: validated (confidence=low)
- score <= -2: rejected
- 其他: rejected (confidence=low)

## 6. 数据泄漏控制

```
Phase 3 Insight 生成: 使用全部历史 Experience
Phase 4 Candidate 生成: 基于 Insight
Phase 5 Walk-Forward: 纯 OOS 验证

关键分离:
  - Insight 来自历史 Experience（Phase 3）
  - Candidate 基于 Insight（Phase 4）
  - Validation 使用 Walk-Forward 的 OOS test period
  - OOS test period 数据从未参与 Insight/Candidate 生成
```

## 7. 样本门槛

| 条件 | 处理 |
|---|---|
| OOS trades < 5 | decision = insufficient_data |
| OOS trades 5-14 | confidence = low |
| OOS trades 15-29 | confidence = medium |
| OOS trades >= 30 | confidence = high |

## 8. Candidate 状态更新

验证后更新 Candidate 状态：
- validated: OOS 验证通过
- rejected: OOS 验证不通过
- insufficient_data: 样本不足

状态更新写入 `candidate/<id>.json`，包含完整 `validation_report`。

## 9. 测试结果

| 测试项 | 结果 |
|---|---|
| Test 1: Walk-Forward 分割 | ✅ 3 窗口，train 在 test 之前，无重叠 |
| Test 2: OOS 与 Discovery 分离 | ✅ test_days 非空 |
| Test 3: 统计检验工具 | ✅ Wilson CI / z-test 正确 |
| Test 4: Validation Report | ✅ 聚合/检验/鲁棒性 |
| Test 5: 验证决策 | ✅ decision=validated, confidence=medium |
| Test 6: 样本不足 | ✅ insufficient_data |
| Test 7: 状态更新 | ✅ candidate → validated |
| Test 8: 持久化 | ✅ |
| Test 9: Phase 2/3/4 | ✅ |
| Test 10: Legacy Flow | ✅ |
| Test 11: RSI Flow | ✅ |

**Mock 验证结果**：
```
决策: validated (confidence=medium)
OOS 平均收益: 3.7% > Production 1.91%
OOS 命中率: 66.7% > Production 47.8%
窗口一致性: 100%
统计显著性: p=0.1772 (不显著，α=0.10)
```

## 10. 当前限制

| 限制 | 说明 |
|---|---|
| 真实回测未执行 | 需要 MCP 连接 |
| OOS 聚合近似 | 用 avg_ret * count 近似，非逐笔 |
| 退化检测简化 | 仅比较首尾窗口 |
| 窗口数固定 | 3 个窗口，非自适应 |

## 文件清单

| 文件 | 操作 |
|---|---|
| `app/rsi_validation.py` | **新增** |
| `data/validation/` | **自动生成** |
| `RSI_VALIDATION_IMPLEMENTATION.md` | **新增** |
