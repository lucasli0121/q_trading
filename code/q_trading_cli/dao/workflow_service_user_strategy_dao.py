#!/usr/bin/env python3
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
class WorkFlowServiceUserStrategyDao:
    """为工作流微服务分配user_strategy资源"""
    id: str
    service_name: str  # 微服务名称
    user_strategy_ids: list[str]

    def __init__(
        self,
        id: str = "",
        service_name: str = "",
        user_strategy_ids: list[str] | None = None,
    ) -> None:
        """初始化数据代理对象。"""
        self.id = id
        self.service_name = service_name
        self.user_strategy_ids = user_strategy_ids if user_strategy_ids is not None else []

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库字典填充对象字段，兼容缺失值和空字符串。"""
        self.id = str(data.get("_id", ""))
        self.service_name = data.get("service_name", "")
        self.user_strategy_ids = data.get("user_strategy_ids", [])

    def to_db(self) -> dict[str, Any]:
        """转换为 MongoDB 存储字典（排除 id，由 MongoDB 自动管理 _id）。"""
        return {
            "service_name": self.service_name,
            "user_strategy_ids": self.user_strategy_ids,
        }
