#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-07-12
Description: 统一 HTTP 错误处理 — 提供 handle_http_error() 函数，
             所有页面捕获 httpx.HTTPStatusError / UnauthorizedError 后调用此函数即可。

使用方式:
    from api.error_handler import handle_http_error
    from api.client import UnauthorizedError

    try:
        data = AppContext().some_api.get_xxx()
    except UnauthorizedError:
        return  # 已弹出确认框并跳转 /login，直接返回即可
    except httpx.HTTPStatusError as e:
        handle_http_error(e)
        return  # 已处理，不再向上抛出
"""
from __future__ import annotations

import logging
from collections.abc import Callable

import httpx
from nicegui import app, ui

from api.client import UnauthorizedError

logger = logging.getLogger(__name__)


# ---- 公开接口 ----

_on_401_callbacks: list[Callable[[], None]] = []


def on_401(callback: Callable[[], None]) -> None:
    """注册 401 事件回调（可用于触发登录对话框）。

    此函数可供框架/中间件层注册全局回调，
    业务代码应直接使用 handle_http_error()。

    :param callback: 401 发生时的回调函数
    """
    _on_401_callbacks.append(callback)


def handle_http_error(e: httpx.HTTPStatusError | UnauthorizedError) -> None:
    """统一处理 HTTP 错误，根据状态码给出友好提示。

    注意：如果传入的是 UnauthorizedError，说明 ApiClient 已自动弹出登录对话框，
    此函数仅记录日志，无需重复弹窗。

    后台任务中跳过 UI 通知（ui.notify），仅记录日志。

    :param e: httpx.HTTPStatusError 或 UnauthorizedError 异常
    """
    # UnauthorizedError 表示 ApiClient 已自动拦截 401 并弹出确认框跳转登录页
    if isinstance(e, UnauthorizedError):
        logger.warning("API 请求未授权 (401)，登录对话框已弹出")
        return

    status_code: int = e.response.status_code
    if status_code == 401:
        logger.warning("API 请求未授权 (401)，需要重新登录")
        _handle_401()
    elif status_code == 403:
        logger.warning(f"API 请求被禁止 (403): {e}")
        if _has_client_context():
            ui.notify("没有权限执行该操作", type="negative")
    elif status_code >= 500:
        logger.warning(f"服务器错误 ({status_code}): {e}")
        if _has_client_context():
            ui.notify(f"服务器错误 (HTTP {status_code})，请稍后重试", type="negative")
    else:
        logger.warning(f"HTTP 错误 ({status_code}): {e}")
        if _has_client_context():
            ui.notify(f"请求失败 (HTTP {status_code})", type="negative")


# ---- 内部实现 ----

def _handle_401() -> None:
    """处理 401 未授权错误：清除登录态，提示用户并引导至登录页。"""
    # 清除当前过期的认证状态
    _clear_auth_state()

    # 通知用户（仅浏览器上下文）
    if _has_client_context():
        ui.notify("登录已过期，请重新登录", type="warning")

    # 弹出确认对话框，用户确认后跳转登录页面
    show_relogin_confirm()


def _clear_auth_state() -> None:
    """清除用户认证状态。"""
    try:
        app.storage.user.update({
            "authenticated": False,
            "token": "",
            "username": "",
        })
    except Exception:
        logger.debug("清除认证状态失败", exc_info=True)


def _has_client_context() -> bool:
    """检查当前是否在 NiceGUI 客户端上下文中（非后台任务）。

    :return: True 表示有可用浏览器上下文，可安全调用 UI 函数
    """
    try:
        from nicegui import context
        client = context.get_client()
        return client is not None and not getattr(client, "_disconnected", False)
    except Exception:  # noqa: BLE001
        return False


def show_relogin_confirm() -> None:
    """弹出浏览器原生确认框提示用户需要登录，确认后跳转 /login 页面。

    仅在浏览器上下文中执行 UI 操作；后台任务中静默跳过。
    """
    if _has_client_context():
        try:
            ui.run_javascript("""
                if (confirm("登录已过期，请重新登录。\\n\\n点击「确定」前往登录页面。")) {
                    window.location.href = "/login";
                }
            """)
        except Exception:
            logger.debug("无法弹出重新登录确认框", exc_info=True)
            try:
                ui.navigate.to("/login")
            except Exception:  # noqa: BLE001, S110
                pass
    else:
        logger.info("401 发生在后台任务中，跳过 UI 弹窗")

    # 触发外部注册的 401 回调
    for cb in _on_401_callbacks:
        try:
            cb()
        except Exception:
            logger.debug("401 回调执行失败", exc_info=True)
