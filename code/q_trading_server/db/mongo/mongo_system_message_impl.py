"""
Author: liguoqiang
Date: 2026-08-11
LastEditors: liguoqiang
LastEditTime: 2026-09-30
Description: 系统消息表 MongoDB 操作实现 — 含用户已读标记（read_user_ids）
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

    @staticmethod
    def is_visible_to(record: dict[str, Any], user_id: str) -> bool:
        """判断一条系统消息对指定用户是否可见。

        规则：``user_ids`` 为空数组或不存在 = 广播，所有用户可见；非空时仅列表内用户可见。

        :param record: 系统消息文档
        :param user_id: 当前用户 ID
        :return: 可见返回 True
        """
        user_ids: list = record.get("user_ids") or []
        return not user_ids or user_id in user_ids

    @staticmethod
    def read_state(record: dict[str, Any], user_id: str) -> tuple[bool, int]:
        """取一条系统消息对当前用户的读状态。

        「已读」与「历史」是同一个状态：已读的消息即归入该用户的历史消息。
        已读集合存的是用户 ID 字符串，这里在内存里做归属判断而不是走 DB 过滤，
        避免字符串等值比较受 collation 影响（库中索引的 collation 并不统一）。

        :param record: 系统消息文档
        :param user_id: 当前用户 ID
        :return: (当前用户是否已读, 已读用户数)
        """
        read_user_ids: list = record.get("read_user_ids") or []
        return user_id in read_user_ids, len(read_user_ids)

    def add(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """新增系统消息

        :param data: 系统消息字典，需包含 message、create_id、create_time
        :return: 成功返回 True 和记录 ID，否则返回 False 和 None
        """
        try:
            data = dict(data)
            data.pop("id", None)
            data.setdefault("read_user_ids", [])
            return self.mongo_exec.add(table=self.table, data=data)
        except Exception as e:
            self.logger.error(f"新增系统消息失败: {e}")
            return False, None

    def mark_read(self, id: str, user_id: str) -> bool:
        """把一条系统消息标记为指定用户已读（幂等）。

        用 ``$addToSet`` 原子追加用户 ID：重复标记不会写入重复项，也不会像
        「先读整数组、再 $set 覆盖」那样在并发下互相覆盖（``MongoExec.update``
        只支持 ``$set`` 全量覆盖，做不了这一步，因此这里直接用 collection）。

        历史数据没有 ``read_user_ids`` 字段，``$addToSet`` 会自动创建该数组。

        :param id: 系统消息记录 ID
        :param user_id: 已读的用户 ID
        :return: 成功返回 True，否则返回 False
        """
        try:
            if not id or not user_id:
                return False
            condition = self._normalize_condition({"_id": id})
            ret = self.table.update_one(condition, {"$addToSet": {"read_user_ids": user_id}})
            return ret.matched_count > 0
        except Exception as e:
            self.logger.error(f"标记系统消息已读失败: {e}")
            return False

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
                # _id 唯一，最多命中一条；不传 limit 会走「未指定上限」的兜底分支并打 WARNING
                limit=1,
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
