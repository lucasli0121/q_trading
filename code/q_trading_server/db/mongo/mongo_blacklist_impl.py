"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-06-21 20:00:00
Description: 股票黑名单表 MongoDB 操作实现
"""

# coding="utf8"

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Hashable

from pymongo import UpdateOne
from app_context import AppContext


def _normalize_code(code: str) -> str:
    """提取股票代码中的纯数字部分，兼容 xxx、xxx.SH、xxx.SZ、shxxx、szxxx 格式。"""
    m = re.search(r"\d+", code)
    return m.group() if m else code


def _code_regex(code: str) -> dict[str, Any]:
    """返回 code 字段的正则匹配条件。"""
    pure = _normalize_code(code)
    return {"$regex": f"^(sh|sz)?{pure}(\\.(SH|SZ))?$"}


def _codes_or(codes: list[str]) -> dict[str, Any]:
    """返回多个 code 的 $or 正则匹配条件。"""
    return {"$or": [{"code": _code_regex(c)} for c in codes]}


class MongoBlacklistImpl:
    """股票黑名单表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.blacklist_table = self.mongo_exec.db["blacklist_tbl"]

    def add_to_blacklist(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """添加股票到黑名单
        :param data: 黑名单信息字典，需包含 user_id 和 code
        :return: 成功返回 True 和记录 ID，否则返回 False 和 None
        """
        try:
            data = data.copy()
            user_id = data.get("user_id", "")
            code = data.get("code", "")
            if not user_id or not code:
                self.logger.error("user_id or code is empty.")
                return False, None
            if "id" in data:
                del data["id"]
            filter: dict[str, Any] = {"user_id": user_id, "code": _code_regex(code)}
            result = self.blacklist_table.update_one(filter, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None
        except Exception as e:
            self.logger.error(f"添加黑名单失败: {e}")
            return False, None

    def batch_add_to_blacklist(self, records: Sequence[Mapping[Hashable, Any]]) -> bool:
        """批量添加股票到黑名单
        :param records: 黑名单信息列表
        :return: 成功返回 True，否则返回 False
        """
        try:
            requests = []
            for data in records:
                data = dict(data)
                data = data.copy()
                if "id" in data:
                    del data["id"]
                user_id = data["user_id"]
                code = data["code"]
                requests.append(
                    UpdateOne(
                        {"user_id": user_id, "code": _code_regex(code)},
                        {"$set": {k: v for k, v in data.items() if k != "id"}},
                        upsert=True,
                    )
                )
            result = self.blacklist_table.bulk_write(requests, ordered=False)
            self.logger.info(
                "批量upsert黑名单: matched=%d, modified=%d, upserted=%d",
                result.matched_count, result.modified_count, result.upserted_count,
            )
            return result.acknowledged
        except Exception as e:
            self.logger.error(f"批量添加黑名单失败: {e}")
            return False

    def remove_from_blacklist(self, user_id: str, code: str) -> bool:
        """从黑名单中移除股票
        :param user_id: 用户 ID
        :param code: 股票代码
        :return: 成功返回 True，否则返回 False
        """
        try:
            query: dict[str, Any] = {"user_id": user_id, "code": _code_regex(code)}
            result = self.mongo_exec.delete(table=self.blacklist_table, condition=query)
            return result
        except Exception as e:
            self.logger.error(f"移除黑名单失败: {e}")
            return False

    def batch_remove_from_blacklist(self, user_id: str, codes: Sequence[str]) -> bool:
        """批量从黑名单中移除股票
        :param user_id: 用户 ID
        :param codes: 股票代码列表
        :return: 成功返回 True，否则返回 False
        """
        try:
            query: dict[str, Any] = {"user_id": user_id, **_codes_or(list(codes))}
            result = self.mongo_exec.delete(table=self.blacklist_table, condition=query)
            return result
        except Exception as e:
            self.logger.error(f"批量移除黑名单失败: {e}")
            return False

    def query_blacklist_by_user(self, user_id: str) -> tuple[bool, Any | None]:
        """查询用户的全部黑名单股票
        :param user_id: 用户 ID
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"user_id": user_id}
            return self.mongo_exec.query_by_condition(
                table=self.blacklist_table,
                condition=query,
                sort={"code": 1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"查询黑名单失败: {e}")
            return False, None

    def is_stock_blacklisted(self, user_id: str, code: str) -> tuple[bool, bool]:
        """检查某只股票是否已在用户黑名单中
        :param user_id: 用户 ID
        :param code: 股票代码
        :return: (操作成功, 是否在黑名单中)
        """
        try:
            query: dict[str, Any] = {"user_id": user_id, "code": _code_regex(code)}
            success, result = self.mongo_exec.query_by_condition(
                table=self.blacklist_table,
                condition=query,
                sort=None,
                skip=None,
                limit=None,
            )
            if success:
                return True, result is not None and len(result) > 0
            return False, False
        except Exception as e:
            self.logger.error(f"检查黑名单失败: {e}")
            return False, False
