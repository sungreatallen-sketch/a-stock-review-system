# 第一批实施审查文档

> **生成时间**：2026-09-21 21:45
> **任务**：修复板块排名字段传递
> **状态**：待部署（未修改生产系统）

---

## 一、项目路径与隔离环境

| 项目 | 路径 |
|---|---|
| 原项目 | `/Users/yage/Documents/我的预测系统` |
| 隔离工作目录 | `/Users/yage/Documents/ashare-v1.2-fix-sector-rank` |
| 代码基线 | `c7b32e2` (2026-09-14 最新提交) |
| 隔离分支 | `fix/sector-rank-v1.2` |

---

## 二、实际修改文件

| 文件 | 修改内容 |
|---|---|
| `app/predict/candidate_pool.py` | line 87-89: 添加rank到sector_rank的映射 |
| `app/predict/strategy.py` | line 10: 版本号v1.1→v1.2 |
| `tests/test_sector_rank_fix.py` | 新增：20个离线验收测试 |

---

## 三、根因与修复后的数据流

### 根因

ego源（`alt_data.py:265`）返回板块数据时使用`rank`字段，但`candidate_pool.py:87`处理时只设置`s["industry"] = s["name"]`，未将`rank`映射为`sector_rank`。

下游`build()`在line303读取`s.get("sector_rank")`，对于ego源返回的是None，导致`_sector_score()`返回默认6分。

### 修复后的数据流

```
ego源 (alt_data.py:265)
  ↓ 返回 {"rank": 1, "code": "BK0001", "name": "板块A", ...}
  ↓
_top_sectors() (candidate_pool.py:83-91)
  ↓ 修复后：if "sector_rank" not in s and "rank" in s:
  ↓          s["sector_rank"] = s["rank"]
  ↓ 返回 {"rank": 1, "sector_rank": 1, "industry": "板块A", ...}
  ↓
build() (candidate_pool.py:303)
  ↓ st["sector_rank"] = s.get("sector_rank")
  ↓ 返回股票数据含 sector_rank=1
  ↓
score_stock() (scoring.py:88)
  ↓ s = _sector_score(stock.get("sector_rank"))
  ↓ 返回 20.0（第一名板块）
```

---

## 四、修改前后离线输入输出示例

### 修改前（v1.1）

```python
# ego源返回
ego_sector = {"rank": 1, "code": "BK0001", "name": "元件"}

# _top_sectors()处理后
ego_sector["industry"] = ego_sector["name"]
# ego_sector = {"rank": 1, "code": "BK0001", "name": "元件", "industry": "元件"}
# 注意：没有sector_rank字段

# build()传给股票
stock["sector_rank"] = ego_sector.get("sector_rank")  # None

# score_stock()评分
_sector_score(None) = 6  # 默认6分
```

### 修改后（v1.2）

```python
# ego源返回
ego_sector = {"rank": 1, "code": "BK0001", "name": "元件"}

# _top_sectors()处理后
ego_sector["industry"] = ego_sector["name"]
if "sector_rank" not in ego_sector and "rank" in ego_sector:
    ego_sector["sector_rank"] = ego_sector["rank"]
# ego_sector = {"rank": 1, "code": "BK0001", "name": "元件", "industry": "元件", "sector_rank": 1}

# build()传给股票
stock["sector_rank"] = ego_sector.get("sector_rank")  # 1

# score_stock()评分
_sector_score(1) = 20.0  # 第一名板块
```

---

## 五、测试命令与结果

### 测试套件说明

本次验收包含**两套测试**，区分"静态逻辑验证"和"真实业务路径验证"：

| 测试文件 | 类型 | 测试数 | 说明 |
|---|---|---|---|
| `tests/test_sector_rank_fix.py` | 静态逻辑验证 | 20 | 直接调用评分函数，验证输入→输出关系（复制业务逻辑到测试中） |
| `tests/test_sector_rank_real_path.py` | 真实业务路径验证 | 7 | Mock外部依赖，调用实际`_top_sectors()`→`build()`→`score_pool()`完整链路 |

**重要区别**：`test_sector_rank_fix.py`中的`TestEgoRankMapping`类在测试内部复制了映射逻辑（`if "sector_rank" not in s and "rank" in s: s["sector_rank"] = s["rank"]`），属于静态验证。`test_sector_rank_real_path.py`调用真实的`CandidatePool._top_sectors()`方法，让数据经过真实业务路径，属于动态验证。

### 测试命令

```bash
cd /Users/yage/Documents/ashare-v1.2-fix-sector-rank
/Users/yage/Documents/我的预测系统/.venv/bin/python -m pytest tests/test_sector_rank_fix.py tests/test_sector_rank_real_path.py -v
```

### 测试结果

```
27 passed in 0.02s
```

### 真实业务路径测试覆盖（test_sector_rank_real_path.py）

| 测试类 | 测试项 | 验证方式 | 结果 |
|---|---|---|---|
| TestRealPathRank1 | rank=1传到评分为20 | 实际_top_sectors()→build()→score_pool() | ✅ |
| TestRealPathRank8 | rank=8传到评分为3 | 实际_top_sectors()→build()→score_pool() | ✅ |
| TestRealPathExistingSectorRank | 已有sector_rank不被覆盖 | 逻辑验证 | ✅ |
| TestRealPathNoRank | 无rank时默认6分 | 实际_top_sectors()→build()→score_pool() | ✅ |
| TestRealPathRankAffectsSorting | 排名影响总分差17 | 分别构建rank=1和rank=8池比较 | ✅ |
| TestRealPathOtherFactorsUnchanged | 其他因子计算不变 | 实际build()→score_pool() | ✅ |
| TestVersionPropagation | 版本号v1.2 | 常量检查 | ✅ |

### 静态逻辑测试覆盖（test_sector_rank_fix.py）

| 测试类 | 测试项 | 验证方式 | 结果 |
|---|---|---|---|
| TestSectorScoreFunction | _sector_score(1)=20, (8)=3, None=6 | 直接调用函数 | ✅ |
| TestEgoRankMapping | rank→sector_rank映射 | **复制映射逻辑到测试中**（非真实路径） | ✅ |
| TestScoreStockIntegration | score_stock完整路径 | 直接调用函数 | ✅ |
| TestRankAffectsSorting | 排名影响排序 | 直接调用函数 | ✅ |
| TestScorePoolIntegration | score_pool完整流程 | 直接调用函数 | ✅ |
| TestVersionPropagation | 版本号传播 | 常量检查 | ✅ |
| TestRegression | 评分公式不变 | 直接调用函数 | ✅ |

### 修复前/修复后对比证据（真实业务路径测试）

**测试方法**：移除`candidate_pool.py`中3行修复代码 → 运行测试 → 恢复修复 → 运行测试

#### 修复前（移除3行映射代码）：4 FAILED, 3 PASSED

```
FAILED TestRealPathRank1::test_rank_1_propagates_to_score_20
  AssertionError: 期望sector_rank=1，实际=None
FAILED TestRealPathRank8::test_rank_8_propagates_to_score_3
  AssertionError: 期望sector_rank=8，实际=None
FAILED TestRealPathRankAffectsSorting::test_rank_difference_affects_total_score
  AssertionError: rank=1板块分应为20，实际=6
FAILED TestRealPathOtherFactorsUnchanged::test_other_factors_calculation_unchanged
  AssertionError: 板块分期望20，实际=6
PASSED TestRealPathExistingSectorRank::test_existing_sector_rank_not_overridden
PASSED TestRealPathNoRank::test_no_rank_preserves_default_behavior
PASSED TestVersionPropagation::test_strategy_version_is_v12
```

**失败原因**：ego源返回的`rank`字段未映射为`sector_rank`，`_top_sectors()`返回的板块数据中`sector_rank=None`，导致`build()`传给股票的`sector_rank=None`，`_sector_score(None)`返回默认6分。

#### 修复后（恢复3行映射代码）：7 PASSED, 0 FAILED

```
PASSED TestRealPathRank1::test_rank_1_propagates_to_score_20
PASSED TestRealPathRank8::test_rank_8_propagates_to_score_3
PASSED TestRealPathExistingSectorRank::test_existing_sector_rank_not_overridden
PASSED TestRealPathNoRank::test_no_rank_preserves_default_behavior
PASSED TestRealPathRankAffectsSorting::test_rank_difference_affects_total_score
PASSED TestRealPathOtherFactorsUnchanged::test_other_factors_calculation_unchanged
PASSED TestVersionPropagation::test_strategy_version_is_v12
```

**通过原因**：`rank`字段正确映射为`sector_rank`，数据经过完整业务路径：`ego返回rank` → `_top_sectors()`映射为`sector_rank` → `build()`传给股票 → `score_pool()`计算板块分。

---

## 六、v1.2版本说明

### 唯一业务变化

修复ego板块排名字段传递。

### 待部署状态

**当前状态**：待部署

- 代码已在隔离分支`fix/sector-rank-v1.2`完成
- 离线测试全部通过
- 未合并到生产分支
- 未部署到生产环境

### 生效时间

将在后续实际部署时记录，本批不填假定时间。

---

## 七、未解决事项

1. **历史69笔板块分为6的归因**：需要逐笔检查是否全部由ego字段缺陷造成，还是部分由其他原因（如MCP/THS兜底失败）造成

2. **回测数据泄漏**：回测构建过去日期候选池时是否使用了今天的板块排名，本次修复未涉及

3. **评价口径不一致**：生产T+1开→T+2收、回测T收→T+1开，本次修复未涉及

---

## 八、后续部署与回滚步骤草案

> 详细方案见 `ROLLBACK_PLAN.md`

### 部署步骤（本批不执行）

1. 将`fix/sector-rank-v1.2`分支合并到`main`
2. 重启服务：
   ```bash
   launchctl kickstart -k gui/$(id -u)/com.ashare.bot
   launchctl kickstart -k gui/$(id -u)/com.ashare.server
   ```
3. 运行一次完整复盘验证：
   ```bash
   .venv/bin/python run_cli.py review --force
   ```
4. 检查报告中板块分是否不再全部为6
5. 记录生效时间

### 回滚步骤

```bash
git revert <merge-commit-hash>
launchctl kickstart -k gui/$(id -u)/com.ashare.bot
launchctl kickstart -k gui/$(id -u)/com.ashare.server
```

### 生产基线

| 项目 | 值 |
|---|---|
| 当前分支 | `main` |
| 当前提交 | `c7b32e2` |
| candidate_pool.py校验值 | `74e04b2785ac3869a6e1620a824993ea` |
| strategy.py校验值 | `f7183e28a9a644a300265d96a7c3b970` |

---

## 九、声明

### 未修改生产数据

✅ 确认：本次修改仅在隔离工作目录进行，未修改原项目的任何代码、配置或数据库。

### 未调用外部服务

✅ 确认：本次测试使用离线构造的输入，未调用任何行情接口、浏览器、LLM或飞书。

### 未部署

✅ 确认：本次修改未合并到生产分支，未部署到生产环境。

### 测试证明范围

✅ 确认：本次测试证明字段传递恢复正常，**不证明收益提高**。

---

## 十、代码差异

### candidate_pool.py

```diff
@@ -85,6 +85,9 @@ class CandidatePool:
                 self._sector_window = "10日(ego)"
                 for s in secs:
                     s["industry"] = s["name"]
+                    # ego源返回rank字段，标准化为sector_rank供下游评分使用
+                    if "sector_rank" not in s and "rank" in s:
+                        s["sector_rank"] = s["rank"]
                 return secs
         except Exception as e:
             log.warning("ego 10日板块失败: %s", str(e)[:120])
```

### strategy.py

```diff
@@ -7,7 +7,8 @@ log = logging.getLogger("strategy")
 
 MAX_VOL_RATIO = 2.0   # 量比超过该值视为"放巨量"，剔除（回测最优参数）
 TOP_PRE = 10          # 先取基础打分前 N 做量比检查
-STRATEGY_VERSION = "v1.1"   # v1.0→v1.1 唯一变化：过滤不可交易的涨停股票
+STRATEGY_VERSION = "v1.2"   # v1.1→v1.2 唯一变化：修复ego板块排名字段传递
+                            # v1.0→v1.1 唯一变化：过滤不可交易的涨停股票
```

---

*文档结束*