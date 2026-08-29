# coding="utf8"

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Hashable

from pymongo import UpdateOne
from app_context import AppContext
from db.mongo.mongo_stock_info_impl import MongoStockInfoImpl


def _pool_code_regex(code: str) -> dict[str, Any]:
    """返回股票池 code 字段的正则匹配条件。"""
    m = re.search(r"\d+", code)
    pure = m.group() if m else code
    return {"$regex": f"^(sh|sz)?{pure}(\\.(SH|SZ))?$"}


def _pool_codes_or(codes: list[str]) -> dict[str, Any]:
    """返回多个 code 的 $or 正则匹配条件。"""
    return {"$or": [{"code": _pool_code_regex(c)} for c in codes]}


class MongoStockPoolImpl:
    """股票池及股票池股票关联表 MongoDB 操作实现。

    管理两个集合：
    - stock_pool_tbl：股票池基本信息
    - stock_pool_stock_tbl：股票池与股票的关联关系
    """

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.pool_table = self.mongo_exec.db["stock_pool_tbl"]
        self.pool_stock_table = self.mongo_exec.db["stock_pool_stock_tbl"]

    # ==================== 股票池 CRUD ====================

    def insert_or_update_stock_pool(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """插入或更新股票池信息。

        :param data: 股票池信息字典
        :return: 成功返回 True 和记录 ID，否则返回 False 和 None
        """
        try:
            data = data.copy()
            name = data.get("name", "")
            if not name:
                self.logger.error("name is empty.")
                return False, None
            if "id" in data:
                del data["id"]
            filter = {"name": name}
            result = self.pool_table.update_one(filter, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None
        except Exception as e:
            self.logger.error(f"插入或更新股票池信息失败: {e}")
            return False, None

    def bulk_upsert_stock_pool(self, records: Sequence[Mapping[Hashable, Any]]) -> bool:
        """批量插入或更新股票池信息。

        :param records: 股票池信息列表
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
            result = self.pool_table.bulk_write(requests, ordered=False)
            self.logger.info(
                "批量upsert股票池: matched=%d, modified=%d, upserted=%d",
                result.matched_count, result.modified_count, result.upserted_count,
            )
            return result.acknowledged
        except Exception as e:
            self.logger.error(f"批量插入或更新股票池信息失败: {e}")
            return False

    def query_stock_pool_by_name(self, name: str) -> tuple[bool, Any | None]:
        """按名称查询股票池。

        :param name: 股票池名称
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"name": name}
            return self.mongo_exec.query_by_condition(
                table=self.pool_table,
                condition=query,
                sort={"name": 1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"查询股票池信息失败: {e}")
            return False, None

    def query_all_stock_pools(self) -> tuple[bool, Any | None]:
        """查询所有股票池。

        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.pool_table,
                condition={},
                sort={"name": 1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"查询所有股票池失败: {e}")
            return False, None

    def delete_stock_pool(self, name: str) -> bool:
        """按名称删除股票池及其关联的股票记录。

        会同步删除 stock_pool_stock_tbl 中所有 pool_name 为该名称的记录，避免孤儿数据。

        :param name: 股票池名称
        :return: 成功返回 True，否则返回 False
        """
        try:
            pool_query: dict[str, Any] = {"name": name}
            result = self.mongo_exec.delete(table=self.pool_table, condition=pool_query)
            # 级联删除关联记录
            self.mongo_exec.delete(
                table=self.pool_stock_table,
                condition={"pool_name": name},
            )
            return result
        except Exception as e:
            self.logger.error(f"删除股票池失败: {e}")
            return False

    # ==================== 用户维度查询 ====================

    def query_stock_pools_by_user(self, user_id: str) -> tuple[bool, Any | None]:
        """按用户 ID 查询该用户的所有股票池。

        :param user_id: 用户 ID
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"user_id": user_id}
            return self.mongo_exec.query_by_condition(
                table=self.pool_table,
                condition=query,
                sort={"name": 1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按用户查询股票池失败: {e}")
            return False, None

    def query_stock_pool_by_name_and_user(
        self, name: str, user_id: str
    ) -> tuple[bool, Any | None]:
        """按名称和用户 ID 查询股票池（用户范围内的精确查找）。

        :param name: 股票池名称
        :param user_id: 用户 ID
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"name": name, "user_id": user_id}
            return self.mongo_exec.query_by_condition(
                table=self.pool_table,
                condition=query,
                sort={"name": 1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按名称和用户查询股票池失败: {e}")
            return False, None

    # ==================== 股票池-股票关联操作 ====================

    def add_stocks_to_pool(self, records: Sequence[Mapping[Hashable, Any]]) -> bool:
        """批量向股票池添加股票。

        :param records: 股票关联记录列表，每项应包含 pool_name、code、add_time
        :return: 成功返回 True，否则返回 False
        """
        try:
            requests = []
            for data in records:
                pool_name = data.get("pool_name", "")
                code = data.get("code", "")
                if not pool_name or not code:
                    continue
                stock_db = MongoStockInfoImpl()
                res, values = stock_db.query_by_codes([code])
                if res and values and len(values) > 0:
                    code = values[0].get("code", code)  # 使用数据库中标准化的 code
                requests.append(
                    UpdateOne(
                        {"pool_name": pool_name, "code": _pool_code_regex(code)},
                        {"$set": {k: v for k, v in data.items() if k != "id"}},
                        upsert=True,
                    )
                )
            if not requests:
                return False
            result = self.pool_stock_table.bulk_write(requests, ordered=False)
            self.logger.info(
                "添加股票到股票池: matched=%d, modified=%d, upserted=%d",
                result.matched_count, result.modified_count, result.upserted_count,
            )
            return result.acknowledged
        except Exception as e:
            self.logger.error(f"添加股票到股票池失败: {e}")
            return False

    def remove_stocks_from_pool(self, pool_name: str, codes: Sequence[str]) -> bool:
        """从股票池中移除指定股票。

        :param pool_name: 股票池名称
        :param codes: 要移除的股票代码列表
        :return: 成功返回 True，否则返回 False
        """
        try:
            query: dict[str, Any] = {
                "pool_name": pool_name,
                **_pool_codes_or(list(codes)),
            }
            result = self.mongo_exec.delete(table=self.pool_stock_table, condition=query)
            return result
        except Exception as e:
            self.logger.error(f"从股票池移除股票失败: {e}")
            return False

    def query_stocks_by_pool_name(self, pool_name: str) -> tuple[bool, Any | None]:
        """查询指定股票池中的所有股票。

        :param pool_name: 股票池名称
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"pool_name": pool_name}
            return self.mongo_exec.query_by_condition(
                table=self.pool_stock_table,
                condition=query,
                sort={"code": 1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"查询股票池中的股票失败: {e}")
            return False, None

    def query_stocks_by_pool_and_codes(
        self, pool_name: str, codes: Sequence[str]
    ) -> tuple[bool, Any | None]:
        """查询股票池中是否包含指定股票（用于去重或验证）。

        :param pool_name: 股票池名称
        :param codes: 要查询的股票代码列表
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {
                "pool_name": pool_name,
                **_pool_codes_or(list(codes)),
            }
            return self.mongo_exec.query_by_condition(
                table=self.pool_stock_table,
                condition=query,
                sort={"code": 1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"查询股票池中指定股票失败: {e}")
            return False, None

    def get_all_pool_stocks_count(self) -> tuple[bool, int]:
        """获取所有股票池中的去重股票代码总数。

        code 可能包含不同格式（sh000001、000001.SH），通过提取纯数字归一化后去重统计。

        :return: 成功返回 True 和总数，否则返回 False 和 0
        """
        try:
            pipeline: list[dict[str, Any]] = [
                # 使用 $regexFind 提取数字部分为对象，若无匹配则回退到原始 code，避免 group 时丢失记录
                {
                    "$addFields": {
                        "_pure_obj": {"$regexFind": {"input": "$code", "regex": "\\d+"}}
                    }
                },
                {"$addFields": {"pure_code": {"$ifNull": ["$_pure_obj.match", "$code"]}}},
                {"$project": {"_pure_obj": 0}},
                {"$group": {"_id": "$pure_code"}},
                {"$count": "total"},
            ]
            result = list(self.pool_stock_table.aggregate(pipeline))
            return True, result[0]["total"] if result else 0
        except Exception as e:
            self.logger.error(f"获取股票池股票总数失败: {e}")
            return False, 0

    def query_all_pool_stocks(
        self, skip: int = 0, limit: int = 0
    ) -> tuple[bool, Any | None]:
        """查询所有股票池中的去重股票代码（分页）。

        使用聚合管道提取 code 纯数字部分归一化后去重，保证 sh000001、000001.SH、000001
        等不同格式的同一只股票只返回一次。返回结果中，code 会与 pool_name 列表形成一对多映射。

        :param skip: 跳过的记录数
        :param limit: 返回的记录数上限
        :return: 成功返回 True 和记录列表（每项含 code 和 pool_name 列表），否则返回 False 和 None
        """
        try:
            pipeline: list[dict[str, Any]] = [
                # 归一化 code：提取纯数字部分，若无则使用原始 code
                {
                    "$addFields": {
                        "_pure_obj": {"$regexFind": {"input": "$code", "regex": "\\d+"}}
                    }
                },
                {"$addFields": {"pure_code": {"$ifNull": ["$_pure_obj.match", "$code"]}}},
                {"$project": {"_pure_obj": 0}},
                # 按归一化 code 去重，保留第一条的原始 code，并聚合所属池名称
                {
                    "$group": {
                        "_id": "$pure_code",
                        "code": {"$first": "$code"},
                        "pool_name": {"$addToSet": "$pool_name"},
                    }
                },
                {"$sort": {"code": 1}},
            ]
            if skip > 0:
                pipeline.append({"$skip": skip})
            if limit > 0:
                pipeline.append({"$limit": limit})
            result = list(self.pool_stock_table.aggregate(pipeline))
            return True, result if result else None
        except Exception as e:
            self.logger.error(f"查询所有股票池股票失败: {e}")
            return False, None
