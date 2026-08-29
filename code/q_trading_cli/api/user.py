"""
Author: liguoqiang
Date: 2026-06-27
Description: 用户管理 API — 注册、登录、登出、注销账号。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class UserApi:
    """用户管理 API 客户端。

    封装 /api/user/* 接口：
    - register: 用户注册
    - login: 用户登录（成功后自动设置 token）
    - logout: 退出登录（自动清除 token）
    - delete_account: 注销账号
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化用户 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def register(
        self,
        account: str,
        password: str,
        phone: str = "",
        email: str = "",
    ) -> str:
        """用户注册（密码使用 SHA256 带盐加密存储）。

        :param account: 用户账号
        :param password: 密码
        :param phone: 手机号（可选）
        :param email: 邮箱（可选）
        :return: 注册成功提示信息
        """
        body: dict[str, str] = {
            "account": account,
            "password": password,
        }
        if phone:
            body["phone"] = phone
        if email:
            body["email"] = email
        result: Any = self._client.post("/api/user/register", body)
        return str(result)

    def login(self, account: str, password: str) -> dict[str, Any]:
        """用户登录，返回包含 token 等登录信息的字典。

        注意：不再自动设置 token 到 ApiClient 单例上，
        调用方应自行将 token 保存到 app.storage.user["token"]，
        以支持多用户会话隔离。

        :param account: 用户账号
        :param password: 密码
        :return: 包含 token 等登录信息的字典
        """
        body: dict[str, str] = {
            "account": account,
            "password": password,
        }
        result: dict[str, Any] = self._client.post("/api/user/login", body)
        return result

    def logout(self) -> str:
        """退出登录，通知后端清除 Redis 中的 token。

        注意：不再自动清除 ApiClient 单例上的 token，
        调用方应自行清除 app.storage.user["token"]。

        :return: 操作结果提示信息
        """
        result: Any = self._client.post("/api/user/logout")
        return str(result)

    def delete_account(self, password: str) -> str:
        """注销账号，验证密码后级联删除用户、股票池、策略、黑名单。

        :param password: 当前密码，用于确认身份
        :return: 操作结果提示信息
        """
        body: dict[str, str] = {"password": password}
        result: Any = self._client.delete("/api/user/account", body)
        return str(result)
