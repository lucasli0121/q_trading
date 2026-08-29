#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-07-13 00:00:00
Description: 全局因子定义数据对象 — 类元数据，管理员管理
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

    def __init__(
        self,
        id: str = "",
        title: str = "",
        message: str = "",
        create_id: str = "",
        user_ids: list | None = None,
        create_time: str = "",
    ) -> None:
        
        self.id = id
        self.title = title
        self.message = message
        self.create_id = create_id
        self.user_ids = user_ids if user_ids is not None else []
        self.create_time = create_time

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库记录填充对象字段

        :param data: MongoDB 文档字典
        """
        self.id = str(data.get("_id", ""))
        self.title = data.get("title", "")
        self.message = data.get("message", "")
        self.create_id = data.get("create_id", "")
        self.user_ids = data.get("user_ids", [])
        self.create_time = data.get("create_time", "")

    def to_db(self) -> dict[str, Any]:
        """将对象转换为数据库可存储的字典

        :return: 数据库文档字典
        """
        return {
            "title": self.title,
            "message": self.message,
            "create_id": self.create_id,
            "user_ids": self.user_ids,
            "create_time": self.create_time,
        }
