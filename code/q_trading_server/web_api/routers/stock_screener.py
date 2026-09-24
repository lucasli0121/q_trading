"""
Author: liguoqiang
Date: 2026-06-25 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-06-25 13:30:00
Description: 股票筛选路由 - 按 TTM 市盈率、总市值、利润率范围筛选股票
"""

from typing import cast

from fastapi import APIRouter, HTTPException, Query
from utils import tools
from db.mongo.mongo_company_finance_impl import MongoCompanyFinanceImpl
from db.mongo.mongo_company_valuation_impl import MongoCompanyValuationImpl
from web_api.models import ApiResponse, StockScreenerItem

router = APIRouter(prefix="/api/screener", tags=["股票筛选"])


@router.get("/search", response_model=ApiResponse[list[StockScreenerItem]])
def search_stocks(
    ttm_min: float = Query(default=0.0, description="TTM市盈率下限（含），0 表示不限制"),
    ttm_max: float = Query(default=0.0, description="TTM市盈率上限（含），0 表示不限制"),
    cap_min: float = Query(default=0.0, description="总市值下限（含），单位：亿元，0 表示不限制"),
    cap_max: float = Query(default=0.0, description="总市值上限（含），单位：亿元，0 表示不限制"),
    margin_min: float = Query(default=0.0, description="利润率下限（含），如 0.15 表示 15%，0 表示不限制"),
    margin_max: float = Query(default=0.0, description="利润率上限（含），如 0.30 表示 30%，0 表示不限制"),
    skip: int = Query(default=0, description="分页跳过条数"),
    limit: int = Query(default=100, description="分页返回条数，0 表示不限制"),
):
    """按财务指标范围筛选股票

    综合查询公司估值表（TTM市盈率、总市值）和公司财务表（利润率=净利润/营业总收入），
    返回同时满足所有范围条件的股票列表。
    每个范围参数为 0 表示该维度不设限制。

    分页在最后一次性完成：利润率来自财务表，必须等两表数据都到齐、过滤完之后才能分页。
    原实现在库侧先按 skip/limit 切一刀、过滤后又切第二刀，skip>0 时几乎必然返回空。
    """
    # 1. 从估值表按 TTM 和市值范围查询（这里不分页）
    val_impl = MongoCompanyValuationImpl()
    # 参数单位为亿元，转换为实际数值后查询
    val_ok, val_records = val_impl.query_valuation_by_ranges(
        ttm_min=ttm_min,
        ttm_max=ttm_max,
        cap_min=cap_min * 1e8 if cap_min > 0 else 0,
        cap_max=cap_max * 1e8 if cap_max > 0 else 0,
        skip=0,
        limit=0,
    )
    if not val_ok:
        raise HTTPException(status_code=500, detail="查询估值数据失败")
    if not val_records:
        return ApiResponse(data=[])

    
    # 建立 code → valuation 映射
    val_map: dict[str, dict] = {}
    for r in val_records:
        key = r.get("code", "")
        if key:
            val_map[key] = r

    val_codes: list[str] = list(val_map.keys())
    # 3. 批量查询最新财务数据
    fin_impl = MongoCompanyFinanceImpl()
    fin_ok, fin_records = fin_impl.query_latest_finance_by_codes(val_codes)
    if not fin_ok:
        raise HTTPException(status_code=500, detail="查询财务数据失败")
    if not fin_records:
        return ApiResponse(data=[])

    # 4. 计算利润率并过滤
    rlist: list[StockScreenerItem] = []
    for f in fin_records:
        code = f.get("code", "")
        key = tools.guess_stock_code_suffix(code)
        val = val_map.get(key)
        if val is None:
            continue

        total_revenue = float(f.get("total_revenue", 0) or 0)
        net_profit = float(f.get("net_profit", 0) or 0)
        profit_margin = (net_profit / total_revenue) if total_revenue > 0 else 0.0

        # 利润率范围过滤
        if margin_min > 0 and profit_margin < margin_min:
            continue
        if margin_max > 0 and profit_margin > margin_max:
            continue

        rlist.append(StockScreenerItem(
            code=code,
            name=val.get("name", ""),
            ttm_pe=float(val.get("ttm_pe", 0) or 0),
            total_market_cap=float(val.get("total_market_cap", 0) or 0),
            profit_margin=round(profit_margin, 4),
            report_date=f.get("report_date", ""),
        ))

    # 5. 排序后一次性分页（按 code 排序，保证翻页结果稳定）
    rlist.sort(key=lambda x: x.code)
    if skip > 0:
        rlist = rlist[skip:]
    if limit > 0:
        rlist = rlist[:limit]

    return ApiResponse(data=rlist)
