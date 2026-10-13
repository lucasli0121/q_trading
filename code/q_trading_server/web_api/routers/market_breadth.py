"""
Author: liguoqiang
Date: 2026-10-13 17:00:00
LastEditors: liguoqiang
LastEditTime: 2026-10-13 17:00:00
Description: 每日市场宽度路由 — 涨跌停家数与门槛偏移的保存与查询

写入方是 q_trading_work 盘前任务（admin token），读取方是同一进程，
因此全部端点要求管理员权限。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from db.mongo.mongo_market_breadth_impl import MongoMarketBreadthImpl
from web_api.auth import require_admin
from web_api.models import (
    ApiResponse,
    MarketBreadthItem,
    MarketBreadthSaveRequest,
)

router = APIRouter(prefix="/api/market_breadth", tags=["每日市场宽度"])


def _to_item(record: dict, trade_date: str = "") -> MarketBreadthItem:
    """把数据库记录转换成响应模型。"""
    return MarketBreadthItem(
        id=str(record.get("_id", "")),
        trade_date=record.get("trade_date", trade_date),
        limit_up=int(record.get("limit_up", 0) or 0),
        limit_down=int(record.get("limit_down", 0) or 0),
        buy_offset=float(record.get("buy_offset", 0.0) or 0.0),
        sell_offset=float(record.get("sell_offset", 0.0) or 0.0),
        source=record.get("source", ""),
        update_time=record.get("update_time", ""),
    )


@router.post("/add", response_model=ApiResponse[MarketBreadthItem])
def add_market_breadth(
    req: MarketBreadthSaveRequest,
    _admin_id: str = Depends(require_admin),
):
    """保存某交易日的涨跌停家数与门槛偏移（按 trade_date 覆盖）。"""
    if not req.trade_date:
        raise HTTPException(status_code=400, detail="trade_date 不能为空")
    impl = MongoMarketBreadthImpl()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record = {
        "trade_date": req.trade_date,
        "limit_up": int(req.limit_up),
        "limit_down": int(req.limit_down),
        "buy_offset": float(req.buy_offset),
        "sell_offset": float(req.sell_offset),
        "source": req.source,
        "update_time": now,
    }
    ok, record_id = impl.upsert_breadth(record)
    if not ok:
        raise HTTPException(status_code=500, detail="保存市场宽度失败")
    record["_id"] = record_id or ""
    return ApiResponse(data=_to_item(record, req.trade_date), message="保存成功")


@router.get("/list", response_model=ApiResponse[list[MarketBreadthItem]])
def list_market_breadth(
    trade_date: str = Query(default="", description="交易日 YYYY-MM-DD，空表示不过滤（按日期倒序）"),
    _admin_id: str = Depends(require_admin),
):
    """查询市场宽度记录。"""
    impl = MongoMarketBreadthImpl()
    ok, records = impl.query_breadth(trade_date)
    if not ok:
        raise HTTPException(status_code=500, detail="查询市场宽度失败")
    items: list[MarketBreadthItem] = [_to_item(r, trade_date) for r in (records or [])]
    return ApiResponse(data=items, message="查询成功")
