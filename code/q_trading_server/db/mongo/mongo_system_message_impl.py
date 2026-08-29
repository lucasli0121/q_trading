"""
Author: liguoqiang
Date: 2026-08-11
LastEditors: liguoqiang
LastEditTime: 2026-08-11
Description: 系统消息表 MongoDB 操作实现
"""

# coding="utf8"

import logging
from typing import Any

from bson import ObjectId

from app_context import AppContext


class MongoSystemMessageImpl:
    """系统消息表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.table = self.mongo_exec.db["system_message_tbl"]

    @staticmethod
    def _normalize_condition(condition: dict[str, Any]) -> dict[str, Any]:
        """规范化条件，将 id 字符串转换为 MongoDB ObjectId。"""
        condition = dict(condition or {})
        if "id" in condition:
            condition["_id"] = ObjectId(condition["id"])
            del condition["id"]
        elif "_id" in condition and isinstance(condition["_id"], str):
            condition["_id"] = ObjectId(condition["_id"])
        return condition

    def add(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """新增系统消息

        :param data: 系统消息字典，需包含 message、create_id、create_time
        :return: 成功返回 True 和记录 ID，否则返回 False 和 None
        """
        try:
            data = dict(data)
            data.pop("id", None)
            return self.mongo_exec.add(table=self.table, data=data)
        except Exception as e:
            self.logger.error(f"新增系统消息失败: {e}")
            return False, None

    def update(self, id: str, data: dict[str, Any]) -> bool:
        """更新系统消息

        :param id: 记录 ID
        :param data: 待更新的字段字典
        :return: 成功返回 True，否则返回 False
        """
        try:
            if "id" in data:
                del data["id"]
            condition = self._normalize_condition({"_id": id})
            return self.mongo_exec.update(table=self.table, data=data, condition=condition)
        except Exception as e:
            self.logger.error(f"更新系统消息失败: {e}")
            return False

    def delete(self, id: str) -> bool:
        """根据 ID 删除系统消息

        :param id: 记录 ID
        :return: 成功返回 True，否则返回 False
        """
        try:
            condition = self._normalize_condition({"_id": id})
            return self.mongo_exec.delete(table=self.table, condition=condition)
        except Exception as e:
            self.logger.error(f"删除系统消息失败: {e}")
            return False

    def query_by_id(self, id: str) -> tuple[bool, Any | None]:
        """根据 ID 查询系统消息

        :param id: 记录 ID
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            condition = self._normalize_condition({"_id": id})
            return self.mongo_exec.query_by_condition(
                table=self.table,
                condition=condition,
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按 ID 查询系统消息失败: {e}")
            return False, None

    def query_by_create_id(self, create_id: str) -> tuple[bool, Any | None]:
        """根据创建者 ID 查询系统消息列表

        :param create_id: 创建者用户 ID
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"create_id": create_id}
            return self.mongo_exec.query_by_condition(
                table=self.table,
                condition=query,
                sort={"create_time": -1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按创建者 ID 查询系统消息失败: {e}")
            return False, None

    def query_user_messages(self, user_id: str) -> tuple[bool, Any | None]:
        """查询指定用户可收到的系统消息（广播 + 定向推送）

        - user_ids 为空数组或不存在：广播消息，所有用户可见
        - user_ids 包含 user_id：定向推送给该用户

        :param user_id: 当前用户 ID
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {
                "$or": [
                    {"user_ids": {"$size": 0}},
                    {"user_ids": {"$exists": False}},
                    {"user_ids": user_id},
                ]
            }
            return self.mongo_exec.query_by_condition(
                table=self.table,
                condition=query,
                sort={"create_time": -1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"查询用户系统消息失败: {e}")
            return False, None

    def query_all(self, skip: int = 0, limit: int = 0) -> tuple[bool, Any | None]:
        """查询全部系统消息（管理员用）

        :param skip: 分页跳过数
        :param limit: 分页限制数
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.table,
                condition={},
                sort={"create_time": -1},
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            self.logger.error(f"查询全部系统消息失败: {e}")
            return False, None

    def delete_all(self) -> bool:
        """删除全部系统消息（管理员用）

        :return: 成功返回 True，否则返回 False
        """
        try:
            return self.mongo_exec.delete(table=self.table, condition={})
        except Exception as e:
            self.logger.error(f"删除全部系统消息失败: {e}")
            return False
