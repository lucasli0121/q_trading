from dataclasses import dataclass
import logging
from typing import Any

from utils.tools import to_float

logger = logging.getLogger(__name__)

@dataclass
class CompanyFinanceDao:
    """公司财务数据对象，支持从数据库记录反序列化与转换为数据库存储格式。"""

    id: str
    code: str
    name: str
    # 营业总收入
    total_revenue: float
    # 营业成本
    operating_cost: float
    # 净利润
    net_profit: float
    # 归母净利润
    net_profit_parent: float
    # 扣非净利润
    net_profit_excl_nonrecurring: float
    # 净利润增长率
    net_profit_growth_rate: float
    # 营业总收入增长率
    total_revenue_growth_rate: float
    #商誉
    goodwill: float
    #资产负债率
    asset_liability_ratio: float
    # 财报发布日期
    report_date: str

    def __init__(self, id: str = "", name: str = "", code: str = "") -> None:
        """初始化公司财务对象，支持仅用 id/name/code 创建实例。"""
        self.id = id
        self.name = name
        self.code = code

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库字典填充对象字段，兼容缺失值和空字符串。"""

        self.id = str(data.get("_id", data.get("id", "")))
        self.code = data.get("code", "")
        self.name = data.get("name", "")
        self.total_revenue = to_float(data.get("total_revenue", 0.0))
        self.operating_cost = to_float(data.get("operating_cost", 0.0))
        self.net_profit = to_float(data.get("net_profit", 0.0))
        self.net_profit_parent = to_float(data.get("net_profit_parent", 0.0))
        self.net_profit_excl_nonrecurring = to_float(data.get("net_profit_excl_nonrecurring", 0.0))
        self.net_profit_growth_rate = to_float(data.get("net_profit_growth_rate", 0.0))
        self.total_revenue_growth_rate = to_float(data.get("total_revenue_growth_rate", 0.0))
        self.goodwill = to_float(data.get("goodwill", 0.0))
        self.asset_liability_ratio = to_float(data.get("asset_liability_ratio", 0.0))
        self.report_date = data.get("report_date", "")

    def to_db(self) -> dict[str, Any]:
        """将对象转换为数据库可存储的字典。"""
        return self.__dict__