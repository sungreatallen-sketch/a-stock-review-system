"""
v1.2 真实业务路径验收测试

测试要求：
- 调用实际的_top_sectors()、build()、score_pool()方法
- Mock外部依赖（ego、MCP、THS），但不mock业务逻辑
- 验证rank从数据源传到评分的完整路径
- 提供"修复前失败、修复后通过"的证据
"""
import pytest
import sys
import os
from unittest.mock import MagicMock, patch

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.predict.candidate_pool import CandidatePool
from app.predict.scoring import score_pool, _sector_score
from app.predict.strategy import STRATEGY_VERSION


class MockEgoData:
    """模拟ego数据源，返回带rank字段的板块数据"""

    def __init__(self, sectors=None, stocks=None):
        self._sectors = sectors or []
        self._stocks = stocks or []

    def top_sectors_10d(self, n=10):
        """返回带rank字段的板块列表（模拟ego源的真实返回格式）"""
        return self._sectors[:n]

    def sector_stocks(self, bk, n=8):
        """返回板块成分股"""
        return self._stocks[:n]


class MockMcp:
    """模拟MCP客户端"""

    def __init__(self):
        self.call_count = 0

    def call(self, method, params=None):
        self.call_count += 1
        return None


def create_candidate_pool_with_mock_ego(ego_sectors, ego_stocks):
    """创建使用mock ego源的CandidatePool"""
    mock_ego = MockEgoData(ego_sectors, ego_stocks)
    mock_mcp = MockMcp()

    # 创建CandidatePool实例，注入mock依赖
    pool = CandidatePool.__new__(CandidatePool)
    pool.ego = mock_ego
    pool.mcp = mock_mcp
    pool._sector_window = ""
    pool._add_source = None

    # Mock _top_amount 和 _limit_up 以隔离外部数据源
    pool._top_amount = lambda trade_date: []
    pool._limit_up = lambda trade_date: []

    return pool


class TestRealPathRank1:
    """测试rank=1从数据源传到评分的真实路径"""

    def test_rank_1_propagates_to_score_20(self):
        """rank=1的板块，其成分股应获得板块分20"""
        # 模拟ego返回rank=1的板块
        ego_sectors = [
            {"rank": 1, "code": "BK0001", "name": "强势板块", "pct_10d": 10.0}
        ]

        # 模拟该板块的成分股
        ego_stocks = [
            {
                "ticker": "000001",
                "name": "测试股票A",
                "close": 10.0,
                "change_ratio": 5.0,
                "amount": 2e8,
                "turnover_rate": 10.0,
            }
        ]

        # 创建候选池并调用真实方法
        pool = create_candidate_pool_with_mock_ego(ego_sectors, ego_stocks)

        # 调用真实的_top_sectors()
        sectors = pool._top_sectors("2026-09-21")

        # 验证_top_sectors()正确映射了rank到sector_rank
        assert len(sectors) == 1
        assert sectors[0].get("sector_rank") == 1, f"期望sector_rank=1，实际={sectors[0].get('sector_rank')}"
        assert sectors[0].get("rank") == 1, "原rank字段应保留"

        # 调用真实的build()
        result = pool.build("2026-09-21")

        # 验证候选池中的股票获得了sector_rank
        candidates = result.get("candidates", [])
        assert len(candidates) == 1
        assert candidates[0].get("sector_rank") == 1, f"股票sector_rank应为1，实际={candidates[0].get('sector_rank')}"

        # 调用真实的score_pool()
        scored = score_pool(result, top_n=1)

        # 验证板块分为20
        assert len(scored) == 1
        assert scored[0]["factors"]["板块"] == 20.0, f"期望板块分20，实际={scored[0]['factors']['板块']}"


class TestRealPathRank8:
    """测试rank=8从数据源传到评分的真实路径"""

    def test_rank_8_propagates_to_score_3(self):
        """rank=8的板块，其成分股应获得板块分3"""
        # 模拟ego返回rank=8的板块
        ego_sectors = [
            {"rank": 8, "code": "BK0008", "name": "弱势板块", "pct_10d": 1.0}
        ]

        # 模拟该板块的成分股
        ego_stocks = [
            {
                "ticker": "000008",
                "name": "测试股票H",
                "close": 10.0,
                "change_ratio": 5.0,
                "amount": 2e8,
                "turnover_rate": 10.0,
            }
        ]

        # 创建候选池并调用真实方法
        pool = create_candidate_pool_with_mock_ego(ego_sectors, ego_stocks)

        # 调用真实的_top_sectors()
        sectors = pool._top_sectors("2026-09-21")

        # 验证_top_sectors()正确映射了rank到sector_rank
        assert len(sectors) == 1
        assert sectors[0].get("sector_rank") == 8, f"期望sector_rank=8，实际={sectors[0].get('sector_rank')}"

        # 调用真实的build()
        result = pool.build("2026-09-21")

        # 验证候选池中的股票获得了sector_rank
        candidates = result.get("candidates", [])
        assert len(candidates) == 1
        assert candidates[0].get("sector_rank") == 8, f"股票sector_rank应为8，实际={candidates[0].get('sector_rank')}"

        # 调用真实的score_pool()
        scored = score_pool(result, top_n=1)

        # 验证板块分为3
        assert len(scored) == 1
        assert scored[0]["factors"]["板块"] == 3.0, f"期望板块分3，实际={scored[0]['factors']['板块']}"


class TestRealPathExistingSectorRank:
    """测试已有sector_rank的来源不被覆盖（调用实际_top_sectors()）"""

    def test_existing_sector_rank_not_overridden(self):
        """如果源数据已有sector_rank和rank，_top_sectors()不应覆盖sector_rank"""
        # 模拟MCP/THS源返回的数据：同时含sector_rank和rank
        # 这种情况下_top_sectors()的条件"sector_rank" not in s为False，不会执行映射
        ego_sectors_with_both = [
            {
                "sector_rank": 5,  # 已有的sector_rank（MCP/THS源设置）
                "rank": 2,         # 同时也有rank字段
                "code": "BK0005",
                "name": "测试板块",
                "pct_10d": 5.0,
            }
        ]

        # 创建候选池，使用含sector_rank和rank的mock数据
        pool = create_candidate_pool_with_mock_ego(ego_sectors_with_both, [
            {"ticker": "000001", "name": "测试股票", "close": 10.0,
             "change_ratio": 5.0, "amount": 2e8, "turnover_rate": 10.0}
        ])

        # 调用实际的_top_sectors()方法
        sectors = pool._top_sectors("2026-09-21")

        # 验证sector_rank保持原值5，不被rank=2覆盖
        assert len(sectors) == 1
        assert sectors[0].get("sector_rank") == 5, \
            f"已有sector_rank不应被覆盖，期望5，实际={sectors[0].get('sector_rank')}"
        # rank字段也应保留
        assert sectors[0].get("rank") == 2, "原rank字段应保留"

    def test_sector_rank_only_no_rank_field(self):
        """只有sector_rank没有rank字段时，sector_rank保持不变"""
        ego_sectors_only_sector_rank = [
            {
                "sector_rank": 3,  # 只有sector_rank
                "code": "BK0003",
                "name": "测试板块",
                "pct_10d": 5.0,
            }
        ]

        pool = create_candidate_pool_with_mock_ego(ego_sectors_only_sector_rank, [
            {"ticker": "000001", "name": "测试股票", "close": 10.0,
             "change_ratio": 5.0, "amount": 2e8, "turnover_rate": 10.0}
        ])

        sectors = pool._top_sectors("2026-09-21")

        # sector_rank应保持原值3
        assert len(sectors) == 1
        assert sectors[0].get("sector_rank") == 3, \
            f"sector_rank应保持原值3，实际={sectors[0].get('sector_rank')}"
        # rank字段不应存在
        assert "rank" not in sectors[0], "无rank字段时不应添加rank"


class TestRealPathNoRank:
    """测试没有排名时的默认行为"""

    def test_no_rank_preserves_default_behavior(self):
        """没有rank字段时，应保留默认行为（板块分6）"""
        # 模拟ego返回没有rank字段的板块（理论上不应该发生，但测试防御性）
        ego_sectors = [
            {"code": "BK0001", "name": "无排名板块", "pct_10d": 5.0}
        ]

        ego_stocks = [
            {
                "ticker": "000001",
                "name": "测试股票",
                "close": 10.0,
                "change_ratio": 5.0,
                "amount": 2e8,
                "turnover_rate": 10.0,
            }
        ]

        pool = create_candidate_pool_with_mock_ego(ego_sectors, ego_stocks)

        # 调用真实的_top_sectors()
        sectors = pool._top_sectors("2026-09-21")

        # 没有rank字段时，sector_rank应为None
        assert len(sectors) == 1
        assert sectors[0].get("sector_rank") is None, f"无rank时sector_rank应为None，实际={sectors[0].get('sector_rank')}"

        # 调用真实的build()
        result = pool.build("2026-09-21")

        # 调用真实的score_pool()
        scored = score_pool(result, top_n=1)

        # 验证使用默认板块分6
        assert len(scored) == 1
        assert scored[0]["factors"]["板块"] == 6.0, f"无排名时应使用默认6分，实际={scored[0]['factors']['板块']}"


class TestRealPathRankAffectsSorting:
    """测试排名影响排序（通过分组比较验证）"""

    def test_rank_difference_affects_total_score(self):
        """不同排名的板块，其成分股的总分应有差异。
        由于mock的sector_stocks对所有板块返回相同股票，
        我们通过分别测试rank=1和rank=8来验证差异。"""
        # 测试1: rank=1的板块
        pool_1 = create_candidate_pool_with_mock_ego(
            [{"rank": 1, "code": "BK0001", "name": "强势板块", "pct_10d": 10.0}],
            [{"ticker": "000001", "name": "股票A", "close": 10.0,
              "change_ratio": 5.0, "amount": 2e8, "turnover_rate": 10.0}]
        )
        result_1 = pool_1.build("2026-09-21")
        scored_1 = score_pool(result_1, top_n=1)

        # 测试2: rank=8的板块
        pool_8 = create_candidate_pool_with_mock_ego(
            [{"rank": 8, "code": "BK0008", "name": "弱势板块", "pct_10d": 1.0}],
            [{"ticker": "000001", "name": "股票A", "close": 10.0,
              "change_ratio": 5.0, "amount": 2e8, "turnover_rate": 10.0}]
        )
        result_8 = pool_8.build("2026-09-21")
        scored_8 = score_pool(result_8, top_n=1)

        # 验证板块分差异
        assert len(scored_1) == 1
        assert len(scored_8) == 1
        assert scored_1[0]["factors"]["板块"] == 20.0, f"rank=1板块分应为20，实际={scored_1[0]['factors']['板块']}"
        assert scored_8[0]["factors"]["板块"] == 3.0, f"rank=8板块分应为3，实际={scored_8[0]['factors']['板块']}"

        # 验证总分差异（板块分差17分）
        score_diff = scored_1[0]["score"] - scored_8[0]["score"]
        assert score_diff == pytest.approx(17.0, abs=0.1), f"总分差应约17，实际={score_diff}"


class TestRealPathOtherFactorsUnchanged:
    """测试评分公式和其他因子保持不变"""

    def test_other_factors_calculation_unchanged(self):
        """验证其他因子（个股强度、资金活跃、换手率）的计算未改变"""
        ego_sectors = [
            {"rank": 1, "code": "BK0001", "name": "测试板块", "pct_10d": 5.0}
        ]

        ego_stocks = [
            {
                "ticker": "000001",
                "name": "测试股票",
                "close": 10.0,
                "change_ratio": 5.0,  # 应得21分
                "amount": 2e8,        # 应得21分
                "turnover_rate": 10.0, # 应得15分
            }
        ]

        pool = create_candidate_pool_with_mock_ego(ego_sectors, ego_stocks)
        result = pool.build("2026-09-21")
        scored = score_pool(result, top_n=1)

        assert len(scored) == 1
        factors = scored[0]["factors"]

        # 验证各因子分数
        assert factors["个股强度"] == pytest.approx(21.0, abs=0.1), f"个股强度期望21，实际={factors['个股强度']}"
        assert factors["资金活跃"] == pytest.approx(21.0, abs=0.1), f"资金活跃期望21，实际={factors['资金活跃']}"
        assert factors["换手率"] == pytest.approx(15.0, abs=0.1), f"换手率期望15，实际={factors['换手率']}"
        assert factors["板块"] == 20.0, f"板块分期望20，实际={factors['板块']}"
        assert factors["情绪"] == 5.0, f"情绪期望5，实际={factors['情绪']}"
        assert factors["质量扣分"] == 0.0, f"质量扣分期望0，实际={factors['质量扣分']}"


class TestVersionPropagation:
    """测试版本传播（使用临时目录验证实际写入）"""

    def test_strategy_version_is_v12(self):
        """当前策略版本应为v1.2"""
        assert STRATEGY_VERSION == "v1.2"

    def test_version_propagates_to_prediction_record(self):
        """版本号应传播到预测记录中（使用临时目录）"""
        import tempfile
        import json
        from pathlib import Path

        # 使用临时目录，不影响生产数据库
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # 创建Tracker实例，使用临时目录
            from app.predict.track import Tracker
            tracker = Tracker(tmp_path)

            # 构造包含STRATEGY_VERSION的预测结果（模拟daily.py的输出）
            prediction = {
                "date": "2026-09-21",
                "strategy_version": STRATEGY_VERSION,
                "targets": [
                    {"ticker": "000001", "name": "测试股票", "score": 61.0,
                     "factors": {"板块": 20.0, "个股强度": 21.0}}
                ],
                "sector_window": "10日(ego)",
                "candidate_count": 1,
            }

            # 调用实际的record_prediction()
            result = tracker.record_prediction(prediction)
            assert result is True, "record_prediction应返回True"

            # 从数据库读取验证
            conn = tracker._conn()
            row = conn.execute("SELECT targets FROM predictions WHERE date='2026-09-21'").fetchone()
            conn.close()

            assert row is not None, "预测记录应已写入"
            stored = json.loads(row[0])
            assert stored.get("strategy_version") == "v1.2", \
                f"存储的版本应为v1.2，实际={stored.get('strategy_version')}"

    def test_historical_v11_predictions_preserved(self):
        """历史v1.1预测记录应保持原样（使用临时目录）"""
        import tempfile
        import json
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            from app.predict.track import Tracker
            tracker = Tracker(tmp_path)

            # 写入模拟的历史v1.1预测
            old_prediction = {
                "date": "2026-09-20",
                "strategy_version": "v1.1",
                "targets": [{"ticker": "000001", "name": "旧股票", "score": 50.0}],
            }
            tracker.record_prediction(old_prediction)

            # 写入新v1.2预测
            new_prediction = {
                "date": "2026-09-21",
                "strategy_version": STRATEGY_VERSION,
                "targets": [{"ticker": "000002", "name": "新股票", "score": 61.0}],
            }
            tracker.record_prediction(new_prediction)

            # 验证历史v1.1记录保持不变
            conn = tracker._conn()
            rows = conn.execute("SELECT date, targets FROM predictions ORDER BY date").fetchall()
            conn.close()

            assert len(rows) == 2, f"应有2条记录，实际={len(rows)}"

            old_record = json.loads(rows[0][1])
            new_record = json.loads(rows[1][1])

            assert old_record.get("strategy_version") == "v1.1", \
                f"历史v1.1记录应保持不变，实际={old_record.get('strategy_version')}"
            assert new_record.get("strategy_version") == "v1.2", \
                f"新记录应为v1.2，实际={new_record.get('strategy_version')}"

    def test_empty_version_predictions_preserved(self):
        """无版本号的历史预测应保持原样（使用临时目录）"""
        import tempfile
        import json
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            from app.predict.track import Tracker
            tracker = Tracker(tmp_path)

            # 写入无版本号的历史预测
            no_version_prediction = {
                "date": "2026-09-19",
                "targets": [{"ticker": "000001", "name": "旧股票", "score": 50.0}],
                # 无strategy_version字段
            }
            tracker.record_prediction(no_version_prediction)

            # 写入新v1.2预测
            new_prediction = {
                "date": "2026-09-21",
                "strategy_version": STRATEGY_VERSION,
                "targets": [{"ticker": "000002", "name": "新股票", "score": 61.0}],
            }
            tracker.record_prediction(new_prediction)

            # 验证无版本号记录保持不变
            conn = tracker._conn()
            rows = conn.execute("SELECT date, targets FROM predictions ORDER BY date").fetchall()
            conn.close()

            assert len(rows) == 2
            old_record = json.loads(rows[0][1])
            new_record = json.loads(rows[1][1])

            assert "strategy_version" not in old_record, \
                f"无版本号记录不应添加版本字段，实际有={old_record.get('strategy_version')}"
            assert new_record.get("strategy_version") == "v1.2"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])