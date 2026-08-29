"""
Author: liguoqiang
Date: 2026-07-02 00:00:00
LastEditors: liguoqiang
LastEditTime: 2026-07-02 00:00:00
Description: 订单数据对象 - 包含策略 ID、股票代码、委托数量、交易价格、交易数量、状态和创建时间
"""

from dataclasses import dataclass
import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class OrderStatus(str, Enum):
    """订单状态枚举"""

    ENTRUST = "委托"
    SUCCESS = "成功"
    FAILED = "失败"
    CANCELLED = "撤单"


@dataclass
class OrderDao:
    """订单数据对象，支持从数据库记录反序列化与转换为数据库存储格式。"""

    id: str
    user_strategy_id: str
    stock_code: str
    entrust_quantity: int
    trade_price: float
    trade_quantity: int
    position_price: float
    profit_rate: float
    profit_amount: float
    commission_fee: float
    status: str
    create_time: str
    action: str

    def __init__(
        self,
        id: str = "",
        user_strategy_id: str = "",
        stock_code: str = "",
        entrust_quantity: int = 0,
        trade_price: float = 0.0,
        trade_quantity: int = 0,
        position_price: float = 0.0,
        profit_rate: float = 0.0,
        profit_amount: float = 0.0,
        commission_fee: float = 0.0,
        status: str = OrderStatus.ENTRUST.value,
        create_time: str = "",
        action: str = "",
    ) -> None:
        """初始化订单对象。"""
        self.id = id
        self.user_strategy_id = user_strategy_id
        self.stock_code = stock_code
        self.entrust_quantity = entrust_quantity
        self.trade_price = trade_price
        self.trade_quantity = trade_quantity
        self.position_price = position_price
        self.profit_rate = profit_rate
        self.profit_amount = profit_amount
        self.commission_fee = commission_fee
        self.status = status
        self.create_time = create_time
        self.action = action

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库字典填充对象字段。"""
        self.id = str(data.get("_id", ""))
        self.user_strategy_id = data.get("user_strategy_id", "")
        self.stock_code = data.get("stock_code", "")
        self.entrust_quantity = int(data.get("entrust_quantity", 0) or 0)
        self.trade_price = float(data.get("trade_price", 0.0) or 0.0)
        self.trade_quantity = int(data.get("trade_quantity", 0) or 0)
        self.position_price = float(data.get("position_price", 0.0) or 0.0)
        self.profit_rate = float(data.get("profit_rate", 0.0) or 0.0)
        self.profit_amount = float(data.get("profit_amount", 0.0) or 0.0)
        self.commission_fee = float(data.get("commission_fee", 0.0) or 0.0)
        self.status = data.get("status", OrderStatus.ENTRUST.value)
        self.create_time = data.get("create_time", data.get("time", ""))
        self.action = data.get("action", "")

    def to_db(self) -> dict[str, Any]:
        """转换为 MongoDB 存储字典。"""
        return {
            "user_strategy_id": self.user_strategy_id,
            "stock_code": self.stock_code,
            "entrust_quantity": self.entrust_quantity,
            "trade_price": self.trade_price,
            "trade_quantity": self.trade_quantity,
            "position_price": self.position_price,
            "profit_rate": self.profit_rate,
            "profit_amount": self.profit_amount,
            "commission_fee": self.commission_fee,
            "status": self.status,
            "create_time": self.create_time,
            "action": self.action,
        }
