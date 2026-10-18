"""
Author: liguoqiang
Date: 2026-07-26 10:00:00
LastEditors: liguoqiang
LastEditTime: 2026-07-26 10:00:00
Description: 用户偏好设置路由 - 查询和更新用户偏好
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from web_api.auth import get_current_user, require_admin
from web_api.models import (
    ApiResponse,
    UserPreferenceRequest,
    UserPreferenceResponse,
)
from db.mongo.mongo_user_preference_impl import MongoUserPreferenceImpl

router = APIRouter(prefix="/api/user/preference", tags=["用户偏好"])

# 推送星级上限，与 dao/trade_signal_dao.py 中 star 字段口径一致（最大 5 星，保留一位小数）
MAX_PUSH_STAR: float = 5.0


def _normalize_push_min_star(value: float, name: str) -> float:
    """校验并归一化推送最小星级，返回保留一位小数后的值

    :raises HTTPException: 400 值超出 0~5
    """
    if value < 0 or value > MAX_PUSH_STAR:
        raise HTTPException(
            status_code=400, detail=f"{name} 应在 0~{MAX_PUSH_STAR} 之间"
        )
    return round(value, 1)


def _to_preference_response(pref: dict, user_id: str) -> UserPreferenceResponse:
    """把数据库记录转换成响应模型

    get / get_by_user / update 回读共三处映射，原先各自手写一遍字段，
    新增字段容易漏改（push_buy_min_star / push_sell_min_star 就是这么漏掉的），
    统一收在这里。
    """
    return UserPreferenceResponse(
        id=str(pref.get("_id", "")),
        user_id=pref.get("user_id", user_id),
        theme_mode=pref.get("theme_mode", "light"),
        enable_wx_push=pref.get("enable_wx_push", False),
        wx_push_url=pref.get("wx_push_url", ""),
        enable_phone_text=pref.get("enable_phone_text", False),
        phone=pref.get("phone", ""),
        push_buy_min_star=pref.get("push_buy_min_star", 0.0) or 0.0,
        push_sell_min_star=pref.get("push_sell_min_star", 0.0) or 0.0,
        update_time=pref.get("update_time", ""),
    )


@router.get("", response_model=ApiResponse[UserPreferenceResponse])
def get_preference(
    user_id: str = Depends(get_current_user),
):
    """查询当前用户的偏好设置，若未设置则返回默认值"""
    impl = MongoUserPreferenceImpl()
    ok, results = impl.query_preference_by_user_id(user_id)
    if ok and results and len(results) > 0:
        return ApiResponse(
            data=_to_preference_response(results[0], user_id),
            message="查询成功",
        )
    # 未设置时返回默认值
    return ApiResponse(
        data=UserPreferenceResponse(user_id=user_id),
        message="未设置偏好，返回默认值",
    )


@router.get("/{user_id}", response_model=ApiResponse[UserPreferenceResponse])
def get_preference_by_user(
    user_id: str,  admin_id = Depends(require_admin),
):
    """
    查询指定用户的偏好设置，若未设置则返回默认值
    需要admin权限
    """
    impl = MongoUserPreferenceImpl()
    ok, results = impl.query_preference_by_user_id(user_id)
    if ok and results and len(results) > 0:
        return ApiResponse(
            data=_to_preference_response(results[0], user_id),
            message="查询成功",
        )
    # 未设置时返回默认值
    return ApiResponse(
        data=UserPreferenceResponse(user_id=user_id),
        message="未设置偏好，返回默认值",
    )


@router.put("", response_model=ApiResponse[UserPreferenceResponse])
def update_preference(
    req: UserPreferenceRequest,
    user_id: str = Depends(get_current_user),
):
    """创建或更新当前用户的偏好设置"""
    # 先校验再落库，避免非法星级写进数据库
    push_buy_min_star = _normalize_push_min_star(
        req.push_buy_min_star, "push_buy_min_star"
    )
    push_sell_min_star = _normalize_push_min_star(
        req.push_sell_min_star, "push_sell_min_star"
    )

    impl = MongoUserPreferenceImpl()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok, _ = impl.insert_or_update_preference({
        "user_id": user_id,
        "theme_mode": req.theme_mode,
        "enable_wx_push": req.enable_wx_push,
        "wx_push_url": req.wx_push_url,
        "enable_phone_text": req.enable_phone_text,
        "phone": req.phone,
        "push_buy_min_star": push_buy_min_star,
        "push_sell_min_star": push_sell_min_star,
        "update_time": now,
    })
    if not ok:
        raise HTTPException(status_code=500, detail="保存偏好设置失败")

    # 回读确认
    ok, results = impl.query_preference_by_user_id(user_id)
    if ok and results and len(results) > 0:
        return ApiResponse(
            data=_to_preference_response(results[0], user_id),
            message="保存成功",
        )
    return ApiResponse(message="保存成功")
