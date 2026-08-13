# AGENTS.md —— 大模型接入本仓库的强制引导

> **任何新版本开发或 bug 修复，必须先阅读 docs/工程记忆.md（工程记忆），再按需阅读其引用的权威文档。**
> 违反引导直接开发，容易重复踩坑（历史 I01–I26 教训）。

## 必读（按顺序）
1. docs/工程记忆.md —— 项目状态速览 + 关键机制 + 运维速查
2. execution_rules.yaml —— 执行规则（R01–R30，唯一事实源；运行前必须预读 R30）
3. docs/需求规格说明书.md —— 原始需求基线（REQ-001）
4. docs/架构设计方案.md —— 定稿架构（ARCH-001）
5. docs/变更与回滚记录.md —— 变更回滚索引（CHG-001）
6. docs/问题与修复记录.md —— 问题与修复日志（ISSUE-001）

## 硬性红线（摘要）
- 数据真实性：不编造，标"数据不可获取"；来源可溯源
- 数据源：ego 优先 → MCP 兜底 → 连不上必须上报用户
- 评估口径：昨收买→今收卖（收盘-收盘）
- 所有修复/更新必须记日志（R29）
- 运行前预读规则（R30）
- 因子未验证未批准不得接入（R28）
- 改 HTML 模板必须重启报告服务（I24 教训）

## 常用命令
```bash
.venv/bin/python run_cli.py review --force   # 完整复盘(强制重生成)
.venv/bin/python run_cli.py predict          # 生成今日预测
.venv/bin/python run_cli.py track auto|stats # 结算/统计
```
服务重启与回滚见 docs/工程记忆.md 第6节。
