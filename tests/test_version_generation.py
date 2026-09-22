"""
版本生成与同日复用测试

测试要求：
- 调用实际app.predict.daily.predict函数
- Mock外部依赖（行情、LLM、新闻等），不mock预测入口及复用判断
- 使用临时数据库和固定交易日历
- 验证版本生成、同日复用、历史记录保持

注意：
- daily.predict的复用路径允许补齐参考价、写入执行窗口
- 需要准备完整历史样本，验证版本和标的不变（不要求所有字段完全不变）
"""
import pytest
import sys
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.predict.strategy import STRATEGY_VERSION


class TestVersionGeneration:
    """测试版本生成：调用实际daily.predict，验证新预测包含v1.2"""

    def test_new_prediction_generates_v12(self):
        """新预测应生成v1.2版本（无历史记录时）"""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            reports_path = tmp_path / "reports"
            reports_path.mkdir(exist_ok=True)

            # 创建临时数据库的Tracker
            from app.predict.track import Tracker
            tracker = Tracker(tmp_path)

            # 确保目标日期无记录
            assert tracker.get_prediction("2026-09-21") is None

            # Mock所有外部依赖
            with patch('app.predict.daily.Backtest') as MockBacktest, \
                 patch('app.predict.daily.CandidatePool') as MockCandidatePool, \
                 patch('app.predict.daily.Strategy') as MockStrategy, \
                 patch('app.predict.daily.NewsScanner') as MockNewsScanner, \
                 patch('app.predict.daily.judge') as mock_judge, \
                 patch('app.predict.daily._kline_lookup_factory') as mock_kline_factory, \
                 patch('app.predict.daily.paths', return_value={"data": tmp_path, "reports": reports_path}), \
                 patch('app.predict.daily._market_context', return_value={}), \
                 patch('app.predict.daily.EXECUTION_PLAN', "T+1开盘买入，T+2收盘卖出"):

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

                # 调用实际的predict函数
                from app.predict.daily import predict
                mock_cached = MagicMock()
                result = predict(mock_cached, target_date="2026-09-21", use_llm=False)

                # 验证结果包含v1.2版本
                assert result.get("strategy_version") == "v1.2", \
                    f"新预测版本应为v1.2，实际={result.get('strategy_version')}"

                # 模拟run_cli.py的行为：保存预测到数据库
                tracker.record_prediction(result)

                # 验证数据库中的记录
                stored = tracker.get_prediction("2026-09-21")
                assert stored is not None, "预测应已保存到数据库"
                assert stored.get("strategy_version") == "v1.2", \
                    f"数据库中版本应为v1.2，实际={stored.get('strategy_version')}"


class TestVersionReuse:
    """测试同日复用：已有预测时应复用原记录"""

    def test_reuse_v11_prediction_same_day(self):
        """同日已有v1.1预测时，应复用原记录，版本不变，标的不变"""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            reports_path = tmp_path / "reports"
            reports_path.mkdir(exist_ok=True)

            from app.predict.track import Tracker
            tracker = Tracker(tmp_path)

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

            # Mock外部依赖（注意：需要patch app.config.paths而不是app.predict.daily.paths）
            with patch('app.predict.daily.Backtest') as MockBacktest, \
                 patch('app.predict.daily._kline_lookup_factory') as mock_kline_factory, \
                 patch('app.config.paths', return_value={"data": tmp_path, "reports": reports_path}), \
                 patch('app.predict.daily.EXECUTION_PLAN', "T+1开盘买入，T+2收盘卖出"):

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

                # 验证复用了原记录（版本不变）
                assert result.get("strategy_version") == "v1.1", \
                    f"复用记录版本应保持v1.1，实际={result.get('strategy_version')}"

                # 验证标的不变
                assert len(result.get("targets", [])) == 1
                assert result["targets"][0]["ticker"] == "000001", \
                    f"复用记录标的应保持000001，实际={result['targets'][0]['ticker']}"

                # 验证数据库中的记录版本不变
                stored = tracker.get_prediction("2026-09-21")
                assert stored.get("strategy_version") == "v1.1", \
                    f"数据库版本应保持v1.1，实际={stored.get('strategy_version')}"

                # 验证标的不变
                assert stored["targets"][0]["ticker"] == "000001"

    def test_reuse_prediction_without_version(self):
        """同日已有无版本字段预测时，应复用原记录，不添加版本字段，标的不变"""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            reports_path = tmp_path / "reports"
            reports_path.mkdir(exist_ok=True)

            from app.predict.track import Tracker
            tracker = Tracker(tmp_path)

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
                 patch('app.predict.daily._kline_lookup_factory') as mock_kline_factory, \
                 patch('app.config.paths', return_value={"data": tmp_path, "reports": reports_path}), \
                 patch('app.predict.daily.EXECUTION_PLAN', "T+1开盘买入，T+2收盘卖出"):

                mock_bt = MagicMock()
                mock_bt.trading_days.return_value = ["2026-09-20", "2026-09-21"]
                MockBacktest.return_value = mock_bt

                mock_kline_factory.return_value = lambda ticker, end_date: {
                    "data": {"points": [{"time": "2026-09-21", "close": 20.0}]}
                }

                from app.predict.daily import predict
                mock_cached = MagicMock()
                result = predict(mock_cached, target_date="2026-09-21", use_llm=False)

                # 验证复用了原记录（不添加版本字段）
                assert "strategy_version" not in result, \
                    f"复用无版本记录不应添加版本字段，实际有={result.get('strategy_version')}"

                # 验证标的不变
                assert len(result.get("targets", [])) == 1
                assert result["targets"][0]["ticker"] == "000002", \
                    f"复用记录标的应保持000002，实际={result['targets'][0]['ticker']}"

                # 验证数据库中的记录也不添加版本
                stored = tracker.get_prediction("2026-09-21")
                assert "strategy_version" not in stored, \
                    f"数据库记录不应添加版本字段，实际有={stored.get('strategy_version')}"

                # 验证标的不变
                assert stored["targets"][0]["ticker"] == "000002"


class TestVersionStorageNote:
    """存储验证说明"""

    def test_note_about_test_scope(self):
        """说明本文件测试范围"""
        # 本文件测试的是：
        # 1. 新预测生成时版本为v1.2（TestVersionGeneration）
        # 2. 同日复用时版本不变（TestVersionReuse）
        #
        # 不测试的是：
        # - 跨日预测（不同日期的新预测）
        # - 版本升级路径（v1.1→v1.2的迁移）
        # - 实际LLM研判结果
        #
        # 已有TestVersionStorage验证Tracker的存储行为
        assert True  # 占位测试，说明测试范围