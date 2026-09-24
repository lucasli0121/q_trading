"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-06-21 20:00:00
Description: 股票黑名单表 MongoDB 操作实现
"""

# coding="utf8"

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Hashable

from pymongo import UpdateOne
from app_context import AppContext
from utils import tools


def _full_code(code: str) -> str:
    """归一化为库中统一使用的完整代码格式：xxxx.SH / xxxx.SZ / xxxx.BJ

    写入与查询都用这个格式做等值匹配，不再使用 $regex。
    正则 ^(sh|sz)?600519(\\.(SH|SZ))?$ 开头是交替分支，MongoDB 提取不到固定前缀，
    配合 $or 时每个分支都要单独评估，{user_id:1, code:1} 索引完全用不上。
    """
    return tools.guess_stock_code_suffix(tools.pure_stock_code(code))


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
            full_code = _full_code(code)
            if not user_id or not full_code:
                self.logger.error("user_id or code is empty.")
                return False, None
            if "id" in data:
                del data["id"]
            data["code"] = full_code
            filter: dict[str, Any] = {"user_id": user_id, "code": full_code}
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
                full_code = _full_code(data["code"])
                if not full_code:
                    self.logger.warning("跳过 code 为空的记录")
                    continue
                data["code"] = full_code
                requests.append(
                    UpdateOne(
                        {"user_id": user_id, "code": full_code},
                        {"$set": {k: v for k, v in data.items() if k != "id"}},
                        upsert=True,
                    )
                )
            if not requests:
                self.logger.warning("批量upsert黑名单：无有效记录")
                return False
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
            full_code = _full_code(code)
            if not full_code:
                self.logger.error("code is empty.")
                return False
            query: dict[str, Any] = {"user_id": user_id, "code": full_code}
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
            full_codes = [c for c in (_full_code(c) for c in codes) if c]
            if not full_codes:
                return False
            query: dict[str, Any] = {"user_id": user_id, "code": {"$in": full_codes}}
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
            full_code = _full_code(code)
            if not full_code:
                return True, False
            query: dict[str, Any] = {"user_id": user_id, "code": full_code}
            success, result = self.mongo_exec.query_by_condition(
                table=self.blacklist_table,
                condition=query,
                sort=None,
                skip=None,
                limit=1,
            )
            if success:
                return True, result is not None and len(result) > 0
            return False, False
        except Exception as e:
            self.logger.error(f"检查黑名单失败: {e}")
            return False, False

    def query_blacklisted_codes(
        self, user_id: str, codes: Sequence[str]
    ) -> tuple[bool, set[str]]:
        """批量查出给定代码中哪些在该用户的黑名单里（一次查询）

        用于替换「循环里逐个 is_stock_blacklisted」的 N+1 写法。
        :param user_id: 用户 ID
        :param codes: 待检查的股票代码列表
        :return: (操作成功, 命中的完整代码集合)
        """
        try:
            full_codes = [c for c in (_full_code(c) for c in codes) if c]
            if not full_codes:
                return True, set()
            query: dict[str, Any] = {"user_id": user_id, "code": {"$in": full_codes}}
            success, records = self.mongo_exec.query_by_condition(
                table=self.blacklist_table,
                condition=query,
                sort=None,
                skip=None,
                limit=None,
            )
            if not success:
                return False, set()
            return True, {r.get("code", "") for r in (records or [])}
        except Exception as e:
            self.logger.error(f"批量检查黑名单失败: {e}")
            return False, set()
