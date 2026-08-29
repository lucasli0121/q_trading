import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

@dataclass
class StockHisHqDao:
    id: str
    code: str
    name: str
    change_percent: float # 涨跌幅
    change_amount: float # 涨跌额
    volume: int # 成交量
    amount: float # 成交额
    # 振幅
    amp: float
    # 最高价
    high: float
    # 最低价
    low: float
    # 今开价
    open: float
    close: float
    # 昨收价
    preclose: float
    # 换手率
    turnover: float
    #持仓量
    open_interest: float
    # 数据创建时间
    create_time: str

    def __init__(self, id: str = "", name: str = "", code: str = "") -> None:
        self.id = id
        self.name = name
        self.code = code

    def from_db(self, data: dict[str, Any]) -> None:
        self.id = str(data.get("_id", ""))
        self.code = data.get("code", "")
        self.name = data.get("name", "")
        self.change_percent = data.get("change_percent", 0.0)
        self.change_amount = data.get("change_amount", 0.0)
        self.volume = data.get("volume", 0)
        self.amount = data.get("amount", 0.0)
        self.amp = data.get("amp", 0.0)
        self.high = data.get("high", 0.0)
        self.low = data.get("low", 0.0)
        self.preclose = data.get("preclose", 0.0)
        self.open = data.get("open", 0.0)
        self.close = data.get("close", 0.0)
        self.turnover = data.get("turnover", 0.0)
        self.open_interest = data.get("open_interest", 0.0)
        self.create_time = data.get("create_time", "")

    def to_db(self) -> dict[str, Any]:
        return self.__dict__