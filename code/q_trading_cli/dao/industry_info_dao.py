import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

@dataclass
class IndustryInfoDao:
    id: str
    code: str
    name: str
    #涨跌幅
    change_percent: float
    #成交量
    volume: int
    #总成交额
    amount: float
    #净流入
    net_inflow: float
    #上涨家数
    up_count: int
    #下跌家数
    down_count: int
    #均价
    avg_price: float
    #领涨股
    leading_stock: str
    # 更新时间
    update_time: str

    def __init__(self, id: str = "", name: str = "", code: str = "", change_percent: float = 0.0, volume: int = 0, amount: float = 0.0, net_inflow: float = 0.0, up_count: int = 0, down_count: int = 0, avg_price: float = 0.0, leading_stock: str = "", update_time: str = "") -> None:
        self.id = id
        self.name = name
        self.code = code
        self.change_percent = change_percent
        self.volume = volume
        self.amount = amount
        self.net_inflow = net_inflow
        self.up_count = up_count
        self.down_count = down_count
        self.avg_price = avg_price
        self.leading_stock = leading_stock
        self.update_time = update_time

    def from_db(self, data: dict[str, Any]) -> None:
        self.id = str(data.get("_id", ""))
        self.code = data.get("code", "")
        self.name = data.get("name", "")
        self.change_percent = data.get("change_percent", 0.0)
        self.volume = data.get("volume", 0)
        self.amount = data.get("amount", 0.0)
        self.net_inflow = data.get("net_inflow", 0.0)
        self.up_count = data.get("up_count", 0)
        self.down_count = data.get("down_count", 0)
        self.avg_price = data.get("avg_price", 0.0)
        self.leading_stock = data.get("leading_stock", "")
        self.update_time = data.get("update_time", "")
        
    def to_db(self) -> dict[str, Any]:
        return self.__dict__