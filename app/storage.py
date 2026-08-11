"""本地存储：SQLite 历史库 + reports 目录 JSON"""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger("storage")

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_reports (
    date TEXT PRIMARY KEY,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    targets TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prediction_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    target_code TEXT NOT NULL,
    target_name TEXT NOT NULL,
    buy_price REAL,
    sell_price REAL,
    ret REAL,
    status TEXT,
    created_at TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, data_dir: Path, report_dir: Path):
        self.data_dir = data_dir
        self.report_dir = report_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = data_dir / "a_share.db"
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA)
        conn.commit()
        conn.close()

    def save_report(self, report: dict):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO daily_reports(date, report_json, created_at) VALUES(?,?,?)",
            (report["date"], json.dumps(report, ensure_ascii=False), datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        # 同时写 JSON 文件
        jp = self.report_dir / f"{report['date']}.json"
        jp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return jp

    def load_report(self, date_str: str):
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT report_json FROM daily_reports WHERE date=?", (date_str,)).fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
        jp = self.report_dir / f"{date_str}.json"
        if jp.exists():
            return json.loads(jp.read_text(encoding="utf-8"))
        return None

    def list_dates(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT date, created_at FROM daily_reports ORDER BY date DESC").fetchall()
        conn.close()
        return rows
