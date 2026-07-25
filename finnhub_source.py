"""
FinnHubSource —— 真实数据源（美股 / 全球）
实现 data_agent.DataSource 接口，与 AKShareSource 可互换。

实测结论（免费账户）：
- ✅ 美股（如 AAPL）可拉取真实日线行情
- ❌ A 股（600519.SS）/ 港股（0700.HK）返回 access error（免费层不开放）
因此 main.py 按代码路由：A股→AKShareSource，其余→FinnHubSource

文档：https://finnhub.io/docs/api#stock-candles
限制：免费账户 60 次/分钟；日线(candle resolution=D)历史深度有限，单次请求即可取回区间全部。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd
import requests

from data_agent import DataSource

logger = logging.getLogger("quant.finnhub")

BASE = "https://finnhub.io/api/v1"


class FinnHubSource(DataSource):
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("未配置 FINNHUB_API_KEY，请先 export 环境变量")
        self.api_key = api_key

    def fetch_daily(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        """
        FinnHub 用美股符号（如 AAPL）。若传入 A 股代码会被服务端拒绝，
        由 main.py 的数据源路由保证此处只收到美股/全球符号。
        """
        symbol = ts_code.split(".")[0] if "." in ts_code else ts_code
        start_ts = int(datetime.strptime(start, "%Y%m%d").timestamp())
        end_ts = int(datetime.strptime(end, "%Y%m%d").timestamp())

        resp = requests.get(
            f"{BASE}/stock/candle",
            params={"symbol": symbol, "resolution": "D",
                    "from": start_ts, "to": end_ts, "token": self.api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("s") != "ok" or not data.get("t"):
            raise RuntimeError(f"FinnHub 无数据或区间无效: {data}")

        # 时间戳为 UTC 秒 → 转 YYYYMMDD
        dates = [int(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y%m%d"))
                 for t in data["t"]]
        # FinnHub 不提供成交额(amount)，用 典型价×成交量 近似
        typical = [(o + h + l + c) / 4 for o, h, l, c in
                   zip(data["o"], data["h"], data["l"], data["c"])]
        amount = [t * v for t, v in zip(typical, data["v"])]

        df = pd.DataFrame({
            "trade_date": dates,
            "open": data["o"], "high": data["h"], "low": data["l"],
            "close": data["c"], "vol": data["v"], "amount": amount,
        })
        return df.sort_values("trade_date").reset_index(drop=True)

    def fetch_moneyflow(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        """
        FinnHub 免费层无「主力净流入」端点。这里返回空表，
        主链路的多因子中『资金因子』实际由 macd_hist 近似（见 strategy_agent.money_score），
        因此不影响分析与回测。如需要，可接入 /stock/insider-transactions 等付费端点扩展。
        """
        return pd.DataFrame(columns=["trade_date", "main_net_inflow", "north_hold_ratio"])

    def fetch_quote(self, ts_code: str) -> dict:
        """
        实时报价快照（FinnHub 免费层真正可用的真实数据能力）。
        对应『标的监控模块』的实时/盘后行情展示。
        返回字段：price 现价、change 涨跌额、pct 涨跌幅%、high/low/open/prev_close。
        """
        symbol = ts_code.split(".")[0] if "." in ts_code else ts_code
        resp = requests.get(f"{BASE}/quote",
                            params={"symbol": symbol, "token": self.api_key}, timeout=10)
        resp.raise_for_status()
        d = resp.json()
        return {
            "price": d.get("c"), "change": d.get("d"), "pct": d.get("dp"),
            "high": d.get("h"), "low": d.get("l"),
            "open": d.get("o"), "prev_close": d.get("pc"),
        }
