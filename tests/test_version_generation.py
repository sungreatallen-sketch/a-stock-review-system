"""
版本生成与同日复用测试（路径隔离版）

测试要求：
- 调用实际 app.predict.daily.predict 函数
- Mock 外部依赖（行情、LLM、新闻、龙虎榜等），不 mock 预测入口及复用判断
- 使用临时数据库和固定交易日历
- 验证版本生成、同日复用、历史记录保持

路径隔离（本版重点）：
- 统一 patch 三个路径引用到同一临时目录：
  1) app.config.paths          —— predict() 内 `from ..config import paths as _paths`
     在调用时解析，决定 Tracker 目录（此前只 patch app.predict.daily.paths 覆盖不到）
  2) app.predict.daily.paths   —— 模块级 `from ..config import paths`，_market_context 使用
  3) app.predict.alt_data.paths—— 模块级绑定，recent_lhb / EgoOpenPrices 使用
- 包装 sqlite3.connect：任何连接目标不在本次临时目录内立即失败（禁止连接项目数据库）；
  同时记录连接目标，验证真实 predict 读取的是临时数据库
- 新生成分支：断言候选池构建、策略选择被调用（防止历史复用误通过）
- 同日复用：断言不重新选股、不调用 LLM

本文件实际测试数量：3
- TestVersionGeneration.test_new_prediction_generates_v12
- TestVersionReuse.test_reuse_v11_prediction_same_day
- TestVersionReuse.test_reuse_prediction_without_version

注意：
- daily.predict 的复用路径允许补齐参考价、写入执行窗口
- 需要准备完整历史样本，验证版本和标的不变（不要求所有字段完全不变）
"""
import sys
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote
from unittest.mock import MagicMock, patch

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.predict.strategy import STRATEGY_VERSION

_REAL_CONNECT = sqlite3.connect


def _connect_target(database):
    """解析 sqlite3.connect 的落盘目标；内存库返回 None。"""
    if database is None:
        return None
    text = str(database)
    if text == ":memory:":
        return None
    if text.startswith("file:"):
        raw = text[5:].split("?", 1)[0]
        text = unquote(raw)
        if not text or text == ":memory:":
            return None
    p = Path(text).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    return p.resolve()


@contextmanager
def isolated_env(tmp_path):
    """所有路径引用与 SQLite 连接强制指向临时目录。

    产出 (path_map, connect_log)：
    - path_map: {"data": <临时数据目录>, "reports": <临时报告目录>}
    - connect_log: 本次允许的落盘连接目标列表（均已确认在临时目录内）

    任何越界连接立即 AssertionError，不会触达项目/生产数据库。
    """
    tmp_resolved = Path(tmp_path).resolve()
    reports_path = tmp_resolved / "reports"
    reports_path.mkdir(exist_ok=True)
    path_map = {"data": tmp_resolved, "reports": reports_path}
    connect_log = []

    def guarded_connect(database, *args, **kwargs):
        target = _connect_target(database)
        if target is not None:
            if target != tmp_resolved and tmp_resolved not in target.parents:
                raise AssertionError(
                    f"数据库连接越界：{database!r} 解析为 {target}，"
                    f"不在本次临时目录 {tmp_resolved} 内"
                )
            connect_log.append(target)
        return _REAL_CONNECT(database, *args, **kwargs)

    with patch("app.config.paths", return_value=dict(path_map)), \
         patch("app.predict.daily.paths", return_value=dict(path_map)), \
         patch("app.predict.alt_data.paths", return_value=dict(path_map)), \
         patch("sqlite3.connect", new=guarded_connect):
        yield path_map, connect_log


class TestVersionGeneration:
    """测试版本生成：调用实际daily.predict，验证新预测包含v1.2"""

    def test_new_prediction_generates_v12(self):
        """新预测应生成v1.2版本（无历史记录时）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with isolated_env(tmpdir) as (path_map, connect_log):
                from app.predict.track import Tracker
                tracker = Tracker(path_map["data"])

                # 确保目标日期无记录
                assert tracker.get_prediction("2026-09-21") is None

                # Mock 所有外部依赖（含龙虎榜 recent_lhb，禁止真实外部调用）
                with patch('app.predict.daily.Backtest') as MockBacktest, \
                     patch('app.predict.daily.CandidatePool') as MockCandidatePool, \
                     patch('app.predict.daily.Strategy') as MockStrategy, \
                     patch('app.predict.daily.NewsScanner') as MockNewsScanner, \
                     patch('app.predict.daily.judge') as mock_judge, \
                     patch('app.predict.daily._kline_lookup_factory') as mock_kline_factory, \
                     patch('app.predict.alt_data.recent_lhb') as mock_lhb:

                    # 配置Backtest返回固定交易日历
                    mock_bt = MagicMock()
                    mock_bt.trading_days.return_value = ["2026-09-20", "2026-09-21"]
                    MockBacktest.return_value = mock_bt

                    # 配置CandidatePool返回空候选池
                    mock_pool = MagicMock()
                    mock_pool.build.return_value = {
                        "candidates": [],
                        "sectors": [],
                        "meta": {"sector_window": "测试", "top_sectors": []}
                    }
                    MockCandidatePool.return_value = mock_pool

                    # 配置Strategy返回空top5
                    mock_strat = MagicMock()
                    mock_strat.select.return_value = []
                    mock_strat.filtered = []
                    MockStrategy.return_value = mock_strat

                    # 配置NewsScanner
                    mock_scanner = MagicMock()
                    mock_scanner.scan.return_value = {"sentiment": "neutral", "summary": "测试"}
                    MockNewsScanner.return_value = mock_scanner

                    # 配置judge返回空结果
                    mock_judge.return_value = {"market_view": "测试", "raw_llm_output": ""}

                    # 配置kline_lookup
                    mock_kline_factory.return_value = lambda ticker, end_date: None

                    # 配置龙虎榜（空结果，不触发真实抓取）
                    mock_lhb.return_value = {}

                    # 调用实际的predict函数
                    from app.predict.daily import predict
                    mock_cached = MagicMock()
                    result = predict(mock_cached, target_date="2026-09-21", use_llm=False)

                    # 验证结果包含v1.2版本
                    assert STRATEGY_VERSION == "v1.2", \
                        f"策略版本应为v1.2，实际={STRATEGY_VERSION}"
                    assert result.get("strategy_version") == "v1.2", \
                        f"新预测版本应为v1.2，实际={result.get('strategy_version')}"

                    # 确实进入新生成分支：候选构建与策略选择都被调用（历史复用不会走到这里）
                    MockCandidatePool.return_value.build.assert_called_once()
                    MockStrategy.return_value.select.assert_called_once()

                    # use_llm=False 不允许调用 LLM
                    mock_judge.assert_not_called()

                    # 真实 predict 读取的是临时数据库（连接目标全在临时目录内，守卫已强制）
                    assert connect_log, "predict 过程应产生 SQLite 连接记录"
                    tmp_db = (path_map["data"] / "a_share.db").resolve()
                    assert tmp_db in connect_log, \
                        f"真实 predict 应读取临时数据库 {tmp_db}，实际连接={connect_log}"

                    # 模拟run_cli.py的行为：保存预测到数据库
                    tracker.record_prediction(result)

                    # 验证数据库中的记录（新开 Tracker，仍指向临时目录）
                    stored = Tracker(path_map["data"]).get_prediction("2026-09-21")
                    assert stored is not None, "预测应已保存到数据库"
                    assert stored.get("strategy_version") == "v1.2", \
                        f"数据库中版本应为v1.2，实际={stored.get('strategy_version')}"


class TestVersionReuse:
    """测试同日复用：已有预测时应复用原记录"""

    def test_reuse_v11_prediction_same_day(self):
        """同日已有v1.1预测时，应复用原记录，版本不变，标的不变，不重新选股、不调用LLM"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with isolated_env(tmpdir) as (path_map, connect_log):
                from app.predict.track import Tracker
                tracker = Tracker(path_map["data"])

                # 预存v1.1历史记录（包含完整字段，模拟实际预测输出）
                old_prediction = {
                    "date": "2026-09-21",
                    "strategy_version": "v1.1",
                    "settlement_rule": "T+1开盘买入，T+2收盘卖出",
                    "strategy": "7-10日强势板块 + 个股强势 + 资金活跃 + 量比<2.0过滤 + 消息面 + LLM研判",
                    "filtered_out": [],
                    "sector_window": "10日(ego)",
                    "market_view": "测试市场观点",
                    "targets": [
                        {
                            "ticker": "000001",
                            "name": "历史股票A",
                            "code": "000001",
                            "score": 55.0,
                            "factors": {"板块": 6.0, "个股强度": 20.0, "资金活跃": 15.0, "换手率": 10.0, "情绪": 5.0, "质量扣分": 0.0},
                            "逻辑": "历史逻辑",
                            "参考买入价(收盘)": 10.0,
                            "量比": 1.5,
                            "hold": "T+1开盘买入，T+2收盘卖出",
                        }
                    ],
                    "rule_candidates": [],
                    "news": {},
                    "news_has_lhb": False,
                    "top_sectors": ["历史板块"],
                    "candidate_count": 1,
                    "raw_llm_output": "",
                }
                tracker.record_prediction(old_prediction)

                # Mock外部依赖（三个路径引用已在 isolated_env 统一指向临时目录）
                with patch('app.predict.daily.Backtest') as MockBacktest, \
                     patch('app.predict.daily.CandidatePool') as MockCandidatePool, \
                     patch('app.predict.daily.Strategy') as MockStrategy, \
                     patch('app.predict.daily.NewsScanner') as MockNewsScanner, \
                     patch('app.predict.daily.judge') as mock_judge, \
                     patch('app.predict.daily._kline_lookup_factory') as mock_kline_factory, \
                     patch('app.predict.alt_data.recent_lhb') as mock_lhb:

                    # 配置Backtest返回固定交易日历
                    mock_bt = MagicMock()
                    mock_bt.trading_days.return_value = ["2026-09-20", "2026-09-21"]
                    MockBacktest.return_value = mock_bt

                    # 配置kline_lookup（复用路径会调用补齐参考价）
                    mock_kline_factory.return_value = lambda ticker, end_date: {
                        "data": {"points": [{"time": "2026-09-21", "close": 10.0}]}
                    }

                    # 调用实际的predict函数
                    from app.predict.daily import predict
                    mock_cached = MagicMock()
                    result = predict(mock_cached, target_date="2026-09-21", use_llm=False)

                    # 复用分支不允许重新选股或调用LLM/新闻/龙虎榜
                    MockCandidatePool.assert_not_called()
                    MockStrategy.assert_not_called()
                    mock_judge.assert_not_called()
                    MockNewsScanner.assert_not_called()
                    mock_lhb.assert_not_called()

                    # 验证复用了原记录（版本不变）
                    assert result.get("strategy_version") == "v1.1", \
                        f"复用记录版本应保持v1.1，实际={result.get('strategy_version')}"

                    # 验证标的不变
                    assert len(result.get("targets", [])) == 1
                    assert result["targets"][0]["ticker"] == "000001", \
                        f"复用记录标的应保持000001，实际={result['targets'][0]['ticker']}"

                    # 验证数据库中的记录版本不变（且确为临时库）
                    tmp_db = (path_map["data"] / "a_share.db").resolve()
                    assert tmp_db in connect_log
                    stored = Tracker(path_map["data"]).get_prediction("2026-09-21")
                    assert stored.get("strategy_version") == "v1.1", \
                        f"数据库版本应保持v1.1，实际={stored.get('strategy_version')}"

                    # 验证标的不变
                    assert stored["targets"][0]["ticker"] == "000001"

    def test_reuse_prediction_without_version(self):
        """同日已有无版本字段预测时，应复用原记录，不添加版本字段，标的不变，不重新选股、不调用LLM"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with isolated_env(tmpdir) as (path_map, connect_log):
                from app.predict.track import Tracker
                tracker = Tracker(path_map["data"])

                # 预存无版本字段的历史记录
                old_prediction = {
                    "date": "2026-09-21",
                    "settlement_rule": "T+1开盘买入，T+2收盘卖出",
                    "strategy": "7-10日强势板块 + 个股强势 + 资金活跃",
                    "filtered_out": [],
                    "sector_window": "10日(ego)",
                    "market_view": "测试市场观点",
                    "targets": [
                        {
                            "ticker": "000002",
                            "name": "历史股票B",
                            "code": "000002",
                            "score": 50.0,
                            "factors": {"板块": 6.0, "个股强度": 18.0, "资金活跃": 12.0, "换手率": 10.0, "情绪": 5.0, "质量扣分": 0.0},
                            "逻辑": "历史逻辑",
                            "参考买入价(收盘)": 20.0,
                            "量比": 1.2,
                            "hold": "T+1开盘买入，T+2收盘卖出",
                        }
                    ],
                    "rule_candidates": [],
                    "news": {},
                    "news_has_lhb": False,
                    "top_sectors": ["历史板块"],
                    "candidate_count": 1,
                    "raw_llm_output": "",
                    # 无strategy_version字段
                }
                tracker.record_prediction(old_prediction)

                # Mock外部依赖
                with patch('app.predict.daily.Backtest') as MockBacktest, \
                     patch('app.predict.daily.CandidatePool') as MockCandidatePool, \
                     patch('app.predict.daily.Strategy') as MockStrategy, \
                     patch('app.predict.daily.NewsScanner') as MockNewsScanner, \
                     patch('app.predict.daily.judge') as mock_judge, \
                     patch('app.predict.daily._kline_lookup_factory') as mock_kline_factory, \
                     patch('app.predict.alt_data.recent_lhb') as mock_lhb:

                    mock_bt = MagicMock()
                    mock_bt.trading_days.return_value = ["2026-09-20", "2026-09-21"]
                    MockBacktest.return_value = mock_bt

                    mock_kline_factory.return_value = lambda ticker, end_date: {
                        "data": {"points": [{"time": "2026-09-21", "close": 20.0}]}
                    }

                    from app.predict.daily import predict
                    mock_cached = MagicMock()
                    result = predict(mock_cached, target_date="2026-09-21", use_llm=False)

                    # 复用分支不允许重新选股或调用LLM/新闻/龙虎榜
                    MockCandidatePool.assert_not_called()
                    MockStrategy.assert_not_called()
                    mock_judge.assert_not_called()
                    MockNewsScanner.assert_not_called()
                    mock_lhb.assert_not_called()

                    # 验证复用了原记录（不添加版本字段）
                    assert "strategy_version" not in result, \
                        f"复用无版本记录不应添加版本字段，实际有={result.get('strategy_version')}"

                    # 验证标的不变
                    assert len(result.get("targets", [])) == 1
                    assert result["targets"][0]["ticker"] == "000002", \
                        f"复用记录标的应保持000002，实际={result['targets'][0]['ticker']}"

                    # 验证数据库中的记录也不添加版本（且确为临时库）
                    tmp_db = (path_map["data"] / "a_share.db").resolve()
                    assert tmp_db in connect_log
                    stored = Tracker(path_map["data"]).get_prediction("2026-09-21")
                    assert "strategy_version" not in stored, \
                        f"数据库记录不应添加版本字段，实际有={stored.get('strategy_version')}"

                    # 验证标的不变
                    assert stored["targets"][0]["ticker"] == "000002"
