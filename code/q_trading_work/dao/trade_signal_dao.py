"""
Author: liguoqiang
Date: 2026-07-02 00:00:00
LastEditors: liguoqiang
LastEditTime: 2026-07-02 00:00:00
Description: 交易信号数据对象 - 包含策略 ID、股票代码、交易价格、收益率、买卖方向和创建时间
"""

from dataclasses import dataclass
import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ActionStatus(str, Enum):
    """信号状态"""

    BUY = "买入"
    SELL = "卖出"
    


@dataclass
class TradeSignalDao:
    """交易信号数据对象，支持从数据库记录反序列化与转换为数据库存储格式。"""

    id: str
    strategy_id: str
    stock_code: str
    trade_price: float
    profit_rate: float
    profit_amount: float
    action: str
    reason: str # 买卖信号原因
    create_time: str

    def __init__(
        self,
        id: str = "",
        strategy_id: str = "",
        stock_code: str = "",
        trade_price: float = 0.0,
        profit_rate: float = 0.0,
        profit_amount: float = 0.0,
        action: str = "",
        reason: str = "",
        create_time: str = ""
    ) -> None:
        """初始化信号对象。"""
        self.id = id
        self.strategy_id = strategy_id
        self.stock_code = stock_code
        self.trade_price = trade_price
        self.profit_rate = profit_rate
        self.profit_amount = profit_amount
        self.action = action
        self.reason = reason
        self.create_time = create_time

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库字典填充对象字段。"""
        self.id = str(data.get("_id", ""))
        self.strategy_id = str(data.get("strategy_id", ""))
        self.stock_code = str(data.get("stock_code", ""))
        self.trade_price = float(data.get("trade_price", 0.0) or 0.0)
        self.profit_rate = float(data.get("profit_rate", 0.0) or 0.0)
        self.profit_amount = float(data.get("profit_amount", 0.0) or 0.0)
        self.action = str(data.get("action", ""))
        self.reason = str(data.get("reason", ""))
        self.create_time = str(data.get("create_time", ""))

    def to_db(self) -> dict[str, Any]:
        """转换为 MongoDB 存储字典。"""
        return {
            "strategy_id": self.strategy_id,
            "stock_code": self.stock_code,
            "trade_price": self.trade_price,
            "profit_rate": self.profit_rate,
            "profit_amount": self.profit_amount,
            "action": self.action,
            "reason": self.reason,
            "create_time": self.create_time,
        }
