"""ego browser 抓取东方财富公开数据（通过浏览器内 fetch 东财公开接口）"""
import json
import logging
import subprocess
from datetime import date

log = logging.getLogger("ego_scraper")

BASE_PAGE = "https://quote.eastmoney.com/ztb/"
NODE_WRAPPER_TMPL = r'''
const task = await useOrCreateTaskSpace('a-share-collector')
try {
  await openOrReuseTab(BASE_PAGE_PLACEHOLDER, { wait: true, timeout: 30 })
  await waitForLoad({ timeout: 15 })
  await wait(2)
  const TASKS = __TASKS__
  const results = {}
  for (const t of TASKS) {
    try {
      const inner = `(async () => {
        const url = ${JSON.stringify(t.url)};
        try {
          const res = await fetch(url, { headers: { 'Referer': ${JSON.stringify(t.referer)} } });
          return await res.text();
        } catch(e) { return 'FETCH_ERR:' + e.message; }
      })()`
      const r = await js(inner)
      results[t.label] = r
    } catch (e) { results[t.label] = 'JS_ERR:' + e.message }
  }
  cliLog(JSON.stringify(results))
} finally {
  try { await completeTaskSpace(task.id, { keep: false }) } catch (e) {}
}
'''

UT = "7eea3edcaed734bea9cbfc24409ed989"


def _t(label, url, referer=BASE_PAGE):
    return {"label": label, "url": url, "referer": referer}


def build_tasks(d: str) -> list:
    """d: YYYYMMDD 交易日"""
    return [
        # 指数
        _t("index",
           "https://push2.eastmoney.com/api/qt/ulist.np/get"
           "?secids=1.000001,0.399001,0.399006,1.000688"
           "&fields=f2,f3,f4,f6,f12,f14&fltt=2&invt=2"),
        # 涨停池（全量，用于统计连板高度）
        _t("zt_pool",
           f"https://push2ex.eastmoney.com/getTopicZTPool?ut={UT}&dpt=wz.ztzt"
           f"&Pageindex=0&pagesize=500&sort=fbt%3Aasc&date={d}"),
        # 跌停池
        _t("dt_pool",
           f"https://push2ex.eastmoney.com/getTopicDTPool?ut={UT}&dpt=wz.ztzt"
           f"&Pageindex=0&pagesize=300&sort=fund%3Aasc&date={d}"),
        # 炸板池
        _t("zb_pool",
           f"https://push2ex.eastmoney.com/getTopicZBPool?ut={UT}&dpt=wz.ztzt"
           f"&Pageindex=0&pagesize=300&sort=fund%3Aasc&date={d}"),
        # 行业板块 Top10（涨幅）
        _t("board_industry",
           "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=1&np=1"
           "&fltt=2&invt=2&fid=f3&fs=m:90+t:2+f:!50"
           "&fields=f2,f3,f12,f14,f62,f104,f105,f128,f140,f136"),
        # 概念板块 Top10（涨幅）
        _t("board_concept",
           "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=1&np=1"
           "&fltt=2&invt=2&fid=f3&fs=m:90+t:3+f:!50"
           "&fields=f2,f3,f12,f14,f62,f104,f105,f128,f140,f136"),
        # 大盘主力资金（上证，日线最近1天）
        _t("main_flow_index",
           "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?lmt=1&klt=101"
           "&secid=1.000001&fields1=f1,f2,f3,f7"
           "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"),
        # 沪深港通（北向成交额）
        _t("hsgt",
           "https://push2.eastmoney.com/api/qt/kamt/get?fields1=f1,f2,f3,f4"
           "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"),
        # 龙虎榜（最新）
        _t("lhb",
           "https://datacenter-web.eastmoney.com/api/data/v1/get"
           "?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL&pageSize=30"
           "&sortColumns=TRADE_DATE&sortTypes=-1&source=WEB&client=WEB",
           referer="https://data.eastmoney.com/longhuzong/"),
    ]


class EgoScraper:
    def __init__(self, browser_path: str = "ego-browser", timeout: int = 180):
        self.browser_path = browser_path
        self.timeout = timeout

    def fetch(self, tasks: list) -> dict:
        tasks_json = json.dumps(tasks, ensure_ascii=False)
        script = (NODE_WRAPPER_TMPL
                  .replace("BASE_PAGE_PLACEHOLDER", json.dumps(BASE_PAGE))
                  .replace("__TASKS__", tasks_json))
        cmd = [self.browser_path, "nodejs"]
        try:
            proc = subprocess.run(
                cmd, input=script, capture_output=True, text=True,
                timeout=self.timeout, env={"PATH": "/Users/yage/.local/bin:/usr/bin:/bin:/usr/local/bin"},
            )
        except subprocess.TimeoutExpired:
            log.error("ego browser 超时")
            return {}
        out = (proc.stdout or "") + (proc.stderr or "")
        # cliLog 输出的 JSON 行
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except Exception:
                    continue
        log.error("ego browser 未返回 JSON，原始输出: %s", out[:800])
        return {}


# ---------------- 解析器 ----------------
def _num(v, default=None):
    try:
        return float(v)
    except Exception:
        return default


def parse_index(raw: dict) -> dict:
    """东财指数 ulist -> {name: {close,pct,change,amount}}"""
    out = {}
    diff = ((raw or {}).get("data") or {}).get("diff") or []
    for row in diff:
        out[row.get("f14")] = {
            "code": row.get("f12"),
            "close": _num(row.get("f2")),
            "pct": _num(row.get("f3")),
            "change": _num(row.get("f4")),
            "amount": _num(row.get("f6")),  # 元
        }
    return out


def parse_pool_counts(raw: dict) -> dict:
    """涨停/跌停/炸板池 -> {count, qdate, max_boards}"""
    data = (raw or {}).get("data") or {}
    count = data.get("tc")
    qdate = data.get("qdate")
    pool = data.get("pool") or []
    max_boards = 0
    board_dist = {}
    for p in pool:
        lbc = int(p.get("lbc") or 0)
        max_boards = max(max_boards, lbc)
        board_dist[lbc] = board_dist.get(lbc, 0) + 1
    return {
        "count": count,
        "qdate": str(qdate) if qdate else None,
        "max_boards": max_boards if pool else None,
        "board_dist": board_dist,
    }


def parse_boards(raw: dict) -> list:
    diff = ((raw or {}).get("data") or {}).get("diff") or []
    out = []
    for i, row in enumerate(diff, 1):
        out.append({
            "rank": i,
            "code": row.get("f12"),
            "name": row.get("f14"),
            "pct": _num(row.get("f3")),
            "main_inflow": _num(row.get("f62")),  # 元，主力净流入
            "up": row.get("f104"),
            "down": row.get("f105"),
            "leader": row.get("f128"),
            "leader_code": row.get("f140"),
            "leader_pct": _num(row.get("f136")),
        })
    return out


def parse_main_flow(raw: dict) -> dict:
    """上证日主力资金 -> {date, main_net(元), ...}"""
    data = (raw or {}).get("data") or {}
    klines = data.get("klines") or []
    if not klines:
        return {}
    fields = klines[-1].split(",")
    # f51日期,f52主力净流入,f53小单,f54中单,f55大单,f56超大单, f57主力净占比...
    if len(fields) >= 6:
        return {
            "date": fields[0],
            "main_net": _num(fields[1]),
            "small_net": _num(fields[2]),
            "medium_net": _num(fields[3]),
            "large_net": _num(fields[4]),
            "xlarge_net": _num(fields[5]),
        }
    return {}


def parse_hsgt(raw: dict) -> dict:
    """北向（陆股通）成交额，万元 -> 亿元"""
    data = (raw or {}).get("data") or {}
    out = {"date": data.get("date2"), "north_turnover": {}, "net_buy": "数据不可获取",
           "note": "2024年8月19日起，北向资金实时净买入额停止披露，仅披露成交总额"}
    for key, label in (("hk2sh", "港>沪"), ("hk2sz", "港>深")):
        d = data.get(key) or {}
        amt = _num(d.get("buySellAmt"))
        if amt is not None:
            out["north_turnover"][label] = round(amt / 10000.0, 2)  # 万元->亿元
    return out


def parse_lhb(raw: dict) -> list:
    result = (raw or {}).get("result") or {}
    rows = result.get("data") or []
    out = []
    for r in rows:
        out.append({
            "trade_date": (r.get("TRADE_DATE") or "")[:10],
            "code": r.get("SECURITY_CODE"),
            "name": r.get("SECURITY_NAME_ABBR"),
            "close": _num(r.get("CLOSE_PRICE")),
            "change_pct": _num(r.get("CHANGE_RATE")),
            "net_amt": _num(r.get("BILLBOARD_NET_AMT")),  # 元
            "reason": r.get("EXPLANATION"),
        })
    return out
