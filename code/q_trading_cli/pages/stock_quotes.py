#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-07-11
LastEditors: liguoqiang
LastEditTime: 2026-08-30
Description: 股票行情页面 — 严格按 stock-quotes.html 原型实现
  区1: 股票池 Tab (动态加载用户股票池)
  区2: 行情表格 (代码/名称, 最新价, 涨跌幅, 涨跌额, 换手率, 动态市盈率, 利润率, 操作)
  颜色遵循 theme 新约定：positive=var(--qs-positive) 红=买入/涨，negative=var(--qs-negative) 绿=卖出/跌
  （中国股市红买绿卖、红涨绿跌，买入红圈、卖出绿圈）。
  搜索支持股票代码和公司名称，切换 Tab/刷新/搜索均有加载进度显示。
  分析按钮：打开个股分析对话框（最近15天日K + 实时行情 + 买卖信号，
  日K/分时双模式 ECharts 图形化，买卖信号以小字圆圈标注在对应时间位置）。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import httpx
from nicegui import ui

from api.client import UnauthorizedError
from app_context import AppContext
from components import custom_tabs
from core.concurrency import run_blocking

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

# 个股分析对话框：查询最近 N 天的日K / 实时行情 / 买卖信号
_ANALYSIS_DAYS: int = 15

# 买卖信号圆圈徽标尺寸（px）— 紧凑小字样式
_SIGNAL_BADGE_SIZE: int = 25


def _record_date_str(item: dict[str, Any]) -> str:
    """提取行情/信号记录中的日期字符串（YYYY-MM-DD）。

    兼容 date / trade_date / create_time 等字段命名。

    :param item: 后端返回的数据字典
    :return: 日期字符串（取前10位），无日期字段时返回空字符串
    """
    for key in ("date", "trade_date", "create_time", "time"):
        val: str = str(item.get(key, "") or "")
        if val:
            return val[:10]
    return ""


def _is_buy_action(action: str) -> bool:
    """判断信号方向是否为买入。

    :param action: 信号方向文本（买入/卖出/B/BUY/SELL 等）
    :return: 买入方向返回 True，否则返回 False
    """
    text: str = str(action or "")
    return "买" in text or "buy" in text.lower()


def _signal_badge(action: str, buy_color: str, sell_color: str) -> ui.label:
    """渲染买卖信号圆圈徽标：买入红色圆圈、卖出绿色圆圈（小字紧凑样式）。

    :param action: 信号方向文本
    :param buy_color: 买入徽标颜色（红）
    :param sell_color: 卖出徽标颜色（绿）
    :return: 圆圈徽标 label 元素
    """
    is_buy: bool = _is_buy_action(action)
    color: str = buy_color if is_buy else sell_color
    text: str = "买入" if is_buy else "卖出"
    return ui.label(text).style(
        f"display: inline-flex; align-items: center; justify-content: center; "
        f"width: {_SIGNAL_BADGE_SIZE}px; height: {_SIGNAL_BADGE_SIZE}px; "
        f"border: 2px solid {color}; border-radius: 50%; color: {color}; "
        f"font-size: 8px; font-weight: 700; line-height: 1; "
        f"flex-shrink: 0; margin: 2px 0;"
    )


def _fetch_analysis_data(code: str) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """查询个股分析数据：最近15天日K、最新实时行情、最近15天买卖信号。

    任一查询失败均降级为空数据，不阻塞对话框其余内容展示。

    :param code: 股票代码
    :return: (日K列表, 实时行情字典, 该股票的信号列表)
    """
    end_date: str = datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005
    start_date: str = (
        datetime.now() - timedelta(days=_ANALYSIS_DAYS)  # noqa: DTZ005
    ).strftime("%Y-%m-%d")

    kline: list[dict[str, Any]] = []
    try:
        kline = AppContext().market_api.get_day_kline(
            code=code, start=start_date, end=end_date
        )
    except UnauthorizedError:
        logger.debug("查询日K数据未授权（401）：%s，会话已过期，等待跳转登录", code)
    except Exception:
        logger.warning("查询日K数据失败: %s", code, exc_info=True)

    realtime: dict[str, Any] = {}
    try:
        rt_list: list[dict[str, Any]] = AppContext().market_api.get_real_time(code=code)
        if rt_list:
            realtime = dict(rt_list[-1])
    except UnauthorizedError:
        logger.debug("查询实时行情未授权（401）：%s，会话已过期，等待跳转登录", code)
    except Exception:
        logger.warning("查询实时行情失败: %s", code, exc_info=True)

    signals: list[dict[str, Any]] = []
    try:
        signals: list[dict[str, Any]] = AppContext().trade_signal_api.list(
            stock_codes=[code],
            start_time=f"{start_date} 00:00:00",
            end_time=f"{end_date} 23:59:59",
        )
    except UnauthorizedError:
        logger.debug("查询交易信号未授权（401）：%s，会话已过期，等待跳转登录", code)
    except Exception:
        logger.warning("查询交易信号失败: %s", code, exc_info=True)

    return kline, realtime, signals


def _minute_time_hhmm(item: dict[str, Any]) -> str:
    """提取分钟行情记录的 HH:MM 时间。

    兼容 minute_time / create_time / time 等字段命名。

    :param item: 后端返回的分钟数据字典
    :return: HH:MM 字符串，无时间字段时返回空字符串
    """
    for key in ("minute_time", "create_time", "time"):
        val: str = str(item.get(key, "") or "")
        if len(val) >= 16:
            return val[11:16]
    return ""


def _nearest_minute(target: str, categories: list[str]) -> str:
    """在分时 x 轴类别中找到与目标 HH:MM 时间最近的一个。

    :param target: 目标 HH:MM
    :param categories: 已有的分钟类别列表
    :return: 最近的类别字符串，空列表返回空字符串
    """
    if not categories:
        return ""
    if target in categories:
        return target

    def to_min(s: str) -> int:
        parts: list[str] = s.split(":")
        if len(parts) != 2:
            return 0
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except ValueError:
            return 0

    t: int = to_min(target)
    return min(categories, key=lambda s: abs(to_min(s) - t))


def _fetch_minute_data(code: str, date_str: str) -> list[dict[str, Any]]:
    """查询指定日期的分时（分钟）行情数据（09:30 - 15:00）。

    :param code: 股票代码
    :param date_str: 日期 YYYY-MM-DD
    :return: 分钟行情数据列表
    """
    if not code or not date_str:
        return []
    try:
        return AppContext().market_api.get_minute_kline(
            code=code,
            start=f"{date_str} 09:30:00",
            end=f"{date_str} 15:00:00",
        )
    except UnauthorizedError:
        logger.debug("查询分时数据未授权（401）：%s %s，会话已过期，等待跳转登录", code, date_str)
        return []
    except Exception:
        logger.warning("查询分时数据失败: %s %s", code, date_str, exc_info=True)
        return []


def _signal_scatter_series(
    name: str,
    points: list[list[Any]],
    color: str,
    text: str,
) -> dict[str, Any]:
    """构建买卖信号圆圈标注的 scatter 系列配置。

    圆圈为透明填充 + 2px 描边，内部显示文字（买入/卖出）。

    :param name: 系列名称
    :param points: 数据点列表 [[x, y], ...]
    :param color: 圆圈与文字颜色
    :param text: 圆圈内显示文字
    :return: ECharts scatter 系列配置字典
    """
    return {
        "name": name,
        "type": "scatter",
        "data": points,
        "symbol": "circle",
        "symbolSize": 25,
        "z": 20,
        "itemStyle": {
            "color": "transparent",
            "borderColor": color,
            "borderWidth": 2,
        },
        "label": {
            "show": True,
            "formatter": text,
            "color": color,
            "fontSize": 8,
            "fontWeight": "bold",
        },
    }


def _build_day_k_option(
    code: str,
    name: str,
    kline: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    colors: dict[str, str],
) -> dict[str, Any]:
    """构建日K蜡烛图 ECharts 配置。

    双图布局：上方为价格蜡烛图，下方为成交量柱状副图；
    买卖信号以圆圈标注在对应日期的成交价位置（买入红圈、卖出绿圈）。

    :param code: 股票代码
    :param name: 股票名称
    :param kline: 最近若干天日K数据列表
    :param signals: 买卖信号列表
    :param colors: 图表配色字典
    :return: ECharts option 字典
    """
    up: str = colors["up"]
    down: str = colors["down"]
    axis: str = colors["axis"]
    grid_c: str = colors["grid"]
    axis_line: str = colors["axis_line"]

    # 按日期升序
    sorted_k: list[dict[str, Any]] = sorted(kline, key=lambda it: _record_date_str(it))
    dates: list[str] = [_record_date_str(it) for it in sorted_k]
    candle: list[list[float]] = []
    volumes: list[float] = []
    for it in sorted_k:
        # ECharts 蜡烛图数据顺序为 [open, close, low, high]
        candle.append([
            _sf(it.get("open", 0)),
            _sf(it.get("close", 0)),
            _sf(it.get("low", 0)),
            _sf(it.get("high", 0)),
        ])
        volumes.append(_sf(it.get("volume", 0)))

    # 买卖信号散点：按日期标注
    buy_data: list[list[Any]] = []
    sell_data: list[list[Any]] = []
    for sg in signals:
        d: str = _record_date_str(sg)
        if not d or d not in dates:
            continue
        price: float = _sf(sg.get("trade_price", 0))
        if _is_buy_action(str(sg.get("action", ""))):
            buy_data.append([d, price])
        else:
            sell_data.append([d, price])

    return {
        "backgroundColor": "transparent",
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "grid": [
            {"left": 60, "right": 20, "top": 20, "height": "58%"},
            {"left": 60, "right": 20, "top": "74%", "height": "20%"},
        ],
        "xAxis": [
            {
                "type": "category", "data": dates, "boundaryGap": False,
                "axisLine": {"lineStyle": {"color": axis_line}},
                "axisLabel": {"color": axis},
            },
            {
                "type": "category", "gridIndex": 1, "data": dates, "boundaryGap": False,
                "axisLabel": {"show": False}, "axisTick": {"show": False},
                "axisLine": {"lineStyle": {"color": axis_line}},
            },
        ],
        "yAxis": [
            {
                "scale": True,
                "splitLine": {"lineStyle": {"color": grid_c, "type": "dashed"}},
                "axisLabel": {"color": axis},
            },
            {
                "scale": True, "gridIndex": 1, "splitNumber": 2,
                "splitLine": {"show": False}, "axisLabel": {"show": False},
            },
        ],
        "dataZoom": [{"type": "inside", "xAxisIndex": [0, 1], "start": 0, "end": 100}],
        "series": [
            {
                "name": "日K", "type": "candlestick", "data": candle,
                "itemStyle": {
                    "color": up, "color0": down,
                    "borderColor": up, "borderColor0": down,
                },
            },
            {
                "name": "成交量", "type": "bar", "xAxisIndex": 1, "yAxisIndex": 1,
                "data": volumes, "itemStyle": {"color": colors["volume"]},
            },
            _signal_scatter_series("买入", buy_data, up, "买入"),
            _signal_scatter_series("卖出", sell_data, down, "卖出"),
        ],
    }


def _build_minute_option(
    code: str,
    name: str,
    date_str: str,
    minute_data: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    colors: dict[str, str],
) -> dict[str, Any]:
    """构建分时折线图 ECharts 配置。

    包含价格折线、昨收参考线，以及当日买卖信号圆圈标注
    （按信号时间 HH:MM 定位到分时图对应位置）。

    :param code: 股票代码
    :param name: 股票名称
    :param date_str: 当日日期 YYYY-MM-DD
    :param minute_data: 当日分钟行情数据列表
    :param signals: 买卖信号列表（自动过滤当日）
    :param colors: 图表配色字典
    :return: ECharts option 字典
    """
    up: str = colors["up"]
    down: str = colors["down"]
    axis: str = colors["axis"]
    grid_c: str = colors["grid"]
    axis_line: str = colors["axis_line"]

    sorted_m: list[dict[str, Any]] = sorted(
        minute_data, key=lambda it: _minute_time_hhmm(it)
    )
    x_data: list[str] = [_minute_time_hhmm(it) for it in sorted_m]
    price_data: list[float] = [
        _sf(it.get("price", it.get("close", 0))) for it in sorted_m
    ]
    preclose: float = _sf(sorted_m[0].get("preclose", 0)) if sorted_m else 0.0
    preclose_data: list[float] = [preclose] * len(x_data)

    # 当日买卖信号：按 HH:MM 定位
    buy_data: list[list[Any]] = []
    sell_data: list[list[Any]] = []
    for sg in signals:
        if _record_date_str(sg) != date_str:
            continue
        ct: str = str(sg.get("create_time", "") or "")
        if len(ct) < 16:
            continue
        x: str = _nearest_minute(ct[11:16], x_data)
        if not x:
            continue
        price: float = _sf(sg.get("trade_price", 0))
        if _is_buy_action(str(sg.get("action", ""))):
            buy_data.append([x, price])
        else:
            sell_data.append([x, price])

    return {
        "backgroundColor": "transparent",
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 60, "right": 20, "top": 20, "bottom": 30},
        "xAxis": {
            "type": "category", "data": x_data, "boundaryGap": False,
            "axisLine": {"lineStyle": {"color": axis_line}},
            "axisLabel": {"color": axis},
        },
        "yAxis": {
            "scale": True,
            "splitLine": {"lineStyle": {"color": grid_c, "type": "dashed"}},
            "axisLabel": {"color": axis},
        },
        "series": [
            {
                "name": "价格", "type": "line", "data": price_data, "symbol": "none",
                "lineStyle": {"color": colors["line"], "width": 2},
                "areaStyle": {
                    "color": {
                        "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
                        "colorStops": [
                            {"offset": 0, "color": colors["line_area_top"]},
                            {"offset": 1, "color": colors["line_area_bottom"]},
                        ],
                    },
                },
            },
            {
                "name": "昨收", "type": "line", "data": preclose_data, "symbol": "none",
                "lineStyle": {"type": "dashed", "color": axis, "width": 1},
            },
            _signal_scatter_series("买入", buy_data, up, "买入"),
            _signal_scatter_series("卖出", sell_data, down, "卖出"),
        ],
    }


def show_stock_quotes_page() -> None:
    """股票行情页面入口 — Tab 显示用户股票池，点击切换对应股票行情。"""
    custom_tabs.load_page_tab_css()
    theme = AppContext().theme_manager
    bg_color: str = theme.var("page")
    border_color: str = theme.var("border")
    # theme 新约定：positive=红=买入/涨，negative=绿=卖出/跌（中国股市红买绿卖、红涨绿跌）
    positive_color: str = theme.var("positive")   # 买入 / 涨 红
    negative_color: str = theme.var("negative")   # 卖出 / 跌 绿
    card_bg: str = theme.var("surface")
    input_bg: str = theme.var("surface-alt")

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
        except UnauthorizedError:
            logger.debug("加载股票池「%s」行情未授权（401），会话已过期，等待跳转登录", pool_name)
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
        keyword: str = (search_input.value or "").strip()
        if keyword:
            # run_blocking：线程池 + 继承请求上下文（线程内仍能取到本会话 token）
            state["quotes"] = await run_blocking(_search_stocks, keyword)
        else:
            cur: str = state["active_tab"]
            if cur:
                pool_quotes_cache.pop(cur, None)
                state["quotes"] = await run_blocking(load_quotes_for_pool, cur)
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
        except UnauthorizedError:
            logger.debug("搜索股票「%s」未授权（401），会话已过期，等待跳转登录", keyword)
            return []
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
        state["quotes"] = await run_blocking(load_quotes_for_pool, tab_name)
        state["loading"] = False
        table_panel.refresh()

    # ---- 个股分析对话框（分析按钮） ----

    def _render_analysis_content(
        code: str,
        name: str,
        kline: list[dict[str, Any]],
        realtime: dict[str, Any],
        signals: list[dict[str, Any]],
    ) -> None:
        """渲染个股分析对话框内容。

        布局：实时行情摘要 → 价格走势图（日K 蜡烛图 / 分时 折线图，
        买卖信号以圆圈标注在图上）→ 买卖信号明细列表。
        买入信号红色圆圈、卖出信号绿色圆圈（中国股市红买绿卖惯例）。

        :param code: 股票代码
        :param name: 股票名称
        :param kline: 最近15天日K数据列表
        :param realtime: 最新实时行情字典
        :param signals: 最近15天买卖信号列表
        """
        muted: str = "var(--qs-muted)"
        normal: str = "var(--qs-text)"

        # ---- 实时行情摘要 ----
        with ui.column().classes("w-full gap-0").style(
            f"padding: 16px 24px; border-bottom: 1px solid {border_color};"
        ):
            ui.label("实时行情").classes("text-sm font-bold").style(f"color: {normal};")
            if realtime:
                rt_price: float = _sf(realtime.get("price", realtime.get("close", 0)))
                rt_chg_pct: float = _sf(
                    realtime.get("change_pct", realtime.get("change_percent", 0))
                ) * 100
                rt_chg_amt: float = _sf(
                    realtime.get("change_amt", realtime.get("change_amount", 0))
                )
                if rt_chg_pct > 0:
                    rt_color: str = positive_color  # 涨为红色（买入色）
                elif rt_chg_pct < 0:
                    rt_color = negative_color  # 跌为绿色（卖出色）
                else:
                    rt_color = "var(--qs-on-accent)"
                rt_sign: str = "+" if rt_chg_pct >= 0 else ""
                with ui.row().classes("w-full items-end gap-3").style("margin-top: 8px;"):
                    ui.label(f"{rt_price:,.2f}").classes("text-2xl font-bold").style(
                        f"color: {rt_color};"
                    )
                    ui.label(f"{rt_sign}{rt_chg_pct:.2f}%").classes("text-base font-bold").style(
                        f"color: {rt_color};"
                    )
                    ui.label(f"{rt_sign}{rt_chg_amt:,.2f}").classes("text-sm font-medium").style(
                        f"color: {rt_color}; margin-bottom: 4px;"
                    )
                rt_fields: list[tuple[str, str]] = [
                    ("今开", f"{_sf(realtime.get('open', 0)):,.2f}"),
                    ("最高", f"{_sf(realtime.get('high', 0)):,.2f}"),
                    ("最低", f"{_sf(realtime.get('low', 0)):,.2f}"),
                    ("昨收", f"{_sf(realtime.get('preclose', 0)):,.2f}"),
                    ("成交量", f"{_sf(realtime.get('volume', 0)):,.0f}"),
                    ("成交额", f"{_sf(realtime.get('amount', 0)):,.0f}"),
                ]
                with ui.row().classes("w-full items-center gap-6").style("margin-top: 10px;"):
                    for label_text, val_text in rt_fields:
                        with ui.column().classes("gap-0"):
                            ui.label(label_text).classes("text-xs").style(f"color: {muted};")
                            ui.label(val_text).classes("text-sm font-medium").style(
                                f"color: {normal};"
                            )
            else:
                ui.label("暂无实时行情数据").classes("text-sm").style(
                    f"color: {muted}; margin-top: 8px;"
                )

        # ---- 价格走势图（日K 蜡烛图 / 分时 折线图，含买卖信号圆圈标注）----
        # ECharts 用 canvas 绘制，不解析 CSS 变量，这里必须取真实色值
        chart_colors: dict[str, str] = AppContext().theme_manager.get_chart_colors()
        # 候选日期（K 线日期，最新在前），用于分时模式选择
        kline_dates: list[str] = sorted(
            {_record_date_str(it) for it in kline if _record_date_str(it)},
            reverse=True,
        )

        chart_state: dict[str, Any] = {
            "mode": "day",               # day | minute
            "minute_date": kline_dates[0] if kline_dates else "",
            "minute_cache": {},          # date -> list[dict]
            "minute_loading": False,
        }

        @ui.refreshable
        def chart_panel() -> None:
            """根据当前模式渲染价格图表（日K 蜡烛图 / 分时 折线图）。"""
            if chart_state["mode"] == "day":
                if not kline:
                    ui.label("暂无日K数据").classes("text-sm").style(
                        f"color: {muted}; padding: 24px;"
                    )
                    return
                opt: dict[str, Any] = _build_day_k_option(
                    code, name, kline, signals, chart_colors
                )
                ui.echart(opt).classes("w-full").style("height: 420px;")
            else:
                if chart_state["minute_loading"]:
                    with ui.row().classes(
                        "w-full justify-center items-center gap-3"
                    ).style("height: 420px;"):
                        ui.spinner(size="md", color="blue")
                        ui.label("正在加载分时数据...").classes("text-sm").style(
                            f"color: {muted};"
                        )
                    return
                minute_data: list[dict[str, Any]] = chart_state["minute_cache"].get(
                    chart_state["minute_date"], []
                )
                if not minute_data:
                    ui.label("暂无分时数据").classes("text-sm").style(
                        f"color: {muted}; padding: 24px;"
                    )
                    return
                opt = _build_minute_option(
                    code, name, chart_state["minute_date"],
                    minute_data, signals, chart_colors,
                )
                ui.echart(opt).classes("w-full").style("height: 420px;")

        async def _ensure_minute_data(date_str: str) -> None:
            """确保分时数据已加载（带缓存），加载完成后刷新图表。"""
            if not date_str:
                return
            chart_state["minute_date"] = date_str
            if date_str in chart_state["minute_cache"]:
                chart_panel.refresh()
                return
            chart_state["minute_loading"] = True
            chart_panel.refresh()
            minute: list[dict[str, Any]] = await run_blocking(
                _fetch_minute_data, code, date_str
            )
            chart_state["minute_cache"][date_str] = minute
            chart_state["minute_loading"] = False
            chart_panel.refresh()

        async def _set_chart_mode(m: str) -> None:
            """切换图表模式（日K / 分时）。"""
            chart_state["mode"] = m
            _sync_chart_controls()
            if m == "minute":
                await _ensure_minute_data(chart_state["minute_date"])
            else:
                chart_panel.refresh()

        async def _on_day_click() -> None:
            """点击日K按钮。"""
            await _set_chart_mode("day")

        async def _on_minute_click() -> None:
            """点击分时按钮。"""
            await _set_chart_mode("minute")

        async def _on_date_change(e: Any) -> None:
            """切换分时日期。"""
            await _ensure_minute_data(str(e.value))

        def _sync_chart_controls() -> None:
            """同步模式按钮高亮与日期选择器可见性。"""
            is_day: bool = chart_state["mode"] == "day"
            active_style: str = (
                "border: 1px solid var(--qs-primary); background-color: rgba(var(--qs-primary-rgb), 0.3); "
                "color: var(--qs-link); padding: 4px 12px; border-radius: 6px; "
                "font-size: 12px; font-weight: 600;"
            )
            inactive_style: str = (
                f"border: 1px solid {border_color}; background-color: transparent; "
                f"color: {muted}; padding: 4px 12px; border-radius: 6px; "
                f"font-size: 12px; font-weight: 600;"
            )
            day_btn.style(active_style if is_day else inactive_style)
            minute_btn.style(active_style if not is_day else inactive_style)
            date_select.set_visibility(not is_day)

        with ui.column().classes("w-full gap-0").style(
            f"padding: 12px 24px 16px; border-bottom: 1px solid {border_color};"
        ):
            with ui.row().classes("w-full items-center gap-3").style("margin-bottom: 8px;"):
                ui.label(f"价格走势（近{_ANALYSIS_DAYS}天）").classes(
                    "text-sm font-bold"
                ).style(f"color: {normal};")
                with ui.row().classes("items-center gap-2"):
                    day_btn: ui.button = ui.button(
                        "日K", on_click=_on_day_click
                    ).props("flat dense").classes("rounded")
                    minute_btn: ui.button = ui.button(
                        "分时", on_click=_on_minute_click
                    ).props("flat dense").classes("rounded")
                if kline_dates:
                    date_select: ui.element = ui.select(
                        options=kline_dates,
                        value=chart_state["minute_date"],
                        on_change=_on_date_change,
                    ).props("outlined dense").style(
                        f"width: 160px; background-color: {input_bg};"
                    )
                else:
                    date_select = ui.label("无可用日期").classes("text-xs").style(
                        f"color: {muted};"
                    )
            chart_panel()
            _sync_chart_controls()


        # ---- 买卖信号列表 ----
        with ui.column().classes("w-full gap-0").style("padding: 16px 24px;"):
            ui.label(f"买卖信号（近{_ANALYSIS_DAYS}天，共{len(signals)}条）").classes(
                "text-sm font-bold"
            ).style(f"color: {normal};")
            if not signals:
                ui.label("近15天无买卖信号").classes("text-sm").style(
                    f"color: {muted}; margin-top: 8px;"
                )
            else:
                # 最新在前
                signals_sorted: list[dict[str, Any]] = sorted(
                    signals, key=lambda sg: str(sg.get("create_time", "")), reverse=True
                )
                for sg in signals_sorted:
                    action: str = str(sg.get("action", "") or "")
                    trade_price: float = _sf(sg.get("trade_price", 0))
                    profit_rate: float = _sf(sg.get("profit_rate", 0))
                    create_time: str = str(sg.get("create_time", "") or "")
                    reason: str = str(sg.get("reason", "") or "")
                    with ui.row().classes("w-full items-center gap-4").style(
                        "padding: 10px 0; border-bottom: 1px solid var(--qs-divider);"
                    ):
                        ui.label(create_time[:16]).classes("text-xs").style(
                            f"color: {muted}; width: 110px; flex-shrink: 0;"
                        )
                        if action:
                            _signal_badge(action, positive_color, negative_color)
                        else:
                            ui.label("—").classes("text-xs").style(f"color: {muted};")
                        ui.label(f"{trade_price:,.2f}").classes("text-sm font-bold").style(
                            f"color: {normal};"
                        )
                        # 卖出信号展示收益率（盈利红色、亏损绿色）
                        if action and not _is_buy_action(action) and profit_rate:
                            pr_sign: str = "+" if profit_rate >= 0 else ""
                            pr_color: str = (
                                positive_color if profit_rate >= 0 else negative_color
                            )
                            ui.label(f"收益 {pr_sign}{profit_rate:.2f}%").classes(
                                "text-xs font-medium"
                            ).style(f"color: {pr_color};")
                        if reason:
                            ui.label(reason).classes("text-xs").style(
                                f"color: {muted}; flex: 1; overflow: hidden; "
                                f"text-overflow: ellipsis; white-space: nowrap;"
                            )

    async def _open_analysis_dialog(code: str, name: str) -> None:
        """打开个股分析对话框：先展示加载动画，后台加载最近15天数据后渲染。

        查询内容：日K线、实时行情、买卖信号（trading_signal）；
        买入信号红色圆圈显示、卖出信号绿色圆圈显示。

        :param code: 股票代码
        :param name: 股票名称
        """
        with ui.dialog() as dialog, ui.card().classes("w-[820px] max-w-[95vw] gap-0").style(
            f"background-color: {card_bg}; border: 1px solid {border_color}; "
            f"border-radius: 12px; padding: 0; display: flex; "
            f"flex-direction: column; max-height: 90vh;"
        ):
            # ---- 头部 ----
            with ui.row().classes("w-full items-center justify-between").style(
                f"padding: 16px 24px; border-bottom: 1px solid {border_color};"
            ):
                ui.label(f"个股分析 — {name or code}({code})").classes(
                    "text-lg font-bold"
                ).style("color: var(--qs-heading);")
                ui.button(icon="close", on_click=dialog.close).props(
                    "flat round dense"
                ).style("color: var(--qs-muted);")
            # ---- 内容区（先加载动画，数据到位后重绘）----
            content: ui.column = ui.column().classes("w-full gap-0").style(
                "overflow-y: auto;"
            )
        dialog.open()

        with content, ui.row().classes("w-full justify-center items-center gap-3").style(
            "padding: 48px;"
        ):
            ui.spinner(size="md", color="blue")
            ui.label("正在加载分析数据...").classes("text-sm").style("color: var(--qs-muted);")

        kline, realtime, signals = await run_blocking(_fetch_analysis_data, code)

        content.clear()
        with content:
            _render_analysis_content(code, name, kline, realtime, signals)

    def _make_analysis_handler(c: str, n: str) -> Callable[[], Any]:
        """为分析按钮创建异步点击处理器（捕获对应行的股票代码与名称）。

        :param c: 股票代码
        :param n: 股票名称
        :return: 无参异步点击处理器
        """

        async def _handler() -> None:
            await _open_analysis_dialog(c, n)

        return _handler

    # 初始加载首个股票池行情
    if active_tab:
        state["quotes"] = load_quotes_for_pool(active_tab)

    # ---- 顶部栏 ----
    with ui.row().classes("w-full items-center justify-between").style(
        f"height: 64px; padding: 0 32px; background-color: {card_bg}; "
        f"border-bottom: 1px solid {border_color};"
    ):
        ui.label("股票实时行情").classes("text-xl font-bold").style("color: var(--qs-heading);")
        with ui.row().classes("items-center gap-4"):
            search_input: ui.input = (
                ui.input(placeholder="输入代码或名称...")
                .props("outlined dense")
                .style(f"width: 256px; background-color: {input_bg};")
            )
            search_input.on("keydown.enter", on_search)
            ui.button(icon="refresh", on_click=on_search).props("flat round dense") \
                .style(f"color: var(--qs-muted); background-color: {input_bg};")

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
                            "color: var(--qs-link); border-bottom: 2px solid var(--qs-primary); "
                            "padding: 12px 24px; cursor: pointer;"
                        ).on("click", lambda _n=pn: on_tab_switch(_n))
                    else:
                        ui.label(pn).classes("text-sm").style(
                            "color: var(--qs-muted); border-bottom: 2px solid transparent; "
                            "padding: 12px 24px; cursor: pointer;"
                        ).on("click", lambda _n=pn: on_tab_switch(_n))
            else:
                ui.label("暂无股票池，请先创建股票池").classes("text-sm").style(
                    "color: var(--qs-muted); padding: 12px 24px;"
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
                            f"color: var(--qs-muted); flex: {flex_val};"
                        )

                # 加载进度
                if state.get("loading", False):
                    with ui.row().classes("w-full justify-center items-center gap-3").style("padding: 48px;"):
                        ui.spinner(size="md", color="blue")
                        ui.label("正在加载行情数据...").classes("text-sm").style("color: var(--qs-muted);")

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
                        price_color = positive_color  # 涨为红色（买入色）
                    elif chg_pct < 0:
                        price_color = negative_color  # 跌为绿色（卖出色）
                    else:
                        price_color = "var(--qs-on-accent)"  # 平为白色

                    with ui.row().classes("w-full items-center").style(
                        "padding: 16px 24px; border-bottom: 1px solid var(--qs-divider);"
                    ):
                        # 代码/名称
                        with ui.column().classes("gap-0").style("flex: 2;"):
                            ui.label(code).classes("font-bold tracking-wider").style("color: var(--qs-text);")
                            ui.label(name).classes("text-xs").style("color: var(--qs-faint);")

                        ui.label(f"{price:,.2f}").classes("font-bold").style(f"color: {price_color}; flex: 1;")
                        sign: str = "+" if chg_pct >= 0 else ""
                        ui.label(f"{sign}{chg_pct:.2f}%").classes("font-bold").style(f"color: {price_color}; flex: 1;")
                        ui.label(f"{sign}{chg_amt:,.2f}").classes("font-medium").style(f"color: {price_color}; flex: 1;")
                        ui.label(f"{to_rate:.2f}%").classes("text-sm").style("color: var(--qs-muted); flex: 1;")
                        pe_text: str = "亏损" if dynamic_pe < 0 else f"{dynamic_pe:.1f}"
                        ui.label(pe_text).classes("text-sm").style("color: var(--qs-muted); flex: 1;")
                        ui.label(f"{profit_growth:.1f}%").classes("text-sm").style("color: var(--qs-muted); flex: 1;")
                        ui.button("分析", on_click=_make_analysis_handler(code, name)) \
                            .props("flat dense") \
                            .style("color: var(--qs-link); font-size: 12px; font-weight: 700; flex: 1; text-align: right;")

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
