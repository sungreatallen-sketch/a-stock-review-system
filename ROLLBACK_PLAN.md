# 上线与回滚方案

> **生成时间**：2026-09-21 22:00
> **状态**：仅方案，不执行

---

## 一、生产基线记录

### Git状态

| 项目 | 值 |
|---|---|
| 当前分支 | `main` |
| 当前提交 | `c7b32e2` (2026-09-14) |
| 隔离分支 | `fix/sector-rank-v1.2` |
| 隔离提交 | `430e052` |

### 未提交修改（数据文件，非代码）

| 文件类型 | 说明 |
|---|---|
| `data/*.db` | 业务数据库（持续写入中） |
| `data/*.json` | 推荐历史、结算结果 |
| `output/report_img/` | 报告图片 |
| `data/data_source_alert_*.json` | 数据源告警状态 |
| `data/last_review_sent_*.flag` | 发送标记 |

**注意**：这些是业务数据文件，不是代码修改。上线时不得覆盖或丢弃。

### 关键文件校验值（MD5）

| 文件 | 校验值 | 说明 |
|---|---|---|
| `app/predict/candidate_pool.py` | `74e04b2785ac3869a6e1620a824993ea` | 待修改 |
| `app/predict/strategy.py` | `f7183e28a9a644a300265d96a7c3b970` | 待修改 |
| `app/predict/scoring.py` | `309f7d3d6eed0d11b913dd084158a3a7` | 不修改 |
| `config/config.yaml` | `65fce5e2b4e422121edf013daddf751d` | 不修改 |

### 目录结构

| 项目 | 路径 |
|---|---|
| 原项目目录 | `/Users/yage/Documents/我的预测系统` |
| 隔离开发目录 | `/Users/yage/Documents/ashare-v1.2-fix-sector-rank` |
| 数据目录 | `/Users/yage/Documents/我的预测系统/data` |
| 报告目录 | `/Users/yage/Documents/我的预测系统/reports` |
| 日志目录 | `/Users/yage/ashare-logs` |

### 运行中的服务

| 服务 | PID | 说明 |
|---|---|---|
| `com.ashare.bot` | 19925 | 飞书机器人 |
| `com.ashare.server` | 1521 | 报告Web服务(:8787) |
| `com.ashare.caffeinate` | 1512 | 防休眠 |
| `com.ashare.settle` | - | 定时结算（非活跃） |
| `com.ashare.review` | - | 定时复盘（非活跃） |

---

## 二、上线涉及的文件

### 必须修改的文件

| 文件 | 修改内容 | 影响范围 |
|---|---|---|
| `app/predict/candidate_pool.py` | line 87-89: 添加rank→sector_rank映射 | 板块评分 |
| `app/predict/strategy.py` | line 10: 版本号v1.1→v1.2 | 版本标识 |

### 必须新增的文件

| 文件 | 说明 |
|---|---|
| `tests/test_sector_rank_fix.py` | 离线验收测试（20个） |

### 必须更新的文件

| 文件 | 说明 |
|---|---|
| `docs/变更与回滚记录.md` | 添加CHG-052 |
| `docs/问题与修复记录.md` | 添加ISSUE-047 |

### 不修改的文件

- `app/predict/scoring.py`（评分公式不变）
- `config/config.yaml`（配置不变）
- `data/*.db`（数据库不变）
- `reports/*`（历史报告不变）

---

## 三、旧版本保留方式

### 代码版本保留

1. **Git历史**：所有版本可通过git log查看
2. **当前生产版本**：`c7b32e2` (main分支)
3. **隔离开发版本**：`430e052` (fix/sector-rank-v1.2分支)

### 数据库备份

**当前状态**：数据库持续写入中，不可简单复制。

**备份方式**（如需）：
```bash
# 使用SQLite一致性备份
sqlite3 /Users/yage/Documents/我的预测系统/data/a_share.db ".backup '/path/to/backup.db'"
```

**已有备份**：
- `data/a_share_backup_before_recalc_20260904_221357.db`（9/4重算前）
- `data/a_share_backup_20260902_091628.db`（9/2备份）

---

## 四、需要回滚的情况

### 必须回滚的情况（硬指标）

| 故障现象 | 判断方法 | 阈值 |
|---|---|---|
| 板块分异常 | 检查报告中板块分分布 | 全部为6或全部为20 |
| 候选池崩溃 | 检查日志`candidate_pool`ERROR | 任何异常 |
| 服务不可用 | `curl http://127.0.0.1:8787/report/$(date +%Y-%m-%d)` | 返回非200 |
| 飞书推送失败 | 检查日志`bot`ERROR | 连续3次失败 |
| 性能严重下降 | 候选池构建时间 | >5秒（正常<1秒） |

### 不需要回滚的情况

1. **评分变化**：板块分从6变为其他值（这是预期行为）
2. **推荐变化**：Top3股票与之前不同（这是预期行为）
3. **命中率波动**：单日命中率变化（正常波动）
4. **候选数变化**：候选池数量变化（正常波动）

---

## 五、部署与回滚步骤

### 部署批次标识

- **批次号**：`fix-sector-rank-v1.2-20260921`
- **部署日期**：待定（需用户授权）
- **隔离目录**：`/Users/yage/Documents/ashare-v1.2-fix-sector-rank`
- **隔离分支**：`fix/sector-rank-v1.2`（提交`e459f46`）

### 定时任务与服务入口（待核实）

| 组件 | 类型 | 核实方法 | 状态 |
|---|---|---|---|
| `com.ashare.bot` | launchd服务（飞书机器人） | `launchctl list com.ashare.bot` | 运行中（PID 19925） |
| `com.ashare.server` | launchd服务（Web服务） | `launchctl list com.ashare.server` | 运行中（PID 1521） |
| `com.ashare.review` | launchd定时任务 | `cat ~/Library/LaunchAgents/com.ashare.review.plist` | ❌ 未核实执行时间 |
| `com.ashare.settle` | launchd定时任务 | `cat ~/Library/LaunchAgents/com.ashare.settle.plist` | ❌ 未核实执行时间 |
| `com.ashare.caffeinate` | launchd服务（防休眠） | `launchctl list com.ashare.caffeinate` | 运行中（PID 1512） |

**部署前必须核实**：review和settle任务的执行时间，避免在执行窗口内部署。

### 完整部署流程（6步，任一步失败必须中止）

```
┌─────────────────────────────────────────────────────────────────┐
│ 步骤1：阻止新的业务任务进入                                       │
│   - 停止launchd定时任务：                                        │
│     launchctl unload ~/Library/LaunchAgents/com.ashare.review.plist│
│     launchctl unload ~/Library/LaunchAgents/com.ashare.settle.plist│
│   - 验证任务已停止：                                              │
│     launchctl list | grep ashare                                │
│   失败处理：停止部署，恢复任务入口                                  │
├─────────────────────────────────────────────────────────────────┤
│ 步骤2：等待在途任务结束                                           │
│   - 检查是否有正在运行的进程：                                     │
│     ps aux | grep -E "run_cli|daily|review|settle" | grep -v grep│
│   - 如果有进程，等待其完成（最长等待30分钟）                         │
│   - 超时仍未结束：停止部署，恢复任务入口                            │
├─────────────────────────────────────────────────────────────────┤
│ 步骤3：备份与校验                                                │
│   - 备份当前文件（精确路径）：                                     │
│     BATCH_ID="fix-sector-rank-v1.2-$(date +%Y%m%d%H%M%S)"       │
│     BACKUP_DIR="/Users/yage/Documents/我的预测系统/backups/${BATCH_ID}"│
│     mkdir -p "${BACKUP_DIR}"                                    │
│     cp .../candidate_pool.py "${BACKUP_DIR}/"                    │
│     cp .../strategy.py "${BACKUP_DIR}/"                          │
│   - 记录备份校验值：                                              │
│     md5 -r "${BACKUP_DIR}/candidate_pool.py"                    │
│       "${BACKUP_DIR}/strategy.py" > "${BACKUP_DIR}/checksums.txt"│
│   - 验证备份校验值与预期一致：                                     │
│     candidate_pool.py: 74e04b2785ac3869a6e1620a824993ea          │
│     strategy.py: f7183e28a9a644a300265d96a7c3b970                │
│   失败处理：停止部署，无需恢复（备份失败不影响现有文件）              │
├─────────────────────────────────────────────────────────────────┤
│ 步骤4：替换两个文件（非原子，需确保一致性）                         │
│   - 复制新文件到临时位置：                                        │
│     cp .../candidate_pool.py.new /tmp/candidate_pool.py.new      │
│     cp .../strategy.py.new /tmp/strategy.py.new                  │
│   - 校验新文件：                                                 │
│     md5 -r /tmp/candidate_pool.py.new /tmp/strategy.py.new       │
│   - 依次替换（间隔<1秒）：                                        │
│     mv /tmp/candidate_pool.py.new .../candidate_pool.py          │
│     mv /tmp/strategy.py.new .../strategy.py                      │
│   注意：两个文件替换之间存在短暂不一致窗口（<1秒）                   │
│   失败处理：停止部署，从备份恢复（见步骤6）                         │
├─────────────────────────────────────────────────────────────────┤
│ 步骤5：校验一致性                                                │
│   - 验证部署后的校验值：                                         │
│     md5 -r .../candidate_pool.py .../strategy.py                 │
│   - 验证版本号：                                                 │
│     grep "STRATEGY_VERSION" .../strategy.py                     │
│   - 校验不匹配：立即回滚（见步骤6），不得继续                       │
├─────────────────────────────────────────────────────────────────┤
│ 步骤6：恢复服务和任务入口                                         │
│   - 重启服务（有启动副作用，见下方说明）：                          │
│     launchctl kickstart -k gui/$(id -u)/com.ashare.bot           │
│     launchctl kickstart -k gui/$(id -u)/com.ashare.server        │
│   - 恢复定时任务：                                                │
│     launchctl load ~/Library/LaunchAgents/com.ashare.review.plist │
│     launchctl load ~/Library/LaunchAgents/com.ashare.settle.plist │
│   - 验证服务状态：                                                │
│     launchctl list | grep ashare                                │
│   失败处理：停止部署，从备份恢复（见下方回滚步骤）                   │
└─────────────────────────────────────────────────────────────────┘
```

### 服务重启副作用说明

**未经核实**：launchctl kickstart重启服务时是否有业务副作用（如重新初始化连接、清空缓存等）。

**已知行为**：
- `com.ashare.bot`：重启后会重新连接飞书WebSocket
- `com.ashare.server`：重启后Web服务短暂不可用（<1秒）

**建议**：在非交易时段执行重启，避免影响正在进行的预测或结算。

### 部署后验证（无业务写入）

```bash
# 1. 检查服务状态
launchctl list | grep ashare

# 2. 核对部署文件校验值（生产检查，非隔离目录测试）
md5 -r /Users/yage/Documents/我的预测系统/app/predict/candidate_pool.py \
       /Users/yage/Documents/我的预测系统/app/predict/strategy.py
# 应与隔离目录修复后的校验值一致

# 3. 核对版本号
grep "STRATEGY_VERSION" /Users/yage/Documents/我的预测系统/app/predict/strategy.py
# 应显示：STRATEGY_VERSION = "v1.2"

# 4. 验证报告服务可访问（只读，使用已存在的无写入端点）
curl -s http://127.0.0.1:8787/
# 应返回HTML页面（200）
curl -s http://127.0.0.1:8787/ip
# 应返回IP信息（200）

# 5. 检查日志无异常（只读）
tail -20 /Users/yage/ashare-logs/bot.log | grep -i error
tail -20 /Users/yage/ashare-logs/server.log | grep -i error
```

**注意**：
- `app/server.py`没有`/health`路由，使用`/`或`/ip`端点验证服务可访问性
- 部署后运行隔离目录测试不等于验证生产加载了新代码，需核对部署文件校验值
- 当日报告尚未生成导致`/report/<日期>`返回404，不等同于服务故障

### 回滚步骤（保留原始备份）

**原则**：恢复时保留原始备份，复制到目标目录临时文件、校验、再替换；不要直接mv走备份。

```bash
# 1. 确认备份批次
BACKUP_DIR="/Users/yage/Documents/我的预测系统/backups/fix-sector-rank-v1.2-<时间戳>"

# 2. 验证备份完整性
md5 -r "${BACKUP_DIR}/candidate_pool.py" "${BACKUP_DIR}/strategy.py"
cat "${BACKUP_DIR}/checksums.txt"
# 两者应一致

# 3. 复制备份到临时位置（不直接mv，保留原始备份）
cp "${BACKUP_DIR}/candidate_pool.py" /tmp/candidate_pool.py.rollback
cp "${BACKUP_DIR}/strategy.py" /tmp/strategy.py.rollback

# 4. 校验临时文件
md5 -r /tmp/candidate_pool.py.rollback /tmp/strategy.py.rollback
# 应与checksums.txt一致

# 5. 替换文件
mv /tmp/candidate_pool.py.rollback /Users/yage/Documents/我的预测系统/app/predict/candidate_pool.py
mv /tmp/strategy.py.rollback /Users/yage/Documents/我的预测系统/app/predict/strategy.py

# 6. 验证回滚
md5 -r /Users/yage/Documents/我的预测系统/app/predict/candidate_pool.py \
       /Users/yage/Documents/我的预测系统/app/predict/strategy.py
# 应恢复到原始校验值：
# candidate_pool.py: 74e04b2785ac3869a6e1620a824993ea
# strategy.py: f7183e28a9a644a300265d96a7c3b970
# 校验不匹配必须中止，不得继续

# 7. 重启服务
launchctl kickstart -k gui/$(id -u)/com.ashare.bot
launchctl kickstart -k gui/$(id -u)/com.ashare.server

# 8. 恢复定时任务（如果部署时已停止）
launchctl load ~/Library/LaunchAgents/com.ashare.review.plist
launchctl load ~/Library/LaunchAgents/com.ashare.settle.plist

# 9. 确认恢复
grep "STRATEGY_VERSION" /Users/yage/Documents/我的预测系统/app/predict/strategy.py
# 应显示：STRATEGY_VERSION = "v1.1"
```

### 回滚触发条件（需综合判断）

| 故障现象 | 判断方法 | 说明 |
|---|---|---|
| 板块分异常 | 比较输入排名与输出分数 | 排名1→分数应为20，排名8→分数应为3；不能仅凭"全部相同"判断 |
| 候选池崩溃 | 检查日志`candidate_pool`ERROR | Python异常，非超时或网络错误 |
| 服务不可用 | `curl http://127.0.0.1:8787/` | 连续3次非200；当日报告未生成导致404不算故障 |
| 飞书推送失败 | 检查日志`bot`ERROR | 连续3次相同错误，网络抖动不算 |

**注意**：
- 当日报告尚未生成导致`/report/<日期>`返回404，不等同于服务故障
- 性能阈值：当前无历史性能基准数据，部署后首次运行时记录候选池构建时间作为基准

---

## 六、业务数据保护

### 上线期间保护

1. **推荐记录**：新版本产生的推荐使用v1.2标记，旧版本保持v1.1或空
2. **结算记录**：结算逻辑未修改，结算结果不受影响
3. **发送记录**：已发送的报告不会重复推送
4. **历史数据**：不修改历史推荐的版本号

### 数据库操作原则

1. **禁止**：用旧数据库覆盖正在运行的数据库
2. **禁止**：因代码回滚删除或改写新产生的业务记录
3. **禁止**：把新版本产生的推荐改标成旧版本
4. **禁止**：重复推送已经发送的报告
5. **必须**：回滚前检查是否有正在进行的结算或复盘任务
6. **必须**：回滚后验证数据库完整性（`PRAGMA integrity_check`）

### 回滚期间数据保护清单

| 数据类型 | 保护措施 |
|---|---|
| `predictions`表 | 回滚不删除新记录，只影响新产生的记录 |
| `prediction_results`表 | 结算逻辑未修改，不受影响 |
| `daily_reports`表 | 历史报告不变 |
| `data/*.json` | 推荐历史文件不变 |
| `data/last_review_sent_*.flag` | 发送标记不变，不会重复推送 |
| `reports/*.html` | 历史报告文件不变 |

### 如需数据库迁移（本批不执行）

```bash
# 一致性备份（必须使用SQLite backup命令）
sqlite3 /Users/yage/Documents/我的预测系统/data/a_share.db \
  ".backup '/Users/yage/Documents/我的预测系统/backups/a_share_pre_v1.2.db'"

# 验证备份完整性
sqlite3 /Users/yage/Documents/我的预测系统/backups/a_share_pre_v1.2.db \
  "SELECT COUNT(*) FROM prediction_results;"
```

---

## 七、上线授权边界

### 当前状态

- ✅ 代码开发完成
- ✅ 离线测试通过（20/20）
- ❌ 未合并到生产分支
- ❌ 未部署到生产环境
- ❌ 未重启生产服务

### 上线前提条件

1. **用户明确授权**：用户必须明确指示"可以部署"或"合并到main"
2. **审查通过**：FIRST_BATCH_REVIEW.md必须被审查通过
3. **测试通过**：全部测试通过（静态 + 真实业务路径）
4. **无在途任务**：确认无正在运行的预测/复盘/结算任务

### 部署窗口（待核实）

**当前状态**：定时任务的具体执行时间未在代码中明确定义，以下为待核实项。

| 定时任务 | 预期执行时间 | 核实方法 | 状态 |
|---|---|---|---|
| 定时复盘（`com.ashare.review`） | 待核实 | `launchctl list com.ashare.review` 或检查plist | ❌ 未核实 |
| 定时结算（`com.ashare.settle`） | 待核实 | `launchctl list com.ashare.settle` 或检查plist | ❌ 未核实 |
| 飞书推送 | 随复盘触发 | - | - |

**部署窗口选择原则**：
1. 避开定时任务执行时间
2. 避开交易时段（09:30-15:00）
3. 部署后有足够时间观察（至少1小时）
4. 建议在用户可监控的时段执行

**核实方法**：
```bash
# 检查launchd定时任务配置
ls ~/Library/LaunchAgents/ | grep ashare
cat ~/Library/LaunchAgents/com.ashare.review.plist
cat ~/Library/LaunchAgents/com.ashare.settle.plist
```

### 上线后验证清单

1. [ ] 版本号显示为v1.2
2. [ ] 板块分不再全部为6
3. [ ] 候选池构建无异常
4. [ ] 报告服务正常
5. [ ] 飞书机器人正常

---

## 八、隔离开发退出方式

### 如果修改失败

1. 停止当前任务
2. 保留代码差异和失败证据
3. 原生产项目继续运行（不受影响）
4. 不修改生产文件
5. 不重启服务
6. 不执行生产回滚
7. 不自动删除隔离副本（留待审查）

### 如果测试失败

1. 记录失败的测试用例
2. 分析失败原因
3. 决定是否需要修改代码或调整测试
4. 重新运行测试直到通过
5. 所有测试通过后才能提交审查

---

## 九、紧急联系

如果上线后出现严重问题：

1. **立即回滚**：按第五节步骤执行
2. **检查日志**：`tail -f /Users/yage/ashare-logs/bot.log`
3. **检查服务**：`launchctl list | grep ashare`
4. **检查数据**：`sqlite3 data/a_share.db "SELECT COUNT(*) FROM prediction_results;"`

---

*方案结束，不执行上线或回滚*