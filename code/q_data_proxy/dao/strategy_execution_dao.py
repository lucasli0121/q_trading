"""
Author: liguoqiang
Date: 2026-06-25 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-07-13 00:00:00
Description: 策略执行结果数据对象
"""

from dataclasses import dataclass
import logging
from typing import Any

from utils.tools import to_float

logger = logging.getLogger(__name__)


@dataclass
class PositionItem:
    """单只持仓股票信息"""

    code: str = ""  # 股票代码
    name: str = ""  # 股票名称
    quantity: int = 0  # 持仓数量（股）
    cost_price: float = 0.0  # 成本价
    current_price: float = 0.0  # 当前价
    profit_rate: float = 0.0  # 个股收益率
    profit_amount: float = 0.0  # 个股收益金额
    buy_time: str = ""  # 买入时间

    def to_dict(self) -> dict[str, Any]:
        """转换为字典

        :return: 持仓股票信息字典
        """
        return {
            "code": self.code,
            "name": self.name,
            "quantity": self.quantity,
            "cost_price": self.cost_price,
            "current_price": self.current_price,
            "profit_rate": self.profit_rate,
            "profit_amount": self.profit_amount,
            "buy_time": self.buy_time,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PositionItem":
        """从字典创建 PositionItem

        :param data: 持仓数据字典
        :return: PositionItem 实例
        """
        return cls(
            code=data.get("code", ""),
            name=data.get("name", ""),
            quantity=int(data.get("quantity", 0) or 0),
            cost_price=to_float(data.get("cost_price", 0.0)),
            current_price=to_float(data.get("current_price", 0.0)),
            profit_rate=to_float(data.get("profit_rate", 0.0)),
            profit_amount=to_float(data.get("profit_amount", 0.0)),
            buy_time=data.get("buy_time", ""),
        )


@dataclass
class StrategyExecutionDao:
    """策略执行结果数据对象，通过 user_strategy_id 关联 UserStrategyDao

    记录策略的实时执行状态，包含收益率、持仓、基准对比等信息。
    """

    id: str
    user_strategy_id: str  # 关联用户策略 ID（UserStrategyDao._id）
    current_return_rate: float  # 目前收益率（如 0.15 表示 15%）
    current_profit: float  # 目前收益金额
    annualized_return_rate: float  # 年化收益率
    benchmark_return_rate: float  # 基准收益率（如沪深300同期收益）
    positions: list[dict[str, Any]]  # 持仓情况
    initial_amount: float  # 初始金额
    remaining_cash: float  # 剩余资金
    start_date: str  # 开始日期 YYYY-MM-DD
    execution_days: int  # 执行天数
    update_time: str  # 最后更新时间

    def __init__(
        self,
        id: str = "",
        user_strategy_id: str = "",
    ) -> None:
        """初始化策略执行结果对象

        :param id: 记录 ID（MongoDB _id）
        :param user_strategy_id: 关联的用户策略 ID
        """
        self.id = id
        self.user_strategy_id = user_strategy_id
        self.current_return_rate = 0.0
        self.current_profit = 0.0
        self.annualized_return_rate = 0.0
        self.benchmark_return_rate = 0.0
        self.positions = []
        self.initial_amount = 0.0
        self.remaining_cash = 0.0
        self.start_date = ""
        self.execution_days = 0
        self.update_time = ""

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库记录填充对象字段

        :param data: MongoDB 文档字典
        """
        self.id = str(data.get("_id", ""))
        self.user_strategy_id = data.get("user_strategy_id", "")
        self.current_return_rate = to_float(data.get("current_return_rate", 0.0))
        self.current_profit = to_float(data.get("current_profit", 0.0))
        self.annualized_return_rate = to_float(data.get("annualized_return_rate", 0.0))
        self.benchmark_return_rate = to_float(data.get("benchmark_return_rate", 0.0))
        self.positions = data.get("positions", []) or []
        self.initial_amount = to_float(data.get("initial_amount", 0.0))
        self.remaining_cash = to_float(data.get("remaining_cash", 0.0))
        self.start_date = data.get("start_date", "")
        self.execution_days = int(data.get("execution_days", 0) or 0)
        self.update_time = data.get("update_time", "")

    def to_db(self) -> dict[str, Any]:
        """将对象转换为数据库可存储的字典

        :return: 数据库文档字典
        """
        return {
            "user_strategy_id": self.user_strategy_id,
            "current_return_rate": self.current_return_rate,
            "current_profit": self.current_profit,
            "annualized_return_rate": self.annualized_return_rate,
            "benchmark_return_rate": self.benchmark_return_rate,
            "positions": self.positions,
            "initial_amount": self.initial_amount,
            "remaining_cash": self.remaining_cash,
            "start_date": self.start_date,
            "execution_days": self.execution_days,
            "update_time": self.update_time,
        }
