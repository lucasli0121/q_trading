"""
Author: liguoqiang
Date: 2026-08-12
Description: 策略选股结果 API — 封装策略选股结果的保存、查询、删除接口。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class StrategySelectStockApi:
    """策略选股结果 API 客户端。

    封装 /api/strategy_select_stocks/* 接口：
    - add: 保存策略选股结果
    - list: 查询策略选股结果列表
    - list_by_user: 按用户查询选股结果
    - delete: 删除选股结果
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化策略选股结果 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def add(
        self,
        user_strategy_id: str,
        codes: list[str],
    ) -> list[dict[str, Any]]:
        """保存策略选股结果。

        :param user_strategy_id: 用户策略 ID（UserStrategy 记录 ID）
        :param codes: 选中的股票代码列表
        :return: 保存结果列表
        """
        body: dict[str, Any] = {
            "user_strategy_id": user_strategy_id,
            "codes": codes,
        }
        result: Any = self._client.post("/api/strategy_select_stocks/add", body)
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    def list(
        self,
        user_strategy_id: str = "",
        start_time: str = "",
        end_time: str = "",
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """查询策略选股结果列表。

        :param user_strategy_id: 用户策略 ID（UserStrategyDao 记录 ID，可选）
        :param start_time: 开始日期 YYYY-MM-DD（可选）

            注意：后端文档写的是 "YYYY-MM-DD HH:MM:SS"，但数据库 create_time
            实际只保存日期、不带时分秒，传带时间的字符串会查不到数据。
        :param end_time: 结束日期 YYYY-MM-DD（可选，同 start_time）
        :param skip: 分页偏移（可选）
        :param limit: 分页大小（可选，0 表示不限制）
        :return: 选股结果列表（含实时行情数据）
        """
        params: dict[str, Any] = {}
        if user_strategy_id:
            params["user_strategy_id"] = user_strategy_id
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit
        result: Any = self._client.get(
            "/api/strategy_select_stocks/list",
            params if params else None,
        )
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    def list_by_user(
        self,
        start_time: str = "",
        end_time: str = "",
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """查询当前用户所有策略的选股结果。

        :param start_time: 开始日期 YYYY-MM-DD（可选）

            注意：后端文档写的是 "YYYY-MM-DD HH:MM:SS"，但数据库 create_time
            实际只保存日期、不带时分秒，传带时间的字符串会查不到数据。
        :param end_time: 结束日期 YYYY-MM-DD（可选，同 start_time）
        :param skip: 分页偏移（可选）
        :param limit: 分页大小（可选，0 表示不限制）
        :return: 选股结果列表（含实时行情数据）
        """
        params: dict[str, Any] = {}
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit
        result: Any = self._client.get(
            "/api/strategy_select_stocks/by_user",
            params if params else None,
        )
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    def delete(self, id: str) -> str:
        """删除选股结果。

        :param id: 选股结果 ID
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(f"/api/strategy_select_stocks/{id}")
        return str(result)
