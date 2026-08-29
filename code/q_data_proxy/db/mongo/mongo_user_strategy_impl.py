"""
Author: liguoqiang
Date: 2026-07-13 00:00:00
LastEditors: liguoqiang
LastEditTime: 2026-07-13 00:00:00
Description: 用户策略关联表 MongoDB 操作实现
"""

# coding="utf8"

import logging
from typing import Any

from bson import ObjectId

from app_context import AppContext


class MongoUserStrategyImpl:
    """用户策略关联表 MongoDB 操作实现"""

    def __init__(self) -> None:
        """初始化"""
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.user_strategy_table = self.mongo_exec.db["user_strategy_tbl"]

    def insert_or_update_user_strategy(
        self, data: dict[str, Any], insert_only: bool = False
    ) -> tuple[bool, str | None, str | None]:
        """插入或更新用户策略关联

        :param data: 用户策略数据字典，需包含 strategy_id、user_id、status、pool_id
        :param insert_only: 为 True 时仅执行新增，若同一用户已关联该策略则返回冲突错误
        :return: (ok, record_id_or_None, error_or_None)
                 error 可能的值: None（无错误）、"duplicate_user_strategy"（重复关联）
        """
        try:
            data = data.copy()
            if "id" in data:
                del data["id"]

            strategy_id = data.get("strategy_id", "")
            user_id = data.get("user_id", "")
            if not strategy_id or not user_id:
                self.logger.error("strategy_id or user_id is empty.")
                return False, None, None

            if insert_only:
                existing = self.user_strategy_table.find_one({
                    "strategy_id": strategy_id,
                    "user_id": user_id,
                })
                if existing:
                    return False, None, "duplicate_user_strategy"

            filter: dict[str, Any] = {
                "strategy_id": strategy_id,
                "user_id": user_id,
            }
            result = self.user_strategy_table.update_one(filter, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None, None
        except Exception as e:
            self.logger.error(f"插入或更新用户策略关联失败: {e}")
            return False, None, None

    def query_all_user_strategies(self) -> tuple[bool, Any | None]:
        """查询所有用户策略关联（无需登录，公开接口）

        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.user_strategy_table,
                condition={},
                sort={"_id": -1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"查询所有用户策略关联失败: {e}")
            return False, None

    def query_user_strategies_by_user(
        self, user_id: str
    ) -> tuple[bool, Any | None]:
        """按用户 ID 查询该用户的所有策略关联

        :param user_id: 用户 ID
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"user_id": user_id}
            return self.mongo_exec.query_by_condition(
                table=self.user_strategy_table,
                condition=query,
                sort={"_id": -1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按用户查询策略关联失败: {e}")
            return False, None

    def query_user_strategy_by_id(
        self, id: str
    ) -> tuple[bool, Any | None]:
        """按记录 ID 查询单条用户策略关联

        :param id: 记录 ID（MongoDB _id）
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.user_strategy_table,
                condition={"_id": ObjectId(id)},
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按 ID 查询用户策略关联失败: {e}")
            return False, None

    def query_user_strategy_by_strategy_and_user(
        self, strategy_id: str, user_id: str
    ) -> tuple[bool, Any | None]:
        """按策略 ID 和用户 ID 查询唯一关联记录

        :param strategy_id: 全局策略 ID
        :param user_id: 用户 ID
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {
                "strategy_id": strategy_id,
                "user_id": user_id,
            }
            return self.mongo_exec.query_by_condition(
                table=self.user_strategy_table,
                condition=query,
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按策略和用户查询关联失败: {e}")
            return False, None

    def update_user_strategy(
        self, id: str, data: dict[str, Any]
    ) -> bool:
        """更新用户策略关联的指定字段

        :param id: 记录 ID（MongoDB _id）
        :param data: 需要更新的字段字典（如 status、pool_id）
        :return: 成功返回 True，否则返回 False
        """
        try:
            result = self.user_strategy_table.update_one(
                {"_id": ObjectId(id)}, {"$set": data}
            )
            if result.matched_count == 0:
                self.logger.warning(f"未找到用户策略关联: id={id}")
                return False
            return result.acknowledged
        except Exception as e:
            self.logger.error(f"更新用户策略关联失败: {e}")
            return False

    def delete_user_strategy(self, id: str) -> bool:
        """按记录 ID 删除用户策略关联

        :param id: 记录 ID（MongoDB _id）
        :return: 成功返回 True，否则返回 False
        """
        try:
            return self.mongo_exec.delete(
                table=self.user_strategy_table,
                condition={"_id": ObjectId(id)},
            )
        except Exception as e:
            self.logger.error(f"删除用户策略关联失败: {e}")
            return False

    def delete_user_strategies_by_user(self, user_id: str) -> bool:
        """按用户 ID 删除该用户的所有策略关联（用于账号注销级联删除）

        :param user_id: 用户 ID
        :return: 成功返回 True，否则返回 False
        """
        try:
            return self.mongo_exec.delete(
                table=self.user_strategy_table,
                condition={"user_id": user_id},
            )
        except Exception as e:
            self.logger.error(f"按用户删除策略关联失败: {e}")
            return False
