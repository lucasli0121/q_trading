"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-06-22 13:30:00
Description: 策略回测结果表 MongoDB 操作实现
"""

# coding="utf8"

import logging
from typing import Any

from app_context import AppContext


class MongoBacktestImpl:
    """策略回测结果表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.backtest_table = self.mongo_exec.db["backtest_tbl"]

    def save_backtest(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """保存回测结果
        :param data: 回测数据，需包含 strategy_id、result_data
        :return: 成功返回 True 和记录 ID
        """
        try:
            data = data.copy()
            if "id" in data:
                del data["id"]
            result = self.backtest_table.insert_one(data)
            return True, str(result.inserted_id)
        except Exception as e:
            self.logger.error(f"保存回测结果失败: {e}")
            return False, None

    def query_backtest_by_strategy(
        self, strategy_id: str
    ) -> tuple[bool, Any | None]:
        """查询策略的回测结果
        :param strategy_id: 策略 ID
        :return: 成功返回 True 和记录列表
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.backtest_table,
                condition={"strategy_id": strategy_id},
                sort={"create_time": -1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"查询回测结果失败: {e}")
            return False, None
