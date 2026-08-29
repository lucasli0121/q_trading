"""
Author: liguoqiang
Date: 2024-08-22 23:29:20
LastEditors: liguoqiang
LastEditTime: 2026-08-17 00:00:00
Description: 定义一个全局变量，用于存储全局变量
    应用上下文
"""

from api.blacklist import BlacklistApi
from api.client import ApiClient
from api.finance import FinanceApi
from api.market import MarketApi
from api.pool import PoolApi
from api.screener import ScreenerApi
from api.stock_info import StockInfoApi
from api.strategy import StrategyApi
from api.user_strategy import UserStrategyApi
from api.user import UserApi
from api.order import OrderApi
from api.preference import PreferenceApi
from api.trade_signal import TradeSignalApi
from mq.mqtt_client import MqttClient
import threading

class AppContext:
    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, '_initialized'):
            from factor.factor_manager import FactorManager
            self._initialized = True
            self.factor_manager = FactorManager()
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
    def strategy_api(self) -> "StrategyApi":
        """获取策略管理 API 实例（懒加载）。"""
        if not hasattr(self, '_strategy_api'):
            from api.strategy import StrategyApi
            self._strategy_api = StrategyApi(self.api_client)
        return self._strategy_api

    @property
    def user_strategy_api(self) -> "UserStrategyApi":
        """获取用户策略关联管理 API 实例（懒加载）。"""
        if not hasattr(self, '_user_strategy_api'):
            from api.user_strategy import UserStrategyApi
            self._user_strategy_api = UserStrategyApi(self.api_client)
        return self._user_strategy_api

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
    def order_api(self) -> "OrderApi":
        """获取订单管理 API 实例（懒加载）。"""
        if not hasattr(self, '_order_api'):
            from api.order import OrderApi
            self._order_api = OrderApi(self.api_client)
        return self._order_api

    @property
    def trade_signal_api(self) -> "TradeSignalApi":
        """获取交易信号 API 实例（懒加载）。"""
        if not hasattr(self, '_trade_signal_api'):
            from api.trade_signal import TradeSignalApi
            self._trade_signal_api = TradeSignalApi(self.api_client)
        return self._trade_signal_api

    @property
    def preference_api(self) -> "PreferenceApi":
        """获取用户偏好 API 实例（懒加载）。"""
        if not hasattr(self, '_preference_api'):
            from api.preference import PreferenceApi
            self._preference_api = PreferenceApi(self.api_client)
        return self._preference_api

    @property
    def redis_exec(self) -> "RedisExec":
        """获取全局 Redis 执行器实例（懒加载）。

        用于股票行情、分钟缓存等数据的 Redis 读写，
        连接配置见 cfg/stock.cfg [redis] 节。
        """
        if not hasattr(self, '_redis_exec'):
            from redis_db.redis_exec import RedisExec
            self._redis_exec = RedisExec()
        return self._redis_exec

    @property
    def strategy_workflow(self) -> "StrategyWorkflow":
        """获取策略工作流实例（懒加载）。

        工作流管理策略的加载、执行、交易订单和日志记录。
        """
        if not hasattr(self, '_strategy_workflow'):
            from workflow.strategy_workflow import StrategyWorkflow
            self._strategy_workflow = StrategyWorkflow()
        return self._strategy_workflow
