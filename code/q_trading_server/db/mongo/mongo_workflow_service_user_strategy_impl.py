"""
Author: generated
Description: MongoDB 操作实现，用于 DataAgentIndustryStocks
"""

# coding="utf8"

import logging
from typing import Any

from bson import ObjectId

from app_context import AppContext


class MongoWorkFlowServiceUserStrategyImpl:
    """数据代理分配行业股票表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.table = self.mongo_exec.db["workflow_service_user_strategy_tbl"]

    @staticmethod
    def _normalize_condition(condition: dict[str, Any]) -> dict[str, Any]:
        condition = dict(condition or {})
        if "id" in condition:
            condition["_id"] = ObjectId(condition["id"])
            del condition["id"]
        elif "_id" in condition and isinstance(condition["_id"], str):
            condition["_id"] = ObjectId(condition["_id"])
        return condition

    def add(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        try:
            data = dict(data)
            data.pop("id", None)
            return self.mongo_exec.add(table=self.table, data=data)
        except Exception as e:
            self.logger.error(f"添加工作流用户策略记录失败: {e}")
            return False, None

    def update(self, id: str, data: dict[str, Any]) -> bool:
        try:
            if "id" in data:
                del data["id"]
            condition = self._normalize_condition({"_id": id})
            return self.mongo_exec.update(table=self.table, data=data, condition=condition)
        except Exception as e:
            self.logger.error(f"更新工作流用户策略分配记录失败: {e}")
            return False

    def delete(self, id: str = "") -> bool:
        try:
            condition = {}
            if id != "":
                condition = self._normalize_condition({"_id": id})
            return self.mongo_exec.delete(table=self.table, condition=condition)
        except Exception as e:
            self.logger.error(f"删除工作流用户策略分配记录失败: {e}")
            return False

    def delete_by_service_name(self, service_name: str, user_strategy_id: str = "") -> bool:
        """按照service_name删除记录，如果参数user_strategy_id不为空，就删除含有user_strategy_id的service_name"""
        try:
            if not service_name:
                self.logger.error("service_name 为空，无法删除")
                return False
            condition: dict[str, Any] = {"service_name": service_name}
            if user_strategy_id and len(user_strategy_id) > 0:
                condition["user_strategy_ids"] = {"$elemMatch": {user_strategy_id: {"$exists": True}}}
            return self.mongo_exec.delete(table=self.table, condition=condition)
        except Exception as e:
            self.logger.error(f"根据 service_name 删除工作流用户策略分配记录失败: {e}")
            return False

    def query_by_service_name(self, service_name: str) -> tuple[bool, Any | None]:
        try:
            query: dict[str, Any] = {}
            if service_name and len(service_name) > 0:
                query = {"service_name": service_name}
            return self.mongo_exec.query_by_condition(
                table=self.table,
                condition=query,
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按 service_name 查询失败: {e}")
            return False, None

    def query_by_service_name_sorted(self, service_name: str) -> tuple[bool, Any | None]:
        """按 service_name 查询，并按 user_strategy_ids 长度从小到大排序。"""
        try:
            res, records = self.query_by_service_name(service_name)
            if not res or not records:
                return res, records
            sorted_records = sorted(
                records,
                key=lambda item: len(item.get("user_strategy_ids", []) or []),
            )
            return True, sorted_records
        except Exception as e:
            self.logger.error(f"按 service_name 排序查询失败: {e}")
            return False, None

    def query_by_id(self, id: str) -> tuple[bool, Any | None]:
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
            self.logger.error(f"按 id 查询失败: {e}")
            return False, None

    def upsert(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """新增或更新工作流微服务分配记录。"""
        try:
            data = data.copy()
            if "id" in data:
                del data["id"]
            service_name = data.get("service_name", "")
            if not service_name:
                self.logger.error("service_name 为空，无法 upsert")
                return False, None
            condition: dict[str, Any] = {"service_name": service_name}
            updated = self.mongo_exec.update(
                table=self.table,
                data=data,
                condition=condition,
            )
            if updated:
                res, records = self.query_by_service_name(service_name)
                if res and records and len(records) > 0:
                    return True, str(records[0].get("_id", ""))
                return True, None
            return self.add(data)
        except Exception as e:
            self.logger.error(f"新增或更新工作流微服务分配记录失败: {e}")
            return False, None

    def delete_by_user_strategy_id(self, user_strategy_id: str) -> bool:
        """根据行业名称删除工作流用户策略分配记录。"""
        try:
            if not user_strategy_id or len(user_strategy_id) == 0:
                self.logger.error("user_strategy_id 为空，无法删除")
                return False
            # 匹配 user_strategy_ids 中包含该行业名称的记录
            condition: dict[str, Any] = {
                "user_strategy_ids": {"$regex": user_strategy_id}
            }
            res, result_list = self.mongo_exec.query_by_condition(
                table=self.table,
                condition=condition,
                sort={"service_name": 1},
                skip=0,
                limit=0,
            )
            if res and result_list and len(result_list) > 0:
                for record in result_list:
                    service_name = record.get("service_name", "")
                    user_strategy_ids = record.get("user_strategy_ids", [])
                    if not service_name or not isinstance(user_strategy_ids, list):
                        continue
                    # 过滤掉需要删除的user_strategy_id
                    new_user_strategy_ids = [id for id in user_strategy_ids if id != user_strategy_id]
                    # 更新记录
                    update_data = {"user_strategy_ids": new_user_strategy_ids}
                    self.update(str(record.get("_id", "")), update_data)
            return True
        except Exception as e:
            self.logger.error(f"根据行业名称删除工作流微服务分配记录失败: {e}")
            return False
