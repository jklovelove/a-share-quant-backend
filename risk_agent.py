"""
Phase 3 —— RiskAgent（风控 Agent）
职责：对综合分做「激进 / 中立 / 保守」三视角风险校准
参考 Vibe-Trading「风控团队」：从不同风险偏好角度评估，避免单边极端

核心思想：
- 保守视角：在波动率/回撤风险高时，显著下调综合分，规避追高。
- 中立视角：轻度校准，仅在极端高估时介入。
- 激进视角：允许在强技术信号下保留较高分，但设置硬上限防止失控。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class RiskAgent:
    def __init__(self, conservative_cap: float = 0.7,
                 neutral_cap: float = 0.85,
                 aggressive_cap: float = 0.95):
        self.caps = {
            "conservative": conservative_cap,
            "neutral": neutral_cap,
            "aggressive": aggressive_cap,
        }

    def _volatility(self, df: pd.DataFrame, window: int = 20) -> float:
        """近期年化波动率代理：日收益标准差"""
        if len(df) < 2:
            return 0.0
        return float(df["pct_chg"].tail(window).std() * np.sqrt(252))

    def _overheat(self, df: pd.DataFrame) -> float:
        """过热度：RSI 越高、均线偏离越大 → 越热（0~1）"""
        rsi = float(df["rsi_14"].iloc[-1]) if "rsi_14" in df else 50
        rsi_heat = max(0.0, (rsi - 70) / 30)  # 70 以上开始过热
        return min(1.0, rsi_heat)

    def calibrate(self, df: pd.DataFrame, composite_score: float) -> dict:
        """
        对综合分做三视角衰减，返回各视角结论。
        penalty 与波动率、过热度正相关。
        """
        vol = self._volatility(df)
        heat = self._overheat(df)
        # 风险惩罚：波动率(封顶40%) + 过热度(封顶40%)，合计最多 0.8
        penalty = min(0.8, vol / 0.5 * 0.4 + heat * 0.4)

        result = {}
        for view, cap in self.caps.items():
            # 保守视角惩罚最重，激进最轻
            weight = {"conservative": 1.0, "neutral": 0.6, "aggressive": 0.3}[view]
            adjusted = composite_score * (1 - penalty * weight)
            adjusted = min(adjusted, composite_score * cap)  # 硬上限
            result[view] = round(float(adjusted), 2)

        return {
            "volatility": round(vol, 4),
            "overheat": round(heat, 3),
            "penalty": round(penalty, 3),
            "scores": result,
        }

    def decide_view(self, risk: dict) -> str:
        """决策层用：取三视角中「最审慎」的结论作为最终风控结论，守住本金"""
        return min(risk["scores"], key=lambda k: risk["scores"][k])
