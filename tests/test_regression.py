"""回归测试：每个测试用例对应一个历史 bug，防止复现。
运行：cd 项目根目录 && .venv/bin/python -m pytest tests/test_regression.py -v

规则索引：
  T01 - 昨日标的日期 < 今日（ISSUE-027）
  T02 - 已结算标的有收益数据（ISSUE-027）
  T03 - 推荐标的数量 = 3（R10）
  T04 - 每个推荐标的有收盘价
  T05 - 板块排名非空
  T06 - 市场指数非空
  T07 - 市场情绪非空
  T08 - 涨停股不进推荐（C42）
  T09 - 校验器正确拦截错误报告
  T10 - 校验器正确通过正常报告
"""
import sys
import os
import json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.report_validator import validate_report, validate_and_block, ValidationError
from app.predict.track import Tracker


def _make_report(
    pred_date="2026-08-27",
    today="2026-08-28",
    targets=None,
    settle_results=None,
    sectors=None,
    market_index=None,
    emotion=None,
):
    """构造测试用报告"""
    if targets is None:
        targets = [
            {"code": "000713", "name": "国投丰乐", "参考买入价(收盘)": 6.69, "confidence": "中"},
            {"code": "002815", "name": "崇达技术", "参考买入价(收盘)": 14.69, "confidence": "高"},
            {"code": "002237", "name": "恒邦股份", "参考买入价(收盘)": 15.61, "confidence": "中"},
        ]
    if sectors is None:
        sectors = [{"板块": "种子", "涨跌幅%": 4.53}]
    if market_index is None:
        market_index = {"上证指数": {"收盘价": 3952.18, "涨跌幅%": -0.11}}
    if emotion is None:
        emotion = {"涨停数量": 82, "跌停数量": 1}
    if settle_results is None:
        settle_results = [
            {"code": "600354", "name": "敦煌种业", "buy_price": 7.46, "sell_price": 8.21, "ret": 10.05, "status": "settled"},
            {"code": "300313", "name": "天山生物", "buy_price": 16.64, "sell_price": 15.35, "ret": -7.75, "status": "settled"},
        ]
    return {
        "date": today,
        "market_index": market_index,
        "emotion": emotion,
        "sector_rank": sectors,
        "prediction": {
            "status": "M3完整版",
            "market_view": "测试市场判断",
            "targets": targets,
        },
        "tracking": {
            "latest_prediction": {"date": pred_date, "targets": targets[:2]},
            "settle": {"pred_date": pred_date, "results": settle_results},
            "stats": {"count": 30, "win_rate": 66.7, "avg_ret": 2.41},
        },
    }


# ===== T01: 昨日标的日期 < 今日 =====
def test_t01_yesterday_date_before_today():
    """ISSUE-027: 昨日推荐标的日期必须 < 今日"""
    report = _make_report(pred_date="2026-08-27", today="2026-08-28")
    errors = validate_report(report, today="2026-08-28")
    v01_errors = [e for e in errors if e.rule == "V01"]
    assert len(v01_errors) == 0, f"V01 不应报错: {v01_errors}"


def test_t01_yesterday_date_equals_today_fails():
    """ISSUE-027: 如果昨日日期=今日，必须报错"""
    report = _make_report(pred_date="2026-08-28", today="2026-08-28")
    errors = validate_report(report, today="2026-08-28")
    v01_errors = [e for e in errors if e.rule == "V01" and e.severity == "error"]
    assert len(v01_errors) == 1, f"V01 应报错: {v01_errors}"


# ===== T02: 已结算标的有收益数据 =====
def test_t02_settle_results_have_ret():
    """ISSUE-027: 结算结果必须有收益数据"""
    report = _make_report()
    errors = validate_report(report, today="2026-08-28")
    v02_errors = [e for e in errors if e.rule == "V02" and e.severity == "error"]
    assert len(v02_errors) == 0, f"V02 不应报错: {v02_errors}"


def test_t02_missing_ret_fails():
    """ISSUE-027: 缺少收益数据必须报错"""
    settle = [{"code": "001", "name": "测试", "buy_price": 10, "sell_price": 11, "ret": None, "status": "settled"}]
    report = _make_report(settle_results=settle)
    errors = validate_report(report, today="2026-08-28")
    v02_errors = [e for e in errors if e.rule == "V02" and e.severity == "error"]
    assert len(v02_errors) == 1, f"V02 应报错: {v02_errors}"


# ===== T03: 推荐标的数量 = 3 =====
def test_t03_targets_count_is_three():
    """R10: 推荐标的必须是3只"""
    report = _make_report()
    errors = validate_report(report, today="2026-08-28")
    v03_errors = [e for e in errors if e.rule == "V03"]
    assert len(v03_errors) == 0, f"V03 不应报错: {v03_errors}"


def test_t03_targets_count_not_three_fails():
    """R10: 推荐标的不是3只必须报错"""
    report = _make_report(targets=[
        {"code": "001", "name": "测试", "参考买入价(收盘)": 10},
    ])
    errors = validate_report(report, today="2026-08-28")
    v03_errors = [e for e in errors if e.rule == "V03" and e.severity == "error"]
    assert len(v03_errors) == 1, f"V03 应报错: {v03_errors}"


# ===== T04: 每个推荐标的有收盘价 =====
def test_t04_targets_have_buy_price():
    """每个推荐标的必须有收盘价"""
    report = _make_report()
    errors = validate_report(report, today="2026-08-28")
    v04_errors = [e for e in errors if e.rule == "V04"]
    assert len(v04_errors) == 0, f"V04 不应报错: {v04_errors}"


def test_t04_missing_buy_price_fails():
    """缺少收盘价必须报错"""
    targets = [
        {"code": "001", "name": "测试", "参考买入价(收盘)": 10},
        {"code": "002", "name": "测试2", "参考买入价(收盘)": None},
        {"code": "003", "name": "测试3", "参考买入价(收盘)": 20},
    ]
    report = _make_report(targets=targets)
    errors = validate_report(report, today="2026-08-28")
    v04_errors = [e for e in errors if e.rule == "V04" and e.severity == "error"]
    assert len(v04_errors) == 1, f"V04 应有1个错误: {v04_errors}"


# ===== T05: 板块排名非空 =====
def test_t05_sectors_not_empty():
    report = _make_report()
    errors = validate_report(report, today="2026-08-28")
    v05_errors = [e for e in errors if e.rule == "V05"]
    assert len(v05_errors) == 0


def test_t05_empty_sectors_fails():
    report = _make_report(sectors=[])
    errors = validate_report(report, today="2026-08-28")
    v05_errors = [e for e in errors if e.rule == "V05" and e.severity == "error"]
    assert len(v05_errors) == 1


# ===== T06: 市场指数非空 =====
def test_t06_market_index_not_empty():
    report = _make_report()
    errors = validate_report(report, today="2026-08-28")
    v06_errors = [e for e in errors if e.rule == "V06"]
    assert len(v06_errors) == 0


def test_t06_empty_market_index_fails():
    report = _make_report(market_index={})
    errors = validate_report(report, today="2026-08-28")
    v06_errors = [e for e in errors if e.rule == "V06" and e.severity == "error"]
    assert len(v06_errors) == 1


# ===== T07: 市场情绪非空 =====
def test_t07_emotion_not_empty():
    report = _make_report()
    errors = validate_report(report, today="2026-08-28")
    v07_errors = [e for e in errors if e.rule == "V07"]
    assert len(v07_errors) == 0


# ===== T08: 涨停股不进推荐 =====
def test_t08_no_limit_up_in_targets():
    """C42: 涨停股不能进入推荐"""
    report = _make_report()
    errors = validate_report(report, today="2026-08-28")
    v08_errors = [e for e in errors if e.rule == "V08"]
    assert len(v08_errors) == 0


def test_t08_limit_up_target_fails():
    """C42: 涨停股进入推荐必须报错"""
    targets = [
        {"code": "001", "name": "涨停股", "参考买入价(收盘)": 10, "limit_status": "涨停"},
        {"code": "002", "name": "正常股", "参考买入价(收盘)": 20},
        {"code": "003", "name": "正常股2", "参考买入价(收盘)": 30},
    ]
    report = _make_report(targets=targets)
    errors = validate_report(report, today="2026-08-28")
    v08_errors = [e for e in errors if e.rule == "V08" and e.severity == "error"]
    assert len(v08_errors) == 1, f"V08 应报错: {v08_errors}"


# ===== T09: 校验器正确拦截错误报告 =====
def test_t09_validate_and_block_rejects_bad_report():
    """校验器应拒绝日期错误的报告"""
    report = _make_report(pred_date="2026-08-28", today="2026-08-28")
    can_send, errors = validate_and_block(report, today="2026-08-28")
    assert can_send is False, "日期错误的报告不应通过校验"
    assert any(e.rule == "V01" for e in errors)


# ===== T10: 校验器正确通过正常报告 =====
def test_t10_validate_and_block_accepts_good_report():
    """校验器应通过结构完整的报告"""
    report = _make_report()
    can_send, errors = validate_and_block(report, today="2026-08-28")
    assert can_send is True, f"正常报告应通过校验，错误: {errors}"
    blocking = [e for e in errors if e.severity == "error"]
    assert len(blocking) == 0, f"正常报告不应有阻断错误: {blocking}"


class _FakeCached:
    def call(self, name, args=None, timeout=90):
        day = (args or {}).get("end_date")
        return {"data": {"points": [{"time": day, "open": 10, "close": 11}]}}


def test_t11_settlement_uses_t1_open_buy_and_t2_close_sell(tmp_path, monkeypatch):
    """T日收盘后的推荐必须按T+1开盘买入、T+2收盘卖出评估。"""
    from datetime import datetime
    from app.ths_client import THSClient

    def fake_trading_days(self, begin, end):
        return ["2026-09-01", "2026-09-02", "2026-09-03"]

    def fake_kline(self, thscode, start, end, interval="1d"):
        end_s = end.isoformat()
        open_price = 10.5 if end_s == "2026-09-02" else 13.0
        close = 11.0 if end_s == "2026-09-02" else 13.2
        def item(day, open_price, close):
            return {"date_ms": int(datetime.fromisoformat(day).timestamp() * 1000),
                    "open_price": open_price, "close_price": close}
        return [item(end_s, open_price, close)]

    monkeypatch.setattr(THSClient, "trading_days", fake_trading_days)
    monkeypatch.setattr(THSClient, "kline", fake_kline)

    tr = Tracker(tmp_path)
    pred = {"date": "2026-09-01", "targets": [
        {"code": "600000", "name": "测试股", "参考买入价(收盘)": 10.0}
    ]}
    assert tr.record_prediction(pred)
    result = tr.settle_pending(_FakeCached(), today="2026-09-03")
    assert result["buy_date"] == "2026-09-02"
    assert result["sell_date"] == "2026-09-03"
    rows = tr.stats()["recent"]
    assert rows[0]["buy"] == 10.5
    assert rows[0]["sell"] == 13.2
    assert rows[0]["ret"] == 25.71
    assert rows[0]["reference_price"] == 10.0
    assert rows[0]["buy_price_type"] == "open"
    assert rows[0]["sell_price_type"] == "close"


def test_t12_repeat_settlement_does_not_duplicate(tmp_path, monkeypatch):
    """同一预测重复结算必须覆盖同一日记录，不能产生重复Experience/记录。"""
    from datetime import datetime
    from app.ths_client import THSClient

    monkeypatch.setattr(THSClient, "trading_days", lambda self, b, e: ["2026-09-01", "2026-09-02", "2026-09-03"])
    def fake_kline(self, code, s, e, interval="1d"):
        day = e.isoformat()
        open_price = 9.5 if day == "2026-09-02" else 10.4
        close = 10.0 if day == "2026-09-02" else 11.0
        return [{"date_ms": int(datetime.fromisoformat(day).timestamp() * 1000),
                 "open_price": open_price, "close_price": close}]
    monkeypatch.setattr(THSClient, "kline", fake_kline)
    tr = Tracker(tmp_path)
    tr.record_prediction({"date": "2026-09-01", "targets": [
        {"code": "000001", "name": "测试", "参考买入价(收盘)": 9.0}
    ]})
    for _ in range(2):
        tr.settle_pending(_FakeCached(), today="2026-09-03")
    conn = tr._conn()
    count = conn.execute("SELECT COUNT(*) FROM prediction_results WHERE date='2026-09-01'").fetchone()[0]
    conn.close()
    assert count == 1


def test_t12b_mature_batch_settles_when_newer_batch_is_pending(tmp_path, monkeypatch):
    """9/3批次未到T+2时，不能挡住9/2批次在9/4完成T+2结算。"""
    from datetime import datetime
    from app.ths_client import THSClient

    monkeypatch.setattr(THSClient, "trading_days", lambda self, b, e: ["2026-09-02", "2026-09-03", "2026-09-04"])
    def fake_kline(self, code, s, e, interval="1d"):
        day = e.isoformat()
        return [{"date_ms": int(datetime.fromisoformat(day).timestamp() * 1000),
                 "open_price": 10.0, "close_price": 11.0}]
    monkeypatch.setattr(THSClient, "kline", fake_kline)
    tr = Tracker(tmp_path)
    target = [{"code": "600000", "name": "测试", "参考买入价(收盘)": 9.0}]
    tr.record_prediction({"date": "2026-09-02", "targets": target})
    tr.record_prediction({"date": "2026-09-03", "targets": target})
    result = tr.settle_pending(_FakeCached(), today="2026-09-04")
    assert result["date"] == "2026-09-02"
    assert result["buy_date"] == "2026-09-03"
    assert result["sell_date"] == "2026-09-04"


def test_t13_auto_review_target_after_1600(monkeypatch):
    """16:03必须视为收盘后；不能因minute<30而回退到前一日。"""
    import scripts.auto_review as auto_review

    class FakeDateTime(auto_review.datetime):
        @classmethod
        def now(cls):
            return cls(2026, 9, 2, 16, 3)

    monkeypatch.setattr(auto_review, "datetime", FakeDateTime)
    assert auto_review.get_target_trade_date() == "2026-09-02"


def test_t14_stale_morning_report_is_not_final(tmp_path, monkeypatch):
    """15:30 前生成的同日报告不能当作收盘后终版推送。"""
    import scripts.auto_review as auto_review

    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    monkeypatch.setattr(auto_review, "paths", lambda: {"data": tmp_path / "data", "reports": report_dir})
    fp = report_dir / "2026-09-02.json"
    payload = {
        "date": "2026-09-02",
        "meta": {"generated_at": "2026-09-02T09:06:52"},
        "prediction": {
            "status": "M3完整版",
            "targets": [{"code": "600000", "name": "测试", "参考买入价(收盘)": 10.0}],
        },
    }
    fp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert auto_review.report_complete("2026-09-02") is True
    assert auto_review.final_report_ready("2026-09-02") is False


def test_t15_closed_final_report_is_ready(tmp_path, monkeypatch):
    """收盘后且价格完整的报告才可进入 16:00 推送流程。"""
    import scripts.auto_review as auto_review

    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    monkeypatch.setattr(auto_review, "paths", lambda: {"data": tmp_path / "data", "reports": report_dir})
    fp = report_dir / "2026-09-02.json"
    payload = {
        "date": "2026-09-02",
        "meta": {"generated_at": "2026-09-02T16:01:00"},
        "prediction": {
            "status": "M3完整版",
            "targets": [
                {"code": "600000", "name": "A", "参考买入价(收盘)": 10.0},
                {"code": "000001", "name": "B", "参考买入价(收盘)": 11.0},
                {"code": "300001", "name": "C", "参考买入价(收盘)": 12.0},
            ],
        },
    }
    fp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert auto_review.final_report_ready("2026-09-02") is True


def test_t16_scheduler_does_not_push_before_1600(monkeypatch):
    """15:30 后但 16:00 前的轮询只等待，不提前生成终版。"""
    import scripts.auto_review as auto_review

    class FakeDateTime(auto_review.datetime):
        @classmethod
        def now(cls):
            return cls(2026, 9, 2, 15, 58)

    monkeypatch.setattr(auto_review, "datetime", FakeDateTime)
    monkeypatch.setattr(auto_review, "get_target_trade_date", lambda: "2026-09-02")
    called = {"run": False}
    def fail_subprocess(*a, **kw):
        called["run"] = True
        raise AssertionError("16:00 前不应执行复盘")
    monkeypatch.setattr(auto_review.subprocess, "run", fail_subprocess)
    auto_review.run_auto_review()
    assert called["run"] is False


def test_t17_tracking_settlement_fields_are_not_shifted(tmp_path):
    """结算详情的字段映射必须与 SQL 列顺序一致，防止日期/状态串位。"""
    from app.predict.track import Tracker
    from app.workflow import _build_tracking

    tr = Tracker(tmp_path)
    tr.record_prediction({
        "date": "2026-09-01",
        "targets": [{"code": "600000", "name": "测试", "参考买入价(收盘)": 10.0}],
    })
    tr.settle("2026-09-01", {"600000": 11.0}, {"600000": 13.2},
              buy_date="2026-09-02", sell_date="2026-09-03")
    tracking = _build_tracking(tr, None, trade_date="2026-09-04")
    row = tracking["settle"]["results"][0]
    assert row["status"] == "settled"
    assert row["buy_date"] == "2026-09-02"
    assert row["sell_date"] == "2026-09-03"
    assert row["reference_price"] == 10.0
    assert row["ret"] == 20.0


def test_t18_legacy_execution_text_never_reaches_ui():
    """历史预测里的旧口径字段不能覆盖当前 T+1开盘→T+2收盘 的用户展示。"""
    from scripts.send_review import build_summary

    report = {
        "date": "2026-09-04",
        "market_index": {},
        "emotion": {},
        "prediction": {
            "targets": [{
                "code": "600000", "name": "旧口径样例", "confidence": "中",
                "参考买入价(收盘)": 10.0, "hold": "T+1收盘买入，T+2收盘卖出",
                "stop_loss": 9.70, "sell_target": 10.30,
            }],
        },
        "tracking": {
            "settled_prediction": {"date": "2026-09-02"},
            "pending_prediction": {
                "date": "2026-09-03",
                "targets": [{"code": "000001", "name": "持仓样例", "hold": "T+1收盘买入，T+2收盘卖出"}],
            },
        },
    }
    text = build_summary(report)
    assert "T+1收盘买入" not in text
    assert "T+1开盘买入，T+2收盘卖出" in text
    assert "9.7" not in text
    assert "10.3" not in text


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
