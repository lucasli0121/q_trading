"""
Author: liguoqiang
Date: 2026-10-13
Description: 每日个股阀门 API — 封装盘后阀门记录的保存与查询接口。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class StrategyDailyValveApi:
    """每日个股阀门 API 客户端。

    封装 /api/strategy_daily_valve 接口：
    - add: 批量保存当日阀门
    - list: 按用户策略查询阀门
    - delete: 删除某用户策略的全部阀门
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化每日阀门 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def add(
        self, user_strategy_id: str, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """批量保存当日阀门（按 user_strategy_id + stock_code + trade_date 覆盖）。

        :param user_strategy_id: 用户策略 ID
        :param items: 阀门列表，每项含 stock_code/trade_date/buy_valve/sell_valve 等
        :return: 保存后的记录列表
        """
        if not user_strategy_id or not items:
            return []
        body: dict[str, Any] = {
            "user_strategy_id": user_strategy_id,
            "items": items,
        }
        result: Any = self._client.post("/api/strategy_daily_valve/add", body)
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    def list(
        self, user_strategy_id: str, trade_date: str = ""
    ) -> list[dict[str, Any]]:
        """查询某用户策略的阀门记录。

        :param user_strategy_id: 用户策略 ID
        :param trade_date: 交易日 YYYY-MM-DD，空串表示不过滤
        :return: 阀门记录列表
        """
        if not user_strategy_id:
            return []
        params: dict[str, Any] = {"user_strategy_id": user_strategy_id}
        if trade_date:
            params["trade_date"] = trade_date
        result: Any = self._client.get("/api/strategy_daily_valve/list", params)
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    def delete(self, user_strategy_id: str) -> str:
        """删除某用户策略的全部阀门记录。

        :param user_strategy_id: 用户策略 ID
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(f"/api/strategy_daily_valve/{user_strategy_id}")
        return str(result)
