# AGENTS.md —— 大模型接入本仓库的强制引导

> **任何新版本开发或 bug 修复，必须先阅读 docs/工程记忆.md（工程记忆），再按需阅读其引用的权威文档。**
> 违反引导直接开发，容易重复踩坑（历史 I01–I34 教训）。

## 必读（按顺序）
1. docs/工程记忆.md —— 项目状态速览 + 关键机制 + 运维速查
2. execution_rules.yaml —— 执行规则（R01–R34，唯一事实源；运行前必须预读 R30）
3. docs/需求规格说明书.md —— 原始需求基线（REQ-001）
4. docs/架构设计方案.md —— 定稿架构（ARCH-001）
5. docs/变更与回滚记录.md —— 变更回滚索引（CHG-001）
6. docs/问题与修复记录.md —— 问题与修复日志（ISSUE-001）

## 硬性红线（摘要）
- 数据真实性：不编造，标“数据不可获取”；来源可溯源
- 数据源：同花顺行情/K线主源 → ego/MCP 兜底；本地 MCP 禁走 VPN/系统代理；连不上必须上报用户
- WorkBuddy失败时，通达信类工具走OAuth直连兜底；`data/tdx_oauth.json`不提交、不打印
- WorkBuddy失败时，同舟工具走OAuth直连；Wind走本地600权限API key直连；token配置均不提交、不打印
- 推荐执行窗口：T 日收盘后发布 → **T+1 收盘买入 → T+2 收盘卖出**
- 评估必须等 T+2 出现；任一执行收盘价缺失保持 pending，不部分结算
- 16:00 只推送收盘后终版；盘中旧报告必须 force 刷新；sent flag 必须绑定 generated_at
- 所有修复/更新必须记日志（R29）
- 运行前预读规则（R30）
- 因子未验证未批准不得接入（R28）
- 改 HTML 模板必须重启报告服务（I24 教训）
- launchd 修改后必须 bootout/bootstrap 并确认 exit 0

## 常用命令
```bash
.venv/bin/python run_cli.py review --force   # 完整复盘(强制重生成)
.venv/bin/python run_cli.py predict          # 生成今日预测
.venv/bin/python run_cli.py track auto|stats # 结算/统计
.venv/bin/python scripts/check_data_sources.py
.venv/bin/python scripts/recalculate_settlements.py    # 先dry-run
.venv/bin/python scripts/recalculate_settlements.py --apply
```
服务重启、launchd 重载与回滚见 docs/工程记忆.md 第6/8节。
