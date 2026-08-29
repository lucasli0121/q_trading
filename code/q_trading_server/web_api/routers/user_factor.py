"""
Author: liguoqiang
Date: 2026-08-11
Description: 用户因子管理路由 — 每个用户对因子的自定义参数
    管理员具有所有操作，普通登录用户仅能操作自己的因子
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from db.mongo.mongo_user_factor_impl import MongoUserFactorImpl
from web_api.auth import get_current_user, get_current_user_with_role
from web_api.models import (
    ApiResponse,
    UserFactorCreateRequest,
    UserFactorItem,
    UserFactorUpdateRequest,
)

router = APIRouter(prefix="/api/user_factor", tags=["用户因子管理"])


def _verify_ownership(impl: MongoUserFactorImpl, user_factor_id: str, user_id: str, role: int) -> dict:
    """验证用户因子归属并返回记录

    管理员（role == 0）可以操作所有用户因子，跳过归属校验。

    :param impl: MongoUserFactorImpl 实例
    :param user_factor_id: 用户因子 ID
    :param user_id: 当前用户 ID
    :param role: 当前用户角色（0=管理员）
    :return: 用户因子记录字典
    :raises HTTPException: 404 记录不存在或 403 不属于当前用户
    """
    res, records = impl.query_user_factor_by_id(user_factor_id)
    if not res or not records or len(records) == 0:
        raise HTTPException(status_code=404, detail="用户因子不存在")
    record = records[0]
    if role != 0 and record.get("user_id", "") != user_id:
        raise HTTPException(status_code=403, detail="无权操作此用户因子")
    return record


@router.post("/create", response_model=ApiResponse[UserFactorItem])
async def create_user_factor(
    req: UserFactorCreateRequest, user_id: str = Depends(get_current_user)
):
    """创建用户因子关联（同一用户不能重复关联同一因子）"""
    impl = MongoUserFactorImpl()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok, record_id, error = impl.insert_or_update_user_factor({
        "factor_id": req.factor_id,
        "user_id": user_id,
        "factor_params": req.factor_params,
        "create_time": now,
    }, insert_only=True)
    if error == "duplicate_user_factor":
        raise HTTPException(status_code=409, detail="已关联该因子，请勿重复创建")
    if not ok:
        raise HTTPException(status_code=500, detail="创建用户因子失败")
    return ApiResponse(
        data=UserFactorItem(
            id=record_id or "",
            factor_id=req.factor_id,
            user_id=user_id,
            factor_params=req.factor_params,
            create_time=now,
        ),
        message="创建成功",
    )


@router.delete("/{id}", response_model=ApiResponse[str])
async def delete_user_factor(
    id: str, user_info: tuple[str, int] = Depends(get_current_user_with_role)
):
    """删除用户因子关联"""
    user_id, role = user_info
    impl = MongoUserFactorImpl()
    _verify_ownership(impl, id, user_id, role)
    deleted = impl.delete_user_factor(id)
    if not deleted:
        raise HTTPException(status_code=500, detail="删除失败")
    return ApiResponse(data=id, message="删除成功")


@router.patch("/{id}", response_model=ApiResponse[str])
async def update_user_factor(
    id: str,
    req: UserFactorUpdateRequest,
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """更新用户因子关联（因子运行参数）"""
    user_id, role = user_info
    impl = MongoUserFactorImpl()
    _verify_ownership(impl, id, user_id, role)
    update_data: dict[str, object] = {}
    if req.factor_params is not None:
        update_data["factor_params"] = req.factor_params
    if not update_data:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    ok = impl.update_user_factor(id, update_data)
    if not ok:
        raise HTTPException(status_code=500, detail="更新失败")
    return ApiResponse(data=id, message="更新成功")


@router.get("/list", response_model=ApiResponse[list[UserFactorItem]])
async def list_user_factors(user_id: str = Depends(get_current_user)):
    """查询当前用户的所有因子关联（需登录）"""
    impl = MongoUserFactorImpl()
    res, records = impl.query_user_factors_by_user(user_id)
    if not res:
        raise HTTPException(status_code=500, detail="查询失败")
    result = [
        UserFactorItem(
            id=str(r.get("_id", "")),
            factor_id=r.get("factor_id", ""),
            user_id=r.get("user_id", ""),
            factor_params=r.get("factor_params", {}) or {},
            create_time=r.get("create_time", ""),
        )
        for r in (records or [])
    ]
    return ApiResponse(data=result)


@router.get("/all", response_model=ApiResponse[list[UserFactorItem]])
async def list_all_user_factors(
    skip: int = Query(default=0, description="分页跳过条数"),
    limit: int = Query(default=0, description="分页限制条数，0 表示不限制"),
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """查询所有用户因子关联（仅管理员）"""
    user_id, role = user_info
    impl = MongoUserFactorImpl()
    if role != 0:
        raise HTTPException(status_code=403, detail="权限不足，仅管理员可执行此操作")
    res, records = impl.query_all_user_factors(skip=skip, limit=limit)
    if not res:
        raise HTTPException(status_code=500, detail="查询失败")
    result = [
        UserFactorItem(
            id=str(r.get("_id", "")),
            factor_id=r.get("factor_id", ""),
            user_id=r.get("user_id", ""),
            factor_params=r.get("factor_params", {}) or {},
            create_time=r.get("create_time", ""),
        )
        for r in (records or [])
    ]
    return ApiResponse(data=result)


@router.get("/{id}", response_model=ApiResponse[UserFactorItem])
async def get_user_factor(
    id: str, user_info: tuple[str, int] = Depends(get_current_user_with_role)
):
    """查询单个用户因子详情"""
    user_id, role = user_info
    impl = MongoUserFactorImpl()
    record = _verify_ownership(impl, id, user_id, role)
    return ApiResponse(
        data=UserFactorItem(
            id=str(record.get("_id", "")),
            factor_id=record.get("factor_id", ""),
            user_id=record.get("user_id", ""),
            factor_params=record.get("factor_params", {}) or {},
            create_time=record.get("create_time", ""),
        )
    )
