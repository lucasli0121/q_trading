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
class FactorDao:
    """定义因子数据对象 — 由管理员创建和管理"""

    id: str
    name: str
    description: str
    class_path: str  # 因子对应的类路径，例如 "factor"
    class_name: str  # 因子对应的类名，例如 "CloseReboundFactor"
    default_params: dict # 默认参数

    def __init__(
        self,
        id: str = "",
        name: str = "",
        description: str = "",
        class_path: str = "",
        class_name: str = "",
        default_params: dict = {}
    ) -> None:
        """初始化因子定义对象

        :param id: 记录 ID（MongoDB _id）
        :param name: 名称
        :param description: 描述
        :param class_path: 类路径
        :param class_name: 类名
        :param default_params: 默认参数
        """
        self.id = id
        self.name = name
        self.description = description
        self.class_path = class_path
        self.class_name = class_name
        self.default_params = default_params

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库记录填充对象字段

        :param data: MongoDB 文档字典
        """
        self.id = str(data.get("_id", ""))
        self.name = data.get("name", "")
        self.description = data.get("description", "")
        self.class_path = data.get("class_path", "")
        self.class_name = data.get("class_name", "")
        self.default_params = data.get("default_params", {})

    def to_db(self) -> dict[str, Any]:
        """将对象转换为数据库可存储的字典

        :return: 数据库文档字典
        """
        return {
            "name": self.name,
            "description": self.description,
            "class_path": self.class_path,
            "class_name": self.class_name,
            "default_params": self.default_params
        }
