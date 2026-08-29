"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-06-21 20:00:00
Description: 用户数据对象 - 包含用户的账号、密码、手机、邮箱等信息
"""

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class UserDao:
    """用户数据对象，支持从数据库记录反序列化与转换为数据库存储格式。"""
    id: str
    account: str  # 用户账号（唯一标识，用于 upsert 定位）
    password: str  # 密码
    role: int # 角色（0: 管理员; 1: 普通用户）
    phone: str  # 手机号
    email: str  # 邮箱
    has_login: bool  # 是否已登录
    is_online: bool # 是否在线
    create_time: str  # 创建时间
    update_time: str  # 更新时间
    online_time: str # 上线时间

    def __init__(
        self,
        id: str = "",
        account: str = "",
        password: str = "",
        phone: str = "",
        email: str = "",
        role: int = 1,
        has_login: bool = False,
        is_online: bool = False,
        create_time: str = "",
        update_time: str = "",
        online_time: str = "",
    ) -> None:
        """初始化用户对象，支持仅用 account 创建实例。"""
        self.id = id
        self.account = account
        self.password = password
        self.phone = phone
        self.email = email
        self.role = role
        self.has_login = has_login
        self.is_online = is_online
        self.create_time = create_time
        self.update_time = update_time
        self.online_time = online_time

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库字典填充对象字段，兼容缺失值和空字符串。"""
        self.id = str(data.get("_id", ""))
        self.account = data.get("account", "")
        self.password = data.get("password", "")
        self.phone = data.get("phone", "")
        self.email = data.get("email", "")
        self.role = data.get("role", 1)
        self.has_login = data.get("has_login", False)
        self.is_online = data.get("is_online", False)
        self.create_time = data.get("create_time", "")
        self.update_time = data.get("update_time", "")
        self.online_time = data.get("online_time", "")

    def to_db(self) -> dict[str, Any]:
        """转换为 MongoDB 存储字典（排除 id，由 MongoDB 自动管理 _id）。"""
        return {
            "account": self.account,
            "password": self.password,
            "phone": self.phone,
            "email": self.email,
            "role": self.role,
            "has_login": self.has_login,
            "is_online": self.is_online,
            "create_time": self.create_time,
            "update_time": self.update_time,
            "online_time": self.online_time,
        }
