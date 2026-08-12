"""H003 数据抓取 v2：Top300 × 400天，断点续传+重试退避+低频率"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.predict.alt_data import EgoDailyData, NODE, _kline_url, BASE
from app.config import paths

OUT = paths()["data"] / "h003_data_v2.json"
LIMIT = 400
BATCH = 8
MAX_RETRY = 4


def main():
    ego = EgoDailyData()
    # 1) 股票池（分页取 Top300）
    stocks = ego.top_amount_stocks(300)
    codes = [c for c, _ in stocks]
    print(f"股票池: {len(codes)} 只", flush=True)

    # 2) 已抓取部分（断点续传）
    done = {}
    if OUT.exists():
        try:
            done = {c: rows for c, rows in json.loads(OUT.read_text(encoding="utf-8"))["klines"].items()}
            print(f"已有缓存: {len(done)} 只", flush=True)
        except Exception:
            done = {}
    todo = [c for c in codes if c not in done]
    print(f"待抓取: {len(todo)} 只", flush=True)

    fail = {}
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        jobs = [{"label": c, "url": _kline_url(c, LIMIT), "referer": BASE} for c in batch]
        for attempt in range(1, MAX_RETRY + 1):
            script = (NODE.replace("BASE_PLACEHOLDER", json.dumps(BASE))
                          .replace("__JOBS__", json.dumps(jobs, ensure_ascii=False)))
            r = ego._run(script)
            got = 0
            for c, text in r.items():
                try:
                    j = json.loads(text)
                    kl = ((j or {}).get("data") or {}).get("klines") or []
                    rows = []
                    for k in kl:
                        p = k.split(",")
                        if len(p) >= 6:
                            rows.append([p[0], float(p[1]), float(p[2]), float(p[3]),
                                         float(p[4]), float(p[5]), float(p[6]) if len(p) > 6 else 0.0])
                    if rows:
                        rows.sort(key=lambda x: x[0])
                        done[c] = rows
                        got += 1
                except Exception:
                    continue
            ok_codes = set(done) & set(batch)
            if len(ok_codes) >= len(batch) * 0.7:
                break
            if attempt < MAX_RETRY:
                time.sleep(8 * attempt)  # 退避
        missing = [c for c in batch if c not in done]
        for c in missing:
            fail[c] = fail.get(c, 0) + 1
        # 增量保存
        OUT.write_text(json.dumps({"stocks": stocks, "klines": done}, ensure_ascii=False), encoding="utf-8")
        print(f"进度 {min(i + BATCH, len(todo))}/{len(todo)} | 累计 {len(done)}/{len(codes)} | 本批失败 {len(missing)}", flush=True)
        time.sleep(2)  # 低频，防限流

    print(f"完成: {len(done)}/{len(codes)} 只")
    if fail:
        print("持续失败:", list(fail.keys())[:20], f"({len(fail)}只)")
    d0 = min(r[0] for r in done.values() if r); d1 = max(r[-1][0] for r in done.values() if r)
    print(f"跨度: {d0} ~ {d1}")


if __name__ == "__main__":
    main()
