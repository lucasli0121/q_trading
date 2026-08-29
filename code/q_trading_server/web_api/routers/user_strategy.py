"""
Author: liguoqiang
Date: 2026-07-13 00:00:00
LastEditors: liguoqiang
LastEditTime: 2026-07-13 00:00:00
Description: 用户策略管理路由 — 用户策略关联 CRUD + 执行结果/运行记录
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app_context import AppContext
from db.mongo.mongo_runlog_impl import MongoRunLogImpl
from db.mongo.mongo_strategy_execution_impl import MongoStrategyExecutionImpl
from db.mongo.mongo_user_strategy_impl import MongoUserStrategyImpl
from db.mongo.mongo_workflow_service_user_strategy_impl import MongoWorkFlowServiceUserStrategyImpl
from web_api.auth import get_current_user, get_current_user_with_role
from web_api.models import (
    ApiResponse,
    RunLogSaveRequest,
    StrategyExecutionItem,
    StrategyExecutionSaveRequest,
    UserStrategyCreateRequest,
    UserStrategyItem,
    UserStrategyUpdateRequest,
)

router = APIRouter(prefix="/api/user_strategy", tags=["用户策略管理"])


def _verify_ownership(impl: MongoUserStrategyImpl, user_strategy_id: str, user_id: str, role: int) -> dict:
    """验证用户策略归属并返回记录

    管理员（role == 0）可以操作所有用户策略，跳过归属校验。

    :param impl: MongoUserStrategyImpl 实例
    :param user_strategy_id: 用户策略 ID
    :param user_id: 当前用户 ID
    :param role: 当前用户角色（0=管理员，可操作所有策略）
    :return: 用户策略记录字典
    :raises HTTPException: 404 记录不存在或 403 不属于当前用户
    """
    res, records = impl.query_user_strategy_by_id(user_strategy_id)
    if not res or not records or len(records) == 0:
        raise HTTPException(status_code=404, detail="用户策略不存在")
    record = records[0]
    if role != 0 and record.get("user_id", "") != user_id:
        raise HTTPException(status_code=403, detail="无权操作此用户策略")
    return record


# ==================== 用户策略关联 CRUD ====================


@router.post("/create", response_model=ApiResponse[UserStrategyItem])
async def create_user_strategy(
    req: UserStrategyCreateRequest, user_id: str = Depends(get_current_user)
):
    """创建用户策略关联（同一用户不能重复关联同一全局策略）"""
    impl = MongoUserStrategyImpl()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok, record_id, error = impl.insert_or_update_user_strategy({
        "strategy_id": req.strategy_id,
        "user_id": user_id,
        "pool_id": req.pool_id,
        "status": req.status,
        "initial_amount": req.initial_amount,
        "total_profit": req.total_profit,
        "max_stock_count": req.max_stock_count,
        "strategy_params": req.strategy_params,
        "create_time": now,
    }, insert_only=True)
    if error == "duplicate_user_strategy":
        raise HTTPException(status_code=409, detail="已关联该策略，请勿重复创建")
    if not ok:
        raise HTTPException(status_code=500, detail="创建用户策略失败")
    await AppContext().stock_fetch.distribute_user_strategy_to_workflow()
    return ApiResponse(
        data=UserStrategyItem(
            id=record_id or "",
            strategy_id=req.strategy_id,
            status=req.status,
            user_id=user_id,
            pool_id=req.pool_id,
            initial_amount=req.initial_amount,
            total_profit=req.total_profit,
            max_stock_count=req.max_stock_count,
            strategy_params=req.strategy_params,
            create_time=now,
        ),
        message="创建成功",
    )


@router.delete("/{id}", response_model=ApiResponse[str])
async def delete_user_strategy(id: str, user_info: tuple[str, int] = Depends(get_current_user_with_role)):
    """删除用户策略关联"""
    user_id, role = user_info
    impl = MongoUserStrategyImpl()
    _verify_ownership(impl, id, user_id, role)
    deleted = impl.delete_user_strategy(id)
    if not deleted:
        raise HTTPException(status_code=500, detail="删除失败")
    workflow_user_strategy_impl = MongoWorkFlowServiceUserStrategyImpl()
    workflow_user_strategy_impl.delete_by_user_strategy_id(id)
    return ApiResponse(data=id, message="删除成功")


@router.patch("/{id}", response_model=ApiResponse[str])
async def update_user_strategy(
    id: str, req: UserStrategyUpdateRequest, user_info: tuple[str, int] = Depends(get_current_user_with_role)
):
    """更新用户策略关联（状态、股票池、初始金额）"""
    user_id, role = user_info
    impl = MongoUserStrategyImpl()
    _verify_ownership(impl, id, user_id, role)
    update_data: dict[str, Any] = {}
    if req.status is not None:
        update_data["status"] = req.status
    if req.pool_id is not None:
        update_data["pool_id"] = req.pool_id
    if req.initial_amount is not None:
        update_data["initial_amount"] = req.initial_amount
    if req.total_profit is not None:
        update_data["total_profit"] = req.total_profit
    if req.max_stock_count is not None:
        update_data["max_stock_count"] = req.max_stock_count
    if req.strategy_params is not None:
        update_data["strategy_params"] = req.strategy_params
    if not update_data:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    ok = impl.update_user_strategy(id, update_data)
    if not ok:
        raise HTTPException(status_code=500, detail="更新失败")
    return ApiResponse(data=id, message="更新成功")


@router.get("/list", response_model=ApiResponse[list[UserStrategyItem]])
async def list_user_strategies(user_id: str = Depends(get_current_user)):
    """查询当前用户的所有策略关联（需登录）"""
    impl = MongoUserStrategyImpl()
    res, records = impl.query_user_strategies_by_user(user_id)
    if not res:
        raise HTTPException(status_code=500, detail="查询失败")
    result = [
        UserStrategyItem(
            id=str(r.get("_id", "")),
            strategy_id=r.get("strategy_id", ""),
            status=r.get("status", ""),
            user_id=r.get("user_id", ""),
            pool_id=r.get("pool_id", ""),
            initial_amount=float(r.get("initial_amount", 0.0) or 0.0),
            total_profit=float(r.get("total_profit", 0.0) or 0.0),
            max_stock_count=int(r.get("max_stock_count", 0) or 0),
            strategy_params=r.get("strategy_params", {}) or {},
            create_time=r.get("create_time", ""),
        )
        for r in (records or [])
    ]
    return ApiResponse(data=result)


@router.get("/all", response_model=ApiResponse[list[UserStrategyItem]])
async def list_all_user_strategies():
    """查询所有用户策略关联（公开接口，无需登录）"""
    impl = MongoUserStrategyImpl()
    res, records = impl.query_all_user_strategies()
    if not res:
        raise HTTPException(status_code=500, detail="查询失败")
    result = [
        UserStrategyItem(
            id=str(r.get("_id", "")),
            strategy_id=r.get("strategy_id", ""),
            status=r.get("status", ""),
            user_id=r.get("user_id", ""),
            pool_id=r.get("pool_id", ""),
            initial_amount=float(r.get("initial_amount", 0.0) or 0.0),
            total_profit=float(r.get("total_profit", 0.0) or 0.0),
            max_stock_count=int(r.get("max_stock_count", 0) or 0),
            strategy_params=r.get("strategy_params", {}) or {},
            create_time=r.get("create_time", ""),
        )
        for r in (records or [])
    ]
    return ApiResponse(data=result)


@router.get("/executions", response_model=ApiResponse[list[StrategyExecutionItem]])
async def list_executions(user_info: tuple[str, int] = Depends(get_current_user_with_role)):
    """查询用户所有策略的执行结果列表"""
    user_id, role = user_info
    # 1. 先查用户的所有用户策略 ID
    user_strategy_impl = MongoUserStrategyImpl()
    if role == 0:
        res, records = user_strategy_impl.query_all_user_strategies()
    else:
        res, records = user_strategy_impl.query_user_strategies_by_user(user_id)
    if not res:
        raise HTTPException(status_code=500, detail="查询用户策略列表失败")
    user_strategy_ids = [str(r["_id"]) for r in (records or [])]
    if not user_strategy_ids:
        return ApiResponse(data=[])

    # 2. 再按用户策略 ID 列表查执行结果
    exec_impl = MongoStrategyExecutionImpl()
    res, results = exec_impl.query_executions_by_user_strategy_ids(user_strategy_ids)
    if not res:
        raise HTTPException(status_code=500, detail="查询策略执行结果失败")
    rlist = []
    for r in (results or []):
        rlist.append(StrategyExecutionItem(
            id=str(r.get("_id", "")),
            user_strategy_id=r.get("user_strategy_id", ""),
            current_return_rate=float(r.get("current_return_rate", 0) or 0),
            current_profit=float(r.get("current_profit", 0) or 0),
            annualized_return_rate=float(r.get("annualized_return_rate", 0) or 0),
            benchmark_return_rate=float(r.get("benchmark_return_rate", 0) or 0),
            positions=r.get("positions", []) or [],
            initial_amount=float(r.get("initial_amount", 0) or 0),
            start_date=r.get("start_date", ""),
            execution_days=int(r.get("execution_days", 0) or 0),
            update_time=r.get("update_time", ""),
        ))
    return ApiResponse(data=rlist)


@router.get("/{id}", response_model=ApiResponse[UserStrategyItem])
async def get_user_strategy(id: str, user_info: tuple[str, int] = Depends(get_current_user_with_role)):
    """查询单个用户策略关联详情"""
    user_id, role = user_info
    impl = MongoUserStrategyImpl()
    record = _verify_ownership(impl, id, user_id, role)
    return ApiResponse(
        data=UserStrategyItem(
            id=str(record.get("_id", "")),
            strategy_id=record.get("strategy_id", ""),
            status=record.get("status", ""),
            user_id=record.get("user_id", ""),
            pool_id=record.get("pool_id", ""),
            initial_amount=float(record.get("initial_amount", 0.0) or 0.0),
            total_profit=float(record.get("total_profit", 0.0) or 0.0),
            max_stock_count=int(record.get("max_stock_count", 0) or 0),
            strategy_params=record.get("strategy_params", {}) or {},
            create_time=record.get("create_time", ""),
        )
    )


# ==================== 策略执行结果 ====================


@router.post("/{id}/execution", response_model=ApiResponse[str])
async def save_execution(
    id: str,
    req: StrategyExecutionSaveRequest,
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """保存策略执行结果（每次插入新记录）"""
    user_id, role = user_info
    impl = MongoUserStrategyImpl()
    _verify_ownership(impl, id, user_id, role)

    exec_impl = MongoStrategyExecutionImpl()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok, record_id = exec_impl.save_execution({
        "user_strategy_id": id,
        "current_return_rate": req.current_return_rate,
        "current_profit": req.current_profit,
        "annualized_return_rate": req.annualized_return_rate,
        "benchmark_return_rate": req.benchmark_return_rate,
        "positions": [p.model_dump() for p in req.positions],
        "initial_amount": req.initial_amount,
        "remaining_cash": req.remaining_cash,
        "start_date": req.start_date,
        "execution_days": req.execution_days,
        "update_time": now,
    })
    if not ok:
        raise HTTPException(status_code=500, detail="保存策略执行结果失败")
    return ApiResponse(data=record_id, message="保存成功")


@router.get("/{id}/execution", response_model=ApiResponse[list[StrategyExecutionItem]])
async def get_execution(
    id: str,
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """查询策略执行结果列表"""
    user_id, role = user_info
    impl = MongoUserStrategyImpl()
    _verify_ownership(impl, id, user_id, role)

    exec_impl = MongoStrategyExecutionImpl()
    res, results = exec_impl.query_execution_by_user_strategy(id)
    if not res:
        raise HTTPException(status_code=500, detail="查询策略执行结果失败")
    rlist = []
    for r in (results or []):
        rlist.append(StrategyExecutionItem(
            id=str(r.get("_id", "")),
            user_strategy_id=r.get("user_strategy_id", ""),
            current_return_rate=float(r.get("current_return_rate", 0) or 0),
            current_profit=float(r.get("current_profit", 0) or 0),
            annualized_return_rate=float(r.get("annualized_return_rate", 0) or 0),
            benchmark_return_rate=float(r.get("benchmark_return_rate", 0) or 0),
            positions=r.get("positions", []) or [],
            initial_amount=float(r.get("initial_amount", 0) or 0),
            remaining_cash=float(r.get("remaining_cash", 0) or 0),
            start_date=r.get("start_date", ""),
            execution_days=int(r.get("execution_days", 0) or 0),
            update_time=r.get("update_time", ""),
        ))
    return ApiResponse(data=rlist)

@router.get("/{id}/execution/latest", response_model=ApiResponse[StrategyExecutionItem])
async def get_latest_execution(
    id: str,
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """根据 user_strategy_id 查询最近一次策略执行结果（按 update_time 倒序取最新一条）"""
    user_id, role = user_info
    impl = MongoUserStrategyImpl()
    _verify_ownership(impl, id, user_id, role)

    exec_impl = MongoStrategyExecutionImpl()
    res, results = exec_impl.query_latest_execution_by_user_strategy(id)
    if not res:
        raise HTTPException(status_code=500, detail="查询策略执行结果失败")
    if not results or len(results) == 0:
        raise HTTPException(status_code=404, detail="未找到该策略的执行结果")
    r = results[0]
    return ApiResponse(data=StrategyExecutionItem(
        id=str(r.get("_id", "")),
        user_strategy_id=r.get("user_strategy_id", ""),
        current_return_rate=float(r.get("current_return_rate", 0) or 0),
        current_profit=float(r.get("current_profit", 0) or 0),
        annualized_return_rate=float(r.get("annualized_return_rate", 0) or 0),
        benchmark_return_rate=float(r.get("benchmark_return_rate", 0) or 0),
        positions=r.get("positions", []) or [],
        initial_amount=float(r.get("initial_amount", 0) or 0),
        remaining_cash=float(r.get("remaining_cash", 0) or 0),
        start_date=r.get("start_date", ""),
        execution_days=int(r.get("execution_days", 0) or 0),
        update_time=r.get("update_time", ""),
    ))


@router.delete("/{id}/execution", response_model=ApiResponse[str])
async def delete_execution(
    id: str,
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """删除策略执行结果"""
    user_id, role = user_info
    impl = MongoUserStrategyImpl()
    _verify_ownership(impl, id, user_id, role)

    exec_impl = MongoStrategyExecutionImpl()
    deleted = exec_impl.delete_execution_by_user_strategy(id)
    if not deleted:
        raise HTTPException(status_code=500, detail="删除策略执行结果失败")
    return ApiResponse(data=id, message="删除成功")


# ==================== 运行记录 ====================


@router.post("/{id}/runlog", response_model=ApiResponse[str])
async def save_runlog(
    id: str,
    req: RunLogSaveRequest,
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """保存运行记录"""
    user_id, role = user_info
    impl = MongoUserStrategyImpl()
    _verify_ownership(impl, id, user_id, role)

    runlog_impl = MongoRunLogImpl()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok, record_id = runlog_impl.save_runlog({
        "user_strategy_id": id,
        "log_content": req.log_content,
        "level": req.level,
        "create_time": now,
    })
    if not ok:
        raise HTTPException(status_code=500, detail="保存运行记录失败")
    return ApiResponse(data=record_id, message="保存成功")


@router.get("/{id}/runlog", response_model=ApiResponse[list])
async def get_runlog(
    id: str,
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
    skip: int = Query(default=0, description="分页偏移"),
    limit: int = Query(default=100, description="分页大小"),
):
    """查看运行记录"""
    user_id, role = user_info
    impl = MongoUserStrategyImpl()
    _verify_ownership(impl, id, user_id, role)

    runlog_impl = MongoRunLogImpl()
    res, results = runlog_impl.query_runlog_by_user_strategy(id, skip=skip, limit=limit)
    if not res:
        raise HTTPException(status_code=500, detail="查询运行记录失败")
    rlist = []
    for r in (results or []):
        r["_id"] = str(r.get("_id", ""))
        rlist.append(r)
    return ApiResponse(data=rlist)

