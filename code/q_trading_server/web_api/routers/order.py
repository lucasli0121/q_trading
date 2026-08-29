"""
Author: liguoqiang
Date: 2026-07-02 00:00:00
LastEditors: liguoqiang
LastEditTime: 2026-07-02 00:00:00
Description: 订单管理路由
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from db.mongo.mongo_order_impl import MongoOrderImpl
from db.mongo.mongo_user_strategy_impl import MongoUserStrategyImpl
from web_api.auth import get_current_user, get_current_user_with_role
from web_api.models import ApiResponse, OrderCreateRequest, OrderInfo, OrderUpdateRequest, OrderUpdateStatusRequest

router = APIRouter(prefix="/api/order", tags=["订单管理"], dependencies=[Depends(get_current_user)])


def _verify_order_ownership(order_id: str, user_id: str, role: int) -> dict:
    """验证订单归属并返回订单记录

    管理员（role == 0）可以操作所有订单，跳过归属校验。

    :param order_id: 订单 ID
    :param user_id: 当前用户 ID
    :param role: 当前用户角色（0=管理员，可操作所有订单）
    :return: 订单记录字典
    :raises HTTPException: 404 订单不存在或 403 不属于当前用户
    """
    impl = MongoOrderImpl()
    ok, orders = impl.query_order_by_id(order_id)
    if not ok or not orders or len(orders) == 0:
        raise HTTPException(status_code=404, detail="订单不存在")
    order = orders[0]
    if role != 0:
        # 普通用户需要验证订单所属的用户策略是否归自己所有
        user_strategy_id = order.get("user_strategy_id", "")
        if user_strategy_id:
            us_impl = MongoUserStrategyImpl()
            res, records = us_impl.query_user_strategy_by_id(user_strategy_id)
            if not res or not records or len(records) == 0:
                raise HTTPException(status_code=404, detail="关联的用户策略不存在")
            if records[0].get("user_id", "") != user_id:
                raise HTTPException(status_code=403, detail="无权操作此订单")
    return order


@router.post("/create", response_model=ApiResponse[OrderInfo])
async def create_order(req: OrderCreateRequest, user_info: tuple[str, int] = Depends(get_current_user_with_role)):
    """创建订单。管理员（role==0）可为任意用户策略创建订单，普通用户仅可为自己的策略创建。"""
    user_id, role = user_info
    impl = MongoOrderImpl()

    # 非管理员校验用户策略归属
    if role != 0:
        us_impl = MongoUserStrategyImpl()
        res, records = us_impl.query_user_strategy_by_id(req.user_strategy_id)
        if not res or not records or len(records) == 0:
            raise HTTPException(status_code=404, detail="用户策略不存在")
        if records[0].get("user_id", "") != user_id:
            raise HTTPException(status_code=403, detail="无权为此用户策略创建订单")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "user_strategy_id": req.user_strategy_id,
        "stock_code": req.stock_code,
        "entrust_quantity": req.entrust_quantity,
        "trade_price": req.trade_price,
        "trade_quantity": req.trade_quantity,
        "position_price": req.position_price,
        "profit_rate": req.profit_rate,
        "profit_amount": req.profit_amount,
        "commission_fee": req.commission_fee,
        "status": req.status or "委托",
        "action": req.action or "买入",
        "create_time": req.create_time or now,
    }
    ok, order_id = impl.save_order(payload)
    if not ok or not order_id:
        raise HTTPException(status_code=500, detail="创建订单失败")
    return ApiResponse(
        data=OrderInfo(
            id=order_id,
            user_strategy_id=req.user_strategy_id,
            stock_code=req.stock_code,
            entrust_quantity=req.entrust_quantity,
            trade_price=req.trade_price,
            trade_quantity=req.trade_quantity,
            position_price=req.position_price,
            profit_rate=req.profit_rate,
            profit_amount=req.profit_amount,
            commission_fee=req.commission_fee,
            status=payload["status"],
            create_time=payload["create_time"],
            action=payload["action"],
        ),
        message="创建成功",
    )


@router.get("/list/{user_strategy_id}", response_model=ApiResponse[list[OrderInfo]])
async def list_orders(user_strategy_id: str, user_id: str = Depends(get_current_user)):
    """查询某个用户策略下的订单列表。"""
    impl = MongoOrderImpl()
    ok, orders = impl.query_orders_by_user_strategy(user_strategy_id)
    if not ok:
        raise HTTPException(status_code=500, detail="查询订单失败")
    result = [
        OrderInfo(
            id=str(o.get("_id", "")),
            user_strategy_id=o.get("user_strategy_id", ""),
            stock_code=o.get("stock_code", ""),
            entrust_quantity=int(o.get("entrust_quantity", 0) or 0),
            trade_price=float(o.get("trade_price", 0.0) or 0.0),
            trade_quantity=int(o.get("trade_quantity", 0) or 0),
            position_price=float(o.get("position_price", 0.0) or 0.0),
            profit_rate=float(o.get("profit_rate", 0.0) or 0.0),
            profit_amount=float(o.get("profit_amount", 0.0) or 0.0),
            commission_fee=float(o.get("commission_fee", 0.0) or 0.0),
            status=o.get("status", "委托"),
            create_time=o.get("create_time", ""),
            action=o.get("action", "买入"),
        )
        for o in (orders or [])
    ]
    return ApiResponse(data=result)


@router.get("/user/list", response_model=ApiResponse[list[OrderInfo]])
async def list_orders_by_user(
    start_time: str | None = Query(default=None, description="起始时间，格式 %Y-%m-%d %H:%M:%S，为空则不限制"),
    end_time: str | None = Query(default=None, description="结束时间，格式 %Y-%m-%d %H:%M:%S，为空则不限制"),
    status: str | None = Query(default=None, description="订单状态，为空则查询所有状态"),
    action: str | None = Query(default=None, description="订单动作（买入/卖出），为空则查询所有动作"),
    user_id: str = Depends(get_current_user),
):
    """根据登录用户 ID 查询该用户所有策略下的订单列表。

    支持按时间范围（start_time/end_time）、订单状态（status）、订单动作（action）过滤，
    对应参数为空时表示不做该维度的过滤。
    """
    # 1. 先查用户的所有用户策略 ID
    user_strategy_impl = MongoUserStrategyImpl()
    ok, records = user_strategy_impl.query_user_strategies_by_user(user_id)
    if not ok:
        raise HTTPException(status_code=500, detail="查询用户策略列表失败")
    user_strategy_ids = [str(r["_id"]) for r in (records or [])]
    if not user_strategy_ids:
        return ApiResponse(data=[])

    # 2. 再按用户策略 ID 列表批量查订单（附带时间范围/状态/动作过滤）
    impl = MongoOrderImpl()
    ok, orders = impl.query_orders_by_user_strategy_ids(
        user_strategy_ids,
        start_time=start_time,
        end_time=end_time,
        status=status,
        action=action,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="查询订单失败")
    result = [
        OrderInfo(
            id=str(o.get("_id", "")),
            user_strategy_id=o.get("user_strategy_id", ""),
            stock_code=o.get("stock_code", ""),
            entrust_quantity=int(o.get("entrust_quantity", 0) or 0),
            trade_price=float(o.get("trade_price", 0.0) or 0.0),
            trade_quantity=int(o.get("trade_quantity", 0) or 0),
            position_price=float(o.get("position_price", 0.0) or 0.0),
            profit_rate=float(o.get("profit_rate", 0.0) or 0.0),
            profit_amount=float(o.get("profit_amount", 0.0) or 0.0),
            commission_fee=float(o.get("commission_fee", 0.0) or 0.0),
            status=o.get("status", "委托"),
            create_time=o.get("create_time", ""),
            action=o.get("action", "买入"),
        )
        for o in (orders or [])
    ]
    return ApiResponse(data=result)


@router.get("/{order_id}", response_model=ApiResponse[OrderInfo])
async def get_order(order_id: str, user_id: str = Depends(get_current_user)):
    """查询单个订单。"""
    impl = MongoOrderImpl()
    ok, orders = impl.query_order_by_id(order_id)
    if not ok or not orders or len(orders) == 0:
        raise HTTPException(status_code=404, detail="订单不存在")
    order = orders[0]
    return ApiResponse(
        data=OrderInfo(
            id=str(order.get("_id", "")),
            user_strategy_id=order.get("user_strategy_id", ""),
            stock_code=order.get("stock_code", ""),
            entrust_quantity=int(order.get("entrust_quantity", 0) or 0),
            trade_price=float(order.get("trade_price", 0.0) or 0.0),
            trade_quantity=int(order.get("trade_quantity", 0) or 0),
            position_price=float(order.get("position_price", 0.0) or 0.0),
            profit_rate=float(order.get("profit_rate", 0.0) or 0.0),
            profit_amount=float(order.get("profit_amount", 0.0) or 0.0),
            commission_fee=float(order.get("commission_fee", 0.0) or 0.0),
            status=order.get("status", "委托"),
            create_time=order.get("create_time", ""),
            action=order.get("action", "买入"),
        )
    )


@router.put("/{order_id}/status", response_model=ApiResponse[OrderInfo])
async def update_order_status(
    order_id: str, req: OrderUpdateStatusRequest, user_info: tuple[str, int] = Depends(get_current_user_with_role)
):
    """更新订单状态。管理员可更新任意订单，普通用户仅可更新自己的订单。"""
    user_id, role = user_info
    _verify_order_ownership(order_id, user_id, role)
    impl = MongoOrderImpl()
    ok = impl.update_order_status(order_id, req.status)
    if not ok:
        raise HTTPException(status_code=500, detail="更新订单状态失败")
    ok2, orders = impl.query_order_by_id(order_id)
    if not ok2 or not orders or len(orders) == 0:
        raise HTTPException(status_code=404, detail="订单不存在")
    order = orders[0]
    return ApiResponse(
        data=OrderInfo(
            id=str(order.get("_id", "")),
            user_strategy_id=order.get("user_strategy_id", ""),
            stock_code=order.get("stock_code", ""),
            entrust_quantity=int(order.get("entrust_quantity", 0) or 0),
            trade_price=float(order.get("trade_price", 0.0) or 0.0),
            trade_quantity=int(order.get("trade_quantity", 0) or 0),
            position_price=float(order.get("position_price", 0.0) or 0.0),
            profit_rate=float(order.get("profit_rate", 0.0) or 0.0),
            profit_amount=float(order.get("profit_amount", 0.0) or 0.0),
            commission_fee=float(order.get("commission_fee", 0.0) or 0.0),
            status=order.get("status", "委托"),
            create_time=order.get("create_time", ""),
            action=order.get("action", "买入"),
        ),
        message="更新成功",
    )


@router.put("/{order_id}", response_model=ApiResponse[OrderInfo])
async def update_order(
    order_id: str, req: OrderUpdateRequest, user_info: tuple[str, int] = Depends(get_current_user_with_role)
):
    """更新订单（仅更新传入的非空字段，user_strategy_id 不可修改）。

    管理员可更新任意订单，普通用户仅可更新自己的订单。
    传入哪些字段就更新哪些字段，未传入的字段保持不变。
    """
    user_id, role = user_info
    _verify_order_ownership(order_id, user_id, role)
    impl = MongoOrderImpl()
    update_data = req.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    ok = impl.update_order(order_id, update_data)
    if not ok:
        raise HTTPException(status_code=500, detail="更新订单失败")
    ok2, orders = impl.query_order_by_id(order_id)
    if not ok2 or not orders or len(orders) == 0:
        raise HTTPException(status_code=404, detail="订单不存在")
    order = orders[0]
    return ApiResponse(
        data=OrderInfo(
            id=str(order.get("_id", "")),
            user_strategy_id=order.get("user_strategy_id", ""),
            stock_code=order.get("stock_code", ""),
            entrust_quantity=int(order.get("entrust_quantity", 0) or 0),
            trade_price=float(order.get("trade_price", 0.0) or 0.0),
            trade_quantity=int(order.get("trade_quantity", 0) or 0),
            position_price=float(order.get("position_price", 0.0) or 0.0),
            profit_rate=float(order.get("profit_rate", 0.0) or 0.0),
            profit_amount=float(order.get("profit_amount", 0.0) or 0.0),
            commission_fee=float(order.get("commission_fee", 0.0) or 0.0),
            status=order.get("status", "委托"),
            create_time=order.get("create_time", ""),
            action=order.get("action", "买入"),
        ),
        message="更新成功",
    )
