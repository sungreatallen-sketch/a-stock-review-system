# A股收盘复盘与次日标的预测系统

## 功能
- **收盘复盘**：抓取指数 / 市场情绪（涨停·跌停·炸板·连板）/ 板块排行 / 资金数据（主力、北向、龙虎榜）
- **数据真实性**：ego browser 优先抓东财公开数据 → 数据缺失时按序调用 WorkBuddy MCP（通达信 → 同舟 → Wind）→ 关键字段双源交叉验证；拿不到一律标注「数据不可获取」，绝不编造
- **可视化**：自动生成手机自适应的 HTML 报告（ECharts 图表、全自包含）
- **飞书入口**：手动发命令触发，结果卡片 + 报告链接回推飞书

## 快速开始
```bash
# 1. 配置
cp .env.example .env   # 填 DEEPSEEK_API_KEY（已填）；飞书三项等你有后填

# 2. 执行收盘复盘（采集→验证→生成 JSON/HTML→入库）
.venv/bin/python run_cli.py review

# 3. 启动报告服务（手机同一 Wi-Fi 可访问）
.venv/bin/python run_cli.py serve
# 浏览器打开 http://<Mac局域网IP>:8787/report/2026-08-11
# 查看局域网地址：http://127.0.0.1:8787/ip
```

## 目录
```
app/            核心代码
  ego_scraper.py   ego browser 抓东财公开数据
  mcp_client.py    WorkBuddy MCP 代理客户端（token 自动发现）
  collector.py     采集编排 + 兜底链 + 交叉验证
  validator.py     双源容差校验
  report_builder.py  用户 JSON 结构
  html_report.py   手机自适应 HTML
  server.py        FastAPI 报告服务 + 飞书事件入口
  feishu/bot.py    飞书机器人（发卡片/收命令）
reports/        每日 JSON + HTML
static/         本地 ECharts（离线可用）
data/           SQLite 历史库
config/         config.yaml（MCP 顺序、Web 端口等）
.env            API Key 等敏感配置（已 gitignore）
```

## 数据来源与验证说明
- 主源：东方财富公开接口（经 ego browser 在浏览器上下文访问），来源页面见报告内 `meta.来源网页`
- 兜底/验证源：WorkBuddy MCP 聚合代理（`connector-proxy`）内的通达信 / 同舟 / Wind
- 每个字段记录来源；未交叉验证的字段在报告中明确标注

## 待办（M2/M3）
- [ ] 7-10 日强势板块/个股候选池 + 规则打分
- [ ] 消息面扫描（公告/新闻/龙虎榜）→ DeepSeek V4 Flash 研判出 3 只
- [ ] 历史回测框架（收盘买/次日开盘卖）
- [ ] 飞书命令：`复盘` / `预测`

## 飞书接入（已完成）
- 独立应用「A股复盘预测助手」已创建并发布：App ID cli_aaf063d9cff89cb0（凭证在 .env）
- 已配置：机器人能力、消息权限、长连接事件订阅（im.message.receive_v1）
- 后台服务（launchd 开机自启 + 崩溃自恢复）：
  - com.ashare.bot    飞书机器人长连接（日志 /Users/yage/ashare-logs/bot.log）
  - com.ashare.server 报告 Web 服务 :8787（日志 /Users/yage/ashare-logs/server.log）
- 使用：飞书内搜索并添加「A股复盘预测助手」机器人，发「复盘」即返回收盘复盘卡片+报告链接

## M2：候选池 + 规则打分 + 回测（已完成）
- 候选池：7-10日强势板块（5日主力资金流）+ 板块内强势个股 + 成交额活跃 + 涨停（剔除 ST/北交所）
- 打分：板块强度/个股强度/资金活跃/换手率/情绪/质量
- **量比过滤**（回测发现的最强因子）：剔除量比>2.0 放巨量标的
- 回测结果（60日）：平均 +0.02%/笔，跑赢指数同口径 +0.20%/笔（详见 docs/M2回测报告.md）
- 命令：`run_cli.py backtest`（回测）、`run_cli.py predict`（今日Top3）、飞书发「预测」

## M3：消息面 + DeepSeek 研判 + 模拟盘（已完成）
- 消息面：同舟 doc_search 扫描候选股近5日新闻/公告/事件（含来源链接、情绪初筛）
- 研判：DeepSeek（deepseek-v4-flash）基于规则候选+消息面+市场环境输出 Top3（逻辑/风险/置信度），输出经白名单防幻觉
- 模拟盘：`run_cli.py track record/settle/stats` 记录预测→次日回填→命中率统计
- 命令：`run_cli.py predict`（完整预测）、飞书「预测」
