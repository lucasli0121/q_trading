"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-07-13 00:00:00
Description: 策略运行记录数据对象
"""

from dataclasses import dataclass
import logging
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StrategyRunLogDao:
    """策略运行记录数据对象，通过 user_strategy_id 关联 UserStrategyDao"""

    id: str
    user_strategy_id: str
    log_content: str
    level: str  # INFO/WARNING/ERROR
    create_time: str

    def __init__(self, id: str = "", user_strategy_id: str = "") -> None:
        """初始化策略运行记录对象

        :param id: 记录 ID（MongoDB _id）
        :param user_strategy_id: 关联的用户策略 ID
        """
        self.id = id
        self.user_strategy_id = user_strategy_id
        self.log_content = ""
        self.level = "INFO"
        self.create_time = ""

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库记录填充对象字段

        :param data: MongoDB 文档字典
        """
        self.id = str(data.get("_id", ""))
        self.user_strategy_id = data.get("user_strategy_id", "")
        self.log_content = data.get("log_content", "")
        self.level = data.get("level", "INFO")
        self.create_time = data.get("create_time", "")

    def to_db(self) -> dict[str, Any]:
        """将对象转换为数据库可存储的字典

        :return: 数据库文档字典
        """
        return {
            "user_strategy_id": self.user_strategy_id,
            "log_content": self.log_content,
            "level": self.level,
            "create_time": self.create_time,
        }
