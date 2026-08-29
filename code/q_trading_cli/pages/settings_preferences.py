#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-07-11
LastEditors: liguoqiang
LastEditTime: 2026-08-03
Description: 个人喜好设置页面 — 通过 /api/user/preference 接口查询和保存用户偏好
    字段: theme_mode / enable_wx_push / wx_push_url / enable_phone_text / phone
"""
from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

from app_context import AppContext

logger = logging.getLogger(__name__)


def _load_preferences() -> dict[str, Any]:
    """从 API 加载当前用户的偏好设置。

    :return: 偏好字典，加载失败返回空字典
    """
    try:
        return AppContext().preference_api.get()
    except Exception as e:  # noqa: BLE001
        logger.warning("加载用户偏好失败: %s", e)
        return {}


def _save_preferences(prefs: dict[str, Any]) -> bool:
    """通过 API 保存用户偏好设置。

    :param prefs: 偏好字典
    :return: 保存成功返回 True
    """
    try:
        AppContext().preference_api.update(prefs)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("保存用户偏好失败: %s", e)
        return False


def _bool_val(value: Any, default: bool = False) -> bool:
    """安全转 bool，兼容 API 返回的 Python bool 或 JSON 字符串。

    :param value: 待转换值
    :param default: 默认值
    :return: bool
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def show_preferences_page() -> None:
    """个人喜好页面入口（作为独立页面，有完整顶栏）。"""
    _render_preferences_content(is_dialog=False)


def show_preferences_dialog() -> None:
    """个人喜好对话框入口。"""
    _render_preferences_content(is_dialog=True)


def _render_preferences_content(is_dialog: bool) -> None:
    """渲染个人喜好设置内容。

    :param is_dialog: 是否在对话框中显示
    """
    current_theme: dict[str, str] = AppContext().theme_manager.get_current_theme()
    bg_color: str = current_theme.get("background", "#0f0f1a")
    font_color: str = current_theme.get("font_color", "#e5e7eb")
    border_color: str = "#334155"
    card_bg: str = "#1e293b"
    input_bg: str = "#111827"

    # 加载已有偏好
    saved_prefs: dict[str, Any] = _load_preferences()

    # 可变的绑定变量（列表包装以支持闭包内修改）
    state: dict[str, Any] = {
        "theme_mode": saved_prefs.get("theme_mode", "dark"),
        "enable_wx_push": _bool_val(saved_prefs.get("enable_wx_push", False)),
        "wx_push_url": saved_prefs.get("wx_push_url", ""),
        "enable_phone_text": _bool_val(saved_prefs.get("enable_phone_text", False)),
        "phone": saved_prefs.get("phone", ""),
    }

    def on_save() -> None:
        """保存更改到 API。"""
        prefs: dict[str, Any] = {
            "theme_mode": state["theme_mode"],
            "enable_wx_push": state["enable_wx_push"],
            "wx_push_url": state["wx_push_url"],
            "enable_phone_text": state["enable_phone_text"],
            "phone": state["phone"],
        }
        if _save_preferences(prefs):
            ui.notify("设置已保存", type="positive")
        else:
            ui.notify("保存失败，请稍后重试", type="negative")

    def on_toggle_dark() -> None:
        """切换深色模式并同步偏好。"""
        AppContext().theme_manager.toggle()
        state["theme_mode"] = "dark" if AppContext().theme_manager.is_dark() else "light"
        ui.notify(
            f"已切换到{'深色' if state['theme_mode'] == 'dark' else '浅色'}主题",
            type="positive",
        )

    def render_body(close_fn: Any = None) -> None:
        """渲染主体内容。"""
        # 顶栏
        with ui.row().classes("w-full items-center justify-between").style(
            f"height: 64px; padding: 0 32px; background-color: {card_bg}; "
            f"border-bottom: 1px solid {border_color};"
        ):
            ui.label("个人喜好设置").classes("text-xl font-bold").style("color: #f3f4f6;")
            with ui.row().classes("items-center gap-4"):
                ui.button("保存更改", on_click=on_save).props("flat").style(
                    "background-color: #3b82f6; color: #ffffff !important; padding: 8px 24px; "
                    "border-radius: 8px; font-size: 14px; font-weight: 500;"
                )
                if close_fn:
                    ui.button(icon="close", on_click=close_fn).props("flat round dense").style(
                        "color: #9ca3af;"
                    )

        with ui.column().classes("w-full gap-0").style(
            f"padding: 32px; background-color: {bg_color}; overflow-y: auto;"
        ).style("max-width: 900px;"):
            # ---- 区1: 界面显示 ----
            with ui.card().classes("w-full gap-0 overflow-hidden").style(
                f"background-color: {card_bg}; border: 1px solid {border_color}; "
                f"border-radius: 12px; margin-bottom: 32px;"
            ):
                with ui.row().classes("w-full").style(
                    f"padding: 24px; border-bottom: 1px solid {border_color};"
                ):
                    ui.label("界面显示").classes("font-bold").style("color: #f3f4f6;")

                with ui.column().classes("w-full gap-6").style("padding: 24px;"):  # noqa: SIM117
                    with ui.row().classes("w-full items-center justify-between"):
                        with ui.column().classes("gap-0"):
                            ui.label("深色模式").classes("text-sm font-semibold").style("color: #e5e7eb;")
                            ui.label("切换系统的显示主题风格").classes("text-xs").style("color: #6b7280;")
                        is_dark: bool = AppContext().theme_manager.is_dark()
                        ui.switch(value=is_dark, on_change=lambda _e: on_toggle_dark()).props(
                            "color=#3b82f6"
                        )

            # ---- 区2: 企业微信推送 ----
            with ui.card().classes("w-full gap-0 overflow-hidden").style(
                f"background-color: {card_bg}; border: 1px solid {border_color}; "
                f"border-radius: 12px; margin-bottom: 32px;"
            ):
                with ui.row().classes("w-full").style(
                    f"padding: 24px; border-bottom: 1px solid {border_color};"
                ):
                    ui.label("企业微信推送").classes("font-bold").style("color: #f3f4f6;")

                with ui.column().classes("w-full gap-6").style("padding: 24px;"):
                    with ui.row().classes("w-full items-center justify-between"):
                        with ui.column().classes("gap-0"):
                            ui.label("启用推送").classes("text-sm font-semibold").style("color: #e5e7eb;")
                            ui.label("当策略触发买入或卖出信号时，通过企业微信发送通知").classes(
                                "text-xs"
                            ).style("color: #6b7280;")
                        ui.switch(
                            value=state["enable_wx_push"],
                            on_change=lambda e: _update_wx_state(e.value),
                        ).props("color=#3b82f6")

                    # Webhook URL（仅在启用推送时显示）
                    url_container = ui.column().classes("w-full gap-2")
                    with url_container:
                        ui.label("Webhook 地址").classes("text-sm font-semibold").style("color: #e5e7eb;")
                        (
                            ui.input(
                                value=state["wx_push_url"],
                                placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...",
                                on_change=lambda e: state.update({"wx_push_url": e.value or ""}),
                            )
                            .props("outlined dense")
                            .classes("w-full")
                            .style(f"color: {font_color}; background-color: {input_bg};")
                        )
                    if not state["enable_wx_push"]:
                        url_container.set_visibility(False)

            # ---- 区3: 手机短信推送 ----
            with ui.card().classes("w-full gap-0 overflow-hidden").style(
                f"background-color: {card_bg}; border: 1px solid {border_color}; "
                f"border-radius: 12px;"
            ):
                with ui.row().classes("w-full").style(
                    f"padding: 24px; border-bottom: 1px solid {border_color};"
                ):
                    ui.label("手机短信推送").classes("font-bold").style("color: #f3f4f6;")

                with ui.column().classes("w-full gap-6").style("padding: 24px;"):
                    with ui.row().classes("w-full items-center justify-between"):
                        with ui.column().classes("gap-0"):
                            ui.label("启用短信推送").classes("text-sm font-semibold").style("color: #e5e7eb;")
                            ui.label("当策略信号触发时，向指定手机号发送短信通知").classes(
                                "text-xs"
                            ).style("color: #6b7280;")
                        ui.switch(
                            value=state["enable_phone_text"],
                            on_change=lambda e: _update_phone_state(e.value),
                        ).props("color=#3b82f6")

                    # 手机号（仅在启用短信时显示）
                    phone_container = ui.column().classes("w-full gap-2")
                    with phone_container:
                        ui.label("推送手机号").classes("text-sm font-semibold").style("color: #e5e7eb;")
                        (
                            ui.input(
                                value=state["phone"],
                                placeholder="请输入手机号",
                                on_change=lambda e: state.update({"phone": e.value or ""}),
                            )
                            .props("outlined dense")
                            .classes("w-full")
                            .style(f"color: {font_color}; background-color: {input_bg};")
                        )
                    if not state["enable_phone_text"]:
                        phone_container.set_visibility(False)

        def _update_wx_state(val: bool) -> None:
            """更新企业微信推送开关状态。"""
            state["enable_wx_push"] = val
            url_container.set_visibility(val)

        def _update_phone_state(val: bool) -> None:
            """更新短信推送开关状态。"""
            state["enable_phone_text"] = val
            phone_container.set_visibility(val)

    if is_dialog:
        with ui.dialog(value=True).props("persistent") as _dialog, ui.card().classes("gap-0").style(
            f"background-color: {bg_color}; width: 700px; max-width: 95vw; max-height: 85vh; "
            f"border: 1px solid {border_color}; border-radius: 12px; padding: 0; overflow-y: auto;"
        ):
            render_body(close_fn=_dialog.close)
    else:
        with ui.column().classes("w-full gap-0").style(
            f"background-color: {bg_color}; flex: 1; overflow-y: auto;"
        ):
            render_body()
