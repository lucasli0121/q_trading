#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-09-07
Description: 策略选股页面 — 顶部 Tab 为用户策略，点击 Tab 查询该策略当日选股内容。
  数据来源：
  - Tab 列表：UserStrategyManager 会话缓存（user_strategy 记录），每条取自身 ID
    （_id/id）作为 user_strategy_id；显示名称优先用 user_strategy.name
    （自定义名称），未设置时回退策略模板名（走 strategy_cache 进程级缓存）。
  - 选股行情：strategy_select_stock_api.list(user_strategy_id=xxx, 当天) 返回
    StrategySelectStockWithRtItem，已自带实时行情字段（name/price/
    change_percent/change_amount/turnover/score 等）；后端默认按 score 正向
    排序并已做同 code 去重，故前端不再自行去重/排序，仅展示分数（保留 2 位小数）。
    仅动态市盈率（ttm_pe）与利润增长率（net_profit_growth_rate）需 finance_api 补充。
  注意：选股结果以 user_strategy_id（用户策略实例）关联，不再使用 strategy_id
  （策略模板 ID）。
  颜色遵循 theme 新约定：positive=var(--qs-positive) 红=涨，negative=var(--qs-negative) 绿=跌
  （中国股市红涨绿跌）。切换 Tab/刷新均有加载进度显示，401 静默等待跳转登录。
  首屏不阻塞：页面渲染完成后用一次性定时器异步拉取（见页面末尾）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx
from nicegui import ui

from api.client import UnauthorizedError
from app_context import AppContext
from components import custom_tabs
from core.concurrency import run_blocking
from core.user_strategy_manager import UserStrategyManager

logger = logging.getLogger(__name__)


def _sf(value: Any) -> float:
    """安全转 float。

    :param value: 待转换值
    :return: float
    """
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0


def _today_range() -> tuple[str, str]:
    """获取当天起止时间字符串。

    注意：数据库 create_time 只保存日期（YYYY-MM-DD），不带时分秒，
    后端文档写的 "YYYY-MM-DD HH:MM:SS" 有误，故此处只传日期。

    :return: (当天日期, 当天日期)，格式 YYYY-MM-DD
    """
    today: str = datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005
    return f"{today}", f"{today}"


def show_strategy_select_page() -> None:
    """策略选股页面入口 — Tab 显示用户策略，点击查询该策略当日选股内容。"""
    custom_tabs.load_page_tab_css()
    theme_manager = AppContext().theme_manager
    bg_color: str = theme_manager.var("page")
    border_color: str = theme_manager.var("border")
    # theme 新约定：positive=红=涨，negative=绿=跌（中国股市红涨绿跌）
    positive_color: str = theme_manager.var("positive")   # 涨 红
    negative_color: str = theme_manager.var("negative")   # 跌 绿
    card_bg: str = "var(--qs-surface)"
    input_bg: str = "var(--qs-surface-alt)"
    muted: str = "var(--qs-muted)"
    normal: str = "var(--qs-text)"

    # ---- 1. 查询用户策略列表，作为 Tab 来源 ----
    user_strategies: list[dict[str, Any]] = []
    user_strategies = UserStrategyManager().list_user_strategies()

    # ---- 2. 为每个策略模板补充名称（仅当该用户策略未设置自定义名称时用到） ----
    name_cache: dict[str, str] = {}

    def _get_template_name(strategy_id: str) -> str:
        """获取策略模板名称（走进程级缓存 strategy_cache，失败回退 ID）。

        :param strategy_id: 策略模板 ID
        :return: 策略模板名称
        """
        if not strategy_id:
            return "未命名策略"
        if strategy_id in name_cache:
            return name_cache[strategy_id]
        name: str = str(AppContext().strategy_cache.get(strategy_id).get("name", "") or "")
        if not name:
            name = strategy_id
        name_cache[strategy_id] = name
        return name

    def _get_display_name(us: dict[str, Any]) -> str:
        """获取用户策略的展示名称。

        优先 user_strategy.name（自定义名称），未设置时回退策略模板名，
        历史数据兜底为"未命名策略"。

        :param us: 用户策略记录
        :return: 展示名称
        """
        custom: str = str(us.get("name", "") or "").strip()
        if custom:
            return custom
        sid: str = str(us.get("strategy_id", "") or "")
        if not sid:
            return "未命名策略"
        return _get_template_name(sid)

    # Tab 项：(用户策略 ID, 展示名称)。按用户策略实例逐个展示，
    # 同一策略模板可关联多次，各自独立选股，故不去重。
    tab_items: list[tuple[str, str]] = []
    for us in user_strategies:
        us_id: str = str(us.get("_id", "") or us.get("id", "") or "")
        if us_id:
            tab_items.append((us_id, _get_display_name(us)))

    active_us_id: str = tab_items[0][0] if tab_items else ""
    active_tab: str = tab_items[0][1] if tab_items else ""

    state: dict[str, Any] = {
        "quotes": [], "active_tab": active_tab,
        "active_sid": active_us_id,
        # 有首个策略时先置为加载中，首屏由异步任务填充（见页面末尾）
        "loading": bool(active_us_id),
    }

    # 行情缓存：user_strategy_id -> list[dict]，避免重复请求
    quotes_cache: dict[str, list[dict[str, Any]]] = {}

    def _load_quotes(user_strategy_id: str) -> list[dict[str, Any]]:
        """查询指定用户策略当日选股行情（list 接口直接传 user_strategy_id）。

        list 返回的 StrategySelectStockWithRtItem 已含 name/price/
        change_percent/change_amount/turnover 等实时行情字段，
        仅动态市盈率（ttm_pe）与利润增长率（net_profit_growth_rate）
        需通过 finance_api 补充。

        :param user_strategy_id: 用户策略 ID（UserStrategy 记录 ID）
        :return: 行情数据列表
        """
        if not user_strategy_id:
            return []
        if user_strategy_id in quotes_cache:
            return quotes_cache[user_strategy_id]
        start_time, end_time = _today_range()
        try:
            data: list[dict[str, Any]] = AppContext().strategy_select_stock_api.list(
                user_strategy_id=user_strategy_id, start_time=start_time, end_time=end_time,
            )
        except UnauthorizedError:
            logger.debug("查询策略「%s」选股未授权（401），等待跳转登录", user_strategy_id)
            return []
        except httpx.HTTPStatusError as e:
            logger.warning(
                "查询策略「%s」选股失败: %s", user_strategy_id, e.response.text, exc_info=True
            )
            return []
        except Exception:
            logger.warning("查询策略「%s」选股失败", user_strategy_id, exc_info=True)
            return []
        if not data:
            return []
        rows: list[dict[str, Any]] = list(data)
        # 说明（CR-12/CR-13）：后端已按 score 正向排序并对同 code 去重，
        # 前端不再自行"保留首条"去重——那会让展示的 price/rt_update_time
        # 依赖后端返回顺序而在刷新之间抖动。排序同样交给后端，前端只展示分数。
        #
        # 代码列表按返回顺序去重（仅供批量补充估值/利润使用，
        # 保证请求参数确定可复现；CR-14：原实现用 set 拼接，顺序不确定）
        codes_list: list[str] = []
        seen_codes: set[str] = set()
        for rec in rows:
            c: str = str(rec.get("code", "") or "")
            if c and c not in seen_codes:
                seen_codes.add(c)
                codes_list.append(c)
        # 补充估值/利润数据（返回数据未含这两个字段）
        codes_str: str = ",".join(codes_list)
        if codes_str:
            # 补充估值数据（动态市盈率）
            # 注：后端 get_valuation 内部按 report_date 排序，参数顺序不影响结果
            try:
                valuation: dict[str, Any] = AppContext().finance_api.get_valuation(codes=codes_str)
                if valuation:
                    for item in rows:
                        item_code: str = str(item.get("code", ""))
                        val: dict[str, Any] | None = valuation.get(item_code) if item_code else None
                        if isinstance(val, dict) and not item.get("ttm_pe"):
                            item["ttm_pe"] = val.get("ttm_pe", 0)
            except UnauthorizedError:
                logger.debug("加载估值数据未授权（401），等待跳转登录")
            except Exception:
                logger.warning("加载估值数据失败", exc_info=True)
            # 补充利润数据（利润增长率）
            try:
                profit: dict[str, Any] = AppContext().finance_api.get_profit(codes=codes_str)
                if profit:
                    for item in rows:
                        item_code = str(item.get("code", ""))
                        pft: dict[str, Any] | None = profit.get(item_code) if item_code else None
                        if isinstance(pft, dict) and not item.get("net_profit_growth_rate"):
                            item["net_profit_growth_rate"] = pft.get("net_profit_growth_rate", 0)
            except UnauthorizedError:
                logger.debug("加载利润数据未授权（401），等待跳转登录")
            except Exception:
                logger.warning("加载利润数据失败", exc_info=True)
        quotes_cache[user_strategy_id] = rows
        return rows

    async def on_tab_switch(user_strategy_id: str, tab_name: str) -> None:
        """切换策略 Tab 并加载对应选股行情（异步，显示加载进度）。

        :param user_strategy_id: 用户策略 ID（UserStrategy 记录 ID）
        :param tab_name: Tab 展示名称
        """
        state["active_sid"] = user_strategy_id
        state["active_tab"] = tab_name
        state["loading"] = True
        tab_bar.refresh()
        table_panel.refresh()
        await asyncio.sleep(0)
        # 切换 Tab 时强制重新拉取（行情可能已更新）
        quotes_cache.pop(user_strategy_id, None)
        # run_blocking 会继承请求上下文，保证线程池内仍能取到本会话 token
        state["quotes"] = await run_blocking(_load_quotes, user_strategy_id)
        state["loading"] = False
        table_panel.refresh()

    async def on_refresh() -> None:
        """刷新当前 Tab 的选股行情。"""
        cur_sid: str = str(state.get("active_sid", ""))
        if not cur_sid:
            return
        state["loading"] = True
        table_panel.refresh()
        await asyncio.sleep(0)
        quotes_cache.pop(cur_sid, None)
        state["quotes"] = await run_blocking(_load_quotes, cur_sid)
        state["loading"] = False
        table_panel.refresh()

    # 初始加载首个策略行情：不在此处同步发起 HTTP（会阻塞事件循环、拖慢首屏），
    # 改为页面渲染完成后用一次性定时器异步加载（CR-15）。
    # 注意：这里是 async 回调，run_blocking 会继承请求上下文，token 解析正常。

    # ---- 顶部栏 ----
    with ui.row().classes("w-full items-center justify-between").style(
        f"height: 64px; padding: 0 32px; background-color: {card_bg}; "
        f"border-bottom: 1px solid {border_color};"
    ):
        ui.label("策略选股").classes("text-xl font-bold").style("color: var(--qs-heading);")
        with ui.row().classes("items-center gap-4"):
            today_str: str = datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005
            ui.label(f"今日选股 · {today_str}").classes("text-sm").style(f"color: {muted};")
            ui.button(icon="refresh", on_click=on_refresh).props("flat round dense").style(
                f"color: {muted}; background-color: {input_bg};"
            )

    # ---- 页面主体 ----
    with ui.column().classes("w-full gap-0").style(
        f"padding: 32px; background-color: {bg_color}; overflow-y: auto; flex: 1;"
    ):
        # ---- 策略 Tab ----
        @ui.refreshable
        def tab_bar() -> None:
            """渲染策略 Tab 标签栏，选中态随 active_tab 刷新。"""
            with ui.row().classes("w-full items-center gap-4").style(
                f"border-bottom: 1px solid {border_color}; margin-bottom: 24px;"
            ):
                if tab_items:
                    for us_id, label in tab_items:
                        # 用 user_strategy_id 判重（同名策略可能关联多次）
                        is_active: bool = (us_id == state["active_sid"])
                        if is_active:
                            ui.label(label).classes("text-sm font-bold").style(
                                "color: var(--qs-link); border-bottom: 2px solid var(--qs-primary); "
                                "padding: 12px 24px; cursor: pointer;"
                            ).on("click", lambda _u=us_id, _l=label: on_tab_switch(_u, _l))
                        else:
                            ui.label(label).classes("text-sm").style(
                                f"color: {muted}; border-bottom: 2px solid transparent; "
                                "padding: 12px 24px; cursor: pointer;"
                            ).on("click", lambda _u=us_id, _l=label: on_tab_switch(_u, _l))
                else:
                    ui.label("暂无策略，请先在策略列表中添加策略").classes("text-sm").style(
                        f"color: {muted}; padding: 12px 24px;"
                    )

        tab_bar()

        # ---- 选股行情表格 ----
        @ui.refreshable
        def table_panel() -> None:
            """渲染选股行情表格，加载中时显示进度动画。"""
            quotes: list[dict[str, Any]] = state["quotes"]

            with ui.card().classes("w-full gap-0 overflow-hidden").style(
                f"background-color: {card_bg}; border: 1px solid {border_color}; border-radius: 12px;"
            ):
                # 表头（选股评分紧随代码/名称，因为后端默认按 score 正向排序）
                headers: list[tuple[str, str]] = [
                    ("代码/名称", "left"), ("选股评分", "left"), ("最新价", "left"),
                    ("涨跌幅", "left"), ("涨跌额", "left"), ("换手率", "left"),
                    ("动态市盈率", "left"), ("利润增长率", "left"),
                ]
                with ui.row().classes("w-full items-center").style(
                    f"background-color: {input_bg}; border-bottom: 1px solid {border_color}; "
                    f"padding: 12px 24px;"
                ):
                    for hdr, _align in headers:
                        flex_val: str = "2" if hdr == "代码/名称" else "1"
                        ui.label(hdr).classes("text-xs font-semibold uppercase tracking-wider").style(
                            f"color: {muted}; flex: {flex_val};"
                        )

                # 加载进度
                if state.get("loading", False):
                    with ui.row().classes("w-full justify-center items-center gap-3").style("padding: 48px;"):
                        ui.spinner(size="md", color="blue")
                        ui.label("正在加载选股行情...").classes("text-sm").style(f"color: {muted};")
                    return

                # 无数据
                if not quotes:
                    with ui.row().classes("w-full justify-center items-center").style("padding: 48px;"):
                        ui.label("该策略今日暂无选股结果").classes("text-sm").style(f"color: {muted};")
                    return

                # 数据行
                for q in quotes:
                    code: str = str(q.get("code", ""))
                    name: str = str(q.get("name", ""))
                    # 选股评分（后端按此字段正向排序，前端仅展示，保留 2 位小数）
                    score: float = _sf(q.get("score", 0))
                    price: float = _sf(q.get("price", q.get("close", 0)))
                    chg_pct: float = _sf(q.get("change_pct", q.get("change_percent", 0))) * 100
                    chg_amt: float = _sf(q.get("change_amt", q.get("change_amount", price * chg_pct / 100)))
                    to_rate: float = _sf(q.get("turnover", q.get("turnover_rate", 0))) * 100
                    dynamic_pe: float = _sf(q.get("ttm_pe", 0))
                    profit_growth: float = _sf(q.get("net_profit_growth_rate", 0))

                    if chg_pct > 0:
                        price_color: str = positive_color  # 涨为红
                    elif chg_pct < 0:
                        price_color = negative_color  # 跌为绿
                    else:
                        price_color = "var(--qs-on-accent)"  # 平为白

                    with ui.row().classes("w-full items-center").style(
                        "padding: 16px 24px; border-bottom: 1px solid var(--qs-divider);"
                    ):
                        # 代码/名称
                        with ui.column().classes("gap-0").style("flex: 2;"):
                            ui.label(code).classes("font-bold tracking-wider").style(f"color: {normal};")
                            ui.label(name).classes("text-xs").style("color: var(--qs-faint);")
                        ui.label(f"{score:.2f}").classes("font-medium").style(f"color: {normal}; flex: 1;")
                        ui.label(f"{price:,.2f}").classes("font-bold").style(f"color: {price_color}; flex: 1;")
                        sign: str = "+" if chg_pct >= 0 else ""
                        ui.label(f"{sign}{chg_pct:.2f}%").classes("font-bold").style(f"color: {price_color}; flex: 1;")
                        ui.label(f"{sign}{chg_amt:,.2f}").classes("font-medium").style(f"color: {price_color}; flex: 1;")
                        ui.label(f"{to_rate:.2f}%").classes("text-sm").style(f"color: {muted}; flex: 1;")
                        pe_text: str = "亏损" if dynamic_pe < 0 else f"{dynamic_pe:.1f}"
                        ui.label(pe_text).classes("text-sm").style(f"color: {muted}; flex: 1;")
                        ui.label(f"{profit_growth:.1f}%").classes("text-sm").style(f"color: {muted}; flex: 1;")

        table_panel()

        # ---- 首屏异步加载（CR-15） ----
        # 页面构建阶段同步发起 3 个 HTTP 会阻塞 asyncio 事件循环、拖慢所有用户；
        # 这里改为渲染完成后用一次性定时器异步拉取，期间表格显示加载进度。
        if active_us_id:
            async def _load_initial() -> None:
                """异步加载首个 Tab 的选股行情。"""
                state["quotes"] = await run_blocking(_load_quotes, active_us_id)
                state["loading"] = False
                table_panel.refresh()

            ui.timer(0.05, _load_initial, once=True)
