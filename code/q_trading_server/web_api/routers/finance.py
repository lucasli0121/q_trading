"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-06-22 13:30:00
Description: 财务管理路由 - 股票基本信息、估值、利润
"""

from fastapi import APIRouter, HTTPException, Query

from db.mongo.mongo_company_finance_impl import MongoCompanyFinanceImpl
from db.mongo.mongo_company_valuation_impl import MongoCompanyValuationImpl
from db.mongo.mongo_stock_info_impl import MongoStockInfoImpl
from web_api.models import ApiResponse

router = APIRouter(prefix="/api/finance", tags=["财务管理"])


@router.get("/stock_info", response_model=ApiResponse[list])
async def get_stock_info(code: str = Query(..., description="股票代码")):
    """查询公司基本信息"""
    impl = MongoStockInfoImpl()
    res, records = impl.query_stock_info(code)
    if not res:
        raise HTTPException(status_code=500, detail="查询股票信息失败")
    rlist = []
    for r in (records or []):
        r["_id"] = str(r.get("_id", ""))
        rlist.append(r)
    return ApiResponse(data=rlist)


@router.get("/stock_list", response_model=ApiResponse[list])
async def get_stock_list(
    skip: int = Query(default=0),
    limit: int = Query(default=100),
):
    """查询股票列表（分页）"""
    impl = MongoStockInfoImpl()
    res, records = impl.query_all_stock_info(skip=skip, limit=limit)
    if not res:
        raise HTTPException(status_code=500, detail="查询股票列表失败")
    rlist = []
    for r in (records or []):
        r["_id"] = str(r.get("_id", ""))
        rlist.append(r)
    return ApiResponse(data=rlist)


@router.get("/valuation", response_model=ApiResponse[list])
async def get_valuation(
    code: str = Query(default="", description="单只股票代码"),
    codes: str = Query(default="", description="多只股票代码，逗号分隔，如 000001,600519"),
):
    """查询估值数据 — 单只或多只股票"""
    if code:
        code_list: str | list[str] = code
    elif codes:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
    else:
        raise HTTPException(status_code=400, detail="请提供 code 或 codes 参数")
    impl = MongoCompanyValuationImpl()
    res, records = impl.query_company_valuation(code_list)
    if not res:
        raise HTTPException(status_code=500, detail="查询估值数据失败")
    rlist = []
    for r in (records or []):
        r["_id"] = str(r.get("_id", ""))
        rlist.append(r)
    return ApiResponse(data=rlist)


@router.get("/profit", response_model=ApiResponse[list])
async def get_profit(
    code: str = Query(default="", description="单只股票代码"),
    codes: str = Query(default="", description="多只股票代码，逗号分隔，如 000001,600519"),
    report_date: str = Query(default="", description="报告期 YYYY-MM-DD"),
):
    """查询利润数据 — 单只或多只股票"""
    if code:
        code_list: str | list[str] = code
    elif codes:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
    else:
        raise HTTPException(status_code=400, detail="请提供 code 或 codes 参数")
    impl = MongoCompanyFinanceImpl()
    res, records = impl.query_company_finance(code_list, report_date)
    if not res:
        raise HTTPException(status_code=500, detail="查询利润数据失败")
    rlist = []
    for r in (records or []):
        r["_id"] = str(r.get("_id", ""))
        rlist.append(r)
    return ApiResponse(data=rlist)
