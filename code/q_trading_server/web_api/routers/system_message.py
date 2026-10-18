"""
Author: liguoqiang
Date: 2026-08-11
LastEditors: liguoqiang
LastEditTime: 2026-09-30
Description: 系统消息管理路由 — 创建、查询、删除系统消息，以及用户已读标记
"""

from datetime import datetime
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from db.mongo.mongo_system_message_impl import MongoSystemMessageImpl
from web_api.auth import get_current_user, require_admin
from web_api.models import (
    ApiResponse,
    SystemMessageCreateRequest,
    SystemMessageItem,
    SystemMessageReadRequest,
)

router = APIRouter(
    prefix="/api/system_message",
    tags=["系统消息"],
    dependencies=[Depends(get_current_user)],
)


def _to_item(record: dict[str, Any], user_id: str) -> SystemMessageItem:
    """把数据库文档转成响应对象，并回填「当前用户读状态」。

    「已读」与「历史」是同一个状态：``is_read`` 为 True 的消息即归入该用户的
    历史消息，前端据此把列表分成未读 / 历史两段。

    :param record: MongoDB 文档
    :param user_id: 当前用户 ID（用于判断 is_read）
    :return: 响应对象
    """
    is_read, read_count = MongoSystemMessageImpl.read_state(record, user_id)
    return SystemMessageItem(
        id=str(record.get("_id", "")),
        title=record.get("title", ""),
        message=record.get("message", ""),
        create_id=record.get("create_id", ""),
        user_ids=record.get("user_ids", []),
        create_time=record.get("create_time", ""),
        is_read=is_read,
        read_count=read_count,
    )


@router.post("/create", response_model=ApiResponse[str])
def create_system_message(
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
        "read_user_ids": [],
    }
    ok, inserted_id = impl.add(data)
    if not ok:
        raise HTTPException(status_code=500, detail="创建系统消息失败")
    return ApiResponse(data=inserted_id, message="创建成功")


@router.get("/list", response_model=ApiResponse[list[SystemMessageItem]])
def list_system_messages(
    skip: int = Query(default=0, description="分页跳过条数"),
    limit: int = Query(default=0, description="分页限制条数，0 表示不限制"),
    user_id: str = Depends(require_admin),
):
    """查询全部系统消息（仅管理员）"""
    impl = MongoSystemMessageImpl()
    res, records = impl.query_all(skip=skip, limit=limit)
    if not res:
        raise HTTPException(status_code=500, detail="查询系统消息失败")
    return ApiResponse(data=[_to_item(r, user_id) for r in (records or [])])


@router.get("/user_messages", response_model=ApiResponse[list[SystemMessageItem]])
def get_user_messages(
    user_id: str = Depends(get_current_user),
):
    """查询当前用户可收到的系统消息（广播 + 定向推送）

    每条带 ``is_read``：未读的亮角标，已读的归入历史消息。
    """
    impl = MongoSystemMessageImpl()
    res, records = impl.query_user_messages(user_id)
    if not res:
        raise HTTPException(status_code=500, detail="查询系统消息失败")
    return ApiResponse(data=[_to_item(r, user_id) for r in (records or [])])


@router.post("/read", response_model=ApiResponse[str])
def mark_system_message_read(
    req: SystemMessageReadRequest,
    user_id: str = Depends(get_current_user),
):
    """把一条系统消息标记为当前用户已读（幂等）

    用户点开单条消息即调用本接口。只能标记自己可见的消息，
    广播消息（user_ids 为空）与定向推送给自己的消息均可标记。
    """
    impl = MongoSystemMessageImpl()
    if not ObjectId.is_valid(req.id):
        raise HTTPException(status_code=400, detail="系统消息 ID 格式不正确")
    res, records = impl.query_by_id(req.id)
    if not res:
        raise HTTPException(status_code=500, detail="查询系统消息失败")
    record: dict[str, Any] | None = (records or [None])[0]
    # 不可见与不存在都按 404 处理，避免通过接口探测别人的定向消息是否存在
    if record is None or not impl.is_visible_to(record, user_id):
        raise HTTPException(status_code=404, detail="系统消息不存在")
    if not impl.mark_read(req.id, user_id):
        raise HTTPException(status_code=500, detail="标记已读失败")
    return ApiResponse(message="已标记为已读")


@router.delete("/delete", response_model=ApiResponse[str])
def delete_system_message(
    id: str = Query(..., description="系统消息 ID"),
    _user_id: str = Depends(require_admin),
):
    """删除系统消息（仅管理员）"""
    impl = MongoSystemMessageImpl()
    ok = impl.delete(id)
    if not ok:
        raise HTTPException(status_code=500, detail="删除系统消息失败")
    return ApiResponse(message="删除成功")
