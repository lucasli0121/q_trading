#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-09-10
Description: 订单管理 API — 封装订单的创建、查询、更新、状态变更接口。
    对应后端 /api/order/* 接口（订单管理）。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class OrderApi:
    """订单管理 API 客户端。

    封装 /api/order/* 接口：
    - create: 创建订单
    - list_by_strategy: 按用户策略 ID 查询订单列表
    - list_by_user: 按当前用户查询订单列表（可按时间/状态/动作过滤）
    - get: 查询单个订单详情
    - get_latest: 查询某策略下指定股票（可多个）的最新订单
    - update: 更新订单（部分字段，None 表示不修改）
    - update_status: 更新订单状态

    订单字段（OrderInfo）：
        id, user_strategy_id, stock_code, entrust_quantity, trade_price,
        trade_quantity, position_price, profit_rate, profit_amount,
        commission_fee, status, action, create_time
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化订单管理 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    # ---- 订单 CRUD ----

    def create(
        self,
        user_strategy_id: str,
        stock_code: str,
        entrust_quantity: int,
        trade_price: float,
        trade_quantity: int = 0,
        position_price: float = 0.0,
        profit_rate: float = 0.0,
        profit_amount: float = 0.0,
        commission_fee: float = 0.0,
        status: str = "",
        action: str = "",
        create_time: str = "",
    ) -> dict[str, Any]:
        """创建订单。

        :param user_strategy_id: 用户策略 ID
        :param stock_code: 股票代码
        :param entrust_quantity: 委托数量
        :param trade_price: 交易价格
        :param trade_quantity: 交易数量（默认 0，表示未成交）
        :param position_price: 持仓价（可选）
        :param profit_rate: 收益率（可选）
        :param profit_amount: 收益额（可选）
        :param commission_fee: 手续费（可选）
        :param status: 订单状态（可选）
        :param action: 买卖方向：买入/卖出（可选）
        :param create_time: 订单时间 YYYY-MM-DD HH:MM:SS（可选，为空则后端填当前时间）
        :return: 创建后的订单信息
        """
        body: dict[str, Any] = {
            "user_strategy_id": user_strategy_id,
            "stock_code": stock_code,
            "entrust_quantity": entrust_quantity,
            "trade_price": trade_price,
        }
        if trade_quantity:
            body["trade_quantity"] = trade_quantity
        if position_price:
            body["position_price"] = position_price
        if profit_rate:
            body["profit_rate"] = profit_rate
        if profit_amount:
            body["profit_amount"] = profit_amount
        if commission_fee:
            body["commission_fee"] = commission_fee
        if status:
            body["status"] = status
        if action:
            body["action"] = action
        if create_time:
            body["create_time"] = create_time
        result: Any = self._client.post("/api/order/create", body)
        return dict(result) if result else {}

    def list_by_strategy(self, user_strategy_id: str) -> list[dict[str, Any]]:
        """按用户策略 ID 查询订单列表。

        :param user_strategy_id: 用户策略 ID
        :return: 订单信息列表
        """
        result: Any = self._client.get(f"/api/order/list/{user_strategy_id}")
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    def list_by_user(
        self,
        start_time: str = "",
        end_time: str = "",
        status: str = "",
        action: str = "",
    ) -> list[dict[str, Any]]:
        """按当前用户查询订单列表（可按时间/状态/动作过滤）。

        :param start_time: 起始时间 YYYY-MM-DD HH:MM:SS（可选，为空则不限制）
        :param end_time: 结束时间 YYYY-MM-DD HH:MM:SS（可选，为空则不限制）
        :param status: 订单状态（可选，为空则查询所有状态）
        :param action: 订单动作 买入/卖出（可选，为空则查询所有动作）
        :return: 订单信息列表
        """
        params: dict[str, Any] = {}
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        if status:
            params["status"] = status
        if action:
            params["action"] = action
        result: Any = self._client.get(
            "/api/order/user/list",
            params if params else None,
        )
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    def get(self, order_id: str) -> dict[str, Any]:
        """查询单个订单详情。

        :param order_id: 订单 ID
        :return: 订单信息字典，不存在时返回空字典
        """
        result: Any = self._client.get(f"/api/order/{order_id}")
        return dict(result) if result else {}

    def get_latest(
        self,
        user_strategy_id: str,
        stock_codes: list[str],
        action: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        """查询某策略下指定股票（可多个）的最新订单。

        :param user_strategy_id: 用户策略 ID
        :param stock_codes: 股票代码列表，内部去重保序后用英文逗号拼接传后端
        :param action: 订单动作 买入/卖出（可选，为空则不限制）
        :param status: 订单状态（可选，为空则不限制）
        :return: 最新订单列表（list[dict]），无匹配订单时返回空列表
        """
        # 去重保序、过滤空值，拼成逗号分隔字符串传后端
        seen: set[str] = set()
        codes: list[str] = []
        for c in stock_codes:
            cs: str = str(c or "").strip()
            if cs and cs not in seen:
                seen.add(cs)
                codes.append(cs)
        if not codes:
            return []
        params: dict[str, Any] = {
            "stock_codes": ",".join(codes),
            "user_strategy_id": user_strategy_id,
        }
        if action:
            params["action"] = action
        if status:
            params["status"] = status
        result: Any = self._client.get("/api/order/latest", params)
        return list(result) if result else []

    def update(
        self,
        order_id: str,
        stock_code: str | None = None,
        entrust_quantity: int | None = None,
        trade_price: float | None = None,
        trade_quantity: int | None = None,
        position_price: float | None = None,
        profit_rate: float | None = None,
        profit_amount: float | None = None,
        commission_fee: float | None = None,
        status: str | None = None,
        action: str | None = None,
        create_time: str | None = None,
    ) -> dict[str, Any]:
        """更新订单（部分字段，None 表示不修改）。

        :param order_id: 订单 ID
        :param stock_code: 股票代码（可选，None 表示不修改）
        :param entrust_quantity: 委托数量（可选，None 表示不修改）
        :param trade_price: 交易价格（可选，None 表示不修改）
        :param trade_quantity: 交易数量（可选，None 表示不修改）
        :param position_price: 持仓价（可选，None 表示不修改）
        :param profit_rate: 收益率（可选，None 表示不修改）
        :param profit_amount: 收益额（可选，None 表示不修改）
        :param commission_fee: 手续费（可选，None 表示不修改）
        :param status: 订单状态（可选，None 表示不修改）
        :param action: 买卖方向（可选，None 表示不修改）
        :param create_time: 订单时间（可选，None 表示不修改）
        :return: 更新后的订单信息
        """
        body: dict[str, Any] = {}
        if stock_code is not None:
            body["stock_code"] = stock_code
        if entrust_quantity is not None:
            body["entrust_quantity"] = entrust_quantity
        if trade_price is not None:
            body["trade_price"] = trade_price
        if trade_quantity is not None:
            body["trade_quantity"] = trade_quantity
        if position_price is not None:
            body["position_price"] = position_price
        if profit_rate is not None:
            body["profit_rate"] = profit_rate
        if profit_amount is not None:
            body["profit_amount"] = profit_amount
        if commission_fee is not None:
            body["commission_fee"] = commission_fee
        if status is not None:
            body["status"] = status
        if action is not None:
            body["action"] = action
        if create_time is not None:
            body["create_time"] = create_time
        result: Any = self._client.put(f"/api/order/{order_id}", body)
        return dict(result) if result else {}

    def update_status(self, order_id: str, status: str) -> dict[str, Any]:
        """更新订单状态。

        :param order_id: 订单 ID
        :param status: 目标订单状态
        :return: 更新后的订单信息
        """
        body: dict[str, str] = {"status": status}
        result: Any = self._client.put(f"/api/order/{order_id}/status", body)
        return dict(result) if result else {}
