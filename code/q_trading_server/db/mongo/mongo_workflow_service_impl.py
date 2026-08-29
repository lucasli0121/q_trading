"""
Author: liguoqiang
Date: 2026-08-06 10:00:00
LastEditors: liguoqiang
LastEditTime: 2026-08-06 10:00:00
Description: 数据代理表 MongoDB 操作实现
"""

# coding="utf8"

import logging
from typing import Any

from app_context import AppContext


class MongoWorkFlowServiceImpl:
    """数据代理表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.workflow_service_table = self.mongo_exec.db["workflow_service_tbl"]

    def add_workflow_service(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """添加数据代理记录"""
        try:
            data = data.copy()
            if "id" in data:
                del data["id"]
            service_name = data.get("service_name", "")
            if not service_name:
                self.logger.error("service_name is empty.")
                return False, None
            if "description" not in data:
                data["description"] = ""
            if "is_online" not in data:
                data["is_online"] = False
            if "online_time" not in data:
                data["online_time"] = ""
            return self.mongo_exec.add(table=self.workflow_service_table, data=data)
        except Exception as e:
            self.logger.error(f"添加数据代理记录失败: {e}")
            return False, None

    def update_workflow_service(self, data: dict[str, Any], condition: dict[str, Any]) -> bool:
        """更新数据代理记录"""
        try:
            if "id" in data:
                del data["id"]
            if "_id" in condition and isinstance(condition["_id"], str):
                from bson import ObjectId

                try:
                    condition["_id"] = ObjectId(condition["_id"])
                except Exception:
                    pass
            return self.mongo_exec.update(table=self.workflow_service_table, data=data, condition=condition)
        except Exception as e:
            self.logger.error(f"更新数据代理记录失败: {e}")
            return False

    def delete_workflow_service(self, id: str) -> bool:
        """删除数据代理记录"""
        try:
            from bson import ObjectId

            condition: dict[str, Any] = {"_id": ObjectId(id)}
            return self.mongo_exec.delete(table=self.workflow_service_table, condition=condition)
        except Exception as e:
            self.logger.error(f"删除数据代理记录失败: {e}")
            return False

    def query_workflow_services(
        self,
        service_name: str = "",
        is_online: int | None = None,
        skip: int = 0,
        limit: int = 0,
    ) -> tuple[bool, Any | None]:
        """查询数据代理记录"""
        try:
            query: dict[str, Any] = {}
            if service_name:
                query["service_name"] = service_name
            if is_online is not None:
                query["is_online"] = bool(is_online)
            return self.mongo_exec.query_by_condition(
                table=self.workflow_service_table,
                condition=query,
                sort={"service_name": 1},
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            self.logger.error(f"查询数据代理记录失败: {e}")
            return False, None

    def query_workflow_service_by_id(self, id: str) -> tuple[bool, Any | None]:
        """按 ID 查询单个数据代理"""
        try:
            from bson import ObjectId

            query: dict[str, Any] = {"_id": ObjectId(id)}
            return self.mongo_exec.query_by_condition(
                table=self.workflow_service_table,
                condition=query,
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按 ID 查询数据代理失败: {e}")
            return False, None
