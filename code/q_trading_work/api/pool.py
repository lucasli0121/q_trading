"""
Author: liguoqiang
Date: 2026-06-27
Description: 股票池管理 API — 创建、删除、列表、添加/剔除股票。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class PoolApi:
    """股票池管理 API 客户端。

    封装 /api/pool/* 接口：
    - create: 创建股票池
    - delete: 删除股票池
    - list: 查询用户所有股票池
    - add_stocks: 向股票池添加股票
    - remove_stocks: 从股票池剔除股票
    - get_stocks: 获取股票池中所有股票
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化股票池 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def create(self, name: str, description: str = "") -> dict[str, Any]:
        """创建股票池。

        :param name: 股票池名称
        :param description: 描述（可选）
        :return: 创建的股票池信息（PoolInfo）
        """
        body: dict[str, str] = {"name": name}
        if description:
            body["description"] = description
        result: Any = self._client.post("/api/pool/create", body)
        return dict(result)

    def delete(self, name: str) -> str:
        """删除股票池（级联删除关联股票）。

        :param name: 股票池名称
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(f"/api/pool/{name}")
        return str(result)

    def list(self) -> list[dict[str, Any]]:
        """查询用户所有股票池。

        :return: 股票池信息列表（list[PoolInfo]）
        """
        result: Any = self._client.get("/api/pool/list")
        return list(result) if result else []

    def add_stocks(self, name: str, codes: list[str]) -> str:
        """向股票池添加股票（自动过滤黑名单中的股票）。

        :param name: 股票池名称
        :param codes: 股票代码列表
        :return: 操作结果提示信息
        """
        body: dict[str, list[str]] = {"codes": codes}
        result: Any = self._client.post(f"/api/pool/{name}/stocks/add", body)
        return str(result)

    def remove_stocks(self, name: str, codes: list[str]) -> str:
        """从股票池剔除股票。

        :param name: 股票池名称
        :param codes: 股票代码列表
        :return: 操作结果提示信息
        """
        body: dict[str, list[str]] = {"codes": codes}
        result: Any = self._client.delete(f"/api/pool/{name}/stocks/remove", body)
        return str(result)

    def get_stocks(self, name: str) -> list[dict[str, Any]]:
        """获取股票池中所有股票。

        :param name: 股票池名称
        :return: 池内股票信息列表（list[PoolStockInfo]）
        """
        result: Any = self._client.get(f"/api/pool/{name}/stocks")
        return list(result) if result else []

    def get_by_id(self, pool_id: str) -> dict[str, Any] | None:
        """根据股票池 ID 查询股票池信息（通过遍历 list 结果查找）。

        :param pool_id: 股票池 ID
        :return: 股票池信息字典（含 name, description 等），未找到返回 None
        """
        pools: list[dict[str, Any]] = self.list()
        for pool in pools:
            pid: str = str(pool.get("id", pool.get("_id", "")))
            if pid == pool_id:
                return pool
        return None
