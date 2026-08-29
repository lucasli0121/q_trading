<!--
 * @Author: liguoqiang
 * @Date: 2026-06-10 16:52:20
 * @LastEditors: liguoqiang
 * @LastEditTime: 2026-07-17 17:53:56
 * @Description: 
-->
# CLAUDE.md

数据代理项目，主要从给定的数据源服务器获取股票数据，包含：日K数据，实时数据

## Project Overview

实现A股股票数据代理接口，从不同的第三方数据源获取数据，并实现统一的数据保存.
**MongoDB**
负责数据存储，对应的数据库操作目录是 db/mongo
**Redis**
提供快速数据缓存，对应的操作目录是 db/redis
**mqtt**
提供远程通知，实时行情，每分钟行情都通过MQTT进行远程通知

- Python 3.12
- Author: liguoqiang (Li Guo Qiang)

## Commands

### Run the app

```bash
# Direct run (development, with hot reload)
python main.py

# 打包部署
build.bat                        # Windows PyInstaller 一键打包
pyinstaller main.spec --clean    # 或直接调用 PyInstaller
docker compose up --build        # Docker 构建并启动
docker compose -f docker-compose.debug.yml up -d   # Docker 调试模式（sleep infinity）

# Run tests
pytest

# Lint
ruff check .

# Format
ruff format .

```

The app listens on `0.0.0.0:6000` by default (configurable in `cfg/stock.cfg` → `[web]` section).

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
- `test_akshare_*.py` — Akshare data fetching (requires network)
- `test_tickflow_*.py` — TickFlow API data fetching (requires network + API token)
- `test_mongo_*.py` — 测试和数据库相关的对象，数据库对象位于dao目录

### Install dependencies

```bash
pip install -r requirements.txt
```

## Architecture

### Entry Point & App Initialization

[main.py](main.py) is the sole entry point. It:
1. Reads `cfg/stock.cfg` for web/db/log configuration
2. Initializes logging from `cfg/log.yaml` (or falls back to basic config)
3. 初始化应用，初始化StockFetch调度(StockFetch作为全局唯一对象保存在AppContext类中)
4. 停止应用，当应用停止或退出时，停止StockFetch调度，停止数据库连接，停止redis连接等

### AppContext (全局应用上下文)

[app_context.py](app_context.py) provides a global `AppContext` class (thread-safe singleton via `__new__`). All major services are accessed through it:

```python
from app_context import AppContext
AppContext().mongo_exec      # MongoExec — MongoDB CRUD
AppContext().redis_exec      # RedisExec — Redis caching
AppContext().stock_fetch     # StockFetch — data fetching + scheduler
AppContext().mqtt # MQ 通知全局对象
```

DO NOT create new instances of these services — always use `AppContext()`.

### Data Layer

**Configuration**: [cfg/stock.cfg](cfg/stock.cfg) — INI format with sections: `[redis]`, `[mongo]`, `[tickflow]`, `[akshare]`, `[stock]`, `[web]`, `[log]`.

**Database access** (`db/`):
- [db/db_base.py](db/db_base.py) — `DbBaseImpl`: reads DB credentials from config, base class for `MongoExec` and `RedisExec`
- [db/mongo/mongo_exec.py](db/mongo/mongo_exec.py) — `MongoExec`: low-level MongoDB CRUD (`add`, `update`, `query_by_condition`, `delete`)
- [db/redis/redis_exec.py](db/redis/redis_exec.py) — `RedisExec`: Redis operations (lists, key-value strings, hashes), all with configurable TTL (default 12h)
- [db/mongo/mongo_rt_stocks_impl.py](db/mongo/mongo_rt_stocks_impl.py) — Real-time stock quotes collection (`rt_stocks_tbl`)
- [db/mongo/mongo_stock_info_impl.py](db/mongo/mongo_stock_info_impl.py) — Stock info collection (`stock_info_tbl`), supports `is_black` flags and fuzzy code matching
- [db/mongo/mongo_industry_impl.py](db/mongo/mongo_industry_impl.py) — Industry info collections (`industry_tbl`, `industry_base_tbl`)

**DAO layer** (`dao/`): Data classes with `from_db()` / `to_db()` serialization:
- `StockRealTimeDao` — real-time quotes (price, change%, volume, PE, PB, etc.)
- `StockHisHqDao` — historical OHLCV data
- `StockInfoDao` — stock metadata (name, shares, market cap, industry)
- `IndustryBaseInfoDao` — industry classification data

### Data Fetching (`stock_fetch/`)

**Dual-source architecture** — data comes from two independent providers, each with its own proxy:

1. **Akshare** (`stock_fetch/akshare_fetch/`): Free, rate-limited Chinese stock data
   - `AkStockBase` — wraps `akshare` library calls for real-time quotes, historical K-lines, industry data, stock base info
   - `AkStockProxy` — renames columns to English, handles scheduling logic
   - akshare在线帮助文档：(https://akshare.akfamily.xyz/)
   - akshare 是免费的数据源提供者，所以在数据操作中优先调用akshare提供的接口，如果调用失败再调用tickflow提供的接口

2. **TickFlow** (`stock_fetch/tickflow_fetch/`): Paid API with higher rate limits
   - `TickFlowBase` — HTTP client using `urllib3` PoolManager, handles UTC↔Asia/Shanghai timezone conversion
   - `TickFlowProxy` — 股票的实时行情，只监控股票池中的股票
   - TickFlow在线帮助文档:
   - 实时行情查询：https://docs.tickflow.org/zh-hans/api-reference/%E5%AE%9E%E6%97%B6%E8%A1%8C%E6%83%85/%E6%9F%A5%E8%AF%A2%E5%AE%9E%E6%97%B6%E8%A1%8C%E6%83%85
   - 查询K线数据:https://docs.tickflow.org/zh-hans/api-reference/k%E7%BA%BF%E6%95%B0%E6%8D%AE/%E6%9F%A5%E8%AF%A2-k%E7%BA%BF%E6%95%B0%E6%8D%AE
   - 查询利润表： https://docs.tickflow.org/zh-hans/api-reference/%E8%B4%A2%E5%8A%A1%E6%95%B0%E6%8D%AE/%E6%9F%A5%E8%AF%A2%E5%88%A9%E6%B6%A6%E8%A1%A8
   - 查询股本表: https://docs.tickflow.org/zh-hans/api-reference/%E8%B4%A2%E5%8A%A1%E6%95%B0%E6%8D%AE/%E6%9F%A5%E8%AF%A2%E8%82%A1%E6%9C%AC%E8%A1%A8

**StockFetch** ([stock_fetch/stock_fetch.py](stock_fetch/stock_fetch.py)) is the central orchestrator:
- Uses `apscheduler` BackgroundScheduler with cron triggers for weekday 9:00-15:00 trading hours
- Schedules: real-time quote sync (both sources), industry data sync, stock base info sync, stale data cleanup
- **Cache-first pattern**: `get_stock_day/week/month_his_hq()` checks Redis first → falls back to Akshare → falls back to TickFlow → caches result in Redis with 24h TTL. Cache keys follow format `stock_{period}_hq:{code}:{start_date}:{end_date}`
- 需要从akshare或者tickflow获取的历史数据，财务数据，都需要redis缓存，查询也是先查询redis，redis查不到数据再从akshare或者tickflow查询
- 需要直接从mongo查询的数据不需要redis缓存
- redis把查询时间作为key，缓存实时，日K线数据，分钟K线数据，历史日K线数据

### Configuration & Logging

- `cfg/stock.cfg` — All runtime config (DB credentials, API tokens, sync intervals, web listen address)
- `cfg/log.yaml` — Python logging config: console + daily rotating file handlers (info + error logs in `log/`)

## Key Patterns

- **Dual data source with fallback**: `StockFetch` methods try Akshare first, then TickFlow, caching successful results in Redis
- **Singleton services**: All major services are accessed via `AppContext()`, initialized on first use
- **Cache-aside**: Historical K-line data follows check-Redis → fetch → write-Redis flow
- **Scheduler in background**: `apscheduler` BackgroundScheduler with cron triggers, only active on trading days/hours (validated via `exchange_calendars` XSHG calendar)

## 技术栈
- Python 3.12（PyInstaller 打包 / Docker 部署）
- akshare
- tickflow

## 编码规范

- 所有函数必须有类型注解，代码符合pylance规范
- 所有函数都要求函数注释，文件头，函数头注释
- 函数参数，类成员定义都要求有类型说明
- 字符串一律使用双引号
- tests目录测试符合unittest规范