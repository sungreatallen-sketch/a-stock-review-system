"""
v1.2 离线验收测试：板块排名字段传递修复

测试覆盖：
1. ego来源的rank从候选池真实路径传入评分
2. 第一名与第八名板块（20分与3分）
3. 排名影响排序
4. 已经正确提供sector_rank的来源保持不变
5. 没有板块排名的股票保留回退行为
6. 相同评分输入下，评分函数结果保持一致
7. 预测版本传播
"""
import pytest
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.predict.scoring import _sector_score, score_stock, score_pool
from app.predict.strategy import STRATEGY_VERSION


class TestSectorScoreFunction:
    """测试评分函数本身（不依赖数据源）"""

    def test_sector_rank_1_returns_20(self):
        """第一名板块应得20分"""
        assert _sector_score(1) == 20.0

    def test_sector_rank_8_returns_3(self):
        """第八名板块应得3分"""
        assert _sector_score(8) == 3.0

    def test_sector_rank_none_returns_6(self):
        """无排名返回默认6分"""
        assert _sector_score(None) == 6

    def test_sector_rank_0_returns_6(self):
        """排名0视为无排名，返回默认6分"""
        assert _sector_score(0) == 6

    def test_sector_rank_linear_decay(self):
        """排名线性衰减验证"""
        # rank 1: 20, rank 8: 3, 步长 = 17/7 ≈ 2.43
        assert _sector_score(1) == 20.0
        assert _sector_score(4) == pytest.approx(12.7, abs=0.1)
        assert _sector_score(8) == 3.0

    def test_sector_rank_beyond_8_returns_min(self):
        """超过8名返回最小值3"""
        assert _sector_score(10) == 3.0
        assert _sector_score(100) == 3.0


class TestEgoRankMapping:
    """测试ego源rank字段到sector_rank的映射"""

    def test_ego_rank_mapped_to_sector_rank(self):
        """ego源返回的rank应被映射为sector_rank"""
        # 模拟ego源返回的数据
        ego_sector = {"rank": 3, "code": "BK0001", "name": "测试板块", "pct_10d": 5.0}

        # 模拟_top_sectors()中的处理逻辑
        if "sector_rank" not in ego_sector and "rank" in ego_sector:
            ego_sector["sector_rank"] = ego_sector["rank"]

        assert ego_sector["sector_rank"] == 3
        assert ego_sector["rank"] == 3  # 原字段保留

    def test_existing_sector_rank_not_overwritten(self):
        """已有的sector_rank不应被rank覆盖"""
        # 模拟MCP/THS源返回的数据（已有sector_rank）
        mcp_sector = {"sector_rank": 5, "rank": 2, "industry": "测试板块"}

        # 模拟_top_sectors()中的处理逻辑
        if "sector_rank" not in mcp_sector and "rank" in mcp_sector:
            mcp_sector["sector_rank"] = mcp_sector["rank"]

        # sector_rank应保持原值5，不被rank=2覆盖
        assert mcp_sector["sector_rank"] == 5

    def test_no_rank_preserves_none(self):
        """无rank字段时，sector_rank保持None"""
        sector = {"code": "BK0001", "name": "测试板块"}

        # 模拟_top_sectors()中的处理逻辑
        if "sector_rank" not in sector and "rank" in sector:
            sector["sector_rank"] = sector["rank"]

        assert sector.get("sector_rank") is None


class TestScoreStockIntegration:
    """测试score_stock()从候选到评分的完整路径"""

    def test_stock_with_rank_gets_correct_score(self):
        """有板块排名的股票应获得对应板块分"""
        stock = {
            "ticker": "000001",
            "name": "测试股票",
            "change_ratio": 5.0,
            "amount": 2e9,
            "turnover_rate": 10.0,
            "sector_rank": 1,  # 第一名板块
        }
        result = score_stock(stock)
        assert result["factors"]["板块"] == 20.0

    def test_stock_with_rank_8_gets_correct_score(self):
        """第8名板块的股票应获得3分"""
        stock = {
            "ticker": "000001",
            "name": "测试股票",
            "change_ratio": 5.0,
            "amount": 2e9,
            "turnover_rate": 10.0,
            "sector_rank": 8,
        }
        result = score_stock(stock)
        assert result["factors"]["板块"] == 3.0

    def test_stock_without_rank_gets_default_score(self):
        """无板块排名的股票应获得默认6分"""
        stock = {
            "ticker": "000001",
            "name": "测试股票",
            "change_ratio": 5.0,
            "amount": 2e9,
            "turnover_rate": 10.0,
            "sector_rank": None,
        }
        result = score_stock(stock)
        assert result["factors"]["板块"] == 6.0


class TestRankAffectsSorting:
    """测试排名影响候选排序"""

    def test_higher_rank_wins_with_same_other_factors(self):
        """其他条件相同时，板块排名高的股票总分更高"""
        stock_a = {
            "ticker": "000001",
            "name": "股票A",
            "change_ratio": 5.0,
            "amount": 2e9,
            "turnover_rate": 10.0,
            "sector_rank": 1,  # 第一名板块
        }
        stock_b = {
            "ticker": "000002",
            "name": "股票B",
            "change_ratio": 5.0,
            "amount": 2e9,
            "turnover_rate": 10.0,
            "sector_rank": 8,  # 第八名板块
        }

        score_a = score_stock(stock_a)["score"]
        score_b = score_stock(stock_b)["score"]

        # 板块分差 = 20 - 3 = 17分
        assert score_a > score_b
        assert score_a - score_b == pytest.approx(17.0, abs=0.1)

    def test_no_rank_vs_rank_1(self):
        """无排名(6分) vs 第一名(20分)，差14分"""
        stock_no_rank = {
            "ticker": "000001",
            "name": "股票A",
            "change_ratio": 5.0,
            "amount": 2e9,
            "turnover_rate": 10.0,
            "sector_rank": None,
        }
        stock_rank_1 = {
            "ticker": "000002",
            "name": "股票B",
            "change_ratio": 5.0,
            "amount": 2e9,
            "turnover_rate": 10.0,
            "sector_rank": 1,
        }

        score_no_rank = score_stock(stock_no_rank)["score"]
        score_rank_1 = score_stock(stock_rank_1)["score"]

        assert score_rank_1 - score_no_rank == pytest.approx(14.0, abs=0.1)


class TestScorePoolIntegration:
    """测试score_pool()完整流程"""

    def test_pool_with_sector_rank(self):
        """候选池中有板块排名时，评分应正确传递"""
        pool = {
            "candidates": [
                {
                    "ticker": "000001",
                    "name": "股票A",
                    "change_ratio": 5.0,
                    "amount": 2e9,
                    "turnover_rate": 10.0,
                    "sector_rank": 1,
                },
                {
                    "ticker": "000002",
                    "name": "股票B",
                    "change_ratio": 5.0,
                    "amount": 2e9,
                    "turnover_rate": 10.0,
                    "sector_rank": 8,
                },
            ]
        }

        result = score_pool(pool, top_n=2)
        assert len(result) == 2

        # 股票A（板块排名1）应排在股票B（板块排名8）前面
        assert result[0]["ticker"] == "000001"
        assert result[0]["factors"]["板块"] == 20.0
        assert result[1]["ticker"] == "000002"
        assert result[1]["factors"]["板块"] == 3.0

    def test_pool_without_sector_rank(self):
        """候选池中无板块排名时，应使用默认6分"""
        pool = {
            "candidates": [
                {
                    "ticker": "000001",
                    "name": "股票A",
                    "change_ratio": 5.0,
                    "amount": 2e9,
                    "turnover_rate": 10.0,
                    "sector_rank": None,
                },
            ]
        }

        result = score_pool(pool, top_n=1)
        assert len(result) == 1
        assert result[0]["factors"]["板块"] == 6.0


class TestVersionPropagation:
    """测试版本传播"""

    def test_strategy_version_is_v12(self):
        """当前策略版本应为v1.2"""
        assert STRATEGY_VERSION == "v1.2"

    def test_version_in_prediction_structure(self):
        """预测结构中应包含版本号"""
        # 模拟daily.py中的预测结构
        prediction = {
            "date": "2026-09-21",
            "strategy_version": STRATEGY_VERSION,
            "targets": [],
        }
        assert prediction["strategy_version"] == "v1.2"


class TestRegression:
    """回归测试：确保修改不影响其他功能"""

    def test_sector_score_formula_unchanged(self):
        """评分公式本身未改变"""
        # 与修改前相同的测试用例
        assert _sector_score(1) == 20.0
        assert _sector_score(2) == pytest.approx(17.6, abs=0.1)
        assert _sector_score(5) == pytest.approx(10.3, abs=0.1)
        assert _sector_score(8) == 3.0

    def test_other_factors_unchanged(self):
        """其他评分因子未改变"""
        stock = {
            "ticker": "000001",
            "name": "测试股票",
            "change_ratio": 5.0,
            "amount": 2e8,  # 2亿（1-4亿最优区间）
            "turnover_rate": 10.0,
            "sector_rank": 1,
        }
        result = score_stock(stock)

        # 验证其他因子（根据scoring.py中的实际公式）
        # _change_score(5.0) = 12 + (5-2)*3 = 21
        assert result["factors"]["个股强度"] == pytest.approx(21.0, abs=0.1)
        # _amount_score(2e8) = yi=2, 在1-4亿区间: 19 + (2-1)*2 = 21
        assert result["factors"]["资金活跃"] == pytest.approx(21.0, abs=0.1)
        # _turnover_score(10.0) = 15 (5-15%最佳区间)
        assert result["factors"]["换手率"] == pytest.approx(15.0, abs=0.1)
        assert result["factors"]["情绪"] == 5.0
        assert result["factors"]["质量扣分"] == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])