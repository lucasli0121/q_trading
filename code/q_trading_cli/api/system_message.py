#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-08-26
LastEditors: liguoqiang
LastEditTime: 2026-09-30
Description: 系统消息 API — 封装系统消息的查询与已读标记接口（用户侧）。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class SystemMessageApi:
    """系统消息 API 客户端。

    封装 /api/system_message/* 接口：
    - user_messages: 查询当前用户可收到的系统消息（广播 + 定向推送），每条带 is_read
    - mark_read: 把单条消息标记为当前用户已读（幂等）
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化系统消息 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def user_messages(self) -> list[dict[str, Any]]:
        """查询当前用户可收到的系统消息（广播 + 定向推送）。

        :return: 系统消息列表，每条包含 id/title/message/create_time/is_read 等字段
        """
        result: Any = self._client.get("/api/system_message/user_messages")
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    def mark_read(self, message_id: str) -> None:
        """把一条系统消息标记为已读。

        用户点开单条消息时调用。后端为幂等写入（$addToSet），重复调用不会出错；
        请求异常由调用方处理（与本模块其他接口保持一致，不在 API 层吞异常）。

        :param message_id: 系统消息 ID；为空时直接返回
        """
        if not message_id:
            return
        self._client.post("/api/system_message/read", json_data={"id": message_id})
