"""全局配置：数据库、数据源、CORS 等"""
from __future__ import annotations

import os

# 数据库：开发用 SQLite，生产改 MySQL
DB_URL = os.getenv("DB_URL", "sqlite:///./quant.db")

# AKShare 无需 token；Tushare 需在此配置
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")

# FinnHub：免费账户覆盖美股/部分全球，但不含 A 股/港股行情
# 运行前注入环境变量：export FINNHUB_API_KEY="你的key"（不要硬编码进仓库）
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")


def is_a_share(ts_code: str) -> bool:
    """判断是否为 A 股代码（沪深北），用于数据源路由"""
    return ts_code.upper().endswith((".SH", ".SZ", ".BJ"))

# WebSocket 心跳与 CORS
CORS_ORIGINS = ["*"]  # 生产请收敛为小程序合法域名

# 默认回看窗口
DEFAULT_LOOKBACK_DAYS = 365
