# A_SHARE_PROJECT_RSI_INTEGRATION_ANALYSIS.md

> 生成日期：2026-08-15
> 用途：将当前「A股收盘复盘与次日标的推荐项目」作为 Application 接入「RSI Agent Framework」的接入前分析资料。
> 约束：仅分析，不修改代码、不重构、不实现 RSI、不安装依赖。所有结论基于实际代码，无法确认处标注【未知】。
> 代码根目录：`/Users/yage/Documents/我的预测系统`

---

## 一、项目基本信息

| 项 | 内容 | 依据 |
|---|---|---|
| 1. 项目名称 | A股收盘复盘与标的预测 | config/config.yaml `project.name` |
| 2. 项目用途 | 收盘后自动复盘 A 股市场 + 生成次日 3 只标的预测；飞书手动触发；输出手机可看 HTML 与图片 | README / 需求规格说明书 |
| 3. 技术栈 | Python（FastAPI + SQLite + lark_oapi 飞书 SDK + MCP SDK + OpenAI SDK）；Node（playwright 截图）；macOS launchd；ego-browser（Chromium 网页抓取） | app/*.py, scripts/ |
| 4. 运行环境 | Python 3.12.13（.venv）；Node v24.19.0（bundled runtime，仅截图用）；macOS | pip list / node --version |
| 5. LLM/模型 | **DeepSeek V4 Flash**（model: `deepseek-v4-flash`），经 OpenAI SDK 调用，base_url=https://api.deepseek.com | config.yaml `model` / app/predict/llm.py |
| 6. Agent Framework | **无第三方 Agent 框架**。自研"函数编排 + 线程调度"（workflow.py 为主编排，bot.py 后台线程触发），另有一套规则系统 execution_rules.yaml（R01–R30） | app/workflow.py / app/rules.py |
| 7. 数据库 | SQLite 三份：`data/a_share.db`（daily_reports / predictions / prediction_results）、`data/mcp_cache.db`（MCP 结果缓存）、`data/ego_kline.db`（东财K线/龙虎榜缓存） | app/storage.py / app/predict/track.py / app/predict/cache.py |
| 8. Redis/MQ | **无** | 代码无引用 |
| 9. Docker/部署 | **无 Docker**。macOS launchd 常驻 4 个服务：com.ashare.bot（飞书）、com.ashare.server（Web:8787）、com.ashare.settle（15:30 定时结算）、com.ashare.caffeinate（防休眠） | ~/Library/LaunchAgents/com.ashare.*.plist |
| 10. 飞书组件 | lark_oapi（1.7.2）**WebSocket 长连接**收消息；应用「A股复盘预测助手」；命令：复盘 / 预测 / 结算 / 规则；输出：卡片 + 图片直发 + HTML 链接 | app/feishu/bot.py |

---

## 二、完整项目目录结构（实际）

```
/Users/yage/Documents/我的预测系统/
├── AGENTS.md                     # 大模型接入引导（必读工程记忆）
├── RULES.md                      # 执行规则人读版（R01–R30）
├── README.md
├── execution_rules.yaml          # 执行规则唯一事实源（30 条，v1.4）
├── investment_hypotheses.yaml/md # 投资经验假设库（16 条 hypothesis）
├── A_SHARE_PROJECT_RSI_INTEGRATION_ANALYSIS.md  # 本文档
├── config/
│   └── config.yaml               # 项目/模型/MCP/Web/飞书/回测 配置
├── .env / .env.example           # 密钥（DEEPSEEK / FEISHU / MCP token）
├── app/                          # 核心代码（见下方模块标注）
│   ├── config.py                 # 配置加载（yaml + .env）
│   ├── collector.py              # 复盘数据采集编排（ego + MCP）
│   ├── ego_scraper.py            # ego-browser 抓东财公开数据（多域名兜底）
│   ├── mcp_client.py             # WorkBuddy MCP 聚合代理客户端（自动发现端口/token）
│   ├── validator.py              # 双源交叉验证（容差）
│   ├── storage.py                # SQLite + reports JSON
│   ├── workflow.py               # 【Orchestrator】复盘+预测+跟踪统一流程
│   ├── report_builder.py         # 报告 JSON 组装
│   ├── html_report.py            # 手机自适应 HTML（ECharts 内嵌）
│   ├── server.py                 # 【API】FastAPI 报告服务 + 飞书事件入口
│   ├── rules.py                  # 规则加载 + 合规自检
│   ├── feishu/
│   │   └── bot.py                # 【Feishu】WS 长连接 + 命令调度 + 卡片/图片发送
│   └── predict/                  # 【预测引擎】
│       ├── candidate_pool.py     # 候选池（板块10日→成分股→全市场活跃→涨停）
│       ├── scoring.py            # 规则打分（技术/资金/情绪）
│       ├── strategy.py           # 量比<2.0 过滤 + TopN
│       ├── news.py               # 消息面（同舟 新闻/公告/事件 + 东财龙虎榜）
│       ├── judge.py              # 【Prompt】LLM 综合研判（DeepSeek）
│       ├── llm.py                # DeepSeek 封装（OpenAI SDK）
│       ├── daily.py              # 每日预测主流程
│       ├── track.py              # 结算/评估/数据积累（Memory+Evaluator）
│       ├── backtest.py           # 回测框架（交易日历/前向收益/基准）
│       ├── cache.py              # MCP 结果 SQLite 缓存
│       ├── alt_data.py           # ego 兜底数据（板块/K线/龙虎榜/指数）
│       └── sweep.py              # 策略扫描
├── backtests/                    # 【Evaluation/Backtest】独立回测（不碰策略）
│   ├── h003_backtest.py / _v2.py # H003 假设回测
│   ├── backtest_all.py           # H001–H016 批量回测
│   └── fetch_h003_v2/v3.py       # 回测数据抓取
├── factors/
│   ├── README.md                 # 因子生命周期（隔离未接入）
│   └── investment_factors.yaml   # 46 个工程因子（R28 隔离）
├── data/                         # 【Database / Data】
│   ├── a_share.db / mcp_cache.db / ego_kline.db
│   ├── recommendation_history.json  # 推荐数据积累+分析（v1.1）
│   ├── h003_data*.json / h003_index.json / factor_probe.json
│   └── mcp_tools_full.json       # MCP 工具清单快照（158 工具）
├── reports/                      # 每日报告 JSON+HTML（2026-08-09 ~ 至今）
├── static/echarts.min.js         # 本地图表库
├── scripts/                      # 启动/截图脚本
│   ├── run_bot.sh / run_server.sh / run_settle.sh / run_caffeinate.sh（含电源感知）
│   ├── start.sh / stop.sh / start_server.sh / run_review.sh
│   └── report_screenshot.mjs     # 报告→手机长图（playwright）
├── docs/                         # 工程文档（REQ/ARCH/ENG/CHG/ISSUE/工程记忆/发布说明等）
├── logs/                         # 运行日志
└── output/report_img/            # 报告截图输出
```

**模块标注**：Agent=无独立类，见第三节；Workflow=app/workflow.py；API=app/server.py（FastAPI）；Tool=数据采集/预测函数；MCP=app/mcp_client.py + app/predict/cache.py；Memory=SQLite+JSON；Database=见第十三节；Scheduler=launchd（com.ashare.settle）+ bot 内线程；Feishu=app/feishu/bot.py；Data=data/；Prompt=app/predict/judge.py（硬编码）+ execution_rules.yaml；Evaluation/Backtest=app/predict/track.py + backtests/；Configuration=config/config.yaml + .env

---

## 三、当前 Agent 架构

**当前项目没有"Agent 类"抽象**，没有 Agent 注册表、没有 Agent 间消息协议。实现为**函数模块 + 显式调用**。可映射为以下"功能代理"：

| 功能代理 | 文件位置 | 职责 | 输入 | 输出 | 使用LLM | 使用Tool/MCP | 调其他代理 | 有Memory | 独立Prompt | Retry/Loop | 参与推荐 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Feishu Bot（入口/调度） | app/feishu/bot.py | 收命令、去重、线程调度、发卡片/图片 | 飞书消息文本 | 飞书卡片/图片/文本 | 无 | 飞书 API | 调 workflow / track | 内存去重表(_PROCESSED_MSG) | 无（命令关键词） | 回复重试2次 | 否 |
| Workflow Orchestrator | app/workflow.py | 复盘+预测+跟踪编排、报告复用、合规自检 | 无/force | 报告 dict + JSON/HTML 落盘 | 无（仅预读规则） | 调 Collector / predict / track | 调全部下游 | SQLite+JSON（读写报告） | 无 | 报告复用检查 | 否（编排） |
| Collector（数据采集） | app/collector.py + ego_scraper.py | 抓指数/情绪/板块/资金/龙虎榜并交叉验证 | 交易日 | market_index/emotion/sector/capital | 无 | ego-browser + MCP(通达信/同舟) | 调 validator | 无（临时） | 无 | MCP 单次失败降级 | 否（喂给复盘） |
| CandidatePool（候选池） | app/predict/candidate_pool.py | 7-10日强势板块→板块内个股+全市场活跃+涨停 | 交易日 | candidates 列表 | 无 | ego(EgoBoardData) + 同舟MCP | 调 alt_data | 无 | 无 | ego失败→MCP兜底 | 是（候选） |
| Scorer（打分） | app/predict/scoring.py | 规则打分（个股/资金/换手/板块/情绪/质量） | 候选 dict | 分数+因子 | 无 | 无 | 无 | 无 | 无 | 无 | 是（排序） |
| Strategy（量比过滤） | app/predict/strategy.py | 量比<2.0 过滤 → TopN | 候选池 | 前5候选 | 无 | 同舟K线 | 调 scoring | 无 | 无 | 无 | 是（筛选） |
| NewsScanner（消息面） | app/predict/news.py | 新闻/公告/事件/龙虎榜扫描+情绪初筛 | 股票代码 | news items | 无（关键词初筛） | 同舟 doc_search + 东财龙虎榜 | 调 alt_data | 无 | 无 | 单次失败降级 | 是（喂LLM） |
| Judge（LLM研判） | app/predict/judge.py + llm.py | 基于候选+消息面+市场环境选3只+买卖计划 | 候选/消息/市场 | targets JSON | **DeepSeek V4 Flash** | 无（纯LLM） | 无 | 无 | **SYSTEM 硬编码** | 无（LLM失败→规则回退） | **是（最终3只）** |
| Tracker（结算/评估/积累） | app/predict/track.py | 次日结算、命中率、数据积累文件 | 预测记录+次日行情 | 结算结果/统计/历史JSON | 无 | 同舟/ego K线 | 无 | **SQLite+JSON** | 无 | 结算幂等 | 否（评估） |
| Validator（交叉验证） | app/validator.py | 双源容差比对 | 两个值+容差 | 验证结果 | 无 | 无 | 无 | 无 | 无 | 无 | 否 |
| Rules（规则/合规） | app/rules.py | 预读规则+合规自检 | 报告上下文 | compliance | 无 | 无 | 无 | 读 execution_rules.yaml | 无 | 无 | 否 |
| Report（输出） | report_builder.py + html_report.py + server.py | JSON/HTML/图片组装与提供 | 报告 dict | JSON/HTML/PNG | 无 | 无 | 无 | 读 reports/ | 无 | 无 | 否 |

**调用关系（真实）**：
```
Feishu Bot (on_message)
 ├── "复盘" → _review_and_reply → Workflow.run_review
 ├── "预测" → _predict_and_reply → Workflow.run_review
 ├── "结算" → _settle_and_reply → Tracker.settle_pending
 └── "规则" → load_rules
Workflow.run_review
 ├── Rules.preload_rules (R30)
 ├── Collector.collect → Validator
 ├── Tracker.settle_pending（先结算昨日）
 ├── predict.daily.predict
 │    ├── Backtest.trading_days
 │    ├── CandidatePool.build → Scorer / Strategy
 │    ├── NewsScanner.scan（+ alt_data.recent_lhb）
 │    └── Judge.judge（DeepSeek）→ 回退规则候选
 ├── Tracker.record_prediction / export_history
 ├── Rules.check_rules（合规自检【当前有 bug，见附录A】）
 └── Storage.save_report + html_report / server
```

---

## 四、完整业务执行流程（真实）

```
用户飞书发「复盘」（或「预测」「结算」「规则」）
  ↓
app/feishu/bot.py on_message（lark WS 长连接事件）
  ├─ 消息去重（_dedup：消息ID+类型，30分钟窗口）
  ├─ 识别命令关键词：复盘/收盘 → 复盘线程；预测/标的 → 预测线程；结算/命中率 → 结算线程；规则 → 直接回复
  ↓ 后台线程（立即返回，避免 WS 重投）
_full_report_and_reply
  ├─ 回复确认文本"收到！正在生成完整报告…"
  ├─ run_review(include_prediction=True)
  │    ├─ preload_rules（R30）
  │    ├─ 复用检查：同日已有完整报告(板块+预测)则复用（tracking 重算）
  │    ├─ 否则：
  │    │    ├─ Collector.collect → build_report（市场/情绪/板块/资金/龙虎榜）
  │    │    ├─ Tracker.settle_pending（自动结算昨日推荐，收盘-收盘口径）
  │    │    ├─ predict.daily.predict（见第六节）
  │    │    ├─ Tracker.record_prediction + export_history
  │    │    └─ _attach_compliance（【当前 bug：compliance 生成失败，见附录A】）
  │    └─ Storage.save_report（SQLite + reports/{date}.json）+ html_report 生成
  ├─ 发送主卡片（市场+昨日推荐复盘+今日3只+链接）
  ├─ 发送报告图片版（生成手机长图3段→飞书上传统计→新消息直发聊天）
  └─ 记录日志
```
**分支**：
- 「结算」：Tracker.settle_pending → 若有待结算则回填次日开盘/收盘 → 回复结算卡片
- 「规则」：load_rules → 回复规则清单
- 同日重复复盘：报告完整则复用（秒回），不重复采集/预测
- LLM 研判失败/空：回退到规则候选前3（保证 3 只）
- MCP 不可用：采集降级为 ego 单源（validator 标注"验证源缺失"）；候选池 ego 优先

---

## 五、数据来源（真实）

| 数据类型 | 来源 | 方式 | 调用位置 | 获取数据 | 格式 | 是否保存 | 保存位置 | 历史 |
|---|---|---|---|---|---|---|---|---|
| A股指数行情 | 东财 push2/push2delay ulist API | ego-browser fetch | app/ego_scraper.py build_tasks | 上证/深成/创业板/科创50 收盘/涨跌/成交额 | JSON | 是 | reports/{date}.json + SQLite daily_reports | 按日 |
| 涨停/跌停/炸板 | 东财 push2ex TopicPool API | ego-browser fetch | ego_scraper.py | 涨停数/跌停数/炸板数/连板高度 | JSON | 是 | reports JSON | 按日 |
| 行业/概念板块 | 东财 push2delay clist | ego-browser fetch（多域名兜底） | ego_scraper.py / alt_data EgoBoardData | 板块涨跌幅/龙头股/10日涨幅 | JSON | 是 | reports JSON + data/h003_data*.json | 按日/回测 |
| 个股K线 | 同舟 get_kline_series（主）；东财 push2delay（ego 兜底） | MCP / ego | app/predict/backtest.py / strategy.py / daily.py | OHLCV/量比 | JSON | 是（缓存） | data/mcp_cache.db / data/ego_kline.db | 回测数据文件 |
| 新闻/公告/事件 | 同舟 doc_search（search_company_news/announcements/events） | MCP | app/predict/news.py | 标题/摘要/来源/情绪 | JSON | 是 | reports JSON（当日） | 近5日窗口 |
| 龙虎榜 | 东财 datacenter-web API | ego-browser fetch | app/predict/alt_data.py recent_lhb | 上榜股/净买入/原因 | JSON | 是 | data/ego_kline.db (ego_lhb) | 缓存 |
| 主力资金流 | 东财 fflow kline API | ego-browser fetch | ego_scraper.py | 上证主力/大单净流入 | JSON | 是 | reports JSON | 按日 |
| 北向资金 | 东财 kamt API | ego-browser fetch | ego_scraper.py | 北向成交额（净买入不可获取） | JSON | 是 | reports JSON | 按日 |
| 财务数据(PE/PB) | 同舟 rank_securities / screen_stocks 附带字段 | MCP | candidate_pool.py | pe_ttm/pb/market_cap | JSON | 否（仅当次） | — | 无独立财务库 |
| MCP 工具集 | WorkBuddy 聚合代理（通达信/同舟/Wind，158 工具） | MCP streamable-http | app/mcp_client.py（自动发现端口/token） | 行情/资金/研究/新闻等 | JSON | 缓存 | data/mcp_cache.db | 按 key |

---

## 六、股票推荐逻辑（真实链路）

```
trading_days(指数K线取最近交易日)
  ↓
CandidatePool.build(t)
  ├─ _top_sectors：东财行业板块 10日涨幅 Top8（ego 优先，失败→同舟 20日资金流兜底）
  ├─ 每板块 _sector_stocks：ego 板块成分股（涨幅 Top6）→ 同舟 rank_securities 兜底
  ├─ _top_amount：同舟 rank_securities 成交额 Top25（全市场资金活跃）
  ├─ _limit_up：同舟 screen_stocks 涨停（最多50）
  └─ _is_valid 过滤：剔除 ST/退市、北交所(920)、|涨跌幅|>21%、成交额<1亿
  ↓
Scorer.score_pool（规则打分 0-100）
  ├─ 个股强度(当日涨幅 0-30, 7-8%最优/追高递减)
  ├─ 资金活跃(成交额 0-25, 1-4亿最优/拥挤扣分)
  ├─ 换手率(0-15, 5-15%最优)
  ├─ 板块(0-20, 板块名次线性)
  ├─ 情绪(涨停-5/其他+5)
  └─ 质量扣分(亏损PE<0、小市值、ST)
  ↓
Strategy.select（取打分前10 → 拉K线算量比 → 量比>2.0剔除 → 按分数排序取 Top5）
  ↓
NewsScanner.scan（每只候选：新闻/公告/事件 + 龙虎榜，情绪关键词初筛）
  ↓
Judge.judge（DeepSeek V4 Flash，SYSTEM 硬编码 prompt）
  ├─ 输入：候选5只(评分/逻辑) + 消息面 + 市场环境(指数/涨停/连板)
  ├─ 输出：market_view + targets[3]（code/name/reason/risk/buy_point/stop_loss/sell_target/hold/sentiment_score/confidence）
  ├─ 白名单：只保留候选池内的 code（防幻觉）
  └─ 失败/空 → 回退规则候选前3
  ↓
回填 参考买入价(收盘)/量比/评分明细 + 买卖计划缺省兜底(-3%止损/+3%目标/T+1)
  ↓
最终 3 只（写入 reports JSON + SQLite predictions）
```
- 股票池来源：东财行业板块10日涨幅Top8 的成分股 + 全市场成交额Top25 + 涨停股（去重，约 60-110 只）
- 初筛条件：剔除 ST/北交所/异常涨跌/低成交额
- 技术指标：当日涨幅、换手率、量比（K线计算）、10日板块强度
- 基本面：仅 PE(亏损扣分)/市值 阈值（同舟附带字段）
- 市场环境判断：LLM 基于指数/涨停/连板（market_view）
- LLM 角色：在规则候选内做最终 3 只选择 + 买卖计划 + 风险（白名单约束）
- 程序规则角色：候选池、打分、量比过滤、白名单、缺省兜底
- 最终几只：3 只；推荐字段：code/name/reason/risk/buy_point/stop_loss/sell_target/hold/sentiment_score/confidence/参考买入价/量比/评分明细/板块
- 人工干预：无在线人工；参数集中在 candidate_pool/scoring/strategy 常量（改代码才变）

---

## 七、复盘逻辑（真实）

复盘结论由 **Collector 采集 → report_builder 组装** 形成，**程序计算为主、LLM 仅用于预测段的市场判断**（market_view），复盘本身无 LLM 推理。

- 程序计算：指数涨跌幅/成交额（东财+通达信双源验证）、涨停/跌停/炸板/最高连板（东财池）、板块涨幅与龙头（东财 clist）、主力资金流/北向/龙虎榜（东财 API）、情绪数值
- 交叉验证：validator 双源容差（指数收盘、成交额；情绪涨停/跌停与通达信 screener 比对）
- 数据完整性：拿不到标"数据不可获取"（如北向净买入）
- LLM 推理：仅 `prediction.market_view`（DeepSeek 基于指数/涨停/连板一句话市场判断）；消息面情绪为关键词初筛（非LLM）
- 输出结构：market_index / emotion / sector_rank / capital_flow / prediction / tracking / source / meta / compliance【compliance 当前生成失败，见附录A】

---

## 八、飞书入口与输出（真实）

1. 触发：飞书文本消息（lark WS 长连接 `lark.ws.Client`，app/feishu/bot.py main()）
2. Webhook：**无 HTTP Webhook 收消息**（WS 长连接）；有本地 FastAPI `POST /feishu/event` 路由但主入口是 WS
3. 定时任务：launchd `com.ashare.settle` 工作日 15:30 执行 `run_cli.py track auto`（自动结算）；无定时自动复盘
4. 谁负责触发：用户手动发命令（复盘/预测/结算/规则）+ 15:30 定时结算
5. Task 形式：无 Task 对象/队列；每个命令 → 后台线程 → 同步执行（threading.Thread）
6. 结果发送：主卡片（reply 交互卡片）+ 报告图片版（新消息，3张长图）+ HTML 链接
7. 消息格式：interactive 卡片（header+markdown+action按钮+img）；文本确认；markdown 明细
8. 用户追问：支持有限——同一消息可多次触发；无多轮对话状态机（每次独立）
9. 会话状态：**无持久会话状态**；仅内存去重表（30分钟窗口）

```
飞书(用户) ──WS事件──> bot.py on_message ──线程──> workflow/collector/predict/track
                                                          ↓
飞书(用户) <─卡片+图片+链接── bot.py reply_card / _send_feishu_message
```

---

## 九、当前 Memory

| 类型 | 存在 | 说明 |
|---|---|---|
| 短期记忆 | 部分 | 内存去重表 `_PROCESSED_MSG/_REPLIED_MSG`（30分钟窗口）；无对话上下文记忆 |
| 长期记忆 | 部分 | SQLite + JSON 文件（见下） |
| Vector DB | **无** | 未实现 |
| 数据库记忆 | 有 | `data/a_share.db`：predictions（每日预测，date唯一/upsert）、prediction_results（每日结算：昨收/今收/收益/收盘收益）、daily_reports（每日完整报告 JSON） |
| 文件记忆 | 有 | `reports/{date}.json`（完整报告）、`data/recommendation_history.json`（全部已结算记录+日/累计/分股/按月分析）、`data/mcp_cache.db`、`data/ego_kline.db` |
| 历史复盘 | 有 | reports/*.json（2026-08-09 起按日） |
| 历史推荐 | 有 | predictions 表 + recommendation_history.json |
| 历史市场状态 | 部分 | 报告 JSON 含当日指数/情绪/板块/资金；无结构化"市场状态序列"库 |

- 写入时机：每次复盘/预测（save_report、record_prediction）；每次结算（settle → export_history 自动更新）
- 读取时机：复盘复用检查（load_report）、统计（stats）、图片/HTML（server load_report）
- 数据结构与位置：见第十三节数据库结构 + recommendation_history.json 结构

---

## 十、当前 Evaluation / Backtest

**存在，但以"模拟盘结算 + 独立回测"形式，非框架化 Evaluator。**

已自动记录（Tracker.settle / prediction_results 表）：
- 每只：日期、代码、名称、**昨收(买入价)**、**今收(卖出价)**、收益率(%)、收盘收益率
- 汇总（Tracker.stats / export_history）：命中率、平均/中位收益、最佳/最差、按日/按月/分股胜率与收益

**未记录/未实现**：
- 次日开盘/最高/最低价（仅收昨收→今收）
- 与指数/行业比较（积累文件无指数基准对比；独立回测有指数基准）
- "是否达到预期"（无目标达成判定）
- 推荐原因与最终结果之间的归因分析

Backtest：`backtests/` 独立回测（H003 两轮、H001–H016 批量），使用东财K线/同舟数据，含股票池等权与指数基准、t检验；**不接入每日流程**（R26 部分实现：滚动回测未显式化）。

---

## 十一、自我改进

| 项 | 存在 | 说明 |
|---|---|---|
| 历史经验总结 | 有 | docs/问题与修复记录.md（I01–I31）+ 变更与回滚记录（C01–C29）+ 假设库/因子库（人工整理） |
| 自动分析失败 | **无** | 无自动失败归因；仅结算统计 |
| 修改 Prompt | **无**（自动） | judge.SYSTEM 硬编码；需人工改代码 |
| 修改策略 | **无**（自动） | scoring/strategy 参数硬编码常量 |
| 修改 Workflow/Agent | **无**（自动） | 人工开发 |
| 自动生成新策略 | **无** | 假设/因子库隔离（factors/），R28 需人工批准才接入 |
| A/B Test | **无** | |
| Benchmark | 部分 | 独立回测有指数/股票池基准；每日流程无 |
| 版本控制 | 有 | git（tag: v1.0.0/v1.0.1/v1.1.0） |
| Rollback | 有 | docs/变更与回滚记录.md（git revert + 服务重启/数据回滚手册） |

---

## 十二、配置与 Prompt（真实位置）

| 位置 | 内容 | 硬编码/可配置 |
|---|---|---|
| app/predict/judge.py `SYSTEM` | LLM 研判 system prompt（研究员角色/铁律/买卖计划要求） | **硬编码**（改代码） |
| app/predict/llm.py `POSITIVE_KW/NEGATIVE_KW` | 消息情绪关键词 | **硬编码** |
| app/predict/candidate_pool.py 常量 | MIN_AMOUNT/MAX_CHANGE/TOP_SECTOR_N/PER_SECTOR_N/TOP_AMOUNT_N | **硬编码** |
| app/predict/scoring.py | 打分权重/分段 | **硬编码** |
| app/predict/strategy.py `MAX_VOL_RATIO/TOP_PRE` | 量比阈值 | **硬编码** |
| execution_rules.yaml | R01–R30 规则（含优先级/状态） | **可配置**（YAML，运行前预读 R30） |
| config/config.yaml | model/mcp/proxy/web/feishu/backtest | **可配置** |
| .env | DEEPSEEK_API_KEY/FEISHU_APP_ID/SECRET/MCP token | **可配置**（敏感） |
| factors/investment_factors.yaml | 因子库（隔离，未接入） | 可配置（人工维护） |

---

## 十三、数据库结构（SQLite，真实）

`data/a_share.db`
- **daily_reports**(date PK, report_json TEXT, created_at) —— 每日完整复盘+预测 JSON
- **predictions**(id, date, targets JSON, created_at) —— 每日预测（date 唯一，当日锁定复用）；targets 含 code/name/reason/risk/buy_point/stop_loss/sell_target/hold/参考买入价/量比/评分明细/板块
- **prediction_results**(id, date, target_code, target_name, buy_price(昨收), sell_price(今收), ret(今收/昨收-1), status, created_at, sell_close, ret_close) —— 每日结算（评估）

`data/mcp_cache.db`
- **mcp_cache**(key, result, created_at) —— MCP 调用结果缓存（sha1 key）

`data/ego_kline.db`
- **ego_kline**(code, date, open) —— 东财K线兜底缓存（开盘价）
- **ego_lhb**(code, info) —— 龙虎榜缓存

`data/recommendation_history.json` —— 推荐积累+分析（records/daily/cumulative/by_stock/by_month）

---

## 十四、测试现状

| 类型 | 存在 | 说明 |
|---|---|---|
| Unit Test | **无** | 无 tests/ 目录、无 pytest |
| Integration Test | **无** | |
| Backtest | 有 | backtests/（H003、H001–H016；含基准与 t 检验） |
| Evaluation Test | 部分 | 模拟盘结算（track stats/history）+ 手动验证 |
| End-to-End | 人工 | 飞书实测（复盘/预测/结算/图片） |

---

## 十五、RSI 接入所需信息（逐条）

1. **Task 入口**：飞书 WS 消息 → `app/feishu/bot.py on_message`；命令映射到线程。若 RSI 要接管 Task，可在 on_message 处加适配（把"复盘/预测"包装成 RSI Task）。
2. **Orchestrator/Workflow**：`app/workflow.py run_review`（含复用、结算、预测、合规）；是接入 RSI 的核心缝点。
3. **可直接作为 RSI Agent**：**无现成 Agent 类**。功能代理（CandidatePool/Scorer/Strategy/NewsScanner/Judge/Tracker）可包装成 Agent；Judge 是最贴近"LLM Agent"的（有 SYSTEM prompt、白名单、回退）。
4. **需要 Adapter**：几乎全部。需要把"函数调用"适配成 RSI 的 Agent 接口（输入/输出 schema、工具声明、记忆读写）。
5. **Tool 接入**：现有"工具"= 数据函数（ego 抓取、MCP 调用、K线、龙虎榜）。RSI 若用 MCP 协议，可直接复用 `app/mcp_client.py`（158 工具，WorkBuddy 聚合代理）；其余需包成 RSI Tool。
6. **MCP 接入**：已有现成 MCP client（streamable-http，自动发现端口/token）+ SQLite 缓存；可直接作为 RSI 的 MCP 通道。
7. **Memory 接入**：现有 SQLite（predictions/prediction_results/daily_reports）+ recommendation_history.json + reports/*.json。RSI 若要长期记忆/经验，可挂在这套存储上（或适配）。
8. **Evaluator 是否存在**：有雏形（Tracker.settle + stats + export_history：命中率/收益/按日/分股/按月），但**不是框架化 Evaluator**，无开盘/最高/最低/指数比较。
9. **Market Result 可否作 Evaluator**：可以——`prediction_results` 含昨收/今收/收益，已是"推荐→真实结果"的配对数据，可直接作为 RSI Evaluator 的输入。
10. **推荐结果可否形成 Experience**：可以——predictions + prediction_results + recommendation_history.json（每期3只+结果+统计）可形成 Experience 样本（含 reason/risk 文本字段）。
11. **可作为 RSI 训练/学习反馈的数据**：推荐字段（评分因子/量比/消息情绪/板块）+ 结果（收益率/命中）+ 分股/按月表现；候选池打分明细（rule_candidates）也可作特征。
12. **RSI 应接入哪一层**：建议在 `app/workflow.py run_review` 之上/之内做 RSI Orchestrator 包装（Task=每日复盘+预测；或把"复盘"与"预测"拆为两个 Task）；保留现有采集/校验/结算为 RSI Tools/Evaluator。
13. **应保留的现有代码**：数据采集/交叉验证（collector/validator/ego_scraper/mcp_client）、候选池/打分/量比（candidate_pool/scoring/strategy）、结算/积累（track）、报告输出（report_builder/html/server/bot 发送）、规则系统（rules）、独立回测（backtests）。
14. **需要增加 Interface 的地方**：Agent/Task 接口（on_message→RSI Task）、Tool 统一接口（数据函数→RSI Tool schema）、Memory 接口（SQLite/JSON→RSI Memory）、Evaluator 接口（track→RSI Evaluator）。
15. **架构冲突点**：① 无 Task/Agent 抽象（需整体适配）；② 报告复用/同日锁定逻辑与"每日新任务"可能冲突；③ 线程调度（无队列/幂等任务表）；④ compliance 自检 bug（附录A）；⑤ judge 硬编码 prompt（RSI 若要策略演化需外置）；⑥ 因子/假设库目前隔离，RSI 接入需定义如何引用。
16. **信息不足/无法判断**：RSI Framework 本身的接口（Task/Agent/Evaluator/Memory/Strategy 的精确契约）【未知】——需另一 AI 提供 RSI 框架接口后再定 Adapter 细节；东财接口限流策略的具体参数【未知】；模型实际成本/延迟【未知】。

---

## 十六、当前项目 → RSI Framework 映射图

```
Current A-Share Project
        ↓
[Feishu on_message —— Task 入口（需要 Adapter）]
        ↓
[Workflow.run_review —— Orchestrator（可包装为 RSI Orchestrator）]
        ↓
[Collector / CandidatePool / Scorer / Strategy / NewsScanner / Judge / Tracker —— 功能代理（需要 Agent Adapter）]
        ↓
[ego-scraper / mcp_client / alt_data —— Data & Tools / MCP（可直接复用）]
        ↓
[Recommendation（3只+买卖计划）]
        ↓
[Feishu 卡片 + 图片 + HTML —— Output]
        ↓
[Tracker.settle / stats / recommendation_history.json —— 已有 Evaluator 雏形 + Memory]
        ↓
[backtests/ —— 独立回测 / Benchmark（雏形）]

映射到 RSI Framework：
        ↓
RSI Task ← [已有 Feishu 命令入口；需要 Adapter]
        ↓
RSI Orchestrator ← [已有 workflow.run_review；需要 Adapter/包装]
        ↓
RSI Planner ← [尚无；需要新增；可基于 Judge/规则排序]
        ↓
RSI Agents ← [CandidatePool/Scorer/Strategy/NewsScanner/Judge —— 需要 Agent Adapter]
        ↓
RSI Tools / MCP ← [已有 ego_scraper/mcp_client(158工具)/alt_data —— 可直接复用；需统一接口]
        ↓
RSI Evaluator ← [已有 Tracker(stats/history) 雏形；可升级为框架 Evaluator；【需要新增接口】]
        ↓
RSI Memory ← [已有 SQLite + recommendation_history.json + reports/*.json；【需要新增接口】]
        ↓
RSI Engine / Strategy ← [已有规则/打分/量比策略（硬编码）；factors/ 因子库隔离待接入]
        ↓
RSI Benchmark ← [已有 backtests/（指数/股票池基准，独立）；每日流程未接入]
        ↓
RSI Promotion ← [尚无自动提升；有 R28 人工批准机制 + 版本 tag/回滚]

标注：[已有]＝可直接复用；[需要 Adapter]＝现有实现需包装；[需要新增]＝当前不存在；[无法判断]＝依赖 RSI 框架接口
```

---

## 附录 A：已发现的问题（供接入时注意）

1. **合规自检 bug**：`app/workflow.py _attach_compliance` 内 `"rules_preloaded": bool(pre.get("ok"))` 引用了未定义的 `pre`（它在 `run_review` 局部作用域）。结果：**所有报告 `compliance.summary` 均为 `自检失败:name 'pre' is not defined`**（已核验 2026-08-12/13/14 三份报告）。R30 预读本身正常，但合规自检结果未正确写入。
2. 同日预测锁定复用依赖 SQLite `predictions.date` 唯一（upsert）；并发触发可能竞态（当前单线程启动风险低）。
3. 东财接口偶发限流（已有 push2delay 备用域名缓解）；R29 要求变更日志，未自动化强制（流程约定）。
4. 图片直发依赖飞书 `im:resource` 权限（已开通并发布）。

---

## 附录 B：交付物核对

- 本文件已严格基于实际代码生成；所有"无/未实现"均为代码核查结论，未做假设补充。
- 关于 RSI Framework 的精确接口（Task/Agent/Evaluator/Memory/Strategy 契约）标记【未知】，需由另一个 AI 结合 RSI 框架补充。
