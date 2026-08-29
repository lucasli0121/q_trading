#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-08-26
Description: 系统消息 API — 封装系统消息的查询接口（用户侧）。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class SystemMessageApi:
    """系统消息 API 客户端。

    封装 /api/system_message/* 接口：
    - user_messages: 查询当前用户可收到的系统消息（广播 + 定向推送）
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化系统消息 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def user_messages(self) -> list[dict[str, Any]]:
        """查询当前用户可收到的系统消息（广播 + 定向推送）。

        :return: 系统消息列表，每条包含 id/title/message/create_time 等字段
        """
        result: Any = self._client.get("/api/system_message/user_messages")
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]
