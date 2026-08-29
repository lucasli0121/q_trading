import logging
from dataclasses import dataclass
from typing import Any

from utils.tools import to_float

logger = logging.getLogger(__name__)

@dataclass
class CompanyValuationDao:
    """公司估值数据对象，支持从数据库记录反序列化与转换为数据库存储格式。"""

    id: str
    code: str
    name: str
    # 总市值
    total_market_cap: float
    # 流通市值
    flow_market_cap: float
    # 总股本
    total_shares: float
    # 流通股本
    flow_shares: float
    # ttm市盈率
    ttm_pe: float
    # 静态市盈率
    pe: float
    # 市净率
    pb: float
    # peg值
    peg: float
    # 市现率
    pc: float
    # 市销率
    ps: float
    # 日期
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
        self.total_market_cap = to_float(data.get("total_market_cap", 0.0))
        self.flow_market_cap = to_float(data.get("flow_market_cap", 0.0))
        self.total_shares = to_float(data.get("total_shares", 0.0))
        self.flow_shares = to_float(data.get("flow_shares", 0.0))
        self.ttm_pe = to_float(data.get("ttm_pe", 0.0))
        self.pe = to_float(data.get("pe", 0.0))
        self.pb = to_float(data.get("pb", 0.0))
        self.peg = to_float(data.get("peg", 0.0))
        self.pc = to_float(data.get("pc", 0.0))
        self.ps = to_float(data.get("ps", 0.0))
        self.report_date = data.get("report_date", "")

    def to_db(self) -> dict[str, Any]:
        """将对象转换为数据库可存储的字典。"""
        return self.__dict__