"""
盘后定时任务：拉真实行情 → 分析 → 写库（factor_snapshot / signal_record）。
对应「标的监控模块」的自动化更新，可配合 crontab / APScheduler 每天 16:00 运行。

用法：
    python tasks.py                 # 更新内置标的列表
    python tasks.py 600519.SH 000001.SZ  # 指定标的

库：默认 SQLite（quant.db）；生产改 config.DB_URL 为 MySQL 即可，schema 见 db_schema.sql。
"""
import sqlite3
import os
import sys
from datetime import datetime, timedelta

from main import _load_features
from strategy_agent import StrategyAgent
from decision_agent import run_pipeline

DB = os.getenv("QUANT_DB", "quant.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS factor_snapshot (
    ts_code TEXT, trade_date INTEGER,
    valuation_score REAL, main_inflow_score REAL, tech_score REAL, composite_score REAL,
    PRIMARY KEY (ts_code, trade_date));
CREATE TABLE IF NOT EXISTS signal_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_code TEXT, trade_date INTEGER,
    signal TEXT, confidence REAL, composite_score REAL, risk_view TEXT);
"""


def init_db():
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def run_for(ts_code: str):
    end = datetime.today().strftime("%Y%m%d")
    start = (datetime.today() - timedelta(days=365)).strftime("%Y%m%d")
    # 真实行情（AKShare 新浪源优先）
    df = _load_features(ts_code, start, end)
    df = StrategyAgent().analyze(df)
    res = run_pipeline(ts_code, df)
    row = df.iloc[-1]

    conn = sqlite3.connect(DB)
    conn.execute(
        """INSERT OR REPLACE INTO factor_snapshot
           (ts_code,trade_date,valuation_score,main_inflow_score,tech_score,composite_score)
           VALUES (?,?,?,?,?,?)""",
        (ts_code, int(row["trade_date"]), res["factors"]["valuation_score"],
         res["factors"]["main_inflow_score"], res["factors"]["tech_score"], res["composite_score"]),
    )
    conn.execute(
        """INSERT INTO signal_record
           (ts_code,trade_date,signal,confidence,composite_score,risk_view)
           VALUES (?,?,?,?,?,?)""",
        (ts_code, int(row["trade_date"]), res["signal"], res["confidence"],
         res["composite_score"], res["risk_view"]),
    )
    conn.commit()
    conn.close()
    print(f"[{ts_code}] 信号={res['signal']} 综合分={res['composite_score']} 已写入 {DB}")


if __name__ == "__main__":
    init_db()
    codes = sys.argv[1:] or ["600519.SH", "000001.SZ"]
    for c in codes:
        run_for(c)
    print("盘后任务完成")
