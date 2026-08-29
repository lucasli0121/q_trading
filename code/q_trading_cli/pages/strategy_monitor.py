#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-08-12
Description: 策略监控页面 — 实时交易信号日志展示
    - 顶部状态栏（实时更新指示灯）
    - 信号筛选标签（全部 / 买入 / 卖出）
    - 实时信号日志表格（时间 / 股票代码 / 信号类型 / 成交价格 / 原因）
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

from app_context import AppContext

logger = logging.getLogger(__name__)


def show_strategy_monitor_page() -> None:
    """策略监控页面入口。"""
    current_theme: dict[str, str] = AppContext().theme_manager.get_current_theme()
    bg_color: str = current_theme.get("background", "#0f0f1a")
    font_color: str = current_theme.get("font_color", "#e5e7eb")
    border_color: str = current_theme.get("widget_border_color", "#334155")
    positive_color: str = current_theme.get("positive", "#34d399")
    negative_color: str = current_theme.get("negative", "#f87171")
    card_bg: str = "#1e293b"
    input_bg: str = "#111827"

    # 页面级 CSS
    ui.add_css("""
        .log-row:nth-child(even) {
            background-color: #111827;
        }
    """)

    filter_state: dict[str, str] = {"action": "全部"}

    with ui.column().classes("w-full gap-0").style(f"padding: 32px; background-color: {bg_color};"):

        # ---- 顶部标题栏 ----
        with ui.row().classes("w-full items-center justify-between").style("margin-bottom: 24px;"), ui.row().classes("items-center gap-4"):
            ui.label("策略监控").classes("text-xl font-bold").style("color: #f3f4f6;")
            with ui.row().classes("items-center gap-1.5").style(
                "padding: 4px 12px; background-color: rgba(52,211,153,0.2); border-radius: 9999px;"
            ):
                ui.element("span").classes("w-1.5 h-1.5 rounded-full").style(
                    "background-color: #34d399; animation: pulse 2s infinite;"
                )
                ui.label("实时更新中").classes("text-xs font-bold").style("color: #34d399;")

        # ---- 信号统计卡片 ----
        _build_signal_stats_row(card_bg, border_color, font_color, positive_color, negative_color)

        # ---- 信号日志表格 ----
        with ui.card().classes("w-full gap-0 overflow-hidden").style(
            f"background-color: {card_bg}; border: 1px solid {border_color}; "
            f"border-radius: 12px; margin-top: 24px;"
        ):
            # 表头 + 筛选标签
            with ui.row().classes("w-full items-center justify-between").style(
                f"padding: 16px 24px; border-bottom: 1px solid {border_color};"
            ):
                ui.label("实时信号日志").classes("text-lg font-bold").style("color: #f3f4f6;")
                with ui.row().classes("gap-2"):
                    for lbl, style_color in [
                        ("全部", "#60a5fa"),
                        ("买入", "#34d399"),
                        ("卖出", "#f87171"),
                    ]:
                        _filter_tag(lbl, style_color, filter_state)

            # 表格容器（可刷新）
            table_container = ui.element("div").classes("w-full")

            def _render_table() -> None:
                """渲染信号表格。"""
                table_container.clear()
                with table_container:
                    # 获取信号并筛选
                    signals: list[dict[str, Any]] = AppContext().signal_manager.get_signals(limit=200)
                    action_filter: str = filter_state["action"]
                    if action_filter != "全部":
                        action_map: dict[str, str] = {"买入": "买入", "卖出": "卖出"}
                        target: str = action_map.get(action_filter, "")
                        if target:
                            signals = [s for s in signals if s.get("action", "") == target]

                    # 表头
                    with ui.row().classes("w-full items-center").style(
                        f"background-color: {input_bg}; border-bottom: 1px solid {border_color}; "
                        f"padding: 12px 24px;"
                    ):
                        for hdr, flex in [
                            ("时间", 2), ("策略ID", 1), ("股票代码", 1),
                            ("信号类型", 1), ("成交价格", 1),
                            ("盈亏率", 1), ("盈亏额", 1), ("原因", 2),
                        ]:
                            ui.label(hdr).classes("text-xs font-semibold uppercase tracking-wider").style(
                                f"color: #9ca3af; flex: {flex};"
                            )

                    if not signals:
                        with ui.column().classes("w-full items-center justify-center").style("padding: 60px;"):
                            ui.icon("inbox").style("color: #6b7280; font-size: 48px;")
                            ui.label("暂无交易信号").classes("text-sm").style("color: #9ca3af; margin-top: 12px;")
                    else:
                        for i, sig in enumerate(signals):
                            action: str = sig.get("action", "")
                            is_buy: bool = action == "买入"
                            action_color: str = positive_color if is_buy else (negative_color if action else font_color)
                            time_str: str = str(sig.get("create_time", ""))[:19]
                            sid: str = str(sig.get("strategy_id", ""))
                            code: str = str(sig.get("stock_code", ""))
                            price: str = _fmt_price(sig.get("trade_price", ""))
                            profit_rate: float = float(sig.get("profit_rate", 0) or 0)
                            profit_amount: float = float(sig.get("profit_amount", 0) or 0)
                            reason: str = str(sig.get("reason", "") or "")

                            # 盈亏率/盈亏额颜色（中国股市：红涨绿跌）
                            pr_color: str = negative_color if profit_rate > 0 else (positive_color if profit_rate < 0 else font_color)
                            pa_color: str = negative_color if profit_amount > 0 else (positive_color if profit_amount < 0 else font_color)

                            row_bg: str = input_bg if i % 2 == 0 else card_bg
                            with ui.row().classes("w-full items-center log-row").style(
                                f"padding: 12px 24px; background-color: {row_bg};"
                            ):
                                ui.label(time_str or "--").classes("text-sm").style("color: #9ca3af; flex: 2;")
                                ui.label(sid or "--").classes("text-sm").style("color: #9ca3af; flex: 1;")
                                ui.label(code or "--").classes("text-sm font-medium").style(f"color: {font_color}; flex: 1;")
                                ui.label(action or "--").classes("text-sm font-bold uppercase").style(f"color: {action_color}; flex: 1;")
                                ui.label(price).classes("text-sm").style(f"color: {font_color}; flex: 1;")
                                ui.label(f"{profit_rate:+.2f}%").classes("text-sm font-medium").style(f"color: {pr_color}; flex: 1;")
                                ui.label(f"{profit_amount:+.2f}").classes("text-sm").style(f"color: {pa_color}; flex: 1;")
                                ui.label(reason or "-").classes("text-sm truncate").style("color: #6b7280; flex: 2;")

            _render_table()

            # 定时刷新
            ui.timer(1.0, _render_table)


def _build_signal_stats_row(
    card_bg: str,
    border_color: str,
    font_color: str,
    positive_color: str,
    negative_color: str,
) -> None:
    """构建信号统计卡片行。

    :param card_bg: 卡片背景
    :param border_color: 边框颜色
    :param font_color: 字体颜色
    :param positive_color: 正向色
    :param negative_color: 负向色
    """
    mgr = AppContext().signal_manager
    signals: list[dict[str, Any]] = mgr.get_signals(limit=500)
    total: int = len(signals)
    buy_count: int = sum(1 for s in signals if s.get("action") == "买入")
    sell_count: int = sum(1 for s in signals if s.get("action") == "卖出")
    today_count: int = mgr.get_today_count()

    cards: list[dict[str, Any]] = [
        {"icon": "notifications", "icon_bg": "rgba(59,130,246,0.4)", "icon_color": "#60a5fa",
         "label": "全部信号", "value": str(total)},
        {"icon": "trending_up", "icon_bg": "rgba(52,211,153,0.4)", "icon_color": "#34d399",
         "label": "买入信号", "value": str(buy_count)},
        {"icon": "trending_down", "icon_bg": "rgba(248,113,113,0.4)", "icon_color": "#f87171",
         "label": "卖出信号", "value": str(sell_count)},
        {"icon": "today", "icon_bg": "rgba(251,146,60,0.4)", "icon_color": "#fb923c",
         "label": "今日信号", "value": str(today_count)},
    ]

    with ui.row().classes("w-full gap-6"):
        for card in cards:
            with ui.card().classes("gap-0").style(
                f"flex: 1; min-width: 180px; background-color: {card_bg}; "
                f"border: 1px solid {border_color}; border-radius: 12px; padding: 20px;"
            ), ui.row().classes("w-full items-start justify-between").style("margin-bottom: 12px;"):
                with ui.element("div").classes("w-10 h-10 rounded-lg flex items-center justify-center").style(
                    f"background-color: {card['icon_bg']}; color: {card['icon_color']};"
                ):
                    ui.icon(card["icon"]).style("font-size: 24px;")
                ui.label(card["label"]).classes("text-sm").style("color: #9ca3af;")
                ui.label(card["value"]).classes("text-2xl font-bold").style(
                    "color: #f3f4f6; margin-top: 4px;"
                )


def _filter_tag(label: str, color: str, state: dict[str, str]) -> None:
    """构建一个筛选标签按钮。

    :param label: 标签文字
    :param color: 标签颜色
    :param state: 筛选状态字典
    """
    is_active: bool = state["action"] == label
    bg: str = f"rgba({_hex_to_rgb(color)}, 0.25)" if is_active else "transparent"
    text_color: str = color if is_active else "#9ca3af"

    ui.label(label).classes("text-xs font-medium rounded px-2 py-1 cursor-pointer").style(
        f"color: {text_color}; background-color: {bg}; "
        f"border: 1px solid {color if is_active else 'transparent'};"
    ).on("click", lambda _l=label: _set_filter(_l, state))


def _set_filter(label: str, state: dict[str, str]) -> None:
    """设置筛选状态。

    :param label: 标签文字
    :param state: 筛选状态字典
    """
    state["action"] = label


def _hex_to_rgb(hex_color: str) -> str:
    """将 hex 颜色转为 rgb 数值字符串。

    :param hex_color: 如 '#60a5fa'
    :return: 如 '96,165,250'
    """
    h: str = hex_color.lstrip("#")
    if len(h) != 6:
        return "96,165,250"
    try:
        r: int = int(h[0:2], 16)
        g: int = int(h[2:4], 16)
        b: int = int(h[4:6], 16)
        return f"{r},{g},{b}"
    except ValueError:
        return "96,165,250"


def _fmt_price(value: Any) -> str:
    """安全格式化价格。

    :param value: 价格值
    :return: 格式化字符串
    """
    if value in (None, ""):
        return "--"
    try:
        v: float = float(value)
        return f"¥{v:.2f}"
    except (ValueError, TypeError):
        return str(value)
