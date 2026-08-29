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
class UsereFactorDao:
    """定义因子数据对象 — 由管理员创建和管理"""

    id: str
    factor_id: str # factor id
    user_id: str
    factor_params: dict # factor 运行参数,为每个用户自定义
    create_time: str # 用户创建自定义时间

    def __init__(
        self,
        id: str = "",
        factor_id: str = "",
        user_id: str = "",
        factor_params: dict = {},
        create_time: str = ""
    ) -> None:
        """初始化自定义因子对象

        :param id: 记录 ID（MongoDB _id）
        :param factor_id: factor id
        :param user_id: user id
        :param factor_params: factor 运行参数
        :param create_time: 创建自定义时间
        """
        self.id = id
        self.factor_id = factor_id
        self.user_id = user_id
        self.factor_params = factor_params
        self.create_time = create_time

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库记录填充对象字段

        :param data: MongoDB 文档字典
        """
        self.id = str(data.get("_id", ""))
        self.factor_id = data.get("factor_id", "")
        self.user_id = data.get("user_id", "")
        self.factor_params = data.get("factor_params", {})
        self.create_time = data.get("create_time", "")
        

    def to_db(self) -> dict[str, Any]:
        """将对象转换为数据库可存储的字典

        :return: 数据库文档字典
        """
        return {
            "factor_id": self.factor_id,
            "user_id": self.user_id,
            "factor_params": self.factor_params,
            "create_time": self.create_time
        }
