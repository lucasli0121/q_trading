"""
Author: liguoqiang
Date: 2026-07-23 00:00:00
LastEditors: liguoqiang
LastEditTime: 2026-07-23 00:00:00
Description: 策略选股管理路由 — 策略选股记录的增删查
"""

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from db.mongo.mongo_rt_stocks_impl import MongoRtStocksImpl
from db.mongo.mongo_strategy_select_stocks_impl import MongoStrategySelectStocksImpl
from db.mongo.mongo_user_strategy_impl import MongoUserStrategyImpl
from web_api.auth import get_current_user_with_role
from web_api.models import (
    ApiResponse,
    StrategySelectStockCreateRequest,
    StrategySelectStockItem,
    StrategySelectStockWithRtItem,
)

router = APIRouter(prefix="/api/strategy_select_stocks", tags=["策略选股管理"])


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


@router.post("/add", response_model=ApiResponse[list[StrategySelectStockItem]])
async def add_select_stock(
    req: StrategySelectStockCreateRequest,
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """批量添加策略选股记录"""
    user_id, role = user_info
    # 非管理员需验证该 strategy_id 属于当前用户
    if role != 0:
        strategy_ids = _resolve_strategy_ids_from_user(user_id, role)
        if not strategy_ids or req.strategy_id not in strategy_ids:
            raise HTTPException(status_code=403, detail="无权操作此策略")
    impl = MongoStrategySelectStocksImpl()
    now = datetime.now().strftime("%Y-%m-%d")
    records = [
        {
            "strategy_id": req.strategy_id,
            "code": code,
            "create_time": now,
        }
        for code in req.codes
    ]
    ok, record_ids = impl.bulk_add_select_stocks(records)
    if not ok:
        raise HTTPException(status_code=500, detail="添加选股记录失败")
    rlist = [
        StrategySelectStockItem(
            id=record_ids[i] if i < len(record_ids) else "",
            strategy_id=req.strategy_id,
            code=code,
            create_time=now,
        )
        for i, code in enumerate(req.codes)
    ]
    return ApiResponse(data=rlist, message="添加成功")


@router.delete("/{id}", response_model=ApiResponse[str])
async def delete_select_stock(
    id: str,
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """删除策略选股记录"""
    impl = MongoStrategySelectStocksImpl()
    deleted = impl.delete_select_stock(id)
    if not deleted:
        raise HTTPException(status_code=500, detail="删除选股记录失败")
    return ApiResponse(data=id, message="删除成功")


@router.get("/list", response_model=ApiResponse[list[StrategySelectStockWithRtItem]])
async def list_select_stocks(
    strategy_id: str = Query(default="", description="策略 ID（可选）"),
    start_time: str = Query(default="", description="创建开始时间 YYYY-MM-DD HH:MM:SS（可选）"),
    end_time: str = Query(default="", description="创建结束时间 YYYY-MM-DD HH:MM:SS（可选）"),
    skip: int = Query(default=0, description="分页跳过条数"),
    limit: int = Query(default=0, description="分页限制条数，0 表示不限制"),
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """查询策略选股记录 — 管理员查所有，普通用户查自己策略的选股记录

    结合最新的实时行情数据一起返回。
    """
    user_id, role = user_info
    impl = MongoStrategySelectStocksImpl()
    if strategy_id:
        # 指定了策略 ID，直接按条件查询
        res, records = impl.query_select_stocks(
            strategy_id=strategy_id,
            start_time=start_time,
            end_time=end_time,
            skip=skip,
            limit=limit,
        )
    else:
        # 未指定策略 ID：管理员查所有，普通用户查自己的策略
        strategy_ids = _resolve_strategy_ids_from_user(user_id, role)
        if strategy_ids is None:
            # 管理员：查询所有记录
            res, records = impl.query_select_stocks(
                start_time=start_time,
                end_time=end_time,
                skip=skip,
                limit=limit,
            )
        elif not strategy_ids:
            return ApiResponse(data=[])
        else:
            res, records = impl.query_select_stocks_by_strategy_ids(
                strategy_ids=strategy_ids,
                start_time=start_time,
                end_time=end_time,
                skip=skip,
                limit=limit,
            )
    if not res:
        raise HTTPException(status_code=500, detail="查询选股记录失败")

    if not records:
        return ApiResponse(data=[])

    # 提取所有唯一的股票代码，查询最新实时行情
    codes = list({r.get("code", "") for r in records if r.get("code", "")})
    rt_map: dict[str, dict[str, Any]] = {}
    if codes:
        rt_impl = MongoRtStocksImpl()
        # use_default_time=False 不限制分钟，获取每只股票的最新一条记录
        rt_ok, rt_records = rt_impl.query_latest_rt_stocks_for_codes(
            codes, use_default_time=False
        )
        if rt_ok and rt_records:
            for rt in rt_records:
                rt_code = rt.get("code", "")
                rt_map[rt_code] = rt

    rlist: list[StrategySelectStockWithRtItem] = []
    for r in records:
        code = r.get("code", "")
        rt_data = rt_map.get(code, {})
        rlist.append(
            StrategySelectStockWithRtItem(
                id=str(r.get("_id", "")),
                strategy_id=r.get("strategy_id", ""),
                code=code,
                create_time=r.get("create_time", ""),
                name=str(rt_data.get("name", "")),
                price=float(rt_data.get("price", 0.0)),
                change_percent=float(rt_data.get("change_percent", 0.0)),
                change_amount=float(rt_data.get("change_amount", 0.0)),
                volume=int(rt_data.get("volume", 0)),
                amount=float(rt_data.get("amount", 0.0)),
                amp=float(rt_data.get("amp", 0.0)),
                high=float(rt_data.get("high", 0.0)),
                low=float(rt_data.get("low", 0.0)),
                open=float(rt_data.get("open", 0.0)),
                preclose=float(rt_data.get("preclose", 0.0)),
                qrr=float(rt_data.get("qrr", 0.0)),
                turnover=float(rt_data.get("turnover", 0.0)),
                rt_update_time=str(rt_data.get("create_time", "")),
            )
        )
    return ApiResponse(data=rlist)


@router.get("/by_user", response_model=ApiResponse[list[StrategySelectStockWithRtItem]])
async def get_select_stocks_by_user(
    start_time: str = Query(default="", description="创建开始时间 YYYY-MM-DD HH:MM:SS（可选）"),
    end_time: str = Query(default="", description="创建结束时间 YYYY-MM-DD HH:MM:SS（可选）"),
    skip: int = Query(default=0, description="分页跳过条数"),
    limit: int = Query(default=0, description="分页限制条数，0 表示不限制"),
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """按用户 ID 查询选股记录 — 通过 UserStrategyDao 获取用户的 strategy_id 列表再查询

    结合最新的实时行情数据一起返回。
    """
    user_id, role = user_info
    query_user_id = user_id
    strategy_ids = _resolve_strategy_ids_from_user(query_user_id, role)
    if strategy_ids is None or len(strategy_ids) <= 0:
        # 管理员且未指定 target_user_id：查询所有
        impl = MongoStrategySelectStocksImpl()
        res, records = impl.query_select_stocks(
            start_time=start_time,
            end_time=end_time,
            skip=skip,
            limit=limit,
        )
    else:
        impl = MongoStrategySelectStocksImpl()
        res, records = impl.query_select_stocks_by_strategy_ids(
            strategy_ids=strategy_ids,
            start_time=start_time,
            end_time=end_time,
            skip=skip,
            limit=limit,
        )
    if not res:
        raise HTTPException(status_code=500, detail="查询选股记录失败")

    if not records:
        return ApiResponse(data=[])

    # 提取所有唯一的股票代码，查询最新实时行情
    codes = list({r.get("code", "") for r in records if r.get("code", "")})
    rt_map: dict[str, dict[str, Any]] = {}
    if codes:
        rt_impl = MongoRtStocksImpl()
        # use_default_time=False 不限制分钟，获取每只股票的最新一条记录
        rt_ok, rt_records = rt_impl.query_latest_rt_stocks_for_codes(
            codes, use_default_time=False
        )
        if rt_ok and rt_records:
            for rt in rt_records:
                rt_code = rt.get("code", "")
                rt_map[rt_code] = rt

    rlist: list[StrategySelectStockWithRtItem] = []
    for r in records:
        code = r.get("code", "")
        rt_data = rt_map.get(code, {})
        rlist.append(
            StrategySelectStockWithRtItem(
                id=str(r.get("_id", "")),
                strategy_id=r.get("strategy_id", ""),
                code=code,
                create_time=r.get("create_time", ""),
                name=str(rt_data.get("name", "")),
                price=float(rt_data.get("price", 0.0)),
                change_percent=float(rt_data.get("change_percent", 0.0)),
                change_amount=float(rt_data.get("change_amount", 0.0)),
                volume=int(rt_data.get("volume", 0)),
                amount=float(rt_data.get("amount", 0.0)),
                amp=float(rt_data.get("amp", 0.0)),
                high=float(rt_data.get("high", 0.0)),
                low=float(rt_data.get("low", 0.0)),
                open=float(rt_data.get("open", 0.0)),
                preclose=float(rt_data.get("preclose", 0.0)),
                qrr=float(rt_data.get("qrr", 0.0)),
                turnover=float(rt_data.get("turnover", 0.0)),
                rt_update_time=str(rt_data.get("create_time", "")),
            )
        )
    return ApiResponse(data=rlist)
