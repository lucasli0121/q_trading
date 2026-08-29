"""
Author: liguoqiang
Date: 2026-07-02
Description: 订单管理 API — 封装订单创建、查询、撤单、状态查询等接口。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient
from dao.order_dao import OrderDao, OrderStatus


class OrderApi:
    """订单管理 API 客户端。

    封装 /api/order/* 接口，包含：
    - create: 创建订单
    - get: 查询单个订单
    - list: 查询指定用户策略下的订单列表
    - list_by_user: 查询当前登录用户的订单列表
    - cancel: 撤单
    - status: 查询订单状态
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化订单 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def _to_api_payload(order: OrderDao | dict[str, Any]) -> dict[str, Any]:
        """将内部订单对象/字典转换为后端 Swagger 需要的字段。"""
        if isinstance(order, OrderDao):
            payload: dict[str, Any] = {
                "user_strategy_id": order.user_strategy_id,
                "stock_code": order.stock_code,
                "entrust_quantity": order.entrust_quantity,
                "trade_price": order.trade_price,
                "trade_quantity": order.trade_quantity,
                "position_price": order.position_price,
                "profit_rate": order.profit_rate,
                "profit_amount": order.profit_amount,
                "commission_fee": order.commission_fee,
                "status": order.status,
                "create_time": order.create_time,
                "action": order.action,
            }
            return payload

        payload = {
            "user_strategy_id": order.get("user_strategy_id", ""),
            "stock_code": order.get("stock_code", ""),
            "entrust_quantity": order.get("entrust_quantity", 0),
            "trade_price": order.get("trade_price", 0.0),
            "trade_quantity": order.get("trade_quantity", 0),
            "position_price": order.get("position_price", 0.0),
            "profit_rate": order.get("profit_rate", 0.0),
            "profit_amount": order.get("profit_amount", 0.0),
            "commission_fee": order.get("commission_fee", 0.0),
            "status": order.get("status", "委托"),
            "create_time": order.get("create_time") or order.get("time", ""),
            "action": order.get("action", "买入"),
        }
        return payload

    @staticmethod
    def _from_api_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """将后端返回的字段映射为 OrderDao 兼容字段。"""
        return {
            "id": payload.get("id", payload.get("_id", "")),
            "user_strategy_id": payload.get("user_strategy_id", ""),
            "stock_code": payload.get("stock_code", ""),
            "entrust_quantity": payload.get("entrust_quantity", 0),
            "trade_price": payload.get("trade_price", 0.0),
            "trade_quantity": payload.get("trade_quantity", 0),
            "position_price": payload.get("position_price", 0.0),
            "profit_rate": payload.get("profit_rate", 0.0),
            "profit_amount": payload.get("profit_amount", 0.0),
            "commission_fee": payload.get("commission_fee", 0.0),
            "status": payload.get("status", "委托"),
            "create_time": payload.get("create_time", payload.get("time", "")),
            "action": payload.get("action", "买入"),
        }

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
        status: str = "委托",
        create_time: str = "",
        time: str | None = None,
        action: str = "买入",
    ) -> dict[str, Any]:
        """创建订单。

        :param user_strategy_id: 用户策略关联 ID
        :param stock_code: 股票代码
        :param entrust_quantity: 委托数量
        :param trade_price: 交易价格
        :param trade_quantity: 交易数量
        :param position_price: 持仓均价
        :param profit_rate: 收益率
        :param profit_amount: 收益金额
        :param commission_fee: 手续费
        :param status: 订单状态
        :param create_time: 订单创建时间
        :param time: 兼容后端字段名，优先用于创建时间
        :param action: 交易方向
        :return: 创建结果字典
        """
        effective_time: str = time or create_time
        body: dict[str, Any] = {
            "user_strategy_id": user_strategy_id,
            "stock_code": stock_code,
            "entrust_quantity": entrust_quantity,
            "trade_price": trade_price,
            "trade_quantity": trade_quantity,
            "position_price": position_price,
            "profit_rate": profit_rate,
            "profit_amount": profit_amount,
            "commission_fee": commission_fee,
            "status": status,
            "create_time": effective_time,
            "action": action,
        }
        result: Any = self._client.post("/api/order/create", body)
        if isinstance(result, dict) and result:
            return self._from_api_payload(result)
        return self._from_api_payload(body | {"create_time": effective_time, "time": effective_time, "id": ""})

    def get(self, order_id: str) -> dict[str, Any]:
        """查询单个订单详情。

        :param order_id: 订单 ID
        :return: 订单详情字典
        """
        result: Any = self._client.get(f"/api/order/{order_id}")
        return dict(result) if result else {}

    def list(self, user_strategy_id: str) -> list[dict[str, Any]]:
        """查询指定用户策略下的订单列表。

        :param user_strategy_id: 用户策略关联 ID
        :return: 订单列表
        """
        result: Any = self._client.get(f"/api/order/list/{user_strategy_id}", None)
        if not result:
            return []
        if isinstance(result, list):
            return [self._from_api_payload(dict(item)) for item in result]
        return [self._from_api_payload(dict(result))]

    def list_by_user(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
        status: str | None = None,
        action: str | None = None,
    ) -> list[dict[str, Any]]:
        """查询当前登录用户的订单列表（需要认证，按 token 解析用户）。

        :param start_time: 开始时间，格式 YYYY-MM-DD HH:MM:SS
        :param end_time: 结束时间，格式 YYYY-MM-DD HH:MM:SS
        :param status: 订单状态筛选（委托/成功/失败/撤单）
        :param action: 交易方向筛选（买入/卖出）
        :return: 当前用户所有策略的订单列表
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

        result: Any = self._client.get("/api/order/user/list", params if params else None)
        if not result:
            return []
        if isinstance(result, list):
            return [self._from_api_payload(dict(item)) for item in result]
        return [self._from_api_payload(dict(result))]

    def cancel(self, order_id: str) -> dict[str, Any]:
        """撤销订单。

        服务端未提供独立的撤单接口，通过状态更新接口将订单置为“撤单”。

        :param order_id: 订单 ID
        :return: 撤单结果字典
        """
        return self.update_status(order_id, OrderStatus.CANCELLED.value)

    def update(self, order_id: str, order: OrderDao) -> dict[str, Any]:
        """更新订单全部字段。

        :param order_id: 订单 ID
        :param order: 包含更新后字段的订单对象
        :return: 更新后的订单信息字典
        """
        body: dict[str, Any] = self._to_api_payload(order)
        # 服务端 OrderUpdateRequest 不含 user_strategy_id / id，去掉避免多余字段
        body.pop("user_strategy_id", None)
        result: Any = self._client.put(f"/api/order/{order_id}", body)
        return self._from_api_payload(dict(result) if result else body)

    def update_status(self, order_id: str, status: str) -> dict[str, Any]:
        """更新订单状态。

        :param order_id: 订单 ID
        :param status: 新状态
        :return: 更新后的订单信息字典
        """
        result: Any = self._client.put(f"/api/order/{order_id}/status", {"status": status})
        return self._from_api_payload(dict(result) if result else {})
