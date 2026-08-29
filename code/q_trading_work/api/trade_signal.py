"""
Author: liguoqiang
Date: 2026-08-03
Description: 交易信号 API — 封装交易信号的保存、查询、列表接口。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class TradeSignalApi:
    """交易信号 API 客户端。

    封装 /api/trade_signal/* 接口：
    - add: 保存交易信号
    - latest: 查询最近一次交易信号
    - list: 查询交易信号列表
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化交易信号 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def add(
        self,
        strategy_id: str,
        stock_code: str,
        trade_price: float,
        action: str,
        profit_rate: float = 0.0,
        profit_amount: float = 0.0,
        reason: str = "",
    ) -> dict[str, Any]:
        """保存交易信号。

        :param strategy_id: 策略模板 ID
        :param stock_code: 股票代码
        :param trade_price: 交易价格
        :param action: 交易方向（买入/卖出）
        :param profit_rate: 收益率（卖出时计算）
        :param profit_amount: 收益金额（卖出时计算）
        :param reason: 信号原因说明（可选）
        :return: 保存结果字典
        """
        body: dict[str, Any] = {
            "strategy_id": strategy_id,
            "stock_code": stock_code,
            "trade_price": trade_price,
            "action": action,
            "profit_rate": round(profit_rate, 2),
            "profit_amount": round(profit_amount, 2),
            "reason": reason,
        }
        result: Any = self._client.post("/api/trade_signal/add", body)
        return dict(result) if result else {}

    def latest(
        self,
        strategy_id: str,
        stock_code: str,
        action: str = "",
    ) -> dict[str, Any]:
        """查询最近一次交易信号。

        :param strategy_id: 策略模板 ID
        :param stock_code: 股票代码
        :param action: 交易方向筛选（可选，买入/卖出，不传则不过滤方向）
        :return: 最近一次交易信号字典，无记录时返回空字典
        """
        params: dict[str, str] = {
            "strategy_id": strategy_id,
            "stock_code": stock_code,
        }
        if action:
            params["action"] = action
        result: Any = self._client.get(
            "/api/trade_signal/latest",
            params=params,
        )
        return dict(result) if result else {}

    def delete(self, signal_id: str) -> str:
        """删除交易信号。

        :param signal_id: 信号 ID
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(f"/api/trade_signal/{signal_id}")
        return str(result)

    def list(
        self,
        strategy_id: str | None = None,
        action: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """查询交易信号列表。

        :param strategy_id: 策略模板 ID（可选）
        :param action: 交易方向筛选（可选，买入/卖出）
        :param start_time: 开始时间（可选，格式 YYYY-MM-DD HH:MM:SS）
        :param end_time: 结束时间（可选，格式 YYYY-MM-DD HH:MM:SS）
        :param skip: 分页偏移（可选）
        :param limit: 分页大小（可选，0 表示不限制）
        :return: 交易信号列表
        """
        params: dict[str, Any] = {}
        if strategy_id:
            params["strategy_id"] = strategy_id
        if action:
            params["action"] = action
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit

        result: Any = self._client.get(
            "/api/trade_signal/list",
            params if params else None,
        )
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]
