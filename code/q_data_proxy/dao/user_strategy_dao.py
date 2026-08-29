"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-07-13 00:00:00
Description: 用户策略关联数据对象 — 将全局策略与用户关联，记录用户维度的策略状态和股票池
"""

from dataclasses import dataclass
import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class StrategyStatus(Enum):
    """策略状态枚举"""

    RUNNING = "running"  # 运行中
    STOPPED = "stopped"  # 已停止
    PAUSED = "paused"  # 已暂停
    ERROR = "error"  # 错误状态


@dataclass
class UserStrategyDao:
    """用户策略关联数据对象，通过 strategy_id 关联 StrategyDao"""

    id: str
    strategy_id: str  # 关联策略 ID（StrategyDao._id）
    status: str  # 策略状态: running / stopped / paused
    user_id: str  # 所属用户 ID（关联 UserDao.id）
    pool_id: str  # 关联股票池 ID（关联 StockPoolDao.id）
    initial_amount: float  # 初始金额
    total_profit: float  # 总收益金额
    max_stock_count: int  # 最大持仓数量
    create_time: str  # 创建时间

    def __init__(
        self,
        id: str = "",
        strategy_id: str = "",
        status: str = StrategyStatus.STOPPED.value,
        user_id: str = "",
        pool_id: str = "",
        initial_amount: float = 0.0,
        total_profit: float = 0.0,
        max_stock_count: int = 0,
        create_time: str = "",
    ) -> None:
        """初始化用户策略关联对象

        :param id: 记录 ID（MongoDB _id）
        :param strategy_id: 关联的全局策略 ID
        :param status: 策略状态
        :param user_id: 所属用户 ID
        :param pool_id: 关联股票池 ID
        :param initial_amount: 初始金额
        :param create_time: 创建时间
        """
        self.id = id
        self.strategy_id = strategy_id
        self.status = status
        self.user_id = user_id
        self.pool_id = pool_id
        self.initial_amount = initial_amount
        self.total_profit = total_profit
        self.max_stock_count = max_stock_count
        self.create_time = create_time

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库记录填充对象字段

        :param data: MongoDB 文档字典
        """
        self.id = str(data.get("_id", ""))
        self.strategy_id = data.get("strategy_id", "")
        self.status = data.get("status", StrategyStatus.STOPPED.value)
        self.user_id = data.get("user_id", "")
        self.pool_id = data.get("pool_id", "")
        self.initial_amount = float(data.get("initial_amount", 0.0) or 0.0)
        self.total_profit = float(data.get("total_profit", 0.0) or 0.0)
        self.max_stock_count = int(data.get("max_stock_count", 0) or 0)
        self.create_time = str(data.get("create_time", ""))

    def to_db(self) -> dict[str, Any]:
        """将对象转换为数据库可存储的字典

        :return: 数据库文档字典
        """
        return {
            "strategy_id": self.strategy_id,
            "status": self.status,
            "user_id": self.user_id,
            "pool_id": self.pool_id,
            "initial_amount": self.initial_amount,
            "total_profit": self.total_profit,
            "max_stock_count": self.max_stock_count,
            "create_time": self.create_time,
        }
