"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-08-06 10:00:00
Description: 数据代理对象 
"""

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WorkFlowServiceDao:
    """工作流处理流程的微服务配置"""
    id: str
    service_name: str  # 代理名称
    description: str  # 代理描述
    is_online: bool  # 是否在线
    online_time: str  # 上线时间

    def __init__(
        self,
        id: str = "",
        service_name: str = "",
        description: str = "",
        is_online: bool = False,
        online_time: str = "",
    ) -> None:
        """初始化数据代理对象。"""
        self.id = id
        self.service_name = service_name
        self.description = description
        self.is_online = is_online
        self.online_time = online_time

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库字典填充对象字段，兼容缺失值和空字符串。"""
        self.id = str(data.get("_id", ""))
        self.service_name = data.get("service_name", "")
        self.description = data.get("description", "")
        self.is_online = data.get("is_online", False)
        self.online_time = data.get("online_time", "")

    def to_db(self) -> dict[str, Any]:
        """转换为 MongoDB 存储字典（排除 id，由 MongoDB 自动管理 _id）。"""
        return {
            "service_name": self.service_name,
            "description": self.description,
            "is_online": self.is_online,
            "online_time": self.online_time,
        }
