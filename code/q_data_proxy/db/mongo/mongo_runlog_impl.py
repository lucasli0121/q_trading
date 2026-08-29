"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-07-13 00:00:00
Description: 策略运行记录表 MongoDB 操作实现
"""

# coding="utf8"

import logging
from typing import Any

from app_context import AppContext


class MongoRunLogImpl:
    """策略运行记录表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.runlog_table = self.mongo_exec.db["runlog_tbl"]

    def save_runlog(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """保存运行记录
        :param data: 运行记录，需包含 user_strategy_id、log_content、level
        :return: 成功返回 True 和记录 ID
        """
        try:
            data = data.copy()
            if "id" in data:
                del data["id"]
            result = self.runlog_table.insert_one(data)
            return True, str(result.inserted_id)
        except Exception as e:
            self.logger.error(f"保存运行记录失败: {e}")
            return False, None

    def query_runlog_by_user_strategy(
        self, user_strategy_id: str, skip: int = 0, limit: int = 100
    ) -> tuple[bool, Any | None]:
        """查询用户策略的运行记录
        :param user_strategy_id: 用户策略 ID
        :param skip: 分页偏移
        :param limit: 分页大小
        :return: 成功返回 True 和记录列表
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.runlog_table,
                condition={"user_strategy_id": user_strategy_id},
                sort={"create_time": -1},
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            self.logger.error(f"查询运行记录失败: {e}")
            return False, None
