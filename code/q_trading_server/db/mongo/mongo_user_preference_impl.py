"""
Author: liguoqiang
Date: 2026-07-26 10:00:00
LastEditors: liguoqiang
LastEditTime: 2026-07-26 10:00:00
Description: 用户偏好表 MongoDB 操作实现
"""

# coding="utf8"

import logging
from typing import Any

from app_context import AppContext


class MongoUserPreferenceImpl:
    """用户偏好表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.preference_table = self.mongo_exec.db["user_preference_tbl"]

    def insert_or_update_preference(
        self, data: dict[str, Any]
    ) -> tuple[bool, str | None]:
        """插入或更新用户偏好设置，以 user_id 为唯一标识

        :param data: 用户偏好字典，user_id 为唯一标识
        :return: (ok, record_id_or_None)
        """
        try:
            data = data.copy()
            user_id = data.get("user_id", "")
            if not user_id:
                self.logger.error("user_id is empty.")
                return False, None
            if "id" in data:
                del data["id"]

            filter_dict: dict[str, Any] = {"user_id": user_id}
            result = self.preference_table.update_one(
                filter_dict, {"$set": data}, upsert=True
            )
            return True, str(result.upserted_id) if result.upserted_id else None
        except Exception as e:
            self.logger.error(f"插入或更新用户偏好失败: {e}")
            return False, None

    def query_preference_by_user_id(
        self, user_id: str
    ) -> tuple[bool, Any | None]:
        """按用户 ID 查询偏好设置

        :param user_id: 用户 ID 字符串
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"user_id": user_id}
            return self.mongo_exec.query_by_condition(
                table=self.preference_table,
                condition=query,
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按用户 ID 查询偏好失败: {e}")
            return False, None

    def delete_preference(self, user_id: str) -> bool:
        """按用户 ID 删除偏好设置

        :param user_id: 用户 ID
        :return: 成功返回 True，否则返回 False
        """
        try:
            query: dict[str, Any] = {"user_id": user_id}
            result = self.mongo_exec.delete(
                table=self.preference_table, condition=query
            )
            return result
        except Exception as e:
            self.logger.error(f"删除用户偏好失败: {e}")
            return False
