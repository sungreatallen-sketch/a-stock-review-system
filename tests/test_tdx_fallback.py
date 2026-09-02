"""通达信MCP直连兜底回归。"""
import asyncio

from app.mcp_client import ResilientMcpClient


class _DownClient:
    url = "http://127.0.0.1:0/mcp"

    async def call_tool(self, name, arguments=None, timeout=90):
        raise RuntimeError("WorkBuddy不可用")


class _FakeTdx:
    def call_tool(self, name, arguments=None, timeout=90):
        assert name == "tdx_kline"
        assert arguments == {"code": "000001", "setcode": "1", "period": "4", "wantNum": 3}
        return {
            "structured": {
                "Rows": [
                    {"Data": "20260901", "Open": "10", "High": "11", "Low": "9.8",
                     "Close": "10.8", "Volume": 100, "Amount": 1080},
                    {"Data": "20260902", "Open": "10.9", "High": "12", "Low": "10.7",
                     "Close": "11.8", "Volume": 120, "Amount": 1290},
                    {"Data": "20260903", "Open": "12", "High": "13", "Low": "11.9",
                     "Close": "12.5", "Volume": 130, "Amount": 1500},
                ]
            }
        }


def test_tdx_kline_fallback_normalizes_existing_contract():
    """同舟K线不可用时，TDX必须归一化成data.points且不混入未来日期。"""
    client = ResilientMcpClient(_DownClient(), _FakeTdx())
    resp = asyncio.run(client.call_tool(
        "tongzhou-fin-research_fin_data__get_kline_series",
        {"ticker": "000001.SH", "market": "index", "end_date": "2026-09-02", "limit": 3},
    ))
    points = resp["data"]["points"]
    assert [p["time"] for p in points] == ["2026-09-01", "2026-09-02"]
    assert points[-1]["close"] == 11.8
    assert resp["source"] == "通达信MCP直连"


def test_non_tdx_tool_does_not_fake_fallback():
    """非通达信能力拿不到时必须显式失败，不能伪造数据。"""
    client = ResilientMcpClient(_DownClient(), _FakeTdx())
    try:
        asyncio.run(client.call_tool("other-server__tool", {}, timeout=1))
    except RuntimeError as e:
        assert "WorkBuddy不可用" in str(e)
    else:
        raise AssertionError("非TDX工具不应静默fallback")
