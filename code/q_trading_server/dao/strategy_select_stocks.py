"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-07-23 00:00:00
Description: 策略选股数据对象 — 记录策略选中的股票，通过 strategy_id 关联 UserStrategyDao
"""

from dataclasses import dataclass
import logging
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StrategySelectStockDao:
    """策略选股数据对象 — 记录策略选中的股票"""

    id: str
    strategy_id: str
    code: str
    score: float
    create_time: str

    def __init__(
        self,
        id: str = "",
        strategy_id: str = "",
        code: str = "",
        score: float = 0.0,
        create_time: str = "",
    ) -> None:
        """初始化策略选股对象

        :param id: 记录 ID（MongoDB _id）
        :param strategy_id: 策略 ID（关联 UserStrategyDao.strategy_id）
        :param code: 股票代码
        :param create_time: 创建时间
        """
        self.id = id
        self.strategy_id = strategy_id
        self.code = code
        self.score = score
        self.create_time = create_time

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库记录填充对象字段

        :param data: MongoDB 文档字典
        """
        self.id = str(data.get("_id", ""))
        self.strategy_id = str(data.get("strategy_id", ""))
        self.code = data.get("code", "")
        self.score = float(data.get("score", 0.0))
        self.create_time = data.get("create_time", "")

    def to_db(self) -> dict[str, Any]:
        """将对象转换为数据库可存储的字典

        :return: 数据库文档字典
        """
        return {
            "strategy_id": self.strategy_id,
            "code": self.code,
            "score": self.score,
            "create_time": self.create_time,
        }
