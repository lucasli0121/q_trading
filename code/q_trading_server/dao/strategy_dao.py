"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-07-13 00:00:00
Description: 全局策略定义数据对象 — 策略模板/类元数据，管理员管理
"""

from dataclasses import dataclass
import logging
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StrategyDao:
    """策略定义数据对象 — 全局策略模板，由管理员创建和管理"""

    id: str
    name: str
    strategy_type: str  # 策略类型，例如 "stock_selection"、"stock_trading" 等
    description: str
    class_path: str  # 策略对应的类路径，例如 "strategy.stock_selection.ma_strategy.MaStrategy"
    class_name: str  # 策略对应的类名，例如 "MaStrategy"
    default_params: dict # 策略默认参数

    def __init__(
        self,
        id: str = "",
        name: str = "",
        strategy_type: str = "",
        description: str = "",
        class_path: str = "",
        class_name: str = "",
        default_params: dict = {}
    ) -> None:
        """初始化策略定义对象

        :param id: 记录 ID（MongoDB _id）
        :param name: 策略名称
        :param strategy_type: 策略类型
        :param description: 策略描述
        :param class_path: 策略类路径
        :param class_name: 策略类名
        :param default_params: 默认参数
        """
        self.id = id
        self.name = name
        self.strategy_type = strategy_type
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
        self.strategy_type = data.get("strategy_type", "")
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
            "strategy_type": self.strategy_type,
            "description": self.description,
            "class_path": self.class_path,
            "class_name": self.class_name,
            "default_params": self.default_params
        }
