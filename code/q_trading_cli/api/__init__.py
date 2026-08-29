"""
Author: liguoqiang
Date: 2026-06-27
Description: API 客户端包 — 统一的 HTTP API 客户端模块，
             封装了 q_trading_server 的所有 REST API 接口。
"""

from api.blacklist import BlacklistApi
from api.client import ApiClient, ApiError, UnauthorizedError
from api.config import ApiConfig
from api.error_handler import handle_http_error
from api.finance import FinanceApi
from api.market import MarketApi
from api.pool import PoolApi
from api.preference import PreferenceApi
from api.screener import ScreenerApi
from api.stock_info import StockInfoApi
from api.user import UserApi

__all__ = [
    "ApiClient",
    "ApiConfig",
    "ApiError",
    "BlacklistApi",
    "FinanceApi",
    "MarketApi",
    "PoolApi",
    "PreferenceApi",
    "ScreenerApi",
    "StockInfoApi",
    "UnauthorizedError",
    "UserApi",
    "handle_http_error",
]
