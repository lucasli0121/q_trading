"""
Author: liguoqiang
Date: 2026-08-11
Description: 用户因子表 MongoDB 操作实现 — 每个用户对因子定义的自定义运行参数
"""

# coding="utf8"

import logging
from typing import Any

from bson import ObjectId

from app_context import AppContext


class MongoUserFactorImpl:
    """用户因子表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.table = self.mongo_exec.db["user_factor_tbl"]

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

    def insert_or_update_user_factor(
        self, data: dict[str, Any], insert_only: bool = False
    ) -> tuple[bool, str | None, str | None]:
        """插入或更新用户因子关联

        :param data: 用户因子数据字典，需包含 factor_id、user_id
        :param insert_only: 为 True 时仅执行新增，若同一用户已关联该因子则返回冲突错误
        :return: (ok, record_id_or_None, error_or_None)
                 error 可能的值: None（无错误）、"duplicate_user_factor"（重复关联）
        """
        try:
            data = data.copy()
            if "id" in data:
                del data["id"]

            factor_id = data.get("factor_id", "")
            user_id = data.get("user_id", "")
            if not factor_id or not user_id:
                self.logger.error("factor_id or user_id is empty.")
                return False, None, None

            if insert_only:
                existing = self.table.find_one({
                    "factor_id": factor_id,
                    "user_id": user_id,
                })
                if existing:
                    return False, None, "duplicate_user_factor"

            filter: dict[str, Any] = {
                "factor_id": factor_id,
                "user_id": user_id,
            }
            result = self.table.update_one(filter, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None, None
        except Exception as e:
            self.logger.error(f"插入或更新用户因子关联失败: {e}")
            return False, None, None

    def query_user_factors_by_user(self, user_id: str) -> tuple[bool, Any | None]:
        """按用户 ID 查询该用户的所有因子关联

        :param user_id: 用户 ID
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"user_id": user_id}
            return self.mongo_exec.query_by_condition(
                table=self.table,
                condition=query,
                sort={"_id": -1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按用户查询因子关联失败: {e}")
            return False, None

    def query_user_factor_by_id(self, id: str) -> tuple[bool, Any | None]:
        """按记录 ID 查询单条用户因子关联

        :param id: 记录 ID（MongoDB _id）
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.table,
                condition={"_id": ObjectId(id)},
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按 ID 查询用户因子关联失败: {e}")
            return False, None

    def query_user_factor_by_factor_and_user(
        self, factor_id: str, user_id: str
    ) -> tuple[bool, Any | None]:
        """按因子 ID 和用户 ID 查询唯一关联记录

        :param factor_id: 全局因子 ID
        :param user_id: 用户 ID
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {
                "factor_id": factor_id,
                "user_id": user_id,
            }
            return self.mongo_exec.query_by_condition(
                table=self.table,
                condition=query,
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按因子和用户查询关联失败: {e}")
            return False, None

    def query_all_user_factors(self, skip: int = 0, limit: int = 0) -> tuple[bool, Any | None]:
        """查询所有用户因子关联（管理员用）

        :param skip: 分页跳过数
        :param limit: 分页限制数
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.table,
                condition={},
                sort={"_id": -1},
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            self.logger.error(f"查询所有用户因子关联失败: {e}")
            return False, None

    def update_user_factor(self, id: str, data: dict[str, Any]) -> bool:
        """更新用户因子关联的指定字段

        :param id: 记录 ID（MongoDB _id）
        :param data: 需要更新的字段字典（如 factor_params）
        :return: 成功返回 True，否则返回 False
        """
        try:
            if "id" in data:
                del data["id"]
            condition = self._normalize_condition({"_id": id})
            return self.mongo_exec.update(table=self.table, data=data, condition=condition)
        except Exception as e:
            self.logger.error(f"更新用户因子关联失败: {e}")
            return False

    def delete_user_factor(self, id: str) -> bool:
        """按记录 ID 删除用户因子关联

        :param id: 记录 ID（MongoDB _id）
        :return: 成功返回 True，否则返回 False
        """
        try:
            return self.mongo_exec.delete(
                table=self.table,
                condition={"_id": ObjectId(id)},
            )
        except Exception as e:
            self.logger.error(f"删除用户因子关联失败: {e}")
            return False

    def delete_user_factors_by_user(self, user_id: str) -> bool:
        """按用户 ID 删除该用户的所有因子关联（用于账号注销级联删除）

        :param user_id: 用户 ID
        :return: 成功返回 True，否则返回 False
        """
        try:
            return self.mongo_exec.delete(
                table=self.table,
                condition={"user_id": user_id},
            )
        except Exception as e:
            self.logger.error(f"按用户删除因子关联失败: {e}")
            return False
