#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-09-15
Description: 应用路由表 —— 页面标识(key) 与 URL 路径的唯一来源。

本次修复背景（登录后 404）：
    pages/main_page.main_page_with_content() 在未登录时写入
    app.storage.user["referrer_path"] = f"/{page_key}"。
    但 page_key 是**页面标识**（如 "monitor"），并不是路由路径；
    登录页登录成功后跳回 referrer_path，于是浏览器请求 "/monitor"，
    真实路由却是 "/strategy-monitor" —— NiceGUI 报
    "https://example.com/monitor not found"，页面显示 404。
    除 "strategies" 恰好与路径同形外，pools / quotes / preferences /
    blacklist / strategy_select 全都存在同样的错位。

约定（重要）：
    新增页面时必须同时登记到 PAGE_ROUTES（页面标识 → 路径）。
    侧边栏导航、登录后回跳、安全中间件白名单都从本表派生，
    避免多处硬编码导致路由不一致。

    页面需要参数时（如策略回测记录页要带策略 ID、以及记录是从哪个入口进来的），
    一律走**查询参数** + 本模块的 URL 构造函数（例：backtest_route），不要用
    ``{id}`` 动态路径：白名单是精确匹配，动态路径会被限流中间件当成未知路径计数。
"""

from __future__ import annotations

from urllib.parse import urlencode

# 页面标识 → 路由路径（与 main.py 中 @ui.page 注册的路径保持一致）
PAGE_ROUTES: dict[str, str] = {
    "home": "/",
    "strategies": "/strategies",
    "monitor": "/strategy-monitor",
    "strategy_select": "/strategy-select",
    "backtest": "/strategy-backtest",
    "pools": "/stock-pools",
    "quotes": "/stock-quotes",
    "preferences": "/settings-preferences",
    "blacklist": "/settings-blacklist",
}

# 首页路径（未知页面标识的回退目标）
HOME_ROUTE: str = "/"

# 登录页路径（不属于 PAGE_ROUTES，但属于合法路由）
LOGIN_ROUTE: str = "/login"

# 全部合法应用路由：安全中间件白名单以此为准
ALL_ROUTES: frozenset[str] = frozenset(PAGE_ROUTES.values()) | {LOGIN_ROUTE}

# 可作为「登录后回跳目标」的路由：仅内容页面，不含登录页本身
REFERRER_ROUTES: frozenset[str] = frozenset(PAGE_ROUTES.values())


def route_for_page_key(page_key: str) -> str:
    """把页面标识转换为路由路径。

    :param page_key: 页面标识（如 "monitor"）
    :return: 对应的路由路径；未知标识回退首页 "/"
    """
    return PAGE_ROUTES.get(str(page_key or "").strip(), HOME_ROUTE)


def sanitize_referrer(path: str | None) -> str:
    """校验「登录后回跳路径」，只放行已登记的合法路由。

    为什么需要：AuthMiddleware 会把未登录时的 ``request.url.path`` 原样写入
    referrer_path。若用户访问的是不存在或已废弃的路径（例如历史版本前端
    错误写出的 "/monitor"、旧书签），登录后就会跳到一个 404 页面。

    :param path: 待校验的路径
    :return: 合法路径；否则回退首页 "/"
    """
    candidate: str = str(path or "").strip()
    if candidate in REFERRER_ROUTES:
        return candidate
    return HOME_ROUTE


# 回测记录页的查询参数名（策略模板 ID）
BACKTEST_STRATEGY_ID_PARAM: str = "strategy_id"

# 回测记录页的查询参数名（来源页面标识，决定左上角「返回」按钮的去向与文案）
BACKTEST_SOURCE_PARAM: str = "source"

# 允许作为回测记录页入口的页面标识。
# 目前是两个入口：策略监控卡片「详情回测」、策略列表操作列「回测记录」。
# 白名单式枚举，避免把 URL 里的任意字符串（甚至外链）当成返回目标。
BACKTEST_SOURCE_KEYS: tuple[str, ...] = ("monitor", "strategies")

# 来源缺失或非法时的兜底：策略监控是主要入口
DEFAULT_BACKTEST_SOURCE: str = "monitor"


def backtest_source(source: str) -> str:
    """归一化「回测记录页来源页面」标识。

    回测页的返回按钮要"从哪进就回哪"，因此来源必须来自白名单，
    不能直接信任 URL 上的字符串（否则可被构造成跳转到任意地址）。

    :param source: 入口传入的页面标识（如 "strategies"）
    :return: 合法的页面标识；缺失或非法时返回 DEFAULT_BACKTEST_SOURCE
    """
    key: str = str(source or "").strip()
    return key if key in BACKTEST_SOURCE_KEYS else DEFAULT_BACKTEST_SOURCE


def backtest_route(strategy_id: str, source: str = DEFAULT_BACKTEST_SOURCE) -> str:
    """构建「策略回测记录」页的 URL。

    策略模板 ID 走**查询参数**而不是路径参数，原因有二：
    1. 安全中间件对应用路由是**精确匹配**（middleware/security.py 的
       ``is_allowed_path`` 用 ``path in ALLOWED_ROUTES``）。带 ``{id}`` 的动态
       路径不在白名单里，会被计入 IP 限流窗口（60s / 30 次），正常用户多点几
       次就会被 429 误拦。
    2. ``AuthMiddleware`` 记录回跳目标时只存 ``request.url.path``，本来也留不住
       动态段；查询串同样留不住（未登录进详情页会落到空态，见页面里的处理）。

    :param strategy_id: 策略模板 ID（StrategyDao._id）；为空时不带该参数
    :param source: 来源页面标识（调用方各自传自己的 page_key，见 BACKTEST_SOURCE_KEYS）
    :return: 可直接交给 ui.navigate.to() 的路径
    """
    route: str = PAGE_ROUTES["backtest"]
    params: dict[str, str] = {}
    sid: str = str(strategy_id or "").strip()
    if sid:
        params[BACKTEST_STRATEGY_ID_PARAM] = sid
    # 来源**即使没有策略 ID 也要带上**：页面在「未指定策略」空态下同样要能正确返回，
    # 否则用户从策略列表误入一个缺 ID 的链接后，返回按钮会指回监控页。
    params[BACKTEST_SOURCE_PARAM] = backtest_source(source)
    return f"{route}?{urlencode(params)}"
