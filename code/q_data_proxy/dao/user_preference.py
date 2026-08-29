"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-06-21 20:00:00
Description: 用户偏好数据对象 - 包含界面主题、消息推送等偏好设置
"""

from dataclasses import dataclass
import logging
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class UserPreferenceDao:
    """用户偏好数据对象，支持从数据库记录反序列化与转换为数据库存储格式。"""

    id: str
    user_id: str  # 用户id
    theme_mode: str  # 界面模式， dark, light
    enable_wx_push: bool  # 是否企业微信消息推送
    wx_push_url: str  # 企业微信推送链接
    enable_phone_text: bool  # 是否手机短信推送
    phone: str  # 推送手机
    update_time: str  # 更新时间

    def __init__(
        self,
        id: str = "",
        user_id: str = "",
        theme_mode: str = "light",
        enable_wx_push: bool = False,
        wx_push_url: str = "",
        enable_phone_text: bool = False,
        phone: str = "",
        update_time: str = "",
    ) -> None:
        """初始化用户偏好对象。"""
        self.id = id
        self.user_id = user_id
        self.theme_mode = theme_mode
        self.enable_wx_push = enable_wx_push
        self.wx_push_url = wx_push_url
        self.enable_phone_text = enable_phone_text
        self.phone = phone
        self.update_time = update_time

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库字典填充对象字段，兼容缺失值和空字符串。"""
        self.id = str(data.get("_id", ""))
        self.user_id = data.get("user_id", "")
        self.theme_mode = data.get("theme_mode", "light")
        self.enable_wx_push = data.get("enable_wx_push", False)
        self.wx_push_url = data.get("wx_push_url", "")
        self.enable_phone_text = data.get("enable_phone_text", False)
        self.phone = data.get("phone", "")
        self.update_time = data.get("update_time", "")

    def to_db(self) -> dict[str, Any]:
        """转换为 MongoDB 存储字典（排除 id，由 MongoDB 自动管理 _id）。"""
        return {
            "user_id": self.user_id,
            "theme_mode": self.theme_mode,
            "enable_wx_push": self.enable_wx_push,
            "wx_push_url": self.wx_push_url,
            "enable_phone_text": self.enable_phone_text,
            "phone": self.phone,
            "update_time": self.update_time,
        }
