"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-06-22 13:30:00
Description: 行情管理路由 - 实时行情、日K/周K/月K/分钟K线
"""

from datetime import datetime

import pandas as pd

from fastapi import APIRouter, HTTPException, Query

from db.mongo.mongo_company_valuation_impl import MongoCompanyValuationImpl
from db.mongo.mongo_rt_stocks_impl import MongoRtStocksImpl
from db.mongo.mongo_stock_minute_hq_impl import MongoStockMinuteHqImpl
from app_context import AppContext
from web_api.models import ApiResponse

router = APIRouter(prefix="/api/market", tags=["行情管理"])


@router.get("/real_time", response_model=ApiResponse[list])
async def get_real_time(
    code: str = Query(default="", description="单只股票代码"),
    codes: str = Query(default="", description="多只股票代码，逗号分隔，如 000001,600519"),
    start_time: str = Query(default="", description="开始时间 YYYY-MM-DD HH:MM:SS"),
    end_time: str = Query(default="", description="结束时间 YYYY-MM-DD HH:MM:SS"),
    use_default_time: bool = Query(default=False, description="使用默认时间范围（当天 00:00:00 到当前时间），仅在未传 start_time/end_time 时生效"),
):
    """实时行情查询 — 单只或多只股票，支持时间范围过滤

    不传 start_time/end_time: 返回每只股票的最新一条记录
    传入 start_time/end_time: 返回指定时间范围内的所有记录
    use_default_time=True 且未传 start_time/end_time: 使用当天 00:00:00 到当前时间作为默认范围
    """
    if code:
        code_list = [code]
    elif codes:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
    else:
        raise HTTPException(status_code=400, detail="请提供 code 或 codes 参数")

    rt_impl = MongoRtStocksImpl()
    rlist: list[dict] = []

    if start_time or end_time:
        # 按时间范围查询：传入代码列表一次查询
        res, records = rt_impl.query_rt_stocks(code_list, start_time, end_time)
        if res and records:
            for r in records:
                r["_id"] = str(r.get("_id", ""))
                rlist.append(r)
    else:
        # 无时间范围：取每只股票的最新一条
        res, records = rt_impl.query_latest_rt_stocks_for_codes(code_list, use_default_time=use_default_time)
        if not res:
            raise HTTPException(status_code=500, detail="查询实时行情失败")
        for r in (records or []):
            r["_id"] = str(r.get("_id", ""))
            rlist.append(r)

    return ApiResponse(data=rlist)


@router.get("/kline/day", response_model=ApiResponse[list])
async def get_day_kline(
    code: str = Query(default="", description="单只股票代码"),
    codes: str = Query(default="", description="多只股票代码，逗号分隔，如 000001,600519"),
    start: str = Query(default="", description="开始日期 YYYY-MM-DD"),
    end: str = Query(default="", description="结束日期 YYYY-MM-DD"),
):
    """日K历史行情"""
    if code:
        code_list = [code]
    elif codes:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
    else:
        raise HTTPException(status_code=400, detail="请提供 code 或 codes 参数")
    df = AppContext().stock_fetch.get_stock_day_his_hq(code_list, start, end)
    if df.empty:
        return ApiResponse(data=[])
    df = df.drop(columns=["_id"], errors="ignore")
    result = df.where(df.notna(), other=None).to_dict(orient="records")
    return ApiResponse(data=result)


@router.get("/kline/week", response_model=ApiResponse[list])
async def get_week_kline(
    code: str = Query(default="", description="单只股票代码"),
    codes: str = Query(default="", description="多只股票代码，逗号分隔，如 000001,600519"),
    start: str = Query(default="", description="开始日期 YYYY-MM-DD"),
    end: str = Query(default="", description="结束日期 YYYY-MM-DD"),
):
    """周K历史行情"""
    if code:
        code_list = [code]
    elif codes:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
    else:
        raise HTTPException(status_code=400, detail="请提供 code 或 codes 参数")
    df = AppContext().stock_fetch.get_stock_week_his_hq(code_list, start, end)
    if df.empty:
        return ApiResponse(data=[])
    df = df.drop(columns=["_id"], errors="ignore")
    result = df.where(df.notna(), other=None).to_dict(orient="records")
    return ApiResponse(data=result)


@router.get("/kline/month", response_model=ApiResponse[list])
async def get_month_kline(
    code: str = Query(default="", description="单只股票代码"),
    codes: str = Query(default="", description="多只股票代码，逗号分隔，如 000001,600519"),
    start: str = Query(default="", description="开始日期 YYYY-MM-DD"),
    end: str = Query(default="", description="结束日期 YYYY-MM-DD"),
):
    """月K历史行情"""
    if code:
        code_list = [code]
    elif codes:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
    else:
        raise HTTPException(status_code=400, detail="请提供 code 或 codes 参数")
    df = AppContext().stock_fetch.get_stock_month_his_hq(code_list, start, end)
    if df.empty:
        return ApiResponse(data=[])
    df = df.drop(columns=["_id"], errors="ignore")
    result = df.where(df.notna(), other=None).to_dict(orient="records")
    return ApiResponse(data=result)


@router.get("/kline/minute", response_model=ApiResponse[list])
async def get_minute_kline(
    code: str = Query(..., description="股票代码"),
    start: str = Query(default="", description="开始分钟时间 YYYY-MM-DD HH:MM:00"),
    end: str = Query(default="", description="结束分钟时间 YYYY-MM-DD HH:MM:00"),
    skip: int = Query(default=0),
    limit: int = Query(default=0),
):
    """分钟K线行情"""
    impl = MongoStockMinuteHqImpl()
    res, records = impl.query_minute_hq(code, start, end, skip=skip, limit=limit)
    if not res:
        raise HTTPException(status_code=500, detail="查询分钟K线失败")
    rlist = []
    for r in (records or []):
        r["_id"] = str(r.get("_id", ""))
        rlist.append(r)
    return ApiResponse(data=rlist)


# ==================== 估值查询 ====================


@router.get("/valuation/by_cap", response_model=ApiResponse[list])
async def get_codes_by_cap_range(
    cap_min: float = Query(default=0.0, description="总市值下限（含），单位：亿元，0 表示不限制"),
    cap_max: float = Query(default=0.0, description="总市值上限（含），单位：亿元，0 表示不限制"),
    skip: int = Query(default=0, description="分页跳过条数"),
    limit: int = Query(default=0, description="分页返回条数，0 表示不限制"),
):
    """按总市值范围查询股票代码列表（公开接口，无需登录）

    返回各股票最新一条估值记录，包含 code、name、total_market_cap、ttm_pe 等字段。
    """
    if cap_min <= 0 and cap_max <= 0:
        raise HTTPException(status_code=400, detail="请至少提供 cap_min 或 cap_max 参数")
    impl = MongoCompanyValuationImpl()
    # 参数单位为亿元，转换为实际数值后查询
    ok, records = impl.query_codes_by_cap_range(
        cap_min=cap_min * 1e8 if cap_min > 0 else 0,
        cap_max=cap_max * 1e8 if cap_max > 0 else 0,
        skip=skip,
        limit=limit,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="查询失败")
    rlist = []
    for r in (records or []):
        r["_id"] = str(r.get("_id", ""))
        rlist.append(r)
    return ApiResponse(data=rlist)


@router.get("/valuation/by_ttm_pe", response_model=ApiResponse[list])
async def get_codes_by_ttm_range(
    ttm_min: float = Query(default=0.0, description="TTM市盈率下限（含），0 表示不限制"),
    ttm_max: float = Query(default=0.0, description="TTM市盈率上限（含），0 表示不限制"),
    skip: int = Query(default=0, description="分页跳过条数"),
    limit: int = Query(default=0, description="分页返回条数，0 表示不限制"),
):
    """按 TTM 市盈率范围查询股票代码列表（公开接口，无需登录）

    返回各股票最新一条估值记录，包含 code、name、ttm_pe、total_market_cap 等字段。
    """
    if ttm_min <= 0 and ttm_max <= 0:
        raise HTTPException(status_code=400, detail="请至少提供 ttm_min 或 ttm_max 参数")
    impl = MongoCompanyValuationImpl()
    ok, records = impl.query_codes_by_ttm_range(
        ttm_min=ttm_min,
        ttm_max=ttm_max,
        skip=skip,
        limit=limit,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="查询失败")
    rlist = []
    for r in (records or []):
        r["_id"] = str(r.get("_id", ""))
        rlist.append(r)
    return ApiResponse(data=rlist)
