<!--
 * @Author: liguoqiang
 * @Date: 2026-06-26 17:16:51
 * @LastEditors: liguoqiang
 * @LastEditTime: 2026-06-27 11:26:40
 * @Description: 
-->
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Q_trading_cli项目实现股票策略，策略WEB页面，展示策略效果，通过调用q_trading_server项目提供的api获取股票数据
api服务器url在cfg文件中配置

**mqtt**
接收mqtt远程通知，实时行情，每分钟行情都通过MQTT进行远程通知, mqtt连接信息在配置文件

**服务端api**
服务端api的url在配置文件中配置，系统关于数据的所有操作都通过api接口获取

- Python 3.12
- Author: liguoqiang (Li Guo Qiang)

## Commands

### Run the app

```bash
# Direct run (development, with hot reload)
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

The app listens on `0.0.0.0:5000` by default (configurable in `cfg/stock.cfg` → `[web]` section).

### Tests

Uses **unittest**. Tests require a running MongoDB and Redis (configured in `cfg/stock.cfg`).

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
1. Reads `cfg/stock.cfg` for web/db/log configuration
2. Initializes logging from `cfg/log.yaml` (or falls back to basic config)
3. Calls `init_app()` which registers FastAPI middleware (`ClientDisconnectMiddleware`), static file serving, and two NiceGUI routes (`/` → main page, `/login` → login)
4. On startup: no scheduled tasks run by default (commented out)
5. On shutdown: clears NiceGUI storage

### AppContext Pattern

[app_context.py](app_context.py) provides a global `AppContext` class (thread-safe singleton via `__new__`). All major services are accessed through it:

```python
from app_context import AppContext
AppContext().factor_manager  # FactorManager — technical factor registry
AppContext().theme_manager   # ThemeManager — dark/light theme
AppContext().mqtt # MQ 通知全局对象
```

DO NOT create new instances of these services — always use `AppContext()`.

### Data Layer

**Configuration**: [cfg/stock.cfg](cfg/stock.cfg) — INI format with sections: `[server]`, `[mqtt]`, `[log]`.

**DAO layer** (`dao/`): Data classes with `from_db()` / `to_db()` serialization:
- `StockRealTimeDao` — real-time quotes (price, change%, volume, PE, PB, etc.)
- `StockHisHqDao` — historical OHLCV data
- `StockInfoDao` — stock metadata (name, shares, market cap, industry)
- `IndustryBaseInfoDao` — industry classification data

### Strategy & Factor Framework

**Factors** ([factor/](factor/)):
- [base_factor.py](factor/base_factor.py) — `BaseFactor(ABC)` with `calculate(df) -> float`
- [factor_manager.py](factor/factor_manager.py) — `FactorManager`: registry pattern (`register(name, factor)`, `get(name)`)
- [factor_utils.py](factor/factor_utils.py) — `FactorUtils`: static helpers to convert DataFrame columns to `np.ndarray` for ta-lib compatibility
- Concrete factors: `PctChangeFactor` (ta-lib ROC), `ReboundFactor` (low→close %), `MaFactor` (ta-lib MA)

**Strategies** ([strategy/](strategy/)):
- [base_strategy.py](strategy/base_strategy.py) — `BaseStrategy(ABC)`: declares `init_factors()`, `is_match_strategy(df) -> (bool, dict)`, and a `select(codes, days) -> list[dict]` method that iterates stock codes, fetches daily K-line data, and checks signals
- [strong_rebound_strategy.py](strategy/strong_rebound_strategy.py) — `StrongReboundStrategy`: matches when (1) daily change > 5%, (2) 5-day rebound > 10%, (3) close > MA5
**Adding a new strategy**: Subclass `BaseStrategy`, register factors in `init_factors()`, implement `is_match_strategy()`. Factors and strategies are registered/used via `AppContext().factor_manager`.

**Backtesting** ([backtest/backtest_engine.py](backtest/backtest_engine.py)):
- `BacktestEngine.run(strategy, stock_codes, start_date, end_date, hold_days)` — walks each stock's daily data with a sliding window, calls `strategy.is_match_strategy()`, simulates buy-next-open / sell-after-N-days, returns `TradeResult` list with profit stats

### Api 访问

- q_trading_server作为后台，为q_trading_cli提供http访问接口，q_trading_cli可以通过http请求访问后台的数据，以及实现数据的增删改查等

- 接口文档地址: http://dening-tech.cn:8000/docs

**用户请求接口**
- 实现用户的注册，登录，退出，注销
- api接口前缀 /api/user/
**股票池请求接口**
- 实现股票池的创建，并且可以向股票池添加股票
- api接口前缀 /api/pool/
**策略请求接口**
- 实现股票策略的保存，查询，回测结果保存，回测日志保存等
- api接口前缀 /api/strategy/
**行情请求接口**
- 实现实时行情查询，分钟行情查询，历史行情查询
- api接口前缀 /api/market/
**估值请求接口**
- 实现股票估值查询
**财务请求接口**
- 实现股票财务报表查询
- api 接口前缀 /api/finance/
**股票信息查询接口**
- 实现股票信息查询, 股票列表查询， 股票板块查询
- api 接口前缀 /api/stock_info
**黑名单请求接口**
- 实现股票的黑名单功能
- api 接口前缀 /api/blacklist/
**股票筛选**
- 根据不同条件筛选需要的股票,比如根据市盈率，估值筛选
- api 接口前缀 /api/screener/search

### UI Layer (NiceGUI)

- UI 采用nicegui实现图形界面
- 界面主要功能包括：策略，策略运行信息等
**策略**
- 显示已经实现的策略。
- 显示策略的状态：是否运行，收益，天数等。
**复盘**
- 复盘主要是查询收盘后的大盘，个股走势，涨跌停股票数量，估值，市盈率，排名，股票信息等。

**Pages** ([pages/](pages/)):
- [main_page.py](pages/main_page.py) — Root layout: header bar + left drawer with 2 tabs (策略/复盘) + settings button
- [first_page.py](pages/first_page.py) — Tab content inside "策略"
- [header_page.py](pages/header_page.py) — Top header with market commentary, search input, and user menu

**Reusable components** ([components/](components/)):
- `inputs.py` — Themed input widgets (search, password, date picker, select dropdowns)
- `labels.py` — Themed label variants (normal, medium, bold)
- `tables.py` — Stock data table, plus extensive invoice/tax/business tables (some carryover from a tax-management system)
- `cards.py` — Tax report cards (carryover from tax system)
- `custom_tabs.py` — CSS theming for left-drawer and page-level tabs

**Theme** ([colors/theme.py](colors/theme.py)): `ThemeManager` with `dark` and `light` presets, sets NiceGUI global colors and dark mode. Default: dark.

**Other UI files**: [menu/top_menu.py](menu/top_menu.py) — user menu with login/register/profile/logout; [pages/login_page.py](pages/login_page.py) — login page; `widght/` — mostly empty.

### Configuration & Logging

- `cfg/stock.cfg` — All runtime config (DB credentials, API tokens, sync intervals, web listen address)
- `cfg/log.yaml` — Python logging config: console + daily rotating file handlers (info + error logs in `log/`)

### Docker

The [Dockerfile](Dockerfile) is multi-purpose (dev + prod). It installs system deps (`wkhtmltopdf`, `ta-lib` C library), copies the app, then runs `python3 main.py`. The [docker-compose.yml](docker-compose.yml) mounts the source as a volume for development. Exposes port 8888 in the container.

## Key Patterns

- **Registry pattern**: `FactorManager` holds a `factor_map` dict; strategies register their factors at init
- **AppContext services**: All major services are accessed via `AppContext()`, initialized on first use

## 技术栈

- Python3.12-slim Docker 版本
- pyqlib
- ta-lib
- Dockerfile

## 编码规范

- 所有函数必须有类型注解，代码符合pylance规范
- 所有函数都要求函数注释，文件头，函数头注释
- 函数参数，类成员定义都要求有类型说明
- 字符串一律使用双引号
- tests目录测试符合unittest规范