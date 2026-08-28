"""备用数据源：ego browser + 东财K线API 获取个股开盘价
用于 MCP（WorkBuddy）不可用时结算模拟盘推荐
"""
import json
import logging
from ..config import paths
import sqlite3
import subprocess
from pathlib import Path

log = logging.getLogger("alt_data")

BASE = "https://quote.eastmoney.com/ztb/"
NODE = r'''
const task = await useOrCreateTaskSpace('alt-kline')
try {
  await openOrReuseTab(BASE_PLACEHOLDER, { wait: true, timeout: 30 })
  await waitForLoad({ timeout: 15 })
  await wait(2)
  const JOBS = __JOBS__
  const results = {}
  for (const j of JOBS) {
    try {
      const inner = `(async () => {
        const url = ${JSON.stringify(j.url)};
        try {
          const res = await fetch(url, { headers: { 'Referer': ${JSON.stringify(j.referer)} } });
          return await res.text();
        } catch(e) { return 'FETCH_ERR:' + e.message; }
      })()`
      results[j.label] = await js(inner)
    } catch (e) { results[j.label] = 'JS_ERR:' + e.message }
  }
  cliLog(JSON.stringify(results))
} finally {
  try { await completeTaskSpace(task.id, { keep: false }) } catch (e) {}
}
'''


def _secid(code: str) -> str:
    code = code.split(".")[0]
    if code.startswith(("6", "68", "9")):
        return f"1.{code}"
    return f"0.{code}"


def _kline_url(code: str, limit: int = 6) -> str:
    secid = _secid(code)
    # push2his: 历史数据（含收盘价），push2delay: 实时数据（个股K线为空）
    return ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
            f"&klt=101&fqt=0&end=20500101&lmt={limit}")


class EgoOpenPrices:
    """通过 ego browser 抓东财日K，提取指定日期开盘价"""

    def __init__(self, cache_db: Path = None):
        self.cache_db = cache_db
        if cache_db:
            conn = sqlite3.connect(cache_db)
            conn.execute("CREATE TABLE IF NOT EXISTS ego_kline (code TEXT, date TEXT, open REAL, PRIMARY KEY(code, date))")
            conn.commit()
            conn.close()

    def _cached(self, code: str, date_str: str):
        if not self.cache_db:
            return None
        conn = sqlite3.connect(self.cache_db)
        row = conn.execute("SELECT open FROM ego_kline WHERE code=? AND date=?", (code, date_str)).fetchone()
        conn.close()
        return row[0] if row else None

    def _save(self, code: str, date_str: str, open_price):
        if not self.cache_db or not open_price:
            return
        conn = sqlite3.connect(self.cache_db)
        conn.execute("INSERT OR REPLACE INTO ego_kline(code, date, open) VALUES(?,?,?)",
                     (code, date_str, open_price))
        conn.commit()
        conn.close()

    def fetch_index_dates(self, end_date: str, limit: int = 15) -> list:
        """通过东财K线取上证指数交易日历（MCP 不可用时确定次日交易日）"""
        url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
               "?secid=1.000001&fields1=f1,f2,f3&fields2=f51&klt=101&fqt=0"
               f"&end=20500101&lmt={limit}")
        script = (NODE
                  .replace("BASE_PLACEHOLDER", json.dumps(BASE))
                  .replace("__JOBS__", json.dumps(
                      [{"label": "idx", "url": url, "referer": BASE}], ensure_ascii=False)))
        try:
            proc = subprocess.run(["ego-browser", "nodejs"], input=script,
                                  capture_output=True, text=True, timeout=180,
                                  env={"PATH": "/Users/yage/.local/bin:/usr/bin:/bin:/usr/local/bin"})
        except Exception as e:
            log.error("ego 指数日历失败: %s", str(e)[:120])
            return []
        for line in ((proc.stdout or "") + (proc.stderr or "")).splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    results = json.loads(line)
                    j = json.loads(results.get("idx") or "{}")
                    klines = ((j or {}).get("data") or {}).get("klines") or []
                    return sorted(k.split(",")[0] for k in klines)
                except Exception:
                    continue
        return []

    def fetch(self, date_str: str, codes: list) -> dict:
        """返回 {code: 开盘价}，只含能取到的"""
        opens, _ = self.fetch_with_close(date_str, codes)
        return opens

    def fetch_with_close(self, date_str: str, codes: list) -> tuple:
        """返回 ({code: 开盘价}, {code: 收盘价})，只含能取到的"""
        opens, closes = {}, {}
        todo = []
        for c in codes:
            v = self._cached(c, date_str)
            if v:
                opens[c] = v
            else:
                todo.append({"label": c, "url": _kline_url(c), "referer": BASE})
        if not todo:
            return opens, closes
        script = (NODE
                  .replace("BASE_PLACEHOLDER", json.dumps(BASE))
                  .replace("__JOBS__", json.dumps(todo, ensure_ascii=False)))
        try:
            proc = subprocess.run(["ego-browser", "nodejs"], input=script,
                                  capture_output=True, text=True, timeout=180,
                                  env={"PATH": "/Users/yage/.local/bin:/usr/bin:/bin:/usr/local/bin"})
        except Exception as e:
            log.error("ego browser 失败: %s", str(e)[:150])
            return opens, closes
        for line in ((proc.stdout or "") + (proc.stderr or "")).splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    results = json.loads(line)
                    for code, text in results.items():
                        try:
                            j = json.loads(text)
                            klines = ((j or {}).get("data") or {}).get("klines") or []
                            for k in klines:
                                parts = k.split(",")
                                if len(parts) >= 3 and parts[0] == date_str:
                                    opens[code] = float(parts[1])   # 开盘价
                                    closes[code] = float(parts[2])  # 收盘价
                                    self._save(code, date_str, float(parts[1]))
                                    break
                        except Exception:
                            continue
                    break
                except Exception:
                    continue
        return opens, closes


class EgoDailyData:
    """H003 独立回测用：东财 全市场成交额Top 股票 + 完整日K（不依赖 MCP）"""

    CLIST_URL = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz={n}&po=1&np=1&fltt=2&invt=2"
                 "&fid=f6&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14,f6")

    @staticmethod
    def _run(script: str):
        try:
            proc = subprocess.run(["ego-browser", "nodejs"], input=script, capture_output=True,
                                  text=True, timeout=300,
                                  env={"PATH": "/Users/yage/.local/bin:/usr/bin:/bin:/usr/local/bin"})
            out = (proc.stdout or "") + (proc.stderr or "")
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        return json.loads(line)
                    except Exception:
                        continue
            log.error("ego 无 JSON 返回: %s", out[:200])
        except Exception as e:
            log.error("ego 失败: %s", str(e)[:150])
        return {}

    def top_amount_stocks(self, n: int = 150) -> list:
        """沪深A股 按成交额 Top n（分页，单页上限100），返回 [(code, name)]"""
        out = []
        page = 1
        while len(out) < n and page <= 8:
            url = self.CLIST_URL.replace("pn=1&", f"pn={page}&").format(n=100)
            script = (NODE
                      .replace("BASE_PLACEHOLDER", json.dumps(BASE))
                      .replace("__JOBS__", json.dumps(
                          [{"label": "top", "url": url,
                            "referer": "https://quote.eastmoney.com/center/gridlist.html"}],
                          ensure_ascii=False)))
            r = self._run(script)
            j = json.loads(r.get("top") or "{}")
            diff = ((j or {}).get("data") or {}).get("diff") or []
            if not diff:
                break
            for d in diff:
                if d.get("f12") and (str(d.get("f12")), d.get("f14")) not in out:
                    out.append((str(d.get("f12")), d.get("f14")))
            page += 1
        return out[:n]

    def daily_klines(self, codes: list, limit: int = 300, batch: int = 20) -> dict:
        """批量取日K，返回 {code: [[date,open,close,high,low,volume,amount], ...]（按日期升序）}"""
        out = {}
        for i in range(0, len(codes), batch):
            batch_codes = codes[i:i + batch]
            jobs = [{"label": c, "url": _kline_url(c, limit), "referer": BASE} for c in batch_codes]
            script = (NODE
                      .replace("BASE_PLACEHOLDER", json.dumps(BASE))
                      .replace("__JOBS__", json.dumps(jobs, ensure_ascii=False)))
            r = self._run(script)
            for code, text in r.items():
                try:
                    j = json.loads(text)
                    klines = ((j or {}).get("data") or {}).get("klines") or []
                    rows = []
                    for k in klines:
                        p = k.split(",")
                        if len(p) >= 6:
                            rows.append([p[0], float(p[1]), float(p[2]), float(p[3]),
                                         float(p[4]), float(p[5]),
                                         float(p[6]) if len(p) > 6 else 0.0])
                    rows.sort(key=lambda x: x[0])
                    out[code] = rows
                except Exception:
                    continue
            log.info("已取 %d/%d 只", min(i + batch, len(codes)), len(codes))
        return out


class EgoBoardData:
    """东财行业板块（10日涨幅）与板块成分股——R21 7-10日强势板块口径（ego优先）"""

    SECTOR_URL = ("https://push2delay.eastmoney.com/api/qt/clist/get?pn=1&pz={n}&po=1&np=1&fltt=2&invt=2"
                  "&fid=f110&fs=m:90+t:2+f:!50&fields=f2,f3,f12,f14,f109,f110,f128")
    STOCK_URL = ("https://push2delay.eastmoney.com/api/qt/clist/get?pn=1&pz={n}&po=1&np=1&fltt=2&invt=2"
                 "&fid=f3&fs=b:{bk}&fields=f12,f14,f2,f3,f6,f8,f20,f100")

    def __init__(self, cache_db=None):
        self.ego = EgoDailyData()
        self.cache_db = cache_db

    def top_sectors_10d(self, n: int = 10) -> list:
        """东财行业板块按10日涨幅 Top n；返回 [{code,name,pct_10d,pct_5d,leader}]"""
        url = self.SECTOR_URL.format(n=n)
        script = (NODE.replace("BASE_PLACEHOLDER", json.dumps(BASE)).replace("__JOBS__", json.dumps(
            [{"label": "sectors", "url": url, "referer": "https://quote.eastmoney.com/center/boardlist.html"}],
            ensure_ascii=False)))
        r = self.ego._run(script)
        j = json.loads(r.get("sectors") or "{}")
        diff = ((j or {}).get("data") or {}).get("diff") or []
        out = []
        for i, d in enumerate(diff, 1):
            if d.get("f12"):
                out.append({"rank": i, "code": str(d.get("f12")), "name": d.get("f14"),
                            "pct_10d": d.get("f110"), "pct_5d": d.get("f109"),
                            "pct_today": d.get("f3"), "leader": d.get("f128")})
        return out[:n]

    def sector_stocks(self, bk: str, n: int = 8) -> list:
        """板块成分股按当日涨幅 Top n；返回 [_clean_rank_row 兼容字段]"""
        url = self.STOCK_URL.format(bk=bk, n=n)
        script = (NODE.replace("BASE_PLACEHOLDER", json.dumps(BASE)).replace("__JOBS__", json.dumps(
            [{"label": "st", "url": url, "referer": "https://quote.eastmoney.com/center/boardlist.html"}],
            ensure_ascii=False)))
        r = self.ego._run(script)
        j = json.loads(r.get("st") or "{}")
        diff = ((j or {}).get("data") or {}).get("diff") or []
        rows = []
        for d in diff:
            if not d.get("f12"):
                continue
            rows.append({
                "ticker": str(d.get("f12")), "name": d.get("f14"),
                "close": d.get("f2"), "change_ratio": d.get("f3"),
                "amount": d.get("f6"), "turnover_rate": d.get("f8"),
                "market_cap": d.get("f20"), "industry": d.get("f100"),
            })
        return rows


def recent_lhb(page_size: int = 100) -> dict:
    """东财龙虎榜近期明细（ego抓取，缓存到 ego_kline.db）
    返回 {code: {date, name, net, reason, seat}}"""
    import sqlite3 as _sq
    cache_db = paths()["data"] / "ego_kline.db"
    if cache_db:
        conn = _sq.connect(cache_db)
        conn.execute("CREATE TABLE IF NOT EXISTS ego_lhb (code TEXT PRIMARY KEY, info TEXT)")
        rows = conn.execute("SELECT code, info FROM ego_lhb").fetchall()
        conn.close()
        if rows:
            return {c: json.loads(i) for c, i in rows}
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
           "?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL"
           f"&pageSize={page_size}&sortColumns=TRADE_DATE&sortTypes=-1&source=WEB&client=WEB")
    script = (NODE.replace("BASE_PLACEHOLDER", json.dumps(BASE)).replace("__JOBS__", json.dumps(
        [{"label": "lhb", "url": url, "referer": "https://data.eastmoney.com/longhuzong/"}], ensure_ascii=False)))
    ego = EgoDailyData()
    r = ego._run(script)
    j = json.loads(r.get("lhb") or "{}")
    rows = ((j or {}).get("result") or {}).get("data") or []
    out = {}
    for d in rows:
        code = str(d.get("SECURITY_CODE") or "")
        if not code:
            continue
        out.setdefault(code, {"date": (d.get("TRADE_DATE") or "")[:10],
                              "name": d.get("SECURITY_NAME_ABBR"),
                              "net": d.get("BILLBOARD_NET_AMT"),
                              "reason": d.get("EXPLANATION")})
    if cache_db and out:
        conn = _sq.connect(cache_db)
        for c, info in out.items():
            conn.execute("INSERT OR REPLACE INTO ego_lhb(code, info) VALUES(?,?)", (c, json.dumps(info, ensure_ascii=False)))
        conn.commit(); conn.close()
    return out
