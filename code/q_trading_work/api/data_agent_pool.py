"""
Author: liguoqiang
Date: 2026-08-12
Description: 数据代理股票池 API — 封装数据代理与股票池关联的 CRUD 接口。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class DataAgentPoolApi:
    """数据代理股票池 API 客户端。

    封装 /api/data_agent_pool/stocks/* 接口：
    - create: 创建代理股票池关联
    - list: 查询代理股票池列表
    - get: 查询单个代理股票池
    - update: 更新代理股票池
    - delete: 删除代理股票池
    - delete_by_agent: 按代理名称删除所有关联
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化数据代理股票池 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def create(
        self,
        agent_name: str,
        stock_codes_pool: dict[str, Any],
    ) -> dict[str, Any]:
        """创建代理股票池关联。

        :param agent_name: 代理名称
        :param stock_codes_pool: 股票池-代码映射字典
        :return: 创建结果字典
        """
        body: dict[str, Any] = {
            "agent_name": agent_name,
            "stock_codes_pool": stock_codes_pool,
        }
        result: Any = self._client.post(
            "/api/data_agent_pool/stocks/create", body
        )
        return dict(result) if result else {}

    def list(
        self,
        agent_name: str = "",
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """查询代理股票池列表。

        :param agent_name: 代理名称筛选（可选）
        :param skip: 分页偏移（可选）
        :param limit: 分页大小（可选，0 表示不限制）
        :return: 代理股票池列表
        """
        params: dict[str, Any] = {}
        if agent_name:
            params["agent_name"] = agent_name
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit
        result: Any = self._client.get(
            "/api/data_agent_pool/stocks/list",
            params if params else None,
        )
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    def get(self, id: str) -> dict[str, Any]:
        """查询单个代理股票池。

        :param id: 关联 ID
        :return: 关联信息字典
        """
        result: Any = self._client.get(f"/api/data_agent_pool/stocks/{id}")
        return dict(result) if result else {}

    def update(
        self,
        id: str,
        agent_name: str | None = None,
        stock_codes_pool: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """更新代理股票池关联。

        :param id: 关联 ID
        :param agent_name: 代理名称（可选，None 表示不修改）
        :param stock_codes_pool: 股票池-代码映射字典（可选，None 表示不修改）
        :return: 更新后的关联信息字典
        """
        body: dict[str, Any] = {}
        if agent_name is not None:
            body["agent_name"] = agent_name
        if stock_codes_pool is not None:
            body["stock_codes_pool"] = stock_codes_pool
        result: Any = self._client.put(
            f"/api/data_agent_pool/stocks/{id}", body
        )
        return dict(result) if result else {}

    def delete(self, id: str) -> str:
        """删除代理股票池关联。

        :param id: 关联 ID
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(f"/api/data_agent_pool/stocks/{id}")
        return str(result)

    def delete_by_agent(self, agent_name: str) -> str:
        """按代理名称删除所有股票池关联。

        :param agent_name: 代理名称
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(
            f"/api/data_agent_pool/stocks/agent/{agent_name}"
        )
        return str(result)
