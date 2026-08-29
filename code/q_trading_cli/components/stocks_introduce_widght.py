#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-06-01
Description: 股票展示组件 — 左侧股票表格（分区1），右侧分时图（分区2）+ K 线图（分区3）
"""
from __future__ import annotations

import datetime
import logging
from typing import Any

import pandas as pd
from nicegui import ui

from app_context import AppContext

logger = logging.getLogger(__name__)


# ============================================================
#  ECharts 配置构建
# ============================================================

def _update_chart_options(chart: ui.echart, option: dict) -> None:
    """更新 EChart 图表配置。

    EChart.options 为只读属性（NiceGUI 新版本无 setter），
    因此原地修改内部字典后调用 update() 将变更推送到前端。

    :param chart: ui.echart 图表元素
    :param option: 新的 ECharts 配置字典
    """
    chart.options.clear()
    chart.options.update(option)
    chart.update()


def _build_intraday_option(
    dates: list[str],
    closes: list[float],
    stock_name: str = "",
    font_color: str = "#c9d1d9",
    bg_color: str = "#1e1e1e",
) -> dict:
    """构建分时走势 ECharts 配置"""
    return {
        "backgroundColor": bg_color,
        "title": {
            "text": f"{stock_name} 分时走势" if stock_name else "分时走势",
            "left": "center",
            "textStyle": {"color": font_color, "fontSize": 14},
        },
        "tooltip": {"trigger": "axis"},
        "grid": {"left": "8%", "right": "4%", "top": "18%", "bottom": "10%"},
        "xAxis": {
            "type": "category",
            "data": dates,
            "axisLine": {"lineStyle": {"color": font_color}},
            "axisLabel": {"color": font_color, "fontSize": 10},
        },
        "yAxis": {
            "type": "value",
            "scale": True,
            "axisLine": {"lineStyle": {"color": font_color}},
            "axisLabel": {"color": font_color, "fontSize": 10},
            "splitLine": {"lineStyle": {"color": "#333"}},
        },
        "series": [
            {
                "name": "价格",
                "type": "line",
                "data": closes,
                "smooth": True,
                "showSymbol": False,
                "lineStyle": {"color": "#569cd6", "width": 1.5},
                "areaStyle": {
                    "color": {
                        "type": "linear",
                        "x": 0, "y": 0, "x2": 0, "y2": 1,
                        "colorStops": [
                            {"offset": 0, "color": "rgba(86,156,214,0.35)"},
                            {"offset": 1, "color": "rgba(86,156,214,0.02)"},
                        ],
                    }
                },
            }
        ],
    }


def _build_kline_option(
    dates: list[str],
    ohlc: list[list[float]],
    stock_name: str = "",
    font_color: str = "#c9d1d9",
    bg_color: str = "#1e1e1e",
) -> dict:
    """构建 K 线图 ECharts 配置"""
    return {
        "backgroundColor": bg_color,
        "title": {
            "text": f"{stock_name} K 线走势" if stock_name else "K 线走势",
            "left": "center",
            "textStyle": {"color": font_color, "fontSize": 14},
        },
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "cross"},
        },
        "grid": {"left": "8%", "right": "4%", "top": "18%", "bottom": "10%"},
        "xAxis": {
            "type": "category",
            "data": dates,
            "axisLine": {"lineStyle": {"color": font_color}},
            "axisLabel": {"color": font_color, "fontSize": 10},
        },
        "yAxis": {
            "type": "value",
            "scale": True,
            "axisLine": {"lineStyle": {"color": font_color}},
            "axisLabel": {"color": font_color, "fontSize": 10},
            "splitLine": {"lineStyle": {"color": "#333"}},
        },
        "series": [
            {
                "name": "K 线",
                "type": "candlestick",
                "data": ohlc,
                "itemStyle": {
                    "color": "#b5cea8",
                    "color0": "#f44747",
                    "borderColor": "#b5cea8",
                    "borderColor0": "#f44747",
                },
            }
        ],
    }


def _empty_chart_option(
    title: str, font_color: str = "#c9d1d9", bg_color: str = "#1e1e1e"
) -> dict:
    """构建空图表占位配置"""
    return {
        "backgroundColor": bg_color,
        "title": {
            "text": title,
            "left": "center",
            "textStyle": {"color": font_color, "fontSize": 14},
        },
        "graphic": {
            "type": "text",
            "left": "center",
            "top": "center",
            "style": {"text": "点击左侧股票查看走势", "fill": font_color, "fontSize": 16},
        },
    }


# ============================================================
#  数据获取
# ============================================================

def _fetch_chart_data(
    code: str, days: int = 120
) -> tuple[list[str], list[float], list[list[float]]]:
    """
    拉取股票历史 K 线行情数据。

    :return: (dates, closes, ohlc)
    """
    end_date = datetime.datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005
    start_date = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")  # noqa: DTZ005

    try:
        df: pd.DataFrame = AppContext().stock_fetch.get_stock_day_his_hq(
            code=code,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"获取 {code} 行情数据失败: {e}")
        return [], [], []

    if df is None or df.empty:
        return [], [], []

    dates: list[str] = df["datetime"].astype(str).tolist()
    closes: list[float] = df["close"].astype(float).tolist()

    ohlc: list[list[float]] = []
    for _, row in df.iterrows():
        ohlc.append([
            float(row["open"]),
            float(row["close"]),
            float(row["low"]),
            float(row["high"]),
        ])

    return dates, closes, ohlc


def _fetch_rt_data(stock_list: list[dict]) -> dict[str, dict]:
    """
    批量拉取股票实时行情数据，返回 code → 实时字段 的映射。

    :return: {code: {price, change_percent, change_amount, high, low, preclose, volume, turnover}, ...}
    """
    result: dict[str, dict] = {}
    codes: list[str] = [
        str(item.get("code", "")) for item in stock_list if item.get("code")
    ]
    if not codes:
        return result

    try:
        data: list[dict[str, Any]] = AppContext().market_api.get_real_time(
            codes=",".join(codes), use_default_time=False
        )
    except Exception:
        logger.debug("获取实时行情失败", exc_info=True)
        return result

    for item in data:
        code: str = str(item.get("code", ""))
        if not code:
            continue
        result[code] = {
            "price": item.get("price", ""),
            "change_percent": item.get("change_percent", ""),
            "change_amount": item.get("change_amount", ""),
            "high": item.get("high", ""),
            "low": item.get("low", ""),
            "preclose": item.get("preclose", ""),
            "volume": item.get("volume", ""),
            "turnover": item.get("turnover", ""),
        }

    return result


# ============================================================
#  主组件
# ============================================================

def show_stocks_introduce(stock_list: list[dict]) -> None:
    """
    显示股票介绍组件。

    布局：
    ┌──────────────────┬──────────────────────────┐
    │  股票表格         │     分时走势图 (分区2)     │
    │  (分区1)         ├──────────────────────────┤
    │  code | name     │     K 线走势图 (分区3)     │
    └──────────────────┴──────────────────────────┘

    :param stock_list: 股票列表，每项格式 {"code": "000001", "name": "平安银行"}
    """
    current_theme = AppContext().theme_manager.get_current_theme()
    bg_color = current_theme.get("background", "#1e1e1e")
    font_color = current_theme.get("font_color", "#c9d1d9")
    widget_border_color = current_theme.get("widget_border_color", "#80808033")

    # 存储 ECharts 引用
    intraday_chart_ref: dict = {}
    kline_chart_ref: dict = {}

    # ---- 两列行容器 ----
    with ui.row().classes("w-full h-full gap-0").style(
        f"background-color: {bg_color};"
    ):

        # ============================================================
        #  分区1 — 左侧：股票表格
        # ============================================================
        with ui.column().classes("w-[35%] h-full gap-0").style(
            f"border-right: 1px solid {widget_border_color}; padding: 8px;"
        ):
            ui.label("股票列表").classes("text-sm font-bold").style(
                f"color: {font_color}; padding: 4px 8px;"
            )

            # 拉取实时行情数据
            rt_data = _fetch_rt_data(stock_list)

            # 构建表格行数据（加序号 + 实时行情字段）
            table_rows = []
            for i, item in enumerate(stock_list):
                code = item.get("code", "")
                name = item.get("name", "")
                rt = rt_data.get(code, {})
                table_rows.append({
                    "sn": i + 1,
                    "code": code,
                    "name": name,
                    "price": rt.get("price", "-"),
                    "change_percent": rt.get("change_percent", "-"),
                    "change_amount": rt.get("change_amount", "-"),
                    "high": rt.get("high", "-"),
                    "low": rt.get("low", "-"),
                    "preclose": rt.get("preclose", "-"),
                    "volume": rt.get("volume", "-"),
                    "turnover": rt.get("turnover", "-"),
                })

            table_columns = [
                {"name": "sn", "label": "序号", "field": "sn", "align": "center"},
                {"name": "code", "label": "代码", "field": "code", "align": "center"},
                {"name": "name", "label": "名称", "field": "name", "align": "center"},
                {"name": "price", "label": "现价", "field": "price", "align": "right"},
                {"name": "change_percent", "label": "涨跌幅", "field": "change_percent", "align": "right"},
                {"name": "change_amount", "label": "涨跌额", "field": "change_amount", "align": "right"},
                {"name": "high", "label": "最高", "field": "high", "align": "right"},
                {"name": "low", "label": "最低", "field": "low", "align": "right"},
                {"name": "preclose", "label": "昨收", "field": "preclose", "align": "right"},
                {"name": "volume", "label": "成交量", "field": "volume", "align": "right"},
                {"name": "turnover", "label": "换手率", "field": "turnover", "align": "right"},
            ]

            # 表格暗色主题 CSS
            ui.add_css(f"""
                .stock-table .q-table__top,
                .stock-table .q-table__bottom,
                .stock-table thead tr:first-child th {{
                    background-color: {bg_color} !important;
                    color: {font_color} !important;
                }}
                .stock-table tbody td {{
                    background-color: {bg_color} !important;
                    color: {font_color} !important;
                }}
                .stock-table tbody tr:hover td {{
                    background-color: {widget_border_color} !important;
                }}
                .stock-table .q-table__sort-icon {{
                    color: {font_color} !important;
                }}
            """)

            with ui.element("div").classes("w-full").style("overflow-x: auto;"):
                stock_table = (
                    ui.table(
                        columns=table_columns,
                        rows=table_rows,
                        row_key="code",
                        pagination={"rowsPerPage": 0, "sortBy": "sn", "page": 1},
                    )
                    .props("dark")
                    .classes("gap-0 stock-table")
                    .style(
                        f"border: 1px solid {widget_border_color}; "
                        f"border-radius: 6px; "
                        f"min-width: 900px;"
                    )
                )

            def on_row_click(event) -> None:
                """表格行点击：更新右侧图表"""
                row = event.args[1] if len(event.args) > 1 else {}
                code = row.get("code", "")
                name = row.get("name", "")
                if code:
                    _on_stock_selected(
                        code,
                        name,
                        intraday_chart_ref,
                        kline_chart_ref,
                        bg_color,
                        font_color,
                    )

            stock_table.on("rowClick", on_row_click)

        # ============================================================
        #  右侧：图表区域
        # ============================================================
        with ui.column().classes("w-[65%] h-full gap-0").style(
            f"padding: 8px; background-color: {bg_color};"
        ):

            # ---- 分区2：上半部分 — 分时走势 ----
            with ui.card().classes("w-full").props("flat bordered").style(
                f"height: 50%; border: 1px solid {widget_border_color}; "
                f"border-radius: 6px; padding: 4px; background-color: {bg_color};"
            ):
                intraday_chart = ui.echart(
                    _empty_chart_option("分时走势", font_color, bg_color)
                ).classes("w-full h-full")
                intraday_chart_ref["chart"] = intraday_chart

            # ---- 分区3：下半部分 — K 线走势 ----
            with ui.card().classes("w-full").props("flat bordered").style(
                f"height: 50%; border: 1px solid {widget_border_color}; "
                f"border-radius: 6px; padding: 4px; background-color: {bg_color};"
            ):
                kline_chart = ui.echart(
                    _empty_chart_option("K 线走势", font_color, bg_color)
                ).classes("w-full h-full")
                kline_chart_ref["chart"] = kline_chart


# ============================================================
#  选中回调
# ============================================================

def _on_stock_selected(
    code: str,
    name: str,
    intraday_ref: dict,
    kline_ref: dict,
    bg_color: str,
    font_color: str,
) -> None:
    """股票选中回调：拉取行情数据并更新两张图表"""
    dates, closes, ohlc = _fetch_chart_data(code)

    intraday_chart = intraday_ref.get("chart")
    kline_chart = kline_ref.get("chart")

    if not dates:
        if intraday_chart:
            _update_chart_options(
                intraday_chart,
                _empty_chart_option("分时走势（无数据）", font_color, bg_color),
            )
        if kline_chart:
            _update_chart_options(
                kline_chart,
                _empty_chart_option("K 线走势（无数据）", font_color, bg_color),
            )
        ui.notify(f"未获取到 {name}({code}) 的行情数据", type="warning")
        return

    # 分时图：最近 30 个交易日收盘价折线
    intra_dates = dates[-30:]
    intra_closes = closes[-30:]

    if intraday_chart:
        _update_chart_options(
            intraday_chart,
            _build_intraday_option(
                intra_dates, intra_closes, f"{name}({code})", font_color, bg_color
            ),
        )

    if kline_chart:
        _update_chart_options(
            kline_chart,
            _build_kline_option(dates, ohlc, f"{name}({code})", font_color, bg_color),
        )
