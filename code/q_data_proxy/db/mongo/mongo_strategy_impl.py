# coding="utf8"

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Hashable

from bson import ObjectId
from pymongo import UpdateOne
from app_context import AppContext


class MongoStrategyImpl:
    """策略表 MongoDB 操作实现 — 全局策略定义，管理员管理"""

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.strategy_table = self.mongo_exec.db["strategy_tbl"]

    def insert_or_update_strategy(
        self, data: dict[str, Any], insert_only: bool = False
    ) -> tuple[bool, str | None, str | None]:
        """插入或更新策略信息

        :param data: 策略信息字典
        :param insert_only: 为 True 时仅执行新增，若同名策略已存在则返回冲突错误
        :return: (ok, record_id_or_None, error_or_None)
                 error 可能的值: None（无错误）、"duplicate_name"（名称重复）
        """
        try:
            data = data.copy()
            name = data.get("name", "")
            if not name:
                self.logger.error("name is empty.")
                return False, None, None
            if "id" in data:
                del data["id"]

            if insert_only:
                existing = self.strategy_table.find_one({"name": name})
                if existing:
                    return False, None, "duplicate_name"

            filter = {"name": name}
            result = self.strategy_table.update_one(filter, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None, None
        except Exception as e:
            self.logger.error(f"插入或更新策略信息失败: {e}")
            return False, None, None

    def bulk_upsert_strategy(self, records: Sequence[Mapping[Hashable, Any]]) -> bool:
        """批量插入或更新策略信息
        :param records: 策略信息列表
        :return: 成功返回 True，否则返回 False
        """
        try:
            requests = []
            for data in records:
                data = dict(data)
                data = data.copy()
                if "id" in data:
                    del data["id"]
                name = data["name"]
                requests.append(
                    UpdateOne(
                        {"name": name},
                        {"$set": {k: v for k, v in data.items() if k != "id"}},
                        upsert=True,
                    )
                )
            result = self.strategy_table.bulk_write(requests, ordered=False)
            self.logger.info(
                "批量upsert策略: matched=%d, modified=%d, upserted=%d",
                result.matched_count, result.modified_count, result.upserted_count,
            )
            return result.acknowledged
        except Exception as e:
            self.logger.error(f"批量插入或更新策略信息失败: {e}")
            return False

    def query_strategy_by_name(self, name: str) -> tuple[bool, Any | None]:
        """按名称查询策略
        :param name: 策略名称
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"name": name}
            return self.mongo_exec.query_by_condition(
                table=self.strategy_table,
                condition=query,
                sort={"name": 1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"查询策略信息失败: {e}")
            return False, None

    def query_strategy_by_id(self, id: str) -> tuple[bool, Any | None]:
        """按记录 ID 查询策略
        :param id: 策略记录 ID（MongoDB _id）
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.strategy_table,
                condition={"_id": ObjectId(id)},
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按 ID 查询策略信息失败: {e}")
            return False, None

    def query_all_strategies(self) -> tuple[bool, Any | None]:
        """查询所有策略
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.strategy_table,
                condition={},
                sort={"name": 1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"查询所有策略失败: {e}")
            return False, None

    def delete_strategy(self, name: str) -> bool:
        """按名称删除策略
        :param name: 策略名称
        :return: 成功返回 True，否则返回 False
        """
        try:
            query: dict[str, Any] = {"name": name}
            result = self.mongo_exec.delete(table=self.strategy_table, condition=query)
            return result
        except Exception as e:
            self.logger.error(f"删除策略失败: {e}")
            return False
