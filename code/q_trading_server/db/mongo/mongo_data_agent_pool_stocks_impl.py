"""
Author: liguoqiang
Date: 2026-08-06 10:30:00
LastEditors: liguoqiang
LastEditTime: 2026-08-06 10:30:00
Description: 数据代理股票分配表 MongoDB 操作实现
"""

# coding="utf8"

import logging
from typing import Any

from bson import ObjectId

from app_context import AppContext


class MongoDataAgentPoolStocksImpl:
    """数据代理分配股票表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.data_agent_pool_stocks_table = self.mongo_exec.db["data_agent_pool_stocks_tbl"]

    @staticmethod
    def _normalize_condition(condition: dict[str, Any]) -> dict[str, Any]:
        """规范化条件，将 id 转换为 MongoDB ObjectId。"""
        condition = dict(condition or {})
        if "id" in condition:
            condition["_id"] = ObjectId(condition["id"])
            del condition["id"]
        elif "_id" in condition and isinstance(condition["_id"], str):
            condition["_id"] = ObjectId(condition["_id"])
        return condition

    def add_data_agent_pool_stock(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """添加数据代理股票分配记录。"""
        try:
            data = data.copy()
            if "id" in data:
                del data["id"]
            agent_name = data.get("agent_name", "")
            stock_codes_pool = data.get("stock_codes_pool", [])
            if not agent_name or not isinstance(stock_codes_pool, list):
                self.logger.error("agent_name 或 stock_codes_pool 不合法")
                return False, None
            return self.mongo_exec.add(table=self.data_agent_pool_stocks_table, data=data)
        except Exception as e:
            self.logger.error(f"添加数据代理股票分配记录失败: {e}")
            return False, None

    def update_data_agent_pool_stock(self, id: str, data: dict[str, Any]) -> bool:
        """更新数据代理股票分配记录。"""
        try:
            if "id" in data:
                del data["id"]
            condition = self._normalize_condition({"_id": id})
            return self.mongo_exec.update(table=self.data_agent_pool_stocks_table, data=data, condition=condition)
        except Exception as e:
            self.logger.error(f"更新数据代理股票分配记录失败: {e}")
            return False

    def delete_data_agent_pool_stocks(self, id: str = "") -> bool:
        """删除数据代理股票分配记录。"""
        try:
            condition = {}
            if id != "":
                condition = self._normalize_condition({"_id": id})
            return self.mongo_exec.delete(table=self.data_agent_pool_stocks_table, condition=condition)
        except Exception as e:
            self.logger.error(f"删除数据代理股票分配记录失败: {e}")
            return False

    def delete_data_agent_pool_stocks_by_agent_name(
        self,
        agent_name: str
    ) -> bool:
        """根据 agent_name 删除数据代理股票分配记录，可按 stock_code 进一步过滤。"""
        try:
            if not agent_name:
                self.logger.error("agent_name 为空，无法删除")
                return False
            condition: dict[str, Any] = {"agent_name": agent_name}
            return self.mongo_exec.delete(table=self.data_agent_pool_stocks_table, condition=condition)
        except Exception as e:
            self.logger.error(f"根据 agent_name 删除数据代理股票分配记录失败: {e}")
            return False

    def delete_data_agent_pool_stocks_by_pool_name(self, pool_name: str) -> bool:
        """根据股票池名称删除数据代理股票分配记录。"""
        try:
            if not pool_name:
                self.logger.error("pool_name 为空，无法删除")
                return False
            # 匹配 stock_codes_pool 中包含该股票池名称的记录
            condition: dict[str, Any] = {
                "stock_codes_pool.pool_name": {"$regex": pool_name}
            }
            res, result_list = self.mongo_exec.query_by_condition(
                table=self.data_agent_pool_stocks_table,
                condition=condition,
                sort={"agent_name": 1},
                skip=0,
                limit=0,
            )
            if res and result_list and len(result_list) > 0:
                for record in result_list:
                    agent_name = record.get("agent_name", "")
                    stock_codes_pool = record.get("stock_codes_pool", [])
                    if not agent_name or not isinstance(stock_codes_pool, list):
                        continue
                    # 过滤掉包含该池名称的股票代码
                    new_stock_codes_pool = []
                    for item in stock_codes_pool:
                        if isinstance(item, dict):
                            code = item.get("code", "")
                            pools = item.get("pool_name", [])
                            new_pools = [name for name in pools if pool_name != name]
                            if new_pools and len(new_pools) > 0:
                                new_item = {"code": code, "pool_name": new_pools}
                                new_stock_codes_pool.append(new_item)
                    # 更新记录
                    update_data = {"stock_codes_pool": new_stock_codes_pool}
                    self.update_data_agent_pool_stock(str(record.get("_id", "")), update_data)
            return True
            # return self.mongo_exec.delete(table=self.data_agent_pool_stocks_table, condition=condition)
        except Exception as e:
            self.logger.error(f"根据股票池名称删除数据代理股票分配记录失败: {e}")
            return False
        
    def query_data_agent_pool_stocks(
        self,
        agent_name: str = "",
        skip: int = 0,
        limit: int = 0,
    ) -> tuple[bool, Any | None]:
        """查询数据代理股票分配记录。"""
        try:
            query: dict[str, Any] = {}
            if agent_name:
                query["agent_name"] = agent_name
            return self.mongo_exec.query_by_condition(
                table=self.data_agent_pool_stocks_table,
                condition=query,
                sort={"agent_name": 1},
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            self.logger.error(f"查询数据代理股票分配记录失败: {e}")
            return False, None

    def upsert_data_agent_pool_stock(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """新增或更新数据代理股票分配记录。"""
        try:
            data = data.copy()
            if "id" in data:
                del data["id"]
            agent_name = data.get("agent_name", "")
            if not agent_name:
                self.logger.error("agent_name 为空，无法 upsert")
                return False, None
            condition: dict[str, Any] = {"agent_name": agent_name}
            updated = self.mongo_exec.update(
                table=self.data_agent_pool_stocks_table,
                data=data,
                condition=condition,
            )
            if updated:
                res, records = self.query_data_agent_pool_stock_by_agent_name(agent_name)
                if res and records and len(records) > 0:
                    return True, str(records[0].get("_id", ""))
                return True, None
            return self.add_data_agent_pool_stock(data)
        except Exception as e:
            self.logger.error(f"新增或更新数据代理股票分配记录失败: {e}")
            return False, None

    def query_data_agent_pool_stock_by_agent_name(
        self,
        agent_name: str,
    ) -> tuple[bool, Any | None]:
        """按 agent_name 查询数据代理股票分配记录。"""
        try:
            query: dict[str, Any] = {"agent_name": agent_name}
            return self.mongo_exec.query_by_condition(
                table=self.data_agent_pool_stocks_table,
                condition=query,
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按 agent_name 查询数据代理股票分配记录失败: {e}")
            return False, None

    def query_data_agent_pool_stock_by_id(self, id: str) -> tuple[bool, Any | None]:
        """按 ID 查询单个数据代理股票分配记录。"""
        try:
            condition = self._normalize_condition({"_id": id})
            return self.mongo_exec.query_by_condition(
                table=self.data_agent_pool_stocks_table,
                condition=condition,
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按 ID 查询数据代理股票分配记录失败: {e}")
            return False, None
