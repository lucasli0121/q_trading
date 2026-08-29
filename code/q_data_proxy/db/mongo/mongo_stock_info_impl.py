# coding="utf8"

import logging
from collections.abc import Mapping, Sequence
import re
from typing import Any, Hashable

from pymongo import UpdateOne
from app_context import AppContext

class MongoStockInfoImpl():
    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.stock_info_table = self.mongo_exec.db["stock_info_tbl"]

    """
    :function normalize_code
    提取code中的数字部分(code可能为sh1111,也可能为1111.sh格式)
    """
    def normalize_code(self, code: str) -> str:
        match = re.search(r"\d+", code)
        return match.group() if match else code

    """
    插入或更新股票信息
    :param data: 股票信息字典
    :return: 成功返回True和记录ID，否则返回False和None
    """
    def insert_or_update_stock_info(self, data: dict[str, Any]) -> tuple[bool, str|None]:
        try:
            data = data.copy()
            if "id" in data:
                del data["id"]
            code = data.get("code", "")
            pure_code = self.normalize_code(code)
            if not pure_code:
                self.logger.error("code is empty.")
                return False, None
            # 先查询是否存在记录
            filter = {"code": {"$regex": f"^(sh|sz)?{pure_code}(\\.(SH|SZ))?$"}}
            result = self.stock_info_table.update_one(filter, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None
        except Exception as e:
            self.logger.error(f"插入或更新股票信息失败: {e}")
            return False, None
        
    """
    批量插入或更新股票信息
    :param records: 股票信息列表
    :return: 成功返回True，否则返回False
    """
    def bulk_upsert_stock_info(self, records: Sequence[Mapping[Hashable, Any]]) -> bool:
        requests = []
        for data in records:
            data = dict(data)
            data = data.copy()
            if "id" in data:
                del data["id"]
            code = data["code"]
            pure_code = self.normalize_code(code)
            requests.append(
                UpdateOne(
                    {"code": {"$regex": f"^(sh|sz)?{pure_code}(\\.(SH|SZ))?$"}},
                    {"$set": dict(data)},
                    upsert=True,
                )
            )
        result = self.stock_info_table.bulk_write(requests, ordered=False)
        self.logger.info(
            "批量upsert股票信息: matched=%d, modified=%d, upserted=%d",
            result.matched_count, result.modified_count, result.upserted_count,
        )
        return result.acknowledged

    """
    查询股票信息
    :param code: 股票代码
    :param start_time: 开始时间
    :param end_time: 结束时间
    :return: 成功返回True和记录列表，否则返回False和None
    """
    def query_stock_info(self, code: str) -> tuple[bool, Any|None]:
        try:
            pure_code = self.normalize_code(code)
            query :dict[str, Any]= {}
            query["code"] = {"$regex": f"^(sh|sz)?{pure_code}(\\.(SH|SZ))?$"}
            return self.mongo_exec.query_by_condition(
                table=self.stock_info_table,
                condition=query,
                sort={"code": 1},
                skip=None,
                limit=None)
        except Exception as e:
            self.logger.error(f"获取股票信息失败: {e}")
            return False, None

    """
    获取股票总数
    :return: 成功返回True和总数，否则返回False和None
    """
    def get_all_stock_count(self, condition: dict[str, Any]) -> tuple[bool, int]:
        try:
            count = self.stock_info_table.count_documents(condition)
            return True, count
        except Exception as e:
            self.logger.error(f"获取股票总数失败: {e}")
            return False, 0        
        
    """
    查询所有股票信息
    :return: 成功返回True和记录列表，否则返回False和None
    """
    def query_all_stock_info(self, skip: int = 0, limit: int = 0) -> tuple[bool, Any|None]:
        try:
            return self.mongo_exec.query_by_condition(
                table=self.stock_info_table,
                condition={},
                sort={"code": 1},
                skip=skip,
                limit=limit)
        except Exception as e:
            self.logger.error(f"获取股票信息失败: {e}")
            return False, None
    """
    批量查询股票信息（按多个代码）
    :param codes: 股票代码列表
    :return: 成功返回True和记录列表，否则返回False和None
    """
    def query_by_codes(self, codes: list[str]) -> tuple[bool, Any|None]:
        try:
            regex_conditions: list[dict[str, Any]] = []
            for code in codes:
                pure_code = self.normalize_code(code)
                regex_conditions.append(
                    {"code": {"$regex": f"^(sh|sz)?{pure_code}(\\.(SH|SZ))?$"}}
                )
            return self.mongo_exec.query_by_condition(
                table=self.stock_info_table,
                condition={"$or": regex_conditions},
                sort={"code": 1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"批量查询股票信息失败: {e}")
            return False, None

    """
    按板块查询股票信息
    :param board: 板块名称（如 主板、创业板、科创板、北交所）
    :param skip: 分页跳过条数
    :param limit: 分页限制条数
    :return: 成功返回True和记录列表，否则返回False和None
    """
    def query_by_board(self, board: str, skip: int = 0, limit: int = 0) -> tuple[bool, Any|None]:
        try:
            return self.mongo_exec.query_by_condition(
                table=self.stock_info_table,
                condition={"board": board},
                sort={"code": 1},
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            self.logger.error(f"按板块查询股票信息失败: {e}")
            return False, None

    """
    按行业查询股票信息
    :param industry: 行业名称
    :param skip: 分页跳过条数
    :param limit: 分页限制条数
    :return: 成功返回True和记录列表，否则返回False和None
    """
    def query_by_industry(self, industry: str, skip: int = 0, limit: int = 0) -> tuple[bool, Any|None]:
        try:
            return self.mongo_exec.query_by_condition(
                table=self.stock_info_table,
                condition={"industry": {"$regex": industry}},
                sort={"code": 1},
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            self.logger.error(f"按行业查询股票信息失败: {e}")
            return False, None

    """
    删除股票信息
    :param code: 股票代码
    :param start_time: 开始时间
    :param end_time: 结束时间
    :return: 成功返回True，否则返回False
    """
    def delete_stock_info(self, code: str) -> bool:
        try:
            pure_code = self.normalize_code(code)
            query :dict[str, Any]= {}
            query["code"] = {"$regex": f"^(sh|sz)?{pure_code}(\\.(SH|SZ))?$"}
            result = self.mongo_exec.delete(table=self.stock_info_table, condition=query)
            return result
        except Exception as e:
            self.logger.error(f"删除股票信息失败: {e}")
            return False
        