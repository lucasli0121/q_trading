"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-06-21 20:00:00
Description: 用户表 MongoDB 操作实现
"""

# coding="utf8"

import logging
from bson import ObjectId
from typing import Any

from app_context import AppContext


class MongoUserImpl:
    """用户表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.user_table = self.mongo_exec.db["user_tbl"]

    def insert_or_update_user(
        self, data: dict[str, Any], insert_only: bool = False
    ) -> tuple[bool, str | None, str | None]:
        """插入或更新用户信息

        :param data: 用户信息字典，account 为唯一标识
        :param insert_only: 为 True 时仅执行新增，若账号已存在则返回冲突错误
        :return: (ok, record_id_or_None, error_or_None)
                 error 可能的值: None（无错误）、"duplicate_account"（账号重复）
        """
        try:
            data = data.copy()
            account = data.get("account", "")
            if not account:
                self.logger.error("account is empty.")
                return False, None, None
            if "id" in data:
                del data["id"]

            if insert_only:
                existing = self.user_table.find_one({"account": account})
                if existing:
                    return False, None, "duplicate_account"

            filter: dict[str, Any] = {"account": account}
            result = self.user_table.update_one(filter, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None, None
        except Exception as e:
            self.logger.error(f"插入或更新用户信息失败: {e}")
            return False, None, None

    def query_user_by_account(self, account: str) -> tuple[bool, Any | None]:
        """按账号查询用户
        :param account: 用户账号
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"account": account}
            return self.mongo_exec.query_by_condition(
                table=self.user_table,
                condition=query,
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按账号查询用户失败: {e}")
            return False, None

    def query_user_by_id(self, user_id: str) -> tuple[bool, Any | None]:
        """按 _id 查询用户
        :param user_id: 用户 _id 字符串
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"_id": ObjectId(user_id)}
            return self.mongo_exec.query_by_condition(
                table=self.user_table,
                condition=query,
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按 ID 查询用户失败: {e}")
            return False, None

    def query_user_by_email(self, email: str) -> tuple[bool, Any | None]:
        """按邮箱查询用户
        :param email: 用户邮箱
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"email": email}
            return self.mongo_exec.query_by_condition(
                table=self.user_table,
                condition=query,
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按邮箱查询用户失败: {e}")
            return False, None

    def query_user_by_phone(self, phone: str) -> tuple[bool, Any | None]:
        """按手机号查询用户
        :param phone: 用户手机号
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"phone": phone}
            return self.mongo_exec.query_by_condition(
                table=self.user_table,
                condition=query,
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按手机号查询用户失败: {e}")
            return False, None

    def delete_user(self, account: str) -> bool:
        """按账号删除用户
        :param account: 用户账号
        :return: 成功返回 True，否则返回 False
        """
        try:
            query: dict[str, Any] = {"account": account}
            result = self.mongo_exec.delete(table=self.user_table, condition=query)
            return result
        except Exception as e:
            self.logger.error(f"删除用户失败: {e}")
            return False

    def set_login_status(self, account: str, has_login: bool) -> bool:
        """设置用户登录状态
        :param account: 用户账号
        :param has_login: True 表示已登录，False 表示已退出
        :return: 成功返回 True，否则返回 False
        """
        try:
            result = self.user_table.update_one(
                {"account": account},
                {"$set": {"has_login": has_login}},
            )
            return result.modified_count > 0
        except Exception as e:
            self.logger.error(f"设置登录状态失败: {e}")
            return False

    def set_online_status(self, account: str, is_online: bool) -> bool:
        """设置用户在线状态
        :param account: 用户账号
        :param is_online: True 表示在线，False 表示离线
        :return: 成功返回 True，否则返回 False
        """
        from datetime import datetime

        try:
            online_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if is_online else ""
            result = self.user_table.update_one(
                {"account": account},
                {"$set": {"is_online": is_online, "online_time": online_time}},
            )
            return result.modified_count > 0 or result.upserted_id is not None
        except Exception as e:
            self.logger.error(f"设置在线状态失败: {e}")
            return False
