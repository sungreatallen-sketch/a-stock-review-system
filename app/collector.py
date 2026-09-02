"""数据采集编排：ego browser 优先 → MCP 兜底 → 交叉验证"""
import asyncio
import json
import logging
from datetime import date, timedelta

from .ego_scraper import (EgoScraper, build_tasks, parse_index, parse_pool_counts,
                          parse_boards, parse_main_flow, parse_hsgt, parse_lhb)
from .mcp_client import McpClient, parse_mcp_json
from .validator import validate_number, validate_text
from .config import load_config
from .ths_client import get_ths_client, thscode_for_index

log = logging.getLogger("collector")

INDEX_TDX = {
    "上证指数": {"code": "000001", "setcode": "1"},
    "深证成指": {"code": "399001", "setcode": "0"},
    "创业板指": {"code": "399006", "setcode": "0"},
    "科创50": {"code": "000688", "setcode": "1"},
}


def _try_load(v):
    """把 ego 返回的文本解码为 dict/list，失败返回空"""
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str) and v.startswith("{"):
        try:
            return json.loads(v)
        except Exception:
            return {}
    return {}


def _find_dict(v):
    """从 MCP 返回(可能含多个文本/对象)中找第一个 dict；优先 structuredContent"""
    if isinstance(v, dict):
        return v
    if isinstance(v, list):
        for it in v:
            if isinstance(it, dict):
                return it
    return {}


def _mcp_payload(resp):
    """优先取 structured 字段，其次解析文本 JSON"""
    if resp and resp.get("structured") is not None:
        return resp["structured"]
    return _find_dict(parse_mcp_json(resp))


def _tdx_parse(resp: dict):
    """通达信 quotes 返回解析出 close/pct/amount"""
    items = parse_mcp_json(resp)
    if isinstance(items, list):
        text = "\n".join(str(i) for i in items)
    else:
        text = str(items)
    try:
        j = json.loads(text[text.find("{"):text.rfind("}") + 1]) if "{" in text else {}
    except Exception:
        j = {}
    hq = j.get("HQInfo") or {}
    now = hq.get("Now")
    amount = hq.get("Amount")
    close = hq.get("Close")  # 昨收
    pct = None
    if now and close:
        try:
            pct = (float(now) / float(close) - 1) * 100
        except Exception:
            pct = None
    return {"close": now, "pct": pct, "amount": amount}


class Collector:
    def __init__(self):
        cfg = load_config()
        m = cfg["mcp"]
        self.mcp = McpClient(m["proxy_url"], m.get("token", ""), m["workbuddy_log_dir"])
        self.ego = EgoScraper()
        self.sources = []
        self.notes = []

    def _add_source(self, s):
        if isinstance(s, str):
            items = [s]
        else:
            items = list(s)
        for it in items:
            if it and it not in self.sources:
                self.sources.append(it)

    def _find_trade_date(self, d: date):
        """从 d 往前找有涨停池数据的交易日"""
        for i in range(4):
            dd = (d - timedelta(days=i)).strftime("%Y%m%d")
            raw = self.ego.fetch(build_tasks(dd))
            if raw:
                dec = {k: _try_load(v) for k, v in raw.items()}
                parsed = {k: parse_pool_counts(dec.get(k)) for k in ("zt_pool", "dt_pool", "zb_pool")}
                if parsed.get("zt_pool", {}).get("qdate"):
                    return dd, dec, parsed
        return None, {}, {}

    # ---------- MCP 辅助 ----------
    def _mcp_call(self, name, args, timeout=90):
        try:
            return asyncio.run(self.mcp.call_tool(name, args, timeout=timeout))
        except Exception as e:
            log.warning("MCP %s 调用失败: %s", name, str(e)[:200])
            return None

    # ---------- 指数 ----------
    def _collect_index(self, ego_index: dict, trade_date: str):
        out = {}
        ths = get_ths_client()
        for name, cfg in INDEX_TDX.items():
            e = ego_index.get(name) or {}
            # THS 优先（REST API 直连，不经过 WorkBuddy）
            thscode = thscode_for_index(name)
            snap = ths.ths_index_snapshot(thscode) if thscode else {}
            t_close = snap.get("last_price")
            t_pct = snap.get("price_change_ratio_pct")
            t_amount = snap.get("turnover")
            ths_src = f"同花顺API（{name}）"
            # MCP 兜底 + 验证：通达信
            resp = self._mcp_call("tdx-connector_tdx_quotes",
                                  {"code": cfg["code"], "setcode": cfg["setcode"],
                                   "hasHQInfo": "1", "hasExtInfo": "1"}, timeout=60)
            t = _tdx_parse(resp) if resp else {"close": None, "pct": None, "amount": None}
            # 交叉验证：THS + 东财 + 通达信
            primary = (t_close or e.get("close"), ths_src if t_close else f"东财行情API（{name}）")
            secondary = (t.get("close") if not t_close else None, "通达信MCP")
            v = validate_number(primary, secondary, f"{name}收盘", rel_tol=0.001, abs_tol=0.2)
            amt_v = validate_number(
                (t_amount or e.get("amount"), ths_src if t_amount else f"东财行情API（{name}）"),
                (t.get("amount"), "通达信MCP"), f"{name}成交额", rel_tol=0.01)
            pct = t_pct if t_pct is not None else e.get("pct")
            out[name] = {
                "code": e.get("code") or cfg["code"],
                "close": v["value"],
                "pct": pct,
                "change": e.get("change"),
                "amount": amt_v["value"],
                "validated": v["validated"] and amt_v["validated"],
                "sources": list(dict.fromkeys(v["sources"] + amt_v["sources"])),
                "note": f"{v['note']}；{amt_v['note']}",
            }
            self._add_source(x for x in v["sources"])
            self._add_source(x for x in amt_v["sources"])
        return out

    # ---------- 情绪 ----------
    def _collect_emotion(self, parsed: dict, trade_date: str):
        zt = parsed.get("zt_pool") or {}
        dt = parsed.get("dt_pool") or {}
        zb = parsed.get("zb_pool") or {}
        # MCP 验证涨停数：同舟 screen_stocks 总数 + 通达信 screener 总数
        # 涨停交叉验证：同舟 screen_stocks 总数（若可取得）+ 通达信 screener 总数
        tz_count = None
        tz_resp = self._mcp_call("tongzhou-fin-research_fin_data__screen_stocks",
                                 {"status": "limit_up", "trade_date": trade_date, "limit": 50})
        tz = _mcp_payload(tz_resp) if tz_resp else {}
        if tz.get("status") == "success":
            secs = (tz.get("data") or {}).get("securities") or []
            if isinstance(secs, list) and len(secs) < 50:
                tz_count = len(secs)
        td_count = None
        td_resp = self._mcp_call("tdx-connector_tdx_screener", {"message": "涨停", "pageSize": 20})
        td = _mcp_payload(td_resp) if td_resp else {}
        td_count = (td.get("meta") or {}).get("total")

        v_zt = validate_number(
            (zt.get("count"), "东财涨停池API"),
            (td_count, "通达信MCP选股(涨停)"), "涨停家数", rel_tol=0.05, abs_tol=3)
        # 跌停交叉验证：通达信 screener 跌停
        tdd_resp = self._mcp_call("tdx-connector_tdx_screener", {"message": "跌停", "pageSize": 20})
        tdd = _mcp_payload(tdd_resp) if tdd_resp else {}
        tdd_count = (tdd.get("meta") or {}).get("total")
        v_dt = validate_number((dt.get("count"), "东财跌停池API"), (tdd_count, "通达信MCP选股(跌停)"),
                               "跌停家数", rel_tol=0.1, abs_tol=2)

        # THS 兜底：如果 ego 和 MCP 都拿不到数据，用 THS 涨跌停池
        ths = get_ths_client()
        ths_lup_count = None
        ths_ldown_count = None
        ths_lbreak_count = None
        ths_max_boards = None
        ths_board_dist = None
        if not v_zt["value"] or not v_dt["value"]:
            try:
                ths_lup = ths.limit_up_pool(report_date)
                ths_lup_count = len(ths_lup) if ths_lup else None
                # 计算连板分布
                if ths_lup:
                    boards = {}
                    for it in ths_lup:
                        b = it.get("continuous_boards") or it.get("boards") or 1
                        boards[str(b)] = boards.get(str(b), 0) + 1
                    if boards:
                        ths_max_boards = max(int(k) for k in boards)
                        ths_board_dist = boards
            except Exception:
                pass
            try:
                ths_ldown = ths.limit_down_pool(report_date)
                ths_ldown_count = len(ths_ldown) if ths_ldown else None
            except Exception:
                pass
            try:
                ths_lbreak = ths.limit_break_pool(report_date)
                ths_lbreak_count = len(ths_lbreak) if ths_lbreak else None
            except Exception:
                pass
            if ths_lup_count:
                self._add_source("同花顺API(涨停池)")
            if ths_ldown_count:
                self._add_source("同花顺API(跌停池)")
            if ths_lbreak_count:
                self._add_source("同花顺API(炸板池)")

        final_zt = v_zt["value"] or ths_lup_count
        final_dt = v_dt["value"] or ths_ldown_count
        final_zb = zb.get("count") or ths_lbreak_count
        final_boards = zt.get("max_boards") or ths_max_boards
        final_dist = zt.get("board_dist") or ths_board_dist

        emotion = {
            "date": zt.get("qdate") or report_date,
            "limit_up": final_zt,
            "limit_down": final_dt,
            "max_boards": final_boards,
            "board_dist": final_dist,
            "break_count": final_zb,
            "validated": v_zt["validated"] and v_dt["validated"],
            "sources": list(dict.fromkeys(v_zt["sources"] + v_dt["sources"] + ["东财涨停池API", "东财跌停池API", "东财炸板池API"])),
            "notes": [
                v_zt["note"],
                v_dt["note"],
                "炸板数为东财炸板池API单源",
                "最高连板由东财涨停池数据计算（lbc 最大值），单源",
            ],
        }
        self._add_source("东财涨停池API")
        self._add_source("东财跌停池API")
        self._add_source("东财炸板池API")
        return emotion

    # ---------- 板块 ----------
    def _collect_sectors(self, raw: dict):
        ind = parse_boards(raw.get("board_industry"))
        con = parse_boards(raw.get("board_concept"))
        # THS 兜底：如果 ego 板块数据为空，用同花顺指数数据
        if not ind or not con:
            try:
                ths = get_ths_client()
                if not ind:
                    ths_ind = ths.ths_index_list(tag="industry")
                    if ths_ind:
                        ind = []
                        for idx, item in enumerate(ths_ind[:20], 1):
                            snap = ths.ths_index_snapshot(item.get("thscode", ""))
                            ind.append({
                                "rank": idx,
                                "name": item.get("name", ""),
                                "code": item.get("thscode", ""),
                                "pct": snap.get("price_change_ratio_pct"),
                                "amount": snap.get("turnover"),
                            })
                        if ind:
                            self._add_source("同花顺API(行业板块)")
            except Exception as e:
                log.warning("THS 板块兜底失败: %s", str(e)[:100])
        return {"industry": ind, "concept": con}

    # ---------- 资金 ----------
    def _collect_capital(self, raw: dict, trade_date: str):
        mf = parse_main_flow(raw.get("main_flow_index"))
        hsgt = parse_hsgt(raw.get("hsgt"))
        lhb = parse_lhb(raw.get("lhb"))
        return {"main_flow": mf, "hsgt": hsgt, "lhb": lhb}

    # ---------- 主流程 ----------
    def collect(self, target_date: date = None):
        target_date = target_date or date.today()
        trade_date, raw, parsed = self._find_trade_date(target_date)
        if not trade_date:
            raise RuntimeError("无法获取交易日数据（ego browser 采集失败）")

        report_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
        ego_index = parse_index(_try_load(raw.get("index")))  # raw 里 index 可能是 JSON 字符串，需先解析
        result = {
            "date": report_date,
            "market_index": self._collect_index(ego_index, trade_date),
            "emotion": self._collect_emotion(parsed, report_date),
            "sector_rank": self._collect_sectors(raw),
            "capital_flow": self._collect_capital(raw, trade_date),
            "source": sorted(set(self.sources)),
        }
        result["_meta"] = {
            "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "notes": self.notes,
        }
        return result
