'''
Author: liguoqiang
Date: 2024-08-22 23:29:20
LastEditors: liguoqiang
LastEditTime: 2024-08-22 23:36:39
Description: 全局应用上下文，用于存储和管理所有核心服务实例
    单例模式
'''


class AppContext:
    """全局应用上下文，持有所有核心服务的单例引用"""

    _instance: "AppContext | None" = None

    def __new__(cls, *args, **kwargs) -> "AppContext":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "_initialized"):
            from db.mongo.mongo_exec import MongoExec
            from db.redis.redis_exec import RedisExec
            from stock_fetch.stock_fetch import StockFetch
            from mq.mqtt_client import MqttClient

            self._initialized: bool = True
            self.mongo_exec: MongoExec = MongoExec()
            self.redis_exec: RedisExec = RedisExec()
            self.stock_fetch: StockFetch = StockFetch()
            self.mqtt: MqttClient = MqttClient()
