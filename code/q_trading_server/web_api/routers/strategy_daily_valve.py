"""
Author: liguoqiang
Date: 2026-10-13 17:00:00
LastEditors: liguoqiang
LastEditTime: 2026-10-13 17:00:00
Description: 每日个股阀门路由 — 盘后阀门记录的批量保存、查询与删除

写入方是 q_trading_work 的盘后定时任务（用 admin token），
读取方是同一进程的盘前任务，因此全部端点要求管理员权限。
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from db.mongo.mongo_strategy_daily_valve_impl import MongoStrategyDailyValveImpl
from web_api.auth import require_admin
from web_api.models import (
    ApiResponse,
    StrategyDailyValveCreateRequest,
    StrategyDailyValveItem,
)

router = APIRouter(prefix="/api/strategy_daily_valve", tags=["每日个股阀门"])

# 阀门上限，与 q_trading_work/strategy/base_strategy.py::VALVE_MAX 口径一致
MAX_VALVE: float = 5.0


def _validate_valve(value: float, name: str) -> float:
    """校验并归一化阀门幅度

    :raises HTTPException: 400 值为负或超出上限
    """
    if value < 0 or value > MAX_VALVE:
        raise HTTPException(
            status_code=400, detail=f"{name} 应在 0~{MAX_VALVE} 之间"
        )
    return round(float(value), 4)


def _to_item(record: dict, user_strategy_id: str = "") -> StrategyDailyValveItem:
    """把数据库记录转换成响应模型（新增字段只需改这一处）。"""
    return StrategyDailyValveItem(
        id=str(record.get("_id", "")),
        user_strategy_id=record.get("user_strategy_id", user_strategy_id),
        stock_code=record.get("stock_code", ""),
        trade_date=record.get("trade_date", ""),
        buy_valve=record.get("buy_valve", 0.0) or 0.0,
        sell_valve=record.get("sell_valve", 0.0) or 0.0,
        support_break_count=int(record.get("support_break_count", 0) or 0),
        resistance_break_count=int(record.get("resistance_break_count", 0) or 0),
        reason=record.get("reason", ""),
        update_time=record.get("update_time", ""),
    )


@router.post("/add", response_model=ApiResponse[list[StrategyDailyValveItem]])
def add_daily_valves(
    req: StrategyDailyValveCreateRequest,
    _admin_id: str = Depends(require_admin),
):
    """批量保存当日个股阀门（按 user_strategy_id + stock_code + trade_date 覆盖）。"""
    if not req.user_strategy_id:
        raise HTTPException(status_code=400, detail="user_strategy_id 不能为空")
    impl = MongoStrategyDailyValveImpl()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    saved: list[StrategyDailyValveItem] = []
    failed: list[str] = []
    for item in req.items:
        if not item.stock_code or not item.trade_date:
            failed.append(item.stock_code or "<empty>")
            continue
        record: dict[str, Any] = {
            "user_strategy_id": req.user_strategy_id,
            "stock_code": item.stock_code,
            "trade_date": item.trade_date,
            "buy_valve": _validate_valve(item.buy_valve, "buy_valve"),
            "sell_valve": _validate_valve(item.sell_valve, "sell_valve"),
            "support_break_count": int(item.support_break_count),
            "resistance_break_count": int(item.resistance_break_count),
            "reason": item.reason,
            "update_time": now,
        }
        ok, record_id = impl.upsert_valve(record)
        if ok:
            record["_id"] = record_id or ""
            saved.append(_to_item(record, req.user_strategy_id))
        else:
            failed.append(item.stock_code)

    if failed and not saved:
        raise HTTPException(status_code=500, detail=f"保存每日阀门失败: {failed}")
    message = f"保存成功 {len(saved)} 条"
    if failed:
        message += f"，失败 {len(failed)} 条: {failed}"
    return ApiResponse(data=saved, message=message)


@router.get("/list", response_model=ApiResponse[list[StrategyDailyValveItem]])
def list_daily_valves(
    user_strategy_id: str = Query(..., description="用户策略 ID"),
    trade_date: str = Query(default="", description="交易日 YYYY-MM-DD，空表示不过滤（按日期倒序）"),
    _admin_id: str = Depends(require_admin),
):
    """查询某用户策略的每日阀门记录。"""
    impl = MongoStrategyDailyValveImpl()
    ok, records = impl.query_valves(user_strategy_id, trade_date)
    if not ok:
        raise HTTPException(status_code=500, detail="查询每日阀门失败")
    items: list[StrategyDailyValveItem] = [
        _to_item(r, user_strategy_id) for r in (records or [])
    ]
    return ApiResponse(data=items, message="查询成功")


@router.delete("/{user_strategy_id}", response_model=ApiResponse[str])
def delete_daily_valves(
    user_strategy_id: str,
    _admin_id: str = Depends(require_admin),
):
    """删除某用户策略的全部阀门记录。"""
    impl = MongoStrategyDailyValveImpl()
    ok = impl.delete_by_user_strategy(user_strategy_id)
    return ApiResponse(data="ok" if ok else "none", message="删除成功" if ok else "无可删除记录")
