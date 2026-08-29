"""
Author: liguoqiang
Date: 2026-08-01 00:00:00
LastEditors: liguoqiang
LastEditTime: 2026-08-01 00:00:00
Description: 交易信号管理路由 — 交易信号记录的增删查
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from db.mongo.mongo_trade_signal_impl import MongoTradeSignalImpl
from db.mongo.mongo_user_strategy_impl import MongoUserStrategyImpl
from web_api.auth import get_current_user_with_role
from web_api.models import ApiResponse, TradeSignalCreateRequest, TradeSignalItem

router = APIRouter(prefix="/api/trade_signal", tags=["交易信号管理"])


def _resolve_strategy_ids_from_user(user_id: str, role: int) -> list[str] | None:
    """通过用户 ID 查询该用户关联的所有 strategy_id

    管理员（role == 0）返回 None 表示不限制策略范围（查询所有）；
    普通用户返回该用户关联的 strategy_id 列表。

    :param user_id: 用户 ID
    :param role: 用户角色，0 为管理员
    :return: strategy_id 列表，管理员返回 None
    """
    if role == 0:
        return None
    impl = MongoUserStrategyImpl()
    res, records = impl.query_user_strategies_by_user(user_id)
    if not res or not records:
        return []
    return [r.get("strategy_id", "") for r in records]


@router.post("/add", response_model=ApiResponse[TradeSignalItem])
async def add_trade_signal(
    req: TradeSignalCreateRequest,
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """添加交易信号记录"""
    user_id, role = user_info
    # 非管理员需验证该 strategy_id 属于当前用户
    if role != 0:
        strategy_ids = _resolve_strategy_ids_from_user(user_id, role)
        if not strategy_ids or req.strategy_id not in strategy_ids:
            raise HTTPException(status_code=403, detail="无权操作此策略")
    impl = MongoTradeSignalImpl()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record = {
        "strategy_id": req.strategy_id,
        "stock_code": req.stock_code,
        "trade_price": req.trade_price,
        "profit_rate": req.profit_rate,
        "profit_amount": req.profit_amount,
        "action": req.action,
        "reason": req.reason,
        "create_time": now,
    }
    ok, record_id = impl.add_signal(record)
    if not ok:
        raise HTTPException(status_code=500, detail="添加交易信号失败")
    return ApiResponse(
        data=TradeSignalItem(
            id=record_id or "",
            strategy_id=req.strategy_id,
            stock_code=req.stock_code,
            trade_price=req.trade_price,
            profit_rate=req.profit_rate,
            profit_amount=req.profit_amount,
            action=req.action,
            reason=req.reason,
            create_time=now,
        ),
        message="添加成功",
    )


@router.delete("/{id}", response_model=ApiResponse[str])
async def delete_trade_signal(
    id: str,
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """删除交易信号记录"""
    impl = MongoTradeSignalImpl()
    deleted = impl.delete_signal(id)
    if not deleted:
        raise HTTPException(status_code=500, detail="删除交易信号失败")
    return ApiResponse(data=id, message="删除成功")


@router.get("/list", response_model=ApiResponse[list[TradeSignalItem]])
async def list_trade_signals(
    strategy_id: str = Query(default="", description="策略 ID（可选）"),
    start_time: str = Query(default="", description="创建开始时间 YYYY-MM-DD HH:MM:SS（可选）"),
    end_time: str = Query(default="", description="创建结束时间 YYYY-MM-DD HH:MM:SS（可选）"),
    action: str = Query(default="", description="买卖方向: 买入/卖出（可选）"),
    skip: int = Query(default=0, description="分页跳过条数"),
    limit: int = Query(default=0, description="分页限制条数，0 表示不限制"),
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """查询交易信号记录 — 管理员查所有，普通用户查自己策略的信号记录"""
    user_id, role = user_info
    impl = MongoTradeSignalImpl()
    if strategy_id:
        # 指定了策略 ID，直接按条件查询
        res, records = impl.query_signals(
            strategy_id=strategy_id,
            start_time=start_time,
            end_time=end_time,
            action=action,
            skip=skip,
            limit=limit,
        )
    else:
        # 未指定策略 ID：管理员查所有，普通用户查自己的策略
        strategy_ids = _resolve_strategy_ids_from_user(user_id, role)
        if strategy_ids is None:
            # 管理员：查询所有记录
            res, records = impl.query_signals(
                start_time=start_time,
                end_time=end_time,
                action=action,
                skip=skip,
                limit=limit,
            )
        elif not strategy_ids:
            return ApiResponse(data=[])
        else:
            res, records = impl.query_signals_by_strategy_ids(
                strategy_ids=strategy_ids,
                start_time=start_time,
                end_time=end_time,
                action=action,
                skip=skip,
                limit=limit,
            )
    if not res:
        raise HTTPException(status_code=500, detail="查询交易信号失败")
    rlist = [
        TradeSignalItem(
            id=str(r.get("_id", "")),
            strategy_id=r.get("strategy_id", ""),
            stock_code=r.get("stock_code", ""),
            trade_price=float(r.get("trade_price", 0.0) or 0.0),
            profit_rate=float(r.get("profit_rate", 0.0) or 0.0),
            profit_amount=float(r.get("profit_amount", 0.0) or 0.0),
            action=r.get("action", ""),
            reason=r.get("reason", ""),
            create_time=r.get("create_time", ""),
        )
        for r in (records or [])
    ]
    return ApiResponse(data=rlist)


@router.get("/latest", response_model=ApiResponse[TradeSignalItem])
async def get_latest_signal(
    strategy_id: str = Query(..., description="策略 ID"),
    stock_code: str = Query(..., description="股票代码"),
    action: str = Query(default="", description="买卖方向: 买入/卖出（可选）"),
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """查询最近一次交易信号 — 按策略 ID、股票代码、买卖方向查询最新一条"""
    user_id, role = user_info
    # 非管理员需验证该 strategy_id 属于当前用户
    if role != 0:
        strategy_ids = _resolve_strategy_ids_from_user(user_id, role)
        if not strategy_ids or strategy_id not in strategy_ids:
            raise HTTPException(status_code=403, detail="无权操作此策略")
    impl = MongoTradeSignalImpl()
    res, records = impl.query_latest_signal(
        strategy_id=strategy_id,
        stock_code=stock_code,
        action=action,
    )
    if not res:
        raise HTTPException(status_code=500, detail="查询交易信号失败")
    if not records or len(records) == 0:
        raise HTTPException(status_code=404, detail="未找到交易信号")
    r = records[0]
    return ApiResponse(
        data=TradeSignalItem(
            id=str(r.get("_id", "")),
            strategy_id=r.get("strategy_id", ""),
            stock_code=r.get("stock_code", ""),
            trade_price=float(r.get("trade_price", 0.0) or 0.0),
            profit_rate=float(r.get("profit_rate", 0.0) or 0.0),
            profit_amount=float(r.get("profit_amount", 0.0) or 0.0),
            action=r.get("action", ""),
            reason=r.get("reason", ""),
            create_time=r.get("create_time", ""),
        )
    )
