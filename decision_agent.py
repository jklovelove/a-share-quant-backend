"""
Phase 4 —— DecisionAgent（决策 Agent）
职责：综合分（经风控校准）映射为 买入/持有/卖出 + 置信度评分
参考 Vibe-Trading「决策层」：综合各团队结论，形成最终可执行建议

设计要点：
- 智能化而非自动化：只输出建议与置信度，绝不自动下单。
- 置信度 = 子信号共识度 × 数据充足度，避免「低质量高分」误判。
"""
from __future__ import annotations

import pandas as pd


# 信号阈值（综合分，0~100）
BUY_THRESHOLD = 70
SELL_THRESHOLD = 30


class DecisionAgent:
    def decide(self, df: pd.DataFrame, risk: dict) -> dict:
        row = df.iloc[-1]
        composite = float(row["composite_score"])
        consensus = float(row["signal_consensus"])       # 0~1
        # 取风控最审慎视角得分作为最终分（守本金）；缺省时自动取最低分视角
        view = risk.get("_chosen_view") or min(risk["scores"], key=risk["scores"].get)
        final_score = risk["scores"][view]

        # —— 信号映射 ——
        if final_score >= BUY_THRESHOLD:
            signal = "BUY"
        elif final_score <= SELL_THRESHOLD:
            signal = "SELL"
        else:
            signal = "HOLD"

        # —— 置信度 ——
        # 数据充足度：样本越多越可信
        sample_sufficiency = min(1.0, len(df) / 250)
        confidence = round(final_score * consensus * sample_sufficiency, 2)

        return {
            "signal": signal,
            "confidence": confidence,
            "final_score": round(final_score, 2),
            "composite_score": round(composite, 2),
            "risk_view": view,
            "volatility": risk["volatility"],
        }


def run_pipeline(ts_code: str, df: pd.DataFrame, weights: dict | None = None) -> dict:
    """
    串联 Phase2-4 的一站式入口（供 API 调用）。
    返回：最新信号 + 因子明细 + 风控结论。
    """
    from strategy_agent import StrategyAgent
    from risk_agent import RiskAgent
    from decision_agent import DecisionAgent

    strat = StrategyAgent(weights)
    df = strat.analyze(df)

    risk_agent = RiskAgent()
    risk = risk_agent.calibrate(df, float(df.iloc[-1]["composite_score"]))
    chosen = risk_agent.decide_view(risk)
    risk["_chosen_view"] = chosen

    decision = DecisionAgent().decide(df, risk)

    return {
        "ts_code": ts_code,
        "trade_date": int(df.iloc[-1]["trade_date"]),
        "signal": decision["signal"],
        "confidence": decision["confidence"],
        "composite_score": decision["composite_score"],
        "final_score": decision["final_score"],
        "risk_view": decision["risk_view"],
        "factors": strat.latest_factors(df),
        "risk": {k: v for k, v in risk.items() if k != "_chosen_view"},
    }
