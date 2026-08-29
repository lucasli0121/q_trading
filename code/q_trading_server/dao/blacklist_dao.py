"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-06-21 20:00:00
Description: 股票黑名单数据对象 - 记录用户拉黑的股票代码
"""

from dataclasses import dataclass
import logging
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BlacklistDao:
    """股票黑名单数据对象，支持从数据库记录反序列化与转换为数据库存储格式。"""
    id: str
    user_id: str  # 用户ID（关联 UserDao.id）
    code: str  # 股票代码
    add_time: str  # 加入黑名单时间
    reason: str  # 拉黑原因，可选字段

    def __init__(
        self,
        id: str = "",
        user_id: str = "",
        code: str = "",
        add_time: str = "",
        reason: str = "",
    ) -> None:
        """初始化黑名单对象，支持仅用 user_id/code 创建实例。"""
        self.id = id
        self.user_id = user_id
        self.code = code
        self.add_time = add_time
        self.reason = reason

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库字典填充对象字段，兼容缺失值和空字符串。"""
        self.id = str(data.get("_id", ""))
        self.user_id = data.get("user_id", "")
        self.code = data.get("code", "")
        self.add_time = data.get("add_time", "")
        self.reason = data.get("reason", "")

    def to_db(self) -> dict[str, Any]:
        """转换为 MongoDB 存储字典（排除 id，由 MongoDB 自动管理 _id）。"""
        return {
            "user_id": self.user_id,
            "code": self.code,
            "add_time": self.add_time,
            "reason": self.reason,
        }
