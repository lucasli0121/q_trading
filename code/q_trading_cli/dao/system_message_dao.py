#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-09-30
Description: 系统消息数据对象 — 管理员创建，广播或定向推送给用户
"""

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SystemMessageDao:
    """定义系统消息，由管理员创建，向所有用户或者指定用户推送"""

    id: str
    title: str
    message: str # 推送的消息
    create_id: str # 创建者id
    user_ids: list  # 需要推送的用户对象ids，如果为空就是所有用户
    create_time: str  # 消息创建时间
    is_read: bool  # 当前用户是否已读；已读即归入历史消息
    read_count: int  # 已读用户数

    def __init__(
        self,
        id: str = "",
        title: str = "",
        message: str = "",
        create_id: str = "",
        user_ids: list | None = None,
        create_time: str = "",
        is_read: bool = False,
        read_count: int = 0,
    ) -> None:
        
        self.id = id
        self.title = title
        self.message = message
        self.create_id = create_id
        self.user_ids = user_ids if user_ids is not None else []
        self.create_time = create_time
        self.is_read = is_read
        self.read_count = read_count

    def from_db(self, data: dict[str, Any]) -> None:
        """从接口返回记录填充对象字段

        :param data: 后端返回的消息字典
        """
        self.id = str(data.get("_id", data.get("id", "")))
        self.title = data.get("title", "")
        self.message = data.get("message", "")
        self.create_id = data.get("create_id", "")
        self.user_ids = data.get("user_ids", [])
        self.create_time = data.get("create_time", "")
        self.is_read = bool(data.get("is_read", False))
        self.read_count = int(data.get("read_count", 0) or 0)

    def to_db(self) -> dict[str, Any]:
        """将对象转换为可提交给后端的字典

        :return: 请求体字典
        """
        return {
            "title": self.title,
            "message": self.message,
            "create_id": self.create_id,
            "user_ids": self.user_ids,
            "create_time": self.create_time,
        }
