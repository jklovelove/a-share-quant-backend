"""
YahooSource —— 海外可达的真实行情源（A股/美股/全球）
实现 data_agent.DataSource 接口。

用途：AKShare 依赖东方财富/新浪，在海外网络环境常被区域/反爬限制（本沙箱实测拉不到）。
Yahoo Finance chart API 海外可达、免费、无需 key，且支持 A 股符号（如 600519.SS）。
因此把它作为「AKShare 不可达时的兜底真实源」，保证任何环境都能跑通真实数据。

要点：
- 必须用 requests.Session 先取 cookie（fc.yahoo.com），否则 chart 接口返回 429。
- 响应结构：chart.result[0].timestamp（UTC秒）+ indicators.quote[0].{open,high,low,close,volume}
- 符号转换：A股 .SH → .SS（如 600519.SH → 600519.SS），.SZ 不变。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd
import requests

from data_agent import DataSource

logger = logging.getLogger("quant.yahoo")
BASE = "https://query1.finance.yahoo.com"


class YahooSource(DataSource):
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        try:  # 预取 cookie，避免 chart 接口 429
            self.session.get("https://fc.yahoo.com", timeout=10)
        except Exception:
            pass

    @staticmethod
    def _to_yahoo_symbol(ts_code: str) -> str:
        if ts_code.upper().endswith(".SH"):
            return ts_code[:-3] + ".SS"
        return ts_code  # .SZ / .BJ / 美股原样

    def fetch_daily(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        symbol = self._to_yahoo_symbol(ts_code)
        p1 = int(datetime.strptime(start, "%Y%m%d").timestamp())
        p2 = int(datetime.strptime(end, "%Y%m%d").timestamp())
        resp = self.session.get(f"{BASE}/v8/finance/chart/{symbol}",
                                params={"period1": p1, "period2": p2, "interval": "1d"},
                                timeout=15)
        resp.raise_for_status()
        result = resp.json()["chart"]["result"][0]
        ts = result["timestamp"]
        q = result["indicators"]["quote"][0]
        dates = [int(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y%m%d")) for t in ts]
        typical = [(o + h + l + c) / 4 for o, h, l, c in
                   zip(q["open"], q["high"], q["low"], q["close"])]
        df = pd.DataFrame({
            "trade_date": dates,
            "open": q["open"], "high": q["high"], "low": q["low"],
            "close": q["close"], "vol": q["volume"],
            "amount": [t * v for t, v in zip(typical, q["volume"])],  # Yahoo 无成交额，估算
        })
        return df.dropna(subset=["close"]).sort_values("trade_date").reset_index(drop=True)

    def fetch_moneyflow(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        # Yahoo 免费层无资金流，返回空表；money_score 自动回退 MACD 近似
        return pd.DataFrame(columns=["trade_date", "main_net_inflow", "north_hold_ratio"])
