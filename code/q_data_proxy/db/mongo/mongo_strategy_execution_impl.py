"""
Author: liguoqiang
Date: 2026-06-25 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-07-13 00:00:00
Description: 策略执行结果表 MongoDB 操作实现
"""

# coding="utf8"

import logging
from typing import Any

from app_context import AppContext


class MongoStrategyExecutionImpl:
    """策略执行结果表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.execution_table = self.mongo_exec.db["strategy_execution_tbl"]

    def save_execution(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """保存策略执行结果（插入新记录，user_strategy_id 必须唯一）

        :param data: 执行结果数据字典
        :return: 成功返回 True 和记录 ID，已存在返回 False 和错误信息
        """
        try:
            data = data.copy()
            if "id" in data:
                del data["id"]
            user_strategy_id = data.get("user_strategy_id", "")
            if not user_strategy_id:
                self.logger.error("user_strategy_id is empty.")
                return False, "user_strategy_id 为空"
            result = self.execution_table.insert_one(data)
            return True, str(result.inserted_id)
        except Exception as e:
            self.logger.error(f"保存策略执行结果失败: {e}")
            return False, None

    def upsert_execution(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """插入或更新策略执行结果（按 user_strategy_id 唯一）

        :param data: 执行结果数据字典
        :return: 成功返回 True 和记录 ID，否则返回 False 和 None
        """
        try:
            data = data.copy()
            user_strategy_id = data.get("user_strategy_id", "")
            if not user_strategy_id:
                self.logger.error("user_strategy_id is empty.")
                return False, None
            if "id" in data:
                del data["id"]
            filter: dict[str, Any] = {"user_strategy_id": user_strategy_id}
            result = self.execution_table.update_one(filter, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None
        except Exception as e:
            self.logger.error(f"upsert 策略执行结果失败: {e}")
            return False, None

    def query_execution_by_user_strategy(
        self, user_strategy_id: str
    ) -> tuple[bool, Any | None]:
        """按用户策略 ID 查询执行结果

        :param user_strategy_id: 用户策略 ID
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.execution_table,
                condition={"user_strategy_id": user_strategy_id},
                sort={"update_time": -1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"查询策略执行结果失败: {e}")
            return False, None

    def query_latest_execution_by_user_strategy(
        self, user_strategy_id: str
    ) -> tuple[bool, Any | None]:
        """按用户策略 ID 查询最近一次执行结果（按 update_time 倒序取 1 条）

        :param user_strategy_id: 用户策略 ID
        :return: 成功返回 True 和记录列表（最多 1 条），否则返回 False 和 None
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.execution_table,
                condition={"user_strategy_id": user_strategy_id},
                sort={"update_time": -1},
                skip=None,
                limit=1,
            )
        except Exception as e:
            self.logger.error(f"查询最近一次策略执行结果失败: {e}")
            return False, None

    def query_executions_by_user_strategy_ids(
        self, user_strategy_ids: list[str]
    ) -> tuple[bool, Any | None]:
        """按用户策略 ID 列表批量查询执行结果

        :param user_strategy_ids: 用户策略 ID 列表
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            if not user_strategy_ids:
                return True, []
            return self.mongo_exec.query_by_condition(
                table=self.execution_table,
                condition={"user_strategy_id": {"$in": user_strategy_ids}},
                sort={"update_time": -1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"批量查询策略执行结果失败: {e}")
            return False, None

    def delete_execution_by_user_strategy(self, user_strategy_id: str) -> bool:
        """删除策略执行结果

        :param user_strategy_id: 用户策略 ID
        :return: 成功返回 True，否则返回 False
        """
        try:
            return self.mongo_exec.delete(
                table=self.execution_table,
                condition={"user_strategy_id": user_strategy_id},
            )
        except Exception as e:
            self.logger.error(f"删除策略执行结果失败: {e}")
            return False
