#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-05-24 15:17:28
LastEditors: liguoqiang
LastEditTime: 2026-06-01
Description: 推荐页面 — 展示股票列表及走势图表
"""

import logging

from db.mongo.mongo_stock_info_impl import MongoStockInfoImpl
from nicegui import ui

from app_context import AppContext
from components.stocks_introduce_widght import show_stocks_introduce

logger = logging.getLogger(__name__)

# 默认展示的股票代码列表（作为降级方案）
_FALLBACK_STOCKS: list[dict] = [
    {"code": "000001", "name": "平安银行"},
    {"code": "000002", "name": "万科A"},
    {"code": "600000", "name": "浦发银行"},
    {"code": "600519", "name": "贵州茅台"},
    {"code": "000858", "name": "五粮液"},
]


def _load_stock_list() -> list[dict]:
    """从 MongoDB 加载股票列表，优先监控股票，不足时补充全部股票，失败时使用降级数据"""
    try:
        db_impl = MongoStockInfoImpl()
        stocks = []

        # 优先加载监控股票
        ok, monitor_stocks = db_impl.query_monitor_stocks(limit=30)
        if ok and monitor_stocks:
            for doc in monitor_stocks:
                code = doc.get("code", "")
                name = doc.get("name", "")
                if code and name:
                    pure_code = code.split(".")[0] if "." in code else code
                    stocks.append({"code": pure_code, "name": name})

        # 监控股票不足时，补充全部股票
        if len(stocks) < 10:
            ok, all_stocks = db_impl.query_all_stock_info(limit=50)
            if ok and all_stocks:
                seen = {s["code"] for s in stocks}
                for doc in all_stocks:
                    code = doc.get("code", "")
                    name = doc.get("name", "")
                    if code and name:
                        pure_code = code.split(".")[0] if "." in code else code
                        if pure_code not in seen:
                            stocks.append({"code": pure_code, "name": name})
                            seen.add(pure_code)
                    if len(stocks) >= 30:
                        break

        if stocks:
            logger.info(f"加载了 {len(stocks)} 只股票")
            return stocks
    except Exception as e:  # noqa: BLE001
        logger.warning(f"从 MongoDB 加载股票列表失败: {e}")

    logger.info("使用默认股票列表")
    return _FALLBACK_STOCKS


def show_recommend_page() -> None:
    """显示推荐页面：股票列表 + 分时图 + K 线图"""
    stock_list = _load_stock_list()

    current_theme = AppContext().theme_manager.get_current_theme()
    bg_color = current_theme.get("background", "#1e1e1e")
    font_color = current_theme.get("font_color", "#c9d1d9")

    with ui.column().classes("w-full h-full gap-0").style(
        f"background-color: {bg_color}; padding: 0; margin: 0;"
    ):
        # 顶部标题栏
        with ui.row().classes("w-full items-center gap-4").style(
            f"padding: 8px 16px; border-bottom: 1px solid {current_theme.get('widget_border_color', '#80808033')};"
        ):
            ui.label("📈 推荐关注").classes("text-lg font-bold").style(
                f"color: {current_theme.get('accent', '#d7ba7d')};"
            )
            ui.label(f"共 {len(stock_list)} 只股票").classes("text-sm").style(
                f"color: {font_color};"
            )

        # 股票展示组件
        show_stocks_introduce(stock_list)