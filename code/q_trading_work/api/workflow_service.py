"""
Author: liguoqiang
Date: 2026-08-12
Description: 工作流服务 API — 封装工作流服务的注册、查询、更新、删除接口。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class WorkflowServiceApi:
    """工作流服务 API 客户端。

    封装 /api/workflow_service/* 接口：
    - create: 注册工作流服务
    - list: 查询工作流服务列表
    - get: 查询单个工作流服务
    - update: 更新工作流服务
    - delete: 删除工作流服务
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化工作流服务 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def create(
        self,
        service_name: str,
        description: str = "",
        is_online: bool = False,
    ) -> dict[str, Any]:
        """注册工作流服务。

        :param service_name: 服务名称（唯一标识）
        :param description: 描述（可选）
        :param is_online: 是否在线，默认 False
        :return: 创建结果字典
        """
        body: dict[str, Any] = {
            "service_name": service_name,
            "description": description,
            "is_online": is_online,
        }
        result: Any = self._client.post("/api/workflow_service/create", body)
        return dict(result) if result else {}

    def list(
        self,
        service_name: str = "",
        is_online: bool | None = None,
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """查询工作流服务列表。

        :param service_name: 服务名称筛选（可选）
        :param is_online: 在线状态筛选（可选，None 表示不筛选）
        :param skip: 分页偏移（可选）
        :param limit: 分页大小（可选，0 表示不限制）
        :return: 工作流服务列表
        """
        params: dict[str, Any] = {}
        if service_name:
            params["service_name"] = service_name
        if is_online is not None:
            params["is_online"] = is_online
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit
        result: Any = self._client.get(
            "/api/workflow_service/list",
            params if params else None,
        )
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    def get(self, id: str) -> dict[str, Any]:
        """查询单个工作流服务。

        :param id: 服务 ID
        :return: 服务信息字典
        """
        result: Any = self._client.get(f"/api/workflow_service/{id}")
        return dict(result) if result else {}

    def update(
        self,
        id: str,
        service_name: str | None = None,
        description: str | None = None,
        is_online: bool | None = None,
    ) -> dict[str, Any]:
        """更新工作流服务。

        :param id: 服务 ID
        :param service_name: 服务名称（可选，None 表示不修改）
        :param description: 描述（可选，None 表示不修改）
        :param is_online: 是否在线（可选，None 表示不修改）
        :return: 更新后的服务信息字典
        """
        body: dict[str, Any] = {}
        if service_name is not None:
            body["service_name"] = service_name
        if description is not None:
            body["description"] = description
        if is_online is not None:
            body["is_online"] = is_online
        result: Any = self._client.put(f"/api/workflow_service/{id}", body)
        return dict(result) if result else {}

    def delete(self, id: str) -> str:
        """删除工作流服务。

        :param id: 服务 ID
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(f"/api/workflow_service/{id}")
        return str(result)
