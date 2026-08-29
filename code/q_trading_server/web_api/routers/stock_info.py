"""
Author: liguoqiang
Date: 2026-06-24 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-06-24 13:30:00
Description: 股票信息查询路由 - 按代码、全部列表、按板块查询
"""

import asyncio

from fastapi import APIRouter, HTTPException, Query

from app_context import AppContext
from db.mongo.mongo_data_agent_industry_stocks_impl import MongoDataAgentIndustryStocksImpl
from db.mongo.mongo_hot_industry_impl import MongoHotIndustryImpl
from db.mongo.mongo_stock_info_impl import MongoStockInfoImpl
from web_api.models import ApiResponse, HotIndustryCreateRequest, HotIndustryItem, StockInfoItem

router = APIRouter(prefix="/api/stock_info", tags=["股票信息查询"])


def _records_to_items(records: list[dict]) -> list[StockInfoItem]:
    """将数据库记录转换为 StockInfoItem 列表"""
    return [
        StockInfoItem(
            code=r.get("code", ""),
            name=r.get("name", ""),
            full_name=r.get("full_name", ""),
            board=r.get("board", ""),
            industry=r.get("industry", ""),
            concept=r.get("concept", ""),
            list_date=r.get("list_date", ""),
        )
        for r in records
    ]


@router.get("/code", response_model=ApiResponse[list[StockInfoItem]])
async def get_stock_info_by_codes(
    codes: str = Query(..., description="股票代码，多个用逗号分隔，如 000001.SZ,600519.SH"),
):
    """根据股票代码查询基本信息（支持单个或多个）"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        raise HTTPException(status_code=400, detail="请提供至少一个股票代码")
    impl = MongoStockInfoImpl()
    res, records = impl.query_by_codes(code_list)
    if not res:
        raise HTTPException(status_code=500, detail="查询股票信息失败")
    return ApiResponse(data=_records_to_items(records or []))


@router.get("/list", response_model=ApiResponse[list[StockInfoItem]])
async def get_stock_info_list(
    skip: int = Query(default=0, description="跳过条数"),
    limit: int = Query(default=100, description="返回条数，0 表示不限制"),
):
    """查询全部股票基本信息（分页）"""
    impl = MongoStockInfoImpl()
    res, records = impl.query_all_stock_info(skip=skip, limit=limit)
    if not res:
        raise HTTPException(status_code=500, detail="查询股票列表失败")
    return ApiResponse(data=_records_to_items(records or []))


@router.get("/board", response_model=ApiResponse[list[StockInfoItem]])
async def get_stock_info_by_board(
    board: str = Query(..., description="板块名称，如 主板、创业板、科创板、北交所"),
    skip: int = Query(default=0, description="跳过条数"),
    limit: int = Query(default=100, description="返回条数，0 表示不限制"),
):
    """根据板块查询股票基本信息"""
    impl = MongoStockInfoImpl()
    res, records = impl.query_by_board(board, skip=skip, limit=limit)
    if not res:
        raise HTTPException(status_code=500, detail="按板块查询股票信息失败")
    return ApiResponse(data=_records_to_items(records or []))


@router.get("/industry", response_model=ApiResponse[list[StockInfoItem]])
async def get_stock_info_by_industry(
    industry: str = Query(..., description="行业名称，如 半导体、白酒、医疗器械"),
    skip: int = Query(default=0, description="跳过条数"),
    limit: int = Query(default=100, description="返回条数，0 表示不限制"),
):
    """根据行业查询股票基本信息"""
    impl = MongoStockInfoImpl()
    res, records = impl.query_by_industry(industry, skip=skip, limit=limit)
    if not res:
        raise HTTPException(status_code=500, detail="按行业查询股票信息失败")
    return ApiResponse(data=_records_to_items(records or []))


# ==================== 热门行业管理 ====================


@router.get("/hot_industry/list", response_model=ApiResponse[list[HotIndustryItem]])
async def list_hot_industries():
    """查询所有热门行业"""
    impl = MongoHotIndustryImpl()
    res, records = impl.list_hot_industries()
    if not res:
        raise HTTPException(status_code=500, detail="查询热门行业列表失败")
    items = [HotIndustryItem(name=r.get("name", "")) for r in (records or [])]
    return ApiResponse(data=items)


@router.post("/hot_industry/add", response_model=ApiResponse[str])
async def add_hot_industry(req: HotIndustryCreateRequest):
    """添加热门行业"""
    impl = MongoHotIndustryImpl()
    ok, result = impl.add_hot_industry(req.name)
    if not ok:
        raise HTTPException(status_code=400, detail=str(result) if result else "添加热门行业失败")
    await AppContext().stock_fetch.distribute_industry_stocks_to_data_agents()
    return ApiResponse(data=str(result))


@router.delete("/hot_industry/delete", response_model=ApiResponse[None])
async def delete_hot_industry(name: str = Query(..., description="行业名称")):
    """删除热门行业"""
    impl = MongoHotIndustryImpl()
    ok = impl.delete_hot_industry(name)
    if not ok:
        raise HTTPException(status_code=400, detail="删除热门行业失败")
    agent_industry_impl = MongoDataAgentIndustryStocksImpl()
    agent_industry_impl.delete_by_industry(name)
    return ApiResponse()
