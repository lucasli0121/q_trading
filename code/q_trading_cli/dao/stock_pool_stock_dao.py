import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StockPoolStockDao:
    """股票池对应股票关联数据对象，支持从数据库记录反序列化与转换为数据库存储格式。"""
    id: str
    pool_name: str  # 所属股票池名称（关联 StockPoolDao.name）
    code: str  # 股票代码
    add_time: str  # 加入时间

    def __init__(
        self,
        id: str = "",
        pool_name: str = "",
        code: str = "",
        add_time: str = "",
    ) -> None:
        """初始化股票池股票关联对象，支持仅用 pool_name/code 创建实例。"""
        self.id = id
        self.pool_name = pool_name
        self.code = code
        self.add_time = add_time

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库字典填充对象字段，兼容缺失值和空字符串。"""
        self.id = str(data.get("_id", ""))
        self.pool_name = data.get("pool_name", "")
        self.code = data.get("code", "")
        self.add_time = data.get("add_time", "")

    def to_db(self) -> dict[str, Any]:
        """转换为 MongoDB 存储字典（排除 id，由 MongoDB 自动管理 _id）。"""
        return {
            "pool_name": self.pool_name,
            "code": self.code,
            "add_time": self.add_time,
        }
