"""
Author: liguoqiang
Date: 2026-08-12
Description: 数据代理 API — 封装数据代理的 CRUD 及行业股票关联接口。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class DataAgentApi:
    """数据代理 API 客户端。

    封装 /api/data_agent/* 接口：
    - create: 创建数据代理
    - list: 查询数据代理列表
    - update: 更新数据代理
    - delete: 删除数据代理
    - create_industry_stocks: 创建行业股票关联
    - get_industry_stocks: 查询行业股票关联
    - update_industry_stocks: 更新行业股票关联
    - delete_industry_stocks: 删除行业股票关联
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化数据代理 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    # ---- 数据代理 CRUD ----

    def create(
        self,
        agent_name: str,
        description: str = "",
        is_online: bool = False,
    ) -> dict[str, Any]:
        """创建数据代理。

        :param agent_name: 代理名称（唯一标识）
        :param description: 描述（可选）
        :param is_online: 是否在线，默认 False
        :return: 创建结果字典
        """
        body: dict[str, Any] = {
            "agent_name": agent_name,
            "description": description,
            "is_online": is_online,
        }
        result: Any = self._client.post("/api/data_agent/create", body)
        return dict(result) if result else {}

    def list(
        self,
        agent_name: str = "",
        is_online: bool | None = None,
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """查询数据代理列表。

        :param agent_name: 代理名称筛选（可选）
        :param is_online: 在线状态筛选（可选，None 表示不筛选）
        :param skip: 分页偏移（可选）
        :param limit: 分页大小（可选，0 表示不限制）
        :return: 数据代理列表
        """
        params: dict[str, Any] = {}
        if agent_name:
            params["agent_name"] = agent_name
        if is_online is not None:
            params["is_online"] = is_online
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit
        result: Any = self._client.get(
            "/api/data_agent/list",
            params if params else None,
        )
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    def update(
        self,
        agent_name: str,
        description: str | None = None,
        is_online: bool | None = None,
    ) -> dict[str, Any]:
        """更新数据代理（按名称查找）。

        :param agent_name: 代理名称
        :param description: 描述（可选，None 表示不修改）
        :param is_online: 是否在线（可选，None 表示不修改）
        :return: 更新后的代理信息字典
        """
        body: dict[str, Any] = {}
        if description is not None:
            body["description"] = description
        if is_online is not None:
            body["is_online"] = is_online
        result: Any = self._client.put(f"/api/data_agent/{agent_name}", body)
        return dict(result) if result else {}

    def delete(self, id: str) -> str:
        """删除数据代理（按 ID）。

        :param id: 代理 ID
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(f"/api/data_agent/{id}")
        return str(result)

    # ---- 行业股票关联 ----

    def create_industry_stocks(
        self,
        agent_name: str,
        stock_codes_industry: dict[str, Any],
    ) -> dict[str, Any]:
        """创建行业股票关联。

        :param agent_name: 代理名称
        :param stock_codes_industry: 行业-股票代码映射字典
        :return: 创建结果字典
        """
        body: dict[str, Any] = {
            "agent_name": agent_name,
            "stock_codes_industry": stock_codes_industry,
        }
        result: Any = self._client.post(
            "/api/data_agent/industry_stocks/create", body
        )
        return dict(result) if result else {}

    def get_industry_stocks(self, agent_name: str) -> dict[str, Any]:
        """按代理名称查询行业股票关联。

        :param agent_name: 代理名称
        :return: 行业股票关联字典
        """
        result: Any = self._client.get(
            f"/api/data_agent/industry_stocks/agent/{agent_name}"
        )
        return dict(result) if result else {}

    def update_industry_stocks(
        self,
        id: str,
        agent_name: str,
        stock_codes_industry: dict[str, Any],
    ) -> dict[str, Any]:
        """更新行业股票关联。

        :param id: 关联 ID
        :param agent_name: 代理名称
        :param stock_codes_industry: 行业-股票代码映射字典
        :return: 更新后的关联信息字典
        """
        body: dict[str, Any] = {
            "agent_name": agent_name,
            "stock_codes_industry": stock_codes_industry,
        }
        result: Any = self._client.put(
            f"/api/data_agent/industry_stocks/{id}", body
        )
        return dict(result) if result else {}

    def delete_industry_stocks(self, id: str) -> str:
        """删除行业股票关联。

        :param id: 关联 ID
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(
            f"/api/data_agent/industry_stocks/{id}"
        )
        return str(result)
