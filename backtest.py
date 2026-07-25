"""
回测引擎（Backtest Agent）
职责：对指定策略在历史区间进行回测，输出关键绩效指标与净值曲线
参考 Vibe-Trading「跨市场回测引擎」：统一框架、可解释、可对比基准

绩效指标：
- 年化收益率 / 累计收益率
- 最大回撤
- 夏普比率
- 胜率 / 交易次数
- 净值曲线（供前端 ECharts 渲染）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def build_strategy_signals(df: pd.DataFrame, strategy: str) -> pd.Series:
    """
    根据策略类型生成每日方向信号：+1 看多 / -1 看空 / 0 中性
    复用 strategy_agent 中的基础策略，保证「分析」与「回测」同源
    """
    from strategy_agent import signal_dual_ma, signal_macd, signal_rsi

    if strategy == "dual_ma":
        return signal_dual_ma(df)
    if strategy == "macd":
        return signal_macd(df)
    if strategy == "rsi":
        return signal_rsi(df)
    if strategy == "multi_factor":
        # 综合分策略：>=70 看多，<=30 看空
        s = pd.Series(0, index=df.index, dtype=int)
        s[df["composite_score"] >= 70] = 1
        s[df["composite_score"] <= 30] = -1
        return s
    raise ValueError(f"未知策略: {strategy}")


def _position_state_machine(signal: pd.Series) -> pd.Series:
    """
    持仓状态机：+1 开仓持有，-1 平仓空仓，0 维持现状。
    返回 0/1 持仓序列（回测数据量千级，循环清晰且正确）。
    """
    pos = 0
    out = []
    for s in signal:
        if s == 1:
            pos = 1
        elif s == -1:
            pos = 0
        out.append(pos)
    return pd.Series(out, index=signal.index, dtype=int)


def _max_drawdown(equity: pd.Series) -> float:
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    return float(drawdown.min() * 100)  # 以 % 返回（负数）


def _sharpe(daily_ret: pd.Series, rf: float = 0.0) -> float:
    if daily_ret.std() == 0:
        return 0.0
    excess = daily_ret - rf / TRADING_DAYS
    return float(excess.mean() / daily_ret.std() * np.sqrt(TRADING_DAYS))


def _trade_stats(position: pd.Series, daily_ret: pd.Series) -> tuple[float, int]:
    """逐笔统计胜率与交易次数（开仓=仓位 0->1）"""
    trades, wins = 0, 0
    in_pos = False
    entry_eq = 1.0
    equity = (1 + daily_ret.fillna(0)).cumprod()
    for i in range(1, len(position)):
        if position[i - 1] == 0 and position[i] == 1:
            in_pos = True
            trades += 1
            entry_eq = equity[i - 1]
        elif position[i - 1] == 1 and position[i] == 0 and in_pos:
            exit_eq = equity[i]
            if exit_eq > entry_eq:
                wins += 1
            in_pos = False
    win_rate = (wins / trades * 100) if trades else 0.0
    return win_rate, trades


def run_backtest(df: pd.DataFrame, strategy: str) -> dict:
    """
    主入口：输入已含特征（经 StrategyAgent.analyze）的 DataFrame。
    返回回测绩效字典。
    """
    df = df.copy()
    signal = build_strategy_signals(df, strategy)
    position = _position_state_machine(signal)

    # 用「上一日持仓」决定当日收益，避免未来函数
    strat_ret = position.shift(1).fillna(0) * df["pct_chg"]
    bench_ret = df["pct_chg"]  # 基准：买入持有

    strat_equity = (1 + strat_ret.fillna(0)).cumprod()
    bench_equity = (1 + bench_ret.fillna(0)).cumprod()

    total_return = float(strat_equity.iloc[-1] - 1) * 100
    bench_return = float(bench_equity.iloc[-1] - 1) * 100
    n = max(len(df), 1)
    annual = (float(strat_equity.iloc[-1]) ** (TRADING_DAYS / n) - 1) * 100
    max_dd = _max_drawdown(strat_equity)
    sharpe = _sharpe(strat_ret)
    win_rate, trades = _trade_stats(position, df["pct_chg"])

    # 净值曲线（前端 ECharts 使用）
    equity_curve = [
        {
            "date": str(int(d)),
            "equity": round(float(e), 4),
            "benchmark": round(float(b), 4),
        }
        for d, e, b in zip(df["trade_date"], strat_equity, bench_equity)
    ]

    return {
        "strategy": strategy,
        "annual_return": round(annual, 2),
        "total_return": round(total_return, 2),
        "max_drawdown": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "win_rate": round(win_rate, 2),
        "trades": trades,
        "benchmark_return": round(bench_return, 2),
        "equity_curve": equity_curve,
    }
