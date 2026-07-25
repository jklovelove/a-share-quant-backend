"""
TushareSource —— A 股真实数据源（优先于 AKShare）
实现 data_agent.DataSource 接口。

实测（免费初始积分）：daily / moneyflow 返回 code:40203 无权限，
需用户在 tushare.pro 积累积分（或参与社区任务）后方可调用。
一旦有权限，Tushare 提供 A 股最规范的日线 + 真实主力资金流，
是多因子中「资金流向因子」的最佳来源。

调用方式：直接 POST https://api.tushare.pro（不依赖 tushare SDK，更轻量）
文档：https://tushare.pro/document/2
"""
from __future__ import annotations

import logging

import pandas as pd
import requests

from data_agent import DataSource

logger = logging.getLogger("quant.tushare")
API = "https://api.tushare.pro"


class TushareSource(DataSource):
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("未配置 TUSHARE_TOKEN")
        self.api_key = api_key

    def _post(self, api_name: str, params: dict, fields: str) -> dict:
        resp = requests.post(API, json={
            "api_name": api_name, "token": self.api_key,
            "params": params, "fields": fields,
        }, timeout=10)
        resp.raise_for_status()
        j = resp.json()
        if j.get("code") != 0:
            # 如免费积分不足会走到这里
            raise RuntimeError(f"Tushare {api_name} 错误: {j.get('msg')}")
        return j["data"]

    def fetch_daily(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        data = self._post("daily",
            {"ts_code": ts_code, "start_date": start, "end_date": end},
            "ts_code,trade_date,open,high,low,close,vol,amount")
        df = pd.DataFrame(data["items"], columns=data["fields"])
        # 类型校正：vol 单位=手，amount 单位=千元（Tushare 约定）
        for c in ["open", "high", "low", "close", "vol", "amount"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["trade_date"] = df["trade_date"].astype(int)
        return df.sort_values("trade_date").reset_index(drop=True)

    def fetch_moneyflow(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        """
        真实主力净流入（单位：元）。这是多因子『资金流向因子』的最佳数据。
        注意：免费账户若无权限，_post 会抛错，由调用方忽略并回退。
        """
        data = self._post("moneyflow",
            {"ts_code": ts_code, "start_date": start, "end_date": end},
            "ts_code,trade_date,main_net_inflow")
        df = pd.DataFrame(data["items"], columns=data["fields"])
        df["main_net_inflow"] = pd.to_numeric(df["main_net_inflow"], errors="coerce")
        df["trade_date"] = df["trade_date"].astype(int)
        df["north_hold_ratio"] = 0.0
        return df.sort_values("trade_date").reset_index(drop=True)
