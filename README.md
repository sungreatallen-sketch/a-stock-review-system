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
