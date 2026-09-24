#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-07-11
LastEditors: liguoqiang
LastEditTime: 2026-07-11
Description: 黑名单管理页面 — 严格按 settings-blacklist.html 原型实现
"""
from __future__ import annotations

import logging
from typing import Any

from nicegui import app, ui

from api.client import ApiError
from app_context import AppContext
from core.holdings import lookup_stock_name

logger = logging.getLogger(__name__)


def show_blacklist_page() -> None:
    """黑名单页面入口（作为独立页面，有完整顶栏）。"""
    _render_blacklist_content(is_dialog=False)


def show_blacklist_dialog() -> None:
    """黑名单对话框入口。"""
    _render_blacklist_content(is_dialog=True)


def _render_blacklist_content(is_dialog: bool) -> None:
    """渲染黑名单内容。

    :param is_dialog: 是否在对话框中显示
    """
    theme = AppContext().theme_manager
    font_color: str = theme.var("text")
    border_color: str = theme.var("border")
    card_bg: str = theme.var("surface")
    input_bg: str = theme.var("surface-alt")
    bg_color: str = theme.var("page")

    state: dict[str, Any] = {"blacklist": [], "name_map": {}}
    current_user: str = str(app.storage.user.get("username", "") or "")

    def reload() -> None:
        """重新加载黑名单并解析股票名称。"""
        try:
            state["blacklist"] = AppContext().blacklist_api.list()
        except Exception:  # noqa: BLE001
            state["blacklist"] = []
        codes: list[str] = [
            str(b.get("code", "")) for b in state["blacklist"] if b.get("code")
        ]
        name_map: dict[str, str] = {}
        if codes:
            try:
                infos = AppContext().stock_info_api.get_by_codes(",".join(codes))
                for info in infos:
                    code: str = str(info.get("code", "") or "")
                    if code:
                        name_map[code] = str(info.get("name", "") or "")
            except Exception:  # noqa: BLE001, S110
                pass
        state["name_map"] = name_map

    def on_add(codes: list[str], reason: str = "") -> None:
        """添加股票。"""
        try:
            AppContext().blacklist_api.add(codes, reason)
            ui.notify(f"已添加 {len(codes)} 只股票到黑名单", type="positive")
            reload()
            table_refresh()
        except ApiError as e:
            ui.notify(f"添加失败: {e.message}", type="negative")
        except Exception as e:  # noqa: BLE001
            ui.notify(f"网络错误: {e!s}", type="negative")

    def on_remove(code: str) -> None:
        """移除股票。"""
        try:
            AppContext().blacklist_api.remove([code])
            ui.notify(f"已从黑名单移除 {code}", type="positive")
            reload()
            table_refresh()
        except ApiError as e:
            ui.notify(f"移除失败: {e.message}", type="negative")
        except Exception as e:  # noqa: BLE001
            ui.notify(f"网络错误: {e!s}", type="negative")

    def table_refresh() -> None:
        """刷新表格内容。"""
        table_container.clear()
        with table_container:
            _build_blacklist_table(
                state["blacklist"], on_remove, input_bg, border_color,
                name_map=state["name_map"], current_user=current_user,
            )

    reload()

    # 构建内容
    def render_body(close_fn: Any = None) -> None:
        """渲染主体内容。"""
        # 顶栏
        with ui.row().classes("w-full items-center justify-between").style(
            f"height: 64px; padding: 0 32px; box-sizing: border-box; background-color: {card_bg}; "
            f"border-bottom: 1px solid {border_color};"
        ):
            ui.label("黑名单股票管理").classes("text-xl font-bold").style("color: var(--qs-heading);")
            with ui.row().classes("items-center gap-4"):
                ui.button(icon="add", text="添加股票", on_click=lambda: _build_add_dialog(
                    on_add, card_bg, border_color, font_color, input_bg
                )).props("flat").style(
                    "background-color: var(--qs-primary); color: var(--qs-on-accent) !important; padding: 8px 16px; "
                    "border-radius: 8px; font-size: 14px; font-weight: 500;"
                )
                if close_fn:
                    ui.button(icon="close", on_click=close_fn).props("flat round dense").style("color: var(--qs-muted);")

        # 提示横幅
        with ui.row().classes("w-full items-start gap-3").style(
            "margin: 24px 32px 0; width: calc(100% - 64px); box-sizing: border-box; "
            "padding: 16px; background-color: rgba(var(--qs-primary-rgb), 0.15); "
            "border: 1px solid var(--qs-info-border); border-radius: 12px;"
        ):
            ui.icon("info").style("color: var(--qs-link); font-size: 20px; margin-top: 2px;")
            with ui.column().classes("gap-0"):
                ui.label("提示：黑名单逻辑生效于所有策略").classes("font-bold").style("color: var(--qs-info); font-size: 14px;")
                ui.label("列入黑名单的股票将不会出现在任何自动筛选的股票池中，且策略信号触发时会自动过滤掉这些标的。") \
                    .classes("text-sm").style("color: var(--qs-info);")

        # 搜索 + 表格
        with ui.column().classes("w-full gap-0").style(
            "padding: 24px 32px; box-sizing: border-box;"
        ):
            ui.input(placeholder="搜索已拉黑股票...") \
                .props("outlined dense") \
                .style(f"width: 256px; background-color: {input_bg}; margin-bottom: 16px;")

            nonlocal table_container
            with ui.card().classes("gap-0 w-full").style(
                f"background-color: {card_bg}; border: 1px solid {border_color}; border-radius: 12px; "
                f"overflow: hidden;"
            ):
                table_container = ui.column().classes("w-full gap-0")
                with table_container:
                    _build_blacklist_table(
                        state["blacklist"], on_remove, input_bg, border_color,
                        name_map=state["name_map"], current_user=current_user,
                    )

    if is_dialog:
        with ui.dialog(value=True).props("persistent") as _dialog, \
            ui.card().classes("gap-0").style(
                f"background-color: {card_bg}; width: 900px; max-width: 95vw; max-height: 85vh; "
                f"border: 1px solid {border_color}; border-radius: 12px; padding: 0; "
                f"overflow-y: auto; overflow-x: hidden; box-sizing: border-box;"
            ):
            table_container = ui.column()
            render_body(close_fn=_dialog.close)
    else:
        with ui.column().classes("w-full gap-0").style(
            f"background-color: {bg_color}; flex: 1; overflow-y: auto; overflow-x: hidden;"
        ):
            table_container = ui.column()
            render_body()


def _build_blacklist_table(
    blacklist: list[dict[str, Any]],
    on_remove: Any,
    input_bg: str,
    border_color: str,
    name_map: dict[str, str] | None = None,
    current_user: str = "",
) -> None:
    """构建黑名单表格（字段对齐 BlacklistDao: code / add_time / reason）。

    :param name_map: 股票代码 -> 股票名称 映射
    :param current_user: 当前登录用户账号（黑名单接口按当前用户过滤时展示用）
    """
    name_map = name_map or {}
    # 列宽（表头与数据行保持一致，允许收缩，不出现横向滚动条）：
    # 代码/名称/时间/原因/用户自适应，操作固定
    col_specs: list[tuple[str, str]] = [
        ("1 1 100px", "股票代码"),
        ("1 1 130px", "股票名称"),
        ("0 1 170px", "拉黑时间"),
        ("2 2 220px", "拉黑原因"),
        ("1 1 120px", "用户"),
        ("0 0 96px", "操作"),
    ]

    def cell_style(flex_style: str, color: str = "var(--qs-muted)") -> str:
        """生成单元格样式：列宽 + 单行省略。"""
        return (
            f"color: {color}; flex: {flex_style}; min-width: 0; "
            "overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
        )

    # 表头
    with ui.row().classes("w-full items-center").style(
        f"background-color: {input_bg}; border-bottom: 1px solid {border_color}; "
        f"padding: 12px 24px; gap: 0;"
    ):
        for flex_style, label in col_specs:
            align_style: str = "text-align: center;" if label == "操作" else ""
            ui.label(label).classes("text-xs font-semibold uppercase tracking-wider").style(
                cell_style(flex_style) + align_style
            )

    if not blacklist:
        with ui.column().classes("w-full items-center justify-center").style("padding: 60px;"):
            ui.icon("shield").style("color: var(--qs-faint); font-size: 48px;")
            ui.label("黑名单为空").style("color: var(--qs-muted); margin-top: 12px;")
        return

    for item in blacklist:
        code: str = str(item.get("code", ""))
        stock_name: str = _lookup_stock_name(code, name_map)
        add_time: str = str(item.get("add_time", "") or "")
        if add_time:
            add_time = add_time[:16]
        reason: str = str(item.get("reason", "") or "--")
        # 优先取记录中的用户信息；接口按当前用户过滤时展示当前登录账号
        user_display: str = str(
            item.get("user_name", "")
            or item.get("username", "")
            or item.get("user_id", "")
            or ""
        ) or current_user or "--"

        with ui.row().classes("w-full items-center").style(
            "padding: 12px 24px; border-bottom: 1px solid var(--qs-divider); gap: 0;"
        ):
            # 股票代码
            ui.label(code).classes("font-bold tracking-wider").style(
                cell_style("1 1 100px", "var(--qs-text)")
            )
            # 股票名称
            ui.label(stock_name or "--").classes("text-sm font-medium").style(
                cell_style("1 1 130px", "var(--qs-label)")
            )
            # 拉黑时间
            ui.label(add_time or "--").classes("text-sm").style(
                cell_style("0 1 170px", "var(--qs-faint)")
            )
            # 拉黑原因
            ui.label(reason).classes("text-sm italic").style(
                cell_style("2 2 220px", "var(--qs-muted)")
            )
            # 用户（谁拉黑的）
            ui.label(user_display).classes("text-sm").style(
                cell_style("1 1 120px", "var(--qs-info)")
            )
            # 操作按钮
            ui.button("移除名单", on_click=lambda c=code: on_remove(c)).props("flat dense").style(
                "color: var(--qs-positive); font-size: 12px; font-weight: 500; flex: 0 0 96px; "
                "box-sizing: border-box; margin: 0 auto; text-align: center; "
                "border: 1px solid var(--qs-danger-border); border-radius: 8px; padding: 4px 8px;"
            )


def _lookup_stock_name(code: str, name_map: dict[str, str]) -> str:
    """从代码 -> 名称映射中查找股票名称，兼容带交易所后缀的代码。

    实现已提到 ``core.holdings.lookup_stock_name``（信号日志表也用它按代码
    取名称），这里只做转发，避免两处各留一份比对规则后漂移。
    """
    return lookup_stock_name(code, name_map)


def _build_add_dialog(
    on_submit: Any,
    card_bg: str,
    border_color: str,
    font_color: str,
    input_bg: str,
) -> None:
    """构建添加黑名单对话框。"""
    with ui.dialog(value=True).props("persistent") as d, \
        ui.card().classes("gap-0").style(
            f"background-color: {card_bg}; width: 480px; max-width: 90vw; "
            f"border: 1px solid {border_color}; border-radius: 12px; padding: 0;"
        ):
        with ui.row().classes("w-full items-center gap-2").style(
            f"padding: 16px 20px; border-bottom: 1px solid {border_color};"
        ):
            ui.icon("add_circle").style("color: var(--qs-link); font-size: 20px;")
            ui.label("添加股票到黑名单").classes("text-lg font-bold").style("color: var(--qs-heading);")
            ui.space()
            ui.button(icon="close", on_click=d.close).props("flat round dense").style("color: var(--qs-muted);")

        with ui.column().classes("w-full gap-3").style("padding: 20px;"):
            ui.label("输入股票代码，多个用逗号分隔。黑名单将在所有策略中自动生效。").classes("text-xs").style("color: var(--qs-muted);")
            ui.label("股票代码").style("font-size: 0.875rem; font-weight: 500; color: var(--qs-label);")
            codes_input = (
                ui.textarea(placeholder="例如: 000002,600000,002460")
                .props("outlined dense autofocus")
                .classes("w-full")
                .style(f"color: {font_color}; background-color: {input_bg}; min-height: 80px;")
            )
            ui.label("拉黑原因").style("font-size: 0.875rem; font-weight: 500; color: var(--qs-label);")
            reason_input = (
                ui.input(placeholder="可选，如：财务造假、ST风险等")
                .props("outlined dense")
                .classes("w-full")
                .style(f"color: {font_color}; background-color: {input_bg};")
            )

        with ui.row().classes("w-full items-center justify-end gap-2").style(
            f"padding: 12px 20px; border-top: 1px solid {border_color};"
        ):
            ui.button("取消", on_click=d.close).props("flat").style(
                "color: var(--qs-muted); border: 1px solid var(--qs-border); border-radius: 8px; padding: 6px 20px;"
            )

            def handle() -> None:
                """解析并提交。"""
                raw: str = (codes_input.value or "").strip()
                if not raw:
                    ui.notify("请输入股票代码", type="warning")
                    return
                codes: list[str] = [c.strip() for c in raw.replace("，", ",").split(",") if c.strip()]
                if not codes:
                    ui.notify("未识别到有效的股票代码", type="warning")
                    return
                reason: str = (reason_input.value or "").strip()
                on_submit(codes, reason)
                d.close()

            ui.button("确认添加", on_click=handle).props("flat").style(
                "background-color: var(--qs-primary); color: var(--qs-on-accent) !important; border-radius: 8px; padding: 6px 20px;"
            )

    d.open()
