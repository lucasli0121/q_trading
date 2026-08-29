"""
Author: liguoqiang
Date: 2026-07-12
LastEditors: liguoqiang
LastEditTime: 2026-08-11
Description: 统一 HTTP 错误处理 — 提供 handle_http_error() 函数，
             所有模块捕获 httpx.HTTPStatusError / UnauthorizedError 后调用此函数即可。
             后台服务场景下仅记录日志，无 UI 交互。

使用方式:
    from api.error_handler import handle_http_error, on_401
    from api.client import UnauthorizedError

    try:
        data = AppContext().some_api.get_xxx()
    except UnauthorizedError:
        return  # token 已清除，直接返回即可
    except httpx.HTTPStatusError as e:
        handle_http_error(e)
        return  # 已处理，不再向上抛出
"""
from __future__ import annotations

import logging
from typing import Callable

import httpx

from api.client import UnauthorizedError

logger: logging.Logger = logging.getLogger(__name__)


# ---- 401 回调机制 ----

_on_401_callbacks: list[Callable[[], None]] = []


def on_401(callback: Callable[[], None]) -> None:
    """注册 401 事件回调。

    此函数可供框架/中间件层注册全局回调，
    当 ApiClient 检测到 401 时自动触发所有已注册的回调。

    :param callback: 401 发生时的回调函数
    """
    _on_401_callbacks.append(callback)


# ---- 公开接口 ----


def handle_http_error(e: httpx.HTTPStatusError | UnauthorizedError) -> None:
    """统一处理 HTTP 错误，根据状态码记录日志。

    后台服务场景下不进行 UI 交互，仅按严重程度记录日志。

    :param e: httpx.HTTPStatusError 或 UnauthorizedError 异常
    """
    # UnauthorizedError 表示 ApiClient 已自动拦截 401 并清除 token
    if isinstance(e, UnauthorizedError):
        logger.warning("API 请求未授权 (401)，token 已清除")
        return

    status_code: int = e.response.status_code
    if status_code == 401:
        logger.warning("API 请求未授权 (401)，需要重新认证")
    elif status_code == 403:
        logger.warning("API 请求被禁止 (403): %s", e)
    elif status_code >= 500:
        logger.error("服务器错误 (HTTP %d): %s", status_code, e)
    else:
        logger.warning("HTTP 错误 (HTTP %d): %s", status_code, e)
