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


def _load_owned_strategy_ids(user_id: str, role: int) -> tuple[set[str], set[str]] | None:
    """一次性取出该用户拥有的 (用户策略 _id 集合, 全局策略 id 集合)

    管理员（role == 0）返回 None，表示不限制范围。
    只查一次库，供下面两个校验函数复用，避免每次校验都往返数据库。

    :param user_id: 用户 ID
    :param role: 用户角色，0 为管理员
    :return: (user_strategy_id 集合, strategy_id 集合)，管理员返回 None
    """
    if role == 0:
        return None
    impl = MongoUserStrategyImpl()
    res, records = impl.query_user_strategies_by_user(user_id)
    if not res or not records:
        return set(), set()
    us_ids = {str(r.get("_id", r.get("id", ""))) for r in records}
    s_ids = {r.get("strategy_id", "") for r in records}
    return us_ids, s_ids


def _resolve_user_strategy_ids_from_user(user_id: str, role: int) -> list[str] | None:
    """通过用户 ID 查询该用户关联的所有用户策略 _id

    管理员（role == 0）返回 None 表示不限制策略范围（查询所有）；
    普通用户返回该用户策略记录的 _id 列表。

    :param user_id: 用户 ID
    :param role: 用户角色，0 为管理员
    :return: user_strategy_id 列表，管理员返回 None
    """
    owned = _load_owned_strategy_ids(user_id, role)
    if owned is None:
        return None
    return sorted(owned[0])


def _assert_strategy_access(
    user_id: str, role: int, strategy_id: str = "", user_strategy_id: str = ""
) -> None:
    """校验指定策略是否属于当前用户，管理员跳过

    归属校验必须在「按 ID 直接查询」的分支里显式执行，
    否则任何登录用户只要拿到别人的 id 就能读取/删除其数据。

    :raises HTTPException: 403 不属于当前用户
    """
    owned = _load_owned_strategy_ids(user_id, role)
    if owned is None:
        return
    us_ids, s_ids = owned
    if user_strategy_id and user_strategy_id not in us_ids:
        raise HTTPException(status_code=403, detail="无权操作此用户策略")
    if strategy_id and strategy_id not in s_ids:
        raise HTTPException(status_code=403, detail="无权操作此策略")


@router.post("/add", response_model=ApiResponse[TradeSignalItem])
def add_trade_signal(
    req: TradeSignalCreateRequest,
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """添加交易信号记录"""
    user_id, role = user_info
    _assert_strategy_access(user_id, role, user_strategy_id=req.user_strategy_id)
    impl = MongoTradeSignalImpl()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record = {
        "strategy_id": req.strategy_id,
        "user_strategy_id": req.user_strategy_id,
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
            user_strategy_id=req.user_strategy_id,
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
def delete_trade_signal(
    id: str,
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """删除交易信号记录"""
    user_id, role = user_info
    impl = MongoTradeSignalImpl()
    # 先取记录做归属校验：原实现取了 user_info 却完全没用，任意登录用户可删他人记录
    res, records = impl.query_signal_by_id(id)
    if not res:
        raise HTTPException(status_code=500, detail="查询交易信号失败")
    if not records:
        raise HTTPException(status_code=404, detail="交易信号不存在")
    _assert_strategy_access(
        user_id, role, user_strategy_id=records[0].get("user_strategy_id", "")
    )
    deleted = impl.delete_signal(id)
    if not deleted:
        raise HTTPException(status_code=500, detail="删除交易信号失败")
    return ApiResponse(data=id, message="删除成功")


@router.get("/list", response_model=ApiResponse[list[TradeSignalItem]])
def list_trade_signals(
    stock_codes: list = Query(default=[], description="股票代码，默认为空，可多选"),
    strategy_id: str = Query(default="", description="策略 ID（可选）"),
    user_strategy_id: str = Query(default="", description="用户策略 ID（可选）"),
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
    if strategy_id or user_strategy_id:
        # 指定了策略 ID：先校验归属，否则可越权读取他人信号
        _assert_strategy_access(
            user_id, role, strategy_id=strategy_id, user_strategy_id=user_strategy_id
        )
        res, records = impl.query_signals(
            stock_codes=stock_codes,
            strategy_id=strategy_id,
            user_strategy_id=user_strategy_id,
            start_time=start_time,
            end_time=end_time,
            action=action,
            skip=skip,
            limit=limit,
        )
    else:
        # 未指定策略 ID：管理员查所有，普通用户查自己的策略
        user_strategy_ids = _resolve_user_strategy_ids_from_user(user_id, role)
        if user_strategy_ids is None:
            # 管理员：查询所有记录
            res, records = impl.query_signals(
                stock_codes=stock_codes,
                start_time=start_time,
                end_time=end_time,
                action=action,
                skip=skip,
                limit=limit,
            )
        elif not user_strategy_ids:
            return ApiResponse(data=[])
        else:
            res, records = impl.query_signals_by_user_strategy_ids(
                stock_codes=stock_codes,
                user_strategy_ids=user_strategy_ids,
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
            user_strategy_id=r.get("user_strategy_id", ""),
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
def get_latest_signal(
    strategy_id: str = Query(..., description="策略 ID"),
    user_strategy_id: str = Query(..., description="用户策略 ID"),
    stock_code: str = Query(..., description="股票代码"),
    action: str = Query(default="", description="买卖方向: 买入/卖出（可选）"),
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """查询最近一次交易信号 — 按策略 ID、股票代码、买卖方向查询最新一条"""
    user_id, role = user_info
    _assert_strategy_access(user_id, role, user_strategy_id=user_strategy_id)
    impl = MongoTradeSignalImpl()
    res, records = impl.query_latest_signal(
        strategy_id=strategy_id,
        user_strategy_id=user_strategy_id,
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
            user_strategy_id=r.get("user_strategy_id", ""),
            stock_code=r.get("stock_code", ""),
            trade_price=float(r.get("trade_price", 0.0) or 0.0),
            profit_rate=float(r.get("profit_rate", 0.0) or 0.0),
            profit_amount=float(r.get("profit_amount", 0.0) or 0.0),
            action=r.get("action", ""),
            reason=r.get("reason", ""),
            create_time=r.get("create_time", ""),
        )
    )
