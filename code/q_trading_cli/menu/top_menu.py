#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-05-25 21:22:50
LastEditors: liguoqiang
LastEditTime: 2026-06-28
Description: 顶部菜单按钮 — 登录/注册、个人资料、退出登录
"""
from nicegui import app, ui

from app_context import AppContext


def _do_logout() -> None:
    """执行退出登录：调用后端 API 清除 token，清除本地会话，返回首页。"""
    try:
        AppContext().user_api.logout()
    except Exception:  # noqa: BLE001, S110
        pass
    # 仅清除实例 token（token 本体存于 app.storage.user，按会话隔离）
    try:
        AppContext().api_client.clear_token()
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        # 注意：只清当前会话。app.storage.general 是**进程级共享**存储，
        # 一个用户退出登录不应清空其他用户的数据，故不再 clear。
        app.storage.user.clear()
        app.storage.browser.clear()
        app.storage.client.clear()
        app.storage.user["authenticated"] = False
        app.storage.user["token"] = ""
    except Exception:  # noqa: BLE001, S110
        pass
    ui.navigate.to("/")


def _show_profile_dialog() -> None:
    """显示个人资料对话框。"""
    theme = AppContext().theme_manager
    font_color: str = theme.var("text")
    font_soft: str = theme.var("text-secondary")
    accent_color: str = theme.var("accent")
    border_color: str = theme.var("border")
    bg_color: str = theme.var("page")
    negative_color: str = theme.var("negative")

    username: str = app.storage.user.get("username", "")
    user_email: str = app.storage.user.get("email", "")
    user_phone: str = app.storage.user.get("phone", "")

    with ui.dialog(value=True).props("persistent") as dialog, \
        ui.card().classes("gap-0").style(
            f"background-color: {bg_color}; width: 380px; max-width: 90vw; "
            f"border: 1px solid {border_color}; border-radius: 12px; padding: 0;"
        ):
        # 标题栏
        with ui.row().classes("w-full items-center gap-2").style(
            f"padding: 16px 20px; border-bottom: 1px solid {border_color};"
        ):
            ui.icon("account_circle").style(f"color: {accent_color}; font-size: 20px;")
            ui.label("个人资料").classes("text-lg font-bold").style(
                f"color: {font_color};"
            )
            ui.space()
            ui.button(icon="close", on_click=dialog.close) \
                .props("flat round dense") \
                .style(f"color: {font_color};")

        # 内容区
        with ui.column().classes("w-full gap-3").style("padding: 24px 20px;"):
            # 头像占位 + 用户名
            with ui.row().classes("w-full items-center gap-3"):
                ui.icon("account_circle").style(
                    f"color: {accent_color}; font-size: 48px;"
                )
                with ui.column().classes("gap-0"):
                    ui.label(username or "未设置").classes("text-lg font-bold").style(
                        f"color: {font_color};"
                    )
                    ui.label("已登录").classes("text-xs").style(
                        f"color: {font_soft};"
                    )

            # 分隔线
            ui.separator().style(f"background-color: {border_color};")

            # 账号
            with ui.row().classes("w-full items-center gap-2"):
                ui.icon("person").style(f"color: {font_soft}; font-size: 18px;")
                ui.label("账号").classes("text-xs").style(f"color: {font_soft};")
                ui.space()
                ui.label(username or "未设置").classes("text-sm").style(f"color: {font_color};")

            # 手机号码
            phone_display: str
            if user_phone:
                phone_clean = user_phone.strip().replace(" ", "").replace("-", "")
                if len(phone_clean) >= 7:
                    phone_display = f"{phone_clean[:3]}****{phone_clean[-4:]}"
                else:
                    phone_display = user_phone
            else:
                phone_display = "未设置"
            with ui.row().classes("w-full items-center gap-2"):
                ui.icon("phone").style(f"color: {font_soft}; font-size: 18px;")
                ui.label("手机号码").classes("text-xs").style(f"color: {font_soft};")
                ui.space()
                ui.label(phone_display).classes("text-sm").style(f"color: {font_color};")

            # 邮箱
            with ui.row().classes("w-full items-center gap-2"):
                ui.icon("email").style(f"color: {font_soft}; font-size: 18px;")
                ui.label("邮箱").classes("text-xs").style(f"color: {font_soft};")
                ui.space()
                ui.label(user_email or "未设置").classes("text-sm").style(f"color: {font_color};")

        # 底部按钮
        with ui.row().classes("w-full items-center place-content-end gap-2").style(
            f"padding: 12px 20px; border-top: 1px solid {border_color};"
        ):
            ui.button("关闭", on_click=dialog.close) \
                .props("flat") \
                .style(
                    f"color: {font_soft}; background-color: transparent; "
                    f"border: 1px solid {border_color}; border-radius: 8px; "
                    f"padding: 6px 20px;"
                )

            def handle_logout() -> None:
                dialog.close()
                _do_logout()

            ui.button("退出登录", on_click=handle_logout) \
                .props("flat") \
                .style(
                    f"color: var(--qs-on-accent); background-color: {negative_color}; "
                    f"border-radius: 8px; padding: 6px 20px;"
                )

    dialog.open()


def top_menu() -> None:
    """渲染顶部菜单按钮。

    根据登录状态显示不同菜单项：
    - 未登录：登录/注册
    - 已登录：个人资料、退出登录
    """
    theme = AppContext().theme_manager
    bg_color: str = theme.var("page")
    border_color: str = theme.var("border")
    font_color: str = theme.var("text")

    authenticated: bool = app.storage.user.get("authenticated", False)

    with ui.row().classes("w-full items-end gap-0"):  # noqa: SIM117
        with ui.button(icon="menu").props("outline flat") \
            .classes("w-full gap-0 justify-self-center") \
            .style(f"color: {font_color} !important; font-size:16px;"):
            with ui.menu() \
                .style(
                    f"width: 130px; min-width: 130px; "
                    f"background-color: {bg_color}; "
                    f"border: 1px solid {border_color} !important; "
                    f"color: {font_color} !important; font-size: 14px;"
                ):
                if authenticated:
                    ui.menu_item("个人资料", on_click=_show_profile_dialog)
                    ui.separator()
                    ui.menu_item("退出登录", on_click=_do_logout)
                else:
                    ui.menu_item("登录 / 注册", on_click=lambda: ui.navigate.to("/login"))
