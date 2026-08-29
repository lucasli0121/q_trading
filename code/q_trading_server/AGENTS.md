<!--
 * @Author: liguoqiang
 * @Date: 2026-06-16 09:10:51
 * @LastEditors: liguoqiang
 * @LastEditTime: 2026-06-21 10:45:35
 * @Description: 
-->
# AI Coding Agent Instructions for q_trading_server

## Purpose
This repository implements a Chinese A-share stock data proxy service with unified storage and web API access. The main responsibilities are data fetching, caching, and persistence.

## Key Concepts
- Entry point: `main.py`
- Web API: `web_api/api_run.py` (FastAPI)
- Singleton services: `app_context.py` exposes `AppContext().mongo_exec`, `AppContext().redis_exec`, `AppContext().stock_fetch`
- Data sources: `stock_fetch/akshare_fetch` and `stock_fetch/tickflow_fetch`
- Database layer: `db/mongo` and `db/redis`
- DAO layer: `dao/` with `from_db()` / `to_db()` methods
- Configuration: `cfg/stock.cfg`
- Logging config: `cfg/log.yaml`

## Development and Runtime
- Run locally: `python main.py`
- Docker: `docker compose up --build`
- Install dependencies: `pip install -r requirements.txt`
- Tests: `pytest` or `python -m pytest tests/ -v`
- Lint: `ruff check .`
- Format: `ruff format .`

## Project Conventions
- Use type annotations for all functions and method signatures.
- Add docstrings for modules, classes, and methods.
- Use double quotes for strings.
- Keep business logic out of the web entrypoint; focus API logic in `web_api/api_run.py` and service orchestration in `stock_fetch/stock_fetch.py`.
- Prefer `AppContext()` instead of creating multiple DB or scheduler instances.
- DAO objects should support `from_db()` deserialization and `to_db()` serialization.
- Historical and financial data flows should use Redis caching when data comes from Akshare or TickFlow.
- For real-time and cached data, follow the existing pattern: check Redis first, then fallback to Akshare, then TickFlow.

## Important Notes
- `cfg/stock.cfg` contains credentials and API tokens; do not hardcode secrets in new source files.
- MongoDB and Redis are expected to be available for full integration tests.
- The repo already has a `CLAUDE.md` file with architecture and command information; use it as a reference rather than duplicating details.

## When Changing Code
- Preserve existing data model field names unless there is a strong reason to rename them.
- When modifying `from_db()` logic, ensure all fields are assigned and missing values are handled safely.
- Maintain the repository’s current data flow: Akshare first, TickFlow fallback, Redis cache-aside.
- Keep web API route definitions and startup behavior aligned with `main.py` and `web_api/api_run.py`.

## Useful Files
- `main.py` — application entrypoint
- `app_context.py` — global singleton access
- `cfg/stock.cfg` — runtime config
- `stock_fetch/stock_fetch.py` — scheduler and data orchestration
- `dao/` — data transfer objects
- `db/mongo/` and `db/redis/` — persistence layers
- `web_api/api_run.py` — FastAPI app
- `CLAUDE.md` — existing repository guidance
