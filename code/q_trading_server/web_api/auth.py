"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-06-23 00:00:00
Description: 认证模块 - 登录验证 + FastAPI 依赖注入
"""

import hashlib
import logging
import time
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from db.mongo.mongo_user_impl import MongoUserImpl
from app_context import AppContext

logger = logging.getLogger(__name__)

# 注册到 OpenAPI schema，Swagger UI 的 "Authorize" 按钮据此自动附加 Authorization header
security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)

# 密码加密盐值
_PASSWORD_SALT = "q_share_pwd_salt"


async def get_optional_current_user_with_role(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
) -> tuple[str | None, int]:
    """可选依赖：返回当前用户ID和角色，如果未提供 token，则默认为普通用户。"""
    if credentials is None:
        return None, 1
    return _resolve_token(credentials)


def hash_password(password: str) -> str:
    """带盐 SHA256 加密密码

    :param password: 明文密码
    :return: 64 位十六进制密文
    """
    raw = f"{password}:{_PASSWORD_SALT}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _hash_token(account: str, timestamp: str) -> str:
    """生成 user token"""
    raw = f"{account}:{timestamp}:q_share_token_salt"
    return hashlib.sha256(raw.encode()).hexdigest()


def verify_login(account: str, password: str) -> tuple[bool, Optional[str], Optional[str], int]:
    """验证登录 — 查询数据库比对带盐 SHA256 密文，成功后生成 token 存入 Redis

    :param account: 用户账号
    :param password: 明文密码
    :return: (成功, user_id, token, role)
    """
    impl = MongoUserImpl()
    res, results = impl.query_user_by_account(account)
    if not res or results is None or len(results) == 0:
        logger.warning("登录失败: 账号不存在, account=%s", account)
        return False, None, None, 1
    user = results[0]
    if user.get("password", "") != hash_password(password):
        logger.warning("登录失败: 密码错误, account=%s", account)
        return False, None, None, 1
    user_id = str(user.get("_id", ""))
    role = user.get("role", 1)
    # 设置登录状态
    impl.set_login_status(account, True)
    impl.set_online_status(account, True)
    # 生成 token 并存入 Redis，值格式: "user_id:role"
    # 管理员(role==0)永不过期(ex=None)，普通用户 5 天过期
    timestamp = str(int(time.time()))
    token = _hash_token(account, timestamp)
    AppContext().redis_exec.set_key_string(
        f"user_token:{token}",
        f"{user_id}:{role}",
        ex=None if role == 0 else 86400 * 5,
    )
    logger.info("登录成功: account=%s, user_id=%s, role=%s", account, user_id, role)
    return True, user_id, token, role


def _resolve_token(credentials: HTTPAuthorizationCredentials) -> tuple[str, int]:
    """从 token 解析用户 ID 和角色

    :param credentials: HTTPBearer 自动解析的凭证对象
    :return: (user_id, role)
    :raises HTTPException: 401 token 无效
    """
    token = credentials.credentials
    value = AppContext().redis_exec.get_key_string(f"user_token:{token}")
    if not value:
        raise HTTPException(status_code=401, detail="token 已过期或无效")
    # 兼容旧格式 "user_id" 和新格式 "user_id:role"
    if ":" in value:
        parts = value.split(":", 1)
        return parts[0], int(parts[1])
    return value, 1


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """FastAPI 依赖：从 Authorization Header 解析当前用户 ID

    用法:
        @router.get("/xxx")
        async def xxx(user_id: str = Depends(get_current_user)):
            ...

    通过 HTTPBearer 安全方案注册到 OpenAPI schema，
    Swagger UI 的 "Authorize" 按钮会正确附加 Authorization header。

    :param credentials: HTTPBearer 自动解析的凭证对象
    :return: 当前用户 ID
    :raises HTTPException: 401 未登录或 token 无效
    """
    user_id, _ = _resolve_token(credentials)
    return user_id


async def get_current_user_with_role(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> tuple[str, int]:
    """FastAPI 依赖：从 Authorization Header 解析当前用户 ID 和角色

    用法:
        @router.get("/xxx")
        async def xxx(user_info: tuple[str, int] = Depends(get_current_user_with_role)):
            user_id, role = user_info
            ...

    通过 HTTPBearer 安全方案注册到 OpenAPI schema。

    :param credentials: HTTPBearer 自动解析的凭证对象
    :return: (user_id, role)，role 为 0 表示管理员
    :raises HTTPException: 401 token 无效
    """
    return _resolve_token(credentials)


async def require_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """FastAPI 依赖：验证当前用户为管理员（role == 0），否则返回 403

    用法:
        @router.post("/xxx")
        async def xxx(user_id: str = Depends(require_admin)):
            ...

    :param credentials: HTTPBearer 自动解析的凭证对象
    :return: 当前管理员用户 ID
    :raises HTTPException: 401 token 无效, 403 非管理员
    """
    user_id, role = _resolve_token(credentials)
    if role != 0:
        raise HTTPException(status_code=403, detail="权限不足，仅管理员可执行此操作")
    return user_id
