"""
Author: liguoqiang
Date: 2026-08-11
LastEditors: liguoqiang
LastEditTime: 2026-08-11
Description: 系统消息管理路由 — 创建、查询、删除系统消息
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from db.mongo.mongo_system_message_impl import MongoSystemMessageImpl
from web_api.auth import get_current_user, require_admin
from web_api.models import ApiResponse, SystemMessageCreateRequest, SystemMessageItem

router = APIRouter(
    prefix="/api/system_message",
    tags=["系统消息"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/create", response_model=ApiResponse[str])
async def create_system_message(
    req: SystemMessageCreateRequest,
    user_id: str = Depends(require_admin),
):
    """创建系统消息（仅管理员）

    - user_ids 为空时，消息为广播，所有用户可见
    - user_ids 非空时，仅指定用户可见
    """
    impl = MongoSystemMessageImpl()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = {
        "title": req.title,
        "message": req.message,
        "create_id": user_id,
        "user_ids": req.user_ids,
        "create_time": now,
    }
    ok, inserted_id = impl.add(data)
    if not ok:
        raise HTTPException(status_code=500, detail="创建系统消息失败")
    return ApiResponse(data=inserted_id, message="创建成功")


@router.get("/list", response_model=ApiResponse[list[SystemMessageItem]])
async def list_system_messages(
    skip: int = Query(default=0, description="分页跳过条数"),
    limit: int = Query(default=0, description="分页限制条数，0 表示不限制"),
    _user_id: str = Depends(require_admin),
):
    """查询全部系统消息（仅管理员）"""
    impl = MongoSystemMessageImpl()
    res, records = impl.query_all(skip=skip, limit=limit)
    if not res:
        raise HTTPException(status_code=500, detail="查询系统消息失败")
    result = [
        SystemMessageItem(
            id=str(r.get("_id", "")),
            title=r.get("title", ""),
            message=r.get("message", ""),
            create_id=r.get("create_id", ""),
            user_ids=r.get("user_ids", []),
            create_time=r.get("create_time", ""),
        )
        for r in (records or [])
    ]
    return ApiResponse(data=result)


@router.get("/user_messages", response_model=ApiResponse[list[SystemMessageItem]])
async def get_user_messages(
    user_id: str = Depends(get_current_user),
):
    """查询当前用户可收到的系统消息（广播 + 定向推送）"""
    impl = MongoSystemMessageImpl()
    res, records = impl.query_user_messages(user_id)
    if not res:
        raise HTTPException(status_code=500, detail="查询系统消息失败")
    result = [
        SystemMessageItem(
            id=str(r.get("_id", "")),
            title=r.get("title", ""),
            message=r.get("message", ""),
            create_id=r.get("create_id", ""),
            user_ids=r.get("user_ids", []),
            create_time=r.get("create_time", ""),
        )
        for r in (records or [])
    ]
    return ApiResponse(data=result)


@router.delete("/delete", response_model=ApiResponse[str])
async def delete_system_message(
    id: str = Query(..., description="系统消息 ID"),
    _user_id: str = Depends(require_admin),
):
    """删除系统消息（仅管理员）"""
    impl = MongoSystemMessageImpl()
    ok = impl.delete(id)
    if not ok:
        raise HTTPException(status_code=500, detail="删除系统消息失败")
    return ApiResponse(message="删除成功")
