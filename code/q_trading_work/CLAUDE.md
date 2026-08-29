# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Q_trading_work 项目实现股票策略的自动化运行，通过 MQTT 接收实时行情数据，
通过调用 q_trading_server 项目提供的 API 获取股票数据、保存交易记录。

**核心功能**
- 策略工作流（StrategyWorkflow）：加载策略模板和用户策略配置，订阅 MQTT 行情，执行策略逻辑
- 策略框架（BaseStrategy）：因子注册、选股筛选、分钟/实时买卖信号检查
- 因子计算（Factor）：基于 ta-lib 的技术指标计算
- 交易管理（TradeManager）：订单执行（滑点+手续费）、盈亏计算、企业微信/微信通知推送
- 回测引擎（BacktestEngine）：历史数据回测，日线/分钟级别

**mqtt**
接收 MQTT 远程通知，实时行情、每分钟行情都通过 MQTT 进行远程通知，MQTT 连接信息在配置文件。

**服务端 API**
服务端 API 的 URL 在配置文件中配置，系统关于数据的所有操作都通过 API 接口获取。

- Python 3.12
- Author: liguoqiang (Li Guo Qiang)

## Commands

### Run the app

```bash
# Direct run
python main.py

# Via shell script (kills existing process, clears logs, launches in background)
./run.sh

# Docker
docker compose up --build

# Run tests
pytest

# Lint
ruff check .

# Format
ruff format .
```

The app runs as a headless background service — no web UI.

### Tests

Uses **unittest**. Tests for factor/strategy/backtest can run without external dependencies.

```bash
# Run all tests
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_factor.py -v

# Run a single test method
python -m pytest tests/test_strategy.py::TestStrategy::test_strategy_match -v

# Some tests can also be run directly
python tests/test_factor.py
```

**Test categories:**
- `test_factor.py` — Factor calculations (PctChange, Rebound, MA) with synthetic DataFrames, no DB needed
- `test_strategy.py` — Strategy signal matching with synthetic DataFrames, no DB needed
- `test_backtest.py` — Backtest engine against real stock data (requires DB/API)
- `test_strategy_workflow.py` — StrategyWorkflow with mocked API clients
- `test_trade_manager.py` — TradeManager order execution and cost model

### Install dependencies

```bash
pip install -r requirements.txt
```

Note: `ta-lib` requires the C library to be installed first (see Dockerfile for steps). On Ubuntu:
```bash
wget https://github.com/ta-lib/ta-lib/releases/download/v0.6.4/ta-lib-0.6.4-src.tar.gz
tar -xzf ta-lib-0.6.4-src.tar.gz && cd ta-lib-0.6.4
./configure --prefix=/usr && make && make install
```

## Architecture

### Entry Point & App Initialization

[main.py](main.py) is the sole entry point. It:
1. Reads `cfg/stock.cfg` for config
2. Initializes logging from `cfg/log.yaml` (or falls back to basic config)
3. Connects MQTT via `AppContext().mqtt_client.connect()`
4. Starts `AppContext().strategy_workflow.start()` (loads strategies, subscribes to market data, starts scheduler)
5. Waits for shutdown signal (Ctrl+C / SIGTERM)
6. On shutdown: stops workflow, disconnects MQTT

### AppContext Pattern

[app_context.py](app_context.py) provides a global `AppContext` class (thread-safe singleton via `__new__`). All major services are accessed through it:

```python
from app_context import AppContext
AppContext().factor_manager     # FactorManager — technical factor registry
AppContext().mqtt_client        # MqttClient — MQTT connection
AppContext().strategy_workflow  # StrategyWorkflow — strategy lifecycle
AppContext().market_api         # MarketApi — market data
AppContext().redis_exec         # RedisExec — Redis 缓存读写（redis_db 包，配置见 [redis] 节）
```

DO NOT create new instances of these services — always use `AppContext()`.

### Data Layer

**Configuration**: [cfg/stock.cfg](cfg/stock.cfg) — INI format with sections: `[server]`, `[mqtt]`, `[log]`, `[scheduler]`, `[trade]`, `[strategy]`, `[signal_filter]`, `[cost_config]`, `[workflow]`.

**DAO layer** (`dao/`): Data classes with `from_db()` / `to_db()` serialization:
- `StockRealTimeDao` — real-time quotes (price, change%, volume, PE, PB, etc.)
- `StockHisHqDao` — historical OHLCV data
- `StockInfoDao` — stock metadata (name, shares, market cap, industry)
- `OrderDao` — trade orders (buy/sell, quantity, price, status)
- `StrategyDao` — strategy templates
- `UserStrategyDao` — user-strategy associations

**API layer** (`api/`): HTTP wrappers over `ApiClient` (httpx-based):
- `ApiClient` — unified HTTP client with token auth, retry, error handling
- `MarketApi`, `FinanceApi`, `StockInfoApi` — data queries
- `StrategyApi`, `UserStrategyApi` — strategy CRUD
- `PoolApi`, `OrderApi`, `BlacklistApi`, `ScreenerApi` — supporting APIs

### Strategy & Factor Framework

**Factors** ([factor/](factor/)):
- [base_factor.py](factor/base_factor.py) — `BaseFactor(ABC)` with `calculate(df) -> float`
- [factor_manager.py](factor/factor_manager.py) — `FactorManager`: registry pattern (`register(name, factor)`, `get(name)`)
- [factor_utils.py](factor/factor_utils.py) — `FactorUtils`: static helpers to convert DataFrame columns to `np.ndarray` for ta-lib compatibility
- Concrete factors: `PctChangeFactor`, `ReboundFactor`, `MaFactor`, `VolumeExpansionFactor`, `DrawdownFactor`, `DeclineFactor`, `CloseReboundFactor`, `VwapFactor`, `PriceTrendFactor`, `AdxTrendFactor`, `KlineSRFactor`, `InDaySRFactor`

**Strategies** ([strategy/](strategy/)):
- [base_strategy.py](strategy/base_strategy.py) — `BaseStrategy(ABC)`: declares `init_factors()`, `is_match_strategy(df) -> (bool, dict)`, and a `select(codes, days) -> list[dict]` method that iterates stock codes, fetches daily K-line data, and checks signals. Also provides `handle_minute_bar()`, `handle_tick_bar()`, `check_minute_buy/sell()`, `check_tick_buy/sell()`, and `before_trading()` hooks.
- [strong_rebound_strategy.py](strategy/strong_rebound_strategy.py) — `StrongReboundStrategy`: 强势反弹策略
- [swing_trading_strategy.py](strategy/swing_trading_strategy.py) — `SwingTradingStrategy`: 低频波段/网格策略

**Adding a new strategy**: Subclass `BaseStrategy`, register factors in `init_factors()`, implement `is_match_strategy()` and buy/sell condition checks. Factors and strategies are registered/used via `AppContext().factor_manager`.

### Workflow Engine

**BaseWorkflow** ([workflow/base_workflow.py](workflow/base_workflow.py)):
- Thread pool for async strategy execution
- MQTT subscription (minute bars + real-time ticks)
- Lifecycle: `start()` → subscribe MQTT + `on_start()`; `stop()` → `on_stop()` + unsubscribe + shutdown pool

**StrategyWorkflow** ([workflow/strategy_workflow.py](workflow/strategy_workflow.py)):
- Loads strategy templates (from `/api/strategy/list`) and user strategy configs (from `/api/user_strategy/all`)
- Each template instantiated once, user configs grouped by `strategy_id`
- `handle_bar()` / `handle_tick()` → dispatches to `BaseStrategy.handle_minute_bar()` / `handle_tick_bar()` via thread pool
- BUY/SELL signals → `_save_trade_signal()` (API + storm filter) → `_create_buy_order()` / `_create_sell_order()` → `TradeManager.submit_order()`
- APScheduler daily `before_trading` task at 9:00 (Mon-Fri): runs `before_trading()` + `select()` for each running strategy
- Blacklist filtering per user strategy

### Trade Manager

[trade/manager.py](trade/manager.py) — `TradeManager`:
- Executes orders with slippage (buy +N, sell -N) and commission fees
- Calculates profit/loss on sell orders
- Updates `StrategyExecutionDao` (positions, remaining cash, profit) via API
- Sends notifications via enterprise WeChat webhook and/or WeChat friends (itchat)

### Backtesting

[backtest/backtest_engine.py](backtest/backtest_engine.py):
- `BacktestEngine.run(strategy, stock_codes, start_date, end_date, hold_days)` — walks each stock's daily data with a sliding window, calls `strategy.is_match_strategy()`, simulates buy-next-open / sell-after-N-days, returns `TradeResult` list with profit stats
- Supports daily and minute-level backtesting
- CLI entry: [backtest/main.py](backtest/main.py)

### API 访问

- q_trading_server 作为后台，为 q_trading_work 提供 HTTP 访问接口
- 接口文档地址: http://dening-tech.cn:8000/docs

### Configuration & Logging

- `cfg/stock.cfg` — All runtime config (API server, MQTT, scheduler, trade costs, signal filter, workflow)
- `cfg/log.yaml` — Python logging config: console + daily rotating file handlers (info + error logs in `log/`)

### Docker

The [Dockerfile](Dockerfile) installs system deps (`wkhtmltopdf`, `ta-lib` C library), copies the app, then runs `python3 main.py`.

## Key Patterns

- **Registry pattern**: `FactorManager` holds a `factor_map` dict; strategies register their factors at init
- **AppContext services**: All major services are accessed via `AppContext()`, initialized on first use
- **Strategy template + user config**: Strategy classes are templates; `UserStrategyDao` links a user, strategy template, and stock pool — one template can serve multiple users

## 技术栈

- Python 3.12-slim Docker 版本
- pyqlib
- ta-lib
- httpx (HTTP client)
- paho-mqtt (MQTT client)
- apscheduler (cron scheduler)
- exchange_calendars (trading calendar)

## 编码规范

- 所有函数必须有类型注解，代码符合 pylance 规范
- 所有函数都要求函数注释，文件头、函数头注释
- 函数参数、类成员定义都要求有类型说明
- 字符串一律使用双引号
- tests 目录测试符合 unittest 规范
