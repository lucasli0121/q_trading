# coding="utf8"

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Hashable

from pymongo import UpdateOne
from app_context import AppContext

class MongoIndustryImpl():
    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.industry_table = self.mongo_exec.db["industry_tbl"]
        self.industry_base_table = self.mongo_exec.db["industry_base_tbl"]

    """
    插入或更新行业信息
    :param data: 行业信息字典
    :return: 成功返回True和记录ID，否则返回False和None
    """
    def insert_or_update_industry_info(self, data: dict[str, Any]) -> tuple[bool, str|None]:
        try:
            data = data.copy()
            name = data.get("name", "")
            if not name:
                self.logger.error("name is empty.")
                return False, None
            if "id" in data:
                del data["id"]
            # 先查询是否存在记录
            filter = {"name": name}
            result = self.industry_table.update_one(filter, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None
        except Exception as e:
            self.logger.error(f"插入或更新行业信息失败: {e}")
            return False, None
        
    """
    批量插入或更新行业信息
    :param records: 行业信息列表
    :return: 成功返回True，否则返回False
    """
    def bulk_upsert_industry_info(self, records: Sequence[Mapping[Hashable, Any]]) -> bool:
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
        result = self.industry_table.bulk_write(requests, ordered=False)
        self.logger.info(
            "批量upsert行业信息: matched=%d, modified=%d, upserted=%d",
            result.matched_count, result.modified_count, result.upserted_count,
        )
        return result.acknowledged

    """
    查询行业信息
    :param name: 行业名称
    :param start_time: 开始时间
    :param end_time: 结束时间
    :return: 成功返回True和记录列表，否则返回False和None
    """
    def query_industry_info(self, name: str) -> tuple[bool, Any|None]:
        try:
            query :dict[str, Any]= {}
            query["name"] = name
            return self.mongo_exec.query_by_condition(
                table=self.industry_table,
                condition=query,
                sort={"name": 1},
                skip=None,
                limit=None)
        except Exception as e:
            self.logger.error(f"获取行业信息失败: {e}")
            return False, None
    
    """
    删除行业信息
    :param code: 行业代码
    :param start_time: 开始时间
    :param end_time: 结束时间
    :return: 成功返回True，否则返回False
    """
    def delete_industry_info(self, name: str) -> bool:
        try:
            query :dict[str, Any]= {}
            if name and len(name) > 0:
                query["name"] = name
            result = self.mongo_exec.delete(table=self.industry_table, condition=query)
            return result
        except Exception as e:
            self.logger.error(f"删除行业信息失败: {e}")
            return False
        
    """
    插入或更新行业基础信息
    :param data: 行业基础信息字典
    :return: 成功返回True和记录ID，否则返回False和None
    """
    def insert_or_update_industry_base_info(self, data: dict[str, Any]) -> tuple[bool, str|None]:
        try:
            data = data.copy()
            industry_id = data.get("id", "")
            if "id" in data:
                del data["id"]
            id = industry_id
            filter = {}
            if id is not None and len(id) > 0:
                # 先查询是否存在记录
                filter["id"] = id
            tick_id = data.get("tick_id", "")
            if tick_id is not None and len(tick_id) > 0:
                # 先查询是否存在记录
                filter["tick_id"] = tick_id
            result = self.industry_base_table.update_one(filter, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None
        except Exception as e:
            self.logger.error(f"插入或更新行业基础信息失败: {e}")
            return False, None
        
    """
    批量插入或更新行业基础信息
    :param records: 行业信息列表
    :return: 成功返回True，否则返回False
    """
    def bulk_upsert_industry_base_info(self, records: Sequence[Mapping[Hashable, Any]]) -> bool:
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
        result = self.industry_base_table.bulk_write(requests, ordered=False)
        self.logger.info(
            "批量upsert行业基础信息: matched=%d, modified=%d, upserted=%d",
            result.matched_count, result.modified_count, result.upserted_count,
        )
        return result.acknowledged
    
    """
    查询行业基础信息
    :return: 成功返回True和记录列表，否则返回False和None
    """
    def query_all_industry_base_info(self) -> tuple[bool, Any|None]:
        try:
            query :dict[str, Any]= {}
            return self.mongo_exec.query_by_condition(
                table=self.industry_base_table,
                condition=query,
                sort=None,
                skip=None,
                limit=None)
        except Exception as e:
            self.logger.error(f"获取行业信息失败: {e}")
            return False, None