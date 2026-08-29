# coding="utf8"

"""
Author: liguoqiang
Date: 2026-07-23 00:00:00
LastEditors: liguoqiang
LastEditTime: 2026-07-23 00:00:00
Description: 策略选股表 MongoDB 操作实现
"""

import logging
from typing import Any

from bson import ObjectId

from app_context import AppContext


class MongoStrategySelectStocksImpl:
    """策略选股表 MongoDB 操作实现"""

    def __init__(self) -> None:
        """初始化"""
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.strategy_select_stocks_table = self.mongo_exec.db["strategy_select_stocks_tbl"]

    @staticmethod
    def _build_time_filter(query: dict[str, Any], start_time: str, end_time: str) -> None:
        """向 query 中添加 create_time 时间范围过滤

        :param query: 查询条件字典（原地修改）
        :param start_time: 开始时间 YYYY-MM-DD HH:MM:SS
        :param end_time: 结束时间 YYYY-MM-DD HH:MM:SS
        """
        if start_time:
            query["create_time"] = {"$gte": start_time}
        if end_time:
            if "create_time" in query:
                query["create_time"]["$lte"] = end_time
            else:
                query["create_time"] = {"$lte": end_time}

    def add_select_stock(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """添加一条策略选股记录

        :param data: 选股数据字典，需包含 strategy_id、code、create_time
        :return: (ok, record_id_or_None)
        """
        try:
            data = data.copy()
            if "id" in data:
                del data["id"]
            strategy_id = data.get("strategy_id", "")
            code = data.get("code", "")
            if not strategy_id or not code:
                self.logger.error("strategy_id or code is empty.")
                return False, None
            result = self.strategy_select_stocks_table.insert_one(data)
            return True, str(result.inserted_id)
        except Exception as e:
            self.logger.error(f"添加策略选股记录失败: {e}")
            return False, None

    def bulk_add_select_stocks(self, records: list[dict[str, Any]]) -> tuple[bool, list[str]]:
        """批量添加策略选股记录

        :param records: 选股数据字典列表，每条需包含 strategy_id、code、create_time
        :return: (ok, record_id_list)
        """
        try:
            if not records:
                return True, []
            docs: list[dict[str, Any]] = []
            for data in records:
                data = data.copy()
                if "id" in data:
                    del data["id"]
                if not data.get("strategy_id") or not data.get("code"):
                    self.logger.error("strategy_id or code is empty in bulk add.")
                    continue
                docs.append(data)
            if not docs:
                return False, []
            result = self.strategy_select_stocks_table.insert_many(docs)
            record_ids = [str(inserted_id) for inserted_id in result.inserted_ids]
            return True, record_ids
        except Exception as e:
            self.logger.error(f"批量添加策略选股记录失败: {e}")
            return False, []

    def delete_select_stock(
        self,
        id: str,
        start_time: str = "",
        end_time: str = "",
    ) -> bool:
        """按记录 ID 删除策略选股记录，可选 create_time 时间范围过滤

        :param id: 记录 ID（MongoDB _id）
        :param start_time: 开始时间 YYYY-MM-DD HH:MM:SS（可选）
        :param end_time: 结束时间 YYYY-MM-DD HH:MM:SS（可选）
        :return: 成功返回 True，否则返回 False
        """
        try:
            condition: dict[str, Any] = {"_id": ObjectId(id)}
            self._build_time_filter(condition, start_time, end_time)
            return self.mongo_exec.delete(
                table=self.strategy_select_stocks_table,
                condition=condition,
            )
        except Exception as e:
            self.logger.error(f"删除策略选股记录失败: {e}")
            return False

    def delete_select_stocks_by_strategy_ids(
        self,
        strategy_ids: list[str],
        start_time: str = "",
        end_time: str = "",
    ) -> bool:
        """按策略 ID 列表批量删除选股记录

        :param strategy_ids: 策略 ID 列表
        :param start_time: 开始时间 YYYY-MM-DD HH:MM:SS（可选）
        :param end_time: 结束时间 YYYY-MM-DD HH:MM:SS（可选）
        :return: 成功返回 True，否则返回 False
        """
        try:
            condition: dict[str, Any] = {}
            if len(strategy_ids) > 0:
                condition = {"strategy_id": {"$in": strategy_ids}}
            self._build_time_filter(condition, start_time, end_time)
            return self.mongo_exec.delete(
                table=self.strategy_select_stocks_table,
                condition=condition,
            )
        except Exception as e:
            self.logger.error(f"按策略 ID 列表删除选股记录失败: {e}")
            return False

    def query_select_stocks(
        self,
        strategy_id: str = "",
        start_time: str = "",
        end_time: str = "",
        skip: int = 0,
        limit: int = 0,
    ) -> tuple[bool, Any | None]:
        """按条件查询策略选股记录

        :param strategy_id: 策略 ID（可选）
        :param start_time: 开始时间 YYYY-MM-DD HH:MM:SS（可选）
        :param end_time: 结束时间 YYYY-MM-DD HH:MM:SS（可选）
        :param skip: 分页跳过条数
        :param limit: 分页限制条数，0 表示不限制
        :return: (ok, records_or_None)
        """
        try:
            query: dict[str, Any] = {}
            if strategy_id and len(strategy_id) > 0:
                query["strategy_id"] = strategy_id
            self._build_time_filter(query, start_time, end_time)
            return self.mongo_exec.query_by_condition(
                table=self.strategy_select_stocks_table,
                condition=query,
                sort={"score": 1},
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            self.logger.error(f"查询策略选股记录失败: {e}")
            return False, None

    def query_select_stocks_by_strategy_ids(
        self,
        strategy_ids: list[str],
        start_time: str = "",
        end_time: str = "",
        skip: int = 0,
        limit: int = 0,
    ) -> tuple[bool, Any | None]:
        """按策略 ID 列表查询选股记录

        :param strategy_ids: 策略 ID 列表
        :param start_time: 开始时间 YYYY-MM-DD HH:MM:SS（可选）
        :param end_time: 结束时间 YYYY-MM-DD HH:MM:SS（可选）
        :param skip: 分页跳过条数
        :param limit: 分页限制条数，0 表示不限制
        :return: (ok, records_or_None)
        """
        try:
            query: dict[str, Any] = {}
            if len(strategy_ids) > 0:
                query = {"strategy_id": {"$in": strategy_ids}}
            self._build_time_filter(query, start_time, end_time)
            return self.mongo_exec.query_by_condition(
                table=self.strategy_select_stocks_table,
                condition=query,
                sort={"score": 1},
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            self.logger.error(f"按策略 ID 列表查询选股记录失败: {e}")
            return False, None
