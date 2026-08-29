"""
Author: liguoqiang
Date: 2026-06-27
LastEditors: liguoqiang
LastEditTime: 2026-07-13
Description: 策略模板管理 API — 全局策略模板的 CRUD（不含用户关联）。
    用户策略关联、执行结果、运行记录 请使用 UserStrategyApi；回测结果按策略模板 ID 关联。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class StrategyApi:
    """策略模板管理 API 客户端。

    封装 /api/strategy/* 接口：
    - create: 创建策略模板
    - delete: 删除策略模板
    - get: 按名称查询单个策略模板详情
    - get_by_id: 按策略 ID 查询单个策略模板详情
    - list: 查询所有策略模板
    - save_backtest / get_backtest: 按策略模板 ID 保存/查看回测结果

    注意：用户策略关联（状态、股票池）、执行结果、运行记录
    使用 UserStrategyApi（/api/user_strategy/*）。
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化策略模板 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    # ---- 策略模板 CRUD ----

    def create(
        self,
        name: str,
        strategy_type: str,
        description: str = "",
        class_path: str = "",
        class_name: str = "",
    ) -> dict[str, Any]:
        """创建策略模板（全局定义，由管理员或系统初始化时调用）。

        :param name: 策略名称
        :param strategy_type: 策略类型: 选股策略/盯盘策略/复盘策略
        :param description: 描述（可选）
        :param class_path: 策略类路径（可选）
        :param class_name: 策略类名（可选）
        :return: 包含 strategy_id 的字典
        """
        body: dict[str, str] = {
            "name": name,
            "strategy_type": strategy_type,
        }
        if description:
            body["description"] = description
        if class_path:
            body["class_path"] = class_path
        if class_name:
            body["class_name"] = class_name
        result: Any = self._client.post("/api/strategy/create", body)
        return dict(result)

    def delete(self, name: str) -> str:
        """删除策略模板。

        :param name: 策略名称
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(f"/api/strategy/{name}")
        return str(result)

    def get(self, name: str) -> dict[str, Any]:
        """按名称查询单个策略模板详情。

        :param name: 策略名称
        :return: 策略信息字典
        """
        result: Any = self._client.get(f"/api/strategy/{name}")
        return dict(result)

    def get_by_id(self, strategy_id: str) -> dict[str, Any]:
        """按策略 ID 查询单个策略模板详情。

        配合 UserStrategyApi.list() 使用：先查用户策略关联拿到 strategy_id，
        再通过本接口获取对应策略模板的完整信息。

        :param strategy_id: 策略模板 ID（StrategyDao._id）
        :return: 策略信息字典，不存在时返回空字典
        """
        result: Any = self._client.get(f"/api/strategy/id/{strategy_id}")
        # Note: 新 API 路径参数名为 {id}，此处使用 strategy_id 值填充
        return dict(result) if result else {}

    def list(self) -> list[dict[str, Any]]:
        """查询所有策略模板。

        :return: 策略模板列表
        """
        result: Any = self._client.get("/api/strategy/list")
        return list(result) if result else []

    # ---- 回测结果（按策略模板 ID 关联） ----

    def save_backtest(
        self,
        strategy_id: str,
        result_data: dict[str, Any],
    ) -> str:
        """保存回测结果到策略模板（/api/strategy/{strategy_id}/backtest）。

        :param strategy_id: 策略模板 ID（StrategyDao._id）
        :param result_data: 回测结果数据（JSON 对象）
        :return: 操作结果提示信息
        """
        body: dict[str, dict[str, Any]] = {"result_data": result_data}
        result: Any = self._client.post(
            f"/api/strategy/{strategy_id}/backtest", body
        )
        return str(result)

    def get_backtest(self, strategy_id: str) -> list[dict[str, Any]]:
        """查看策略模板的回测结果。

        :param strategy_id: 策略模板 ID（StrategyDao._id）
        :return: 回测结果列表
        """
        result: Any = self._client.get(
            f"/api/strategy/{strategy_id}/backtest"
        )
        return list(result) if result else []
