"""
Author: liguoqiang
Date: 2026-07-13 00:00:00
LastEditors: liguoqiang
LastEditTime: 2026-07-13 00:00:00
Description: 用户策略关联表 MongoDB 操作实现
"""

# coding="utf8"

import logging
from datetime import datetime
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

    def _is_name_duplicated(self, user_id: str, name: str, exclude_id: str | None = None) -> bool:
        """检查同一用户下是否已存在同名策略

        :param user_id: 用户 ID
        :param name: 策略名称，为空时不做唯一性校验
        :param exclude_id: 需要排除的记录 ID（更新时排除自身）
        :return: 已存在同名记录返回 True，否则返回 False
        """
        if not user_id or not name:
            return False
        condition: dict[str, Any] = {"user_id": user_id, "name": name}
        if exclude_id:
            condition["_id"] = {"$ne": ObjectId(exclude_id)}
        return self.user_strategy_table.count_documents(condition) > 0

    def insert_user_strategy(self, data: dict[str, Any]) -> tuple[bool, str | None, str | None]:
        """新增用户策略关联（允许重复新增，不做重复检测、不更新已有记录）

        同一用户下策略名称（name）需保持唯一；name 为空时不做唯一性校验。
        update_time 表示 status 最近一次变更的时间：未显式传入时取 create_time（即初始状态生效时刻）。

        :param data: 用户策略数据字典，需包含 strategy_id、user_id、status、pool_id
        :return: (ok, record_id_or_None, error_or_None)
                 error 可能的值: None（无错误）、"duplicate_name"（同一用户下名称重复）
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

            name = data.get("name", "")
            if self._is_name_duplicated(user_id, name):
                self.logger.warning(f"已存在同名用户策略: user_id={user_id}, name={name}")
                return False, None, "duplicate_name"

            # 新增即首次写入状态，update_time 与 create_time 对齐（显式传入则尊重传入值）
            if not data.get("update_time"):
                data["update_time"] = data.get("create_time") or datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

            result = self.user_strategy_table.insert_one(data)
            return True, str(result.inserted_id), None
        except Exception as e:
            self.logger.error(f"插入用户策略关联失败: {e}")
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

    def update_user_strategy(self, id: str, data: dict[str, Any]) -> tuple[bool, str | None]:
        """更新用户策略关联的指定字段

        同一用户下策略名称（name）需保持唯一（更新时排除自身）；name 为空时不做唯一性校验。
        update_time 表示 status 最近一次变更的时间：
        - 本次更新未包含 status（仅改名称/股票池等）：不改动 update_time；
        - 本次更新包含 status 且与库中原值相同：视为未发生变化，同样不改动 update_time；
        - 本次更新包含 status 且与库中原值不同：刷新为当前时间（显式传入非空 update_time 则尊重传入值）。

        :param id: 记录 ID（MongoDB _id）
        :param data: 需要更新的字段字典（如 name、status、pool_id）
        :return: (ok, error_or_None)
                 error 可能的值: None（无错误）、"duplicate_name"（同一用户下名称重复）
        """
        try:
            data = data.copy()

            # name 唯一性校验与 status 变化判定都需要原记录
            record: dict[str, Any] | None = None
            if data.get("name") or "status" in data:
                record = self.user_strategy_table.find_one({"_id": ObjectId(id)})
                if record is None:
                    self.logger.warning(f"未找到用户策略关联: id={id}")
                    return False, None

            name = data.get("name", "")
            if name and self._is_name_duplicated(
                (record or {}).get("user_id", ""), name, exclude_id=id
            ):
                self.logger.warning(f"已存在同名用户策略: id={id}, name={name}")
                return False, "duplicate_name"

            # 仅当 status 真正发生变化时才打时间戳（显式传入非空 update_time 则尊重传入值）
            if "status" in data:
                old_status = (record or {}).get("status", "")
                if data.get("status") != old_status:
                    if not data.get("update_time"):
                        data["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                else:
                    data.pop("update_time", None)

            result = self.user_strategy_table.update_one(
                {"_id": ObjectId(id)}, {"$set": data}
            )
            if result.matched_count == 0:
                self.logger.warning(f"未找到用户策略关联: id={id}")
                return False, None
            return result.acknowledged, None
        except Exception as e:
            self.logger.error(f"更新用户策略关联失败: {e}")
            return False, None

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

    def query_user_strategies_by_pool_id(
        self, pool_id: str
    ) -> tuple[bool, Any | None]:
        """按股票池 ID 查询关联的用户策略

        :param pool_id: 股票池 ID（MongoDB _id 字符串）
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"pool_id": pool_id}
            return self.mongo_exec.query_by_condition(
                table=self.user_strategy_table,
                condition=query,
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按股票池 ID 查询用户策略关联失败: {e}")
            return False, None

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
