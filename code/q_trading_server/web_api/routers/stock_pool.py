"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-06-22 13:30:00
Description: 股票池管理路由
"""

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app_context import AppContext
from db.mongo.mongo_data_agent_pool_stocks_impl import MongoDataAgentPoolStocksImpl
from db.mongo.mongo_stock_info_impl import MongoStockInfoImpl
from db.mongo.mongo_stock_pool_impl import MongoStockPoolImpl
from db.mongo.mongo_blacklist_impl import MongoBlacklistImpl
from db.mongo.mongo_strategy_impl import MongoStrategyImpl
from db.mongo.mongo_user_strategy_impl import MongoUserStrategyImpl
from web_api.auth import get_current_user, get_current_user_with_role
from web_api.models import (
    ApiResponse,
    PoolCreateRequest,
    PoolInfo,
    PoolStockInfo,
    PoolStockModifyRequest,
)

# A股股票代码格式: 6位数字 + .SH/.SZ/.BJ
_STOCK_CODE_PATTERN = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")

router = APIRouter(prefix="/api/pool", tags=["股票池管理"], dependencies=[Depends(get_current_user)])


@router.post("/create", response_model=ApiResponse[PoolInfo])
async def create_pool(req: PoolCreateRequest, user_id: str = Depends(get_current_user)):
    """创建股票池"""
    impl = MongoStockPoolImpl()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok, pool_id = impl.insert_or_update_stock_pool({
        "name": req.name,
        "description": req.description,
        "create_time": now,
        "user_id": user_id,
    })
    if not ok:
        raise HTTPException(status_code=500, detail="创建股票池失败")
    return ApiResponse(data=PoolInfo(
            id=str(pool_id) if pool_id else "",
            name=req.name,
            description=req.description,
            create_time=now,
            user_id=user_id
        ),
        message="创建成功")


@router.delete("/{name}", response_model=ApiResponse[str])
async def delete_pool(name: str, user_id: str = Depends(get_current_user)):
    """删除股票池（级联删除关联股票）"""
    impl = MongoStockPoolImpl()
    # 验证池属于当前用户
    res, pools = impl.query_stock_pool_by_name_and_user(name, user_id)
    if not res or not pools:
        raise HTTPException(status_code=404, detail="股票池不存在")
    # 检查股票池是否已被用户策略引用
    pool_id = str(pools[0].get("_id", ""))
    strategy_impl = MongoUserStrategyImpl()
    ok, strategies = strategy_impl.query_user_strategies_by_pool_id(pool_id)
    if ok and strategies and len(strategies) > 0:
        strategy_global_impl = MongoStrategyImpl()
        strategy_names: list[str] = []
        for s in strategies:
            sid = s.get("strategy_id", "")
            ok2, s_records = strategy_global_impl.query_strategy_by_id(sid)
            if ok2 and s_records and len(s_records) > 0:
                strategy_names.append(s_records[0].get("name", sid))
            else:
                strategy_names.append(sid)
        raise HTTPException(
            status_code=400,
            detail=f"股票池已被以下策略引用，请先从策略中移除再删除: {', '.join(strategy_names)}",
        )
    deleted = impl.delete_stock_pool(name)
    if not deleted:
        raise HTTPException(status_code=500, detail="删除失败")
    # 从分配的资源库中移除这个股票池
    agent_impl = MongoDataAgentPoolStocksImpl()
    agent_impl.delete_data_agent_pool_stocks_by_pool_name(pool_name=name)
    return ApiResponse(data=name, message="删除成功")


@router.get("/list", response_model=ApiResponse[list[PoolInfo]])
async def list_pools(user_info: tuple[str, int] = Depends(get_current_user_with_role)):
    """查询股票池列表（管理员可查看全部股票池）"""
    user_id, role = user_info
    impl = MongoStockPoolImpl()
    if role == 0:
        res, pools = impl.query_all_stock_pools()
    else:
        res, pools = impl.query_stock_pools_by_user(user_id)
    if not res:
        raise HTTPException(status_code=500, detail="查询失败")
    result = [PoolInfo(
        id=p.get("id", str(p.get("_id", ""))),
        name=p.get("name", ""),
        description=p.get("description", ""),
        create_time=p.get("create_time", ""),
        user_id=p.get("user_id", ""),
    ) for p in (pools or [])]
    return ApiResponse(data=result)


@router.post("/{name}/stocks/add", response_model=ApiResponse[str])
async def add_stocks(name: str, req: PoolStockModifyRequest, user_id: str = Depends(get_current_user)):
    """向股票池添加股票（自动过滤黑名单中的股票）"""
    impl = MongoStockPoolImpl()
    # 验证池属于当前用户
    res, pools = impl.query_stock_pool_by_name_and_user(name, user_id)
    if not res or not pools:
        raise HTTPException(status_code=404, detail="股票池不存在")
    
    # 获取系统要求的真实代码
    stock_impl = MongoStockInfoImpl()
    stock_real_codes = []
    for code in req.codes:
        ok, exists = stock_impl.query_by_codes([code])
        if not ok:
            raise HTTPException(status_code=500, detail=f"查询股票信息失败: {code}")
        if not exists or len(exists) == 0:
            raise HTTPException(status_code=400, detail=f"股票不存在: {code}")
        stock_real_codes.append(exists[0].get("code", ""))
    # 检查黑名单
    blacklist_impl = MongoBlacklistImpl()
    blocked_codes: list[str] = []
    for code in stock_real_codes:
        ok, is_blocked = blacklist_impl.is_stock_blacklisted(user_id, code)
        if ok and is_blocked:
            blocked_codes.append(code)
    if blocked_codes:
        raise HTTPException(
            status_code=400,
            detail=f"以下股票在黑名单中，无法添加: {', '.join(blocked_codes)}",
        )
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    records = [{"pool_name": name, "code": c, "add_time": now} for c in stock_real_codes]
    ok = impl.add_stocks_to_pool(records)
    if not ok:
        raise HTTPException(status_code=500, detail="添加失败")
    # 将股票池的股票分配给数据代理对象进行抓取和处理
    await AppContext().stock_fetch.distribute_pool_stocks_to_data_agents(pool_name=name)
    return ApiResponse(data=f"已添加 {len(stock_real_codes)} 只股票", message="添加成功")


@router.delete("/{name}/stocks/remove", response_model=ApiResponse[str])
async def remove_stocks(name: str, req: PoolStockModifyRequest, user_id: str = Depends(get_current_user)):
    """从股票池剔除股票"""
    impl = MongoStockPoolImpl()
    res, pools = impl.query_stock_pool_by_name_and_user(name, user_id)
    if not res or not pools:
        raise HTTPException(status_code=404, detail="股票池不存在")
    # 获取系统要求的真实代码
    stock_impl = MongoStockInfoImpl()
    stock_real_codes = []
    for code in req.codes:
        ok, exists = stock_impl.query_by_codes([code])
        if not ok:
            raise HTTPException(status_code=500, detail=f"查询股票信息失败: {code}")
        if not exists or len(exists) == 0:
            raise HTTPException(status_code=400, detail=f"股票不存在: {code}")
        stock_real_codes.append(exists[0].get("code", ""))
    ok = impl.remove_stocks_from_pool(name, stock_real_codes)
    if not ok:
        raise HTTPException(status_code=500, detail="剔除失败")
    # 将股票池的股票分配给数据代理对象进行抓取和处理
    await AppContext().stock_fetch.distribute_pool_stocks_to_data_agents(pool_name=name)
    return ApiResponse(data=f"已剔除 {len(stock_real_codes)} 只股票", message="剔除成功")


@router.get("/{name}/stocks", response_model=ApiResponse[list[PoolStockInfo]])
async def get_pool_stocks(name: str, user_info: tuple[str, int] = Depends(get_current_user_with_role)):
    """获取股票池中所有股票（管理员可查看任意股票池）"""
    user_id, role = user_info
    impl = MongoStockPoolImpl()
    if role == 0:
        # 管理员：仅按名称校验股票池是否存在，不校验归属
        res, pools = impl.query_stock_pool_by_name(name)
        if not res or not pools:
            raise HTTPException(status_code=404, detail="股票池不存在")
    else:
        res, pools = impl.query_stock_pool_by_name_and_user(name, user_id)
        if not res or not pools:
            raise HTTPException(status_code=404, detail="股票池不存在")
    res2, stocks = impl.query_stocks_by_pool_name(name)
    result = [PoolStockInfo(code=s.get("code", ""), add_time=s.get("add_time", "")) for s in (stocks or [])]
    return ApiResponse(data=result)
