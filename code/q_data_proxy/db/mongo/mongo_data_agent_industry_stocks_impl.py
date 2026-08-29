"""
Author: generated
Description: MongoDB 操作实现，用于 DataAgentIndustryStocks
"""

# coding="utf8"

import logging
from typing import Any

from bson import ObjectId

from app_context import AppContext


class MongoDataAgentIndustryStocksImpl:
    """数据代理分配行业股票表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.table = self.mongo_exec.db["data_agent_industry_stocks_tbl"]

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
            self.logger.error(f"添加数据代理行业分配记录失败: {e}")
            return False, None

    def update(self, id: str, data: dict[str, Any]) -> bool:
        try:
            if "id" in data:
                del data["id"]
            condition = self._normalize_condition({"_id": id})
            return self.mongo_exec.update(table=self.table, data=data, condition=condition)
        except Exception as e:
            self.logger.error(f"更新数据代理行业分配记录失败: {e}")
            return False

    def delete(self, id: str = "") -> bool:
        try:
            condition = {}
            if id != "":
                condition = self._normalize_condition({"_id": id})
            return self.mongo_exec.delete(table=self.table, condition=condition)
        except Exception as e:
            self.logger.error(f"删除数据代理行业分配记录失败: {e}")
            return False

    def delete_by_agent_name(self, agent_name: str, stock_code: str = "") -> bool:
        try:
            if not agent_name:
                self.logger.error("agent_name 为空，无法删除")
                return False
            condition: dict[str, Any] = {"agent_name": agent_name}
            if stock_code:
                # stock_codes_industry 存储为数组，元素为 { code: [industry_ids] }
                condition["stock_codes_industry"] = {"$elemMatch": {stock_code: {"$exists": True}}}
            return self.mongo_exec.delete(table=self.table, condition=condition)
        except Exception as e:
            self.logger.error(f"根据 agent_name 删除数据代理行业分配记录失败: {e}")
            return False

    def query_by_agent_name(self, agent_name: str) -> tuple[bool, Any | None]:
        try:
            query: dict[str, Any] = {}
            if agent_name != "":
                query = {"agent_name": agent_name}
            return self.mongo_exec.query_by_condition(
                table=self.table,
                condition=query,
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按 agent_name 查询失败: {e}")
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
                table=self.table,
                data=data,
                condition=condition,
            )
            if updated:
                res, records = self.query_by_agent_name(agent_name)
                if res and records and len(records) > 0:
                    return True, str(records[0].get("_id", ""))
                return True, None
            return self.add(data)
        except Exception as e:
            self.logger.error(f"新增或更新数据代理股票分配记录失败: {e}")
            return False, None

    def delete_by_industry(self, industry: str) -> bool:
        """根据行业名称删除数据代理行业分配记录。"""
        try:
            if not industry:
                self.logger.error("industry 为空，无法删除")
                return False
            # 匹配 stock_codes_industry 中包含该行业名称的记录
            condition: dict[str, Any] = {
                "stock_codes_industry.industry": {"$regex": industry}
            }
            res, result_list = self.mongo_exec.query_by_condition(
                table=self.table,
                condition=condition,
                sort={"agent_name": 1},
                skip=0,
                limit=0,
            )
            if res and result_list and len(result_list) > 0:
                for record in result_list:
                    agent_name = record.get("agent_name", "")
                    stock_codes_industry = record.get("stock_codes_industry", [])
                    if not agent_name or not isinstance(stock_codes_industry, list):
                        continue
                    # 过滤掉包含该行业名称的股票代码
                    new_stock_codes_industry = []
                    for item in stock_codes_industry:
                        if isinstance(item, dict):
                            code = item.get("code", "")
                            industrys = item.get("industry", [])
                            new_industrys = [name for name in industrys if industry != name]
                            if new_industrys and len(new_industrys) > 0:
                                new_item = {"code": code, "industry": new_industrys}
                                new_stock_codes_industry.extend(new_item)
                    # 更新记录
                    update_data = {"stock_codes_industry": new_stock_codes_industry}
                    self.update(str(record.get("_id", "")), update_data)
            return True
            # return self.mongo_exec.delete(table=self.table, condition=condition)
        except Exception as e:
            self.logger.error(f"根据行业名称删除数据代理股票分配记录失败: {e}")
            return False
