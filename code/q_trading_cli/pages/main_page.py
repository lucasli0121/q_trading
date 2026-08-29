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
    - 系统累计收益概览图表（list_executions 执行结果集构建）+ 近期运行状态
    - 近期更新策略表格
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from nicegui import app, ui

from app_context import AppContext
from components import custom_tabs
from menu.top_menu import _do_logout, _show_profile_dialog

logger = logging.getLogger(__name__)

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


def main_page() -> None:
    """主页面入口：侧边栏导航 + 内容区。未登录时直接跳转登录页。"""
    if not app.storage.user.get("authenticated", False):
        app.storage.user["referrer_path"] = "/"
        ui.navigate.to("/login")
        return

    AppContext().theme_manager.set_theme("dark")
    custom_tabs.load_left_drawer_tab_css()

    current_theme: dict[str, str] = AppContext().theme_manager.get_current_theme()
    bg_color: str = current_theme.get("background", "#0f0f1a")
    font_color: str = current_theme.get("font_color", "#e5e7eb")
    border_color: str = current_theme.get("main_border_color", "#334155")
    accent_color: str = current_theme.get("accent", "#fbbf24")
    sidebar_bg: str = "#111827"
    card_bg: str = "#1e293b"

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
            "background-color: #3b82f6; color: #ffffff;"
        ):
            ui.icon("trending_up").style("font-size: 16px;")
        ui.label("QuantSys").classes("text-xl font-bold").style("color: #f3f4f6;")

    # 导航链接
    with ui.column().classes("w-full flex-1 gap-0 px-4 py-4 overflow-y-auto"):
        for group in _NAV_GROUPS:
            if group["label"]:
                ui.label(group["label"]).classes("text-xs font-semibold uppercase tracking-wider").style(
                    "color: #6b7280; padding: 16px 16px 8px 16px;"
                )
            for item_label, item_icon, item_key in group["items"]:
                is_active: bool = (item_key == current_page)
                link_bg: str = "#1e293b" if is_active else "transparent"
                link_color: str = "#60a5fa" if is_active else "#9ca3af"
                link_border: str = "3px solid #3b82f6" if is_active else "3px solid transparent"

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
            "background-color: #1e3a5f; color: #93c5fd;"
        ):
            ui.icon("person").style("font-size: 16px;")
        with ui.column().classes("gap-0 overflow-hidden flex-1"):
            ui.label(display_name).classes("text-sm font-medium truncate").style("color: #e5e7eb;")
            ui.label(display_sub).classes("text-xs truncate").style("color: #6b7280;")
        # 菜单触发按钮
        with ui.button(icon="more_vert").props("flat round dense").style("color: #6b7280;"), ui.menu().style(
            f"width: 150px; min-width: 150px; "
            f"background-color: {sidebar_bg}; "
            f"border: 1px solid {border_color}; "
            f"color: #e5e7eb; font-size: 14px;"
        ):
            if authenticated:
                ui.menu_item("个人资料", on_click=_show_profile_dialog)
                ui.separator()
                ui.menu_item("退出登录", on_click=_do_logout)
            else:
                ui.menu_item("登录 / 注册", on_click=lambda: ui.navigate.to("/login"))


def _navigate_to_page(key: str) -> None:
    """处理侧边栏页面导航。

    :param key: 页面标识符
    """
    route_map: dict[str, str] = {
        "home": "/",
        "strategies": "/strategies",
        "monitor": "/strategy-monitor",
        "pools": "/stock-pools",
        "quotes": "/stock-quotes",
        "preferences": "/settings-preferences",
        "blacklist": "/settings-blacklist",
    }
    path: str = route_map.get(key, "/")
    ui.navigate.to(path)


def main_page_with_content(content_fn: Any, page_key: str = "home") -> None:
    """渲染主页面布局 + 指定内容函数（用于独立页面路由）。

    :param content_fn: 内容渲染函数
    :param page_key: 当前页面 key（用于侧边栏高亮）
    """
    if not app.storage.user.get("authenticated", False):
        app.storage.user["referrer_path"] = f"/{page_key}" if page_key != "home" else "/"
        ui.navigate.to("/login")
        return

    AppContext().theme_manager.set_theme("dark")
    custom_tabs.load_left_drawer_tab_css()

    current_theme: dict[str, str] = AppContext().theme_manager.get_current_theme()
    bg_color: str = current_theme.get("background", "#0f0f1a")
    font_color: str = current_theme.get("font_color", "#e5e7eb")
    border_color: str = current_theme.get("main_border_color", "#334155")
    accent_color: str = current_theme.get("accent", "#fbbf24")
    sidebar_bg: str = "#111827"

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
                ui.icon("error").style("color: #f87171; font-size: 48px;")
                ui.label("页面加载失败").style("color: #f87171;")
                ui.label(str(e)).style("color: #9ca3af;")


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
        ui.label(title).classes("text-xl font-bold").style("color: #f3f4f6;")
        with ui.row().classes("items-center gap-4"):
            with ui.row().classes("items-center gap-2"):
                ui.element("span").classes("w-2 h-2 rounded-full inline-block").style("background-color: #34d399;")
                ui.label("交易系统运行中").classes("text-sm").style("color: #9ca3af;")

            # ---- 通知铃铛：红点提示新系统消息，点击弹出消息列表 ----
            # 已读消息 ID 集合，用于判断是否有新消息
            seen_msg_ids: set[str] = set()

            def _on_notif_click() -> None:
                """点击通知按钮：弹出系统消息对话框。"""
                # 标记当前所有消息为已读
                try:
                    msgs: list[dict[str, Any]] = AppContext().system_message_api.user_messages()
                    for m in msgs:
                        mid: str = str(m.get("id", ""))
                        if mid:
                            seen_msg_ids.add(mid)
                except Exception:  # noqa: BLE001
                    msgs = []
                # 隐藏红点
                notif_badge.set_visibility(False)
                # 弹出对话框
                _show_message_dialog(msgs)

            def _show_message_dialog(msgs: list[dict[str, Any]]) -> None:
                """展示系统消息对话框。

                :param msgs: 系统消息列表
                """
                with ui.dialog() as dialog, ui.card().style(
                    "background-color: #1e293b; border: 1px solid #334155; "
                    "border-radius: 12px; padding: 0; width: 520px; max-width: 90vw;"
                ):
                    # 标题栏
                    with ui.row().classes("w-full items-center justify-between").style(
                        "padding: 16px 24px; border-bottom: 1px solid #334155;"
                    ):
                        ui.label("系统消息").classes("text-lg font-bold").style("color: #f3f4f6;")
                        ui.button(icon="close", on_click=dialog.close).props("flat round dense").style(
                            "color: #9ca3af;"
                        )
                    # 消息列表
                    with ui.column().classes("w-full gap-0").style("max-height: 60vh; overflow-y: auto;"):
                        if not msgs:
                            with ui.row().classes("w-full items-center justify-center").style("padding: 48px 0;"):
                                ui.icon("notifications_none").style("color: #4b5563; font-size: 48px;")
                            with ui.row().classes("w-full items-center justify-center").style("padding-bottom: 24px;"):
                                ui.label("暂无系统消息").classes("text-sm").style("color: #6b7280;")
                        else:
                            for idx, msg in enumerate(msgs):
                                title: str = str(msg.get("title", ""))
                                content: str = str(msg.get("message", ""))
                                create_time: str = str(msg.get("create_time", ""))[:16]
                                # 分隔线（非首条）
                                if idx > 0:
                                    ui.separator().style("background-color: #334155;")
                                with ui.column().classes("w-full gap-1").style("padding: 16px 24px;"):
                                    with ui.row().classes("w-full items-center justify-between"):
                                        ui.label(title).classes("text-sm font-semibold").style(
                                            "color: #e5e7eb;"
                                        )
                                        ui.label(create_time).classes("text-xs").style("color: #6b7280;")
                                    ui.label(content).classes("text-sm").style(
                                        "color: #9ca3af; white-space: pre-wrap; word-break: break-all;"
                                    )
                dialog.open()

            notif_btn = ui.button(icon="notifications", on_click=_on_notif_click).props(
                "flat round dense"
            ).style("color: #9ca3af;")
            notif_badge = ui.badge("0", color="red").props("floating")
            notif_badge.set_visibility(False)
            with notif_btn:
                notif_badge  # noqa: B018

            # 定时轮询系统消息，有新消息则显示红点
            def _update_notif_badge() -> None:
                """定时检查系统消息，新消息到达时显示红点。"""
                try:
                    msgs = AppContext().system_message_api.user_messages()
                    for m in msgs:
                        mid = str(m.get("id", ""))
                        if mid and mid not in seen_msg_ids:
                            notif_badge.set_text("1")
                            notif_badge.set_visibility(True)
                            return
                except Exception:  # noqa: BLE001, S110
                    pass

            ui.timer(10.0, _update_notif_badge)


# ============================================================
# 首页仪表盘内容 (home.html)
# ============================================================

# 累计收益图周期选项 → 对应天数
_PERIOD_DAYS: dict[str, int] = {
    "7天": 7,
    "30天": 30,
    "1年": 365,
}

# 用户策略状态 → (显示文本, 文字颜色, 背景颜色)
_STATUS_STYLES: dict[str, tuple[str, str, str]] = {
    "running": ("运行中", "#34d399", "rgba(52,211,153,0.3)"),
    "stopped": ("已停止", "#d1d5db", "rgba(55,65,81,0.8)"),
    "paused": ("已暂停", "#fbbf24", "rgba(251,191,36,0.25)"),
    "error": ("异常", "#f87171", "rgba(248,113,113,0.25)"),
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
    positive_color: str = AppContext().theme_manager.get_current_theme().get("positive", "#34d399")
    negative_color: str = AppContext().theme_manager.get_current_theme().get("negative", "#f87171")

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
    user_strategies: list[dict[str, Any]] = []
    try:
        user_strategies = AppContext().user_strategy_api.list()
    except Exception:
        logger.warning("查询用户策略列表失败", exc_info=True)
    strategy_total: int = len(user_strategies)
    running_count: int = sum(
        1 for s in user_strategies if s.get("status") == "running"
    )
    month_prefix: str = datetime.now().strftime("%Y-%m")  # noqa: DTZ005
    month_new: int = sum(
        1 for s in user_strategies
        if str(s.get("create_time", ""))[:7] == month_prefix
    )

    # ---- 股票池数量 ----
    pool_count: int = 0
    try:
        pools: list[dict[str, Any]] = AppContext().pool_api.list()
        pool_count = len(pools)
    except Exception:
        logger.warning("查询股票池列表失败", exc_info=True)

    # ---- 今日信号数：先查数据库初始化基数 ----
    db_today_count: int = 0
    try:
        today_start: str = datetime.now().strftime("%Y-%m-%d") + " 00:00:00"  # noqa: DTZ005
        db_signals: list[dict[str, Any]] = AppContext().trade_signal_api.list(
            start_time=today_start,
        )
        db_today_count = len(db_signals)
    except Exception:
        logger.warning("查询今日信号（数据库）失败", exc_info=True)
    try:
        AppContext().signal_manager.init_today_count(db_today_count)
    except Exception:
        logger.warning("初始化今日信号基数失败", exc_info=True)
    today_signals: int = AppContext().signal_manager.get_today_count()

    # ---- 加载历史信号到内存（必须在 UI 上下文中调用） ----
    AppContext().signal_manager.load_history(limit=10)

    cards_data: list[dict[str, Any]] = [
        {"key": "total", "icon": "description",
         "icon_bg": "rgba(59,130,246,0.4)", "icon_color": "#60a5fa",
         "label": "策略总数", "value": str(strategy_total),
         "badge": f"+{month_new} 本月", "badge_color": "#34d399",
         "badge_bg": "rgba(52,211,153,0.3)"},
        {"key": "running", "icon": "play_circle",
         "icon_bg": "rgba(52,211,153,0.4)", "icon_color": "#34d399",
         "label": "运行中策略", "value": str(running_count),
         "badge": "运行平稳", "badge_color": "#9ca3af",
         "badge_bg": "rgba(55,65,81,0.8)"},
        {"key": "pools", "icon": "database",
         "icon_bg": "rgba(168,85,247,0.4)", "icon_color": "#c084fc",
         "label": "股票池数量", "value": str(pool_count),
         "badge": "", "badge_color": "", "badge_bg": ""},
        {"key": "signals", "icon": "bolt",
         "icon_bg": "rgba(251,146,60,0.4)", "icon_color": "#fb923c",
         "label": "今日信号数", "value": str(today_signals),
         "badge": "今日活跃" if today_signals > 0 else "暂无",
         "badge_color": "#f87171", "badge_bg": "rgba(248,113,113,0.3)"},
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
                        f"color: {card['badge_color'] or '#9ca3af'}; "
                        f"background-color: {card['badge_bg'] or 'transparent'};"
                    )
                ui.label(card["label"]).classes("text-sm").style("color: #9ca3af;")
                value_label: ui.label = ui.label(card["value"]).classes("text-2xl font-bold").style(
                    "color: #f3f4f6; margin-top: 4px;"
                )
                card_refs[card["key"]] = {"value": value_label, "badge": badge_label}

    # ---- 定时刷新今日信号数（MQ 新信号由 SignalManager 实时接收） ----
    def _refresh_today_signals() -> None:
        """定时刷新今日信号卡片数值与角标。"""
        try:
            count: int = AppContext().signal_manager.get_today_count()
            refs: dict[str, Any] | None = card_refs.get("signals")
            if refs is None:
                return
            value_lbl: ui.label = refs["value"]
            badge_lbl: ui.label = refs["badge"]
            value_lbl.set_text(str(count))
            if count > 0:
                badge_lbl.set_text("今日活跃")
                badge_lbl.style("color: #f87171; background-color: rgba(248,113,113,0.3);")
            else:
                badge_lbl.set_text("暂无")
                badge_lbl.style("color: #9ca3af; background-color: transparent;")
        except Exception:  # noqa: BLE001, S110
            pass

    ui.timer(2.0, _refresh_today_signals)


def _build_profit_chart(
    card_bg: str,
    border_color: str,
    font_color: str,
    accent_color: str,
) -> None:
    """构建系统累计收益概览卡片 — ECharts 折线图。

    数据来源：user_strategy_api.list_executions() 获取登录用户
    所有策略执行结果集，取每条记录的收益率（current_return_rate）
    与时间（update_time），按日期汇总后绘制累计收益曲线。
    支持 7天 / 30天 / 1年 周期切换。

    :param card_bg: 卡片背景
    :param border_color: 边框颜色
    :param font_color: 字体颜色
    :param accent_color: 强调色
    """
    period_state: dict[str, str] = {"period": "30天"}
    button_refs: dict[str, ui.label] = {}

    def _button_style(is_active: bool) -> str:
        """周期按钮样式。

        :param is_active: 是否为当前选中项
        :return: CSS 样式字符串
        """
        if is_active:
            return (
                "border: 1px solid #3b82f6; background-color: rgba(59,130,246,0.3); "
                "color: #60a5fa; padding: 4px 12px; border-radius: 6px; "
                "font-size: 12px; font-weight: 500; cursor: pointer;"
            )
        return (
            f"border: 1px solid {border_color}; background-color: transparent; "
            f"color: #9ca3af; padding: 4px 12px; border-radius: 6px; "
            f"font-size: 12px; font-weight: 500; cursor: pointer;"
        )

    with ui.card().classes("w-full gap-0").style(
        f"background-color: {card_bg}; border: 1px solid {border_color}; "
        f"border-radius: 12px; padding: 24px; min-height: 416px; height: 450px"
    ):
        # ---- 标题 + 周期切换按钮 ----
        with ui.row().classes("w-full items-center justify-between").style("margin-bottom: 24px;"):
            ui.label("系统累计收益概览").classes("text-lg font-bold").style("color: #f3f4f6;")
            with ui.row().classes("gap-2"):
                for period in _PERIOD_DAYS:
                    btn: ui.label = ui.label(period).classes("rounded").style(
                        _button_style(period == period_state["period"])
                    ).on(
                        "click",
                        lambda _p=period: _set_period(str(_p)),
                    )
                    button_refs[period] = btn

        chart = ui.echart(_profit_chart_option([], [])).classes("w-full").style("height: 350px;")

        def _set_period(period: str) -> None:
            """切换统计周期并刷新按钮样式与图表数据。

            :param period: 周期文本（7天/30天/1年）
            """
            period_state["period"] = period
            for p, b in button_refs.items():
                b.style(_button_style(p == period))
            _load_data()

        def _load_data() -> None:
            """拉取执行结果集并更新累计收益图表。"""
            executions: list[dict[str, Any]] = []
            try:
                executions = AppContext().user_strategy_api.list_executions()
            except Exception:
                logger.warning("查询策略执行结果失败", exc_info=True)
            days_limit: int = _PERIOD_DAYS.get(period_state["period"], 30)
            labels: list[str]
            values: list[float]
            labels, values = _build_cumulative_series(executions, days_limit)
            # EChart.options 为只读属性，需原地修改字典后调用 update() 推送变更
            chart.options.clear()
            chart.options.update(_profit_chart_option(labels, values))
            chart.update()

        _load_data()


def _build_cumulative_series(
    executions: list[dict[str, Any]],
    days_limit: int,
) -> tuple[list[str], list[float]]:
    """将执行结果集转换为按日汇总的累计收益序列。

    :param executions: list_executions 返回的执行结果列表，
                       每条包含 current_return_rate（0.15 表示 15%）与 update_time
    :param days_limit: 保留最近 N 天的数据，<=0 表示不限制
    :return: (日期标签列表 MM-DD, 收益率百分数列表)
    """
    points: list[tuple[str, float]] = []
    for ex in executions:
        raw_time: str = str(
            ex.get("update_time") or ex.get("create_time") or ""
        ).strip()
        if len(raw_time) < 10:
            continue
        try:
            rate_pct: float = float(ex.get("current_return_rate", 0) or 0) * 100.0
        except (TypeError, ValueError):
            continue
        points.append((raw_time[:10], rate_pct))
    if not points:
        return [], []

    # 按时间升序排列；同一天多条记录保留最后一条（最新状态）
    points.sort(key=lambda p: p[0])
    daily: dict[str, float] = {}
    for day, rate in points:
        daily[day] = rate

    days: list[str] = list(daily.keys())
    rates: list[float] = list(daily.values())

    # 按周期截取最近 N 天
    if days_limit > 0 and len(days) > 1:
        try:
            last_day: datetime = datetime.strptime(days[-1], "%Y-%m-%d")  # noqa: DTZ007
            cutoff: str = (last_day - timedelta(days=days_limit)).strftime("%Y-%m-%d")
            filtered_days: list[str] = [d for d in days if d >= cutoff]
            if filtered_days:
                days = filtered_days
                rates = [daily[d] for d in filtered_days]
        except ValueError:
            pass

    labels: list[str] = [d[5:] for d in days]  # YYYY-MM-DD → MM-DD
    return labels, rates


def _profit_chart_option(labels: list[str], values: list[float]) -> dict[str, Any]:
    """构建累计收益 ECharts 配置（深色主题，蓝色渐变面积折线）。

    :param labels: X 轴日期标签
    :param values: 累计收益率（百分数）
    :return: ECharts option 字典
    """
    option: dict[str, Any] = {
        "backgroundColor": "transparent",
        "grid": {"top": 20, "right": 20, "bottom": 40, "left": 50},
        "tooltip": {"trigger": "axis"},
        "xAxis": {
            "type": "category",
            "data": labels,
            "axisLine": {"lineStyle": {"color": "#334155"}},
            "axisLabel": {"color": "#9ca3af"},
        },
        "yAxis": {
            "type": "value",
            "axisLine": {"show": False},
            "splitLine": {"lineStyle": {"color": "#2d3748", "type": "dashed"}},
            "axisLabel": {"color": "#9ca3af", "formatter": "{value}%"},
        },
        "series": [{
            "data": values,
            "type": "line",
            "smooth": True,
            "symbol": "none",
            "lineStyle": {"color": "#3b82f6", "width": 3},
            "areaStyle": {
                "color": {
                    "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
                    "colorStops": [
                        {"offset": 0, "color": "rgba(59,130,246,0.3)"},
                        {"offset": 1, "color": "rgba(59,130,246,0)"},
                    ],
                },
            },
        }],
    }
    return option


def _build_recent_activity(
    card_bg: str,
    border_color: str,
    font_color: str,
    positive_color: str,
    negative_color: str,
) -> None:
    """构建近期运行状态列表 — 从 SignalManager 获取最近交易信号。

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
            "color: #f3f4f6; margin-bottom: 24px;"
        )

        activity_container = ui.column().classes("w-full gap-6")

        def _render() -> None:
            activity_container.clear()
            with activity_container:
                signals: list[dict[str, Any]] = AppContext().signal_manager.get_signals(limit=4)
                if not signals:
                    ui.label("暂无信号活动").classes("text-sm").style("color: #6b7280;")
                for sig in signals:
                    action: str = sig.get("action", "")
                    code: str = sig.get("stock_code", "")
                    sid: str = sig.get("strategy_id", "")
                    strategy_dao = AppContext().strategy_api.get_by_id(sid)
                    strategy_name: str = ""
                    if strategy_dao:
                        strategy_name = strategy_dao.get("name", "")
                    time_str: str = str(sig.get("create_time", ""))[:16]
                    color: str = "#34d399" if action == "卖出" else "#f87171"
                    with ui.row().classes("w-full items-start gap-4"):
                        with ui.element("div").classes("w-2 h-2 mt-2 rounded-full").style(
                            f"background-color: {color}; min-width: 8px;"
                        ):
                            pass
                        with ui.column().classes("gap-1").style("flex: 1;"):
                            ui.label(strategy_name).classes("text-sm font-semibold").style("color: #e5e7eb;")
                            ui.label(f"触发{action}信号: {code}").classes("text-xs").style(f"color: {color};")
                            ui.label(time_str).classes("text-xs").style("color: #6b7280;")

        _render()

        # 定时刷新
        ui.timer(1.0, _render)

        ui.button("查看全部活动", on_click=lambda: ui.navigate.to("/strategy-monitor")).props(
            "flat"
        ).classes("w-full mt-1").style(
            "color: #60a5fa; font-size: 14px; font-weight: 500;"
        )


def _build_recent_strategies_table(
    card_bg: str,
    border_color: str,
    font_color: str,
    positive_color: str,
    negative_color: str,
) -> None:
    """构建近期更新策略表格（策略名称 / 类型 / 创建日期 / 状态 / 收益率）。

    数据来源：user_strategy_api.list() 用户策略关联 + list_executions()
    执行结果集（取每个用户策略最新一条记录的收益率），
    策略名称与类型通过 strategy_api.get_by_id() 补充。

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
            ui.label("近期更新策略").classes("text-lg font-bold").style("color: #f3f4f6;")
            ui.button("管理所有策略", on_click=lambda: ui.navigate.to("/strategy-monitor")).props(
                "flat dense"
            ).style("color: #60a5fa; font-size: 14px;")

        table_container: ui.column = ui.column().classes("w-full gap-0")

        # 用户策略ID → 最新执行时间（用于判断哪条执行记录最新）
        latest_time: dict[str, str] = {}

        def _fetch_table_data() -> tuple[
            list[dict[str, Any]], dict[str, float], dict[str, dict[str, Any]],
        ]:
            """拉取表格数据。

            :return: (用户策略列表按创建时间倒序,
                      用户策略ID → 最新收益率百分数,
                      策略模板ID → 策略模板信息)
            """
            try:
                user_strategies: list[dict[str, Any]] = AppContext().user_strategy_api.list()
            except Exception:
                logger.warning("查询用户策略列表失败", exc_info=True)
                user_strategies = []
            user_strategies.sort(key=lambda s: str(s.get("create_time", "")), reverse=True)

            # 用户策略ID → 最新收益率（%），来自执行结果集中时间最新的一条
            latest_rate: dict[str, float] = {}
            latest_time.clear()
            executions: list[dict[str, Any]] = []
            try:
                executions = AppContext().user_strategy_api.list_executions()
            except Exception:
                logger.warning("查询策略执行结果失败", exc_info=True)
            for ex in executions:
                us_id: str = str(ex.get("user_strategy_id", ""))
                if not us_id:
                    continue
                ex_time: str = str(ex.get("update_time") or ex.get("create_time") or "")
                try:
                    rate_pct: float = float(ex.get("current_return_rate", 0) or 0) * 100.0
                except (TypeError, ValueError):
                    continue
                if us_id not in latest_rate or ex_time >= latest_time.get(us_id, ""):
                    latest_rate[us_id] = rate_pct
                    latest_time[us_id] = ex_time

            # 策略模板信息缓存
            templates: dict[str, dict[str, Any]] = {}
            for us in user_strategies[:5]:
                sid: str = str(us.get("strategy_id", ""))
                if sid and sid not in templates:
                    try:
                        templates[sid] = AppContext().strategy_api.get_by_id(sid)
                    except Exception:  # noqa: BLE001
                        templates[sid] = {}
            return user_strategies, latest_rate, templates

        def _render() -> None:
            """渲染表格内容。"""
            table_container.clear()
            with table_container:
                user_strategies, latest_rate, templates = _fetch_table_data()
                if not user_strategies:
                    with ui.column().classes("w-full items-center justify-center").style("padding: 40px;"):
                        ui.icon("inbox").style("color: #6b7280; font-size: 36px;")
                        ui.label("暂无策略数据").classes("text-sm").style(
                            "color: #9ca3af; margin-top: 8px;"
                        )
                    return

                rows: list[dict[str, Any]] = user_strategies[:5]

                # 表头
                with ui.row().classes("w-full items-center").style(
                    f"background-color: #111827; border-bottom: 1px solid {border_color}; "
                    f"padding: 12px 24px;"
                ):
                    for hdr, flex in [
                        ("策略名称", 2), ("类型", 1), ("创建日期", 1),
                        ("状态", 1), ("收益率 (30d)", 1),
                    ]:
                        ui.label(hdr).classes(
                            "text-xs font-semibold uppercase tracking-wider"
                        ).style(f"color: #9ca3af; flex: {flex};")

                for idx, us in enumerate(rows):
                    us_id: str = str(us.get("_id", "") or us.get("id", ""))
                    sid: str = str(us.get("strategy_id", ""))
                    tpl: dict[str, Any] = templates.get(sid, {})
                    name: str = str(tpl.get("name", "") or sid or "未命名策略")
                    stype: str = str(tpl.get("strategy_type", "") or "-")
                    create_date: str = str(us.get("create_time", ""))[:10] or "-"
                    status: str = str(us.get("status", "stopped"))
                    status_text: str
                    status_color: str
                    status_bg: str
                    status_text, status_color, status_bg = _STATUS_STYLES.get(
                        status, ("未知", "#d1d5db", "rgba(55,65,81,0.8)"),
                    )
                    rate_pct: float | None = latest_rate.get(us_id)
                    row_bg: str = "#1e293b" if idx % 2 == 0 else "#19222f"

                    with ui.row().classes("w-full items-center").style(
                        f"padding: 16px 24px; background-color: {row_bg}; "
                        f"border-bottom: 1px solid rgba(45,55,72,0.6);"
                    ):
                        ui.label(name).classes("text-sm font-medium").style(
                            "color: #e5e7eb; flex: 2;"
                        )
                        ui.label(stype).classes("text-sm").style("color: #9ca3af; flex: 1;")
                        ui.label(create_date).classes("text-sm").style("color: #6b7280; flex: 1;")
                        with ui.row().classes("items-center").style("flex: 1;"):
                            ui.label(status_text).classes(
                                "text-xs font-medium rounded-full px-2.5 py-0.5"
                            ).style(
                                f"color: {status_color}; background-color: {status_bg};"
                            )
                        if rate_pct is None:
                            ui.label("-").classes("text-sm font-semibold").style(
                                "color: #6b7280; flex: 1;"
                            )
                        else:
                            # 中国股市习惯：涨红跌绿
                            rate_color: str = (
                                negative_color if rate_pct > 0
                                else (positive_color if rate_pct < 0 else font_color)
                            )
                            ui.label(f"{rate_pct:+.1f}%").classes("text-sm font-semibold").style(
                                f"color: {rate_color}; flex: 1;"
                            )

        _render()

        # 定时刷新（状态/收益变化）
        ui.timer(60.0, _render)
