"""
Phase 1 —— DataAgent（数据 Agent）
职责：行情获取 → 清洗 → 技术指标计算
参考 Vibe-Trading「分析师团队」：并行收集并预处理市场信息

行业最佳实践：
1. 数据源做接口抽象（DataSource），AKShare / Tushare 可热插拔、可单测。
2. 技术指标函数全部为纯函数（输入 DataFrame / Series，输出 Series），便于复用与测试。
3. 复权统一用「前复权(qfq)」，保证回测与实盘信号一致。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger("quant.data_agent")


# ----------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------
@dataclass
class Quote:
    ts_code: str
    trade_date: int
    open: float
    high: float
    low: float
    close: float
    vol: float
    amount: float


# ----------------------------------------------------------------------
# 数据源抽象层
# ----------------------------------------------------------------------
class DataSource(ABC):
    """屏蔽 AKShare / Tushare 差异，业务层只依赖该接口"""

    @abstractmethod
    def fetch_daily(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        """返回列: trade_date(int YYYYMMDD), open, high, low, close, vol, amount"""

    @abstractmethod
    def fetch_moneyflow(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        """返回列: trade_date, main_net_inflow, north_hold_ratio"""


class AKShareSource(DataSource):
    """AKShare 实现：免费、无需 token"""

    def fetch_daily(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        import akshare as ak

        symbol = ts_code.split(".")[0]  # '600519.SH' -> '600519'
        sina = ("sh" if ts_code.upper().endswith(".SH") else "sz") + symbol  # sh600519
        # 优先新浪源（海外/沙箱网络可达）；失败再回退东方财富
        try:
            raw = ak.stock_zh_a_daily(symbol=sina, start_date=start, end_date=end, adjust="qfq")
            df = raw.rename(columns={"date": "trade_date", "volume": "vol"})
            df["trade_date"] = df["trade_date"].astype(str).str.replace("-", "").astype(int)
            df = df[["trade_date", "open", "high", "low", "close", "vol", "amount"]]
            return df.sort_values("trade_date").reset_index(drop=True)
        except Exception as e:
            logger.warning("AKShare 新浪源失败，回退东方财富: %s", e)
            raw = ak.stock_zh_a_hist(
                symbol=symbol, period="daily",
                start_date=start, end_date=end, adjust="qfq",
            )
            rename = {
                "日期": "trade_date", "开盘": "open", "最高": "high",
                "最低": "low", "收盘": "close", "成交量": "vol", "成交额": "amount",
            }
            df = raw.rename(columns=rename)
            df["trade_date"] = df["trade_date"].astype(str).str.replace("-", "").astype(int)
            df = df[["trade_date", "open", "high", "low", "close", "vol", "amount"]]
            return df.sort_values("trade_date").reset_index(drop=True)

    def fetch_moneyflow(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        import akshare as ak

        symbol = ts_code.split(".")[0]
        raw = ak.stock_individual_fund_flow(stock=symbol, market="sh" if "SH" in ts_code else "sz")
        rename = {"日期": "trade_date", "主力净流入-净额": "main_net_inflow"}
        df = raw.rename(columns=rename)
        df["trade_date"] = df["trade_date"].astype(str).str.replace("-", "").astype(int)
        df = df[["trade_date", "main_net_inflow"]]
        # 北向持仓比例需另取，示例留空
        df["north_hold_ratio"] = 0.0
        return df.sort_values("trade_date").reset_index(drop=True)


# ----------------------------------------------------------------------
# 技术指标（纯函数，可单测）
# ----------------------------------------------------------------------
def sma(close: pd.Series, n: int) -> pd.Series:
    """简单移动平均"""
    return close.rolling(n, min_periods=1).mean()


def ema(close: pd.Series, n: int) -> pd.Series:
    """指数移动平均"""
    return close.ewm(span=n, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """返回 (DIF, DEA, HIST) 三个 Series —— 借鉴 Vibe-Trading 技术因子"""
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, signal)
    hist = (dif - dea) * 2
    return dif, dea, hist


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """相对强弱指标：>70 超买，<30 超卖"""
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n, min_periods=1).mean()
    loss = -delta.clip(upper=0).rolling(n, min_periods=1).mean()
    rs = gain / (loss + 1e-9)
    return 100 - 100 / (1 + rs)


def ma_arrangement(close: pd.Series, fast: int = 5, slow: int = 60) -> pd.Series:
    """
    均线排列因子：-1 空头排列 / 0 震荡 / +1 多头排列
    短线在长线上方且向上 = 多头；反之空头
    """
    ma_f = sma(close, fast)
    ma_s = sma(close, slow)
    out = pd.Series(0, index=close.index, dtype=int)
    bull = (ma_f > ma_s) & (ma_f > ma_f.shift(1))
    bear = (ma_f < ma_s) & (ma_f < ma_f.shift(1))
    out[bull] = 1
    out[bear] = -1
    return out


def cross_signal(ma_fast: pd.Series, ma_slow: pd.Series) -> pd.Series:
    """
    双均线交叉：1 金叉 / -1 死叉 / 0 其他
    金叉 = 快线上穿慢线；死叉 = 快线下穿慢线
    """
    diff = ma_fast - ma_slow
    prev = diff.shift(1)
    out = pd.Series(0, index=ma_fast.index, dtype=int)
    out[(prev <= 0) & (diff > 0)] = 1    # 金叉
    out[(prev >= 0) & (diff < 0)] = -1   # 死叉
    return out


def macd_divergence(close: pd.Series, hist: pd.Series, window: int = 20) -> pd.Series:
    """
    MACD 背离识别（量化版「研究员观点」）：
    +1 底背离（价创新低、柱未新低，看多）
    -1 顶背离（价创新高、柱未新高，看空）
     0 无
    """
    out = pd.Series(0, index=close.index, dtype=int)
    for i in range(window, len(close)):
        price_win = close[i - window:i]
        hist_win = hist[i - window:i]
        # 价格新低但柱更高 → 底背离
        if close[i] <= price_win.min() and hist[i] > hist_win.min():
            out[i] = 1
        # 价格新高但柱更低 → 顶背离
        elif close[i] >= price_win.max() and hist[i] < hist_win.max():
            out[i] = -1
    return out


# ----------------------------------------------------------------------
# DataAgent 编排：获取 + 清洗 + 指标
# ----------------------------------------------------------------------
class DataAgent:
    """Phase 1 编排器：对外暴露「带指标的基础数据」"""

    def __init__(self, source: DataSource):
        self.source = source

    def build_features(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        df = self.source.fetch_daily(ts_code, start, end)
        df = self._clean(df)
        # —— 技术指标注入 ——
        df["ma5"] = sma(df["close"], 5)
        df["ma20"] = sma(df["close"], 20)
        df["ma60"] = sma(df["close"], 60)
        df["dif"], df["dea"], df["macd_hist"] = macd(df["close"])
        df["rsi_14"] = rsi(df["close"], 14)
        df["ma_arrange"] = ma_arrangement(df["close"])
        df["cross"] = cross_signal(df["ma20"], df["ma60"])
        df["macd_div"] = macd_divergence(df["close"], df["macd_hist"])
        # 涨跌幅（便于因子与回测）
        df["pct_chg"] = df["close"].pct_change().fillna(0)

        # 尝试合并真实资金流（Tushare/AKShare）；无权限或 SDK 未装则忽略，
        # strategy_agent.money_score 会自动回退到 MACD 柱近似。
        try:
            mf = self.source.fetch_moneyflow(ts_code, start, end)
            if mf is not None and not mf.empty and "main_net_inflow" in mf:
                df = df.merge(
                    mf[["trade_date", "main_net_inflow", "north_hold_ratio"]],
                    on="trade_date", how="left",
                )
        except Exception:
            pass
        return df

    @staticmethod
    def _clean(df: pd.DataFrame) -> pd.DataFrame:
        """清洗：去空、去重、类型校正、按日期升序"""
        df = df.dropna(subset=["close"]).drop_duplicates(subset=["trade_date"])
        for col in ["open", "high", "low", "close", "vol", "amount"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.sort_values("trade_date").reset_index(drop=True)
