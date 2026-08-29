#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-08-25
Description: 策略列表页面 — 严格按 strategies.html 原型实现
    - 筛选栏（搜索策略名称/描述 + 策略类型 + 状态 + 重置）
    - 策略表格（名称+描述 / 类型 / 创建时间 / 状态 / 最近信号 / 查看详情）
    - 分页页脚
    - 策略详情面板（持仓信息 / 交易明细 / 运行日志）
    - 创建策略对话框（基本信息与类配置 / 选股配置 / 回测参数）

数据来源：
    - 用户策略关联: user_strategy_api.list()
    - 策略模板:     strategy_api.get_by_id() / strategy_api.create()
    - 最近信号列:   SignalManager 内存信号
    - 持仓信息:     user_strategy_api.get_latest_execution().positions
    - 交易明细:     trade_signal_api.list(strategy_id=...)
    - 运行日志:     user_strategy_api.get_runlog()
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any

from nicegui import ui

from app_context import AppContext
from utils import tools

logger = logging.getLogger(__name__)

# 分页每页条数
PAGE_SIZE: int = 10

# 用户策略状态 → (显示文本, 文字颜色, 背景颜色)
_STATUS_STYLES: dict[str, tuple[str, str, str]] = {
    "running": ("运行中", "#34d399", "rgba(52,211,153,0.3)"),
    "stopped": ("已停止", "#d1d5db", "rgba(55,65,81,0.8)"),
    "paused": ("已暂停", "#fbbf24", "rgba(251,191,36,0.25)"),
    "error": ("异常", "#f87171", "rgba(248,113,113,0.3)"),
}

# 创建对话框中的候选策略类型（与 StrategyDao.strategy_type 取值一致）
_STRATEGY_TYPES: list[str] = ["选股策略", "盯盘策略", "复盘策略"]

# 策略默认参数分组 key → 显示标题
_PARAM_GROUP_LABELS: dict[str, str] = {
    "match": "选股参数",
    "buy": "买入参数",
    "sell": "卖出参数",
}


def _is_numeric_value(value: Any) -> bool:
    """判断参数默认值是否为数字类型（决定使用数字输入框还是文本输入框）。

    :param value: 参数默认值
    :return: 数字类型返回 True，否则返回 False
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        stripped: str = value.strip()
        if not stripped:
            return False
        try:
            float(stripped)
            return True
        except ValueError:
            return False
    return False


def _render_params_container(
    container: Any,
    params: dict[str, Any],
    saved: dict[str, Any] | None,
    input_bg: str,
    font_color: str,
    title_color: str,
    muted_color: str,
) -> dict[str, dict[str, Any]]:
    """在目标容器内渲染策略参数编辑区域。

    依据 default_params 结构中的分组（match/buy/sell）逐组渲染：
    每个参数显示 desc 作为标签，默认值为 value；若 saved 中已有用户保存的值则优先使用。
    数字值渲染为数字输入框，非数字值渲染为文本输入框。

    :param container: 目标容器（NiceGUI column / tab_panel）
    :param params: 策略模板的 default_params 字典
    :param saved: 用户已保存的 strategy_params（可选，None 表示使用默认值）
    :param input_bg: 输入框背景色
    :param font_color: 输入框文字颜色
    :param title_color: 分组标题文字颜色
    :param muted_color: 参数标签/占位文字颜色
    :return: 参数收集器 {group: {key: {"input": 控件, "is_number": bool}}}
    """
    saved = saved or {}
    collector: dict[str, dict[str, Any]] = {}
    if not params:
        with container:
            ui.label("该策略暂无参数配置").classes("text-sm").style(f"color: {muted_color};")
        return collector
    with container:
        for group_key, group_params in (params or {}).items():
            group_label: str = _PARAM_GROUP_LABELS.get(str(group_key), str(group_key))
            ui.label(group_label).classes("text-base font-semibold").style(
                f"color: {title_color}; margin-top: 16px;"
            )
            collector[str(group_key)] = {}
            for key, item in (group_params or {}).items():
                desc: str = str(item.get("desc", key) or key)
                default_value: Any = item.get("value", 0.0)
                saved_group: dict[str, Any] = saved.get(str(group_key)) or {}
                current_val: Any = saved_group.get(key, default_value)
                is_number: bool = _is_numeric_value(default_value)
                with ui.row().classes("w-full items-center gap-3").style("padding: 8px 0;"):
                    ui.label(desc).classes("w-[45%] text-sm").style(f"color: {muted_color};")
                    if is_number:
                        num = ui.number(
                            value=float(current_val or 0.0),
                        ).classes("flex-1").props("outlined dense").style(
                            f"background-color: {input_bg}; color: {font_color}; width: 100%;"
                        )
                        collector[str(group_key)][str(key)] = {
                            "input": num, "is_number": True,
                        }
                    else:
                        txt = ui.input(
                            value=str(current_val if current_val is not None else ""),
                        ).classes("flex-1").props("outlined dense").style(
                            f"background-color: {input_bg}; color: {font_color}; width: 100%;"
                        )
                        collector[str(group_key)][str(key)] = {
                            "input": txt, "is_number": False,
                        }
    return collector


def _collect_params(collector: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """从参数收集器中提取用户填写的参数结果字典。

    :param collector: _render_params_container 返回的收集器
    :return: {group: {key: value}}
    """
    result: dict[str, dict[str, Any]] = {}
    for group_key, items in collector.items():
        result[group_key] = {}
        for key, meta in items.items():
            result[group_key][key] = meta["input"].value
    return result


def show_strategies_page() -> None:
    """策略列表页面入口。"""
    current_theme: dict[str, str] = AppContext().theme_manager.get_current_theme()
    bg_color: str = current_theme.get("background", "#0f0f1a")
    font_color: str = current_theme.get("font_color", "#e5e7eb")
    border_color: str = current_theme.get("widget_border_color", "#334155")
    card_bg: str = current_theme.get("tab_normal_color", "#1e293b")
    input_bg: str = current_theme.get("background", "#111827")
    primary_color: str = current_theme.get("primary", "#2563eb")
    title_color: str = current_theme.get("text", "#f3f4f6")
    muted_color: str = current_theme.get("placeholder", "#9ca3af")
    field_label_color: str = current_theme.get("placeholder", "#d1d5db")
    tab_bg: str = current_theme.get("tab_normal_color", "#1e293b")
    tab_active: str = current_theme.get("tab_active_color", "#2563eb")

    # 页面级 CSS：自定义表格行样式（行高、悬浮、选中高亮）
    ui.add_css("""
        .s-table-row {
            display: flex;
            align-items: center;
            padding: 14px 24px;
            border-bottom: 1px solid rgba(45,55,72,0.6);
            cursor: pointer;
            transition: background-color 0.15s;
        }
        .s-table-row:hover {
            background-color: rgba(37,99,235,0.10) !important;
        }
        .s-table-row.my-selected {
            background-color: rgba(37,99,235,0.22) !important;
        }
        .s-table-row.my-selected:hover {
            background-color: rgba(37,99,235,0.28) !important;
        }
        .s-col-name { flex: 32; min-width: 0; }
        .s-col-type { flex: 12; }
        .s-col-time { flex: 12; }
        .s-col-status { flex: 10; text-align: center; }
        .s-col-signal { flex: 18; }
        .s-col-action { flex: 10; text-align: right; }
        .s-table-header {
            display: flex;
            align-items: center;
            padding: 12px 24px;
            border-bottom: 1px solid #334155;
            background-color: #111827;
        }
    """)

    # 页面级 CSS：策略对话框标签页配色（tab_normal_color / tab_active_color）
    ui.add_css(f"""
        .strategy-dialog-tabs .q-tab {{
            background-color: {tab_bg} !important;
            color: {muted_color} !important;
            border-radius: 8px !important;
            margin: 6px 2px !important;
        }}
        .strategy-dialog-tabs .q-tab--active,
        .strategy-dialog-tabs .q-tab.q-tab--active,
        .strategy-dialog-tabs .q-tab[aria-selected="true"] {{
            background-color: {tab_active} !important;
            color: #ffffff !important;
        }}
        .strategy-dialog-tabs .q-tab__indicator {{
            display: none !important;
        }}
        .strategy-dialog-tabs {{
            background-color: {input_bg} !important;
            border-bottom: 1px solid {border_color} !important;
        }}
    """)

    # 筛选与分页状态
    filter_state: dict[str, str] = {
        "search": "", "stype": "全部类型", "status": "全部状态",
    }
    page_state: dict[str, int] = {"page": 0}
    # 视图状态：是否已自动展示过首条策略详情（原型默认选中第一条）、当前选中行
    view_state: dict[str, Any] = {"auto_shown": False, "selected": None}

    # ---- 页面骨架（容器先行创建，渲染函数在下方定义并调用）----
    with ui.column().classes("w-full gap-6").style(
        f"padding: 32px; background-color: {bg_color};"
    ):
        # 标题行：标题 + 创建策略按钮
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("策略列表").classes("text-xl font-bold").style("color: #f3f4f6;")
            ui.button(
                "创建策略", icon="add",
                on_click=lambda: _open_create_strategy_dialog(_refresh),
            ).props("unelevated no-caps").classes("text-sm font-medium").style(
                "background-color: #2563eb; color: #ffffff; "
                "border-radius: 8px; padding: 8px 16px;"
            )

        # 筛选栏
        with ui.row().classes("w-full items-center gap-4 flex-wrap").style(
            f"background-color: {card_bg}; border: 1px solid {border_color}; "
            f"border-radius: 12px; padding: 16px;"
        ):
            search_input: ui.input = (
                ui.input(placeholder="搜索策略名称或描述...")
                .props("outlined dense clearable debounce=300")
                .classes("flex-1 min-w-[200px]")
                .style(f"background-color: {input_bg}; color: {font_color};")
            )
            strategy_types = tools.load_strategy_type()
            type_select: ui.select = (
                ui.select(options=["全部类型", *strategy_types], value="全部类型")
                .props("outlined dense")
                .classes("min-w-[160px]")
                .style(f"background-color: {input_bg}; color: {font_color};")
            )
            status_select: ui.select = (
                ui.select(
                    options=["全部状态", "运行中", "已停止", "已暂停", "异常"],
                    value="全部状态",
                )
                .props("outlined dense")
                .classes("min-w-[140px]")
                .style(f"background-color: {input_bg}; color: {font_color};")
            )

            def _on_search_change(e: Any) -> None:
                """搜索输入变化：更新条件并刷新。"""
                val: Any = e.value if hasattr(e, "value") else getattr(e, "args", "")
                filter_state["search"] = str(val or "").strip().lower()
                page_state["page"] = 0
                _refresh()

            def _on_type_change(e: Any) -> None:
                """类型下拉变化：更新条件并刷新。"""
                val: Any = e.value if hasattr(e, "value") else getattr(e, "args", "")
                if isinstance(val, dict):
                    val = val.get("value", "")
                filter_state["stype"] = str(val) if val else "全部类型"
                page_state["page"] = 0
                _refresh()

            def _on_status_change(e: Any) -> None:
                """状态下拉变化：更新条件并刷新。"""
                val: Any = e.value if hasattr(e, "value") else getattr(e, "args", "")
                if isinstance(val, dict):
                    val = val.get("value", "")
                filter_state["status"] = str(val) if val else "全部状态"
                page_state["page"] = 0
                _refresh()

            def _reset_filters() -> None:
                """重置全部筛选条件。"""
                search_input.value = ""
                type_select.value = "全部类型"
                status_select.value = "全部状态"
                filter_state["search"] = ""
                filter_state["stype"] = "全部类型"
                filter_state["status"] = "全部状态"
                page_state["page"] = 0
                _refresh()

            search_input.on_value_change(_on_search_change)
            type_select.on_value_change(_on_type_change)
            status_select.on_value_change(_on_status_change)
            ui.button("重置", on_click=_reset_filters).props("flat no-caps") \
                .classes("text-sm font-medium").style("color: #9ca3af;")

        # 策略表格卡片（含分页页脚）
        with ui.card().classes("w-full gap-0 overflow-hidden").style(
            f"background-color: {card_bg}; border: 1px solid {border_color}; "
            f"border-radius: 12px; padding: 0;"
        ):
            table_container: ui.column = ui.column().classes("w-full gap-0")
            pagination_container: ui.row = (
                ui.row().classes("w-full items-center justify-between").style(
                    f"background-color: {input_bg}; "
                    f"border-top: 1px solid {border_color}; padding: 16px 24px;"
                )
            )

        # 详情面板（点击表格行后显示）
        detail_wrap: ui.column = (
            ui.column().classes("w-full gap-0 hidden").style(
                f"background-color: {input_bg}; border: 1px solid {border_color}; "
                f"border-radius: 12px; overflow: hidden;"
            )
        )


    # ---- 数据加载 ----

    def _load_rows() -> list[dict[str, Any]]:
        """拉取并组装策略表格数据。

        以用户策略关联为主表，关联策略模板补充名称/类型/描述。

        :return: 策略行数据列表（按创建时间倒序）
        """
        try:
            user_strategies: list[dict[str, Any]] = AppContext().user_strategy_api.list()
        except Exception:
            logger.warning("查询用户策略列表失败", exc_info=True)
            user_strategies = []
        user_strategies.sort(key=lambda s: str(s.get("create_time", "")), reverse=True)

        templates: dict[str, dict[str, Any]] = {}
        rows: list[dict[str, Any]] = []
        for us in user_strategies:
            sid: str = str(us.get("strategy_id", ""))
            if sid and sid not in templates:
                try:
                    templates[sid] = AppContext().strategy_api.get_by_id(sid)
                except Exception:
                    logger.warning("查询策略模板失败: %s", sid, exc_info=True)
                    templates[sid] = {}
            tpl: dict[str, Any] = templates.get(sid, {})
            total_profit = float(us.get("total_profit", 0.0))
            init_amount = float(us.get("initial_amount", 0.0))
            profit = 0.00 if init_amount == 0.0 else total_profit / init_amount * 100
            total_profit_str: str = f"{round(profit, 2)}%"
            # 正收益显示红色，负收益显示绿色（A 股惯例）
            profit_color: str = "#f87171" if profit > 0 else ("#34d399" if profit < 0 else "#6b7280")
            rows.append({
                "us_id": str(us.get("_id", "") or us.get("id", "")),
                "sid": sid,
                "name": str(tpl.get("name", "") or sid or "未命名策略"),
                "description": str(tpl.get("description", "") or ""),
                "stype": str(tpl.get("strategy_type", "") or "-"),
                "total_profit": total_profit_str,
                "profit_color": profit_color,
                "status": str(us.get("status", "stopped")),
                "pool_id": str(us.get("pool_id", "") or ""),
                "initial_amount": float(us.get("initial_amount", 0.0) or 0.0),
                "max_stock_count": int(us.get("max_stock_count", 0) or 0),
                "strategy_params": us.get("strategy_params", {}) or {},
            })
        return rows

    def _latest_signal_text(sid: str) -> tuple[str, str]:
        """获取指定策略最近一次信号文案与颜色。

        :param sid: 策略模板 ID
        :return: (信号文本如 '买入 600519', 颜色)，无信号返回 ('-', 灰色)
        """
        signals: list[dict[str, Any]] = AppContext().signal_manager.get_signals(limit=50)
        for sig in signals:
            if str(sig.get("strategy_id", "")) == sid:
                text: str = f"{sig.get('action', '')} {sig.get('stock_code', '')}".strip()
                color: str = "#34d399" if sig.get("action") == "买入" else "#f87171"
                return (text or "-", color)
        return ("-", "#6b7280")

    def _filtered_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按筛选条件过滤行数据。

        :param rows: 全量行数据
        :return: 过滤后的行数据
        """
        result: list[dict[str, Any]] = []
        for row in rows:
            if filter_state["search"]:
                haystack: str = (row["name"] + row["description"]).lower()
                if filter_state["search"] not in haystack:
                    continue
            if filter_state["stype"] != "全部类型" and row["stype"] != filter_state["stype"].strip():
                continue
            status_text: str = _STATUS_STYLES.get(row["status"], ("未知",))[0]
            if filter_state["status"] != "全部状态" and status_text != filter_state["status"]:
                continue
            result.append(row)
        
        return result

    # ---- 表格与分页渲染 ----

    def _render_pagination(total_items: int) -> None:
        """渲染分页页脚。

        :param total_items: 过滤后总条数
        """
        pagination_container.clear()
        total_pages: int = max(1, math.ceil(total_items / PAGE_SIZE))
        current: int = min(page_state["page"], total_pages - 1)
        start_idx: int = current * PAGE_SIZE + 1 if total_items > 0 else 0
        end_idx: int = min(start_idx + PAGE_SIZE - 1, total_items)
        with pagination_container:
            ui.label(f"显示第 {start_idx}-{end_idx} 条结果，共 {total_items} 条").classes(
                "text-sm tracking-tight"
            ).style("color: #6b7280;")
            with ui.row().classes("items-center gap-2"):
                prev_style: str = _page_btn_style(disabled=(current <= 0))
                ui.label("上一页").classes("text-sm rounded-lg px-4 py-2 cursor-pointer").style(
                    prev_style
                ).on("click", lambda: _goto_page(current - 1))
                for p in range(total_pages):
                    active: bool = p == current
                    style: str = (
                        "border: 1px solid #3b82f6; background-color: #1e293b; "
                        "color: #e5e7eb; font-weight: 500;"
                        if active
                        else f"border: 1px solid {border_color}; background-color: transparent; color: #9ca3af;"
                    )
                    ui.label(str(p + 1)).classes(
                        "text-sm rounded-lg px-3 py-2 cursor-pointer"
                    ).style(style).on("click", lambda _p=p: _goto_page(_p))
                next_style: str = _page_btn_style(disabled=(current >= total_pages - 1))
                ui.label("下一页").classes("text-sm rounded-lg px-4 py-2 cursor-pointer").style(
                    next_style
                ).on("click", lambda: _goto_page(current + 1))

    def _page_btn_style(disabled: bool) -> str:
        """分页按钮样式。

        :param disabled: 是否禁用
        :return: CSS 样式字符串
        """
        if disabled:
            return f"border: 1px solid {border_color}; color: #4b5563; cursor: not-allowed;"
        return f"border: 1px solid {border_color}; color: #9ca3af;"

    def _goto_page(page: int) -> None:
        """跳转到指定页并刷新表格。

        :param page: 目标页码（0 起始）
        """
        if page < 0:
            return
        page_state["page"] = page
        _render_table()

    def _render_table() -> list[dict[str, Any]]:
        """渲染策略表格（手动构建 UI 行元素 + Python click 回调，确保点击事件可靠传递）。

        :return: 过滤后的全部行数据（供首次自动展示详情使用）
        """
        table_container.clear()
        rows: list[dict[str, Any]] = _load_rows()
        filtered: list[dict[str, Any]] = _filtered_rows(rows)
        total_pages: int = max(1, math.ceil(len(filtered) / PAGE_SIZE))
        if page_state["page"] >= total_pages:
            page_state["page"] = total_pages - 1
        start: int = page_state["page"] * PAGE_SIZE
        page_rows: list[dict[str, Any]] = filtered[start:start + PAGE_SIZE]

        # 预计算每行的信号和状态
        for r in page_rows:
            sig_t, sig_c = _latest_signal_text(r["sid"])
            r["_signal_text"] = sig_t
            r["_signal_color"] = sig_c
            st = _STATUS_STYLES.get(r["status"], ("未知", "#d1d5db", "rgba(55,65,81,0.8)"))
            r["_status_text"] = st[0]
            r["_status_color"] = st[1]
            r["_status_bg"] = st[2]
            r["_desc"] = r.get("description", "") or "暂无描述"

        with table_container:
            # 表头
            with ui.row().classes("s-table-header w-full"):
                for label_text, col_cls in [
                    ("策略名称", "s-col-name"), ("类型", "s-col-type"),
                    ("总收益", "s-col-time"), ("状态", "s-col-status"),
                    ("最近信号", "s-col-signal"), ("操作", "s-col-action"),
                ]:
                    align: str = "text-align: right;" if col_cls == "s-col-action" else ""
                    ui.label(label_text).classes(
                        "text-xs font-semibold uppercase tracking-wider"
                    ).style(f"color: #9ca3af; {align}").classes(col_cls)

            # 数据行
            for row_data in page_rows:
                with ui.row().classes("s-table-row w-full") as data_row:
                    # 策略名称（名称 + 描述两行）
                    with ui.column().classes("s-col-name gap-0"):
                        ui.label(row_data["name"]).classes(
                            "text-sm font-medium"
                        ).style("color: #e5e7eb;")
                        ui.label(row_data["_desc"]).classes(
                            "text-xs italic"
                        ).style(
                            "color: #6b7280; overflow:hidden; text-overflow:ellipsis;"
                            "white-space:nowrap; max-width:260px;"
                        )
                    # 类型
                    ui.label(row_data["stype"]).classes("text-sm s-col-type").style(
                        "color: #9ca3af;"
                    )
                    # 总收益（正数红色，负数绿色，A 股惯例）
                    ui.label(row_data["total_profit"]).classes(
                        "text-sm font-medium s-col-time"
                    ).style(f"color: {row_data['profit_color']};")
                    # 状态徽标
                    with ui.column().classes("s-col-status gap-0 items-center"):
                        if row_data["status"] == "running":
                            ui.label("").classes(
                                "inline-block w-[6px] h-[6px] rounded-full mr-1 animate-pulse"
                            ).style("background-color: #34d399;")
                        ui.label(row_data["_status_text"]).classes(
                            "text-xs font-medium rounded-full px-2.5 py-0.5"
                        ).style(
                            f"color: {row_data['_status_color']}; "
                        )
                    # 最近信号
                    ui.label(row_data["_signal_text"]).classes(
                        "text-sm font-medium s-col-signal"
                    ).style(f"color: {row_data['_signal_color']};")
                    # 操作按钮（编辑图标）
                    with ui.column().classes("s-col-action gap-0 items-end"):
                        def _make_edit_click(r: dict[str, Any]) -> Callable[[], None]:
                            """创建编辑按钮回调（闭包捕获当前行数据）。"""
                            return lambda: _open_edit_strategy_dialog(r, _refresh)
                        ui.button(icon="edit", on_click=_make_edit_click(row_data)) \
                            .props("flat round dense").style(
                                "color: #60a5fa; cursor: pointer;"
                            )

                    # 绑定整行点击事件（点击行的任意位置触发详情展示）
                    def _make_row_click(r: dict[str, Any]) -> Callable[[], None]:
                        """创建整行点击回调（闭包捕获当前行数据）。"""
                        return lambda: _on_row_click(r)
                    data_row.on("click", _make_row_click(row_data))

        _render_pagination(len(filtered))
        return filtered

    def _on_row_click(row: dict[str, Any]) -> None:
        """处理表格行点击：更新选中状态并展示详情面板。

        :param row: 被点击行的数据字典
        """
        view_state["selected"] = row
        _show_detail(row)

    def _refresh() -> None:
        """刷新表格与分页；首次加载后自动在页面底部展示第一条策略详情。"""
        filtered_rows: list[dict[str, Any]] = _render_table()
        if not view_state["auto_shown"] and filtered_rows:
            view_state["auto_shown"] = True
            _show_detail(dict(filtered_rows[0]))

    # ---- 详情面板 ----

    def _hide_detail() -> None:
        """隐藏详情面板。"""
        detail_wrap.classes("hidden")
        view_state["selected"] = None

    def _show_detail(row: dict[str, Any]) -> None:
        """显示策略详情面板。

        :param row: 表格行数据
        """
        detail_wrap.clear()
        detail_wrap.classes(remove="hidden")
        view_state["selected"] = dict(row)
        tab_state: dict[str, str] = {"tab": "positions"}

        with detail_wrap:
            # 面板头部：标题 + 关闭按钮
            with ui.row().classes("w-full items-center justify-between").style(
                f"padding: 16px 24px; border-bottom: 1px solid {border_color};"
            ):
                ui.label(f"{row['name']} — 策略详情").classes("text-base font-semibold").style(
                    "color: #e5e7eb;"
                )
                ui.button(icon="close", on_click=_hide_detail).props("flat round dense").style(
                    "color: #9ca3af;"
                )
            tabs_row: ui.row = ui.row().classes("w-full items-center gap-2").style(
                f"padding: 12px 24px; border-bottom: 1px solid {border_color};"
            )
            content_container: ui.column = ui.column().classes("w-full gap-0")

        def _render_tab_content() -> None:
            """渲染当前选中标签页的内容。

            注意：必须在 with content_container 上下文中创建元素，
            避免事件处理期间元素被添加到错误插槽。
            """
            content_container.clear()
            with content_container:
                if tab_state["tab"] == "positions":
                    _render_positions(content_container, row)
                elif tab_state["tab"] == "trades":
                    _render_trades(content_container, row)
                else:
                    _render_runlogs(content_container, row)

        def _render_tabs() -> None:
            """渲染标签页切换栏（同样需要显式进入 tabs_row 上下文）。"""
            tabs_row.clear()
            with tabs_row:
                for key, text in [
                    ("positions", "持仓信息"), ("trades", "交易明细"), ("runlogs", "运行日志"),
                ]:
                    active: bool = tab_state["tab"] == key
                    style: str = (
                        "background-color: #1d4ed8; color: #ffffff;"
                        if active
                        else "background-color: transparent; color: #9ca3af;"
                    )
                    ui.label(text).classes(
                        "text-sm font-medium rounded-lg px-4 py-2 cursor-pointer"
                    ).style(style).on("click", lambda _k=key: _switch_tab(_k))

        def _switch_tab(key: str) -> None:
            """切换标签页。

            :param key: 标签页标识
            """
            tab_state["tab"] = key
            _render_tabs()
            _render_tab_content()

        _render_tabs()
        _render_tab_content()

    def _detail_empty(container: ui.column, text: str) -> None:
        """在容器内渲染空数据提示。

        :param container: 目标容器
        :param text: 提示文本
        """
        with container, ui.column().classes("w-full items-center justify-center").style("padding: 48px;"):
            ui.icon("inbox").style("color: #6b7280; font-size: 40px;")
            ui.label(text).classes("text-sm").style("color: #6b7280; margin-top: 8px;")

    def _render_positions(container: ui.column, row: dict[str, Any]) -> None:
        """渲染持仓信息标签页内容（原型列：股票代码/股票名称/持仓数量/成本价/现价/盈亏）。

        持仓字典字段来自 PositionItem: code/name/quantity/cost_price/current_price/
        profit_rate/profit_amount。

        :param container: 目标容器
        :param row: 表格行数据
        """
        execution: dict[str, Any] = {}
        try:
            execution = AppContext().user_strategy_api.get_latest_execution(row["us_id"])
        except Exception:
            logger.warning("查询最新执行记录失败: %s", row["us_id"], exc_info=True)
        positions: list[dict[str, Any]] = list(execution.get("positions", []))
        if not positions:
            _detail_empty(container, "暂无持仓")
            return
        with container:
            # 表头（原型样式：深色背景 + 底部边框）
            with ui.row().classes("w-full items-center").style(
                f"background-color: {card_bg}; border-bottom: 1px solid {border_color}; "
                "padding: 10px 24px;"
            ):
                for hdr in ["股票代码", "股票名称", "持仓数量", "成本价", "现价", "盈亏"]:
                    align: str = "text-align: right;" if hdr in ("成本价", "现价", "盈亏") else ""
                    ui.label(hdr).classes(
                        "text-xs font-semibold uppercase tracking-wider"
                    ).style(f"color: #9ca3af; flex: 1; {align}")
            # 数据行（字段: code/name/quantity/cost_price/current_price/profit_amount）
            for idx, pos in enumerate(positions):
                quantity: int = int(float(pos.get("quantity", 0) or 0))
                cost_price: float = float(pos.get("cost_price", 0.0) or 0.0)
                current_price: float = float(pos.get("current_price", 0.0) or 0.0)
                profit_amount: float = float(pos.get("profit_amount", 0.0) or 0.0)
                bg: str = input_bg if idx % 2 == 0 else "#151c29"
                profit_text: str = f"{profit_amount:+,.0f}"
                profit_color: str = "#34d399" if profit_amount >= 0 else "#f87171"
                with ui.row().classes("w-full items-center").style(
                    f"padding: 12px 24px; background-color: {bg}; "
                    "border-bottom: 1px solid rgba(45,55,72,0.6);"
                ):
                    ui.label(str(pos.get("code", "-"))).classes("text-sm font-medium").style(
                        f"color: {font_color}; flex: 1;"
                    )
                    ui.label(str(pos.get("name", "-"))).classes("text-sm").style(
                        "color: #d1d5db; flex: 1;"
                    )
                    ui.label(str(quantity)).classes("text-sm").style(
                        "color: #d1d5db; flex: 1;"
                    )
                    ui.label(f"{cost_price:.2f}").classes("text-sm").style(
                        "color: #d1d5db; flex: 1; text-align: right;"
                    )
                    ui.label(f"{current_price:.2f}").classes("text-sm").style(
                        "color: #d1d5db; flex: 1; text-align: right;"
                    )
                    ui.label(profit_text).classes("text-sm font-medium").style(
                        f"color: {profit_color}; flex: 1; text-align: right;"
                    )

    def _stock_name_map(codes: list[str]) -> dict[str, str]:
        """批量查询股票代码对应名称。

        :param codes: 股票代码列表
        :return: code → name 映射（查询失败时返回空映射）
        """
        result: dict[str, str] = {}
        if not codes:
            return result
        try:
            infos: list[dict[str, Any]] = AppContext().stock_info_api.get_by_codes(
                ",".join(codes)
            )
            for info in infos:
                code_key: str = str(info.get("stock_code", "") or info.get("code", ""))
                name_val: str = str(info.get("stock_name", "") or info.get("name", ""))
                if code_key:
                    result[code_key] = name_val
        except Exception:
            logger.warning("查询股票名称失败", exc_info=True)
        return result

    def _render_trades(container: ui.column, row: dict[str, Any]) -> None:
        """渲染交易明细标签页内容（原型列：日期/股票/方向/数量/价格/成交额）。

        信号记录字段：create_time/action/stock_code/trade_price/profit_rate/
        profit_amount/reason，不含数量与成交额，缺失列显示 "--"。

        :param container: 目标容器
        :param row: 表格行数据
        """
        trades: list[dict[str, Any]] = []
        try:
            trades = AppContext().trade_signal_api.list(strategy_id=row["sid"], limit=20)
        except Exception:
            logger.warning("查询交易明细失败: %s", row["sid"], exc_info=True)
        if not trades:
            _detail_empty(container, "暂无交易记录")
            return
        names: dict[str, str] = _stock_name_map(
            [str(t.get("stock_code", "")) for t in trades if t.get("stock_code")]
        )
        with container:
            with ui.row().classes("w-full items-center").style(
                f"background-color: {card_bg}; border-bottom: 1px solid {border_color}; "
                "padding: 10px 24px;"
            ):
                for hdr in ["日期", "股票", "方向", "数量", "价格", "成交额"]:
                    align: str = "text-align: right;" if hdr in ("数量", "价格", "成交额") else ""
                    ui.label(hdr).classes(
                        "text-xs font-semibold uppercase tracking-wider"
                    ).style(f"color: #9ca3af; flex: 1; {align}")
            for idx, trade in enumerate(trades):
                action: str = str(trade.get("action", ""))
                code: str = str(trade.get("stock_code", ""))
                price: float = float(trade.get("trade_price", 0.0) or 0.0)
                bg: str = input_bg if idx % 2 == 0 else "#151c29"
                action_color: str = "#34d399" if action == "买入" else (
                    "#f87171" if action == "卖出" else "#9ca3af"
                )
                display_name: str = f"{code} {names.get(code, '')}".strip() or "-"
                with ui.row().classes("w-full items-center").style(
                    f"padding: 12px 24px; background-color: {bg}; "
                    "border-bottom: 1px solid rgba(45,55,72,0.6);"
                ):
                    ui.label(str(trade.get("create_time", ""))[:16]).classes("text-sm").style(
                        "color: #d1d5db; flex: 1;"
                    )
                    ui.label(display_name).classes("text-sm").style(
                        f"color: {font_color}; flex: 1;"
                    )
                    ui.label(action or "-").classes("text-sm font-medium").style(
                        f"color: {action_color}; flex: 1;"
                    )
                    ui.label("--").classes("text-sm").style(
                        "color: #6b7280; flex: 1; text-align: right;"
                    )
                    ui.label(f"{price:.2f}" if price else "--").classes("text-sm").style(
                        f"color: {font_color}; flex: 1; text-align: right;"
                    )
                    ui.label("--").classes("text-sm").style(
                        "color: #6b7280; flex: 1; text-align: right;"
                    )

    def _render_runlogs(container: ui.column, row: dict[str, Any]) -> None:
        """渲染运行日志标签页内容（原型样式：时间 [级别] 内容 的纯文本行）。

        运行记录字段：create_time/level(INFO/WARNING/ERROR)/log_content。

        :param container: 目标容器
        :param row: 表格行数据
        """
        logs: list[dict[str, Any]] = []
        try:
            logs = AppContext().user_strategy_api.get_runlog(row["us_id"], limit=50)
        except Exception:
            logger.warning("查询运行日志失败: %s", row["us_id"], exc_info=True)
        if not logs:
            _detail_empty(container, "暂无日志")
            return
        level_colors: dict[str, str] = {
            "ERROR": "#f87171", "WARNING": "#fbbf24", "INFO": "#9ca3af",
        }
        with container, ui.scroll_area().classes("w-full").style("max-height: 240px;"):
            for log_item in logs:
                    level: str = str(log_item.get("level", "INFO")).upper()
                    line_color: str = level_colors.get(level, "#9ca3af")
                    log_line: str = (
                        f"{str(log_item.get('create_time', ''))[:19]} "
                        f"[{level}] {log_item.get('log_content', '')!s}"
                    ).strip()
                    ui.label(log_line).classes("text-sm").style(
                        f"color: {line_color}; word-break: break-all; "
                        "padding: 6px 24px; border-bottom: 1px solid rgba(30,41,59,0.8);"
                    )
    # ---- 创建策略对话框 ----

    def _open_create_strategy_dialog(on_saved: Callable[[], None]) -> None:
        """打开创建策略对话框。

        左栏（2/3）：策略选择 + 筛选条件 + 回测参数
        右栏（1/3）：热门板块股票列表

        :param on_saved: 保存成功后的回调（用于刷新表格）
        """
        # 预加载数据
        all_strategies: list[dict[str, Any]] = []
        try:
            all_strategies = AppContext().strategy_api.list()
        except Exception:
            logger.warning("查询策略列表失败", exc_info=True)
        strategy_name_map: dict[str, dict[str, Any]] = {
            str(s.get("name", "")): s for s in all_strategies if s.get("name", "")
        }
        all_pools_list: list[dict[str, Any]] = []
        try:
            all_pools_list = AppContext().pool_api.list()
        except Exception:
            logger.exception("查询股票池失败")
        pool_name_map: dict[str, dict[str, Any]] = {
            str(s.get("name", "")): s for s in all_pools_list if s.get("name", "")
        }
        field_label_style = f"font-size: 0.875rem; font-weight: 500; color: {field_label_color};"
        input_style = f"flex: 1; background-color: {input_bg}; color: {font_color}; width: 100%;"
        with ui.dialog() as dialog, ui.card().classes("w-[600px] max-w-[95vw] gap-0").style(
                f"background-color: {card_bg}; border: 1px solid {border_color}; height: 70vh; max-height: 70vh;"
                f"border-radius: 12px; padding: 0; "
                f"display: flex; flex-direction: column;"
            ):
                # ---- 头部 ----
                with ui.row().classes("w-full items-center justify-between").style(
                    f"padding: 16px 24px; border-bottom: 1px solid {border_color};"
                ):
                    ui.label("创建新策略").classes("text-xl font-bold").style(f"color: {title_color};")
                    ui.button(icon="close", on_click=dialog.close).props("flat round dense").style(f"color: {muted_color};")

                # ---- 标签页（策略 / 参数） ----
                with ui.tabs().classes("w-full strategy-dialog-tabs").props("dense") as dialog_tabs:
                    ui.tab("策略").classes("text-sm")
                    ui.tab("参数").classes("text-sm")
                with ui.tab_panels(dialog_tabs, value="策略").classes("w-full flex-1").style(f"background-color: {card_bg};"):
                    # ---- 策略 tab：策略选择 + 股票池 配置 ----
                    with ui.tab_panel("策略").classes("p-0"):
                        with ui.scroll_area().classes("w-full").style("max-height: 48vh;"):
                            with ui.column().classes("w-full gap-0"):
                                def _section(title: str) -> None:
                                    """渲染对话框内的小节标题。"""
                                    with ui.row().classes("w-full items-center gap-3").style(
                                        "padding: 16px 24px 4px;"
                                    ):
                                        ui.label(title).classes("text-lg font-bold").style(f"flex: 1; color: {title_color};")

                                # ---- STEP 01：策略选择 ----
                                _section("策略选择")
                                strategy_name_options: list[str] = list(strategy_name_map.keys())

                                def _on_strategy_select(e: Any) -> None:
                                    """选中策略后刷新类型与描述标签，并重新渲染参数页。"""
                                    val: Any = e.value if hasattr(e, "value") else ""
                                    tpl = strategy_name_map.get(str(val), {})
                                    type_detail_label.set_text(
                                        str(tpl.get("strategy_type", "-") or "-")
                                    )
                                    desc_detail_label.set_text(
                                        str(tpl.get("description", "暂无描述") or "暂无描述")
                                    )
                                    if "params" in param_state:
                                        _render_params_panel()

                                with ui.row().classes("w-full gap-1 items-center").style("padding: 8px 24px 0;"):
                                    ui.label("* 策略名称").classes("w-[20%] text-xs").style(
                                        field_label_style,
                                    )
                                    strategy_name_select: ui.select = (
                                        ui.select(
                                            options=strategy_name_options,
                                            value=(
                                                strategy_name_options[0]
                                                if strategy_name_options else None
                                            ),
                                            on_change=_on_strategy_select,
                                        )
                                        .props("outlined dense").style(
                                            "flex: 1; min-width: 100px; border-bottom: 1px solid {border_color}"
                                        )
                                    )

                                # 选中后显示策略类型和描述（只读）
                                with ui.column().classes("w-full gap-3").style("padding: 8px 24px 0; margin-top: 10px"):
                                    with ui.row().classes("w-full gap-1 items-center"):
                                        ui.label("策略类型").classes("w-[20%] text-xs").style(
                                            field_label_style,
                                        )
                                        first_tpl = strategy_name_map.get(
                                            strategy_name_options[0], {}
                                        ) if strategy_name_options else {}
                                        type_detail_label = ui.label(
                                            str(first_tpl.get("strategy_type", "-") or "-")
                                        ).classes("text-sm").style(
                                            f"color: {font_color}; padding: 8px 12px; "
                                            "border-radius: 6px; "
                                        )
                                    with ui.row().classes("w-full gap-1 items-center"):
                                        ui.label("策略描述").classes("w-[20%] text-xs").style(
                                            field_label_style,
                                        )
                                        desc_detail_label = ui.label(
                                            str(
                                                first_tpl.get("description", "暂无描述")
                                                or "暂无描述"
                                            )
                                        ).classes("text-sm").style(
                                            f"color: {font_color}; padding: 8px 12px; "
                                            "border-radius: 6px; word-break: break-all;"
                                        )

                                _section("股票池")
                                pool_name_options: list[str] = list(pool_name_map.keys())

                                with ui.column().classes("w-full gap-3 flex-wrap").style("padding: 8px 24px 0; margin-top: 10px"):
                                    with ui.row().classes("w-full gap-1 items-center"):
                                        ui.label("* 股票池").classes("w-[20%] text-xs").style(
                                            field_label_style,
                                        )
                                        pool_name_select: ui.select = (
                                            ui.select(
                                                options=pool_name_options,
                                                value=(
                                                    pool_name_options[0]
                                                    if pool_name_options else None
                                                ),
                                            )
                                            .props("outlined dense").style(
                                                "flex: 1; min-width: 100px; border-bottom: 1px solid {border_color}"
                                            )
                                        )
                                    with ui.row().classes("w-full gap-1 items-center"):
                                        ui.label("初始资金 (元)").classes("w-[20%] text-xs").style(
                                            field_label_style
                                        )
                                        capital_input: ui.number = ui.number(
                                            value=1000000.0, format="%.0f",
                                        ).classes("w-[30%]").props("outlined dense").style(input_style)

                                    with ui.row().classes("w-full gap-1 items-center"):
                                        ui.label("最大持仓数").classes("w-[20%] text-xs").style(
                                            field_label_style,
                                        )
                                        max_stock_count_input: ui.number = ui.number(
                                            value=4, format="%d",
                                        ).classes("w-[30%]").props("outlined dense").style(input_style)

                    # ---- 参数 tab：策略运行参数 ----
                    with ui.tab_panel("参数").classes("p-0"):
                        with ui.scroll_area().classes("w-full").style("max-height: 48vh;"):
                            params_container: ui.column = (
                                ui.column().classes("w-full gap-0").style("padding: 16px 24px;")
                            )

                # 参数页状态与渲染（在控件创建后定义，初始调用一次）
                param_state: dict[str, Any] = {"collector": {}, "params": {}}

                def _render_params_panel() -> None:
                    """根据当前选中策略重新渲染参数页。"""
                    params_container.clear()
                    current_name: str = str(strategy_name_select.value or "")
                    tpl = strategy_name_map.get(current_name, {})
                    params: dict[str, Any] = tpl.get("default_params", {}) or {}
                    param_state["params"] = params
                    param_state["collector"] = _render_params_container(
                        params_container, params, None,
                        input_bg=input_bg, font_color=font_color,
                        title_color=title_color, muted_color=muted_color,
                    )

                _render_params_panel()

                # ---- 底部按钮区 ----
                with ui.row().classes("w-full items-center justify-end gap-3").style(
                    f"padding: 16px 24px; border-top: 1px solid {border_color};"
                ):
                    ui.button("取消", on_click=dialog.close).props("flat no-caps").classes(
                        "text-sm"
                    ).style(f"color: {muted_color};")

                    def _save() -> None:
                        """保存策略：创建用户关联（策略模板已存在）。"""
                        name_value: str = str(strategy_name_select.value or "").strip()
                        if not name_value:
                            ui.notify("请选择策略名称", type="warning")
                            return
                        tpl = strategy_name_map.get(name_value, {})
                        strategy_id: str = str(tpl.get("_id", "") or tpl.get("id", ""))
                        if not strategy_id:
                            ui.notify("所选策略模板无效", type="warning")
                            return
                        pool_name: str = str(pool_name_select.value or "").strip()
                        if not pool_name:
                            ui.notify("请选择股票池", type="warning")
                            return
                        pool_dao = pool_name_map.get(pool_name, {})
                        pool_id: str = str(pool_dao.get("_id", "") or pool_dao.get("id", ""))
                        if capital_input and capital_input.value and capital_input.value <= 0.0:
                            ui.notify("请输入初始模拟资金")
                            return
                        
                        try:
                            AppContext().user_strategy_api.create(
                                strategy_id=strategy_id,
                                pool_id=pool_id,
                                status="stopped",
                                initial_amount=float(capital_input.value or 0.0),
                                max_stock_count=int(max_stock_count_input.value or 0),
                                strategy_params=_collect_params(
                                    param_state.get("collector", {})
                                ),
                            )
                        except Exception as exc:
                            logger.exception("创建策略失败")
                            ui.notify(f"创建策略失败: {exc}", type="negative")
                            return
                        dialog.close()
                        ui.notify("创建成功", type="positive")
                        on_saved()

                    ui.button("保存", on_click=_save).props("unelevated no-caps").classes(
                        "text-sm font-medium"
                    ).style(
                        f"background-color: {primary_color}; color: #ffffff; "
                        "border-radius: 8px; padding: 8px 20px;"
                    )

        dialog.open()

    # ---- 编辑策略对话框 ----

    def _open_edit_strategy_dialog(row: dict[str, Any], on_saved: Callable[[], None]) -> None:
        """打开编辑策略对话框：可选择股票池、修改初始资金与最大持仓数量。

        :param row: 表格行数据（含 us_id/pool_id/initial_amount/max_stock_count）
        :param on_saved: 保存成功后的回调（用于刷新表格）
        """
        field_label_style = f"font-size: 0.875rem; font-weight: 500; color: {field_label_color};"
        input_style = f"flex: 1; background-color: {input_bg}; color: {font_color}; width: 100%;"

        # 加载股票池列表
        all_pools_list: list[dict[str, Any]] = []
        try:
            all_pools_list = AppContext().pool_api.list()
        except Exception:
            logger.exception("查询股票池失败")
        pool_name_map: dict[str, dict[str, Any]] = {
            str(s.get("name", "")): s for s in all_pools_list
            if s.get("name", "")
        }
        pool_name_options: list[str] = list(pool_name_map.keys())

        # 当前选中的股票池名（根据行的 pool_id 反查）
        current_pool_name: str = ""
        current_pool_id: str = str(row.get("pool_id", "") or "")
        for pname, pdao in pool_name_map.items():
            if str(pdao.get("_id", "") or pdao.get("id", "")) == current_pool_id:
                current_pool_name = pname
                break

        # 加载策略模板的默认参数与已保存的参数
        default_params: dict[str, Any] = {}
        try:
            tpl: dict[str, Any] = AppContext().strategy_api.get_by_id(row["sid"])
            default_params = tpl.get("default_params", {}) or {}
        except Exception:
            logger.warning("查询策略模板失败: %s", row["sid"], exc_info=True)
        saved_params: dict[str, Any] = row.get("strategy_params", {}) or {}

        dialog = ui.dialog()
        with dialog:
            card = ui.card().classes("w-[600px] max-w-[95vw] gap-0").style(
                f"background-color: {card_bg}; border: 1px solid {border_color}; "
                f"border-radius: 12px; padding: 0; "
                f"display: flex; flex-direction: column;"
            )
            with card:
                # ---- 头部 ----
                with ui.row().classes("w-full items-center justify-between").style(
                    f"padding: 16px 24px; border-bottom: 1px solid {border_color};"
                ):
                    ui.label(f"编辑策略 — {row['name']}").classes(
                        "text-xl font-bold"
                    ).style(f"color: {title_color};")
                    ui.button(icon="close", on_click=dialog.close).props(
                        "flat round dense"
                    ).style(f"color: {muted_color};")

                # ---- 标签页（策略 / 参数） ----
                with ui.tabs().classes("w-full strategy-dialog-tabs").props("dense") as edit_tabs:
                    ui.tab("策略").classes("text-sm")
                    ui.tab("参数").classes("text-sm")
                with ui.tab_panels(edit_tabs, value="策略").classes("w-full flex-1").style(f"background-color: {card_bg};"):
                    # ---- 策略 tab：股票池 + 资金配置 ----
                    with ui.tab_panel("策略").classes("p-0"):
                        with ui.scroll_area().classes("w-full").style("max-height: 48vh;"):
                            with ui.column().classes("w-full gap-3").style("padding: 20px 24px;"):
                                # 股票池
                                with ui.row().classes("w-full gap-1 items-center"):
                                    ui.label("* 股票池").classes("w-[20%] text-xs").style(
                                        field_label_style,
                                    )
                                    pool_select: ui.select = (
                                        ui.select(
                                            options=pool_name_options,
                                            value=current_pool_name or None,
                                        )
                                        .props("outlined dense").style(
                                            "flex: 1; min-width: 100px;"
                                        )
                                    )
                                # 初始资金
                                with ui.row().classes("w-full gap-1 items-center"):
                                    ui.label("初始资金 (元)").classes("w-[20%] text-xs").style(
                                        field_label_style,
                                    )
                                    capital_input: ui.number = ui.number(
                                        value=float(row.get("initial_amount", 0.0) or 0.0),
                                        format="%.0f",
                                    ).classes("w-[30%]").props("outlined dense").style(input_style)
                                # 最大持仓数量
                                with ui.row().classes("w-full gap-1 items-center"):
                                    ui.label("最大持仓数").classes("w-[20%] text-xs").style(
                                        field_label_style,
                                    )
                                    max_stock_count_input: ui.number = ui.number(
                                        value=int(row.get("max_stock_count", 0) or 0),
                                        format="%d",
                                    ).classes("w-[30%]").props("outlined dense").style(input_style)

                    # ---- 参数 tab：策略运行参数 ----
                    with ui.tab_panel("参数").classes("p-0"):
                        with ui.scroll_area().classes("w-full").style("max-height: 48vh;"):
                            params_container: ui.column = (
                                ui.column().classes("w-full gap-0").style("padding: 16px 24px;")
                            )

                # 参数页状态与渲染（在控件创建后定义并渲染一次）
                param_state: dict[str, Any] = {"collector": {}}
                param_state["collector"] = _render_params_container(
                    params_container, default_params, saved_params,
                    input_bg=input_bg, font_color=font_color,
                    title_color=title_color, muted_color=muted_color,
                )

                # ---- 底部按钮区 ----
                with ui.row().classes("w-full items-center justify-end gap-3").style(
                    f"padding: 16px 24px; border-top: 1px solid {border_color};"
                ):
                    ui.button("取消", on_click=dialog.close).props("flat no-caps").classes(
                        "text-sm"
                    ).style(f"color: {muted_color};")

                    def _save() -> None:
                        """保存修改的用户策略关联并刷新列表。"""
                        pool_name: str = str(pool_select.value or "").strip()
                        if not pool_name:
                            ui.notify("请选择股票池", type="warning")
                            return
                        pool_dao = pool_name_map.get(pool_name, {})
                        pool_id: str = str(
                            pool_dao.get("_id", "") or pool_dao.get("id", "")
                        )
                        try:
                            AppContext().user_strategy_api.update(
                                user_strategy_id=row["us_id"],
                                pool_id=pool_id,
                                initial_amount=float(capital_input.value or 0.0),
                                max_stock_count=int(max_stock_count_input.value or 0),
                                strategy_params=_collect_params(
                                    param_state.get("collector", {})
                                ),
                            )
                        except Exception as exc:
                            logger.exception("编辑策略失败")
                            ui.notify(f"编辑策略失败: {exc}", type="negative")
                            return
                        dialog.close()
                        ui.notify("保存成功", type="positive")
                        on_saved()

                    ui.button("保存", on_click=_save).props("unelevated no-caps").classes(
                        "text-sm font-medium"
                    ).style(
                        f"background-color: {primary_color}; color: #ffffff; "
                        "border-radius: 8px; padding: 8px 20px;"
                    )

        dialog.open()

    # 初始渲染表格
    _refresh()
