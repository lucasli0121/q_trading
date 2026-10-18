"""q_trading_archive：把服务端滚动窗口内的行情按日转存为本地 parquet。

只读 mongo，不写任何库、不连 redis、不订阅 mqtt。独立进程、独立部署，
与 q_trading_work（可多实例）完全解耦。
"""

__all__ = ["config", "mongo_reader", "store", "state", "archiver"]
