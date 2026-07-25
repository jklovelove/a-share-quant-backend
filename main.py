"""
FastAPI 入口 + WebSocket 长连接
串联 Phase1-4 Swarm 与回测引擎，对外提供 REST / WS 接口。

本地运行：
    pip install fastapi uvicorn akshare pandas numpy
    uvicorn main:app --reload --port 8000

说明：analysis / backtest 接口在「无 AKShare / 离线」时自动降级为
随机行情演示，保证接口可跑通；接入真实数据源后即为生产逻辑。
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import CORS_ORIGINS, DEFAULT_LOOKBACK_DAYS, FINNHUB_API_KEY, TUSHARE_TOKEN, is_a_share
from data_agent import AKShareSource, DataAgent
from finnhub_source import FinnHubSource
from tushare_source import TushareSource
from yahoo_source import YahooSource
from strategy_agent import StrategyAgent
from decision_agent import run_pipeline
from backtest import run_backtest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("quant.api")

app = FastAPI(title="A股量化小程序后端", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS,
                   allow_methods=["*"], allow_headers=["*"])


# ----------------------------------------------------------------------
# 请求模型
# ----------------------------------------------------------------------
class BacktestReq(BaseModel):
    ts_code: str
    strategy: str = "multi_factor"
    start_date: str = ""   # YYYYMMDD，空则取默认回看
    end_date: str = ""


# ----------------------------------------------------------------------
# 数据获取：真实优先，离线降级演示
# ----------------------------------------------------------------------
def _resolve_sources(ts_code: str) -> list[DataSource]:
    """
    返回按优先级排列的数据源列表（多源 Swarm 的数据入口）：
    - A 股：Tushare（积分足够时最优）→ AKShare（免费真实历史，兜底）
    - 美股/全球：FinnHub（实时报价）
    _load_features 会逐个尝试，命中即用，全部失败才降级演示。
    """
    if is_a_share(ts_code):
        sources: list[DataSource] = []
        if TUSHARE_TOKEN:
            sources.append(TushareSource(TUSHARE_TOKEN))  # 积分足够时最优
        sources.append(AKShareSource())                  # 用户指定的真实主源
        sources.append(YahooSource())                    # 海外可达真实兜底（沙箱验证用）
        return sources
    return [FinnHubSource(FINNHUB_API_KEY)]


def _load_features(ts_code: str, start: str, end: str) -> pd.DataFrame:
    for src in _resolve_sources(ts_code):
        try:
            df = DataAgent(src).build_features(ts_code, start, end)
            if not df.empty:
                logger.info("数据源命中: %s", src.__class__.__name__)
                return df
        except Exception as e:  # 无权限 / 未安装 SDK / 无网
            logger.warning("数据源 %s 不可用: %s", src.__class__.__name__, e)

    return _demo_features(ts_code, start, end)


def _demo_features(ts_code: str, start: str, end: str) -> pd.DataFrame:
    """生成一段带趋势与噪声的演示行情，用于接口联调"""
    end_d = datetime.strptime(end, "%Y%m%d") if end else datetime.today()
    start_d = (datetime.strptime(start, "%Y%m%d") if start
               else end_d - timedelta(days=DEFAULT_LOOKBACK_DAYS))
    days = pd.bdate_range(start_d, end_d)
    n = len(days)
    rng = np.random.default_rng(abs(hash(ts_code)) % (2**32))
    drift = rng.normal(0.0005, 0.02, n).cumsum()
    close = 20 * np.exp(drift)
    df = pd.DataFrame({
        "trade_date": [int(d.strftime("%Y%m%d")) for d in days],
        "open": close * (1 + rng.normal(0, 0.005, n)),
        "high": close * (1 + abs(rng.normal(0, 0.01, n))),
        "low": close * (1 - abs(rng.normal(0, 0.01, n))),
        "close": close,
        "vol": rng.integers(1e6, 5e6, n).astype(float),
        "amount": rng.integers(1e8, 5e8, n).astype(float),
    })
    # 复用 DataAgent 的指标函数注入特征（与真实路径计算逻辑一致）
    df = DataAgent(AKShareSource())._clean(df)
    return _attach_features(df)


def _attach_features(df: pd.DataFrame) -> pd.DataFrame:
    """演示分支：直接调用 DataAgent 的指标函数（与真实路径一致）"""
    from data_agent import sma, macd, rsi, ma_arrangement, cross_signal, macd_divergence
    df = df.copy()
    df["ma5"] = sma(df["close"], 5)
    df["ma20"] = sma(df["close"], 20)
    df["ma60"] = sma(df["close"], 60)
    df["dif"], df["dea"], df["macd_hist"] = macd(df["close"])
    df["rsi_14"] = rsi(df["close"], 14)
    df["ma_arrange"] = ma_arrangement(df["close"])
    df["cross"] = cross_signal(df["ma20"], df["ma60"])
    df["macd_div"] = macd_divergence(df["close"], df["macd_hist"])
    df["pct_chg"] = df["close"].pct_change().fillna(0)
    return df


# ----------------------------------------------------------------------
# REST 接口
# ----------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/analysis/{ts_code}")
def analysis(ts_code: str, start: str = "", end: str = ""):
    """Phase1-4 全链路：返回信号 + 因子明细 + 风控结论"""
    df = _load_features(ts_code, start, end)
    df = StrategyAgent().analyze(df)
    result = run_pipeline(ts_code, df)
    return result


@app.get("/api/quote/{ts_code}")
def quote(ts_code: str):
    """
    实时/盘后行情快照（标的监控模块）：
    - A 股：取最近一日收盘（生产环境 AKShare 真实数据）
    - 美股/全球：FinnHub /quote 实时报价（你提供的 key，免费层可用）
    """
    if is_a_share(ts_code):
        df = _load_features(ts_code, "", "")
        row = df.iloc[-1]
        return {
            "ts_code": ts_code,
            "price": float(row["close"]),
            "trade_date": int(row["trade_date"]),
            "source": "akshare",
        }
    src = FinnHubSource(FINNHUB_API_KEY)  # 无 key 会抛 400，由全局异常处理
    return {"ts_code": ts_code, "source": "finnhub", **src.fetch_quote(ts_code)}


@app.post("/api/backtest")
def backtest(req: BacktestReq):
    """回测：返回关键绩效指标 + 净值曲线"""
    df = _load_features(req.ts_code, req.start_date, req.end_date)
    df = StrategyAgent().analyze(df)
    try:
        report = run_backtest(df, req.strategy)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ts_code": req.ts_code, "report": report}


# ----------------------------------------------------------------------
# WebSocket：自选股异动 / 信号推送
# ----------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, message: dict):
        for ws in list(self.active):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(ws)


manager = ConnectionManager()


@app.websocket("/ws/watchlist")
async def ws_watchlist(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            # 小程序端可发送 {ts_code: [...]} 订阅；此处简化为心跳
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


# 后台定时任务（示意）：盘中每 3 分钟推送异动
async def _push_loop():
    while True:
        await asyncio.sleep(180)
        await manager.broadcast({"type": "tick", "ts": datetime.now().isoformat()})


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_push_loop())
