"""
本地真实验证脚本：用 AKShare（优先）+ 路由兜底，跑通「行情→分析→回测」。
部署到有国内网络的主机后：
    pip install akshare
    python verify.py              # 默认 600519.SH
    python verify.py 000001.SZ    # 指定标的
注意：当前沙箱（海外网络）访问不到东方财富/新浪，会自动降级为演示数据；
      在国内/有国内网络环境运行即可得到真实 A 股行情。
"""
import sys
from main import _load_features, _resolve_sources
from strategy_agent import StrategyAgent
from decision_agent import run_pipeline
from backtest import run_backtest

code = sys.argv[1] if len(sys.argv) > 1 else "600519.SH"

print("数据源链:", [s.__class__.__name__ for s in _resolve_sources(code)])
df = _load_features(code, "20240101", "20240601")
print(f"[{code}] 日线 {len(df)} 行 | 最新收盘 {df.iloc[-1]['close']:.2f} | 日期 {df.iloc[-1]['trade_date']}")

df = StrategyAgent().analyze(df)
r = run_pipeline(code, df)
print(f"信号={r['signal']} 置信度={r['confidence']} 综合分={r['composite_score']} 风控视角={r['risk_view']}")
print("因子:", r["factors"])

for s in ["dual_ma", "macd", "rsi", "multi_factor"]:
    rep = run_backtest(df, s)
    print(f"[{s}] 年化={rep['annual_return']}% 回撤={rep['max_drawdown']}% "
          f"夏普={rep['sharpe']} 胜率={rep['win_rate']}% 交易={rep['trades']}")
