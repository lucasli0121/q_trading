"""
Author: liguoqiang
Date: 2026-08-03
Description: 用户偏好 API — 封装用户喜好的查询与更新接口。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class PreferenceApi:
    """用户偏好 API 客户端。

    封装 /api/user/preference 接口：
    - get: 查询当前用户的偏好设置
    - update: 更新当前用户的偏好设置
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化用户偏好 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def get(self) -> dict[str, Any]:
        """查询当前用户的偏好设置。

        :return: 用户偏好字典，无记录时返回空字典
        """
        result: Any = self._client.get("/api/user/preference")
        return dict(result) if result else {}

    def update(self, preference: dict[str, Any]) -> dict[str, Any]:
        """更新当前用户的偏好设置（全量替换）。

        :param preference: 用户偏好字典，字段见 UserPreferenceDao
        :return: 更新后的偏好字典
        """
        result: Any = self._client.put("/api/user/preference", preference)
        return dict(result) if result else {}
