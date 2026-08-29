import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StockPoolDao:
    """股票池数据对象，支持从数据库记录反序列化与转换为数据库存储格式。"""
    id: str
    name: str  # 股票池名称（唯一标识，用于 upsert 定位）
    description: str  # 描述
    create_time: str  # 创建时间
    user_id: str  # 所属用户 ID（关联 UserDao.id）

    def __init__(
        self,
        id: str = "",
        name: str = "",
        description: str = "",
        create_time: str = "",
        user_id: str = "",
    ) -> None:
        """初始化股票池对象，支持仅用 name 创建实例。"""
        self.id = id
        self.name = name
        self.description = description
        self.create_time = create_time
        self.user_id = user_id

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库字典填充对象字段，兼容缺失值和空字符串。"""
        self.id = str(data.get("_id", ""))
        self.name = data.get("name", "")
        self.description = data.get("description", "")
        self.create_time = data.get("create_time", "")
        self.user_id = data.get("user_id", "")

    def to_db(self) -> dict[str, Any]:
        """转换为 MongoDB 存储字典（排除 id，由 MongoDB 自动管理 _id）。"""
        return {
            "name": self.name,
            "description": self.description,
            "create_time": self.create_time,
            "user_id": self.user_id,
        }
