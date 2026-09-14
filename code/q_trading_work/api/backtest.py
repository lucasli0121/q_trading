"""
Author: liguoqiang
Date: 2026-09-14
Description: 回测结果 API 客户端 —— 封装策略回测结果的保存与查询。

    服务端接口（均在 StrategyRouter 下，按策略模板 ID 关联）：
    - POST /api/strategy/{strategy_id}/backtest  保存回测结果（body: {"result_data": {...}}）
    - GET  /api/strategy/{strategy_id}/backtest  查询该策略的历史回测记录（按 create_time 倒序）

    本模块是回测结果读写的**唯一入口**：保存时把 BacktestDao 序列化为请求体，
    查询时把服务端记录反序列化回 BacktestDao，字段口径统一由 DAO 维护。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient
from dao.backtest_dao import BacktestDao


class BacktestApi:
    """策略回测结果 API 客户端。

    封装 /api/strategy/{strategy_id}/backtest 的保存与查询：

    使用方式::

        api = BacktestApi(client)
        api.save(dao)                       # 保存 BacktestDao
        records = api.get("s-1")            # -> list[BacktestDao]
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化回测结果 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    # ---- 路径 ----

    @staticmethod
    def _path(strategy_id: str) -> str:
        """拼接回测结果接口路径。

        :param strategy_id: 策略模板 ID（StrategyDao._id）
        :return: /api/strategy/{strategy_id}/backtest
        """
        return f"/api/strategy/{strategy_id}/backtest"

    # ---- 保存 ----

    def save(self, dao: BacktestDao) -> str:
        """保存回测结果（POST /api/strategy/{strategy_id}/backtest）。

        :param dao: 回测结果数据对象（strategy_id 不可为空）
        :return: 服务端返回的记录 ID
        :raises ValueError: dao.strategy_id 为空
        """
        if not dao.strategy_id:
            raise ValueError("保存回测结果需要 strategy_id（策略模板 ID）")
        return self.save_result_data(dao.strategy_id, dao.to_result_data())

    def save_result_data(self, strategy_id: str, result_data: dict[str, Any]) -> str:
        """按原始 result_data 保存回测结果（兼容旧调用方）。

        :param strategy_id: 策略模板 ID（StrategyDao._id）
        :param result_data: 回测结果 JSON
        :return: 服务端返回的记录 ID
        """
        body: dict[str, dict[str, Any]] = {"result_data": result_data}
        result: Any = self._client.post(self._path(strategy_id), body)
        self.logger.info("回测结果已保存: strategy_id=%s", strategy_id)
        return str(result)

    # ---- 查询 ----

    def get_raw(self, strategy_id: str) -> list[dict[str, Any]]:
        """查询策略的历史回测记录（原始字典列表）。

        :param strategy_id: 策略模板 ID（StrategyDao._id）
        :return: 回测记录列表（按 create_time 倒序），无记录时返回空列表
        """
        result: Any = self._client.get(self._path(strategy_id))
        return list(result) if result else []

    def get(self, strategy_id: str) -> list[BacktestDao]:
        """查询策略的历史回测记录并解析为 BacktestDao 列表。

        :param strategy_id: 策略模板 ID（StrategyDao._id）
        :return: BacktestDao 列表，无记录时返回空列表
        """
        return [
            BacktestDao.from_db_record(record)
            for record in self.get_raw(strategy_id)
            if isinstance(record, dict)
        ]
