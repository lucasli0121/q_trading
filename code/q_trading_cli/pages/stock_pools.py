#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-07-11
LastEditors: liguoqiang
LastEditTime: 2026-07-11
Description: 股票池列表页面 — 严格按 stock-pools.html 原型实现
  区1: 统计栏（总数/自动更新/静态池 + 搜索）
  区2: 股票池卡片网格（3列），每卡片含: 图标/同步标签/名称/描述/股票数/更新频率/操作按钮
  区3: 创建新股票池占位卡片
  创建股票池模态框（基本信息 + 自动筛选条件 + 手动追加 + 实时预览）
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx
from nicegui import ui

from api.client import ApiError
from app_context import AppContext
from components import custom_tabs

logger = logging.getLogger(__name__)


def show_stock_pools_page() -> None:
    """股票池列表页面入口。"""
    custom_tabs.load_page_tab_css()
    current_theme: dict[str, str] = AppContext().theme_manager.get_current_theme()
    bg_color: str = current_theme.get("background", "#0f0f1a")
    font_color: str = current_theme.get("font_color", "#e5e7eb")
    border_color: str = current_theme.get("widget_border_color", "#334155")
    card_bg: str = "#1e293b"
    input_bg: str = "#111827"

    # 加载股票池
    pools: list[dict[str, Any]] = []
    filtered_pools: list[dict[str, Any]] = []
    try:
        pools = AppContext().pool_api.list()
        filtered_pools = list(pools)
    except httpx.HTTPStatusError as e:
        logger.warning("加载股票池列表失败: %s", e.response.text, exc_info=True)
    except Exception:
        logger.warning("加载股票池列表失败", exc_info=True)

    def on_search() -> None:
        """搜索筛选股票池。"""
        nonlocal filtered_pools
        query: str = (search_input.value or "").strip().lower()
        if query:
            filtered_pools = [
                p for p in pools
                if query in p.get("name", "").lower() or query in p.get("description", "").lower()
            ]
        else:
            filtered_pools = list(pools)
        cards_container.clear()
        with cards_container:
            _render_pool_cards(filtered_pools, card_bg, border_color, font_color, input_bg)

    # ---- 顶部栏 ----
    with ui.row().classes("w-full items-center justify-between").style(
        f"height: 64px; padding: 0 32px; background-color: {card_bg}; "
        f"border-bottom: 1px solid {border_color};"
    ):
        ui.label("股票池列表").classes("text-xl font-bold").style("color: #f3f4f6;")
        ui.button(icon="add", text="创建股票池", on_click=_open_create_pool_modal).props("flat").style(
            "background-color: #3b82f6; color: #ffffff !important; padding: 8px 16px; "
            "border-radius: 8px; font-size: 14px; font-weight: 500;"
        )

    # ---- 页面主体 ----
    with ui.column().classes("w-full gap-0").style(
        f"padding: 32px; background-color: {bg_color}; overflow-y: auto; flex: 1;"
    ):
        # ---- 统计栏 ----
        auto_count: int = sum(1 for p in pools if p.get("type") in ("auto", "自动同步", "条件筛选", "系统自动"))
        static_count: int = len(pools) - auto_count

        with ui.card().classes("w-full gap-0").style(  # noqa: SIM117
            f"background-color: {card_bg}; border: 1px solid {border_color}; "
            f"border-radius: 12px; padding: 16px; margin-bottom: 24px;"
        ):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.row().classes("items-center gap-4"):
                    ui.label(f"总数: {len(pools)}").classes("text-sm font-bold rounded-lg px-4 py-2").style(
                        "color: #60a5fa; background-color: rgba(59,130,246,0.15);"
                    )
                    ui.label(f"自动更新: {auto_count}").classes("text-sm font-bold rounded-lg px-4 py-2").style(
                        "color: #34d399; background-color: rgba(52,211,153,0.15);"
                    )
                    ui.label(f"静态池: {static_count}").classes("text-sm font-bold rounded-lg px-4 py-2").style(
                        "color: #9ca3af; background-color: #2d3748;"
                    )
                # 搜索
                search_input: ui.input = (
                    ui.input(placeholder="搜索股票池...")
                    .props("outlined dense")
                    .style(f"width: 256px; color: {font_color}; background-color: {input_bg};")
                )
                search_input.on("keydown.enter", lambda e: on_search())

        # ---- 股票池卡片网格 (3列) ----
        cards_container = ui.row().classes("w-full gap-6")
        with cards_container:
            _render_pool_cards(filtered_pools, card_bg, border_color, font_color, input_bg)


def _render_pool_cards(
    pools: list[dict[str, Any]],
    card_bg: str,
    border_color: str,
    font_color: str,
    input_bg: str,
) -> None:
    """渲染股票池卡片网格。

    :param pools: 股票池列表
    :param card_bg: 卡片背景
    :param border_color: 边框颜色
    :param font_color: 字体颜色
    :param input_bg: 输入框背景
    """
    for p in pools:
        _build_pool_card(p, card_bg, border_color, font_color, input_bg)

    # 创建新股票池占位卡片
    with ui.element("div").classes("flex-1 min-w-[280px]").style(
        f"border: 2px dashed {border_color}; border-radius: 12px; "
        f"display: flex; flex-direction: column; align-items: center; "
        f"justify-content: center; padding: 32px; min-height: 280px; cursor: pointer;"
    ).on("click", _open_create_pool_modal):
        with ui.element("div").classes("w-12 h-12 rounded-full flex items-center justify-center").style(
            f"background-color: {card_bg}; color: #6b7280; margin-bottom: 16px;"
        ):
            ui.icon("add").style("font-size: 24px;")
        ui.label("创建新股票池").classes("font-bold").style("color: #9ca3af;")


def _build_pool_card(
    pool: dict[str, Any],
    card_bg: str,
    border_color: str,
    font_color: str,
    input_bg: str,
) -> None:
    """构建单个股票池卡片。

    :param pool: 股票池数据
    :param card_bg: 卡片背景
    :param border_color: 边框颜色
    :param font_color: 字体颜色
    :param input_bg: 输入框背景
    """
    name: str = pool.get("name", "未命名")
    desc: str = pool.get("description", "")
    pool_type: str = pool.get("type", "手动维护")
    stock_count: int = 0
    try:
        stocks: list[dict[str, Any]] = AppContext().pool_api.get_stocks(name)
        stock_count = len(stocks)
    except httpx.HTTPStatusError as e:
        logger.warning("获取股票池「%s」股票列表失败: %s", name, e.response.text, exc_info=True)
    except Exception:
        logger.warning("获取股票池「%s」股票列表失败", name, exc_info=True)

    # 图标/颜色根据类型
    type_config: dict[str, dict[str, str]] = {
        "自动同步": {"icon": "layers", "icon_bg": "rgba(59,130,246,0.4)", "icon_color": "#60a5fa", "badge": "自动同步", "badge_bg": "rgba(52,211,153,0.2)", "badge_color": "#34d399"},
        "条件筛选": {"icon": "bolt", "icon_bg": "rgba(168,85,247,0.4)", "icon_color": "#c084fc", "badge": "条件筛选", "badge_bg": "rgba(52,211,153,0.2)", "badge_color": "#34d399"},
        "系统自动": {"icon": "warning", "icon_bg": "rgba(248,113,113,0.4)", "icon_color": "#f87171", "badge": "系统自动", "badge_bg": "rgba(52,211,153,0.2)", "badge_color": "#34d399"},
        "手动维护": {"icon": "person", "icon_bg": "rgba(251,146,60,0.4)", "icon_color": "#fb923c", "badge": "手动维护", "badge_bg": "#2d3748", "badge_color": "#9ca3af"},
    }
    cfg: dict[str, str] = type_config.get(pool_type, type_config["手动维护"])

    update_text: str = {"自动同步": "每日更新", "条件筛选": "盘后更新", "系统自动": "实时同步", "手动维护": "2026-07-01 更新"}.get(pool_type, "手动更新")

    with ui.card().classes("gap-0 group").style(
        f"flex: 1; min-width: 280px; max-width: calc(33.33% - 16px); "
        f"background-color: {card_bg}; border: 1px solid {border_color}; "
        f"border-radius: 12px; padding: 24px; transition: border-color 0.15s;"
    ):
        # 图标 + 标签
        with ui.row().classes("w-full items-start justify-between").style("margin-bottom: 16px;"):
            with ui.element("div").classes("w-12 h-12 rounded-xl flex items-center justify-center").style(
                f"background-color: {cfg['icon_bg']}; color: {cfg['icon_color']};"
            ):
                ui.icon(cfg["icon"]).style("font-size: 24px;")
            ui.label(cfg["badge"]).classes("text-xs font-bold rounded uppercase px-2 py-1").style(
                f"color: {cfg['badge_color']}; background-color: {cfg['badge_bg']};"
            )

        # 名称 + 描述
        ui.label(name).classes("text-lg font-bold").style("color: #f3f4f6; margin-bottom: 4px;")
        ui.label(desc or "无描述").classes("text-sm line-clamp-2").style(
            "color: #9ca3af; margin-bottom: 24px;"
        )

        # 统计
        with ui.row().classes("w-full items-center justify-between").style(
            f"border-top: 1px solid {border_color}; padding-top: 16px; margin-bottom: 24px;"
        ):
            ui.label(f"{stock_count} 只股票").classes("text-xs").style("color: #6b7280;")
            ui.label(update_text).classes("text-xs").style("color: #6b7280;")

        # 按钮
        with ui.row().classes("w-full gap-2"):
            ui.button("查看股票", on_click=lambda n=name: _view_pool_stocks(n)).props("flat").classes("flex-1").style(
                f"color: #d1d5db; background-color: {input_bg}; border-radius: 8px; "
                f"font-size: 14px; font-weight: 500; padding: 8px;"
            )
            ui.button(icon="edit", on_click=lambda p=pool: _open_edit_pool_modal(p)).props("flat").style(
                f"color: #6b7280; border: 1px solid {border_color}; border-radius: 8px; padding: 8px;"
            )


def _view_pool_stocks(name: str) -> None:
    """查看股票池内股票。

    :param name: 股票池名称
    """
    try:
        stocks: list[dict[str, Any]] = AppContext().pool_api.get_stocks(name)
        if stocks:
            codes: str = ", ".join([str(s.get("code", "")) for s in stocks[:20]])
            ui.notify(f"「{name}」: {codes}" + (f" ... 等 {len(stocks)} 只" if len(stocks) > 20 else ""),
                      color="positive", timeout=5000)
        else:
            ui.notify(f"「{name}」暂无股票", color="info")
    except httpx.HTTPStatusError as e:
        ui.notify(f"加载失败(HTTP {e.response.status_code}): {e.response.text}", color="negative")
    except Exception as e:  # noqa: BLE001
        ui.notify(f"加载失败: {e!s}", color="negative")


def _open_create_pool_modal() -> None:
    """打开创建股票池模态框（含筛选条件 + 预览面板）。"""
    current_theme: dict[str, str] = AppContext().theme_manager.get_current_theme()
    font_color: str = current_theme.get("font_color", "#e5e7eb")
    border_color: str = current_theme.get("widget_border_color", "#334155")
    card_bg: str = "#1e293b"
    input_bg: str = "#111827"

    with ui.dialog(value=True).props("persistent") as dialog, \
        ui.card().classes("gap-0").style(
            f"background-color: {card_bg}; width: 1000px; max-width: 98vw; height: 90vh; max-height: 90vh; "
            f"border: 1px solid {border_color}; border-radius: 12px; padding: 0; "
            f"display: flex; flex-direction: column; overflow: hidden;"
        ):
        # 标题+关闭
        with ui.row().classes("w-full items-center justify-between").style(
            f"padding: 16px 24px; border-bottom: 1px solid {border_color};"
        ):
            ui.label("创建新股票池").classes("text-2xl font-bold").style("color: #f3f4f6;")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense").style("color: #9ca3af;")

        with ui.row().classes("w-full h-full gap-6 items-stretch").style("padding: 24px;"):
            # 左侧表单 — 内容超出时竖向滚动，与右侧面板底部对齐
            with ui.column().classes("h-full gap-6").style("flex: 3; overflow-y: auto; min-height: 0;"):
                # 基本信息
                with ui.column().classes("w-full gap-4"):
                    ui.label("基本信息").classes("text-lg font-bold").style("color: #f3f4f6;")
                    ui.label("股票池名称").style("font-size: 0.875rem; font-weight: 500; color: #d1d5db;")
                    pool_name = (
                        ui.input(placeholder="例如: 科技龙头精选")
                        .props("outlined dense").classes("w-full")
                        .style(f"color: {font_color}; background-color: {input_bg};")
                    )
                    ui.label("描述").style("font-size: 0.875rem; font-weight: 500; color: #d1d5db;")
                    pool_desc = (
                        ui.textarea(placeholder="说明该股票池的用途...")
                        .props("outlined dense input-style='min-height:40px; height:50px'").classes("w-full")
                        .style(f"color: {font_color}; background-color: {input_bg}; ")
                    )

                

                    # 行业选择
                    ui.label("所属行业 (可多选)").classes("text-sm").style("color: #d1d5db;")
                    selected_industries: set[str] = set()

                    # 从 API 加载热门行业
                    hot_industries: list[dict[str, Any]] = []
                    try:
                        hot_industries = AppContext().stock_info_api.get_hot_industries()
                    except httpx.HTTPStatusError as e:
                        logger.warning("加载热门行业失败: %s", e.response.text, exc_info=True)
                    except Exception:
                        logger.warning("加载热门行业失败", exc_info=True)

                    def _toggle_industry(ind_name: str, lbl: ui.label) -> None:
                        """切换行业选中状态。

                        :param ind_name: 行业名称
                        :param lbl: 行业标签元素
                        """
                        if ind_name in selected_industries:
                            selected_industries.discard(ind_name)
                            lbl.style(
                                "color: #60a5fa; background-color: rgba(59,130,246,0.15); "
                                "border: 1px solid #3b82f6; cursor: pointer;"
                            )
                        else:
                            selected_industries.add(ind_name)
                            lbl.style(
                                "color: #ffffff; background-color: #3b82f6; "
                                "border: 1px solid #3b82f6; cursor: pointer;"
                            )

                    with ui.row().classes("w-full gap-2 flex-wrap"):
                        if hot_industries:
                            for ind in hot_industries:
                                ind_name: str = ind.get("industry_name") or ind.get("name") or str(ind)
                                lbl: ui.label = ui.label(ind_name).classes(
                                    "text-xs font-medium rounded-lg px-3 py-1.5"
                                )
                                lbl.style(
                                    "color: #60a5fa; background-color: rgba(59,130,246,0.15); "
                                    "border: 1px solid #3b82f6; cursor: pointer;"
                                )
                                lbl.on("click", lambda _n=ind_name, _l=lbl: _toggle_industry(_n, _l))
                        else:
                            ui.label("暂无热门行业数据").classes("text-xs").style("color: #6b7280;")
                    # 自动筛选条件
                    with ui.column().classes("w-full gap-4"):
                        with ui.row().classes("w-full items-center justify-between"):
                            ui.label("自动筛选条件").classes("text-lg font-bold").style("color: #f3f4f6;")
                        with ui.row().classes("w-full gap-4"):
                            with ui.column().classes("flex-1 gap-2"):
                                ui.label("市值范围 (亿元)").classes("text-sm").style("color: #d1d5db;")
                                with ui.row().classes("items-center gap-2"):
                                    cap_min_input: ui.number = (
                                        ui.number(value=0, min=0).props("outlined dense").classes("flex-1")
                                        .style(f"color: {font_color}; background-color: {input_bg};")
                                    )
                                    ui.label("~").style("color: #6b7280;")
                                    cap_max_input: ui.number = (
                                        ui.number(value=0, min=0).props("outlined dense").classes("flex-1")
                                        .style(f"color: {font_color}; background-color: {input_bg};")
                                    )
                            with ui.column().classes("flex-1 gap-2"):
                                ui.label("PE (市盈率) 范围").classes("text-sm").style("color: #d1d5db;")
                                with ui.row().classes("items-center gap-2"):
                                    pe_min_input: ui.number = (
                                        ui.number(value=0, min=0).props("outlined dense").classes("flex-1")
                                        .style(f"color: {font_color}; background-color: {input_bg};")
                                    )
                                    ui.label("~").style("color: #6b7280;")
                                    pe_max_input: ui.number = (
                                        ui.number(value=0, min=0).props("outlined dense").classes("flex-1")
                                        .style(f"color: {font_color}; background-color: {input_bg};")
                                    )

                # 手动追加
                with ui.column().classes("w-full gap-2"):
                    ui.label("追加股票(必选)").classes("text-lg font-bold").style("color: #f3f4f6;")
                    pool_stocks_input = ui.textarea(placeholder="输入股票代码，以逗号,分号或者换行分隔。例如: 600519, 000001") \
                        .props("outlined dense input-style='min-height:80px; height:80px'").classes("w-full") \
                        .style(f"color: {font_color}; background-color: {input_bg}; ")
                    ui.label("手动追加的股票将无视筛选条件强行入池。").classes("text-xs").style("color: #6b7280;")

            # 右侧预览面板 — 与左侧表单底部对齐，内部竖向滚动
            with ui.card().classes("h-full gap-6 no-padding").style(  # noqa: SIM117
                f"background-color: {input_bg}; flex: 2; border: 1px solid {border_color}; "
                f"border-radius: 12px; display: flex; flex-direction: column; overflow: hidden;"
            ):
                with ui.column().classes("w-full h-full gap-0").style(
                    "flex: 1; display: flex; flex-direction: column; "
                ):
                    with ui.row().classes("w-full items-center justify-between").style(
                        f"padding: 16px; border-bottom: 1px solid {border_color};"
                    ):
                        with ui.row().classes("items-center gap-2"):
                            ui.label("实时预览结果").classes("font-bold").style("color: #f3f4f6;")
                        preview_count_label: ui.label = (
                            ui.label("已加入0个股票").classes("text-xs font-bold").style("color: #60a5fa;")
                        )

                        def _update_stock_count() -> None:
                            """更新 preview_count_label 显示 pool_stocks_input 中的股票个数。"""
                            text: str = (pool_stocks_input.value or "").strip()
                            if text:
                                codes: set[str] = {
                                    s.strip() for s in text.replace("\n", ",").replace(";", ",").split(",") if s.strip()
                                }
                                count: int = len(codes)
                            else:
                                count = 0
                            preview_count_label.set_text(f"已加入{count}个股票")

                        # 监听 pool_stocks_input 变更，实时更新计数
                        pool_stocks_input.on("change", lambda e: _update_stock_count())

                    # 预览表格内搜索
                    with ui.row().classes("w-full").style("padding: 0 16px 8px; margin-top: 20px;"):
                        preview_search: ui.input = (
                            ui.input(placeholder="输入代码或名称筛选...")
                            .props("outlined dense clearable")
                            .classes("w-full")
                            .style(f"color: {font_color}; background-color: {card_bg};")
                        )

                    # 表格区域 — flex:1 + overflow:auto 双向滚动，ui.table 自带表头固定
                    preview_columns: list[dict[str, Any]] = [
                        {"name": "code_name", "label": "代码/名称", "field": "code_name", "align": "left", 'width': '10%', "sortable": True},
                        {"name": "price", "label": "最新价", "field": "price", "align": "right", 'width': '5%', "sortable": True},
                        {"name": "ttm", "label": "动态市盈率", "field": "ttm", "align": "right", 'width': '5%', "sortable": True},
                        {"name": "cap", "label": "市值(亿)", "field": "cap", "align": "right", 'width': '5%', "sortable": True},
                        {"name": "margin", "label": "利润率", "field": "margin", "align": "right", 'width': '5%', "sortable": True},
                    ]
                    with ui.scroll_area().classes('w-full preview-scroll').style('flex: 1; min-height: 0;'):
                        preview_table: ui.table = (
                            ui.table(columns=preview_columns, rows=[], row_key="code_name")
                                .props("dense flat bordered hide-bottom selection='multiple'")
                                .style(f"color: {font_color}; background-color: transparent;")
                        )

                        # 预览表格多选框浅色样式
                        ui.add_css("""
                            .preview-scroll .q-checkbox__inner,
                            .preview-scroll .q-checkbox__bg {
                                color: #94a3b8 !important;
                                border-color: #64748b !important;
                            }
                            .preview-scroll .q-checkbox__inner--truthy,
                            .preview-scroll .q-checkbox__bg--truthy {
                                color: #60a5fa !important;
                                border-color: #3b82f6 !important;
                            }
                        """)

                    # 缓存最近一次筛选的完整结果与股价映射，供本地搜索过滤使用
                    _cached_results: list[dict[str, Any]] = []
                    _cached_price_map: dict[str, float] = {}

                    # 选中的股票代码集合
                    selected_preview_codes: set[str] = set()

                    def _on_table_selection(e: Any) -> None:
                        """处理预览表格多选变更，累加/移除选中的股票代码。

                        :param e: NiceGUI 表格 selection 事件对象
                        """
                        nonlocal selected_preview_codes
                        if not e.args or not isinstance(e.args, dict):
                            return
                        rows: list[dict[str, Any]] = e.args.get("rows", [])
                        added: bool = e.args.get("added", True)
                        delta: set[str] = {str(r.get("code", "")) for r in rows if r.get("code")}
                        if added:
                            selected_preview_codes |= delta
                        else:
                            selected_preview_codes -= delta

                    preview_table.on("selection", _on_table_selection)

                    def _fmt_num(val: Any, precision: int = 2) -> str:
                        """安全格式化数值为字符串，非数值返回 '-'。"""
                        if val is None or val == "":
                            return "-"
                        try:
                            return f"{float(val):.{precision}f}"
                        except (ValueError, TypeError):
                            return str(val)

                    def _fmt_pct(val: Any) -> str:
                        """安全格式化百分比值。"""
                        if val is None or val == "":
                            return "-"
                        try:
                            return f"{float(val) * 100:.1f}%"
                        except (ValueError, TypeError):
                            return str(val)

                    def _fmt_pe(val: Any) -> str:
                        """格式化市盈率，负值显示「亏损」。"""
                        if val is None or val == "":
                            return "-"
                        try:
                            v: float = float(val)
                            return "亏损" if v < 0 else f"{v:.1f}"
                        except (ValueError, TypeError):
                            return str(val)

                    def _build_row(row: dict[str, Any]) -> dict[str, str]:
                        """将筛选结果转换为表格行数据。"""
                        code: str = str(row.get("code", ""))
                        name: str = str(row.get("name", ""))
                        price_val: Any = _cached_price_map.get(code, "")
                        ttm_val: Any = row.get("ttm_pe", "")
                        cap_val: Any = row.get("total_market_cap", "")
                        margin_val: Any = row.get("profit_margin", "")
                        return {
                            "code_name": f"{code}  {name}",
                            "price": _fmt_num(price_val, 2),
                            "ttm": _fmt_pe(ttm_val),
                            "cap": _fmt_num(cap_val, 2),
                            "margin": _fmt_num(margin_val, 2),
                            "code": code,
                        }

                    def _render_filtered_rows() -> None:
                        """根据搜索框内容过滤缓存结果并更新表格。"""
                        nonlocal preview_table
                        query: str = (preview_search.value or "").strip().lower()
                        filtered: list[dict[str, Any]] = _cached_results
                        if query:
                            filtered = [
                                r for r in _cached_results
                                if query in str(r.get("code", "")).lower()
                                or query in str(r.get("name", "")).lower()
                            ]
                        preview_table.rows = [_build_row(r) for r in filtered]
                        preview_table.update()

                    # 搜索框输入时实时过滤
                    preview_search.on("keydown.enter", lambda e: _render_filtered_rows())
                    preview_search.on("change", lambda e: _render_filtered_rows())

                    _preview_loading_ref: dict[str, Any] = {}

                    def _do_refresh_preview_sync() -> None:
                        """同步执行刷新预览（在后台线程中运行）。"""
                        nonlocal _cached_results, _cached_price_map

                        cap_min_val: float = float(cap_min_input.value or 0)
                        cap_max_val: float = float(cap_max_input.value or 0)
                        pe_min_val: float = float(pe_min_input.value or 0)
                        pe_max_val: float = float(pe_max_input.value or 0)

                        filters_set: bool = not (
                            cap_min_val == 0 and cap_max_val == 0
                            and pe_min_val == 0 and pe_max_val == 0
                        )
                        industries_selected: bool = bool(selected_industries)

                        if not filters_set and not industries_selected:
                            # 两者都无 → 空结果
                            _cached_results = []
                        elif filters_set and not industries_selected:
                            # 仅筛选条件 → screener 结果
                            try:
                                _cached_results = AppContext().screener_api.search(
                                    cap_min=cap_min_val,
                                    cap_max=cap_max_val,
                                    ttm_min=pe_min_val,
                                    ttm_max=pe_max_val,
                                )
                            except ApiError as e:
                                ui.notify(f"筛选失败: {e.message}", color="negative")
                                return
                            except httpx.HTTPStatusError as e:
                                ui.notify(f"筛选失败(HTTP {e.response.status_code}): {e.response.text}", color="negative")
                                return
                            except Exception as e:  # noqa: BLE001
                                ui.notify(f"网络错误: {e!s}", color="negative")
                                return
                        elif not filters_set and industries_selected:
                            # 仅行业 → 按行业查询股票
                            _cached_results = []
                            for ind_name in selected_industries:
                                try:
                                    _cached_results.extend(
                                        AppContext().stock_info_api.get_by_industry(ind_name)
                                    )
                                except httpx.HTTPStatusError as e:
                                    logger.warning("按行业查询股票失败: %s", e.response.text)
                                except Exception:  # noqa: BLE001, S110
                                    pass
                        else:
                            # 两者都有 → screener 结果与行业取交集
                            try:
                                _cached_results = AppContext().screener_api.search(
                                    cap_min=cap_min_val,
                                    cap_max=cap_max_val,
                                    ttm_min=pe_min_val,
                                    ttm_max=pe_max_val,
                                )
                            except ApiError as e:
                                ui.notify(f"筛选失败: {e.message}", color="negative")
                                return
                            except httpx.HTTPStatusError as e:
                                ui.notify(f"筛选失败(HTTP {e.response.statusCode}): {e.response.text}", color="negative")
                                return
                            except Exception as e:  # noqa: BLE001
                                ui.notify(f"网络错误: {e!s}", color="negative")
                                return

                            if _cached_results:
                                try:
                                    result_codes: str = ",".join(
                                        str(r.get("code", "")) for r in _cached_results
                                    )
                                    stock_infos: list[dict[str, Any]] = (
                                        AppContext().stock_info_api.get_by_codes(result_codes)
                                    )
                                    code_industry: dict[str, str] = {}
                                    for si in stock_infos:
                                        c: str = str(si.get("code", ""))
                                        ind: str = str(si.get("industry") or si.get("industry_name") or "")
                                        if c and ind:
                                            code_industry[c] = ind
                                    _cached_results = [
                                        r for r in _cached_results
                                        if code_industry.get(str(r.get("code", "")), "") in selected_industries
                                    ]
                                except httpx.HTTPStatusError as e:
                                    logger.warning("按代码查询股票信息失败: %s", e.response.text)
                                    # 行业过滤失败不影响整体渲染
                                except Exception:  # noqa: BLE001, S110
                                    pass  # 行业过滤失败不影响整体渲染

                        # 批量获取估值和利润数据，丰富缓存结果
                        codes: list[str] = [
                            str(r.get("code", "")) for r in _cached_results
                        ]
                        valid_codes: list[str] = [c for c in codes if c]
                        _valuation_map: dict[str, dict[str, Any]] = {}
                        _profit_map: dict[str, dict[str, Any]] = {}
                        if valid_codes:
                            codes_str: str = ",".join(valid_codes)
                            # 获取 TTM 市盈率
                            try:
                                _valuation_map = AppContext().finance_api.get_valuation(codes=codes_str)
                            except httpx.HTTPStatusError as e:
                                logger.warning("获取估值数据失败: %s", e.response.text)
                            except Exception:
                                logger.warning("获取估值数据失败", exc_info=True)
                            # 获取市值、净利润增长率
                            try:
                                _profit_map = AppContext().finance_api.get_profit(codes=codes_str)
                            except httpx.HTTPStatusError as e:
                                logger.warning("获取利润数据失败: %s", e.response.text)
                            except Exception:
                                logger.warning("获取利润数据失败", exc_info=True)

                        # 将估值和利润数据合并到 _cached_results
                        for r in _cached_results:
                            c: str = str(r.get("code", ""))
                            if c in _valuation_map:
                                val_data: dict[str, Any] = _valuation_map[c]
                                if not r.get("ttm_pe"):
                                    r["ttm_pe"] = val_data.get("ttm_pe", "")
                                if not r.get("total_market_cap"):
                                    r["total_market_cap"] = val_data.get("total_market_cap", "")
                            if c in _profit_map:
                                profit_data: dict[str, Any] = _profit_map[c]
                                if not r.get("profit_margin"):
                                    r["profit_margin"] = profit_data.get("net_profit_growth_rate", "")

                        # 批量获取当日实时行情价
                        _cached_price_map = {}
                        if valid_codes:
                            try:
                                start_day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")  # noqa: DTZ005
                                end_day = datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005
                                all_kline: list[dict[str, Any]] = AppContext().market_api.get_day_kline(
                                    codes=",".join(valid_codes),
                                    start=start_day,
                                    end=end_day
                                )
                                for row in all_kline:
                                    c: str = str(row.get("code", ""))
                                    price: Any = row.get("price", row.get("close", ""))
                                    if c and price is not None and price != "":
                                        _cached_price_map[c] = float(price)
                            except httpx.HTTPStatusError as e:
                                logger.warning("获取日K线收盘价失败: %s", e.response.text)
                            except Exception:  # noqa: BLE001, S110
                                pass

                        # 清空搜索框并渲染全部结果
                        preview_search.value = ""
                        _render_filtered_rows()

                    async def refresh_preview() -> None:
                        """刷新预览：筛选条件与选中行业取交集后展示（带加载等待状态）。"""
                        btn: Any = _preview_loading_ref.get("btn")
                        spinner: Any = _preview_loading_ref.get("spinner")
                        if btn:
                            btn.set_enabled(False)
                            btn.set_text("刷新中...")
                        if spinner:
                            spinner.set_visibility(True)
                        await asyncio.sleep(0.15)  # 让出控制权使加载状态先渲染到浏览器
                        try:
                            _do_refresh_preview_sync()
                        finally:
                            if btn:
                                btn.set_enabled(True)
                                btn.set_text("刷新预览")
                            if spinner:
                                spinner.set_visibility(False)

                    def _append_selected_stocks() -> None:
                        """将预览中选中的股票代码追加到 pool_stocks_input。"""
                        nonlocal selected_preview_codes
                        if not selected_preview_codes:
                            ui.notify("请先在预览表格中勾选股票", color="warning")
                            return
                        current_text: str = (pool_stocks_input.value or "").strip()
                        existing_codes: set[str] = {
                            s.strip() for s in current_text.replace("\n", ",").replace(";", ",").split(",") if s.strip()
                        } if current_text else set()
                        new_codes: set[str] = selected_preview_codes - existing_codes
                        if not new_codes:
                            ui.notify("选中的股票已在列表中", color="info")
                            return
                        all_codes: list[str] = list(existing_codes) + list(new_codes)
                        pool_stocks_input.value = ", ".join(all_codes)
                        _update_stock_count()

                    with ui.row().classes("w-full gap-2").style(
                        f"padding: 12px; border-top: 1px solid {border_color};"
                    ):
                        with ui.row().classes("items-center gap-2"):
                            refresh_btn = ui.button("刷新预览", on_click=refresh_preview).props("flat").style(
                                f"color: #60a5fa; background-color: {card_bg}; border: 1px solid {border_color}; "
                                f"border-radius: 8px; font-size: 12px; font-weight: 700;"
                            )
                            _preview_loading_ref["btn"] = refresh_btn
                            refresh_spinner = ui.spinner(size="sm").style("color: #60a5fa;")
                            refresh_spinner.set_visibility(False)
                            _preview_loading_ref["spinner"] = refresh_spinner
                        ui.button("追加选中股票", on_click=_append_selected_stocks).props("flat").style(
                            "color: #ffffff !important; background-color: #3b82f6; "
                            "border-radius: 8px; font-size: 12px; font-weight: 700;"
                        )

        # 底部按钮
        with ui.row().classes("w-full items-center justify-end gap-3").style(
            f"padding: 16px 24px; border-top: 1px solid {border_color};"
        ):
            ui.button("取消", on_click=dialog.close).props("flat").style(
                "color: #9ca3af; font-size: 14px; font-weight: 500;"
            )

            def handle_create() -> None:
                """处理创建。"""
                _name: str = (pool_name.value or "").strip()
                if not _name:
                    ui.notify("股票池名称不能为空", color="warning")
                    return
                _stocks = (pool_stocks_input.value or "").strip()
                if not _stocks and len(_stocks) == 0:
                    ui.notify("请至少手动追加一只股票或设置筛选条件", color="warning")
                    return
                
                try:
                    AppContext().pool_api.create(name=_name, description=(pool_desc.value or "").strip())
                    # 手动追加的股票代码，去重并按逗号分隔
                    _stocks_list: list[str] = list({s.strip() for s in _stocks.replace("\n", ",").replace(";", ",").split(",") if s.strip()})
                    try:
                        AppContext().pool_api.add_stocks(_name, _stocks_list)
                    except httpx.HTTPStatusError as e:
                        ui.notify(f"向股票池追加股票失败(HTTP {e.response.status_code}): {e.response.text}", color="negative")
                        return
                    except Exception as e:  # noqa: BLE001
                        ui.notify(f"向股票池追加股票失败: {e!s}", color="negative")
                        return
                    ui.notify(f"股票池「{_name}」创建成功", color="positive")
                    dialog.close()
                    ui.navigate.reload()
                except ApiError as e:
                    ui.notify(f"创建失败: {e.message}", color="negative")
                except httpx.HTTPStatusError as e:
                    ui.notify(f"创建失败(HTTP {e.response.status_code}): {e.response.text}", color="negative")
                except Exception as e:  # noqa: BLE001
                    ui.notify(f"网络错误: {e!s}", color="negative")

            ui.button("保存股票池", on_click=handle_create).props("flat").style(
                "background-color: #3b82f6; color: #ffffff !important; padding: 8px 24px; "
                "border-radius: 8px; font-size: 14px; font-weight: 500;"
            )

    dialog.open()


def _open_edit_pool_modal(pool: dict[str, Any]) -> None:
    """打开编辑股票池模态框 — 管理池内股票（添加/移除）及删除股票池。
    左侧: 基本信息 + 当前股票表格（含日K行情） + 危险区域
    右侧: 预览面板（筛选条件 + 预览表格 + 追加选中股票）

    :param pool: 股票池数据字典
    """
    current_theme: dict[str, str] = AppContext().theme_manager.get_current_theme()
    font_color: str = current_theme.get("font_color", "#e5e7eb")
    border_color: str = current_theme.get("widget_border_color", "#334155")
    card_bg: str = "#1e293b"
    input_bg: str = "#111827"

    pool_name: str = pool.get("name", "")
    pool_desc: str = pool.get("description", "")

    # 加载当前股票池内股票
    current_stocks: list[dict[str, Any]] = []
    try:
        current_stocks = AppContext().pool_api.get_stocks(pool_name)
    except httpx.HTTPStatusError as e:
        logger.warning("编辑时加载股票池「%s」股票列表失败: %s", pool_name, e.response.text, exc_info=True)
    except Exception:
        logger.warning("编辑时加载股票池「%s」股票列表失败", pool_name, exc_info=True)

    # 为当前股票池内股票预加载日K行情数据
    _kline_cache: dict[str, dict[str, Any]] = {}
    if current_stocks:
        try:
            codes_str: str = ",".join(str(s.get("code", "")) for s in current_stocks if s.get("code"))
            if codes_str:
                start_day: str = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")  # noqa: DTZ005
                end_day: str = datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005
                all_kline: list[dict[str, Any]] = AppContext().market_api.get_day_kline(
                    codes=codes_str, start=start_day, end=end_day
                )
                by_code: dict[str, list[dict[str, Any]]] = {}
                for row in all_kline:
                    c: str = str(row.get("code", ""))
                    if c:
                        by_code.setdefault(c, []).append(row)
                for c, rows in by_code.items():
                    rows.sort(key=lambda r: str(r.get("date", r.get("trade_date", ""))))
                    latest: dict[str, Any] = rows[-1]
                    close: float = float(latest.get("close", 0) or 0)
                    pct: float = 0.0
                    if len(rows) >= 2:
                        prev_close: float = float(rows[-2].get("close", close) or close)
                        if prev_close != 0:
                            pct = (close - prev_close) / prev_close
                    _kline_cache[c] = {"price": close, "change_pct": pct}
        except (httpx.HTTPStatusError, Exception):
            logger.warning("加载股票池「%s」K线数据失败", pool_name, exc_info=True)

    # 状态变量
    removed_codes: set[str] = set()
    pending_add_codes: set[str] = set()

    # 预览面板状态（声明在函数顶层，供 nonlocal 引用）
    _cached_results: list[dict[str, Any]] = []
    _cached_price_map: dict[str, float] = {}
    selected_preview_codes: set[str] = set()
    selected_industries: set[str] = set()
    _preview_loading_ref: dict[str, Any] = {}

    with ui.dialog(value=True).props("persistent") as dialog, \
        ui.card().classes("gap-0").style(
            f"background-color: {card_bg}; width: 1000px; max-width: 98vw; height: 90vh; max-height: 90vh; "
            f"border: 1px solid {border_color}; border-radius: 12px; padding: 0; "
            f"display: flex; flex-direction: column; overflow: hidden;"
        ):
        # ---- 标题栏 ----
        with ui.row().classes("w-full items-center justify-between").style(
            f"padding: 16px 24px; border-bottom: 1px solid {border_color};"
        ):
            ui.label(f"编辑股票池 — {pool_name}").classes("text-xl font-bold").style("color: #f3f4f6;")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense").style("color: #9ca3af;")

        # ---- 内容区：左右两栏 ----
        with ui.row().classes("w-full h-full gap-6 items-stretch").style("padding: 24px;"):
            # ======== 左侧：基本信息 + 当前股票表格 + 危险区域 ========
            with ui.column().classes("h-full gap-6").style("flex: 2; overflow-y: auto; min-height: 0;"):
                # 基本信息（只读）
                with ui.column().classes("w-full gap-2"):
                    ui.label("基本信息").classes("text-lg font-bold").style("flex: 1; color: #f3f4f6;")
                    with ui.row().classes("w-full gap-4"):
                        with ui.column().classes("flex-1 gap-1"):
                            ui.label("股票池名称").classes("text-sm").style("color: #9ca3af;")
                            ui.label(pool_name).classes("text-base font-medium").style(f"color: {font_color};")
                        with ui.column().classes("flex-1 gap-1"):
                            ui.label("描述").classes("text-sm").style("color: #9ca3af;")
                            ui.label(pool_desc or "无描述").classes("text-base").style(f"color: {font_color};")
                    ui.label("名称和描述不支持修改（服务端限制）").classes("text-xs italic").style("color: #6b7280;")

                # 当前股票表格
                with ui.column().classes("w-full gap-2").style("flex: 3; min-height: 0;"):
                    ui.label(f"当前股票 ({len(current_stocks)} 只)").classes("text-lg font-bold").style("color: #f3f4f6;")

                    current_columns: list[dict[str, Any]] = [
                        {"name": "code", "label": "代码", "field": "code", "align": "left", "sortable": True},
                        {"name": "name", "label": "名称", "field": "name", "align": "left", "sortable": True},
                        {"name": "price", "label": "最新价", "field": "price", "align": "right", "sortable": True},
                        {"name": "change_pct", "label": "涨跌幅", "field": "change_pct", "align": "right", "sortable": True},
                    ]

                    with ui.scroll_area().classes("w-full").style("flex: 1; min-height: 0;"):
                        current_table: ui.table = (
                            ui.table(columns=current_columns, rows=[], row_key="code")
                            .classes("w-full")
                            .props("dense flat bordered hide-bottom selection='multiple'")
                            .style(f"color: {font_color}; background-color: transparent; ")
                        )

                    # 涨跌幅列自定义渲染（红涨绿跌）
                    current_table.add_slot("body-cell-change_pct", """
                        <q-td :props="props">
                            <span :style="'color: ' + props.row.change_pct_color">
                                {{ props.value }}
                            </span>
                        </q-td>
                    </template>
                    """)

                    # 当前表格选中（用于移除选中股票）
                    current_selected_codes: set[str] = set()

                    def _on_current_selection(e: Any) -> None:
                        """处理当前股票表格多选变更。"""
                        nonlocal current_selected_codes
                        if not e.args or not isinstance(e.args, dict):
                            return
                        rows: list[dict[str, Any]] = e.args.get("rows", [])
                        added: bool = e.args.get("added", True)
                        delta: set[str] = {str(r.get("code", "")) for r in rows if r.get("code")}
                        if added:
                            current_selected_codes |= delta
                        else:
                            current_selected_codes -= delta

                    current_table.on("selection", _on_current_selection)

                    def _render_current_table() -> None:
                        """重新渲染当前股票表格，排除已标记移除的股票。"""
                        nonlocal current_table
                        visible: list[dict[str, Any]] = [
                            s for s in current_stocks
                            if str(s.get("code", "")) not in removed_codes
                        ]
                        codes: str = ",".join(s.get("code", "") for s in visible if s.get("code", "") != "")
                        stocks_list = AppContext().stock_info_api.get_by_codes(codes=codes)
                        rows: list[dict[str, Any]] = []
                        for s in stocks_list:
                            code: str = str(s.get("code", ""))
                            name: str = str(s.get("name", ""))
                            k: dict[str, Any] = _kline_cache.get(code, {})
                            price: Any = k.get("price", "")
                            pct: float = k.get("change_pct", 0.0)
                            # 红涨绿跌配色
                            if price and pct > 0:
                                pct_str: str = f"+{pct * 100:.2f}%"
                                pct_color: str = "#ef4444"  # 红色
                            elif price and pct < 0:
                                pct_str = f"{pct * 100:.2f}%"
                                pct_color = "#34d399"  # 绿色
                            elif price:
                                pct_str = "0.00%"
                                pct_color = "#e5e7eb"  # 白色
                            else:
                                pct_str = "-"
                                pct_color = "#6b7280"  # 灰色
                            rows.append({
                                "code": code,
                                "name": name,
                                "price": f"{float(price):.2f}" if price else "-",
                                "change_pct": pct_str,
                                "change_pct_color": pct_color,
                            })
                        current_table.rows = rows
                        current_table.update()

                    _render_current_table()

                    def _remove_selected() -> None:
                        """将当前表格中勾选的股票标记为待移除。"""
                        nonlocal current_selected_codes
                        if not current_selected_codes:
                            ui.notify("请先在表格中勾选要移除的股票", color="warning")
                            return
                        removed_codes.update(current_selected_codes)
                        current_selected_codes.clear()
                        _render_current_table()

                    ui.button("移除选中", icon="delete", on_click=_remove_selected).props("flat").style(
                        "color: #ef4444; border: 1px solid #ef4444; border-radius: 8px; "
                        "font-size: 13px; font-weight: 500; padding: 6px 12px;"
                    )

                # 危险区域
                with ui.column().classes("w-full gap-2").style(
                    f"flex: 1; padding-top: 16px; border-top: 1px solid {border_color};"
                ):
                    ui.label("危险区域").classes("text-lg font-bold").style("color: #ef4444;")

                    def _confirm_delete() -> None:
                        """弹出二次确认后删除股票池。"""
                        dialog.close()

                        with ui.dialog() as confirm_dialog, \
                            ui.card().classes("gap-4").style(
                                f"background-color: {card_bg}; border: 1px solid {border_color}; "
                                f"border-radius: 12px; padding: 24px; max-width: 420px;"
                            ):
                            ui.label("确认删除").classes("text-xl font-bold").style("color: #f3f4f6;")
                            ui.label(
                                f"确定要删除股票池「{pool_name}」吗？此操作不可撤销，池内所有股票关联将被移除。"
                            ).classes("text-sm").style("color: #9ca3af;")
                            with ui.row().classes("w-full justify-end gap-3"):
                                ui.button("取消", on_click=confirm_dialog.close).props("flat").style(
                                    "color: #9ca3af; font-size: 14px;"
                                )

                                def _do_delete() -> None:
                                    """执行删除股票池。"""
                                    try:
                                        AppContext().pool_api.delete(pool_name)
                                        ui.notify(f"股票池「{pool_name}」已删除", color="positive")
                                        confirm_dialog.close()
                                        ui.navigate.reload()
                                    except ApiError as e:
                                        ui.notify(f"删除失败: {e.message}", color="negative")
                                    except httpx.HTTPStatusError as e:
                                        ui.notify(f"删除失败(HTTP {e.response.status_code}): {e.response.text}", color="negative")
                                    except Exception as e:  # noqa: BLE001
                                        ui.notify(f"删除失败: {e!s}", color="negative")

                                ui.button("确认删除", on_click=_do_delete).props("flat").style(
                                    "background-color: #ef4444; color: #ffffff !important; "
                                    "border-radius: 8px; font-size: 14px; font-weight: 500;"
                                )
                        confirm_dialog.open()

                    ui.button("删除股票池", icon="delete", on_click=_confirm_delete).props("flat").style(
                        "color: #ef4444; border: 1px solid #ef4444; border-radius: 8px; "
                        "font-size: 14px; font-weight: 500; padding: 8px 16px;"
                    )

            # ======== 右侧：预览面板（同创建模态框的实时预览） ========
            with ui.card().classes("h-full gap-6 no-padding").style(  # noqa: SIM117
                f"background-color: {input_bg}; flex: 2; border: 1px solid {border_color}; "
                f"border-radius: 12px; display: flex; flex-direction: column; overflow: hidden;"
            ):
                with ui.column().classes("w-full h-full gap-0").style(
                    "flex: 1; display: flex; flex-direction: column; "
                ):
                    with ui.row().classes("w-full items-center justify-between").style(
                        f"padding: 16px; border-bottom: 1px solid {border_color};"
                    ):
                        with ui.row().classes("items-center gap-2"):
                            ui.label("实时预览结果").classes("font-bold").style("color: #f3f4f6;")
                        preview_count_label: ui.label = (
                            ui.label("已加入0个股票").classes("text-xs font-bold").style("color: #60a5fa;")
                        )

                        def _update_pending_count() -> None:
                            """更新 pending_add_codes 计数显示。"""
                            preview_count_label.set_text(f"已加入{len(pending_add_codes)}个股票")

                        _update_pending_count()

                    # 预览表格内搜索
                    with ui.row().classes("w-full").style("padding: 0 16px 8px; margin-top: 20px;"):
                        preview_search: ui.input = (
                            ui.input(placeholder="输入代码或名称筛选...")
                            .props("outlined dense clearable")
                            .classes("w-full")
                            .style(f"color: {font_color}; background-color: {card_bg};")
                        )

                    # 筛选条件（在预览面板内）
                    with ui.column().classes("w-full gap-3").style("padding: 0 16px 8px;"):
                        # 行业选择
                        with ui.row().classes("w-full gap-2 flex-wrap"):
                            hot_industries: list[dict[str, Any]] = []
                            try:
                                hot_industries = AppContext().stock_info_api.get_hot_industries()
                            except (httpx.HTTPStatusError, Exception):
                                logger.warning("加载热门行业失败", exc_info=True)

                            def _toggle_industry(ind_name: str, lbl: ui.label) -> None:
                                """切换行业选中状态。"""
                                nonlocal selected_industries
                                if ind_name in selected_industries:
                                    selected_industries.discard(ind_name)
                                    lbl.style(
                                        "color: #60a5fa; background-color: rgba(59,130,246,0.15); "
                                        "border: 1px solid #3b82f6; cursor: pointer;"
                                    )
                                else:
                                    selected_industries.add(ind_name)
                                    lbl.style(
                                        "color: #ffffff; background-color: #3b82f6; "
                                        "border: 1px solid #3b82f6; cursor: pointer;"
                                    )

                            if hot_industries:
                                for ind in hot_industries:
                                    ind_name: str = ind.get("industry_name") or ind.get("name") or str(ind)
                                    lbl: ui.label = ui.label(ind_name).classes(
                                        "text-xs font-medium rounded-lg px-2 py-1"
                                    )
                                    lbl.style(
                                        "color: #60a5fa; background-color: rgba(59,130,246,0.15); "
                                        "border: 1px solid #3b82f6; cursor: pointer;"
                                    )
                                    lbl.on("click", lambda _n=ind_name, _l=lbl: _toggle_industry(_n, _l))
                            else:
                                ui.label("暂无热门行业").classes("text-xs").style("color: #6b7280;")

                        # 筛选数值
                        with ui.row().classes("w-full gap-2"):
                            with ui.column().classes("flex-1 gap-1"):
                                ui.label("市值(亿) ≥").classes("text-xs").style("color: #9ca3af;")
                                cap_min_input: ui.number = (
                                    ui.number(value=0, min=0).props("outlined dense").classes("w-full")
                                    .style(f"color: {font_color}; background-color: {card_bg};")
                                )
                            with ui.column().classes("flex-1 gap-1"):
                                ui.label("市值(亿) ≤").classes("text-xs").style("color: #9ca3af;")
                                cap_max_input: ui.number = (
                                    ui.number(value=0, min=0).props("outlined dense").classes("w-full")
                                    .style(f"color: {font_color}; background-color: {card_bg};")
                                )
                            with ui.column().classes("flex-1 gap-1"):
                                ui.label("PE ≥").classes("text-xs").style("color: #9ca3af;")
                                pe_min_input: ui.number = (
                                    ui.number(value=0, min=0).props("outlined dense").classes("w-full")
                                    .style(f"color: {font_color}; background-color: {card_bg};")
                                )
                            with ui.column().classes("flex-1 gap-1"):
                                ui.label("PE ≤").classes("text-xs").style("color: #9ca3af;")
                                pe_max_input: ui.number = (
                                    ui.number(value=0, min=0).props("outlined dense").classes("w-full")
                                    .style(f"color: {font_color}; background-color: {card_bg};")
                                )

                    # 预览表格
                    preview_columns: list[dict[str, Any]] = [
                        {"name": "code_name", "label": "代码/名称", "field": "code_name", "align": "left", "sortable": True},
                        {"name": "price", "label": "最新价", "field": "price", "align": "right", "sortable": True},
                        {"name": "ttm", "label": "动态市盈率", "field": "ttm", "align": "right", "sortable": True},
                        {"name": "cap", "label": "市值(亿)", "field": "cap", "align": "right", "sortable": True},
                        {"name": "margin", "label": "利润率", "field": "margin", "align": "right", "sortable": True},
                    ]
                    with ui.scroll_area().classes("w-full preview-scroll-edit").style("flex: 1; min-height: 0;"):
                        preview_table: ui.table = (
                            ui.table(columns=preview_columns, rows=[], row_key="code_name")
                                .props("dense flat bordered hide-bottom selection='multiple'")
                                .style(f"color: {font_color}; background-color: transparent;")
                        )
                        ui.add_css("""
                            .preview-scroll-edit .q-checkbox__inner,
                            .preview-scroll-edit .q-checkbox__bg {
                                color: #94a3b8 !important;
                                border-color: #64748b !important;
                            }
                            .preview-scroll-edit .q-checkbox__inner--truthy,
                            .preview-scroll-edit .q-checkbox__bg--truthy {
                                color: #60a5fa !important;
                                border-color: #3b82f6 !important;
                            }
                        """)

                    # 表格选中处理
                    def _on_table_selection(e: Any) -> None:
                        """处理预览表格多选变更，累加/移除选中的股票代码。"""
                        nonlocal selected_preview_codes
                        if not e.args or not isinstance(e.args, dict):
                            return
                        rows: list[dict[str, Any]] = e.args.get("rows", [])
                        added: bool = e.args.get("added", True)
                        delta: set[str] = {str(r.get("code", "")) for r in rows if r.get("code")}
                        if added:
                            selected_preview_codes |= delta
                        else:
                            selected_preview_codes -= delta

                    preview_table.on("selection", _on_table_selection)

                    # 格式化辅助
                    def _fmt_num(val: Any, precision: int = 2) -> str:
                        """安全格式化数值为字符串。"""
                        if val is None or val == "":
                            return "-"
                        try:
                            return f"{float(val):.{precision}f}"
                        except (ValueError, TypeError):
                            return str(val)

                    def _fmt_pe(val: Any) -> str:
                        """格式化市盈率，负值显示「亏损」。"""
                        if val is None or val == "":
                            return "-"
                        try:
                            v: float = float(val)
                            return "亏损" if v < 0 else f"{v:.1f}"
                        except (ValueError, TypeError):
                            return str(val)

                    def _build_row(row: dict[str, Any]) -> dict[str, str]:
                        """将筛选结果转换为表格行数据。"""
                        code: str = str(row.get("code", ""))
                        name: str = str(row.get("name", ""))
                        price_val: Any = _cached_price_map.get(code, "")
                        ttm_val: Any = row.get("ttm_pe", "")
                        cap_val: Any = row.get("total_market_cap", "")
                        margin_val: Any = row.get("profit_margin", "")
                        return {
                            "code_name": f"{code}  {name}",
                            "price": _fmt_num(price_val, 2),
                            "ttm": _fmt_pe(ttm_val),
                            "cap": _fmt_num(cap_val, 2),
                            "margin": _fmt_num(margin_val, 2),
                            "code": code,
                        }

                    def _render_filtered_rows() -> None:
                        """根据搜索框内容过滤缓存结果并更新表格。"""
                        nonlocal preview_table
                        query: str = (preview_search.value or "").strip().lower()
                        filtered: list[dict[str, Any]] = _cached_results
                        if query:
                            filtered = [
                                r for r in _cached_results
                                if query in str(r.get("code", "")).lower()
                                or query in str(r.get("name", "")).lower()
                            ]
                        preview_table.rows = [_build_row(r) for r in filtered]
                        preview_table.update()

                    preview_search.on("keydown.enter", lambda e: _render_filtered_rows())
                    preview_search.on("change", lambda e: _render_filtered_rows())

                    def _do_refresh_preview_sync() -> None:
                        """同步执行刷新预览。"""
                        nonlocal _cached_results, _cached_price_map

                        cap_min_val: float = float(cap_min_input.value or 0)
                        cap_max_val: float = float(cap_max_input.value or 0)
                        pe_min_val: float = float(pe_min_input.value or 0)
                        pe_max_val: float = float(pe_max_input.value or 0)

                        filters_set: bool = not (
                            cap_min_val == 0 and cap_max_val == 0
                            and pe_min_val == 0 and pe_max_val == 0
                        )
                        industries_selected: bool = bool(selected_industries)

                        if not filters_set and not industries_selected:
                            _cached_results = []
                        elif filters_set and not industries_selected:
                            try:
                                _cached_results = AppContext().screener_api.search(
                                    cap_min=cap_min_val,
                                    cap_max=cap_max_val,
                                    ttm_min=pe_min_val,
                                    ttm_max=pe_max_val,
                                )
                            except ApiError as e:
                                ui.notify(f"筛选失败: {e.message}", color="negative")
                                return
                            except httpx.HTTPStatusError as e:
                                ui.notify(f"筛选失败(HTTP {e.response.status_code}): {e.response.text}", color="negative")
                                return
                            except Exception as e:  # noqa: BLE001
                                ui.notify(f"网络错误: {e!s}", color="negative")
                                return
                        elif not filters_set and industries_selected:
                            _cached_results = []
                            for ind_name in selected_industries:
                                try:
                                    _cached_results.extend(
                                        AppContext().stock_info_api.get_by_industry(ind_name)
                                    )
                                except (httpx.HTTPStatusError, Exception):  # noqa: BLE001, S110
                                    pass
                        else:
                            try:
                                _cached_results = AppContext().screener_api.search(
                                    cap_min=cap_min_val,
                                    cap_max=cap_max_val,
                                    ttm_min=pe_min_val,
                                    ttm_max=pe_max_val,
                                )
                            except ApiError as e:
                                ui.notify(f"筛选失败: {e.message}", color="negative")
                                return
                            except httpx.HTTPStatusError as e:
                                ui.notify(f"筛选失败(HTTP {e.response.statusCode}): {e.response.text}", color="negative")
                                return
                            except Exception as e:  # noqa: BLE001
                                ui.notify(f"网络错误: {e!s}", color="negative")
                                return

                            if _cached_results:
                                try:
                                    result_codes: str = ",".join(
                                        str(r.get("code", "")) for r in _cached_results
                                    )
                                    stock_infos: list[dict[str, Any]] = (
                                        AppContext().stock_info_api.get_by_codes(result_codes)
                                    )
                                    code_industry: dict[str, str] = {}
                                    for si in stock_infos:
                                        c: str = str(si.get("code", ""))
                                        ind: str = str(si.get("industry") or si.get("industry_name") or "")
                                        if c and ind:
                                            code_industry[c] = ind
                                    _cached_results = [
                                        r for r in _cached_results
                                        if code_industry.get(str(r.get("code", "")), "") in selected_industries
                                    ]
                                except (httpx.HTTPStatusError, Exception):  # noqa: BLE001, S110
                                    pass

                        # 批量获取估值和利润数据
                        codes: list[str] = [str(r.get("code", "")) for r in _cached_results]
                        valid_codes: list[str] = [c for c in codes if c]
                        _valuation_map: dict[str, dict[str, Any]] = {}
                        _profit_map: dict[str, dict[str, Any]] = {}
                        if valid_codes:
                            codes_str_v: str = ",".join(valid_codes)
                            try:
                                _valuation_map = AppContext().finance_api.get_valuation(codes=codes_str_v)
                            except (httpx.HTTPStatusError, Exception):  # noqa: BLE001, S110
                                pass
                            try:
                                _profit_map = AppContext().finance_api.get_profit(codes=codes_str_v)
                            except (httpx.HTTPStatusError, Exception):  # noqa: BLE001, S110
                                pass

                        for r in _cached_results:
                            c: str = str(r.get("code", ""))
                            if c in _valuation_map:
                                val_data: dict[str, Any] = _valuation_map[c]
                                if not r.get("ttm_pe"):
                                    r["ttm_pe"] = val_data.get("ttm_pe", "")
                                if not r.get("total_market_cap"):
                                    r["total_market_cap"] = val_data.get("total_market_cap", "")
                            if c in _profit_map:
                                profit_data: dict[str, Any] = _profit_map[c]
                                if not r.get("profit_margin"):
                                    r["profit_margin"] = profit_data.get("net_profit_growth_rate", "")

                        # 批量获取当日实时行情价
                        _cached_price_map = {}
                        if valid_codes:
                            try:
                                start_day_k = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")  # noqa: DTZ005
                                end_day_k = datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005
                                all_kline_p: list[dict[str, Any]] = AppContext().market_api.get_day_kline(
                                    codes=",".join(valid_codes),
                                    start=start_day_k,
                                    end=end_day_k,
                                )
                                for row in all_kline_p:
                                    c: str = str(row.get("code", ""))
                                    price: Any = row.get("price", row.get("close", ""))
                                    if c and price is not None and price != "":
                                        _cached_price_map[c] = float(price)
                            except (httpx.HTTPStatusError, Exception):  # noqa: BLE001, S110
                                pass

                        preview_search.value = ""
                        _render_filtered_rows()

                    async def refresh_preview() -> None:
                        """刷新预览（带加载状态）。"""
                        nonlocal _preview_loading_ref
                        btn: Any = _preview_loading_ref.get("btn")
                        spinner: Any = _preview_loading_ref.get("spinner")
                        if btn:
                            btn.set_enabled(False)
                            btn.set_text("刷新中...")
                        if spinner:
                            spinner.set_visibility(True)
                        await asyncio.sleep(0.15)
                        try:
                            _do_refresh_preview_sync()
                        finally:
                            if btn:
                                btn.set_enabled(True)
                                btn.set_text("刷新预览")
                            if spinner:
                                spinner.set_visibility(False)

                    def _append_selected_stocks() -> None:
                        """将预览中选中的股票追加到待添加列表。"""
                        nonlocal selected_preview_codes, pending_add_codes
                        if not selected_preview_codes:
                            ui.notify("请先在预览表格中勾选股票", color="warning")
                            return
                        # 过滤掉已在当前股票池中的股票
                        existing: set[str] = {
                            str(s.get("code", "")) for s in current_stocks
                            if str(s.get("code", "")) not in removed_codes
                        }
                        new_codes: set[str] = selected_preview_codes - existing
                        if not new_codes:
                            ui.notify("选中的股票已在当前股票池中", color="info")
                            return
                        pending_add_codes.update(new_codes)
                        selected_preview_codes.clear()
                        _render_pending_chips()
                        _update_pending_count()
                        ui.notify(f"已添加 {len(new_codes)} 只股票到待添加列表", color="positive")

                    # 待添加股票标签展示
                    with ui.column().classes("w-full gap-2").style("padding: 0 16px 8px;"):
                        ui.label("待添加股票:").classes("text-xs font-bold").style("color: #9ca3af;")
                        pending_container: ui.column = ui.column().classes("w-full gap-1")

                    def _render_pending_chips() -> None:
                        """渲染待添加股票为可移除标签。"""
                        pending_container.clear()
                        if pending_add_codes:
                            with pending_container:  # noqa: SIM117
                                with ui.row().classes("w-full gap-2 flex-wrap"):
                                    for code in sorted(pending_add_codes):
                                        with ui.row().classes("items-center gap-1").style(
                                            "background-color: rgba(59,130,246,0.15); border: 1px solid #3b82f6; "
                                            "border-radius: 12px; padding: 2px 6px;"
                                        ):
                                            ui.label(code).classes("text-xs").style("color: #60a5fa;")

                                            def _make_remove_pending(c: str) -> Any:
                                                return lambda: _remove_pending_code(c)

                                            ui.button(
                                                icon="close",
                                                on_click=_make_remove_pending(code),
                                            ).props("flat round dense size=xs").style("color: #ef4444;")

                    def _remove_pending_code(code: str) -> None:
                        """从待添加列表中移除股票代码。"""
                        nonlocal pending_add_codes
                        pending_add_codes.discard(code)
                        _render_pending_chips()
                        _update_pending_count()

                    _render_pending_chips()

                    # 底部按钮行
                    with ui.row().classes("w-full gap-2").style(
                        f"padding: 12px; border-top: 1px solid {border_color};"
                    ):
                        with ui.row().classes("items-center gap-2"):
                            refresh_btn = ui.button("刷新预览", on_click=refresh_preview).props("flat").style(
                                f"color: #60a5fa; background-color: {card_bg}; border: 1px solid {border_color}; "
                                f"border-radius: 8px; font-size: 12px; font-weight: 700;"
                            )
                            _preview_loading_ref["btn"] = refresh_btn
                            refresh_spinner = ui.spinner(size="sm").style("color: #60a5fa;")
                            refresh_spinner.set_visibility(False)
                            _preview_loading_ref["spinner"] = refresh_spinner
                        ui.button("追加选中股票", on_click=_append_selected_stocks).props("flat").style(
                            "color: #ffffff !important; background-color: #3b82f6; "
                            "border-radius: 8px; font-size: 12px; font-weight: 700;"
                        )

        # ---- 底部操作栏 ----
        with ui.row().classes("w-full items-center justify-end gap-3").style(
            f"padding: 16px 24px; border-top: 1px solid {border_color};"
        ):
            ui.button("取消", on_click=dialog.close).props("flat").style(
                "color: #9ca3af; font-size: 14px; font-weight: 500;"
            )

            def handle_save() -> None:
                """保存编辑：执行股票添加和移除操作。"""
                new_codes: list[str] = list(pending_add_codes)

                has_changes: bool = bool(new_codes) or bool(removed_codes)
                if not has_changes:
                    ui.notify("没有修改", color="info")
                    return

                errors: list[str] = []

                if removed_codes:
                    try:
                        AppContext().pool_api.remove_stocks(pool_name, list(removed_codes))
                        ui.notify(f"已从「{pool_name}」移除 {len(removed_codes)} 只股票", color="positive")
                    except ApiError as e:
                        errors.append(f"移除失败: {e.message}")
                    except httpx.HTTPStatusError as e:
                        errors.append(f"移除失败(HTTP {e.response.status_code}): {e.response.text}")
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"移除失败: {e!s}")

                if new_codes:
                    try:
                        AppContext().pool_api.add_stocks(pool_name, new_codes)
                        ui.notify(f"已向「{pool_name}」添加 {len(new_codes)} 只股票", color="positive")
                    except ApiError as e:
                        errors.append(f"添加失败: {e.message}")
                    except httpx.HTTPStatusError as e:
                        errors.append(f"添加失败(HTTP {e.response.status_code}): {e.response.text}")
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"添加失败: {e!s}")

                if errors:
                    ui.notify("; ".join(errors), color="negative")
                else:
                    dialog.close()
                    ui.navigate.reload()

            ui.button("保存修改", on_click=handle_save).props("flat").style(
                "background-color: #3b82f6; color: #ffffff !important; padding: 8px 24px; "
                "border-radius: 8px; font-size: 14px; font-weight: 500;"
            )

    dialog.open()
