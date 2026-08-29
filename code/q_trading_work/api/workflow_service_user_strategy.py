"""
Author: liguoqiang
Date: 2026-08-12
Description: 工作流服务用户策略关联 API — 封装服务与用户策略关联的 CRUD 接口。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class WorkflowServiceUserStrategyApi:
    """工作流服务用户策略关联 API 客户端。

    封装 /api/workflow_service_user_strategy/* 接口：
    - create: 创建服务与用户策略的关联
    - list: 查询关联列表
    - list_by_service: 按服务名称查询关联
    - get: 查询单个关联
    - update: 更新关联
    - delete: 删除关联
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化工作流服务用户策略关联 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def create(
        self,
        service_name: str,
        user_strategy_ids: list[str],
    ) -> dict[str, Any]:
        """创建服务与用户策略的关联。

        :param service_name: 服务名称
        :param user_strategy_ids: 用户策略 ID 列表
        :return: 创建结果字典
        """
        body: dict[str, Any] = {
            "service_name": service_name,
            "user_strategy_ids": user_strategy_ids,
        }
        result: Any = self._client.post(
            "/api/workflow_service_user_strategy/create", body
        )
        return dict(result) if result else {}

    def list(
        self,
        service_name: str = "",
    ) -> list[dict[str, Any]]:
        """查询服务与用户策略的关联列表。

        :param service_name: 服务名称筛选（可选）
        :return: 关联列表
        """
        params: dict[str, str] | None = None
        if service_name:
            params = {"service_name": service_name}
        result: Any = self._client.get(
            "/api/workflow_service_user_strategy/list", params
        )
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    def list_by_service(self, service_name: str) -> list[dict[str, Any]]:
        """按服务名称查询关联列表。

        :param service_name: 服务名称
        :return: 关联列表
        """
        result: Any = self._client.get(
            f"/api/workflow_service_user_strategy/service/{service_name}"
        )
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    def get(self, id: str) -> dict[str, Any]:
        """查询单个关联详情。

        :param id: 关联 ID
        :return: 关联信息字典
        """
        result: Any = self._client.get(
            f"/api/workflow_service_user_strategy/{id}"
        )
        return dict(result) if result else {}

    def update(
        self,
        id: str,
        service_name: str | None = None,
        user_strategy_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """更新服务与用户策略的关联。

        :param id: 关联 ID
        :param service_name: 服务名称（可选，None 表示不修改）
        :param user_strategy_ids: 用户策略 ID 列表（可选，None 表示不修改）
        :return: 更新后的关联信息字典
        """
        body: dict[str, Any] = {}
        if service_name is not None:
            body["service_name"] = service_name
        if user_strategy_ids is not None:
            body["user_strategy_ids"] = user_strategy_ids
        result: Any = self._client.put(
            f"/api/workflow_service_user_strategy/{id}", body
        )
        return dict(result) if result else {}

    def delete(self, id: str) -> str:
        """删除服务与用户策略的关联。

        :param id: 关联 ID
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(
            f"/api/workflow_service_user_strategy/{id}"
        )
        return str(result)
