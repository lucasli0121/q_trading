"""
Author: liguoqiang
Date: 2026-06-22 15:00:00
LastEditors: liguoqiang
LastEditTime: 2026-06-22 15:00:00
Description: 股票黑名单管理路由
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from db.mongo.mongo_blacklist_impl import MongoBlacklistImpl
from db.mongo.mongo_stock_info_impl import MongoStockInfoImpl
from web_api.auth import get_current_user
from web_api.models import ApiResponse

router = APIRouter(prefix="/api/blacklist", tags=["黑名单管理"], dependencies=[Depends(get_current_user)])


class BlacklistAddRequest(BaseModel):
    """添加黑名单请求"""
    codes: list[str] = Field(..., description="股票代码列表")
    reason: str = Field(default="", description="拉黑原因")


class BlacklistRemoveRequest(BaseModel):
    """移除黑名单请求"""
    codes: list[str] = Field(..., description="股票代码列表")


class BlacklistInfo(BaseModel):
    """黑名单条目信息"""
    code: str = ""
    add_time: str = ""
    reason: str = ""


def _resolve_real_codes(codes: list[str]) -> list[str]:
    """批量查出代码在库中对应的规范代码。

    原写法在 for 循环里逐个 query_by_codes([code])，N 只股票就是 N 次往返；
    这里改成一次 $in 查询。
    :raises HTTPException: 查询失败 500；存在库中不存在的代码 400
    """
    stock_impl = MongoStockInfoImpl()
    ok, infos = stock_impl.query_by_codes(codes)
    if not ok:
        raise HTTPException(status_code=500, detail="查询股票信息失败")
    found = {r.get("code", ""): r for r in (infos or [])}
    missing = [c for c in codes if stock_impl.full_code(c) not in found]
    if missing:
        raise HTTPException(status_code=400, detail=f"股票不存在: {', '.join(missing)}")
    return [
        found[full].get("code", "")
        for full in (stock_impl.full_code(c) for c in codes)
        if full in found
    ]


@router.post("/add", response_model=ApiResponse[str])
def add_blacklist(
    req: BlacklistAddRequest,
    user_id: str = Depends(get_current_user),
):
    """添加股票到黑名单"""
    impl = MongoBlacklistImpl()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stock_real_codes = _resolve_real_codes(req.codes)

    records = [
        {"user_id": user_id, "code": c, "add_time": now, "reason": req.reason}
        for c in stock_real_codes
    ]
    ok = impl.batch_add_to_blacklist(records)
    if not ok:
        raise HTTPException(status_code=500, detail="添加黑名单失败")
    return ApiResponse(data=f"已添加 {len(stock_real_codes)} 只股票到黑名单", message="添加成功")


@router.post("/remove", response_model=ApiResponse[str])
def remove_blacklist(
    req: BlacklistRemoveRequest,
    user_id: str = Depends(get_current_user),
):
    stock_real_codes = _resolve_real_codes(req.codes)
    """从黑名单中移除股票"""
    impl = MongoBlacklistImpl()
    ok = impl.batch_remove_from_blacklist(user_id, stock_real_codes)
    if not ok:
        raise HTTPException(status_code=500, detail="移除黑名单失败")
    return ApiResponse(data=f"已移除 {len(stock_real_codes)} 只股票", message="移除成功")


@router.get("/list", response_model=ApiResponse[list[BlacklistInfo]])
def list_blacklist(user_id: str = Depends(get_current_user)):
    """查询用户的所有黑名单股票"""
    impl = MongoBlacklistImpl()
    res, records = impl.query_blacklist_by_user(user_id)
    if not res:
        raise HTTPException(status_code=500, detail="查询黑名单失败")
    result = [
        BlacklistInfo(
            code=r.get("code", ""),
            add_time=r.get("add_time", ""),
            reason=r.get("reason", ""),
        )
        for r in (records or [])
    ]
    return ApiResponse(data=result)


@router.get("/check", response_model=ApiResponse[dict])
def check_blacklist(
    code: str = Query(..., description="股票代码"),
    user_id: str = Depends(get_current_user),
):
    """检查某只股票是否在黑名单中"""
    impl = MongoBlacklistImpl()
    ok, is_blocked = impl.is_stock_blacklisted(user_id, code)
    if not ok:
        raise HTTPException(status_code=500, detail="查询失败")
    return ApiResponse(data={"code": code, "is_blacklisted": is_blocked})
