-- =============================================================
-- A股量化小程序 · 核心数据库表结构
-- 兼容 MySQL 8（生产）与 SQLite（开发）
-- 约定：ts_code 为 6 位代码 + 交易所后缀，如 600519.SH / 000001.SZ
--       trade_date 统一为 'YYYYMMDD' 整型，便于范围查询与索引
-- =============================================================

-- ---------- 1. 用户表 ----------
CREATE TABLE IF NOT EXISTS users (
    id              BIGINT          PRIMARY KEY AUTO_INCREMENT,
    openid          VARCHAR(64)     NOT NULL UNIQUE COMMENT '微信 openid',
    nickname        VARCHAR(64)     DEFAULT '' COMMENT '昵称',
    avatar          VARCHAR(255)    DEFAULT '' COMMENT '头像 url',
    created_at      DATETIME        DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) COMMENT='微信用户';

-- ---------- 2. 标的基础信息表 ----------
CREATE TABLE IF NOT EXISTS stock_basic (
    ts_code         VARCHAR(16)     PRIMARY KEY COMMENT '股票代码 如 600519.SH',
    symbol          VARCHAR(8)      NOT NULL COMMENT '纯数字代码 600519',
    name            VARCHAR(32)     NOT NULL COMMENT '股票名称',
    exchange        VARCHAR(8)      NOT NULL COMMENT 'SH / SZ',
    industry        VARCHAR(32)     DEFAULT '' COMMENT '申万一级行业',
    list_date       VARCHAR(8)      DEFAULT '' COMMENT '上市日期 YYYYMMDD',
    delist_date     VARCHAR(8)      DEFAULT '' COMMENT '退市日期',
    updated_at      DATETIME        DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) COMMENT='标的基础信息';
CREATE INDEX idx_stock_basic_name ON stock_basic(name);

-- ---------- 3. 日线行情表（量最大，按 ts_code+trade_date 联合主键）----------
CREATE TABLE IF NOT EXISTS daily_quote (
    ts_code         VARCHAR(16)     NOT NULL,
    trade_date      INT             NOT NULL COMMENT 'YYYYMMDD',
    open            DECIMAL(12,4)   NOT NULL,
    high            DECIMAL(12,4)   NOT NULL,
    low             DECIMAL(12,4)   NOT NULL,
    close           DECIMAL(12,4)   NOT NULL,
    pre_close       DECIMAL(12,4)   DEFAULT 0 COMMENT '昨收，用于算涨跌幅',
    vol             BIGINT          DEFAULT 0 COMMENT '成交量(股)',
    amount          DECIMAL(20,4)   DEFAULT 0 COMMENT '成交额(元)',
    turnover_rate   DECIMAL(8,4)    DEFAULT 0 COMMENT '换手率 %',
    pe_ttm          DECIMAL(12,4)   DEFAULT 0 COMMENT '市盈率(TTM)',
    pb              DECIMAL(12,4)   DEFAULT 0 COMMENT '市净率',
    PRIMARY KEY (ts_code, trade_date)
) COMMENT='日线行情';
CREATE INDEX idx_daily_date ON daily_quote(trade_date);

-- ---------- 4. 资金流向表 ----------
CREATE TABLE IF NOT EXISTS moneyflow (
    ts_code         VARCHAR(16)     NOT NULL,
    trade_date      INT             NOT NULL,
    main_net_inflow DECIMAL(20,4)   DEFAULT 0 COMMENT '主力净流入(元)',
    retail_net_inflow DECIMAL(20,4) DEFAULT 0 COMMENT '散户净流入(元)',
    north_hold_ratio DECIMAL(8,4)   DEFAULT 0 COMMENT '北向持股占流通比 %',
    PRIMARY KEY (ts_code, trade_date)
) COMMENT='资金流向';

-- ---------- 5. 因子快照表（每日收盘后重算）----------
CREATE TABLE IF NOT EXISTS factor_snapshot (
    ts_code         VARCHAR(16)     NOT NULL,
    trade_date      INT             NOT NULL,
    pe_percentile   DECIMAL(6,4)    DEFAULT 0 COMMENT 'PE 历史分位 0~1',
    pb_percentile   DECIMAL(6,4)    DEFAULT 0 COMMENT 'PB 历史分位 0~1',
    rsi_14          DECIMAL(8,4)    DEFAULT 0 COMMENT '14 日 RSI',
    macd_hist       DECIMAL(12,6)   DEFAULT 0 COMMENT 'MACD 柱(红绿柱)',
    macd_divergence TINYINT         DEFAULT 0 COMMENT '1顶背离 -1底背离 0无',
    ma_signal       TINYINT         DEFAULT 0 COMMENT '1多头排列 -1空头排列 0震荡',
    ma_gold_cross   TINYINT         DEFAULT 0 COMMENT '1金叉 0无 -1死叉',
    main_inflow_score DECIMAL(6,4)  DEFAULT 0 COMMENT '资金流向因子分 0~100',
    tech_score      DECIMAL(6,4)    DEFAULT 0 COMMENT '技术形态因子分 0~100',
    valuation_score DECIMAL(6,4)    DEFAULT 0 COMMENT '估值水位因子分 0~100',
    composite_score DECIMAL(6,4)    DEFAULT 0 COMMENT '多因子综合分 0~100',
    PRIMARY KEY (ts_code, trade_date)
) COMMENT='因子快照';
CREATE INDEX idx_factor_score ON factor_snapshot(composite_score);

-- ---------- 6. 信号记录表 ----------
CREATE TABLE IF NOT EXISTS signal_record (
    id              BIGINT          PRIMARY KEY AUTO_INCREMENT,
    ts_code         VARCHAR(16)     NOT NULL,
    trade_date      INT             NOT NULL,
    signal          VARCHAR(8)      NOT NULL COMMENT 'BUY / HOLD / SELL',
    confidence      DECIMAL(6,4)    DEFAULT 0 COMMENT '置信度 0~100',
    composite_score DECIMAL(6,4)    DEFAULT 0,
    risk_view       VARCHAR(8)      DEFAULT '' COMMENT '激进/中立/保守结论',
    factors_detail  JSON            COMMENT '各因子明细，便于前端解释',
    created_at      DATETIME        DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_signal (ts_code, trade_date)
) COMMENT='买卖信号记录';
CREATE INDEX idx_signal_record_code ON signal_record(ts_code);

-- ---------- 7. 自选股关系表 ----------
CREATE TABLE IF NOT EXISTS watchlist (
    id              BIGINT          PRIMARY KEY AUTO_INCREMENT,
    user_id         BIGINT          NOT NULL,
    ts_code         VARCHAR(16)     NOT NULL,
    group_name      VARCHAR(32)     DEFAULT '默认' COMMENT '分组',
    alert_threshold DECIMAL(6,4)    DEFAULT 0 COMMENT '异动预警阈值 %',
    note            VARCHAR(255)    DEFAULT '' COMMENT '备注',
    created_at      DATETIME        DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_watch (user_id, ts_code),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) COMMENT='自选股';
CREATE INDEX idx_watchlist_user ON watchlist(user_id);

-- ---------- 8. 策略配置表 ----------
CREATE TABLE IF NOT EXISTS strategy_config (
    id              BIGINT          PRIMARY KEY AUTO_INCREMENT,
    user_id         BIGINT          NOT NULL,
    strategy        VARCHAR(32)     NOT NULL COMMENT 'dual_ma / macd / rsi / multi_factor',
    params          JSON            NOT NULL COMMENT '策略参数: 均线周期/阈值/权重',
    is_default      TINYINT         DEFAULT 0,
    created_at      DATETIME        DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) COMMENT='用户策略参数配置';

-- ---------- 9. 回测任务表 ----------
CREATE TABLE IF NOT EXISTS backtest_task (
    id              BIGINT          PRIMARY KEY AUTO_INCREMENT,
    user_id         BIGINT          NOT NULL,
    ts_code         VARCHAR(16)     NOT NULL,
    strategy        VARCHAR(32)     NOT NULL,
    start_date      INT             NOT NULL COMMENT 'YYYYMMDD',
    end_date        INT             NOT NULL,
    status          VARCHAR(16)     DEFAULT 'PENDING' COMMENT 'PENDING/RUNNING/DONE/FAILED',
    created_at      DATETIME        DEFAULT CURRENT_TIMESTAMP,
    finished_at     DATETIME        NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) COMMENT='回测任务';
CREATE INDEX idx_backtest_user ON backtest_task(user_id, created_at);

-- ---------- 10. 回测结果表 ----------
CREATE TABLE IF NOT EXISTS backtest_result (
    task_id         BIGINT          PRIMARY KEY,
    annual_return   DECIMAL(10,4)   DEFAULT 0 COMMENT '年化收益率 %',
    total_return    DECIMAL(10,4)   DEFAULT 0 COMMENT '累计收益率 %',
    max_drawdown    DECIMAL(10,4)   DEFAULT 0 COMMENT '最大回撤 %',
    sharpe          DECIMAL(8,4)    DEFAULT 0 COMMENT '夏普比率',
    win_rate        DECIMAL(6,4)    DEFAULT 0 COMMENT '胜率 %',
    trades          INT             DEFAULT 0 COMMENT '交易次数',
    benchmark_return DECIMAL(10,4)  DEFAULT 0 COMMENT '沪深300同期收益 %',
    equity_curve    JSON            COMMENT '净值曲线 [{date, equity, benchmark}]',
    FOREIGN KEY (task_id) REFERENCES backtest_task(id) ON DELETE CASCADE
) COMMENT='回测结果';
