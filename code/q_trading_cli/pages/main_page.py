#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2025-03-16 15:26:37
LastEditors: liguoqiang
LastEditTime: 2026-08-25
Description: 主页面 — 严格按 home.html 原型实现仪表盘布局
    - 左侧分组导航抽屉（策略管理 / 股票池管理 / 系统设置）
    - 顶部标题栏
    - 统计卡片（策略总数、运行中策略、股票池数量、今日信号数）
      今日信号数：打开页面时先查询数据库初始化，之后接收 MQ 消息实时累加
    - 系统累计收益概览图表（系统总收益曲线 = 各用户策略收益率之和，
      数据点走 executions/range 按窗口查询；右上角下拉框可叠加一条单策略对比曲线）
      + 近期运行状态
    - 近期更新策略表格

策略名称口径（两处展示共用）：一律显示**用户策略名称** ``user_strategy.name``，
    回退链为 用户策略名 → 策略模板名 → 策略 ID。信号里的 ``strategy_id`` 是
    **策略模板 ID**，同一模板建多个用户策略时会显示成同一个名字，必须按
    ``user_strategy_id`` 归并（2026-09-23 用户定）。

收益率口径（两处展示共用 core.holdings，改动前先读该模块头部）：
    执行记录里的 current_return_rate 只含**已实现**盈利（后端 manager.py:
    current_profit / initial_amount），直接展示会漏掉持仓浮盈。所以：
      · 收益曲线：每个点 = 各用户策略收益率之和 —— 单个策略当天的取值是它当天
        全部记录 current_return_rate 之和，当天没交易的策略沿用最近一次的取值
        （只含已实现）；只有代表「当前」的末点覆盖成各策略「含浮盈」收益率之和
        —— 历史持仓的实时价无法回溯；
      · 近期更新策略表：每个策略取最新一条执行记录，同样叠加未实现。
    未实现盈亏一律由实时价与成本价现算，不取持仓里的 profit_rate / profit_amount
    （成交时点快照）。算不出（缺实时价 / 缺字段）就显示 "-"，不退回已实现口径凑数。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

from nicegui import app, ui

from app_context import AppContext
from colors.theme import resolve_mode_from_prefs
from components import custom_tabs
from core.concurrency import PollingTask, run_blocking
from core.holdings import (
    collect_position_codes,
    current_rate_pct_with_fallback,
    average_current_rate_pct,
    current_return_rate_pct,
    daily_average_rates,
    execution_day,
    fetch_price_map,
    last_execution_day,
    latest_execution_map,
    strategy_levels_before,
    strategy_levels_on_days,
    unrealized_profit,
)
from core.routes import route_for_page_key
from core.user_strategy_manager import UserStrategyManager
from menu.top_menu import _do_logout, _show_profile_dialog

logger = logging.getLogger(__name__)

# "近期运行状态"卡片展示的条数（全局最近 N 条）
ACTIVITY_COUNT: int = 4

# 概要页实时价缓存：累计收益图的末点与近期更新策略表共用一份，
# 避免 60 秒轮询与切换周期各打一次行情接口。口径见 core.holdings。
_PRICE_TTL_SECONDS: float = 30.0
_price_state: dict[str, Any] = {"key": "", "at": 0.0, "map": {}}

# 概要页全量执行记录缓存：曲线窗口基准 / 曲线末点 / 近期更新策略表共用。
_EXEC_TTL_SECONDS: float = 30.0
_exec_state: dict[str, Any] = {"at": 0.0, "data": []}

# 时间范围查询的分页上限。后端上限 10000、默认只有 500 —— 画一年曲线时
# 多策略每天多条记录很容易超过 500，超了会被静默截断（曲线少了后半段却不报错）。
_RANGE_LIMIT: int = 10000


def _cached_price_map(codes: set[str]) -> dict[str, float]:
    """带 TTL 的批量实时价查询（概要页共用）。

    命中条件：代码集合与上次请求一致且未超过 TTL。代码集合变化（持仓变动）
    时立即重查；查询失败返回空映射，且**照常推进时间戳**，避免轮询下反复重试。

    :param codes: 需要查询的股票代码集合
    :return: {股票代码: 实时价}；无代码或查询失败时为空映射
    """
    if not codes:
        return {}
    key: str = ",".join(sorted(codes))
    now: float = time.monotonic()
    if _price_state["key"] == key and now - float(_price_state["at"]) < _PRICE_TTL_SECONDS:
        return dict(_price_state["map"])
    price_map: dict[str, float] = fetch_price_map(codes)
    _price_state["key"] = key
    _price_state["at"] = now
    _price_state["map"] = price_map
    return dict(price_map)


def _cached_all_executions() -> list[dict[str, Any]]:
    """带 TTL 的**全量**执行记录（概要页共用）。

    用于三处：① 近期更新策略表取「每策略最新一条」；② 收益曲线确定窗口基准
    （最后一个数据点）；③ 曲线末点算各策略含浮盈收益率的平均。
    曲线的**数据点**另走 ``executions/range`` 按窗口查询，不走这里。

    ⚠ 末点必须基于全量：按时间范围查会漏掉窗口外长期未更新的策略，让平均值的
    分母随窗口大小变化，曲线末点会莫名跳变。

    失败时沿用上一批数据并**照常推进时间戳**，避免轮询下反复重试打爆后端。

    :return: 全部执行记录（浅拷贝的列表）
    """
    now: float = time.monotonic()
    if _exec_state["data"] and now - float(_exec_state["at"]) < _EXEC_TTL_SECONDS:
        return list(_exec_state["data"])
    try:
        data: list[dict[str, Any]] = AppContext().user_strategy_api.list_executions()
    except Exception:
        logger.warning("查询策略执行结果失败", exc_info=True)
        _exec_state["at"] = now
        return list(_exec_state["data"])
    _exec_state["at"] = now
    _exec_state["data"] = data
    return list(data)


def _window_bounds(last_day: str, days_limit: int) -> tuple[str, str]:
    """以**最后一个数据点**为基准算出查询窗口（闭区间的起止时间戳）。

    窗口 = ``[last_day − days_limit 天, last_day]``，与旧的本地日期过滤边界一致，
    所以「7天」实际覆盖 8 个日历日。基准取最后数据点而不是今天：数据不一定每天都有
    （策略没交易就没新记录），以今天为基准时久未交易的策略会画出空图。

    :param last_day: 最后一个数据点的日期 YYYY-MM-DD
    :param days_limit: 周期天数
    :return: (start_time, end_time)，格式 ``YYYY-MM-DD HH:MM:SS``；
             last_day 无法解析时返回两个空串（查询表示不限时间范围）
    """
    if not last_day:
        return "", ""
    try:
        end: datetime = datetime.strptime(last_day, "%Y-%m-%d")  # noqa: DTZ007
    except ValueError:
        return "", ""
    start: datetime = end - timedelta(days=days_limit)
    return (
        start.strftime("%Y-%m-%d 00:00:00"),
        end.strftime("%Y-%m-%d 23:59:59"),
    )


def _query_window_executions(
    start_time: str,
    end_time: str,
    all_executions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按时间范围查询窗口内的执行记录（服务端过滤）。

    失败时**回退到本地过滤**全量结果：全量已经在手上，回退结果与服务端过滤等价，
    曲线不会因为新接口故障而空白。

    :param start_time: 起始时间戳，空串表示不限
    :param end_time: 结束时间戳，空串表示不限
    :param all_executions: 已拉取的全量记录（回退用）
    :return: 窗口内的执行记录
    """
    try:
        return AppContext().user_strategy_api.list_executions_by_range(
            start_time=start_time, end_time=end_time, limit=_RANGE_LIMIT,
        )
    except Exception:
        logger.warning("按时间范围查询执行结果失败，回退本地过滤", exc_info=True)
    start_day: str = start_time[:10]
    end_day: str = end_time[:10]
    return [
        ex for ex in all_executions
        if (not start_day or execution_day(ex) >= start_day)
        and (not end_day or execution_day(ex) <= end_day)
    ]


def _strategy_current_rate_pct(
    executions: list[dict[str, Any]],
    us_id: str,
    price_map: dict[str, float],
) -> float | None:
    """所选策略最新记录的「含浮动盈亏」当前收益率（百分数），用于对比曲线末点。

    :param executions: 全部执行记录（用全量：该策略可能在本周期内没有新记录）
    :param us_id: 用户策略 ID；空表示未选策略
    :param price_map: {股票代码: 实时价}
    :return: 收益率百分数；未选策略或无记录时返回 None
    """
    if not us_id:
        return None
    record: dict[str, Any] | None = latest_execution_map(executions).get(us_id)
    if record is None:
        return None
    return current_rate_pct_with_fallback(record, price_map)

# 导航分组定义 — icon 使用 Material Design 名称
_NAV_GROUPS: list[dict[str, Any]] = [
    {
        "label": None,
        "items": [
            ("仪表盘", "dashboard", "home"),
        ],
    },
    {
        "label": "策略管理",
        "items": [
            ("策略列表", "list", "strategies"),
            ("策略监控", "monitor_heart", "monitor"),
            ("策略选股", "fact_check", "strategy_select"),
        ],
    },
    {
        "label": "股票池管理",
        "items": [
            ("股票池列表", "layers", "pools"),
            ("股票行情", "candlestick_chart", "quotes"),
        ],
    },
    {
        "label": "系统设置",
        "items": [
            ("个人喜好", "settings", "preferences"),
            ("黑名单", "block", "blacklist"),
        ],
    },
]


def _check_auth_heartbeat() -> None:
    """会话心跳：检测到未登录（如后台线程 401 已清除 authenticated）立即跳转登录页。

    后台线程（run_in_executor / MQTT 回调）触发 401 时无法直接操作 UI 跳转，
    但 ApiClient._on_401 会把 app.storage.user["authenticated"] 置为 False（跨线程可见）。
    本心跳每秒在客户端 UI 线程检查一次，发现未登录即跳转 /login。
    """
    try:
        if not app.storage.user.get("authenticated", False):
            ui.navigate.to("/login")
    except Exception:
        logger.debug("心跳检测登录状态失败", exc_info=True)


def _ensure_user_strategies() -> None:
    """确保会话内的用户策略缓存可用（按会话节流，默认 60 秒）。

    由所有页面入口调用：既解决"子页面首次访问缓存键不存在导致 KeyError"，
    又能在节流窗口后感知后端的策略状态变更（如策略被外部停止）。
    token 无需在此补设：后台线程通过 core.concurrency.run_blocking()
    继承请求上下文后，可直接从会话解析出本会话的 token。
    """
    try:
        UserStrategyManager().ensure_loaded()
    except Exception:
        logger.warning("加载用户策略缓存失败", exc_info=True)


def _apply_theme_for_session() -> None:
    """应用本会话的主题（深色 / 浅色）。

    主题归属存在会话（``app.storage.user["theme_mode"]``）而不是进程级变量，
    否则多用户会互相串号。会话里还没有记录时，从后端用户偏好补齐一次
    （只补一次，失败也打标记，避免每次翻页都发请求）。

    注意：后端在"用户从未保存过偏好"时会返回默认的 theme_mode="light"，
    那不是用户的选择，不能直接采信——由 resolve_mode_from_prefs 判定。

    注意：页面渲染期间会先应用主题再取色，因此下面这个顺序不能调换。
    """
    theme_manager = AppContext().theme_manager
    try:
        bootstrapped: bool = bool(app.storage.user.get("theme_bootstrapped", False))
        if theme_manager.session_mode() is None and not bootstrapped:
            mode: str | None = None
            try:
                mode = resolve_mode_from_prefs(AppContext().preference_api.get())
            except Exception as e:  # noqa: BLE001
                logger.warning("读取用户主题偏好失败: %s", e)
            app.storage.user["theme_bootstrapped"] = True
            if mode is not None:
                theme_manager.remember_mode(mode)
    except Exception:  # noqa: BLE001 - 会话存储不可用时退回默认主题
        logger.debug("读取会话主题失败", exc_info=True)
    theme_manager.apply_session_theme()


def main_page() -> None:
    """主页面入口：侧边栏导航 + 内容区。未登录时直接跳转登录页。"""
    if not app.storage.user.get("authenticated", False):
        app.storage.user["referrer_path"] = "/"
        ui.navigate.to("/login")
        return

    # 心跳：会话被后台 401 清除后立即跳转登录页（覆盖后台线程触发的登出）
    ui.timer(1.0, _check_auth_heartbeat, immediate=False)

    # 确保会话内的用户策略缓存可用（按会话节流，默认 60 秒）
    _ensure_user_strategies()

    _apply_theme_for_session()
    custom_tabs.load_left_drawer_tab_css()

    theme = AppContext().theme_manager
    bg_color: str = theme.var("page")
    font_color: str = theme.var("text")
    border_color: str = theme.var("border")
    accent_color: str = theme.var("accent")
    sidebar_bg: str = theme.var("surface-alt")
    card_bg: str = theme.var("surface")

    ui.add_css(f"""
        .q-page {{
            padding: 0 !important;
            margin: 0 !important;
            width: 100% !important;
            background-color: {bg_color} !important;
        }}
        .q-drawer {{
            top: 0;
            bottom: 0;
            padding: 0;
            margin: 0;
            height: 100%;
            background-color: {sidebar_bg} !important;
        }}
        .nicegui-drawer {{
            padding: 0 !important;
            margin: 0 !important;
        }}
    """)

    # ---- 加载历史信号到内存（必须在 UI 上下文中调用；用户策略缓存已在函数入口加载） ----
    AppContext().signal_manager.load_history(limit=10)
    # ---- 左侧抽屉导航 ----
    with ui.left_drawer(top_corner=True, bordered=True).props("width=256") \
        .classes("gap-0") \
        .style(f"border: 1px solid {border_color}; background-color: {sidebar_bg};"):
        _build_sidebar(font_color, accent_color, sidebar_bg, border_color, current_page="home")

    # ---- 右侧内容区 ----
    with ui.column().classes("w-full h-full gap-0").style(
        f"background-color: {bg_color}; margin: 0; padding: 0;"
    ):
        _build_topbar("仪表盘", font_color, accent_color, border_color, card_bg)

        with ui.column().classes("w-full gap-0").style(
            f"flex: 1; overflow-y: auto; padding: 32px; background-color: {bg_color}; min-height: 0;"
        ):
            _render_home_dashboard(font_color, accent_color, border_color, card_bg, bg_color)


def _build_sidebar(
    font_color: str,
    accent_color: str,
    sidebar_bg: str,
    border_color: str,
    current_page: str = "home",
) -> None:
    """构建侧边栏导航。

    :param font_color: 字体颜色
    :param accent_color: 强调色
    :param sidebar_bg: 侧边栏背景色
    :param border_color: 边框颜色
    :param current_page: 当前活跃页面 key
    """
    # Logo — 点击回首页
    with ui.row().classes("w-full items-center gap-3").style("padding: 24px;"):
        with ui.element("div").classes("w-8 h-8 rounded flex items-center justify-center").style(
            "background-color: var(--qs-primary); color: var(--qs-on-accent);"
        ):
            ui.icon("trending_up").style("font-size: 16px;")
        ui.label("QTrade").classes("text-xl font-bold").style("color: var(--qs-heading);")

    # 导航链接
    with ui.column().classes("w-full flex-1 gap-0 px-4 py-4 overflow-y-auto"):
        for group in _NAV_GROUPS:
            if group["label"]:
                ui.label(group["label"]).classes("text-xs font-semibold uppercase tracking-wider").style(
                    "color: var(--qs-faint); padding: 16px 16px 8px 16px;"
                )
            for item_label, item_icon, item_key in group["items"]:
                is_active: bool = (item_key == current_page)
                link_bg: str = "var(--qs-surface)" if is_active else "transparent"
                link_color: str = "var(--qs-link)" if is_active else "var(--qs-muted)"
                link_border: str = "3px solid var(--qs-primary)" if is_active else "3px solid transparent"

                with ui.row().classes("w-full items-center gap-3 cursor-pointer rounded-lg").style(
                    f"padding: 12px 16px; background-color: {link_bg}; color: {link_color}; "
                    f"border-right: {link_border}; font-size: 14px; font-weight: 500; "
                    f"transition: all 0.15s;"
                ).on("click", lambda _k=item_key: _navigate_to_page(_k)):
                    ui.icon(item_icon).style(f"font-size: 18px; color: {link_color};")
                    ui.label(item_label)

    # 底部用户信息 — 点击弹出菜单
    username: str = app.storage.user.get("username", "")
    if username:
        display_name = username
        phone: str = app.storage.user.get("phone", "")
        user_email: str = app.storage.user.get("email", "")
        if phone:
            phone_clean: str = phone.strip().replace(" ", "").replace("-", "")
            if len(phone_clean) >= 7:
                display_sub = f"{phone_clean[:3]}****{phone_clean[-4:]}"
            else:
                display_sub = phone
        elif user_email:
            display_sub = user_email
        else:
            display_sub = "信息待完善"
    else:
        display_name = "未登录"
        display_sub = "请先登录"

    authenticated: bool = app.storage.user.get("authenticated", False)
    # 底部用户信息 — 右侧 ⋮ 按钮弹出菜单
    with ui.row().classes("w-full items-center gap-3 px-6 py-4").style(
        f"border-top: 1px solid {border_color}; margin-top: auto;"
    ):
        with ui.element("div").classes("w-8 h-8 rounded-full flex items-center justify-center").style(
            "background-color: var(--qs-avatar-bg); color: var(--qs-info);"
        ):
            ui.icon("person").style("font-size: 16px;")
        with ui.column().classes("gap-0 overflow-hidden flex-1"):
            ui.label(display_name).classes("text-sm font-medium truncate").style("color: var(--qs-text);")
            ui.label(display_sub).classes("text-xs truncate").style("color: var(--qs-faint);")
        # 菜单触发按钮
        with ui.button(icon="more_vert").props("flat round dense").style("color: var(--qs-faint);"), ui.menu().style(
            f"width: 150px; min-width: 150px; "
            f"background-color: {sidebar_bg}; "
            f"border: 1px solid {border_color}; "
            f"color: var(--qs-text); font-size: 14px;"
        ):
            if authenticated:
                ui.menu_item("个人资料", on_click=_show_profile_dialog)
                ui.separator()
                ui.menu_item("退出登录", on_click=_do_logout)
            else:
                ui.menu_item("登录 / 注册", on_click=lambda: ui.navigate.to("/login"))


def _navigate_to_page(key: str) -> None:
    """处理侧边栏页面导航。

    :param key: 页面标识符（如 "monitor"，不是 URL 路径）
    """
    ui.navigate.to(route_for_page_key(key))


def main_page_with_content(content_fn: Any, page_key: str = "home") -> None:
    """渲染主页面布局 + 指定内容函数（用于独立页面路由）。

    :param content_fn: 内容渲染函数
    :param page_key: 当前页面 key（用于侧边栏高亮）
    """
    if not app.storage.user.get("authenticated", False):
        # 必须走 page_key → 路由的映射，不能拼 f"/{page_key}"：
        # page_key 是页面标识（如 "monitor"），真实路由是 "/strategy-monitor"，
        # 直接拼接会得到不存在的 "/monitor"，登录回跳后落到 404 页面。
        app.storage.user["referrer_path"] = route_for_page_key(page_key)
        ui.navigate.to("/login")
        return

    # 心跳：会话被后台 401 清除后立即跳转登录页（覆盖后台线程触发的登出）
    ui.timer(1.0, _check_auth_heartbeat, immediate=False)

    # 所有页面入口统一保证用户策略缓存可用（按会话节流，默认 60 秒）：
    # 子页面不再是"首次访问时缓存键不存在 → KeyError"，也能感知后端状态变更。
    _ensure_user_strategies()

    # 所有页面入口统一把「最近的历史信号」补进内存（本进程本会话只做一次）。
    # SignalManager 的信号容器是**进程级**的，应用热重载 / 重启后即为空；在下一条
    # MQ 信号到达前不补历史信号，所有展示信号的地方（监控页信号表与策略卡片、
    # 策略列表的"最近信号"列、首页近期运行状态）都会是空的，且刷新页面也无法恢复。
    # ⚠ 原先只有 main_page()（首页 /）做了这一步，子页面直接访问或刷新时取不到信号。
    AppContext().signal_manager.load_history(limit=10)

    _apply_theme_for_session()
    custom_tabs.load_left_drawer_tab_css()

    theme = AppContext().theme_manager
    bg_color: str = theme.var("page")
    font_color: str = theme.var("text")
    border_color: str = theme.var("border")
    accent_color: str = theme.var("accent")
    sidebar_bg: str = theme.var("surface-alt")

    ui.add_css(f"""
        .q-page {{
            padding: 0 !important;
            margin: 0 !important;
            width: 100% !important;
            background-color: {bg_color} !important;
        }}
        .q-drawer {{
            top: 0;
            bottom: 0;
            padding: 0;
            margin: 0;
            height: 100%;
            background-color: {sidebar_bg} !important;
        }}
        .nicegui-drawer {{
            padding: 0 !important;
            margin: 0 !important;
        }}
    """)

    with ui.left_drawer(top_corner=True, bordered=True).props("width=256") \
        .classes("gap-0") \
        .style(f"border: 1px solid {border_color}; background-color: {sidebar_bg};"):
        _build_sidebar(font_color, accent_color, sidebar_bg, border_color, current_page=page_key)

    with ui.column().classes("w-full h-full gap-0").style(
        f"background-color: {bg_color}; margin: 0; padding: 0;"
    ), ui.column().classes("w-full gap-0").style(
        f"flex: 1; overflow-y: auto; background-color: {bg_color};"
    ):
        try:
            content_fn()
        except Exception as e:  # noqa: BLE001
            logger.error(f"页面渲染失败: {e}")
            with ui.column().classes("w-full items-center justify-center").style("padding: 60px;"):
                ui.icon("error").style("color: var(--qs-positive); font-size: 48px;")
                ui.label("页面加载失败").style("color: var(--qs-positive);")
                ui.label(str(e)).style("color: var(--qs-muted);")


def _build_topbar(
    title: str,
    font_color: str,
    accent_color: str,
    border_color: str,
    card_bg: str,
) -> None:
    """构建顶部标题栏。

    :param title: 页面标题
    :param font_color: 字体颜色
    :param accent_color: 强调色
    :param border_color: 边框颜色
    :param card_bg: 卡片背景色
    """
    with ui.row().classes("w-full items-center justify-between").style(
        f"height: 64px; padding: 0 32px; background-color: {card_bg}; "
        f"border-bottom: 1px solid {border_color}; position: sticky; top: 0; z-index: 10;"
    ):
        ui.label(title).classes("text-xl font-bold").style("color: var(--qs-heading);")
        with ui.row().classes("items-center gap-4"):
            with ui.row().classes("items-center gap-2"):
                ui.element("span").classes("w-2 h-2 rounded-full inline-block").style("background-color: var(--qs-negative);")
                ui.label("交易系统运行中").classes("text-sm").style("color: var(--qs-muted);")

            # ---- 通知铃铛：按服务端未读数亮角标，点击弹出消息列表 ----
            # 已读状态由服务端维护（system_message_tbl.read_user_ids）。前端不再自己记
            # 「见过的消息 ID」：那种做法只活在本会话内存里，刷新页面后红点会重新亮起，
            # 换端登录也读不到同一份已读历史。

            def _fetch_notif_messages() -> list[dict[str, Any]]:
                """查询当前用户的系统消息（在线程池中执行）。

                :return: 消息列表（每条带 is_read）；查询失败返回空列表
                """
                try:
                    return list(AppContext().system_message_api.user_messages() or [])
                except Exception:
                    logger.debug("查询系统消息失败", exc_info=True)
                    return []

            def _count_unread(msgs: list[dict[str, Any]]) -> int:
                """统计未读消息条数。

                :param msgs: 系统消息列表
                :return: is_read 为假的消息条数
                """
                return sum(1 for m in msgs if not m.get("is_read", False))

            def _sync_badge(msgs: list[dict[str, Any]]) -> None:
                """按服务端未读数刷新铃铛角标。

                :param msgs: 系统消息列表
                """
                unread: int = _count_unread(msgs)
                notif_badge.set_text(str(unread))
                notif_badge.set_visibility(unread > 0)

            async def _update_notif_badge() -> None:
                """定时轮询：按服务端未读数刷新角标。"""
                _sync_badge(await run_blocking(_fetch_notif_messages))

            async def _on_notif_click() -> None:
                """点击通知按钮：拉取消息并弹出列表。"""
                _show_message_dialog(await run_blocking(_fetch_notif_messages))

            def _mark_message_read(message_id: str) -> None:
                """把单条消息标记为已读（失败只记日志，不打断界面）。

                :param message_id: 系统消息 ID
                """
                try:
                    AppContext().system_message_api.mark_read(message_id)
                except Exception:
                    logger.warning("标记系统消息已读失败: %s", message_id, exc_info=True)

            def _show_message_dialog(msgs: list[dict[str, Any]]) -> None:
                """展示系统消息对话框：未读在上、历史在下，点开单条即置为已读。

                「已读」与「历史」是同一个状态（后端 is_read），所以列表按 is_read 分两段：
                未读段带圆点、点击后调接口标记已读并原地刷新；历史段置灰，仅供查阅。

                :param msgs: 已拉取的系统消息列表
                """
                with ui.dialog() as dialog, ui.card().style(
                    "background-color: var(--qs-surface); border: 1px solid var(--qs-border); "
                    "border-radius: 12px; padding: 0; width: 520px; max-width: 90vw;"
                ):
                    # 标题栏
                    with ui.row().classes("w-full items-center justify-between").style(
                        "padding: 16px 24px; border-bottom: 1px solid var(--qs-border);"
                    ):
                        ui.label("系统消息").classes("text-lg font-bold").style("color: var(--qs-heading);")
                        ui.button(icon="close", on_click=dialog.close).props("flat round dense").style(
                            "color: var(--qs-muted);"
                        )
                    # 消息列表容器（标记已读后原地重绘，不重新打开对话框）
                    body: ui.column = ui.column().classes("w-full gap-0").style(
                        "max-height: 60vh; overflow-y: auto;"
                    )
                    # 当前展示的数据。点开单条后重新拉一次，避免只改本地副本导致与服务端不一致
                    state: dict[str, list[dict[str, Any]]] = {"msgs": list(msgs)}

                    def _render_row(msg: dict[str, Any], unread: bool) -> None:
                        """渲染一条消息；未读的点一下即标记为已读。

                        :param msg: 消息字典
                        :param unread: 是否未读（未读带圆点、加粗、可点击）
                        """
                        message_id: str = str(msg.get("id", ""))
                        title: str = str(msg.get("title", ""))
                        content: str = str(msg.get("message", ""))
                        create_time: str = str(msg.get("create_time", ""))[:16]
                        row: ui.column = ui.column().classes("w-full gap-1").style(
                            f"padding: 16px 24px; cursor: {'pointer' if unread else 'default'};"
                        )
                        if unread:
                            row.on("click", _row_click_handler(message_id))
                        with row:
                            with ui.row().classes("w-full items-center justify-between"):
                                with ui.row().classes("items-center gap-2"):
                                    if unread:
                                        ui.element("span").classes(
                                            "w-2 h-2 rounded-full inline-block"
                                        ).style("background-color: var(--qs-negative);")
                                    ui.label(title).classes(
                                        "text-sm font-semibold" if unread else "text-sm"
                                    ).style(
                                        f"color: {'var(--qs-text)' if unread else 'var(--qs-muted)'};"
                                    )
                                ui.label(create_time).classes("text-xs").style("color: var(--qs-faint);")
                            ui.label(content).classes("text-sm").style(
                                "color: var(--qs-muted); white-space: pre-wrap; word-break: break-all;"
                            )

                    def _render_body() -> None:
                        """按 state 重绘消息列表（未读段 + 历史段）。"""
                        body.clear()
                        current: list[dict[str, Any]] = state["msgs"]
                        unread_list: list[dict[str, Any]] = [
                            m for m in current if not m.get("is_read", False)
                        ]
                        history_list: list[dict[str, Any]] = [
                            m for m in current if m.get("is_read", False)
                        ]
                        with body:
                            if not current:
                                with ui.row().classes("w-full items-center justify-center").style(
                                    "padding: 48px 0;"
                                ):
                                    ui.icon("notifications_none").style(
                                        "color: var(--qs-icon-muted); font-size: 48px;"
                                    )
                                with ui.row().classes("w-full items-center justify-center").style(
                                    "padding-bottom: 24px;"
                                ):
                                    ui.label("暂无系统消息").classes("text-sm").style(
                                        "color: var(--qs-faint);"
                                    )
                                return
                            for section, items, unread in (
                                ("未读消息", unread_list, True),
                                ("历史消息", history_list, False),
                            ):
                                if not items:
                                    continue
                                with ui.row().classes("w-full items-center justify-between").style(
                                    "padding: 12px 24px; background-color: var(--qs-surface-alt);"
                                ):
                                    ui.label(section).classes(
                                        "text-xs font-semibold uppercase tracking-wider"
                                    ).style("color: var(--qs-muted);")
                                    ui.label(str(len(items))).classes("text-xs").style(
                                        "color: var(--qs-faint);"
                                    )
                                for idx, msg in enumerate(items):
                                    if idx > 0:
                                        ui.separator().style("background-color: var(--qs-border);")
                                    _render_row(msg, unread)

                    async def _mark_and_refresh(message_id: str) -> None:
                        """标记单条消息已读，然后重新拉取并刷新列表与角标。

                        :param message_id: 系统消息 ID
                        """
                        await run_blocking(_mark_message_read, message_id)
                        current: list[dict[str, Any]] = await run_blocking(_fetch_notif_messages)
                        state["msgs"] = current
                        _sync_badge(current)
                        _render_body()

                    def _row_click_handler(message_id: str) -> Any:
                        """为某条消息生成点击处理器。

                        这里返回真正的 async 函数（而不是 lambda）：NiceGUI 对协程函数会在
                        事件循环里 await，lambda 返回协程对象的写法依赖框架内部实现细节。

                        :param message_id: 系统消息 ID
                        :return: 无参 async 事件处理器
                        """
                        async def _handler() -> None:
                            await _mark_and_refresh(message_id)
                        return _handler

                    _render_body()
                    _sync_badge(state["msgs"])
                dialog.open()

            notif_btn = ui.button(icon="notifications", on_click=_on_notif_click).props(
                "flat round dense"
            ).style("color: var(--qs-muted);")
            notif_badge = ui.badge("0", color="red").props("floating")
            notif_badge.set_visibility(False)
            with notif_btn:
                notif_badge  # noqa: B018

            # 定时轮询系统消息，按服务端未读数刷新角标
            PollingTask(10.0, _update_notif_badge, name="顶栏未读消息角标").start()


# ============================================================
# 首页仪表盘内容 (home.html)
# ============================================================

# 累计收益图周期选项 → 对应天数
_PERIOD_DAYS: dict[str, int] = {
    "7天": 7,
    "30天": 30,
    "1年": 365,
}

# 收益图主曲线名（图例与 tooltip 里显示）
_TOTAL_SERIES_NAME: str = "系统总收益"

# 用户策略状态 → (显示文本, 文字颜色, 背景颜色)
_STATUS_STYLES: dict[str, tuple[str, str, str]] = {
    "running": ("运行中", "var(--qs-negative)", "rgba(var(--qs-negative-rgb), 0.3)"),
    "stopped": ("已停止", "var(--qs-label)", "var(--qs-chip-neutral)"),
    "paused": ("已暂停", "var(--qs-accent)", "rgba(var(--qs-accent-rgb), 0.25)"),
    "error": ("异常", "var(--qs-positive)", "rgba(var(--qs-positive-rgb), 0.25)"),
}


def _render_home_dashboard(
    font_color: str,
    accent_color: str,
    border_color: str,
    card_bg: str,
    bg_color: str,
) -> None:
    """渲染首页仪表盘全部内容。

    :param font_color: 字体颜色
    :param accent_color: 强调色
    :param border_color: 边框颜色
    :param card_bg: 卡片背景色
    :param bg_color: 页面背景色
    """
    positive_color: str = AppContext().theme_manager.var("positive")
    negative_color: str = AppContext().theme_manager.var("negative")

    # 统计卡片行（策略总数 / 运行中策略 / 股票池数量 / 今日信号数）
    _build_stats_cards(card_bg, border_color, font_color, positive_color, negative_color)

    # 累计收益图（2/3） + 近期运行状态（1/3）
    with ui.row().classes("w-full gap-8").style("margin-top: 32px;"):
        with ui.column().classes("gap-0").style("flex: 2; min-width: 0;"):
            _build_profit_chart(card_bg, border_color, font_color, accent_color)
        with ui.column().classes("gap-0").style("flex: 1; min-width: 300px;"):
            _build_recent_activity(card_bg, border_color, font_color, positive_color, negative_color)

    # 近期更新策略表格
    with ui.row().classes("w-full").style("margin-top: 32px;"):
        _build_recent_strategies_table(
            card_bg, border_color, font_color, positive_color, negative_color,
        )


def _build_stats_cards(
    card_bg: str,
    border_color: str,
    font_color: str,
    positive_color: str,
    negative_color: str,
) -> None:
    """构建4个统计卡片：策略总数 / 运行中策略 / 股票池数量 / 今日信号数。

    今日信号数特殊处理：
    1. 页面打开时先通过 trade_signal_api 查询数据库中今日信号总数；
    2. 将基数写入 SignalManager（自动扣除内存中已有的 MQ 信号避免重复）；
    3. 之后 SignalManager 接收 MQTT 交易信号实时累加，
       定时器读取 get_today_count() 刷新卡片显示。

    :param card_bg: 卡片背景
    :param border_color: 边框颜色
    :param font_color: 字体颜色
    :param positive_color: 正向色
    :param negative_color: 负向色
    """
    # ---- 用户策略关联列表（策略总数 / 运行中数量 / 本月新增） ----
    
    strategy_total: int = UserStrategyManager().get_strategy_count()
    running_count: int = UserStrategyManager().get_running_count()
    month_new: int = UserStrategyManager().get_month_new_count()

    # ---- 股票池数量 ----
    pool_count: int = 0
    try:
        pools: list[dict[str, Any]] = AppContext().pool_api.list()
        pool_count = len(pools)
    except Exception:
        logger.warning("查询股票池列表失败", exc_info=True)

    # ---- 今日信号数：先查数据库初始化基数 ----
    today_signal_count: int = AppContext().signal_manager.get_today_count()

    cards_data: list[dict[str, Any]] = [
        {"key": "total", "icon": "description",
         "icon_bg": "rgba(var(--qs-primary-rgb), 0.4)", "icon_color": "var(--qs-link)",
         "label": "策略总数", "value": str(strategy_total),
         "badge": f"+{month_new} 本月", "badge_color": "var(--qs-negative)",
         "badge_bg": "rgba(var(--qs-negative-rgb), 0.3)"},
        {"key": "running", "icon": "play_circle",
         "icon_bg": "rgba(var(--qs-negative-rgb), 0.4)", "icon_color": "var(--qs-negative)",
         "label": "运行中策略", "value": str(running_count),
         "badge": "运行平稳", "badge_color": "var(--qs-muted)",
         "badge_bg": "var(--qs-chip-neutral)"},
        {"key": "pools", "icon": "database",
         "icon_bg": "rgba(var(--qs-purple-rgb), 0.4)", "icon_color": "var(--qs-purple)",
         "label": "股票池数量", "value": str(pool_count),
         "badge": "", "badge_color": "", "badge_bg": ""},
        {"key": "signals", "icon": "bolt",
         "icon_bg": "rgba(var(--qs-orange-rgb), 0.4)", "icon_color": "var(--qs-orange)",
         "label": "今日信号数", "value": str(today_signal_count),
         "badge": "今日活跃" if today_signal_count > 0 else "暂无",
         "badge_color": "var(--qs-positive)", "badge_bg": "rgba(var(--qs-positive-rgb), 0.3)"},
    ]

    # key → {value: 数值label, badge: 角标label}，供定时刷新使用
    card_refs: dict[str, dict[str, Any]] = {}

    with ui.row().classes("w-full gap-6"):
        for card in cards_data:
            with ui.card().classes("gap-0").style(
                f"flex: 1; min-width: 200px; background-color: {card_bg}; "
                f"border: 1px solid {border_color}; border-radius: 12px; padding: 24px;"
            ):
                with ui.row().classes("w-full items-start justify-between").style("margin-bottom: 16px;"):
                    with ui.element("div").classes("w-10 h-10 rounded-lg flex items-center justify-center").style(
                        f"background-color: {card['icon_bg']}; color: {card['icon_color']};"
                    ):
                        ui.icon(card["icon"]).style("font-size: 24px;")
                    badge_label: ui.label = ui.label(card["badge"]).classes(
                        "text-xs font-medium rounded px-2 py-1"
                    ).style(
                        f"color: {card['badge_color'] or 'var(--qs-muted)'}; "
                        f"background-color: {card['badge_bg'] or 'transparent'};"
                    )
                ui.label(card["label"]).classes("text-sm").style("color: var(--qs-muted);")
                value_label: ui.label = ui.label(card["value"]).classes("text-2xl font-bold").style(
                    "color: var(--qs-heading); margin-top: 4px;"
                )
                card_refs[card["key"]] = {"value": value_label, "badge": badge_label}

    # ---- 定时刷新今日信号数（MQ 新信号由 SignalManager 实时接收） ----
    def _refresh_today_signals() -> None:
        """定时刷新今日信号卡片数值与角标（纯内存读取，无需切线程）。"""
        try:
            today_signal_count: int = AppContext().signal_manager.get_today_count()
            refs: dict[str, Any] | None = card_refs.get("signals")
            if refs is None:
                return
            value_lbl: ui.label = refs["value"]
            badge_lbl: ui.label = refs["badge"]
            value_lbl.set_text(str(today_signal_count))
            if today_signal_count > 0:
                badge_lbl.set_text("今日活跃")
                badge_lbl.style("color: var(--qs-positive); background-color: rgba(var(--qs-positive-rgb), 0.3);")
            else:
                badge_lbl.set_text("暂无")
                badge_lbl.style("color: var(--qs-muted); background-color: transparent;")
        except Exception:
            logger.debug("刷新今日信号数失败", exc_info=True)

    PollingTask(3.0, _refresh_today_signals, in_thread=False, name="首页今日信号数").start()


def _build_profit_chart(
    card_bg: str,
    border_color: str,
    font_color: str,
    accent_color: str,
) -> None:
    """构建系统累计收益概览卡片 — ECharts 折线图（可叠加一条单策略对比线）。

    两条曲线（口径见 core/holdings.py 头部）：
    - **系统总收益**：按 update_time 落到日期，每个点 = 各用户策略收益率的**平均值** ——
      单个策略当天的取值是它当天**最后一条**执行记录的 current_return_rate（该字段是
      累计值，同日多条相加会重复累加），当天没交易的策略沿用最近一次的取值，
      **收益为 0 的策略不计入平均**（只含已实现，×100 后为百分数）。代表「当前」的
      末点由 average_current_rate_pct 覆盖成各策略「含持仓浮盈」收益率的平均值
      （同样排除 0）—— 历史点的实时价无法回溯，浮盈只能在末点体现。
    - **单策略对比**：右上角下拉框选中某个用户策略后追加，数据是同一批记录按
      user_strategy_id 过滤后的该策略取值，琥珀色、不带面积，避免与主曲线互相遮盖。

    取数分三步：先用 list_executions() 全量确定窗口基准（最后一个数据点）、末点，
    以及**窗口开始前各策略的取值**（求和的起始基准，窗口数据里没有这部分）；
    再调 executions/range 按窗口做服务端过滤取数据点。两者都带 30 秒 TTL ——
    切换周期只重查窗口；切换策略连窗口都复用；页面加载后首次绘制与表格共用行情缓存。

    支持 7天 / 30天 / 1年 周期切换。

    ⚠ **没有定时刷新**：_load_data() 只在卡片构建、切换周期、切换策略时调用，
    不像「近期更新策略表」那样挂 60 秒 PollingTask。

    :param card_bg: 卡片背景
    :param border_color: 边框颜色
    :param font_color: 字体颜色
    :param accent_color: 强调色
    """
    period_state: dict[str, str] = {"period": "30天"}
    select_state: dict[str, str] = {"us_id": "", "name": ""}
    # 当前周期查回的窗口数据：切换策略时复用，不重复打时间范围接口
    window_state: dict[str, Any] = {"period": "", "data": []}
    button_refs: dict[str, ui.label] = {}

    # 下拉框选项：value = user_strategy_id，label = 用户策略名称
    # （名称缺失时退回 ID，避免出现一行空白选项）
    us_options: dict[str, str] = {}
    for us in UserStrategyManager().list_user_strategies():
        us_id: str = str(us.get("_id", "") or us.get("id", "") or "")
        if us_id:
            us_options[us_id] = str(us.get("name", "") or "") or us_id

    def _button_style(is_active: bool) -> str:
        """周期按钮样式。

        :param is_active: 是否为当前选中项
        :return: CSS 样式字符串
        """
        if is_active:
            return (
                "border: 1px solid var(--qs-primary); background-color: rgba(var(--qs-primary-rgb), 0.3); "
                "color: var(--qs-link); padding: 4px 12px; border-radius: 6px; "
                "font-size: 12px; font-weight: 500; cursor: pointer;"
            )
        return (
            f"border: 1px solid {border_color}; background-color: transparent; "
            f"color: var(--qs-muted); padding: 4px 12px; border-radius: 6px; "
            f"font-size: 12px; font-weight: 500; cursor: pointer;"
        )

    with ui.card().classes("w-full gap-0").style(
        f"background-color: {card_bg}; border: 1px solid {border_color}; "
        f"border-radius: 12px; padding: 24px; min-height: 416px; height: 450px"
    ):
        # ---- 标题 + 对比策略下拉 + 周期切换按钮 ----
        with ui.row().classes("w-full items-center justify-between").style("margin-bottom: 24px;"):
            ui.label("系统累计收益概览").classes("text-lg font-bold").style("color: var(--qs-heading);")
            with ui.row().classes("gap-3 items-center"):
                # 默认不选策略：只画系统总收益曲线，选中后才追加对比线
                strategy_select: ui.select = ui.select(
                    options=us_options,
                    value=None,
                    label="对比策略",
                    with_input=True,
                    clearable=True,
                ).props("dense outlined options-dense").style("min-width: 150px;")
                for period in _PERIOD_DAYS:
                    btn: ui.label = ui.label(period).classes("rounded").style(
                        _button_style(period == period_state["period"])
                    ).on(
                        "click",
                        lambda _p=period: _set_period(str(_p)),
                    )
                    button_refs[period] = btn

        chart = ui.echart(_profit_chart_option([], [])).classes("w-full").style("height: 350px;")
        # 所选策略在当前窗口内没有记录时的提示位
        hint: ui.label = ui.label("").classes("text-xs").style(
            "color: var(--qs-muted); margin-top: 4px;"
        )
        hint.set_visibility(False)

        def _on_select(event: Any) -> None:
            """下拉框选择变化 → 重画（选中时额外画一条该策略的对比曲线）。

            :param event: ValueChangeEventArguments，value 为 user_strategy_id
            """
            us_id: str = str(getattr(event, "value", None) or "")
            select_state["us_id"] = us_id
            select_state["name"] = us_options.get(us_id, "")
            _load_data()

        def _set_period(period: str) -> None:
            """切换统计周期并刷新按钮样式与图表数据。

            :param period: 周期文本（7天/30天/1年）
            """
            period_state["period"] = period
            for p, b in button_refs.items():
                b.style(_button_style(p == period))
            _load_data()

        def _load_data() -> None:
            """按当前周期与所选策略刷新图表。"""
            all_execs: list[dict[str, Any]] = _cached_all_executions()
            days_limit: int = _PERIOD_DAYS.get(period_state["period"], 30)

            # 窗口数据按周期缓存：只在周期变化时重查时间范围接口
            period: str = period_state["period"]
            if window_state["period"] != period or not window_state["data"]:
                start_time, end_time = _window_bounds(
                    last_execution_day(all_execs), days_limit,
                )
                window_state["period"] = period
                window_state["data"] = _query_window_executions(
                    start_time, end_time, all_execs,
                )
            window_execs: list[dict[str, Any]] = window_state["data"]

            # 实时价按「全部策略最新记录」的持仓代码一次取回，与近期更新策略表共用缓存
            price_map: dict[str, float] = _cached_price_map(collect_position_codes(
                [list(rec.get("positions", []) or [])
                 for rec in latest_execution_map(all_execs).values()]
            ))

            us_id: str = select_state["us_id"]
            labels: list[str]
            total_rates: list[float | None]
            single_rates: list[float | None]
            labels, total_rates, single_rates = _build_cumulative_series(
                window_execs,
                days_limit,
                average_current_rate_pct(all_execs, price_map),
                us_id,
                _strategy_current_rate_pct(all_execs, us_id, price_map),
                # 窗口开始前的各策略取值：求和要带上「窗口外没再交易的策略」，
                # 而时间范围接口只回窗口内的记录
                history=all_execs,
            )

            series: list[dict[str, Any]] = [
                {"name": _TOTAL_SERIES_NAME, "data": total_rates},
            ]
            # 窗口内没有该策略的记录时不追加空曲线：ECharts 会把全 None 的 series
            # 画成一条不存在的线，图例里却留着名字，读起来像"数据丢了"
            has_single: bool = bool(us_id) and any(v is not None for v in single_rates)
            if has_single:
                series.append({
                    "name": select_state["name"] or us_id,
                    "data": single_rates,
                })

            # EChart.options 为只读属性，需原地修改字典后调用 update() 推送变更
            chart.options.clear()
            chart.options.update(_profit_chart_option(labels, series))
            chart.update()

            # 所选策略在本窗口内没有记录时给一句提示，免得被读成「曲线丢了」
            hint.set_text(
                f"「{select_state['name']}」在该时间范围内没有执行记录"
                if us_id and not has_single else ""
            )
            hint.set_visibility(bool(us_id) and not has_single)

        strategy_select.on_value_change(_on_select)
        _load_data()


def _build_cumulative_series(
    executions: list[dict[str, Any]],
    days_limit: int,
    last_rate_pct: float | None = None,
    select_us_id: str = "",
    select_last_rate_pct: float | None = None,
    history: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[float | None], list[float | None]]:
    """把执行记录转成曲线序列（系统总收益 + 可选的单策略对比曲线）。

    - 总收益曲线：每个点 = 各用户策略收益率的**平均值**（见
      core.holdings.daily_average_rates）—— 单个策略当天的取值是它当天**最后一条**
      记录的 current_return_rate（该字段是累计值，同日多条相加会重复累加），当天没
      交易的策略沿用最近一次的取值，**收益为 0 的策略不计入分子也不占分母**；
    - 单策略曲线：按 user_strategy_id 过滤后按同一规则取值
      （见 core.holdings.strategy_levels_on_days）；
    - 两条曲线的日期取**并集**，各自缺失的日期补 ``None``（ECharts 断线）。
      不能补 0：补 0 会在图上画出一条跌到 0 的假线。

    :param executions: 窗口内的执行记录
    :param days_limit: 保留最近 N 天的数据，<=0 表示不限制
    :param last_rate_pct: 总收益曲线末点要覆盖成的百分数（含持仓浮盈），
                          由 core.holdings.average_current_rate_pct 算好传进来；
                          None 表示不覆盖
    :param select_us_id: 要叠加对比曲线的用户策略 ID，空表示不叠加
    :param select_last_rate_pct: 对比曲线末点要覆盖成的百分数，None 表示不覆盖
    :param history: 全量执行记录，用来取「窗口开始前各策略的取值」。时间范围接口
                    只回窗口内的记录，缺这一份时窗口外已停交易的策略会在整个窗口里
                    凭空消失（总和少一截）
    :return: (日期标签列表 MM-DD, 总收益曲线数据, 单策略曲线数据；
              未选策略时第三个为空列表)
    """
    # 窗口第一天：求和的起始基准要取「它之前各策略的取值」，由全量记录算
    window_days: list[str] = sorted(
        d for d in (execution_day(ex) for ex in executions) if d
    )
    source: list[dict[str, Any]] = history if history is not None else executions
    carry_in: dict[str, float] = (
        strategy_levels_before(source, window_days[0]) if window_days else {}
    )
    total_daily: dict[str, float] = daily_average_rates(executions, carry_in)

    # 单策略曲线：同一批记录按 user_strategy_id 过滤，规则与总和里的单策略口径一致
    single_execs: list[dict[str, Any]] = []
    single_carry: dict[str, float] = {}
    if select_us_id:
        single_execs = [
            ex for ex in executions
            if str(ex.get("user_strategy_id", "") or "") == select_us_id
        ]
        if window_days:
            single_carry = strategy_levels_before(
                [ex for ex in source
                 if str(ex.get("user_strategy_id", "") or "") == select_us_id],
                window_days[0],
            )

    days: list[str] = sorted(total_daily)
    if not days:
        return [], [], []

    # 按周期截取最近 N 天（基准是**最后一个数据点**，不是今天）
    if days_limit > 0 and len(days) > 1:
        try:
            last_day: datetime = datetime.strptime(days[-1], "%Y-%m-%d")  # noqa: DTZ007
            cutoff: str = (last_day - timedelta(days=days_limit)).strftime("%Y-%m-%d")
            filtered_days: list[str] = [d for d in days if d >= cutoff]
            if filtered_days:
                days = filtered_days
        except ValueError:
            pass

    total_rates: list[float | None] = [total_daily.get(d) for d in days]
    single_rates: list[float | None] = (
        strategy_levels_on_days(
            single_execs, days, single_carry.get(select_us_id),
        )
        if select_us_id else []
    )

    # 末点代表「当前」：叠加持仓未实现盈亏（历史点无实时价可回溯，保持已实现口径）
    if last_rate_pct is not None and total_rates:
        total_rates[-1] = round(float(last_rate_pct), 2)
    # 对比曲线只在窗口内确实有数据时覆盖末点：否则会凭空多出一个孤立的点
    if (select_last_rate_pct is not None and single_rates
            and any(v is not None for v in single_rates)):
        single_rates[-1] = round(float(select_last_rate_pct), 2)

    labels: list[str] = [d[5:] for d in days]  # YYYY-MM-DD → MM-DD
    return labels, total_rates, single_rates


def _profit_chart_option(
    labels: list[str], series: list[dict[str, Any]],
) -> dict[str, Any]:
    """构建累计收益 ECharts 配置（主曲线带渐变面积，对比曲线只描线）。

    ECharts 用 canvas 绘制，**不解析 CSS 变量**，所以配色必须取真实色值
    （``theme_manager.get_chart_colors()``），不能传 ``var(--qs-*)``。

    tooltip 模板按曲线数量动态拼：字符串模板里引用不存在的 series 索引
    （例如只有一条线却写 ``{c1}``）会渲染出空段。

    :param labels: X 轴日期标签（两条曲线的日期并集）
    :param series: 曲线定义，每项 {name, data}；第一条为主曲线
    :return: ECharts option 字典
    """
    chart_colors: dict[str, str] = AppContext().theme_manager.get_chart_colors()
    tooltip_formatter: str = "<br/>".join(
        ["{b}"] + [f"{{a{i}}}: {{c{i}}}%" for i in range(len(series))]
    )

    option: dict[str, Any] = {
        "backgroundColor": "transparent",
        # 两条曲线时顶部留出图例的位置，否则图例会压在折线上
        "grid": {"top": 36 if len(series) > 1 else 20, "right": 20, "bottom": 40, "left": 50},
        "tooltip": {"trigger": "axis", "formatter": tooltip_formatter},
        "xAxis": {
            "type": "category",
            "data": labels,
            "axisLine": {"lineStyle": {"color": chart_colors["axis_line"]}},
            "axisLabel": {"color": chart_colors["axis"]},
        },
        "yAxis": {
            "type": "value",
            "axisLine": {"show": False},
            "splitLine": {"lineStyle": {"color": chart_colors["grid"], "type": "dashed"}},
            "axisLabel": {"color": chart_colors["axis"], "formatter": "{value}%"},
        },
        "series": [
            _echart_line(item, chart_colors, index)
            for index, item in enumerate(series)
        ],
    }
    if len(series) > 1:
        option["legend"] = {
            "top": 0, "right": 0, "itemWidth": 14, "itemHeight": 8,
            "textStyle": {"color": chart_colors["axis"], "fontSize": 11},
        }
    return option


def _echart_line(
    item: dict[str, Any], chart_colors: dict[str, str], index: int,
) -> dict[str, Any]:
    """把一条曲线定义转成 ECharts series。

    第一条（index 0，系统总收益）用主色 + 渐变面积、线宽 3；其余（单策略对比）
    用副色、线宽 2、**不铺面积**——两条曲线都铺面积会互相遮盖，看不出一条压着
    另一条的部分。

    :param item: {name, data}
    :param chart_colors: 真实色值集合
    :param index: 曲线序号
    :return: ECharts series 字典
    """
    is_primary: bool = index == 0
    color: str = chart_colors["line"] if is_primary else chart_colors["line2"]
    line: dict[str, Any] = {
        "name": item.get("name", ""),
        "data": item.get("data", []),
        "type": "line",
        "smooth": True,
        "symbol": "none",
        # 缺失日期补的是 None，会让线断开。绝不能连起来：那会把缺口画成直线，
        # 看起来像"这段时间一直平稳"，实际只是没有数据。
        "connectNulls": False,
        "lineStyle": {"color": color, "width": 3 if is_primary else 2},
        "itemStyle": {"color": color},
    }
    if is_primary:
        line["areaStyle"] = {
            "color": {
                "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
                "colorStops": [
                    {"offset": 0, "color": chart_colors["line_area_top"]},
                    {"offset": 1, "color": chart_colors["line_area_bottom"]},
                ],
            },
        }
    return line


def _build_recent_activity(
    card_bg: str,
    border_color: str,
    font_color: str,
    positive_color: str,
    negative_color: str,
) -> None:
    """构建近期运行状态列表 — 从 SignalManager 获取最近交易信号。

    本次改造（对应 CR-02 / CR-03 / CR-04 / CR-18）：
    - 改用 signal_manager.get_signals(limit=ACTIVITY_COUNT)：**全局**按时间倒序
      取最近 N 条。原实现用 get_signals_by_user_strategy(limit=4)，其 limit 是
      "每策略"上限（会渲染 4×策略数 行），且该函数原先完全不排序，
      展示的既不是"最近"也不是"4 条"。
    - 拉取放到线程池（run_blocking），策略名称走会话缓存里的用户策略名
      （``user_strategy.name``），模板名只兜底，不再对每条信号各发一次
      strategy_api.get_by_id()。
    - 数据签名未变化时跳过 clear()+重建，避免每秒重建 DOM 造成闪屏、
      丢失滚动位置。
    - 轮询改用 PollingTask（重入保护 + 最小间隔），避免 sleep(负数) 退化。

    :param card_bg: 卡片背景
    :param border_color: 边框颜色
    :param font_color: 字体颜色
    :param positive_color: 正向色
    :param negative_color: 负向色
    """
    with ui.card().classes("w-full gap-0").style(
        f"background-color: {card_bg}; border: 1px solid {border_color}; "
        f"border-radius: 12px; padding: 24px; min-height: 416px; height: 450px"
    ):
        ui.label("近期运行状态").classes("text-lg font-bold").style(
            "color: var(--qs-heading); margin-bottom: 24px;"
        )

        activity_container = ui.column().classes("w-full gap-6")
        # 渲染状态：signature 用于判断数据是否变化，避免无谓重建
        render_state: dict[str, Any] = {"signature": None}

        def _fetch_activities() -> list[dict[str, Any]]:
            """拉取最近活动（在线程池中执行）。

            策略名称取**用户策略名称**（``user_strategy.name``）。信号里的
            ``strategy_id`` 是**策略模板 ID**，同一模板下建了多个用户策略时
            会显示成同一个名字、分不清是哪条策略的信号，因此必须按
            ``user_strategy_id`` 归并。名称回退链：用户策略名 → 模板名 → ``--``。

            :return: [{name, action, code, time}]，按时间倒序
            """
            signals: list[dict[str, Any]] = AppContext().signal_manager.get_signals(
                limit=ACTIVITY_COUNT, only_running=True,
            )

            # 用户策略 ID → 名称（取自会话缓存，不发请求）
            us_names: dict[str, str] = {}
            for us in UserStrategyManager().list_user_strategies():
                us_id: str = str(us.get("_id", "") or us.get("id", ""))
                if us_id:
                    us_names[us_id] = str(us.get("name", "") or "")

            # 模板名只作兜底：会话缓存里没有该策略时（如缓存未刷新）还能显示名字
            strategy_ids: list[str] = list({
                str(s.get("strategy_id", "") or "") for s in signals if s.get("strategy_id")
            })
            templates: dict[str, dict[str, Any]] = AppContext().strategy_cache.get_many(strategy_ids)
            tpl_names: dict[str, str] = {
                sid: str(tpl.get("name", "") or "") for sid, tpl in templates.items()
            }
            return [
                {
                    "name": (
                        us_names.get(str(s.get("user_strategy_id", "") or ""), "")
                        or tpl_names.get(str(s.get("strategy_id", "")), "")
                        or "--"
                    ),
                    "action": str(s.get("action", "") or ""),
                    "code": str(s.get("stock_code", "") or ""),
                    "time": str(s.get("create_time", ""))[:16],
                }
                for s in signals
            ]

        def _render(activities: list[dict[str, Any]]) -> None:
            """渲染活动列表（在 UI 线程执行；数据未变化时直接跳过）。

            :param activities: 活动条目列表
            """
            signature: tuple = tuple(
                (it["time"], it["name"], it["action"], it["code"]) for it in activities
            )
            if signature == render_state["signature"]:
                return
            render_state["signature"] = signature

            activity_container.clear()
            with activity_container:
                if not activities:
                    ui.label("暂无信号活动").classes("text-sm").style("color: var(--qs-faint);")
                    return
                for it in activities:
                    action: str = it["action"]
                    color: str = negative_color if action == "卖出" else positive_color
                    with ui.row().classes("w-full items-start gap-4"):
                        with ui.element("div").classes("w-2 h-2 mt-2 rounded-full").style(
                            f"background-color: {color}; min-width: 8px;"
                        ):
                            pass
                        with ui.column().classes("gap-1").style("flex: 1;"):
                            ui.label(it["name"]).classes("text-sm font-semibold").style("color: var(--qs-text);")
                            ui.label(f"触发{action}信号: {it['code']}").classes("text-xs").style(f"color: {color};")
                            ui.label(it["time"]).classes("text-xs").style("color: var(--qs-faint);")

        async def _poll() -> None:
            """轮询任务：线程池拉取，UI 线程渲染。"""
            _render(await run_blocking(_fetch_activities))

        # 首屏渲染
        _render(_fetch_activities())

        # 定时刷新（PollingTask）
        PollingTask(1.0, _poll, name="首页近期运行状态").start()

        ui.button("查看全部活动", on_click=lambda: ui.navigate.to("/strategy-monitor")).props(
            "flat"
        ).classes("w-full mt-1").style(
            "color: var(--qs-link); font-size: 14px; font-weight: 500;"
        )


def _build_recent_strategies_table(
    card_bg: str,
    border_color: str,
    font_color: str,
    positive_color: str,
    negative_color: str,
) -> None:
    """构建近期更新策略表格（策略名称 / 类型 / 创建日期 / 状态 / 收益率）。

    数据来源：UserStrategyManager 会话缓存（用户策略关联）+ list_executions()
    执行结果集（取每个用户策略最新一条记录的收益率）；
    策略名称优先 user_strategy.name（用户自定义名称），类型通过策略模板补充，
    模板走 AppContext().strategy_cache 进程级缓存。

    :param card_bg: 卡片背景
    :param border_color: 边框颜色
    :param font_color: 字体颜色
    :param positive_color: 正向色
    :param negative_color: 负向色
    """
    with ui.card().classes("w-full gap-0 overflow-hidden").style(
        f"background-color: {card_bg}; border: 1px solid {border_color}; "
        f"border-radius: 12px; padding: 0;"
    ):
        # ---- 表头栏 ----
        with ui.row().classes("w-full items-center justify-between").style(
            f"padding: 16px 24px; border-bottom: 1px solid {border_color};"
        ):
            ui.label("近期更新策略").classes("text-lg font-bold").style("color: var(--qs-heading);")
            ui.button("管理所有策略", on_click=lambda: ui.navigate.to("/strategy-monitor")).props(
                "flat dense"
            ).style("color: var(--qs-link); font-size: 14px;")

        table_container: ui.column = ui.column().classes("w-full gap-0")

        def _fetch_table_data() -> tuple[
            list[dict[str, Any]], dict[str, float], dict[str, dict[str, Any]],
        ]:
            """拉取表格数据。

            :return: (用户策略列表按创建时间倒序,
                      用户策略ID → 最新执行记录的当前收益率百分数,
                      策略模板ID → 策略模板信息)
            """
            user_strategies: list[dict[str, Any]] = UserStrategyManager().list_user_strategies()

            # 全量执行记录走概要页共用缓存（与收益曲线的窗口基准、末点共用一次请求）。
            # 必须用全量：取「每个策略最新一条」时，按时间范围查会漏掉窗口外的策略。
            executions: list[dict[str, Any]] = _cached_all_executions()

            # 每个用户策略只取最新一条执行记录：历史记录对应的是当时的持仓，
            # 实时价无法回溯，只有最新那条的浮动盈亏才是「当前」的。
            latest_map: dict[str, dict[str, Any]] = latest_execution_map(executions)
            price_map: dict[str, float] = _cached_price_map(
                collect_position_codes(
                    [list(rec.get("positions", []) or []) for rec in latest_map.values()]
                )
            )
            # 当前收益率 = (已实现 current_profit + 持仓未实现) / initial_amount；
            # 缺实时价或字段缺失 ⇒ 不在映射里 ⇒ 渲染成 "-"（不退回已实现口径凑数）
            latest_rate: dict[str, float] = {}
            for us_id, rec in latest_map.items():
                positions: list[dict[str, Any]] = list(rec.get("positions", []) or [])
                rate_pct: float | None = current_return_rate_pct(
                    rec, unrealized_profit(positions, price_map),
                )
                if rate_pct is not None:
                    latest_rate[us_id] = rate_pct

            # 策略模板信息（走进程级缓存 strategy_cache，避免每次渲染重复请求）
            template_ids: list[str] = [
                str(us.get("strategy_id", "") or "") for us in user_strategies[:5]
            ]
            templates: dict[str, dict[str, Any]] = AppContext().strategy_cache.get_many(template_ids)
            return user_strategies, latest_rate, templates

        def _render(
            user_strategies: list[dict[str, Any]],
            latest_rate: dict[str, float],
            templates: dict[str, dict[str, Any]],
        ) -> None:
            """渲染表格内容（在 UI 线程执行，仅做绘制）。

            :param user_strategies: 用户策略列表
            :param latest_rate: 用户策略ID → 最新收益率百分数
            :param templates: 策略模板ID → 策略模板信息
            """
            table_container.clear()
            with table_container:
                if not user_strategies:
                    with ui.column().classes("w-full items-center justify-center").style("padding: 40px;"):
                        ui.icon("inbox").style("color: var(--qs-faint); font-size: 36px;")
                        ui.label("暂无策略数据").classes("text-sm").style(
                            "color: var(--qs-muted); margin-top: 8px;"
                        )
                    return

                rows: list[dict[str, Any]] = user_strategies[:5]

                # 表头
                with ui.row().classes("w-full items-center").style(
                    f"background-color: var(--qs-surface-alt); border-bottom: 1px solid {border_color}; "
                    f"padding: 12px 24px;"
                ):
                    for hdr, flex in [
                        ("策略名称", 2), ("类型", 1), ("创建日期", 1),
                        ("状态", 1), ("收益率", 1),
                    ]:
                        ui.label(hdr).classes(
                            "text-xs font-semibold uppercase tracking-wider"
                        ).style(f"color: var(--qs-muted); flex: {flex};")

                for idx, us in enumerate(rows):
                    us_id: str = str(us.get("_id", "") or us.get("id", ""))
                    sid: str = str(us.get("strategy_id", ""))
                    tpl: dict[str, Any] = templates.get(sid, {})
                    # 优先用户自定义名称（user_strategy.name），与策略列表页保持一致；
                    # 历史数据无自定义名称时回退策略模板名，最后回退策略 ID
                    name: str = str(
                        us.get("name", "") or tpl.get("name", "") or sid or "未命名策略"
                    )
                    stype: str = str(tpl.get("strategy_type", "") or "-")
                    create_date: str = str(us.get("create_time", ""))[:10] or "-"
                    status: str = str(us.get("status", "stopped"))
                    status_text: str
                    status_color: str
                    status_bg: str
                    status_text, status_color, status_bg = _STATUS_STYLES.get(
                        status, ("未知", "var(--qs-label)", "var(--qs-chip-neutral)"),
                    )
                    rate_pct: float | None = latest_rate.get(us_id)
                    row_bg: str = "var(--qs-surface)" if idx % 2 == 0 else "var(--qs-row-alt)"

                    with ui.row().classes("w-full items-center").style(
                        f"padding: 16px 24px; background-color: {row_bg}; "
                        f"border-bottom: 1px solid var(--qs-divider-strong);"
                    ):
                        ui.label(name).classes("text-sm font-medium").style(
                            "color: var(--qs-text); flex: 2;"
                        )
                        ui.label(stype).classes("text-sm").style("color: var(--qs-muted); flex: 1;")
                        ui.label(create_date).classes("text-sm").style("color: var(--qs-faint); flex: 1;")
                        with ui.row().classes("items-center").style("flex: 1;"):
                            ui.label(status_text).classes(
                                "text-xs font-medium rounded-full px-2.5 py-0.5"
                            ).style(
                                f"color: {status_color}; background-color: {status_bg};"
                            )
                        if rate_pct is None:
                            ui.label("-").classes("text-sm font-semibold").style(
                                "color: var(--qs-faint); flex: 1;"
                            )
                        else:
                            # 中国股市习惯：涨红跌绿
                            rate_color: str = (
                                positive_color if rate_pct > 0
                                else (negative_color if rate_pct < 0 else font_color)
                            )
                            ui.label(f"{rate_pct:+.1f}%").classes("text-sm font-semibold").style(
                                f"color: {rate_color}; flex: 1;"
                            )

        # 首屏渲染（页面构建阶段本就在线程池中，不必再切线程）
        _render(*_fetch_table_data())

        async def _poll() -> None:
            """轮询任务：线程池拉取执行结果，UI 线程重建表格。"""
            _render(*(await run_blocking(_fetch_table_data)))

        # 定时刷新（状态/收益变化）——PollingTask 保证不阻塞事件循环且有最小间隔
        PollingTask(60.0, _poll, name="首页近期更新策略表").start()
