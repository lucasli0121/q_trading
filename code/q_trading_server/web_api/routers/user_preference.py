"""
Author: liguoqiang
Date: 2026-07-26 10:00:00
LastEditors: liguoqiang
LastEditTime: 2026-07-26 10:00:00
Description: 用户偏好设置路由 - 查询和更新用户偏好
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from web_api.auth import get_current_user, security
from web_api.models import (
    ApiResponse,
    UserPreferenceRequest,
    UserPreferenceResponse,
)
from db.mongo.mongo_user_preference_impl import MongoUserPreferenceImpl

router = APIRouter(prefix="/api/user/preference", tags=["用户偏好"])


@router.get("", response_model=ApiResponse[UserPreferenceResponse])
async def get_preference(
    user_id: str = Depends(get_current_user),
):
    """查询当前用户的偏好设置，若未设置则返回默认值"""
    impl = MongoUserPreferenceImpl()
    ok, results = impl.query_preference_by_user_id(user_id)
    if ok and results and len(results) > 0:
        pref = results[0]
        return ApiResponse(
            data=UserPreferenceResponse(
                id=str(pref.get("_id", "")),
                user_id=pref.get("user_id", user_id),
                theme_mode=pref.get("theme_mode", "light"),
                enable_wx_push=pref.get("enable_wx_push", False),
                wx_push_url=pref.get("wx_push_url", ""),
                enable_phone_text=pref.get("enable_phone_text", False),
                phone=pref.get("phone", ""),
                update_time=pref.get("update_time", ""),
            ),
            message="查询成功",
        )
    # 未设置时返回默认值
    return ApiResponse(
        data=UserPreferenceResponse(user_id=user_id),
        message="未设置偏好，返回默认值",
    )


@router.put("", response_model=ApiResponse[UserPreferenceResponse])
async def update_preference(
    req: UserPreferenceRequest,
    user_id: str = Depends(get_current_user),
):
    """创建或更新当前用户的偏好设置"""
    impl = MongoUserPreferenceImpl()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok, _ = impl.insert_or_update_preference({
        "user_id": user_id,
        "theme_mode": req.theme_mode,
        "enable_wx_push": req.enable_wx_push,
        "wx_push_url": req.wx_push_url,
        "enable_phone_text": req.enable_phone_text,
        "phone": req.phone,
        "update_time": now,
    })
    if not ok:
        raise HTTPException(status_code=500, detail="保存偏好设置失败")

    # 回读确认
    ok, results = impl.query_preference_by_user_id(user_id)
    if ok and results and len(results) > 0:
        pref = results[0]
        return ApiResponse(
            data=UserPreferenceResponse(
                id=str(pref.get("_id", "")),
                user_id=pref.get("user_id", user_id),
                theme_mode=pref.get("theme_mode", "light"),
                enable_wx_push=pref.get("enable_wx_push", False),
                wx_push_url=pref.get("wx_push_url", ""),
                enable_phone_text=pref.get("enable_phone_text", False),
                phone=pref.get("phone", ""),
                update_time=pref.get("update_time", ""),
            ),
            message="保存成功",
        )
    return ApiResponse(message="保存成功")
