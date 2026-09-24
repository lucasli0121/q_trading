#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2024-08-22 23:29:20
LastEditors: liguoqiang
LastEditTime: 2026-06-27
Description: 定义一个全局变量，用于存储全局变量
    应用上下文
"""

import threading
from typing import TYPE_CHECKING, Any, Self, cast

from api.blacklist import BlacklistApi
from api.client import ApiClient
from api.finance import FinanceApi
from api.market import MarketApi
from api.pool import PoolApi
from api.preference import PreferenceApi
from api.screener import ScreenerApi
from api.stock_info import StockInfoApi
from api.user import UserApi
from mq.mqtt_client import MqttClient

if TYPE_CHECKING:
    from api.order import OrderApi
    from api.strategy import StrategyApi
    from api.strategy_select_stock import StrategySelectStockApi
    from api.system_message import SystemMessageApi
    from api.trade_signal import TradeSignalApi
    from api.user_strategy import UserStrategyApi
    from core.strategy_cache import StrategyTemplateCache
    from mq.signal_manager import SignalManager


class AppContext:
    _instance: "AppContext | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cast(Self, cls._instance)

    def __init__(self):
        if not hasattr(self, '_initialized'):
            from colors.theme import ThemeManager
            self._initialized = True
            self.theme_manager = ThemeManager()
            self.mqtt_client = MqttClient()

    # ---- API 客户端（懒加载） ----

    @property
    def api_client(self) -> "ApiClient":
        """获取全局 API 客户端实例（懒加载）。"""
        if not hasattr(self, '_api_client'):
            from api.client import ApiClient
            self._api_client = ApiClient()
        return self._api_client

    @property
    def user_api(self) -> "UserApi":
        """获取用户管理 API 实例（懒加载）。"""
        if not hasattr(self, '_user_api'):
            from api.user import UserApi
            self._user_api = UserApi(self.api_client)
        return self._user_api

    @property
    def pool_api(self) -> "PoolApi":
        """获取股票池管理 API 实例（懒加载）。"""
        if not hasattr(self, '_pool_api'):
            from api.pool import PoolApi
            self._pool_api = PoolApi(self.api_client)
        return self._pool_api

    @property
    def market_api(self) -> "MarketApi":
        """获取行情管理 API 实例（懒加载）。"""
        if not hasattr(self, '_market_api'):
            from api.market import MarketApi
            self._market_api = MarketApi(self.api_client)
        return self._market_api

    @property
    def finance_api(self) -> "FinanceApi":
        """获取财务管理 API 实例（懒加载）。"""
        if not hasattr(self, '_finance_api'):
            from api.finance import FinanceApi
            self._finance_api = FinanceApi(self.api_client)
        return self._finance_api

    @property
    def stock_info_api(self) -> "StockInfoApi":
        """获取股票信息查询 API 实例（懒加载）。"""
        if not hasattr(self, '_stock_info_api'):
            from api.stock_info import StockInfoApi
            self._stock_info_api = StockInfoApi(self.api_client)
        return self._stock_info_api

    @property
    def blacklist_api(self) -> "BlacklistApi":
        """获取黑名单管理 API 实例（懒加载）。"""
        if not hasattr(self, '_blacklist_api'):
            from api.blacklist import BlacklistApi
            self._blacklist_api = BlacklistApi(self.api_client)
        return self._blacklist_api

    @property
    def screener_api(self) -> "ScreenerApi":
        """获取股票筛选 API 实例（懒加载）。"""
        if not hasattr(self, '_screener_api'):
            from api.screener import ScreenerApi
            self._screener_api = ScreenerApi(self.api_client)
        return self._screener_api

    @property
    def preference_api(self) -> "PreferenceApi":
        """获取用户偏好 API 实例（懒加载）。"""
        if not hasattr(self, '_preference_api'):
            from api.preference import PreferenceApi
            self._preference_api = PreferenceApi(self.api_client)
        return self._preference_api

    @property
    def strategy_api(self) -> "StrategyApi":
        """获取策略模板管理 API 实例（懒加载）。"""
        if not hasattr(self, '_strategy_api'):
            from api.strategy import StrategyApi
            self._strategy_api = StrategyApi(self.api_client)
        return self._strategy_api

    @property
    def user_strategy_api(self) -> "UserStrategyApi":
        """获取用户策略关联 API 实例（懒加载）。

        提供用户策略关联、执行结果（list_executions）、运行记录等接口。
        """
        if not hasattr(self, '_user_strategy_api'):
            from api.user_strategy import UserStrategyApi
            self._user_strategy_api = UserStrategyApi(self.api_client)
        return self._user_strategy_api

    @property
    def strategy_cache(self) -> "StrategyTemplateCache":
        """获取策略模板缓存实例（懒加载，进程级单例）。

        缓存策略模板（全局公共数据，不区分用户），供页面高频查询复用，
        避免 strategy_api.get_by_id() 反复请求后端。
        """
        if not hasattr(self, '_strategy_cache'):
            from core.strategy_cache import StrategyTemplateCache
            self._strategy_cache = StrategyTemplateCache()
        return self._strategy_cache

    @property
    def order_api(self) -> "OrderApi":
        """获取订单管理 API 实例（懒加载）。

        提供订单的创建、按策略/按用户查询、详情、更新、状态变更接口
        （/api/order/*）。
        """
        if not hasattr(self, '_order_api'):
            from api.order import OrderApi
            self._order_api = OrderApi(self.api_client)
        return self._order_api

    @property
    def trade_signal_api(self) -> "TradeSignalApi":
        """获取交易信号 API 实例（懒加载）。

        提供交易信号的保存与查询接口（如查询今日信号初始化仪表盘计数）。
        """
        if not hasattr(self, '_trade_signal_api'):
            from api.trade_signal import TradeSignalApi
            self._trade_signal_api = TradeSignalApi(self.api_client)
        return self._trade_signal_api

    @property
    def system_message_api(self) -> "SystemMessageApi":
        """获取系统消息 API 实例（懒加载）。

        提供当前用户系统消息查询接口。
        """
        if not hasattr(self, '_system_message_api'):
            from api.system_message import SystemMessageApi
            self._system_message_api = SystemMessageApi(self.api_client)
        return self._system_message_api

    @property
    def strategy_select_stock_api(self) -> "StrategySelectStockApi":
        """获取策略选股结果 API 实例（懒加载）。

        提供策略选股结果的保存、按用户查询、按策略查询等接口
        （/api/strategy_select_stocks/*）。
        """
        if not hasattr(self, '_strategy_select_stock_api'):
            from api.strategy_select_stock import StrategySelectStockApi
            self._strategy_select_stock_api = StrategySelectStockApi(self.api_client)
        return self._strategy_select_stock_api

    @property
    def signal_manager(self) -> "SignalManager":
        """获取全局交易信号管理器实例（懒加载）。

        管理 MQTT 交易信号的订阅、存储和查询。
        """
        if not hasattr(self, '_signal_manager'):
            from mq.signal_manager import SignalManager
            self._signal_manager = SignalManager()
        return self._signal_manager

