# coding="utf8"

"""
Author: liguoqiang
Date: 2026-08-13
Description: 因子表 MongoDB 操作实现 — 因子定义，管理员管理
"""

import logging
from typing import Any

from bson import ObjectId

from app_context import AppContext


class MongoFactorImpl:
    """因子表 MongoDB 操作实现 — 因子定义，管理员管理"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.factor_table = self.mongo_exec.db["factor_tbl"]

    def insert_or_update_factor(
        self, data: dict[str, Any], insert_only: bool = False
    ) -> tuple[bool, str | None, str | None]:
        """插入或更新因子信息

        :param data: 因子信息字典
        :param insert_only: 为 True 时仅执行新增，若同名因子已存在则返回冲突错误
        :return: (ok, record_id_or_None, error_or_None)
                 error 可能的值: None（无错误）、"duplicate_name"（名称重复）
        """
        try:
            data = data.copy()
            name = data.get("name", "")
            if not name:
                self.logger.error("name is empty.")
                return False, None, None
            if "id" in data:
                del data["id"]

            if insert_only:
                existing = self.factor_table.find_one({"name": name})
                if existing:
                    return False, None, "duplicate_name"

            filter_dict: dict[str, Any] = {"name": name}
            result = self.factor_table.update_one(
                filter_dict, {"$set": data}, upsert=True
            )
            return True, str(result.upserted_id) if result.upserted_id else None, None
        except Exception as e:
            self.logger.error(f"插入或更新因子信息失败: {e}")
            return False, None, None

    def query_factor_by_name(self, name: str) -> tuple[bool, Any | None]:
        """按名称查询因子

        :param name: 因子名称
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"name": name}
            return self.mongo_exec.query_by_condition(
                table=self.factor_table,
                condition=query,
                sort={"name": 1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"查询因子信息失败: {e}")
            return False, None

    def query_factor_by_id(self, id: str) -> tuple[bool, Any | None]:
        """按记录 ID 查询因子

        :param id: 因子记录 ID（MongoDB _id）
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.factor_table,
                condition={"_id": ObjectId(id)},
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按 ID 查询因子信息失败: {e}")
            return False, None

    def query_all_factors(self) -> tuple[bool, Any | None]:
        """查询所有因子

        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.factor_table,
                condition={},
                sort={"name": 1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"查询所有因子失败: {e}")
            return False, None

    def delete_factor(self, name: str) -> bool:
        """按名称删除因子

        :param name: 因子名称
        :return: 成功返回 True，否则返回 False
        """
        try:
            query: dict[str, Any] = {"name": name}
            result = self.mongo_exec.delete(table=self.factor_table, condition=query)
            return result
        except Exception as e:
            self.logger.error(f"删除因子失败: {e}")
            return False
