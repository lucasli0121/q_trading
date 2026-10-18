#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-07-11
LastEditors: liguoqiang
LastEditTime: 2026-08-03
Description: 个人喜好设置页面 — 通过 /api/user/preference 接口查询和保存用户偏好
    字段: theme_mode / enable_wx_push / wx_push_url / enable_phone_text / phone /
    push_buy_min_star / push_sell_min_star

    两个星级（买入 / 卖出信号推送的最小星级）是**两个推送通道共用**的：企业微信与短信
    任一打开就显示这一组滑块，两者都关时整块隐藏（此时改它也没有意义）。
"""
from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

from app_context import AppContext
from colors.theme import resolve_mode_from_prefs

logger = logging.getLogger(__name__)

# /api/user/preference 请求体字段（PUT 为全量替换，提交前只保留这些字段，
# 避免把服务端响应里的 id / user_id / update_time 一并回传）
_PREF_FIELDS: tuple[str, ...] = (
    "theme_mode",
    "enable_wx_push",
    "wx_push_url",
    "enable_phone_text",
    "phone",
    "push_buy_min_star",
    "push_sell_min_star",
)

# 推送星级上限与步长，与服务端 web_api/routers/user_preference.py 的 MAX_PUSH_STAR 一致
# （服务端 0~5 且保留一位小数，超范围会被 400 拒绝）
_MAX_PUSH_STAR: float = 5.0
_STAR_STEP: float = 0.5


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


def _star_val(value: Any) -> float:
    """安全转星级，落在 0~5 之间并保留一位小数。

    后端对这两个字段是「先校验再落库」，超范围直接 400；前端同样要夹紧，
    否则一个脏数据（字符串 / 负数 / 旧记录里的 None）就会让保存整条失败。

    :param value: 待转换值
    :return: 0~5 之间、一位小数的星级；无法解析时按 0（不限）处理
    """
    try:
        star: float = float(value)
    except (TypeError, ValueError):
        return 0.0
    if star < 0.0:
        return 0.0
    if star > _MAX_PUSH_STAR:
        return _MAX_PUSH_STAR
    return round(star, 1)


def _star_text(value: float) -> str:
    """星级显示文案：0 表示不限，其余显示「N.N 星」。

    :param value: 星级
    :return: 展示文案
    """
    return "不限" if value <= 0 else f"{value:.1f} 星"


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
    theme_manager = AppContext().theme_manager

    # 加载已有偏好
    saved_prefs: dict[str, Any] = _load_preferences()
    # 读取失败 / 无记录时为空字典：此时不能做「只改主题」的全量提交，
    # 否则会把服务端其余字段一并覆盖成默认值。
    prefs_available: bool = bool(saved_prefs)

    # 以服务端偏好为准对齐本会话主题：页面由 main_page 渲染时已应用过一次主题，
    # 这里把「后端记录值」与「会话当前值」拉平，避免两处不一致。
    # resolve_mode_from_prefs 会区分"用户真的选过"与"记录不存在时的默认值"。
    chosen: str | None = resolve_mode_from_prefs(saved_prefs) or theme_manager.session_mode()
    live_mode: str = theme_manager.normalize_mode(chosen)
    theme_manager.set_theme(live_mode)
    theme_manager.remember_mode(live_mode)

    # 服务端偏好快照：切换主题时只改动其中 theme_mode 一项再整体提交
    # （preference_api.update 是全量替换语义）
    server_prefs: dict[str, Any] = {**saved_prefs, "theme_mode": live_mode}

    bg_color: str = theme_manager.var("page")
    font_color: str = theme_manager.var("text")
    border_color: str = "var(--qs-border)"
    card_bg: str = "var(--qs-surface)"
    input_bg: str = "var(--qs-surface-alt)"

    # 可变的绑定变量（列表包装以支持闭包内修改）
    state: dict[str, Any] = {
        "theme_mode": live_mode,
        "enable_wx_push": _bool_val(saved_prefs.get("enable_wx_push", False)),
        "wx_push_url": saved_prefs.get("wx_push_url", ""),
        "enable_phone_text": _bool_val(saved_prefs.get("enable_phone_text", False)),
        "phone": saved_prefs.get("phone", ""),
        "push_buy_min_star": _star_val(saved_prefs.get("push_buy_min_star", 0.0)),
        "push_sell_min_star": _star_val(saved_prefs.get("push_sell_min_star", 0.0)),
    }
    # 两个星级滑块右侧的文案标签（拖动后要改文案，留引用）
    star_text_refs: dict[str, Any] = {}

    def on_save() -> None:
        """保存更改到 API。"""
        prefs: dict[str, Any] = {
            "theme_mode": state["theme_mode"],
            "enable_wx_push": state["enable_wx_push"],
            "wx_push_url": state["wx_push_url"],
            "enable_phone_text": state["enable_phone_text"],
            "phone": state["phone"],
            "push_buy_min_star": state["push_buy_min_star"],
            "push_sell_min_star": state["push_sell_min_star"],
        }
        if _save_preferences(prefs):
            server_prefs.update(prefs)
            theme_manager.remember_mode(prefs["theme_mode"])
            ui.notify("设置已保存", type="positive")
        else:
            ui.notify("保存失败，请稍后重试", type="negative")

    def on_toggle_dark() -> None:
        """切换深色 / 浅色主题：即时生效并立即落库。

        主题是「即时生效」型开关（开关本身就已表达用户意图），因此不等
        「保存更改」；否则用户切完主题一翻页就又变回去，会被当成 bug。
        由于界面颜色全部走 CSS 变量，切换后当前页面无需重新渲染即整体变色。
        """
        mode: str = theme_manager.toggle()
        state["theme_mode"] = mode
        theme_manager.remember_mode(mode)
        label: str = "深色" if mode == "dark" else "浅色"

        if not prefs_available:
            ui.notify(f"已切换到{label}主题（未能保存到服务器）", type="warning")
            return

        server_prefs["theme_mode"] = mode
        payload: dict[str, Any] = {
            key: server_prefs[key] for key in _PREF_FIELDS if key in server_prefs
        }
        # 星级字段在**服务端旧记录**里可能还没有（PUT 是全量替换，缺字段会被当成 0）；
        # 此时用页面当前值补上，别把用户刚拖好的星级冲掉。
        for key in ("push_buy_min_star", "push_sell_min_star"):
            if payload.get(key) is None:
                payload[key] = state[key]
        if _save_preferences(payload):
            ui.notify(f"已切换到{label}主题", type="positive")
        else:
            ui.notify(f"已切换到{label}主题，但保存失败；下次登录可能回到原主题", type="warning")

    def render_body(close_fn: Any = None) -> None:
        """渲染主体内容。"""
        # 顶栏
        with ui.row().classes("w-full items-center justify-between").style(
            f"height: 64px; padding: 0 32px; background-color: {card_bg}; "
            f"border-bottom: 1px solid {border_color};"
        ):
            ui.label("个人喜好设置").classes("text-xl font-bold").style("color: var(--qs-heading);")
            with ui.row().classes("items-center gap-4"):
                ui.button("保存更改", on_click=on_save).props("flat").style(
                    "background-color: var(--qs-primary); color: var(--qs-on-accent) !important; padding: 8px 24px; "
                    "border-radius: 8px; font-size: 14px; font-weight: 500;"
                )
                if close_fn:
                    ui.button(icon="close", on_click=close_fn).props("flat round dense").style(
                        "color: var(--qs-muted);"
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
                    ui.label("界面显示").classes("font-bold").style("color: var(--qs-heading);")

                with ui.column().classes("w-full gap-6").style("padding: 24px;"):  # noqa: SIM117
                    with ui.row().classes("w-full items-center justify-between"):
                        with ui.column().classes("gap-0"):
                            ui.label("深色模式").classes("text-sm font-semibold").style("color: var(--qs-text);")
                            ui.label("切换系统的显示主题风格").classes("text-xs").style("color: var(--qs-faint);")
                        is_dark: bool = theme_manager.is_dark()
                        ui.switch(value=is_dark, on_change=lambda _e: on_toggle_dark()).props(
                            f"color={theme_manager.get_color('primary')}"
                        )

            # ---- 区2: 企业微信推送 ----
            with ui.card().classes("w-full gap-0 overflow-hidden").style(
                f"background-color: {card_bg}; border: 1px solid {border_color}; "
                f"border-radius: 12px; margin-bottom: 32px;"
            ):
                with ui.row().classes("w-full").style(
                    f"padding: 24px; border-bottom: 1px solid {border_color};"
                ):
                    ui.label("企业微信推送").classes("font-bold").style("color: var(--qs-heading);")

                with ui.column().classes("w-full gap-6").style("padding: 24px;"):
                    with ui.row().classes("w-full items-center justify-between"):
                        with ui.column().classes("gap-0"):
                            ui.label("启用推送").classes("text-sm font-semibold").style("color: var(--qs-text);")
                            ui.label("当策略触发买入或卖出信号时，通过企业微信发送通知").classes(
                                "text-xs"
                            ).style("color: var(--qs-faint);")
                        ui.switch(
                            value=state["enable_wx_push"],
                            on_change=lambda e: _update_wx_state(e.value),
                        ).props(f"color={theme_manager.get_color('primary')}")

                    # Webhook URL（仅在启用推送时显示）
                    url_container = ui.column().classes("w-full gap-2")
                    with url_container:
                        ui.label("Webhook 地址").classes("text-sm font-semibold").style("color: var(--qs-text);")
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
                    ui.label("手机短信推送").classes("font-bold").style("color: var(--qs-heading);")

                with ui.column().classes("w-full gap-6").style("padding: 24px;"):
                    with ui.row().classes("w-full items-center justify-between"):
                        with ui.column().classes("gap-0"):
                            ui.label("启用短信推送").classes("text-sm font-semibold").style("color: var(--qs-text);")
                            ui.label("当策略信号触发时，向指定手机号发送短信通知").classes(
                                "text-xs"
                            ).style("color: var(--qs-faint);")
                        ui.switch(
                            value=state["enable_phone_text"],
                            on_change=lambda e: _update_phone_state(e.value),
                        ).props(f"color={theme_manager.get_color('primary')}")

                    # 手机号（仅在启用短信时显示）
                    phone_container = ui.column().classes("w-full gap-2")
                    with phone_container:
                        ui.label("推送手机号").classes("text-sm font-semibold").style("color: var(--qs-text);")
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

            # ---- 区4: 推送星级（两个通道共用，任一开关打开才显示） ----
            star_container = ui.card().classes("w-full gap-0 overflow-hidden").style(
                f"background-color: {card_bg}; border: 1px solid {border_color}; "
                f"border-radius: 12px;"
            )
            with star_container:
                with ui.row().classes("w-full").style(
                    f"padding: 24px; border-bottom: 1px solid {border_color};"
                ):
                    ui.label("推送星级").classes("font-bold").style("color: var(--qs-heading);")

                with ui.column().classes("w-full gap-5").style("padding: 24px;"):
                    ui.label("信号星级低于设定值时不推送；拖到 0 表示不限制星级").classes(
                        "text-xs"
                    ).style("color: var(--qs-faint);")

                    for field, title in (
                        ("push_buy_min_star", "买入信号最小星级"),
                        ("push_sell_min_star", "卖出信号最小星级"),
                    ):
                        with ui.column().classes("w-full gap-1"):
                            with ui.row().classes("w-full items-center justify-between"):
                                ui.label(title).classes("text-sm font-semibold").style(
                                    "color: var(--qs-text);"
                                )
                                star_text = ui.label(_star_text(state[field])).classes(
                                    "text-xs font-semibold"
                                ).style(f"color: {theme_manager.var('primary')};")
                                star_text_refs[field] = star_text
                            (
                                ui.slider(
                                    min=0.0,
                                    max=_MAX_PUSH_STAR,
                                    step=_STAR_STEP,
                                    value=state[field],
                                    on_change=lambda e, _f=field: _update_star(_f, e.value),
                                )
                                .props("label-always")
                                .classes("w-full")
                            )

        def _update_star(field: str, val: Any) -> None:
            """滑块拖动 → 记录星级并刷新右侧文案。"""
            star: float = _star_val(val)
            state[field] = star
            label = star_text_refs.get(field)
            if label is not None:
                label.set_text(_star_text(star))

        def _update_star_visibility() -> None:
            """星级区只在**任一推送通道打开**时显示。"""
            star_container.set_visibility(
                bool(state["enable_wx_push"] or state["enable_phone_text"])
            )

        def _update_wx_state(val: bool) -> None:
            """更新企业微信推送开关状态。"""
            state["enable_wx_push"] = val
            url_container.set_visibility(val)
            _update_star_visibility()

        def _update_phone_state(val: bool) -> None:
            """更新短信推送开关状态。"""
            state["enable_phone_text"] = val
            phone_container.set_visibility(val)
            _update_star_visibility()

        # 初始显隐：两个开关都关时整块隐藏（此时设星级没有意义）
        _update_star_visibility()

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
