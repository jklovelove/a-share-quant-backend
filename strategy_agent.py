"""
Phase 2 —— StrategyAgent（策略 / 研究员 Agent）
职责：多策略子信号生成 + 多因子评分（估值/资金/技术）+ 合成综合分
参考 Vibe-Trading「研究员团队」：不同 Agent 从不同角度论证，最终形成观点

设计要点：
- 每个基础策略产出 [-1, 0, +1] 的子信号（看空/中性/看多）。
- 多因子分别归一化到 0~100 分，再按权重合成综合分。
- 全程可解释：因子明细随信号一并下发给前端展示。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 默认因子权重（可在 strategy_config 表中由用户调整）
DEFAULT_WEIGHTS = {
    "valuation": 0.30,   # 估值水位
    "money": 0.25,       # 资金流向
    "tech": 0.45,        # 技术形态
}


# ----------------------------------------------------------------------
# 基础策略子信号
# ----------------------------------------------------------------------
def signal_dual_ma(df: pd.DataFrame) -> pd.Series:
    """双均线策略：金叉看多(+1)，死叉看空(-1)，其余 0"""
    return df["cross"].fillna(0).astype(int)


def signal_macd(df: pd.DataFrame) -> pd.Series:
    """MACD 策略：红柱放大看多，绿柱放大看空；背离叠加"""
    hist = df["macd_hist"]
    out = pd.Series(0, index=df.index, dtype=int)
    out[hist > 0] = 1
    out[hist < 0] = -1
    # 背离增强：底背离强看多，顶背离强看空
    out[df["macd_div"] == 1] = 1
    out[df["macd_div"] == -1] = -1
    return out


def signal_rsi(df: pd.DataFrame, lower: int = 30, upper: int = 70) -> pd.Series:
    """RSI 策略：超卖看多，超买看空"""
    r = df["rsi_14"]
    out = pd.Series(0, index=df.index, dtype=int)
    out[r < lower] = 1
    out[r > upper] = -1
    return out


# ----------------------------------------------------------------------
# 多因子打分（0~100）
# ----------------------------------------------------------------------
def valuation_score(df: pd.DataFrame, lookback: int = 250) -> pd.Series:
    """
    估值水位分：用「收盘价在近两年区间中的百分位」做代理。
    价格越低（越便宜）分数越高。生产环境可替换为 PE/PB 历史分位。
    """
    def _pct(x: pd.Series) -> float:
        if len(x) < 2:
            return 50.0
        return float(x.iloc[-1] / (x.max() + 1e-9) * 100)

    return df["close"].rolling(lookback, min_periods=20).apply(_pct, raw=False)


def money_score(df: pd.DataFrame) -> pd.Series:
    """
    资金流向分（0~100）：
    - 若已合并真实主力净流入（Tushare/AKShare），用其滚动 Z-Score；
    - 否则回退到 MACD 红绿柱近似动能（沙箱/无权限时）。
    """
    if "main_net_inflow" in df.columns and df["main_net_inflow"].fillna(0).abs().sum() > 0:
        roll = df["main_net_inflow"].rolling(60, min_periods=5)
        z = (df["main_net_inflow"] - roll.mean()) / (roll.std() + 1e-9)
    else:
        roll = df["macd_hist"].rolling(60, min_periods=5)
        z = (df["macd_hist"] - roll.mean()) / (roll.std() + 1e-9)
    return (z.clip(-3, 3) / 6 + 0.5) * 100


def tech_score(df: pd.DataFrame) -> pd.Series:
    """技术形态分：均线排列 + 金叉 + MACD 方向 + RSI 反向 综合"""
    s = pd.Series(50.0, index=df.index)
    s += df["ma_arrange"] * 15          # 多头 +15 / 空头 -15
    s += df["cross"] * 10               # 金叉 +10 / 死叉 -10
    s += df["macd_div"] * 8             # 底背离 +8 / 顶背离 -8
    # RSI 反向：超卖(低)加分，超买(高)减分
    s += (50 - df["rsi_14"]).clip(-30, 30) * 0.3
    return s.clip(0, 100)


# ----------------------------------------------------------------------
# StrategyAgent 编排
# ----------------------------------------------------------------------
class StrategyAgent:
    def __init__(self, weights: dict | None = None):
        self.weights = weights or DEFAULT_WEIGHTS

    def analyze(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        在 DataAgent 产出的特征 DataFrame 上追加：
        - 各子策略信号
        - 三类因子分
        - composite_score（综合分 0~100）
        - 子信号共识度（用于置信度）
        """
        df = df.copy()
        # 子策略信号
        df["sig_dual_ma"] = signal_dual_ma(df)
        df["sig_macd"] = signal_macd(df)
        df["sig_rsi"] = signal_rsi(df)

        # 因子分
        df["valuation_score"] = valuation_score(df).fillna(50)
        df["main_inflow_score"] = money_score(df).fillna(50)
        df["tech_score"] = tech_score(df).fillna(50)

        # 综合分
        w = self.weights
        df["composite_score"] = (
            df["valuation_score"] * w["valuation"]
            + df["main_inflow_score"] * w["money"]
            + df["tech_score"] * w["tech"]
        ).clip(0, 100)

        # 子信号共识度：同向则置信度高
        subs = df[["sig_dual_ma", "sig_macd", "sig_rsi"]].sum(axis=1)
        df["signal_consensus"] = subs.abs().clip(0, 3) / 3  # 0~1
        return df

    def latest_factors(self, df: pd.DataFrame) -> dict:
        """取最新一行因子明细，供前端解释展示"""
        row = df.iloc[-1]
        return {
            "valuation_score": round(float(row["valuation_score"]), 2),
            "main_inflow_score": round(float(row["main_inflow_score"]), 2),
            "tech_score": round(float(row["tech_score"]), 2),
            "composite_score": round(float(row["composite_score"]), 2),
            "rsi_14": round(float(row["rsi_14"]), 2),
            "macd_hist": round(float(row["macd_hist"]), 4),
            "ma_arrange": int(row["ma_arrange"]),
            "signal_consensus": round(float(row["signal_consensus"]), 2),
        }
