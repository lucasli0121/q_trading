"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-06-22 15:00:00
Description: 用户管理路由 - 注册、登录、退出、注销
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from web_api.auth import get_current_user, get_optional_current_user_with_role, hash_password, security, verify_login

from db.mongo.mongo_blacklist_impl import MongoBlacklistImpl
from db.mongo.mongo_stock_pool_impl import MongoStockPoolImpl
from db.mongo.mongo_strategy_impl import MongoStrategyImpl
from db.mongo.mongo_user_impl import MongoUserImpl
from db.mongo.mongo_user_strategy_impl import MongoUserStrategyImpl
from pydantic import BaseModel, Field
from app_context import AppContext
from web_api.models import ApiResponse, LoginRequest, RegisterRequest, SetOnlineRequest, UserInfo

router = APIRouter(prefix="/api/user", tags=["用户管理"])


@router.post("/register", response_model=ApiResponse[str])
async def register(req: RegisterRequest):
    """用户注册（密码使用 SHA256 带盐加密存储）"""
    impl = MongoUserImpl()

    # 检查账号唯一性
    ok, existing = impl.query_user_by_account(req.account)
    if ok and existing and len(existing) > 0:
        raise HTTPException(status_code=409, detail="账号已存在")

    # 检查邮箱唯一性（非空时校验）
    if req.email:
        ok, existing = impl.query_user_by_email(req.email)
        if ok and existing and len(existing) > 0:
            raise HTTPException(status_code=409, detail="邮箱已被注册")

    # 检查手机号唯一性（非空时校验）
    if req.phone:
        ok, existing = impl.query_user_by_phone(req.phone)
        if ok and existing and len(existing) > 0:
            raise HTTPException(status_code=409, detail="手机号已被注册")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok, user_id, _ = impl.insert_or_update_user({
        "account": req.account,
        "password": hash_password(req.password),
        "role": req.role,
        "phone": req.phone,
        "email": req.email,
        "create_time": now,
        "update_time": now,
    }, insert_only=True)
    if not ok:
        raise HTTPException(status_code=500, detail="注册失败")
    return ApiResponse(data=user_id, message="注册成功")


@router.post("/login", response_model=ApiResponse[dict])
async def login(req: LoginRequest):
    """用户登录"""
    ok, user_id, token, role = verify_login(req.account, req.password)
    if not ok:
        raise HTTPException(status_code=401, detail="账号或密码错误")
    return ApiResponse(data={
        "token": token,
        "user_id": user_id,
        "account": req.account,
        "role": role
    }, message="登录成功")


@router.post("/logout", response_model=ApiResponse[str])
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    user_id: str = Depends(get_current_user),
):
    """退出登录 — 清除登录状态并删除 Redis token"""
    token = credentials.credentials
    # 查询用户 account 并设置 has_login=false
    user_impl = MongoUserImpl()
    res, users = user_impl.query_user_by_id(user_id)
    if res and users and len(users) > 0:
        account = users[0].get("account", "")
        if account:
            user_impl.set_login_status(account, False)
            user_impl.set_online_status(account, False)
    # 删除 Redis token
    AppContext().redis_exec.get_key_string(f"user_token:{token}", ifdel=True)
    return ApiResponse(message="已退出登录")


@router.post("/online", response_model=ApiResponse[str])
async def set_online_status(
    req: SetOnlineRequest,
    user_id: str = Depends(get_current_user),
):
    """设置用户在线状态 — 在线时自动记录上线时间，离线时清空"""
    user_impl = MongoUserImpl()
    res, users = user_impl.query_user_by_id(user_id)
    if not res or not users or len(users) == 0:
        raise HTTPException(status_code=404, detail="用户不存在")
    account = users[0].get("account", "")
    if not account:
        raise HTTPException(status_code=500, detail="用户账号异常")
    updated = user_impl.set_online_status(account, req.is_online)
    if not updated:
        raise HTTPException(status_code=500, detail="设置在线状态失败")
    status_text = "在线" if req.is_online else "离线"
    return ApiResponse(message=f"已设置为{status_text}")


class DeleteAccountRequest(BaseModel):
    """注销账号请求"""
    password: str = Field(..., description="当前密码，用于确认身份")


@router.delete("/account", response_model=ApiResponse[str])
async def delete_account(
    req: DeleteAccountRequest,
    user_id: str = Depends(get_current_user),
):
    """注销账号 — 验证密码后级联删除用户、股票池、策略、黑名单"""
    user_impl = MongoUserImpl()
    # 查询用户并验证密码
    res, users = user_impl.query_user_by_id(user_id)
    if not res or not users or len(users) == 0:
        raise HTTPException(status_code=404, detail="用户不存在")
    user = users[0]
    if user.get("password", "") != hash_password(req.password):
        raise HTTPException(status_code=401, detail="密码错误")
    account = user.get("account", "")

    # 级联删除：股票池
    pool_impl = MongoStockPoolImpl()
    res, pools = pool_impl.query_stock_pools_by_user(user_id)
    if res and pools:
        for pool in pools:
            pool_impl.delete_stock_pool(pool.get("name", ""))

    # 级联删除：用户策略关联
    user_strategy_impl = MongoUserStrategyImpl()
    user_strategy_impl.delete_user_strategies_by_user(user_id)

    # 级联删除：黑名单
    blacklist_impl = MongoBlacklistImpl()
    res, blacklist = blacklist_impl.query_blacklist_by_user(user_id)
    if res and blacklist:
        codes = [b.get("code", "") for b in blacklist]
        if codes:
            blacklist_impl.batch_remove_from_blacklist(user_id, codes)

    # 最后删除用户
    deleted = user_impl.delete_user(account)
    if not deleted:
        raise HTTPException(status_code=500, detail="注销失败")
    return ApiResponse(data=account, message="账号已注销")
