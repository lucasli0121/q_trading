# coding="utf8"

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Hashable

from bson import ObjectId
from utils import tools
from pymongo import UpdateOne
from app_context import AppContext
from db.mongo.mongo_stock_info_impl import MongoStockInfoImpl


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
                self.logger.error("股票池 name is empty.")
                return False, "股票池名称不能为空"
            user_id = data.get("user_id", "")
            if not user_id:
                self.logger.error("新增股票池user_id不能为空")
                return False, "用户id不能为空"
            res, values = self.query_stock_pool_by_name_and_user(name, user_id)
            if res and values and len(values) > 0:
                self.logger.info("股票池已经存在，不能重复创建")
                return False, "重复的股票池名称已经存在，不能重复创建"
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

    def delete_stock_pool_by_id(self, pool_id: str) -> bool:
        if pool_id == "":
            return False
        return self.mongo_exec.delete(table=self.pool_table, condition={"_id": ObjectId(pool_id)})






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

    def add_stocks_to_pool(self, records: list[dict[str, Any]]) -> bool:
        """批量向股票池添加股票。

        :param records: 股票关联记录列表，每项应包含 pool_name、code、add_time
        :return: 成功返回 True，否则返回 False
        """
        try:
            requests = []
            for data in records:
                pool_id = data.get("pool_id", "")
                code = data.get("code", "")
                if not pool_id or not code:
                    continue
                requests.append(
                    UpdateOne(
                        {"pool_id": pool_id, "code": code},
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

    def remove_stocks_from_pool(self, pool_id: str, codes: Sequence[str]) -> bool:
        """从股票池中移除指定股票。

        :param pool_id: 股票池id
        :param codes: 要移除的股票代码列表
        :return: 成功返回 True，否则返回 False
        """
        try:
            query: dict[str, Any] = {
                "pool_id": pool_id,
                "code": {"$in": codes}
            }
            result = self.mongo_exec.delete(table=self.pool_stock_table, condition=query)
            return result
        except Exception as e:
            self.logger.error(f"从股票池移除股票失败: {e}")
            return False

    def query_stocks_by_pool_ids(self, pool_ids: list) -> tuple[bool, Any | None]:
        """查询指定股票池中的所有股票。

        :param pool_name: 股票池名称
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"pool_id": {"$in": pool_ids}}
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
        self, pool_id: str, codes: Sequence[str]
    ) -> tuple[bool, Any | None]:
        """查询股票池中是否包含指定股票（用于去重或验证）。

        :param pool_id: 股票池id
        :param codes: 要查询的股票代码列表
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            real_codes = [tools.guess_stock_code_suffix(c) for c in codes]
            query: dict[str, Any] = {
                "pool_id": pool_id,
                "code": {"$in": real_codes},
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
        :return: 成功返回 True 和总数，否则返回 False 和 0
        """
        try:
            pipeline: list[dict[str, Any]] = [
                {"$match": {"code": {"$exists": True, "$ne": None}}},
                {"$group": {"_id": "$code"}},
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

        等不同格式的同一只股票只返回一次。返回结果中，code 会与 pool_id 列表形成一对多映射。

        :param skip: 跳过的记录数
        :param limit: 返回的记录数上限
        :return: 成功返回 True 和记录列表（每项含 code 和 pool_id 列表），否则返回 False 和 None
        """
        try:
            pipeline: list[dict[str, Any]] = [
                {
                    "$group": {
                        "_id": "$code",
                        "code": {"$first": "$code"},
                        "pool_id": {"$addToSet": "$pool_id"},
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
