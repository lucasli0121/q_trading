"""
Author: liguoqiang
Date: 2026-06-27
Description: 黑名单管理 API — 添加、移除、列表、检查。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class BlacklistApi:
    """黑名单管理 API 客户端。

    封装 /api/blacklist/* 接口：
    - add: 添加股票到黑名单
    - remove: 从黑名单中移除股票
    - list: 查询用户的所有黑名单股票
    - check: 检查某只股票是否在黑名单中
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化黑名单 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def add(self, codes: list[str], reason: str = "") -> str:
        """添加股票到黑名单。

        :param codes: 股票代码列表
        :param reason: 拉黑原因（可选）
        :return: 操作结果提示信息
        """
        body: dict[str, Any] = {"codes": codes}
        if reason:
            body["reason"] = reason
        result: Any = self._client.post("/api/blacklist/add", body)
        return str(result)

    def remove(self, codes: list[str]) -> str:
        """从黑名单中移除股票。

        :param codes: 股票代码列表
        :return: 操作结果提示信息
        """
        body: dict[str, list[str]] = {"codes": codes}
        result: Any = self._client.post("/api/blacklist/remove", body)
        return str(result)

    def list(self) -> list[dict[str, Any]]:
        """查询用户的所有黑名单股票。

        :return: 黑名单条目列表（list[BlacklistInfo]）
        """
        result: Any = self._client.get("/api/blacklist/list")
        return list(result) if result else []

    def check(self, code: str) -> dict[str, Any]:
        """检查某只股票是否在黑名单中。

        :param code: 股票代码
        :return: 检查结果字典
        """
        result: Any = self._client.get(
            "/api/blacklist/check", {"code": code}
        )
        return dict(result) if result else {}
