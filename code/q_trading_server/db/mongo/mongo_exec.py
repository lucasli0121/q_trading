'''
Author: liguoqiang
Date: 2021-08-06 14:10:41
LastEditors: liguoqiang
LastEditTime: 2024-06-06 21:00:51
Description: 
'''
# coding="utf8"

from copy import deepcopy
from typing import Any
from bson import ObjectId
from pymongo import MongoClient, ReadPreference
from pymongo.collection import Collection
import logging
from db.db_base import DbBaseImpl

# 查询缺省条数上限。
# pymongo 中 .limit(0) 等于「不限制」，调用方完全不指定 limit（None）时会整表物化，
# 因此给一个兜底上限，触发时打 WARNING 日志，便于定位漏传的调用点。
#
# 取值约束：必须大于单表「全市场」的自然行数（A 股约 5400 只）。
# 否则 query_all_stock_info / query_by_codes / query_minute_hq_by_time 这类
# 按设计就要返回全市场记录的查询会被误截断，反而制造出静默的数据缺失。
DEFAULT_QUERY_LIMIT: int = 10000

# 「需要完整结果集」的哨兵值。
# 确需全量读取的调用方（如 K 线同步任务要靠 db_codes 判断缺失股票）显式传该值，
# 让「全表扫描」成为有意为之的行为，而不是默认行为。
UNLIMITED_QUERY_LIMIT: int = -1

# 中文排序 collation。仅在需要拼音排序或忽略大小写比较时由调用方显式传入。
# 注意：带 collation 的索引与不带 collation 的查询互不匹配，两者必须成对出现。
COLLATION_ZH: dict[str, Any] = {"locale": "zh", "strength": 1}

class MongoExec(DbBaseImpl):
    _initialized = False

    def __init__(self):
         if not self._initialized:
            self._initialized = True
            super().__init__()
            self.logger = logging.getLogger(__name__)
            if self.mongo_host is not None:
                self.conn = MongoClient(
                    host = self.mongo_host,
                    port=int(self.mongo_port),
                    w="majority",
                    read_preference=ReadPreference.PRIMARY,
                    username=self.mongo_username,
                    password=self.mongo_password,
                    authSource=self.mongo_database)
                self.db = self.conn[self.mongo_database]

    def __del__(self):
        try:
            if self.conn is not None:
                self.conn.close()
        except Exception as e:
            pass
    
    """
    添加
    :param data: 信息字典
    :return: 成功返回True，否则返回False
    """
    def add(self, table: Collection, data: dict[str, Any]) -> tuple[bool, str|None]:
        try:
            if table is None:
                self.logger.error("table not found in MongoDB.")
                return False, None
            data = dict(data)
            data.pop('id', None)  # 移除'id'字段，MongoDB会自动生成'_id'
            ret = table.insert_one(data)
            return ret.acknowledged, str(ret.inserted_id)  # 确认插入操作已被确认
        except Exception as e:
            self.logger.error(f"添加信息失败: {e}")
            return False, e.args[0]
        
    """ 
    更新到数据库
    :param data: 字典
    :param condition: 更新条件，例如 "id = 1"
    :return: 成功返回True，否则返回False
    """
    def update(self, table: Collection, data: dict[str, Any], condition: dict[str, Any]) -> bool:
        try:
            if table is None:
                self.logger.error("table not found in MongoDB.")
                return False
            data = deepcopy(data)
            if condition is None or len(condition) == 0:
                condition = {'_id': ObjectId(data.get('id', ''))}  # 如果没有条件，使用id作为默认条件
            else:
                condition = deepcopy(condition)
                if 'id' in condition:
                    condition['_id'] = ObjectId(condition['id'])
                    del condition['id']
            if 'id' in data:
                del data['id']
            ret = table.update_one(condition, {'$set': data})
            return ret.matched_count > 0  # 返回是否有记录被修改
        except Exception as e:
            self.logger.error(f"更新信息失败: {e}")
            return False
        
    """
    查询信息
    :param condition: 查询条件，例如 "id = 1"
    :return: 查询结果列表，每个元素是一个字典，包含公司信息
    """
    def query_by_condition(self,
            table: Collection,
            condition: dict[str, Any],
            sort: dict[str, Any]|None = None,
            skip: int|None = None,
            limit: int|None = None,
            collation: dict[str, Any]|None = None,
            max_limit: int|None = None ) -> tuple[bool, Any]:
        """按条件查询。

        :param limit: 返回条数上限。
            - 未传 / None：完全没指定上限，按 DEFAULT_QUERY_LIMIT（或 max_limit）兜底截断并打 WARNING
            - 0：按既有 impl / API 的「0 表示不限制」约定，不截断
            - 负数（UNLIMITED_QUERY_LIMIT）：显式声明需要完整结果集，不截断
            - 正数：按值截断
        :param collation: 排序规则。默认不指定（simple 二进制比较），
            这样查询才能命中不带 collation 的索引。
            需要拼音排序 / 忽略大小写时显式传 COLLATION_ZH。
        :param max_limit: 覆盖本次调用的缺省上限
        """
        try:
            if table is None:
                self.logger.error("table not found in MongoDB.")
                return False, None
            if skip is None:
                skip = 0
            if limit is None:
                # 调用方完全没有指定上限：按兜底值截断，并提示调用点，避免静默全表扫描
                limit = DEFAULT_QUERY_LIMIT if max_limit is None else max_limit
                self.logger.warning(
                    "query_by_condition 未指定 limit，按 %d 条截断（如需全量请显式传 limit=%d）: %s",
                    limit, UNLIMITED_QUERY_LIMIT, condition,
                )
            elif limit < 0:
                limit = 0  # pymongo 语义：0 表示不限制
            cursor = table.find(condition)
            if collation:
                cursor = cursor.collation(collation)
            if sort is not None and len(sort) > 0:
                cursor = cursor.sort(sort)
            results = list(cursor.skip(skip).limit(limit))
            return True, results if results else None
        except Exception as e:
            self.logger.error(f"查询信息失败: {e}")
            return False, None
    
    """
    function:
    description: 删除
    param {*} self
    return {*}
    """
    def delete(self, table: Collection, condition: dict[str, Any]) -> bool:
        try:
            if table is None:
                self.logger.error("table not found in MongoDB.")
                return False
            ret = table.delete_many(condition)  # 删除所有匹配条件的记录
            return ret.deleted_count > 0  # 返回是否有记录被删除
        except Exception as e:
            self.logger.error(f"删除失败: {e}")
            return False