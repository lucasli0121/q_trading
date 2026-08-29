"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-07-13 00:00:00
Description: 全局策略管理路由 — 管理员创建/删除全局策略定义，所有用户可浏览，回测结果保存与查询
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from db.mongo.mongo_backtest_impl import MongoBacktestImpl
from db.mongo.mongo_strategy_impl import MongoStrategyImpl
from web_api.auth import get_current_user, require_admin
from web_api.models import (
    ApiResponse,
    BacktestSaveRequest,
    StrategyCreateRequest,
    StrategyUpdateRequest,
)

router = APIRouter(prefix="/api/strategy", tags=["全局策略管理"])


@router.post("/create", response_model=ApiResponse[dict])
async def create_strategy(req: StrategyCreateRequest, user_id: str = Depends(require_admin)):
    """创建全局策略（仅管理员，同名策略返回错误）"""
    impl = MongoStrategyImpl()
    ok, _, error = impl.insert_or_update_strategy({
        "name": req.name,
        "strategy_type": req.strategy_type,
        "description": req.description,
        "class_path": req.class_path,
        "class_name": req.class_name,
        "default_params": req.default_params,
    }, insert_only=True)
    if error == "duplicate_name":
        raise HTTPException(status_code=409, detail=f"策略名称 \"{req.name}\" 已存在")
    if not ok:
        raise HTTPException(status_code=500, detail="创建策略失败")
    return ApiResponse(data={"name": req.name}, message="创建成功")


@router.delete("/{name}", response_model=ApiResponse[str])
async def delete_strategy(name: str, user_id: str = Depends(require_admin)):
    """删除全局策略（仅管理员）"""
    impl = MongoStrategyImpl()
    # 验证策略存在
    res, strategies = impl.query_strategy_by_name(name)
    if not res or not strategies:
        raise HTTPException(status_code=404, detail="策略不存在")
    deleted = impl.delete_strategy(name)
    if not deleted:
        raise HTTPException(status_code=500, detail="删除失败")
    return ApiResponse(data=name, message="删除成功")


@router.patch("/{id}", response_model=ApiResponse[str])
async def update_strategy(
    id: str, req: StrategyUpdateRequest, user_id: str = Depends(require_admin)
):
    """更新全局策略（仅管理员，仅更新传入的字段）"""
    impl = MongoStrategyImpl()
    # 验证策略存在
    res, strategies = impl.query_strategy_by_id(id)
    if not res or not strategies or len(strategies) == 0:
        raise HTTPException(status_code=404, detail="策略不存在")

    update_data: dict[str, object] = {}
    if req.name is not None:
        update_data["name"] = req.name
    if req.strategy_type is not None:
        update_data["strategy_type"] = req.strategy_type
    if req.description is not None:
        update_data["description"] = req.description
    if req.class_path is not None:
        update_data["class_path"] = req.class_path
    if req.class_name is not None:
        update_data["class_name"] = req.class_name
    if req.default_params is not None:
        update_data["default_params"] = req.default_params
    if not update_data:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")

    ok = impl.update_strategy(id, update_data)
    if not ok:
        raise HTTPException(status_code=500, detail="更新失败")
    return ApiResponse(data=id, message="更新成功")


@router.get("/list", response_model=ApiResponse[list])
async def list_strategies():
    """查询所有全局策略（公开接口，无需登录）"""
    impl = MongoStrategyImpl()
    res, strategies = impl.query_all_strategies()
    if not res:
        raise HTTPException(status_code=500, detail="查询失败")
    result = []
    for s in (strategies or []):
        s["_id"] = str(s.get("_id", ""))
        result.append(s)
    return ApiResponse(data=result)


@router.get("/id/{id}", response_model=ApiResponse[dict])
async def get_strategy_by_id(id: str, user_id: str = Depends(get_current_user)):
    """按记录 ID 查询单个全局策略详情"""
    impl = MongoStrategyImpl()
    res, strategies = impl.query_strategy_by_id(id)
    if not res or not strategies or len(strategies) == 0:
        raise HTTPException(status_code=404, detail="策略不存在")
    s = strategies[0]
    s["_id"] = str(s.get("_id", ""))
    return ApiResponse(data=s)


@router.get("/{name}", response_model=ApiResponse[dict])
async def get_strategy(name: str, user_id: str = Depends(get_current_user)):
    """查询单个全局策略详情"""
    impl = MongoStrategyImpl()
    res, strategies = impl.query_strategy_by_name(name)
    if not res or not strategies or len(strategies) == 0:
        raise HTTPException(status_code=404, detail="策略不存在")
    s = strategies[0]
    s["_id"] = str(s.get("_id", ""))
    return ApiResponse(data=s)


# ==================== 回测结果 ====================


@router.post("/{strategy_id}/backtest", response_model=ApiResponse[str])
async def save_backtest(
    strategy_id: str,
    req: BacktestSaveRequest,
    user_id: str = Depends(get_current_user),
):
    """保存回测结果（直接使用全局 strategy_id）"""
    # 验证策略存在
    impl = MongoStrategyImpl()
    res, strategies = impl.query_strategy_by_id(strategy_id)
    if not res or not strategies or len(strategies) == 0:
        raise HTTPException(status_code=404, detail="策略不存在")

    backtest_impl = MongoBacktestImpl()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok, record_id = backtest_impl.save_backtest({
        "strategy_id": strategy_id,
        "result_data": req.result_data,
        "create_time": now,
    })
    if not ok:
        raise HTTPException(status_code=500, detail="保存回测结果失败")
    return ApiResponse(data=record_id, message="保存成功")


@router.get("/{strategy_id}/backtest", response_model=ApiResponse[list])
async def get_backtest(
    strategy_id: str,
    user_id: str = Depends(get_current_user),
):
    """查看回测结果（直接使用全局 strategy_id）"""
    # 验证策略存在
    impl = MongoStrategyImpl()
    res, strategies = impl.query_strategy_by_id(strategy_id)
    if not res or not strategies or len(strategies) == 0:
        raise HTTPException(status_code=404, detail="策略不存在")

    backtest_impl = MongoBacktestImpl()
    res, results = backtest_impl.query_backtest_by_strategy(strategy_id)
    if not res:
        raise HTTPException(status_code=500, detail="查询回测结果失败")
    rlist = []
    for r in (results or []):
        r["_id"] = str(r.get("_id", ""))
        rlist.append(r)
    return ApiResponse(data=rlist)
