#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-07-11
LastEditors: liguoqiang
LastEditTime: 2026-07-31
Description: 股票行情页面 — 严格按 stock-quotes.html 原型实现
  区1: 股票池 Tab (动态加载用户股票池)
  区2: 行情表格 (代码/名称, 最新价, 涨跌幅, 涨跌额, 换手率, 动态市盈率, 利润率, 操作)
  涨用绿色(#34d399)，跌用红色(#f87171)
  搜索支持股票代码和公司名称，切换 Tab/刷新/搜索均有加载进度显示。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from nicegui import ui

from app_context import AppContext
from components import custom_tabs

logger = logging.getLogger(__name__)

# 默认行情数据（原型中的硬编码数据 + API 降级）
_DEFAULT_QUOTES: list[dict[str, Any]] = [
    {"code": "600519", "name": "贵州茅台", "price": 1645.20, "change_pct": 1.42, "change_amt": 23.10,
     "turnover": 0.25, "ttm_pe": 27.1, "net_profit_growth_rate": 12.5},
    {"code": "000001", "name": "平安银行", "price": 12.45, "change_pct": -0.56, "change_amt": -0.07,
     "turnover": 0.75, "ttm_pe": 4.0, "net_profit_growth_rate": -3.2},
    {"code": "002594", "name": "比亚迪", "price": 245.10, "change_pct": 3.20, "change_amt": 7.60,
     "turnover": 2.84, "ttm_pe": 17.8, "net_profit_growth_rate": 25.3},
    {"code": "600030", "name": "中信证券", "price": 21.80, "change_pct": 0.93, "change_amt": 0.20,
     "turnover": 1.25, "ttm_pe": 11.9, "net_profit_growth_rate": 8.5},
    {"code": "000333", "name": "美的集团", "price": 65.40, "change_pct": -1.12, "change_amt": -0.74,
     "turnover": 0.51, "ttm_pe": 9.8, "net_profit_growth_rate": 15.1},
    {"code": "300750", "name": "宁德时代", "price": 182.45, "change_pct": 2.84, "change_amt": 5.05,
     "turnover": 1.82, "ttm_pe": 20.6, "net_profit_growth_rate": 18.7},
]


def show_stock_quotes_page() -> None:
    """股票行情页面入口 — Tab 显示用户股票池，点击切换对应股票行情。"""
    custom_tabs.load_page_tab_css()
    current_theme: dict[str, str] = AppContext().theme_manager.get_current_theme()
    bg_color: str = current_theme.get("background", "#0f0f1a")
    border_color: str = current_theme.get("widget_border_color", "#334155")
    positive_color: str = current_theme.get("positive", "#34d399")
    negative_color: str = current_theme.get("negative", "#f87171")
    card_bg: str = "#1e293b"
    input_bg: str = "#111827"

    # ---- 加载股票池列表 ----
    pools: list[dict[str, Any]] = []
    try:
        pools = AppContext().pool_api.list()
    except httpx.HTTPStatusError as e:
        logger.warning("加载股票池列表失败: %s", e.response.text, exc_info=True)
    except Exception:
        logger.warning("加载股票池列表失败", exc_info=True)

    # 提取股票池名称列表；若无股票池则使用一个默认占位 Tab
    pool_names: list[str] = [p.get("name", "未命名") for p in pools] if pools else []
    active_tab: str = pool_names[0] if pool_names else ""

    state: dict[str, Any] = {"quotes": _DEFAULT_QUOTES, "active_tab": active_tab, "loading": False}

    # 缓存每个股票池的行情数据，避免重复请求
    pool_quotes_cache: dict[str, list[dict[str, Any]]] = {}

    def load_quotes_for_pool(pool_name: str) -> list[dict[str, Any]]:
        """加载指定股票池内所有股票的实时行情。

        :param pool_name: 股票池名称
        :return: 实时行情数据列表
        """
        if not pool_name:
            return list(_DEFAULT_QUOTES)
        # 命中缓存直接返回
        if pool_name in pool_quotes_cache:
            return pool_quotes_cache[pool_name]
        try:
            stocks: list[dict[str, Any]] = AppContext().pool_api.get_stocks(pool_name)
            if not stocks:
                return []
            codes: str = ",".join(str(s.get("code", "")) for s in stocks if s.get("code"))
            if not codes:
                return []
            data: list[dict[str, Any]] = AppContext().market_api.get_real_time(codes=codes, use_default_time=False)
            # 查询估值数据（动态市盈率、利润率），合并到行情数据中
            try:
                valuation: dict[str, Any] = AppContext().finance_api.get_valuation(codes=codes)
                if valuation:
                    for item in data:
                        item_code: str = str(item.get("code", ""))
                        val: dict[str, Any] | None = valuation.get(item_code) if item_code else None
                        if isinstance(val, dict) and "ttm_pe" in val and not item.get("ttm_pe"):
                            item["ttm_pe"] = val["ttm_pe"]
            except Exception:
                logger.warning("加载估值数据失败", exc_info=True)
            # 查询利润数据（利润增长率），合并到行情数据中
            try:
                profit: dict[str, Any] = AppContext().finance_api.get_profit(codes=codes)
                if profit:
                    for item in data:
                        item_code: str = str(item.get("code", ""))
                        pft: dict[str, Any] | None = profit.get(item_code) if item_code else None
                        if isinstance(pft, dict) and "net_profit_growth_rate" in pft and not item.get("net_profit_growth_rate"):
                            item["net_profit_growth_rate"] = pft["net_profit_growth_rate"]
            except Exception:
                logger.warning("加载利润数据失败", exc_info=True)
            pool_quotes_cache[pool_name] = data if data else []
            return pool_quotes_cache[pool_name]
        except httpx.HTTPStatusError as e:
            logger.warning("加载股票池「%s」行情失败: %s", pool_name, e.response.text, exc_info=True)
        except Exception:
            logger.warning("加载股票池「%s」行情失败", pool_name, exc_info=True)
        return list(_DEFAULT_QUOTES)

    async def on_search() -> None:
        """搜索/刷新股票行情，显示加载进度。"""
        state["loading"] = True
        table_panel.refresh()
        await asyncio.sleep(0)
        loop = asyncio.get_running_loop()
        keyword: str = (search_input.value or "").strip()
        if keyword:
            state["quotes"] = await loop.run_in_executor(None, _search_stocks, keyword)
        else:
            cur: str = state["active_tab"]
            if cur:
                pool_quotes_cache.pop(cur, None)
                state["quotes"] = await loop.run_in_executor(None, load_quotes_for_pool, cur)
        state["loading"] = False
        table_panel.refresh()

    def _search_stocks(keyword: str) -> list[dict[str, Any]]:
        """根据关键字搜索股票行情（支持代码和公司名称）。

        先通过 stock_info 接口将名称/拼音解析为股票代码，
        再通过 real_time 接口获取行情，最后补充估值和利润数据。

        :param keyword: 股票代码、公司名称或拼音关键字
        :return: 行情数据列表
        """
        # 第一步：将关键字解析为股票代码
        codes_str: str = keyword
        try:
            stock_list: list[dict[str, Any]] = AppContext().stock_info_api.get_list(limit=5000)
            kw_lower: str = keyword.lower()
            matched: list[dict[str, Any]] = [
                s for s in stock_list
                if kw_lower in str(s.get("name", "")).lower()
                or kw_lower in str(s.get("code", "")).lower()
                or kw_lower in str(s.get("pinyin", s.get("py", ""))).lower()
            ]
            if matched:
                codes_str = ",".join(
                    str(s.get("code", "")) for s in matched[:30] if s.get("code")
                )
        except Exception:
            logger.warning("解析股票关键字「%s」失败，直接使用原始关键字", keyword, exc_info=True)

        if not codes_str:
            return []

        # 第二步：获取行情数据
        try:
            data: list[dict[str, Any]] = AppContext().market_api.get_real_time(
                codes=codes_str, use_default_time=False
            )
            if data:
                # 按 code 去重
                seen_codes: set[str] = set()
                deduped: list[dict[str, Any]] = []
                for item in data:
                    c: str = str(item.get("code", ""))
                    if c and c not in seen_codes:
                        seen_codes.add(c)
                        deduped.append(item)
                data = deduped
                # 补充估值和利润数据
                all_codes: str = ",".join(
                    str(item.get("code", "")) for item in data if item.get("code")
                )
                if all_codes:
                    try:
                        valuation_map: dict[str, dict[str, Any]] = (
                            AppContext().finance_api.get_valuation(codes=all_codes)
                        )
                        if valuation_map:
                            for item in data:
                                item_code: str = str(item.get("code", ""))
                                if item_code and item_code in valuation_map:
                                    val_data: dict[str, Any] = valuation_map[item_code]
                                    if not item.get("ttm_pe"):
                                        item["ttm_pe"] = val_data.get("ttm_pe", 0)
                    except Exception:
                        logger.warning("搜索估值数据失败", exc_info=True)
                    try:
                        profit_map: dict[str, dict[str, Any]] = (
                            AppContext().finance_api.get_profit(codes=all_codes)
                        )
                        if profit_map:
                            for item in data:
                                item_code: str = str(item.get("code", ""))
                                if item_code and item_code in profit_map:
                                    profit_data: dict[str, Any] = profit_map[item_code]
                                    if not item.get("net_profit_growth_rate"):
                                        item["net_profit_growth_rate"] = profit_data.get(
                                            "net_profit_growth_rate", 0
                                        )
                    except Exception:
                        logger.warning("搜索利润数据失败", exc_info=True)
            return data if data else []
        except Exception:
            logger.warning("搜索股票「%s」失败", keyword, exc_info=True)
            return []

    async def on_tab_switch(tab_name: str) -> None:
        """切换股票池 Tab 并加载对应行情（异步，显示加载进度）。

        :param tab_name: 股票池名称
        """
        state["active_tab"] = tab_name
        state["loading"] = True
        table_panel.refresh()
        await asyncio.sleep(0)
        loop = asyncio.get_running_loop()
        state["quotes"] = await loop.run_in_executor(None, load_quotes_for_pool, tab_name)
        state["loading"] = False
        table_panel.refresh()

    # 初始加载首个股票池行情
    if active_tab:
        state["quotes"] = load_quotes_for_pool(active_tab)

    # ---- 顶部栏 ----
    with ui.row().classes("w-full items-center justify-between").style(
        f"height: 64px; padding: 0 32px; background-color: {card_bg}; "
        f"border-bottom: 1px solid {border_color};"
    ):
        ui.label("股票实时行情").classes("text-xl font-bold").style("color: #f3f4f6;")
        with ui.row().classes("items-center gap-4"):
            search_input: ui.input = (
                ui.input(placeholder="输入代码或名称...")
                .props("outlined dense")
                .style(f"width: 256px; background-color: {input_bg};")
            )
            search_input.on("keydown.enter", on_search)
            ui.button(icon="refresh", on_click=on_search).props("flat round dense") \
                .style(f"color: #9ca3af; background-color: {input_bg};")

    # ---- 页面主体 ----
    with ui.column().classes("w-full gap-0").style(
        f"padding: 32px; background-color: {bg_color}; overflow-y: auto; flex: 1;"
    ):
        # ---- 股票池 Tab ----
        with ui.row().classes("w-full items-center gap-4").style(
            f"border-bottom: 1px solid {border_color}; margin-bottom: 24px;"
        ):
            if pool_names:
                for pn in pool_names:
                    is_active: bool = pn == state["active_tab"]
                    if is_active:
                        ui.label(pn).classes("text-sm font-bold").style(
                            "color: #60a5fa; border-bottom: 2px solid #3b82f6; "
                            "padding: 12px 24px; cursor: pointer;"
                        ).on("click", lambda _n=pn: on_tab_switch(_n))
                    else:
                        ui.label(pn).classes("text-sm").style(
                            "color: #9ca3af; border-bottom: 2px solid transparent; "
                            "padding: 12px 24px; cursor: pointer;"
                        ).on("click", lambda _n=pn: on_tab_switch(_n))
            else:
                ui.label("暂无股票池，请先创建股票池").classes("text-sm").style(
                    "color: #9ca3af; padding: 12px 24px;"
                )

        # ---- 行情表格 ----
        @ui.refreshable
        def table_panel() -> None:
            """渲染行情表格，加载中时显示进度动画。"""
            quotes: list[dict[str, Any]] = state["quotes"]

            with ui.card().classes("w-full gap-0 overflow-hidden").style(
                f"background-color: {card_bg}; border: 1px solid {border_color}; border-radius: 12px;"
            ):
                # 表头
                headers: list[tuple[str, str]] = [
                    ("代码/名称", "left"), ("最新价", "left"), ("涨跌幅", "left"),
                    ("涨跌额", "left"), ("换手率", "left"), ("动态市盈率", "left"),
                    ("利润增长率", "left"), ("操作", "right"),
                ]
                with ui.row().classes("w-full items-center").style(
                    f"background-color: {input_bg}; border-bottom: 1px solid {border_color}; "
                    f"padding: 12px 24px;"
                ):
                    for hdr, _align in headers:
                        flex_val: str = "2" if hdr == "代码/名称" else "1"
                        ui.label(hdr).classes("text-xs font-semibold uppercase tracking-wider").style(
                            f"color: #9ca3af; flex: {flex_val};"
                        )

                # 加载进度
                if state.get("loading", False):
                    with ui.row().classes("w-full justify-center items-center gap-3").style("padding: 48px;"):
                        ui.spinner(size="md", color="blue")
                        ui.label("正在加载行情数据...").classes("text-sm").style("color: #9ca3af;")

                # 数据行
                for q in quotes:
                    code: str = str(q.get("code", ""))
                    name: str = str(q.get("name", ""))
                    price: float = _sf(q.get("price", q.get("close", 0)))
                    chg_pct: float = _sf(q.get("change_pct", q.get("change_percent", 0))) * 100
                    chg_amt: float = _sf(q.get("change_amt", q.get("change_amount", price * chg_pct / 100)))
                    to_rate: float = _sf(q.get("turnover", q.get("turnover_rate", 0))) * 100
                    dynamic_pe: float = _sf(q.get("ttm_pe", 0))
                    profit_growth: float = _sf(q.get("net_profit_growth_rate", 0))

                    if chg_pct > 0:
                        price_color = negative_color  # 涨为红色
                    elif chg_pct < 0:
                        price_color = positive_color  # 跌为绿色
                    else:
                        price_color = "#ffffff"  # 平为白色

                    with ui.row().classes("w-full items-center").style(
                        f"padding: 16px 24px; border-bottom: 1px solid {border_color}33;"
                    ):
                        # 代码/名称
                        with ui.column().classes("gap-0").style("flex: 2;"):
                            ui.label(code).classes("font-bold tracking-wider").style("color: #e5e7eb;")
                            ui.label(name).classes("text-xs").style("color: #6b7280;")

                        ui.label(f"{price:,.2f}").classes("font-bold").style(f"color: {price_color}; flex: 1;")
                        sign: str = "+" if chg_pct >= 0 else ""
                        ui.label(f"{sign}{chg_pct:.2f}%").classes("font-bold").style(f"color: {price_color}; flex: 1;")
                        ui.label(f"{sign}{chg_amt:,.2f}").classes("font-medium").style(f"color: {price_color}; flex: 1;")
                        ui.label(f"{to_rate:.2f}%").classes("text-sm").style("color: #9ca3af; flex: 1;")
                        pe_text: str = "亏损" if dynamic_pe < 0 else f"{dynamic_pe:.1f}"
                        ui.label(pe_text).classes("text-sm").style("color: #9ca3af; flex: 1;")
                        ui.label(f"{profit_growth:.1f}%").classes("text-sm").style("color: #9ca3af; flex: 1;")
                        ui.button("分析", on_click=lambda c=code: ui.notify(f"分析 {c}", color="info")) \
                            .props("flat dense") \
                            .style("color: #60a5fa; font-size: 12px; font-weight: 700; flex: 1; text-align: right;")

        table_panel()


def _sf(value: Any) -> float:
    """安全转 float。

    :param value: 待转换值
    :return: float
    """
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0
