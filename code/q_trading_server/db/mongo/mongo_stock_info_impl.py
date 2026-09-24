# coding="utf8"

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Hashable

from pymongo import UpdateOne
from app_context import AppContext
from utils import tools

class MongoStockInfoImpl():
    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.stock_info_table = self.mongo_exec.db["stock_info_tbl"]

    """
    :function normalize_code
    提取code中的数字部分(code可能为sh1111,也可能为1111.sh格式)

    实现已下沉到 utils.tools.pure_stock_code，这里只做转发：
    外部模块（如 AkStockProxy）需要纯代码时应当直接调用 tools.pure_stock_code，
    不要再通过本类获取——本类实例化会连带构造 AppContext。
    """
    def normalize_code(self, code: str) -> str:
        return tools.pure_stock_code(code)

    """
    :function full_code
    归一化为库中统一使用的完整代码格式：xxxx.SH / xxxx.SZ / xxxx.BJ

    写入与查询都必须用这个格式做**等值匹配**，不再使用 $regex。
    原因：正则形如 ^(sh|sz)?600519(\\.(SH|SZ))?$，开头是交替分支，
    MongoDB 无法提取固定前缀；配合 $or 时每个分支都要单独评估，
    索引完全用不上。归一化后等值匹配可以直接命中 {code: 1} 索引。
    """
    def full_code(self, code: str) -> str:
        return tools.guess_stock_code_suffix(self.normalize_code(code))

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
            full_code = self.full_code(code)
            if not full_code:
                self.logger.error("code is empty.")
                return False, None
            # 统一按规范格式落库，保证后续等值查询可命中
            data["code"] = full_code
            result = self.stock_info_table.update_one({"code": full_code}, {"$set": data}, upsert=True)
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
            full_code = self.full_code(data.get("code", ""))
            if not full_code:
                self.logger.warning("跳过 code 为空的股票信息记录")
                continue
            data["code"] = full_code
            requests.append(
                UpdateOne(
                    {"code": full_code},
                    {"$set": dict(data)},
                    upsert=True,
                )
            )
        if not requests:
            self.logger.warning("批量upsert股票信息：无有效记录")
            return False
        result = self.stock_info_table.bulk_write(requests, ordered=False)
        self.logger.info(
            "批量upsert股票信息: matched=%d, modified=%d, upserted=%d",
            result.matched_count, result.modified_count, result.upserted_count,
        )
        return result.acknowledged

    """
    查询股票信息
    :param code: 股票代码
    :return: 成功返回True和记录列表，否则返回False和None
    """
    def query_stock_info(self, code: str) -> tuple[bool, Any|None]:
        try:
            full_code = self.full_code(code)
            if not full_code:
                self.logger.error("code is empty.")
                return False, None
            return self.mongo_exec.query_by_condition(
                table=self.stock_info_table,
                condition={"code": full_code},
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
            full_codes = [c for c in (self.full_code(code) for code in codes) if c]
            if not full_codes:
                return True, None
            return self.mongo_exec.query_by_condition(
                table=self.stock_info_table,
                condition={"code": {"$in": full_codes}},
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
    :return: 成功返回True，否则返回False
    """
    def delete_stock_info(self, code: str) -> bool:
        try:
            full_code = self.full_code(code)
            if not full_code:
                self.logger.error("code is empty.")
                return False
            result = self.mongo_exec.delete(
                table=self.stock_info_table, condition={"code": full_code}
            )
            return result
        except Exception as e:
            self.logger.error(f"删除股票信息失败: {e}")
            return False
        