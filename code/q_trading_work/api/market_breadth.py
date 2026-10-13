"""
Author: liguoqiang
Date: 2026-10-13
Description: 每日市场宽度 API — 封装涨跌停家数与门槛偏移的保存与查询接口。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class MarketBreadthApi:
    """每日市场宽度 API 客户端。

    封装 /api/market_breadth 接口：
    - add: 保存某交易日的涨跌停家数与门槛偏移
    - list: 查询市场宽度记录
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化市场宽度 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def add(
        self,
        trade_date: str,
        limit_up: int,
        limit_down: int,
        buy_offset: float,
        sell_offset: float,
        source: str = "akshare",
    ) -> dict[str, Any]:
        """保存某交易日的市场宽度记录（按 trade_date 覆盖）。

        :return: 保存后的记录字典，失败时返回空字典
        """
        if not trade_date:
            return {}
        body: dict[str, Any] = {
            "trade_date": trade_date,
            "limit_up": int(limit_up),
            "limit_down": int(limit_down),
            "buy_offset": float(buy_offset),
            "sell_offset": float(sell_offset),
            "source": source,
        }
        result: Any = self._client.post("/api/market_breadth/add", body)
        return dict(result) if result else {}

    def list(self, trade_date: str = "") -> list[dict[str, Any]]:
        """查询市场宽度记录。

        :param trade_date: 交易日 YYYY-MM-DD，空串表示不过滤（按日期倒序）
        :return: 记录列表
        """
        params: dict[str, Any] = {}
        if trade_date:
            params["trade_date"] = trade_date
        result: Any = self._client.get(
            "/api/market_breadth/list", params if params else None
        )
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]
