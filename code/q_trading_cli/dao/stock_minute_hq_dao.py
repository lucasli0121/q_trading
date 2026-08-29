"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-06-21 20:00:00
Description: 分频行情数据对象 - 每分钟行情快照，结构与实时行情对齐
"""

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StockMinuteHqDao:
    """分频行情数据对象，记录每分钟的行情快照。"""
    id: str
    code: str
    name: str
    price: float
    change_percent: float  # 涨跌幅
    change_amount: float  # 涨跌额
    volume: int  # 成交量
    amount: float  # 成交额
    amp: float  # 振幅
    high: float  # 最高价
    low: float  # 最低价
    open: float  # 今开价
    close: float # 分钟收盘价
    preclose: float  # 昨收价
    qrr: float  # 量比
    turnover: float  # 换手率
    minute_time: str  # 分钟时间（格式 YYYY-MM-DD HH:MM:00）
    create_time: str  # 数据创建时间

    def __init__(self, id: str = "", name: str = "", code: str = "") -> None:
        self.id = id
        self.name = name
        self.code = code

    def from_db(self, data: dict[str, Any]) -> None:
        self.id = str(data.get("_id", ""))
        self.code = data.get("code", "")
        self.name = data.get("name", "")
        self.price = data.get("price", 0.0)
        self.change_percent = data.get("change_percent", 0.0)
        self.change_amount = data.get("change_amount", 0.0)
        self.volume = data.get("volume", 0)
        self.amount = data.get("amount", 0.0)
        self.amp = data.get("amp", 0.0)
        self.high = data.get("high", 0.0)
        self.low = data.get("low", 0.0)
        self.open = data.get("open", 0.0)
        self.close = data.get("close", 0.0)
        self.preclose = data.get("preclose", 0.0)
        self.qrr = data.get("qrr", 0.0)
        self.turnover = data.get("turnover", 0.0)
        self.minute_time = data.get("minute_time", "")
        self.create_time = data.get("create_time", "")

    def to_db(self) -> dict[str, Any]:
        return self.__dict__
