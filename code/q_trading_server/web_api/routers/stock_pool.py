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
from core.config_notify import (
    ACTION_CREATE,
    ACTION_DELETE,
    ACTION_UPDATE,
    SCOPE_POOL,
    notify_config_change,
)
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

router = APIRouter(prefix="/api/pool", tags=["股票池管理"], dependencies=[Depends(get_current_user)])


@router.post("/create", response_model=ApiResponse[PoolInfo])
def create_pool(req: PoolCreateRequest, user_id: str = Depends(get_current_user)):
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
        raise HTTPException(status_code=500, detail=f"创建股票池失败{pool_id if pool_id else ""}")
    notify_config_change(SCOPE_POOL, ACTION_CREATE, str(pool_id) if pool_id else req.name)
    return ApiResponse(data=PoolInfo(
            id=str(pool_id) if pool_id else "",
            name=req.name,
            description=req.description,
            create_time=now,
            user_id=user_id
        ),
        message="创建成功")


@router.delete("/{name}", response_model=ApiResponse[str])
def delete_pool(name: str, user_id: str = Depends(get_current_user)):
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
    deleted = impl.delete_stock_pool_by_id(pool_id=pool_id)
    if not deleted:
        raise HTTPException(status_code=500, detail="删除失败")
    # 从分配的资源库中移除这个股票池
    agent_impl = MongoDataAgentPoolStocksImpl()
    agent_impl.delete_data_agent_pool_stocks_by_pool_id(pool_id=pool_id)
    notify_config_change(SCOPE_POOL, ACTION_DELETE, pool_id)
    return ApiResponse(data=name, message="删除成功")


@router.get("/list", response_model=ApiResponse[list[PoolInfo]])
def list_pools(user_info: tuple[str, int] = Depends(get_current_user_with_role)):
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
def add_stocks(name: str, req: PoolStockModifyRequest, user_id: str = Depends(get_current_user)):
    """向股票池添加股票（自动过滤黑名单中的股票）"""
    impl = MongoStockPoolImpl()
    # 验证池属于当前用户
    res, pools = impl.query_stock_pool_by_name_and_user(name, user_id)
    if not res or not pools:
        raise HTTPException(status_code=404, detail="股票池不存在")

    pool_id: str = str(pools[0].get("_id"))
    # 批量校验代替逐只往返：原写法每只股票 2 次查询（股票信息 + 黑名单命中），
    # 500 只票就是 1000 次数据库往返。
    stock_impl = MongoStockInfoImpl()
    blacklist_impl = MongoBlacklistImpl()
    ok, infos = stock_impl.query_by_codes(req.codes)
    if not ok:
        raise HTTPException(status_code=500, detail="查询股票信息失败")
    found = {r.get("code", ""): r for r in (infos or [])}
    missing = [c for c in req.codes if stock_impl.full_code(c) not in found]
    if missing:
        raise HTTPException(status_code=400, detail=f"股票不存在: {', '.join(missing)}")
    stock_real_codes = [
        code for code in (found[stock_impl.full_code(c)].get("code", "") for c in req.codes)
        if code
    ]
    ok, blocked = blacklist_impl.query_blacklisted_codes(user_id, stock_real_codes)
    if not ok:
        raise HTTPException(status_code=500, detail="查询黑名单失败")
    if blocked:
        raise HTTPException(
            status_code=400,
            detail=f"以下股票在黑名单中，无法添加: {', '.join(sorted(blocked))}",
        )
    stock_real_codes = [c for c in stock_real_codes if c not in blocked]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    records = [{"pool_id": pool_id, "code": c, "add_time": now} for c in stock_real_codes]
    ok = impl.add_stocks_to_pool(records)
    if not ok:
        raise HTTPException(status_code=500, detail="添加失败")
    # 将股票池的股票分配给数据代理对象进行抓取和处理
    # 后台执行：全量重分配是长任务，不能在请求里同步跑
    AppContext().stock_fetch.submit_pool_stocks_distribution(pool_id=pool_id)
    # 通知 q_trading_work：池内容变了，state.stock_pool 与当日选股结果都要跟着刷新。
    # 只刷行情订阅不够 —— handle_bar 还会用旧 stock_pool 过滤，新入池的股票仍被挡掉。
    notify_config_change(SCOPE_POOL, ACTION_UPDATE, pool_id)
    return ApiResponse(data=f"已添加 {len(stock_real_codes)} 只股票", message="添加成功")


@router.delete("/{name}/stocks/remove", response_model=ApiResponse[str])
def remove_stocks(name: str, req: PoolStockModifyRequest, user_id: str = Depends(get_current_user)):
    """从股票池剔除股票"""
    impl = MongoStockPoolImpl()
    res, pools = impl.query_stock_pool_by_name_and_user(name, user_id)
    if not res or not pools or len(pools) == 0:
        raise HTTPException(status_code=404, detail="股票池不存在")
    pool_dao = pools[0]
    pool_id: str = str(pool_dao.get("_id", pool_dao.get("id", "")))
    # 批量校验（原写法在循环里逐只查询）
    stock_impl = MongoStockInfoImpl()
    ok, infos = stock_impl.query_by_codes(req.codes)
    if not ok:
        raise HTTPException(status_code=500, detail="查询股票信息失败")
    found = {r.get("code", ""): r for r in (infos or [])}
    missing = [c for c in req.codes if stock_impl.full_code(c) not in found]
    if missing:
        raise HTTPException(status_code=400, detail=f"股票不存在: {', '.join(missing)}")
    stock_real_codes = [
        code for code in (found[stock_impl.full_code(c)].get("code", "") for c in req.codes)
        if code
    ]
    ok = impl.remove_stocks_from_pool(pool_id = pool_id, codes = stock_real_codes)
    if not ok:
        raise HTTPException(status_code=500, detail="剔除失败")
    # 将股票池的股票分配给数据代理对象进行抓取和处理
    # 后台执行：全量重分配是长任务，不能在请求里同步跑
    AppContext().stock_fetch.submit_pool_stocks_distribution(pool_id=pool_id)
    notify_config_change(SCOPE_POOL, ACTION_UPDATE, pool_id)
    return ApiResponse(data=f"已剔除 {len(stock_real_codes)} 只股票", message="剔除成功")


@router.get("/{name}/stocks", response_model=ApiResponse[list[PoolStockInfo]])
def get_pool_stocks(name: str, user_info: tuple[str, int] = Depends(get_current_user_with_role)):
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
    pool_ids = [str(p.get("_id", p.get("id", ""))) for p in pools]
    res2, stocks = impl.query_stocks_by_pool_ids(pool_ids=pool_ids)
    result = [PoolStockInfo(code=s.get("code", ""), add_time=s.get("add_time", "")) for s in (stocks or [])]
    return ApiResponse(data=result)
