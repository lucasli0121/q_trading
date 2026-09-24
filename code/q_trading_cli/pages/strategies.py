#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-08-25
Description: 策略列表页面 — 严格按 strategies.html 原型实现
    - 筛选栏（搜索策略名称/描述 + 策略类型 + 状态 + 重置）
    - 策略表格（名称+描述 / 类型 / 创建时间 / 状态 / 最近信号 / 查看详情）
    - 分页页脚
    - 策略详情面板（持仓信息 / 订单明细 / 运行日志）
    - 操作列「回测记录」入口 → /strategy-backtest?strategy_id=<策略模板ID>&source=strategies
      （带 source 是为了让回测页的「返回」按钮回到本页，而不是回策略监控）
    - 创建策略对话框（策略选择卡片横向滑动 + 股票池配置 / 参数）
      策略模板以卡片呈现（名称 / 类型 / 描述），左上角勾选框选中，选中卡片边框高亮；
      含「运行状态」→「立即运行」勾选框（**默认勾选**）：勾选即以 running 创建，
      取消则以 stopped 创建
    - 编辑策略对话框（自定义名称 / 股票池 / 初始资金 / 最大持仓数 / **运行状态**）
      运行状态可在此直接切换，等价于监控卡片上的启动/暂停/停止

数据来源：
    - 用户策略关联: user_strategy_api.list()
    - 策略模板:     strategy_api.get_by_id() / strategy_api.create()
    - 最近信号列:   SignalManager 内存信号
    - 持仓信息:     user_strategy_api.get_latest_execution().positions
    - 订单明细:     order_api.get_latest(user_strategy_id, 持仓股票代码, action=买入)
    - 运行日志:     user_strategy_api.get_runlog()
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any

import httpx
from nicegui import ui

from app_context import AppContext
from core.routes import backtest_route
from core.user_strategy_manager import UserStrategyManager
from utils import tools
from utils.timeutils import cur_date_time_str

logger = logging.getLogger(__name__)

# 分页每页条数
PAGE_SIZE: int = 10

# 用户策略状态 → (显示文本, 文字颜色, 背景颜色)
_STATUS_STYLES: dict[str, tuple[str, str, str]] = {
    "running": ("运行中", "var(--qs-negative)", "rgba(var(--qs-negative-rgb), 0.3)"),
    "stopped": ("已停止", "var(--qs-label)", "var(--qs-chip-neutral)"),
    "paused": ("已暂停", "var(--qs-accent)", "rgba(var(--qs-accent-rgb), 0.25)"),
    "error": ("异常", "var(--qs-positive)", "rgba(var(--qs-positive-rgb), 0.3)"),
}

# 编辑对话框可切换的运行状态：文案取自 _STATUS_STYLES（保持单一来源），
# 但**排除 error** —— 异常是后端检测出的故障态，不作为用户可选项目。
# 若某条记录当前状态恰为「异常」，该状态会被临时补进下拉选项
# （见 _open_edit_strategy_dialog），否则下拉框会显示为空白且保存时会把状态悄悄改掉。
_EDITABLE_STATUS_KEYS: tuple[str, ...] = ("running", "paused", "stopped")

# 运行状态码 → 显示文案（与 api/user_strategy.py 支持的状态值一致）
_EDITABLE_STATUS_TEXTS: dict[str, str] = {
    key: _STATUS_STYLES.get(key, (key,))[0] for key in _EDITABLE_STATUS_KEYS
}

# 创建对话框中的候选策略类型（与 StrategyDao.strategy_type 取值一致）
_STRATEGY_TYPES: list[str] = ["选股策略", "盯盘策略", "复盘策略"]

# 策略默认参数分组 key → 显示标题
_PARAM_GROUP_LABELS: dict[str, str] = {
    "match": "选股参数",
    "buy": "买入参数",
    "sell": "卖出参数",
}


def _is_numeric_value(value: Any) -> bool:
    """判断参数默认值是否为数字类型（决定使用数字输入框还是文本输入框）。

    :param value: 参数默认值
    :return: 数字类型返回 True，否则返回 False
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        stripped: str = value.strip()
        if not stripped:
            return False
        try:
            float(stripped)
            return True
        except ValueError:
            return False
    return False


def _render_params_container(
    container: Any,
    params: dict[str, Any],
    saved: dict[str, Any] | None,
    input_bg: str,
    font_color: str,
    title_color: str,
    muted_color: str,
) -> dict[str, dict[str, Any]]:
    """在目标容器内渲染策略参数编辑区域。

    依据 default_params 结构中的分组（match/buy/sell）逐组渲染：
    每个参数显示 desc 作为标签，默认值为 value；若 saved 中已有用户保存的值则优先使用。
    数字值渲染为数字输入框，非数字值渲染为文本输入框。

    :param container: 目标容器（NiceGUI column / tab_panel）
    :param params: 策略模板的 default_params 字典
    :param saved: 用户已保存的 strategy_params（可选，None 表示使用默认值）
    :param input_bg: 输入框背景色
    :param font_color: 输入框文字颜色
    :param title_color: 分组标题文字颜色
    :param muted_color: 参数标签/占位文字颜色
    :return: 参数收集器 {group: {key: {"input": 控件, "is_number": bool}}}
    """
    saved = saved or {}
    collector: dict[str, dict[str, Any]] = {}
    if not params:
        with container:
            ui.label("该策略暂无参数配置").classes("text-sm").style(f"color: {muted_color};")
        return collector
    with container:
        for group_key, group_params in (params or {}).items():
            group_label: str = _PARAM_GROUP_LABELS.get(str(group_key), str(group_key))
            ui.label(group_label).classes("text-base font-semibold").style(
                f"color: {title_color}; margin-top: 16px;"
            )
            collector[str(group_key)] = {}
            for key, item in (group_params or {}).items():
                desc: str = str(item.get("desc", key) or key)
                default_value: Any = item.get("value", 0.0)
                saved_group: dict[str, Any] = saved.get(str(group_key)) or {}
                current_val: Any = saved_group.get(key, default_value)
                is_number: bool = _is_numeric_value(default_value)
                with ui.row().classes("w-full items-center gap-3").style("padding: 8px 0;"):
                    ui.label(desc).classes("w-[45%] text-sm").style(f"color: {muted_color};")
                    if is_number:
                        num = ui.number(
                            value=float(current_val or 0.0),
                        ).classes("flex-1").props("outlined dense").style(
                            f"background-color: {input_bg}; color: {font_color}; width: 100%;"
                        )
                        collector[str(group_key)][str(key)] = {
                            "input": num, "is_number": True,
                        }
                    else:
                        txt = ui.input(
                            value=str(current_val if current_val is not None else ""),
                        ).classes("flex-1").props("outlined dense").style(
                            f"background-color: {input_bg}; color: {font_color}; width: 100%;"
                        )
                        collector[str(group_key)][str(key)] = {
                            "input": txt, "is_number": False,
                        }
    return collector


def _collect_params(collector: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """从参数收集器中提取用户填写的参数结果字典。

    :param collector: _render_params_container 返回的收集器
    :return: {group: {key: value}}
    """
    result: dict[str, dict[str, Any]] = {}
    for group_key, items in collector.items():
        result[group_key] = {}
        for key, meta in items.items():
            result[group_key][key] = meta["input"].value
    return result


# 对话框高度上限（另受视口约束）
_DIALOG_MAX_HEIGHT_PX: int = 860
# 对话框在视口上下留白的合计（计算 100vh 可分配高度时需减掉）
_DIALOG_VIEWPORT_MARGIN_PX: int = 40


def _dialog_card_height() -> str:
    """对话框卡片的高度表达式 —— **定高**，而不是 min/max-height 夹出来的高度。

    为什么必须是定高：Quasar 的 QScrollArea 自带 ``contain: strict``（含 size containment），
    它自身的**固有高度恒为 0**，只能靠祖先链层层给出确定高度。而"确定高度"要求卡片自身的
    height 明确；``min-height`` 不构成确定高度（卡片 height 仍由内容决定），于是 flex 子项的
    主尺寸在规范上是"不确定"的：``height: 100%`` 解析失败、``flex-basis: 0%`` 退化成内容高度
    （被 size containment 压成 0），滚动区塌成 0 高并被 paint containment 整块裁掉
    —— 表现就是"对话框内容全部消失，连滚动条都没有"。
    卡片定高后，各级容器都有确定高度，``flex-basis: 0% + flex-grow: 1`` 的链才传得下去。

    :return: CSS height 表达式，如 "min(calc(100vh - 40px), 860px)"
    """
    return f"min(calc(100vh - {_DIALOG_VIEWPORT_MARGIN_PX}px), {_DIALOG_MAX_HEIGHT_PX}px)"
# 对话框宽度（创建 / 编辑共用，须与卡片上的 Tailwind 类 "w-[760px] max-w-[95vw]" 一致）
_DIALOG_WIDTH_PX: int = 760
# 卡片横向滚动行需要扣掉的横向占用：卡片边框(2) + QScrollArea 内容层内边距(32) + 冗余
_CARD_ROW_SIDE_PX: int = 40


def _card_row_width() -> str:
    """策略卡片横向滚动行的宽度表达式（显式定宽，不能依赖父容器）。

    为什么必须显式定宽：``ui.scroll_area`` 的内容层被 NiceGUI 设置为
    ``display:flex; flex-direction:column; align-items:flex-start``（见 nicegui.css），
    是一个「收缩到内容宽度」的容器。放在它内部的 ``overflow-x:auto`` 拿不到有界宽度，
    浏览器会把容器撑到卡片总宽，横向滚动条不会出现、右侧卡片被直接裁掉且滑不到。
    这里按对话框宽度算出确切宽度，横向滚动才能正常生效。

    :return: CSS width 表达式，如 "calc(min(95vw, 760px) - 40px)"
    """
    return f"calc(min(95vw, {_DIALOG_WIDTH_PX}px) - {_CARD_ROW_SIDE_PX}px)"


# 说明：对话框内容区高度不做 100vh 反推，而是由 flex 链把**卡片定高**一路分配到滚动区
# （见页面级 CSS 的 .strategy-dialog-panels）。卡片定高后，头部 / 标签栏 / 底部按钮怎么改
# 都不用重算常量，也不会出现"内容把卡片撑破后被 .q-panel-parent 的 overflow:hidden 裁掉"。


def show_strategies_page() -> None:
    """策略列表页面入口。"""
    theme = AppContext().theme_manager
    bg_color: str = theme.var("page")
    font_color: str = theme.var("text")
    border_color: str = theme.var("border")
    card_bg: str = theme.var("surface")
    input_bg: str = theme.var("page")
    primary_color: str = theme.var("primary")
    title_color: str = theme.var("text")
    muted_color: str = theme.var("muted")
    field_label_color: str = theme.var("muted")
    tab_bg: str = theme.var("surface")
    tab_active: str = theme.var("primary")
    primary_glow: str = theme.var("primary-glow")

    # 页面级 CSS：自定义表格行样式（行高、悬浮、选中高亮）
    ui.add_css("""
        .s-table-row {
            display: flex;
            align-items: center;
            padding: 14px 24px;
            border-bottom: 1px solid var(--qs-divider-strong);
            cursor: pointer;
            transition: background-color 0.15s;
        }
        .s-table-row:hover {
            background-color: rgba(var(--qs-primary-strong-rgb), 0.10) !important;
        }
        .s-table-row.my-selected {
            background-color: rgba(var(--qs-primary-strong-rgb), 0.22) !important;
        }
        .s-table-row.my-selected:hover {
            background-color: rgba(var(--qs-primary-strong-rgb), 0.28) !important;
        }
        .s-col-name { flex: 32; min-width: 0; }
        .s-col-type { flex: 12; }
        .s-col-time { flex: 12; }
        .s-col-status { flex: 10; text-align: center; }
        .s-col-signal { flex: 18; }
        .s-col-action { flex: 10; text-align: right; }
        .s-table-header {
            display: flex;
            align-items: center;
            padding: 12px 24px;
            border-bottom: 1px solid var(--qs-border);
            background-color: var(--qs-surface-alt);
        }
    """)

    # 页面级 CSS：策略对话框标签页配色（tab_normal_color / tab_active_color）
    ui.add_css(f"""
        .strategy-dialog-tabs .q-tab {{
            background-color: {tab_bg} !important;
            color: {muted_color} !important;
            border-radius: 3px !important;
            margin: 2px 2px !important;
            width: 100% !important;
        }}
        .strategy-dialog-tabs .q-tab--active,
        .strategy-dialog-tabs .q-tab.q-tab--active,
        .strategy-dialog-tabs .q-tab[aria-selected="true"] {{
            background-color: {tab_active} !important;
            color: var(--qs-on-accent) !important;
        }}
        .strategy-dialog-tabs .q-tab__indicator {{
            display: none !important;
        }}
        .strategy-dialog-tabs {{
            background-color: {input_bg} !important;
            border-bottom: 1px solid {border_color} !important;
        }}
    """)

    # 页面级 CSS：对话框内容区高度链（把卡片定高一路分到滚动区）
    # Quasar 的真实 DOM 是三层，不是两层（漏掉中间那层高度就传不下去）：
    #   .q-tab-panels.q-panel-parent          ← 卡片 flex 子项（flex-1）+ overflow:hidden
    #     └ .q-panel.scroll                   ← QTabPanels 动态生成的包裹层（height:100%）
    #         └ .q-tab-panel.nicegui-tab-panel ← 面板本体（height:100%）
    #             └ .nicegui-scroll-area        ← 内容滚动区（contain:strict）
    # 说明：
    #   - 不写 !important：ui.add_css 注入的样式不在 @layer 内，未分层的作者样式优先级高于
    #     @layer 内的规则（Tailwind utilities 等），不加 !important 也能生效。
    #   - 每层都要 min-height: 0：flex item 默认 min-height:auto 会阻止收缩，高度传不下去。
    #   - 滚动区补一个 min-height 兜底：即便上层高度链再次失效，内容也不会整块消失。
    #   - .q-panel 是 Quasar 生成的中间层，必须显式设为纵向 flex，其子项才吃得到 flex:1。
    ui.add_css("""
        .strategy-dialog-panels {
            display: flex;
            flex-direction: column;
            min-height: 0;
        }
        .strategy-dialog-panels > .q-panel,
        .strategy-dialog-panels > .q-panel > .q-tab-panel,
        .strategy-dialog-panels > .q-panel > .q-tab-panel > * {
            flex: 1 1 0%;
            min-height: 0;
        }
        /* .q-panel 与面板本体都必须是纵向 flex，里面的子项才吃得到 flex:1。
           Quasar 只给它们设了 height:100%，那属于百分比通道（父高不确定就失效）。 */
        .strategy-dialog-panels > .q-panel,
        .strategy-dialog-panels > .q-panel > .q-tab-panel {
            display: flex;
            flex-direction: column;
        }
        .strategy-dialog-panels .q-scrollarea.nicegui-scroll-area {
            flex: 1 1 0%;
            min-height: 8rem;
            max-height: 100%;
        }
    """)

    # 筛选与分页状态
    filter_state: dict[str, str] = {
        "search": "", "stype": "全部类型", "status": "全部状态",
    }
    page_state: dict[str, int] = {"page": 0}
    # 视图状态：是否已自动展示过首条策略详情（原型默认选中第一条）、当前选中行
    view_state: dict[str, Any] = {"auto_shown": False, "selected": None}

    # ---- 页面骨架（容器先行创建，渲染函数在下方定义并调用）----
    with ui.column().classes("w-full gap-6").style(
        f"padding: 32px; background-color: {bg_color};"
    ):
        # 标题行：标题 + 创建策略按钮
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("策略列表").classes("text-xl font-bold").style("color: var(--qs-heading);")
            ui.button(
                "创建策略", icon="add",
                on_click=lambda: _open_create_strategy_dialog(_refresh),
            ).props("unelevated no-caps").classes("text-sm font-medium").style(
                "background-color: var(--qs-primary-hover); color: var(--qs-on-accent); "
                "border-radius: 8px; padding: 8px 16px;"
            )

        # 筛选栏
        with ui.row().classes("w-full items-center gap-4 flex-wrap").style(
            f"background-color: {card_bg}; border: 1px solid {border_color}; "
            f"border-radius: 12px; padding: 16px;"
        ):
            search_input: ui.input = (
                ui.input(placeholder="搜索策略名称或描述...")
                .props("outlined dense clearable debounce=300")
                .classes("flex-1 min-w-[200px]")
                .style(f"background-color: {input_bg}; color: {font_color};")
            )
            strategy_types = tools.load_strategy_type()
            type_select: ui.select = (
                ui.select(options=["全部类型", *strategy_types], value="全部类型")
                .props("outlined dense")
                .classes("min-w-[160px]")
                .style(f"background-color: {input_bg}; color: {font_color};")
            )
            status_select: ui.select = (
                ui.select(
                    options=["全部状态", "运行中", "已停止", "已暂停", "异常"],
                    value="全部状态",
                )
                .props("outlined dense")
                .classes("min-w-[140px]")
                .style(f"background-color: {input_bg}; color: {font_color};")
            )

            def _on_search_change(e: Any) -> None:
                """搜索输入变化：更新条件并刷新。"""
                val: Any = e.value if hasattr(e, "value") else getattr(e, "args", "")
                filter_state["search"] = str(val or "").strip().lower()
                page_state["page"] = 0
                _refresh()

            def _on_type_change(e: Any) -> None:
                """类型下拉变化：更新条件并刷新。"""
                val: Any = e.value if hasattr(e, "value") else getattr(e, "args", "")
                if isinstance(val, dict):
                    val = val.get("value", "")
                filter_state["stype"] = str(val) if val else "全部类型"
                page_state["page"] = 0
                _refresh()

            def _on_status_change(e: Any) -> None:
                """状态下拉变化：更新条件并刷新。"""
                val: Any = e.value if hasattr(e, "value") else getattr(e, "args", "")
                if isinstance(val, dict):
                    val = val.get("value", "")
                filter_state["status"] = str(val) if val else "全部状态"
                page_state["page"] = 0
                _refresh()

            def _reset_filters() -> None:
                """重置全部筛选条件。"""
                search_input.value = ""
                type_select.value = "全部类型"
                status_select.value = "全部状态"
                filter_state["search"] = ""
                filter_state["stype"] = "全部类型"
                filter_state["status"] = "全部状态"
                page_state["page"] = 0
                _refresh()

            search_input.on_value_change(_on_search_change)
            type_select.on_value_change(_on_type_change)
            status_select.on_value_change(_on_status_change)
            ui.button("重置", on_click=_reset_filters).props("flat no-caps") \
                .classes("text-sm font-medium").style("color: var(--qs-muted);")

        # 策略表格卡片（含分页页脚）
        with ui.card().classes("w-full gap-0 overflow-hidden").style(
            f"background-color: {card_bg}; border: 1px solid {border_color}; "
            f"border-radius: 12px; padding: 0;"
        ):
            table_container: ui.column = ui.column().classes("w-full gap-0")
            pagination_container: ui.row = (
                ui.row().classes("w-full items-center justify-between").style(
                    f"background-color: {input_bg}; "
                    f"border-top: 1px solid {border_color}; padding: 16px 24px;"
                )
            )

        # 详情面板（点击表格行后显示）
        detail_wrap: ui.column = (
            ui.column().classes("w-full gap-0 hidden").style(
                f"background-color: {input_bg}; border: 1px solid {border_color}; "
                f"border-radius: 12px; overflow: hidden;"
            )
        )


    # ---- 数据加载 ----

    def _load_rows() -> list[dict[str, Any]]:
        """拉取并组装策略表格数据。

        以用户策略关联为主表，关联策略模板补充名称/类型/描述。

        :return: 策略行数据列表（按创建时间倒序）
        """
        user_strategies: list[dict[str, Any]] = UserStrategyManager().list_user_strategies()
        # 策略模板走进程级缓存（strategy_cache，模板是全局公共数据，多用户共享安全）
        templates: dict[str, dict[str, Any]] = AppContext().strategy_cache.get_many(
            [str(us.get("strategy_id", "") or "") for us in user_strategies]
        )
        rows: list[dict[str, Any]] = []
        for us in user_strategies:
            sid: str = str(us.get("strategy_id", ""))
            tpl: dict[str, Any] = templates.get(sid, {})
            # ⚠ 用 tools.to_float 而不是 float()：会话缓存里同一个键可能被写成
            # 展示格式的字符串（例如 total_profit="0.0%"）。float("0.0%") 会抛
            # ValueError，导致整个表格渲染中断、页面看着「什么都不显示」；
            # to_float 能正确解析带 % / 千分位 / 中文单位的写法，失败也给默认值。
            total_profit: float = tools.to_float(us.get("total_profit", 0.0))
            init_amount: float = tools.to_float(us.get("initial_amount", 0.0))
            profit = 0.00 if init_amount == 0.0 else total_profit / init_amount * 100
            total_profit_str: str = f"{round(profit, 2)}%"
            # 正收益显示红色，负收益显示绿色（A 股惯例）
            profit_color: str = "var(--qs-positive)" if profit > 0 else ("var(--qs-negative)" if profit < 0 else "var(--qs-faint)")
            rows.append({
                "us_id": str(us.get("_id", "") or us.get("id", "")),
                "sid": sid,
                # 用户自定义名称原始值（user_strategy.name，可能为空），
                # 供编辑对话框回填；展示用的 name 走下面的回退链
                "custom_name": str(us.get("name", "") or ""),
                # 优先显示用户自定义名称（user_strategy.name），
                # 历史数据未设置自定义名称时回退策略模板名
                "name": str(us.get("name") or tpl.get("name", "") or sid or "未命名策略"),
                "description": str(tpl.get("description", "") or ""),
                "stype": str(tpl.get("strategy_type", "") or "-"),
                "total_profit": total_profit_str,
                "profit_color": profit_color,
                "status": str(us.get("status", "stopped")),
                "pool_id": str(us.get("pool_id", "") or ""),
                # 同样走 to_float：缓存里这两个键也可能被写成展示格式（"4" / "10.0万"）
                "initial_amount": init_amount,
                "max_stock_count": int(tools.to_float(us.get("max_stock_count", 0))),
                "strategy_params": us.get("strategy_params", {}) or {},
            })
        return rows

    def _latest_signal_map(us_ids: list[str]) -> dict[str, tuple[str, str]]:
        """批量获取各用户策略的最近信号文案与颜色。

        原实现按行调用（每行一次），而每次内部都会查询用户策略列表并做全量排序，
        PAGE_SIZE=10 时单次表格渲染就是 10 次重复工作。这里一次性批量取回，
        且只取每个策略各自的最近一条（不受全局条数窗口限制）。

        :param us_ids: 当前页各行的 user_strategy_id 列表
        :return: {user_strategy_id: (信号文本如 '买入 600519', 颜色)}
        """
        positive_color: str = theme.var("positive")
        negative_color: str = theme.var("negative")
        result: dict[str, tuple[str, str]] = {}
        try:
            latest: dict[str, dict[str, Any]] = AppContext().signal_manager.get_latest_signals(
                [str(i or "") for i in us_ids if i]
            )
        except Exception:
            logger.warning("查询最近信号失败", exc_info=True)
            return result
        for us_id, sig in latest.items():
            action: str = str(sig.get("action", "") or "")
            text: str = f"{action} {sig.get('stock_code', '')}".strip()
            color: str = positive_color if action == "买入" else negative_color
            result[us_id] = (text or "-", color)
        return result

    def _filtered_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按筛选条件过滤行数据。

        :param rows: 全量行数据
        :return: 过滤后的行数据
        """
        result: list[dict[str, Any]] = []
        for row in rows:
            if filter_state["search"]:
                haystack: str = (row["name"] + row["description"]).lower()
                if filter_state["search"] not in haystack:
                    continue
            if filter_state["stype"] != "全部类型" and row["stype"] != filter_state["stype"].strip():
                continue
            status_text: str = _STATUS_STYLES.get(row["status"], ("未知",))[0]
            if filter_state["status"] != "全部状态" and status_text != filter_state["status"]:
                continue
            result.append(row)
        
        return result

    # ---- 表格与分页渲染 ----

    def _render_pagination(total_items: int) -> None:
        """渲染分页页脚。

        :param total_items: 过滤后总条数
        """
        pagination_container.clear()
        total_pages: int = max(1, math.ceil(total_items / PAGE_SIZE))
        current: int = min(page_state["page"], total_pages - 1)
        start_idx: int = current * PAGE_SIZE + 1 if total_items > 0 else 0
        end_idx: int = min(start_idx + PAGE_SIZE - 1, total_items)
        with pagination_container:
            ui.label(f"显示第 {start_idx}-{end_idx} 条结果，共 {total_items} 条").classes(
                "text-sm tracking-tight"
            ).style("color: var(--qs-faint);")
            with ui.row().classes("items-center gap-2"):
                prev_style: str = _page_btn_style(disabled=(current <= 0))
                ui.label("上一页").classes("text-sm rounded-lg px-4 py-2 cursor-pointer").style(
                    prev_style
                ).on("click", lambda: _goto_page(current - 1))
                for p in range(total_pages):
                    active: bool = p == current
                    style: str = (
                        "border: 1px solid var(--qs-primary); background-color: var(--qs-surface); "
                        "color: var(--qs-text); font-weight: 500;"
                        if active
                        else f"border: 1px solid {border_color}; background-color: transparent; color: var(--qs-muted);"
                    )
                    ui.label(str(p + 1)).classes(
                        "text-sm rounded-lg px-3 py-2 cursor-pointer"
                    ).style(style).on("click", lambda _p=p: _goto_page(_p))
                next_style: str = _page_btn_style(disabled=(current >= total_pages - 1))
                ui.label("下一页").classes("text-sm rounded-lg px-4 py-2 cursor-pointer").style(
                    next_style
                ).on("click", lambda: _goto_page(current + 1))

    def _page_btn_style(disabled: bool) -> str:
        """分页按钮样式。

        :param disabled: 是否禁用
        :return: CSS 样式字符串
        """
        if disabled:
            return f"border: 1px solid {border_color}; color: var(--qs-icon-muted); cursor: not-allowed;"
        return f"border: 1px solid {border_color}; color: var(--qs-muted);"

    def _goto_page(page: int) -> None:
        """跳转到指定页并刷新表格。

        :param page: 目标页码（0 起始）
        """
        if page < 0:
            return
        page_state["page"] = page
        _render_table()

    def _render_table() -> list[dict[str, Any]]:
        """渲染策略表格（手动构建 UI 行元素 + Python click 回调，确保点击事件可靠传递）。

        :return: 过滤后的全部行数据（供首次自动展示详情使用）
        """
        table_container.clear()
        rows: list[dict[str, Any]] = _load_rows()
        filtered: list[dict[str, Any]] = _filtered_rows(rows)
        total_pages: int = max(1, math.ceil(len(filtered) / PAGE_SIZE))
        if page_state["page"] >= total_pages:
            page_state["page"] = total_pages - 1
        start: int = page_state["page"] * PAGE_SIZE
        page_rows: list[dict[str, Any]] = filtered[start:start + PAGE_SIZE]

        # 预计算每行的信号和状态（信号一次性批量取回，避免逐行查询）
        signal_map: dict[str, tuple[str, str]] = _latest_signal_map(
            [str(r.get("us_id", "") or "") for r in page_rows]
        )
        for r in page_rows:
            sig_t, sig_c = signal_map.get(r["us_id"], ("-", "var(--qs-faint)"))
            r["_signal_text"] = sig_t
            r["_signal_color"] = sig_c
            st = _STATUS_STYLES.get(r["status"], ("未知", "var(--qs-label)", "var(--qs-chip-neutral)"))
            r["_status_text"] = st[0]
            r["_status_color"] = st[1]
            r["_status_bg"] = st[2]
            r["_desc"] = r.get("description", "") or "暂无描述"

        with table_container:
            # 表头
            with ui.row().classes("s-table-header w-full"):
                for label_text, col_cls in [
                    ("策略名称", "s-col-name"), ("类型", "s-col-type"),
                    ("总收益", "s-col-time"), ("状态", "s-col-status"),
                    ("最近信号", "s-col-signal"), ("操作", "s-col-action"),
                ]:
                    align: str = "text-align: right;" if col_cls == "s-col-action" else ""
                    ui.label(label_text).classes(
                        "text-xs font-semibold uppercase tracking-wider"
                    ).style(f"color: var(--qs-muted); {align}").classes(col_cls)

            # 数据行
            for row_data in page_rows:
                with ui.row().classes("s-table-row w-full") as data_row:
                    # 策略名称（名称 + 描述两行）
                    with ui.column().classes("s-col-name gap-0"):
                        ui.label(row_data["name"]).classes(
                            "text-sm font-medium"
                        ).style("color: var(--qs-text);")
                        ui.label(row_data["_desc"]).classes(
                            "text-xs italic"
                        ).style(
                            "color: var(--qs-faint); overflow:hidden; text-overflow:ellipsis;"
                            "white-space:nowrap; max-width:260px;"
                        )
                    # 类型
                    ui.label(row_data["stype"]).classes("text-sm s-col-type").style(
                        "color: var(--qs-muted);"
                    )
                    # 总收益（正数红色，负数绿色，A 股惯例）
                    ui.label(row_data["total_profit"]).classes(
                        "text-sm font-medium s-col-time"
                    ).style(f"color: {row_data['profit_color']};")
                    # 状态徽标
                    with ui.column().classes("s-col-status gap-0 items-center"):
                        if row_data["status"] == "running":
                            ui.label("").classes(
                                "inline-block w-[6px] h-[6px] rounded-full mr-1 animate-pulse"
                            ).style("background-color: var(--qs-negative);")
                        ui.label(row_data["_status_text"]).classes(
                            "text-xs font-medium rounded-full px-2.5 py-0.5"
                        ).style(
                            f"color: {row_data['_status_color']}; "
                        )
                    # 最近信号
                    ui.label(row_data["_signal_text"]).classes(
                        "text-sm font-medium s-col-signal"
                    ).style(f"color: {row_data['_signal_color']};")
                    # 操作按钮（回测记录 + 编辑图标）
                    with ui.column().classes("s-col-action gap-0 items-end"):
                        def _make_edit_click(r: dict[str, Any]) -> Callable[[], None]:
                            """创建编辑按钮回调（闭包捕获当前行数据）。"""
                            return lambda: _open_edit_strategy_dialog(r, _refresh)
                        with ui.row().classes("items-center gap-1 no-wrap"):
                            # 回测记录：跳转到策略回测记录页（/strategy-backtest?strategy_id=…）
                            # 回测按**策略模板 ID** 关联，因此入口带的是行里的 sid
                            ui.button(icon="query_stats", on_click=_make_backtest_click(row_data)) \
                                .props("flat round dense").style(
                                    "color: var(--qs-link); cursor: pointer;"
                                )
                            ui.button(icon="edit", on_click=_make_edit_click(row_data)) \
                                .props("flat round dense").style(
                                    "color: var(--qs-link); cursor: pointer;"
                                )

                    # 绑定整行点击事件（点击行的任意位置触发详情展示）
                    def _make_row_click(r: dict[str, Any]) -> Callable[[], None]:
                        """创建整行点击回调（闭包捕获当前行数据）。"""
                        return lambda: _on_row_click(r)
                    data_row.on("click", _make_row_click(row_data))

        _render_pagination(len(filtered))
        return filtered

    def _on_row_click(row: dict[str, Any]) -> None:
        """处理表格行点击：更新选中状态并展示详情面板。

        :param row: 被点击行的数据字典
        """
        view_state["selected"] = row
        _show_detail(row)

    def _make_backtest_click(row: dict[str, Any]) -> Callable[[], None]:
        """创建「回测记录」按钮回调（闭包捕获当前行数据）。"""
        return lambda: _open_backtest(row)

    def _open_backtest(row: dict[str, Any]) -> None:
        """打开该策略的回测记录页。

        ⚠ 回测按**策略模板 ID** 关联（GET /api/strategy/{strategy_id}/backtest）：
        同一个模板被多个用户策略复用时，它们看到的是**同一份**回测记录 ——
        这是接口与数据模型决定的，不是串数据。
        模板 ID 缺失（历史数据）时不跳转，给一句提示，免得落到只能看空态的页面。

        :param row: 表格行数据（含 sid = 策略模板 ID）
        """
        sid: str = str(row.get("sid", "") or "").strip()
        if not sid:
            ui.notify("该策略未关联策略模板，无法查看回测记录", type="warning")
            return
        # 带上来源标识：回测页的「返回」按钮会回到本页（策略列表）而不是策略监控
        ui.navigate.to(backtest_route(sid, "strategies"))

    def _refresh() -> None:
        """刷新表格与分页；首次加载后自动在页面底部展示第一条策略详情。"""
        filtered_rows: list[dict[str, Any]] = _render_table()
        if not view_state["auto_shown"] and filtered_rows:
            view_state["auto_shown"] = True
            _show_detail(dict(filtered_rows[0]))

    # ---- 详情面板 ----

    def _hide_detail() -> None:
        """隐藏详情面板。"""
        detail_wrap.classes("hidden")
        view_state["selected"] = None

    def _show_detail(row: dict[str, Any]) -> None:
        """显示策略详情面板。

        :param row: 表格行数据
        """
        detail_wrap.clear()
        detail_wrap.classes(remove="hidden")
        view_state["selected"] = dict(row)
        tab_state: dict[str, str] = {"tab": "positions"}

        with detail_wrap:
            # 面板头部：标题 + 关闭按钮
            with ui.row().classes("w-full items-center justify-between").style(
                f"padding: 16px 24px; border-bottom: 1px solid {border_color};"
            ):
                ui.label(f"{row['name']} — 策略详情").classes("text-base font-semibold").style(
                    "color: var(--qs-text);"
                )
                ui.button(icon="close", on_click=_hide_detail).props("flat round dense").style(
                    "color: var(--qs-muted);"
                )
            tabs_row: ui.row = ui.row().classes("w-full items-center gap-2").style(
                f"padding: 12px 24px; border-bottom: 1px solid {border_color};"
            )
            content_container: ui.column = ui.column().classes("w-full gap-0")

        def _render_tab_content() -> None:
            """渲染当前选中标签页的内容。

            注意：必须在 with content_container 上下文中创建元素，
            避免事件处理期间元素被添加到错误插槽。
            """
            content_container.clear()
            with content_container:
                if tab_state["tab"] == "positions":
                    _render_positions(content_container, row)
                elif tab_state["tab"] == "trades":
                    _render_trades(content_container, row)
                else:
                    _render_runlogs(content_container, row)

        def _render_tabs() -> None:
            """渲染标签页切换栏（同样需要显式进入 tabs_row 上下文）。"""
            tabs_row.clear()
            with tabs_row:
                for key, text in [
                    ("positions", "持仓信息"), ("trades", "订单明细"), ("runlogs", "运行日志"),
                ]:
                    active: bool = tab_state["tab"] == key
                    style: str = (
                        "background-color: var(--qs-primary-active); color: var(--qs-on-accent);"
                        if active
                        else "background-color: transparent; color: var(--qs-muted);"
                    )
                    ui.label(text).classes(
                        "text-sm font-medium rounded-lg px-4 py-2 cursor-pointer"
                    ).style(style).on("click", lambda _k=key: _switch_tab(_k))

        def _switch_tab(key: str) -> None:
            """切换标签页。

            :param key: 标签页标识
            """
            tab_state["tab"] = key
            _render_tabs()
            _render_tab_content()

        _render_tabs()
        _render_tab_content()

    def _detail_empty(container: ui.column, text: str) -> None:
        """在容器内渲染空数据提示。

        :param container: 目标容器
        :param text: 提示文本
        """
        with container, ui.column().classes("w-full items-center justify-center").style("padding: 48px;"):
            ui.icon("inbox").style("color: var(--qs-faint); font-size: 40px;")
            ui.label(text).classes("text-sm").style("color: var(--qs-faint); margin-top: 8px;")

    def _render_positions(container: ui.column, row: dict[str, Any]) -> None:
        """渲染持仓信息标签页内容（原型列：股票代码/股票名称/持仓数量/成本价/现价/盈亏）。

        持仓字典字段来自 PositionItem: code/name/quantity/cost_price/current_price/
        profit_rate/profit_amount。

        「盈亏」列展示 **收益率(收益额)**，形如 ``+10.00%(2000.00)``；两者都用实时价与
        成本价现算，**必须忽略** ``profit_rate`` / ``profit_amount``（详见下方注释）。
        拿不到实时价时，现价与盈亏一并显示 ``--``。

        :param container: 目标容器
        :param row: 表格行数据
        """
        execution: dict[str, Any] = {}
        try:
            execution = AppContext().user_strategy_api.get_latest_execution(row["us_id"])
        except Exception:
            logger.warning("查询最新执行记录失败: %s", row["us_id"], exc_info=True)
        positions: list[dict[str, Any]] = list(execution.get("positions", []))
        if not positions:
            _detail_empty(container, "暂无持仓")
            return
        positive_color: str = theme.var("positive")
        negative_color: str = theme.var("negative")
        with container:
            # 表头（原型样式：深色背景 + 底部边框）
            with ui.row().classes("w-full items-center").style(
                f"background-color: {card_bg}; border-bottom: 1px solid {border_color}; "
                "padding: 10px 24px;"
            ):
                for hdr in ["股票代码", "股票名称", "持仓数量", "成本价", "现价", "盈亏"]:
                    align: str = "text-align: right;" if hdr in ("成本价", "现价", "盈亏") else ""
                    ui.label(hdr).classes(
                        "text-xs font-semibold uppercase tracking-wider"
                    ).style(f"color: var(--qs-muted); flex: 1; {align}")
            # 数据行（可用字段: code/name/quantity/cost_price；current_price 见下）
            #
            # 盈亏口径（2026-09-22 需求方明确）：百分率与金额都必须**用实时价与成本价
            # 现算**，不得读 PositionItem 的 profit_rate / profit_amount。
            # 这两个字段是**成交时点值**——买入时写入、持仓期间后端不参与计算、
            # 直到卖出那一刻才重算。所以持仓期间读它，读到的是「买入时点的收益」，
            # 恒为手续费口径的微亏，与当前浮动盈亏无关。
            # 持仓期间唯一可用的事实是 cost_price 与 quantity（不随行情变），
            # 收益必须由它们和实时价现推。另外本页「现价」列已用 market_api 的实时价，
            # 若盈亏取快照，同一行会出现「现价是新的、盈亏是旧的」自相矛盾；
            # 同源重算后恒有 收益率 × 成本价 × 数量 ≈ 收益额。
            codes: str = ",".join(str(p.get("code")) for p in positions if p.get("code", "") != "")
            rt_hq_list = AppContext().market_api.get_real_time(codes=codes)
            price_dict: dict[str, float] = {}
            for hq in rt_hq_list:
                code_key: str = str(hq.get("code", "") or "")
                px: float = float(hq.get("price") or hq.get("close") or 0)
                # 只认正价：返回 0 / 空值时按「没有实时价」处理，不能当成跌到 0
                if code_key and px > 0:
                    price_dict[code_key] = px
            for idx, pos in enumerate(positions):
                code: str = pos.get("code", "")
                quantity: int = int(float(pos.get("quantity", 0) or 0))
                cost_price: float = float(pos.get("cost_price", 0.0) or 0.0)
                # 实时价缺失（停牌 / 行情接口失败 / 代码不在返回里）时，现价与盈亏一并显示
                # "--"：持仓期内 cost_price 与 quantity 是唯一可用的事实，收益必须由它们
                # 现推，没有实时价就算不出来。此时**不能**回退 PositionItem.current_price
                # ——它与 profit_* 同属成交时点写入，大概率是买入时的价；拿它算出来的数字
                # 看着像实时、其实是建仓当刻的收益，正是本次要避免的误导。
                current_price: float = float(price_dict.get(code, 0.0) or 0.0)
                if current_price > 0.0:
                    profit_amount: float = (current_price - cost_price) * quantity
                    # 成本价为 0 时收益率无定义（除零），退化为 0.00% 而不是抛异常
                    profit_rate: float = (
                        0.0 if cost_price == 0.0
                        else (current_price - cost_price) / cost_price * 100
                    )
                    # 展示「收益率(收益额)」，形如 +10.00%(2000.00)：正负号由百分率承担，
                    # 金额只给绝对值 + 两位小数（需求方 2026-09-22 确认的格式）。
                    profit_text: str = f"{profit_rate:+.2f}%({abs(profit_amount):.2f})"
                    profit_color: str = positive_color if profit_amount >= 0 else negative_color
                    price_text: str = f"{current_price:.2f}"
                    price_color: str = "var(--qs-label)"
                else:
                    profit_text = "--"
                    profit_color = "var(--qs-faint)"
                    price_text = "--"
                    price_color = "var(--qs-faint)"
                bg: str = input_bg if idx % 2 == 0 else "var(--qs-row-alt-2)"
                with ui.row().classes("w-full items-center").style(
                    f"padding: 12px 24px; background-color: {bg}; "
                    "border-bottom: 1px solid var(--qs-divider-strong);"
                ):
                    ui.label(str(pos.get("code", "-"))).classes("text-sm font-medium").style(
                        f"color: {font_color}; flex: 1;"
                    )
                    ui.label(str(pos.get("name", "-"))).classes("text-sm").style(
                        "color: var(--qs-label); flex: 1;"
                    )
                    ui.label(str(quantity)).classes("text-sm").style(
                        "color: var(--qs-label); flex: 1;"
                    )
                    ui.label(f"{cost_price:.2f}").classes("text-sm").style(
                        "color: var(--qs-label); flex: 1; text-align: right;"
                    )
                    ui.label(price_text).classes("text-sm").style(
                        f"color: {price_color}; flex: 1; text-align: right;"
                    )
                    # nowrap：加了「率(额)」后文案近乎翻倍，flex 列在窄屏下会被折行，
                    # 「+10.00%(2000.00)」断成两行很难读
                    ui.label(profit_text).classes("text-sm font-medium").style(
                        f"color: {profit_color}; flex: 1; text-align: right; white-space: nowrap;"
                    )

    def _stock_name_map(codes: list[str]) -> dict[str, str]:
        """批量查询股票代码对应名称。

        :param codes: 股票代码列表
        :return: code → name 映射（查询失败时返回空映射）
        """
        result: dict[str, str] = {}
        if not codes:
            return result
        try:
            infos: list[dict[str, Any]] = AppContext().stock_info_api.get_by_codes(
                ",".join(codes)
            )
            for info in infos:
                code_key: str = str(info.get("stock_code", "") or info.get("code", ""))
                name_val: str = str(info.get("stock_name", "") or info.get("name", ""))
                if code_key:
                    result[code_key] = name_val
        except Exception:
            logger.warning("查询股票名称失败", exc_info=True)
        return result

    def _render_trades(container: ui.column, row: dict[str, Any]) -> None:
        """渲染订单明细标签页内容（原型列：日期/股票/方向/数量/价格/成交额）。

        以持仓信息的股票代码为参数，按 action=买入 查询各股票最新订单
        （order_api.get_latest），构建订单明细。订单字段：create_time/
        stock_code/action/entrust_quantity/trade_price/trade_quantity。

        :param container: 目标容器
        :param row: 表格行数据
        """
        # 1. 拿持仓，提取股票代码
        execution: dict[str, Any] = {}
        try:
            execution = AppContext().user_strategy_api.get_latest_execution(row["us_id"])
        except Exception:
            logger.warning("查询持仓失败: %s", row["us_id"], exc_info=True)
        positions: list[dict[str, Any]] = list(execution.get("positions", []))
        codes: list[str] = [str(p.get("code", "")) for p in positions if p.get("code")]
        if not codes:
            _detail_empty(container, "暂无订单记录")
            return
        # 2. 逐个股票查最新买入订单
        orders: list[dict[str, Any]] = []
        try:
            orders = AppContext().order_api.get_latest(
                row["us_id"], codes, action=""
            )
        except Exception:
            logger.warning("查询最新订单失败: %s", codes, exc_info=True)
        if not orders:
            _detail_empty(container, "暂无订单记录")
            return
        names: dict[str, str] = _stock_name_map(
            [str(o.get("stock_code", "")) for o in orders if o.get("stock_code")]
        )
        positive_color: str = theme.var("positive")
        negative_color: str = theme.var("negative")
        with container:
            with ui.row().classes("w-full items-center").style(
                f"background-color: {card_bg}; border-bottom: 1px solid {border_color}; "
                "padding: 10px 24px;"
            ):
                for hdr in ["日期", "股票", "方向", "数量", "价格", "成交额"]:
                    align: str = "text-align: right;" if hdr in ("数量", "价格", "成交额") else ""
                    ui.label(hdr).classes(
                        "text-xs font-semibold uppercase tracking-wider"
                    ).style(f"color: var(--qs-muted); flex: 1; {align}")
            for idx, order in enumerate(orders):
                action: str = str(order.get("action", ""))
                code: str = str(order.get("stock_code", ""))
                price: float = float(order.get("trade_price", 0.0) or 0.0)
                quantity: int = int(float(order.get("entrust_quantity", 0) or 0))
                trade_qty: int = int(float(order.get("trade_quantity", 0) or 0))
                amount: float = price * trade_qty
                bg: str = input_bg if idx % 2 == 0 else "var(--qs-row-alt-2)"
                action_color: str = positive_color if action == "买入" else (
                    negative_color if action == "卖出" else font_color
                )
                display_name: str = f"{code} {names.get(code, '')}".strip() or "-"
                with ui.row().classes("w-full items-center").style(
                    f"padding: 12px 24px; background-color: {bg}; "
                    "border-bottom: 1px solid var(--qs-divider-strong);"
                ):
                    ui.label(str(order.get("create_time", ""))[:16]).classes("text-sm").style(
                        "color: var(--qs-label); flex: 1;"
                    )
                    ui.label(display_name).classes("text-sm").style(
                        f"color: {font_color}; flex: 1;"
                    )
                    ui.label(action or "-").classes("text-sm font-medium").style(
                        f"color: {action_color}; flex: 1;"
                    )
                    ui.label(str(quantity) if quantity else "--").classes("text-sm").style(
                        f"color: {font_color}; flex: 1; text-align: right;"
                    )
                    ui.label(f"{price:.2f}" if price else "--").classes("text-sm").style(
                        f"color: {font_color}; flex: 1; text-align: right;"
                    )
                    ui.label(f"{amount:,.0f}" if amount else "--").classes("text-sm").style(
                        f"color: {font_color}; flex: 1; text-align: right;"
                    )

    def _render_runlogs(container: ui.column, row: dict[str, Any]) -> None:
        """渲染运行日志标签页内容（原型样式：时间 [级别] 内容 的纯文本行）。

        运行记录字段：create_time/level(INFO/WARNING/ERROR)/log_content。

        :param container: 目标容器
        :param row: 表格行数据
        """
        logs: list[dict[str, Any]] = []
        try:
            logs = AppContext().user_strategy_api.get_runlog(row["us_id"], limit=50)
        except Exception:
            logger.warning("查询运行日志失败: %s", row["us_id"], exc_info=True)
        if not logs:
            _detail_empty(container, "暂无日志")
            return
        level_colors: dict[str, str] = {
            "ERROR": "var(--qs-positive)", "WARNING": "var(--qs-accent)", "INFO": "var(--qs-muted)",
        }
        with container, ui.scroll_area().classes("w-full").style("max-height: 240px;"):
            for log_item in logs:
                    level: str = str(log_item.get("level", "INFO")).upper()
                    line_color: str = level_colors.get(level, "var(--qs-muted)")
                    log_line: str = (
                        f"{str(log_item.get('create_time', ''))[:19]} "
                        f"[{level}] {log_item.get('log_content', '')!s}"
                    ).strip()
                    ui.label(log_line).classes("text-sm").style(
                        f"color: {line_color}; word-break: break-all; "
                        "padding: 6px 24px; border-bottom: 1px solid var(--qs-divider-strong);"
                    )
    # ---- 创建策略对话框 ----

    def _strategy_card_style(
        picked: bool,
        *,
        width: str = "240px",
        clickable: bool = True,
        stretch: bool = False,
    ) -> str:
        """策略卡片的内联样式；选中态改变边框、底色与描边。

        用 ``style(replace=...)`` 整体替换而不是追加，避免反复切换时内联样式越拼越长。

        :param picked: 是否选中
        :param width: 卡片宽度（创建对话框的横向列表用 240px）
        :param clickable: 是否可点击（决定鼠标手型）
        :param stretch: 是否横向撑满父容器（编辑对话框的单张只读卡片用）。
            这里刻意不用 ``width: 100%``：卡片父级是 nicegui 的 flex 纵向容器
            （``align-items: flex-start``），且在 ``ui.scroll_area`` 的内容层内，
            百分比宽度既要跟父宽推导、又容易比可用宽度大 1~2px 而多出横向滚动条。
            ``align-self: stretch`` 直接取容器内容盒宽度，精确且与内边距无关。
        :return: CSS 样式串
        """
        border: str = primary_color if picked else border_color
        bg: str = "rgba(var(--qs-primary-strong-rgb), 0.14)" if picked else input_bg
        shadow: str = f"0 0 0 3px {primary_glow}" if picked else "none"
        sizing: str = "align-self: stretch; width: auto;" if stretch else f"width: {width};"
        return (
            f"flex: 0 0 auto; {sizing} min-height: 158px; "
            f"cursor: {'pointer' if clickable else 'default'}; "
            "border-radius: 10px; padding: 14px 16px; transition: all 0.15s; "
            f"background-color: {bg}; border: 2px solid {border}; "
            f"box-shadow: {shadow};"
        )

    def _build_strategy_card(
        tpl: dict[str, Any],
        *,
        picked: bool,
        on_pick: Callable[[str], None] | None = None,
        width: str = "240px",
        stretch: bool = False,
    ) -> tuple[ui.card, ui.checkbox]:
        """渲染一张策略模板卡片（左上角勾选框 / 右上角类型徽标 / 名称 / 描述三行省略）。

        :param tpl: 策略模板对象（name / strategy_type / description）
        :param picked: 初始是否选中
        :param on_pick: 点击回调（入参为模板名）；None 表示只读展示（不注册点击、鼠标不改手型）
        :param width: 卡片宽度（stretch=True 时忽略）
        :param stretch: 是否横向撑满父容器（编辑对话框的单张只读卡片用）
        :return: (卡片元素, 勾选框元素) —— 供调用方就地切换选中态
        """
        tpl_name: str = str(tpl.get("name", ""))
        tpl_type: str = str(tpl.get("strategy_type", "") or "-")
        tpl_desc: str = str(tpl.get("description", "") or "暂无描述")
        card: ui.card = ui.card().classes("gap-0").style(
            replace=_strategy_card_style(
                picked, width=width, clickable=on_pick is not None, stretch=stretch
            )
        )
        if on_pick is not None:
            card.on("click", lambda _n=tpl_name: on_pick(_n))
        with card:
            # 左上角勾选框：pointer-events:none 让点击穿透到卡片，
            # 由卡片统一处理选中，避免勾选框与卡片各触发一次而互相抵消
            with ui.row().classes("w-full items-center").style("margin-bottom: 10px;"):
                check: ui.checkbox = (
                    ui.checkbox(value=picked).props("dense").style("pointer-events: none;")
                )
                ui.space()
                ui.label(tpl_type).classes("text-xs font-medium rounded px-2 py-0.5").style(
                    "color: var(--qs-info); background-color: rgba(var(--qs-primary-rgb), 0.2);"
                )
            # 名称/描述都占满卡片宽度：否则在 flex 列里会收缩到内容宽度，
            # -webkit-line-clamp 的多行省略也会失效
            ui.label(tpl_name).classes("w-full text-sm font-bold").style(f"color: {font_color};")
            ui.label(tpl_desc).classes("w-full text-xs").style(
                f"color: {muted_color}; margin-top: 6px; "
                "display: -webkit-box; -webkit-line-clamp: 3; "
                "-webkit-box-orient: vertical; overflow: hidden; word-break: break-all;"
            )
        return card, check

    def _open_create_strategy_dialog(on_saved: Callable[[], None]) -> None:
        """打开创建策略对话框。

        内容：「策略」标签页为 策略模板卡片选择 + 自定义名称 + 股票池 / 初始资金 /
        最大持仓数 + 「立即运行」开关；「参数」标签页为所选模板的运行参数。

        「立即运行」默认勾选：勾选时以 status=running 创建（创建完成即开始运行），
        取消勾选则以 status=stopped 创建。

        :param on_saved: 保存成功后的回调（用于刷新表格）
        """
        # 预加载数据
        all_strategies: list[dict[str, Any]] = []
        try:
            all_strategies = AppContext().strategy_api.list()
        except Exception:
            logger.warning("查询策略列表失败", exc_info=True)
        strategy_name_map: dict[str, dict[str, Any]] = {
            str(s.get("name", "")): s
            for s in all_strategies
            if s.get("name", "")
        }
        all_pools_list: list[dict[str, Any]] = []
        try:
            all_pools_list = AppContext().pool_api.list()
        except Exception:
            logger.exception("查询股票池失败")
        pool_name_map: dict[str, dict[str, Any]] = {
            str(s.get("name", "")): s for s in all_pools_list if s.get("name", "")
        }
        field_label_style = f"font-size: 0.875rem; font-weight: 500; color: {field_label_color};"
        input_style = f"flex: 1; background-color: {input_bg}; color: {font_color}; width: 100%;"
        with ui.dialog() as dialog, ui.card().classes("w-[760px] max-w-[95vw] gap-0").style(
                f"background-color: {card_bg}; border: 1px solid {border_color}; "
                f"border-radius: 12px; padding: 0; "
                f"display: flex; flex-direction: column; overflow: hidden; "
                # 定高（见 _dialog_card_height）：内容区（滚动条）的高度由
                # .strategy-dialog-panels 的 flex 链从卡片高度里分配，不依赖手算常量。
                f"height: {_dialog_card_height()};"
            ):
                # ---- 头部 ----
                with ui.row().classes("w-full items-center justify-between").style(
                    f"padding: 16px 24px; border-bottom: 1px solid {border_color};"
                ):
                    ui.label("创建新策略").classes("text-xl font-bold").style(f"color: {title_color};")
                    ui.button(icon="close", on_click=dialog.close).props("flat round dense").style(f"color: {muted_color};")

                # ---- 标签页（策略 / 参数） ----
                with ui.tabs().classes("w-full strategy-dialog-tabs").props("dense") as dialog_tabs:
                    ui.tab("策略").classes("text-sm")
                    ui.tab("参数").classes("text-sm")
                with ui.tab_panels(dialog_tabs, value="策略").classes("w-full flex-1 strategy-dialog-panels").style(f"background-color: {card_bg};"):
                    # ---- 策略 tab：策略选择 + 股票池 配置 ----
                    with ui.tab_panel("策略").classes("p-0"):  # noqa: SIM117
                        with ui.scroll_area().classes("w-full"):
                            with ui.column().classes("w-full gap-0"):
                                def _section(title: str) -> None:
                                    """渲染对话框内的小节标题。"""
                                    with ui.row().classes("w-full items-center gap-3").style(
                                        "padding: 16px 24px 4px;"
                                    ):
                                        ui.label(title).classes("text-lg font-bold").style(f"flex: 1; color: {title_color};")

                                # ---- STEP 01：策略选择（卡片横向滑动 + 左上角勾选） ----
                                _section("策略选择")
                                strategy_templates: list[dict[str, Any]] = [
                                    tpl for tpl in strategy_name_map.values() if tpl
                                ]
                                # 当前选中的策略模板名（"" 表示尚未勾选）
                                selected_state: dict[str, str] = {"name": ""}
                                # {模板名: {"card": 卡片元素, "check": 勾选框}}，用于切换选中态时就地改样式
                                card_refs: dict[str, dict[str, Any]] = {}

                                def _apply_card_selection(tpl_name: str) -> None:
                                    """勾选某张策略卡片：刷新全部卡片选中态并重渲染参数页。

                                    刻意**不**整块重渲染——重渲染会把横向滚动位置重置到最左，
                                    勾选靠右的卡片时列表会明显跳动。这里只改样式与勾选值。
                                    """
                                    selected_state["name"] = tpl_name
                                    for name, ref in card_refs.items():
                                        picked: bool = (name == tpl_name)
                                        # style(replace=...) 会整体替换内联样式（非追加），
                                        # 因此反复切换不会残留上一次的边框色
                                        ref["card"].style(replace=_strategy_card_style(picked))
                                        ref["check"].set_value(picked)
                                    # 参数页跟随所选模板刷新（仅在 param_state 已创建后才有交互）
                                    if "params" in param_state:
                                        _render_params_panel()

                                with ui.row().classes("w-full").style(
                                    # 显式定宽 + nowrap + overflow-x:auto → 卡片左右滑动
                                    # （宽度不能交给父容器推导，原因见 _card_row_width）
                                    f"width: {_card_row_width()}; flex-wrap: nowrap; "
                                    "overflow-x: auto; overflow-y: hidden; "
                                    "gap: 16px; padding: 8px 24px 16px; align-items: stretch;"
                                ):
                                    if not strategy_templates:
                                        ui.label("暂无可用策略模板").classes("text-sm").style(
                                            f"color: {muted_color}; padding: 12px 0;"
                                        )
                                    for _tpl in strategy_templates:
                                        # 卡片结构/样式见 _build_strategy_card（两个对话框共用）
                                        _card, _check = _build_strategy_card(
                                            _tpl,
                                            picked=False,
                                            on_pick=_apply_card_selection,
                                        )
                                        card_refs[str(_tpl.get("name", ""))] = {
                                            "card": _card, "check": _check,
                                        }

                                # 自定义名称（user_strategy.name，必填，列表按此名称展示）
                                with ui.row().classes("w-full gap-1 items-center").style("padding: 8px 24px 0;"):
                                    ui.label("* 自定义名称").classes("w-[20%] text-xs").style(
                                        field_label_style,
                                    )
                                    custom_name_input: ui.input = (
                                        ui.input(placeholder="请输入策略自定义名称")
                                        .classes("flex-1")
                                        .props("outlined dense")
                                        .style(input_style)
                                    )

                                _section("股票池")
                                pool_name_options: list[str] = list(pool_name_map.keys())

                                with ui.column().classes("w-full gap-3 flex-wrap").style("padding: 8px 24px 0; margin-top: 10px"):
                                    with ui.row().classes("w-full gap-1 items-center"):
                                        ui.label("* 股票池").classes("w-[20%] text-xs").style(
                                            field_label_style,
                                        )
                                        pool_name_select: ui.select = (
                                            ui.select(
                                                options=pool_name_options,
                                                value=(
                                                    pool_name_options[0]
                                                    if pool_name_options else None
                                                ),
                                            )
                                            .props("outlined dense").style(
                                                "flex: 1; min-width: 100px; border-bottom: 1px solid {border_color}"
                                            )
                                        )
                                    with ui.row().classes("w-full gap-1 items-center"):
                                        ui.label("初始资金 (元)").classes("w-[20%] text-xs").style(
                                            field_label_style
                                        )
                                        capital_input: ui.number = ui.number(
                                            value=1000000.0, format="%.0f",
                                        ).classes("w-[30%]").props("outlined dense").style(input_style)

                                    with ui.row().classes("w-full gap-1 items-center"):
                                        ui.label("最大持仓数").classes("w-[20%] text-xs").style(
                                            field_label_style,
                                        )
                                        max_stock_count_input: ui.number = ui.number(
                                            value=4, format="%d",
                                        ).classes("w-[30%]").props("outlined dense").style(input_style)

                                    # 创建后是否立即运行：默认勾选 → status=running；
                                    # 取消勾选 → status=stopped（保存时决定，见 _save）
                                    with ui.row().classes("w-full gap-1 items-center"):
                                        ui.label("运行状态").classes("w-[20%] text-xs").style(
                                            field_label_style,
                                        )
                                        run_now_checkbox: ui.checkbox = (
                                            ui.checkbox("立即运行", value=True)
                                            .classes("text-sm")
                                            .style(f"color: {font_color};")
                                        )
                                        ui.label("勾选后创建完成即开始运行").classes(
                                            "text-xs"
                                        ).style(f"color: {muted_color};")

                    # ---- 参数 tab：策略运行参数（高度随参数数量自适应）----
                    with ui.tab_panel("参数").classes("p-0"):
                        params_tab_container: ui.column = (
                            ui.column().classes("w-full gap-0")
                        )

                # 参数页状态与渲染（在控件创建后定义，初始调用一次）
                param_state: dict[str, Any] = {"collector": {}, "params": {}}

                def _render_params_panel() -> None:
                    """根据当前勾选的策略模板重新渲染参数页（参数区高度随参数数量自适应）。"""
                    params_tab_container.clear()
                    current_name: str = str(selected_state.get("name", "") or "")
                    # 尚未勾选任何卡片：给出引导，而不是让参数页显示成"该策略暂无参数配置"
                    if not current_name:
                        with params_tab_container, ui.column().classes(
                            "w-full items-center justify-center"
                        ).style("padding: 56px 24px;"):
                            ui.icon("touch_app").style(f"color: {muted_color}; font-size: 40px;")
                            ui.label("请先在「策略」标签页勾选一个策略模板").classes(
                                "text-sm"
                            ).style(f"color: {muted_color}; margin-top: 10px;")
                        param_state["collector"] = {}
                        param_state["params"] = {}
                        return
                    tpl = strategy_name_map.get(current_name, {})
                    params: dict[str, Any] = tpl.get("default_params", {}) or {}
                    param_state["params"] = params
                    with params_tab_container:  # noqa: SIM117
                        with ui.scroll_area().classes("w-full"):
                            params_container: ui.column = (
                                ui.column().classes("w-full gap-0").style("padding: 16px 24px;")
                            )
                    param_state["collector"] = _render_params_container(
                        params_container, params, None,
                        input_bg=input_bg, font_color=font_color,
                        title_color=title_color, muted_color=muted_color,
                    )

                _render_params_panel()

                # ---- 底部按钮区 ----
                with ui.row().classes("w-full items-center justify-end gap-3").style(
                    f"padding: 16px 24px; border-top: 1px solid {border_color};"
                ):
                    ui.button("取消", on_click=dialog.close).props("flat no-caps").classes(
                        "text-sm"
                    ).style(f"color: {muted_color};")

                    def _save() -> None:
                        """保存策略：创建用户关联（策略模板已存在）。"""
                        # 策略模板来自卡片勾选（左上角勾选框），不再是下拉框
                        name_value: str = str(selected_state.get("name", "") or "").strip()
                        if not name_value:
                            ui.notify("请点击卡片左上角勾选策略模板", type="warning")
                            return
                        custom_name: str = str(custom_name_input.value or "").strip()
                        if not custom_name:
                            ui.notify("请输入策略自定义名称", type="warning")
                            return
                        tpl = strategy_name_map.get(name_value, {})
                        strategy_id: str = str(tpl.get("_id", "") or tpl.get("id", ""))
                        if not strategy_id:
                            ui.notify("所选策略模板无效", type="warning")
                            return
                        pool_name: str = str(pool_name_select.value or "").strip()
                        if not pool_name:
                            ui.notify("请选择股票池", type="warning")
                            return
                        pool_dao = pool_name_map.get(pool_name, {})
                        pool_id: str = str(pool_dao.get("_id", "") or pool_dao.get("id", ""))
                        if capital_input and capital_input.value and capital_input.value <= 0.0:
                            ui.notify("请输入初始模拟资金")
                            return

                        # 「立即运行」勾选 → 创建即为运行中；取消勾选 → 创建为已停止
                        create_status: str = (
                            "running" if run_now_checkbox.value else "stopped"
                        )

                        try:
                            result = AppContext().user_strategy_api.create(
                                strategy_id=strategy_id,
                                pool_id=pool_id,
                                status=create_status,
                                initial_amount=float(capital_input.value or 0.0),
                                max_stock_count=int(max_stock_count_input.value or 0),
                                strategy_params=_collect_params(
                                    param_state.get("collector", {})
                                ),
                                name=custom_name,
                            )
                            user_strategy_id = result.get("_id", result.get("id", ""))
                            # 仅在后端**没回填**该字段时兜底：后端若已经给出状态
                            # （例如启动失败被判为 error），不能拿本次选择覆盖它。
                            if not str(result.get("status", "") or "").strip():
                                result["status"] = create_status
                            if create_status == "running" and not result.get("update_time"):
                                # 监控页的「已运行」以 update_time 起算，就地补一个现在时刻
                                result["update_time"] = cur_date_time_str()
                            UserStrategyManager().update_user_strategy(user_strategy_id, result)
                        except httpx.HTTPStatusError as exc:
                            if exc.response.status_code == 409:
                                ui.notify("该策略已关联，请在列表中编辑，请勿重复创建", type="warning")
                                return
                            logger.exception("创建策略失败")
                            ui.notify(f"创建策略失败: {exc}", type="negative")
                            return
                        except Exception as exc:
                            logger.exception("创建策略失败")
                            ui.notify(f"创建策略失败: {exc}", type="negative")
                            return
                        dialog.close()
                        ui.notify(
                            "创建成功，策略已开始运行"
                            if create_status == "running"
                            else "创建成功",
                            type="positive",
                        )
                        on_saved()

                    ui.button("保存", on_click=_save).props("unelevated no-caps").classes(
                        "text-sm font-medium"
                    ).style(
                        f"background-color: {primary_color}; color: var(--qs-on-accent); "
                        "border-radius: 8px; padding: 8px 20px;"
                    )

        dialog.open()

    # ---- 编辑策略对话框 ----

    def _open_edit_strategy_dialog(row: dict[str, Any], on_saved: Callable[[], None]) -> None:
        """打开编辑策略对话框：可修改自定义名称、股票池、初始资金、最大持仓数量与运行状态。

        运行状态可在此直接切换（等价于监控卡片上的启动/暂停/停止），保存后落库并
        同步会话缓存。

        :param row: 表格行数据（含 us_id/pool_id/initial_amount/max_stock_count/status）
        :param on_saved: 保存成功后的回调（用于刷新表格）
        """
        field_label_style = f"font-size: 0.875rem; font-weight: 500; color: {field_label_color};"
        input_style = f"flex: 1; background-color: {input_bg}; color: {font_color}; width: 100%;"

        # 加载股票池列表
        all_pools_list: list[dict[str, Any]] = []
        try:
            all_pools_list = AppContext().pool_api.list()
        except Exception:
            logger.exception("查询股票池失败")
        pool_name_map: dict[str, dict[str, Any]] = {
            str(s.get("name", "")): s for s in all_pools_list
            if s.get("name", "")
        }
        pool_name_options: list[str] = list(pool_name_map.keys())

        # 当前选中的股票池名（根据行的 pool_id 反查）
        current_pool_name: str = ""
        current_pool_id: str = str(row.get("pool_id", "") or "")
        for pname, pdao in pool_name_map.items():
            if str(pdao.get("_id", "") or pdao.get("id", "")) == current_pool_id:
                current_pool_name = pname
                break

        # 运行状态下拉选项：默认选中当前状态。当前状态不在可选集合里时
        # （例如后端判定的「异常」）临时补进选项 —— ui.select 的 value 若不在
        # options 中会显示成空白，用户一保存就会把状态改成别的值。
        current_status: str = str(row.get("status", "") or "stopped")
        status_options: dict[str, str] = dict(_EDITABLE_STATUS_TEXTS)
        if current_status not in status_options:
            status_options[current_status] = _STATUS_STYLES.get(
                current_status, (current_status,)
            )[0]

        # 加载策略模板的默认参数与已保存的参数（模板走进程级缓存，避免重复请求）
        tpl: dict[str, Any] = {}
        default_params: dict[str, Any] = {}
        try:
            tpl = AppContext().strategy_cache.get(row["sid"])
            default_params = dict(tpl.get("default_params", {}) or {})
        except Exception:
            logger.warning("查询策略模板失败: %s", row["sid"], exc_info=True)
        if not isinstance(tpl, dict):
            tpl = {}
        saved_params: dict[str, Any] = row.get("strategy_params", {}) or {}
        # 顶部只读卡片的模板信息：模板查询失败时逐项回退行数据，
        # 保证卡片仍有名称 / 类型 / 描述可展示（而不是变成空卡片）
        card_tpl: dict[str, Any] = {
            "name": str(tpl.get("name", "") or row.get("name", "") or row.get("sid", "")),
            "strategy_type": str(tpl.get("strategy_type", "") or row.get("stype", "") or "-"),
            "description": str(
                tpl.get("description", "") or row.get("description", "") or "暂无描述"
            ),
        }

        dialog = ui.dialog()
        with dialog:
            # 宽度与创建对话框一致（w-[760px]，见 _DIALOG_WIDTH_PX）：
            # 两个对话框等高同宽，避免编辑框出现"又窄又高"的失衡观感
            card = ui.card().classes("w-[760px] max-w-[95vw] gap-0").style(
                f"background-color: {card_bg}; border: 1px solid {border_color}; "
                f"border-radius: 12px; padding: 0; "
                f"display: flex; flex-direction: column; overflow: hidden; "
                # 与创建对话框同一策略：定高，内容区滚动条高度由 flex 链分配
                # （见 _dialog_card_height / .strategy-dialog-panels）。
                f"height: {_dialog_card_height()};"
            )
            with card:
                # ---- 头部 ----
                with ui.row().classes("w-full items-center justify-between").style(
                    f"padding: 16px 24px; border-bottom: 1px solid {border_color};"
                ):
                    ui.label(f"编辑策略 — {row['name']}").classes(
                        "text-xl font-bold"
                    ).style(f"color: {title_color};")
                    ui.button(icon="close", on_click=dialog.close).props(
                        "flat round dense"
                    ).style(f"color: {muted_color};")

                # ---- 标签页（策略 / 参数） ----
                with ui.tabs().classes("w-full strategy-dialog-tabs").props("dense") as edit_tabs:
                    ui.tab("策略").classes("text-sm")
                    ui.tab("参数").classes("text-sm")
                with ui.tab_panels(edit_tabs, value="策略").classes("w-full flex-1 strategy-dialog-panels").style(f"background-color: {card_bg};"):
                    # ---- 策略 tab：股票池 + 资金配置 ----
                    with ui.tab_panel("策略").classes("p-0"):  # noqa: SIM117
                        with ui.scroll_area().classes("w-full"):
                            with ui.column().classes("w-full gap-3").style("padding: 20px 24px;"):
                                # ---- 当前关联的策略模板（只展示这一张，选中态；编辑时不可更换）----
                                # 卡片与创建对话框同款（见 _build_strategy_card），此处只读：
                                # 不注册点击、鼠标保持默认光标，避免用户误以为能换模板
                                with ui.row().classes("w-full items-center gap-2"):
                                    ui.label("策略模板").classes(
                                        "text-base font-semibold"
                                    ).style(f"color: {title_color};")
                                    ui.label("（编辑时不可更换）").classes("text-xs").style(
                                        f"color: {muted_color};"
                                    )
                                _build_strategy_card(card_tpl, picked=True, stretch=True)

                                # 自定义名称（user_strategy.name，列表显示用）
                                with ui.row().classes("w-full gap-1 items-center"):
                                    ui.label("* 自定义名称").classes("w-[20%] text-xs").style(
                                        field_label_style,
                                    )
                                    custom_name_input: ui.input = ui.input(
                                        # 历史数据可能未设置自定义名称，回退列表当前显示的名称
                                        value=str(
                                            row.get("custom_name", "")
                                            or row.get("name", "")
                                            or ""
                                        ),
                                        placeholder="请输入策略自定义名称",
                                    ).props("outlined dense").style(input_style)
                                # 股票池
                                with ui.row().classes("w-full gap-1 items-center"):
                                    ui.label("* 股票池").classes("w-[20%] text-xs").style(
                                        field_label_style,
                                    )
                                    pool_select: ui.select = (
                                        ui.select(
                                            options=pool_name_options,
                                            value=current_pool_name or None,
                                        )
                                        .props("outlined dense").style(
                                            "flex: 1; min-width: 100px;"
                                        )
                                    )
                                # 初始资金
                                with ui.row().classes("w-full gap-1 items-center"):
                                    ui.label("初始资金 (元)").classes("w-[20%] text-xs").style(
                                        field_label_style,
                                    )
                                    capital_input: ui.number = ui.number(
                                        value=float(row.get("initial_amount", 0.0) or 0.0),
                                        format="%.0f",
                                    ).classes("w-[30%]").props("outlined dense").style(input_style)
                                # 最大持仓数量
                                with ui.row().classes("w-full gap-1 items-center"):
                                    ui.label("最大持仓数").classes("w-[20%] text-xs").style(
                                        field_label_style,
                                    )
                                    max_stock_count_input: ui.number = ui.number(
                                        value=int(row.get("max_stock_count", 0) or 0),
                                        format="%d",
                                    ).classes("w-[30%]").props("outlined dense").style(input_style)
                                # 运行状态：在此直接切换（等价于监控卡片上的启动/暂停/停止）
                                with ui.row().classes("w-full gap-1 items-center"):
                                    ui.label("运行状态").classes("w-[20%] text-xs").style(
                                        field_label_style,
                                    )
                                    status_select: ui.select = (
                                        ui.select(options=status_options, value=current_status)
                                        .classes("w-[30%]")
                                        .props("outlined dense")
                                        .style(input_style)
                                    )
                                    ui.label("保存后立即生效").classes("text-xs").style(
                                        f"color: {muted_color};"
                                    )

                    # ---- 参数 tab：策略运行参数 ----
                    with ui.tab_panel("参数").classes("p-0"):  # noqa: SIM117
                        with ui.scroll_area().classes("w-full"):
                            params_container: ui.column = (
                                ui.column().classes("w-full gap-0").style("padding: 16px 24px;")
                            )

                # 参数页状态与渲染（在控件创建后定义并渲染一次）
                param_state: dict[str, Any] = {"collector": {}}
                param_state["collector"] = _render_params_container(
                    params_container, default_params, saved_params,
                    input_bg=input_bg, font_color=font_color,
                    title_color=title_color, muted_color=muted_color,
                )

                # ---- 底部按钮区 ----
                with ui.row().classes("w-full items-center justify-end gap-3").style(
                    f"padding: 16px 24px; border-top: 1px solid {border_color};"
                ):
                    ui.button("取消", on_click=dialog.close).props("flat no-caps").classes(
                        "text-sm"
                    ).style(f"color: {muted_color};")

                    def _save() -> None:
                        """保存修改的用户策略关联并刷新列表。"""
                        custom_name: str = str(custom_name_input.value or "").strip()
                        if not custom_name:
                            ui.notify("请输入策略自定义名称", type="warning")
                            return
                        pool_name: str = str(pool_select.value or "").strip()
                        if not pool_name:
                            ui.notify("请选择股票池", type="warning")
                            return
                        pool_dao = pool_name_map.get(pool_name, {})
                        pool_id: str = str(
                            pool_dao.get("_id", "") or pool_dao.get("id", "")
                        )
                        # 目标状态：下拉框取不到值时保持原状态，绝不臆造
                        new_status: str = str(
                            status_select.value or current_status or "stopped"
                        )
                        status_changed: bool = new_status != current_status
                        try:
                            params = _collect_params(param_state.get("collector", {}))
                            AppContext().user_strategy_api.update(
                                user_strategy_id=row["us_id"],
                                name=custom_name,
                                pool_id=pool_id,
                                status=new_status,
                                initial_amount=float(capital_input.value or 0.0),
                                max_stock_count=int(max_stock_count_input.value or 0),
                                strategy_params=params,
                            )
                            row["custom_name"] = custom_name
                            row["name"] = custom_name
                            row["pool_id"] = pool_id
                            row["status"] = new_status
                            row["initial_amount"] = float(capital_input.value or 0.0)
                            row["max_stock_count"] = int(max_stock_count_input.value or 0)
                            row["strategy_params"] = params
                            # ⚠ 只能把**业务字段**写回会话缓存，不能把 row 整份合并进去。
                            # row 是「表格展示行」，含 total_profit="0.0%"（已格式化的字符串）、
                            # profit_color、stype/_desc/_signal_* 等展示专用键；真实现的
                            # update_user_strategy 是**浅合并**，整份写入会把缓存里的
                            # total_profit(数值) 覆盖成字符串 "0.0%"，而 _load_rows() 下一次
                            # 会 float("0.0%") ⇒ ValueError ⇒ 表格已 clear 但渲染中断，
                            # 页面表现为「列表什么都不显示了」（监控页读同一份缓存，同样会炸）。
                            cache_patch: dict[str, Any] = {
                                "name": custom_name,
                                "pool_id": pool_id,
                                "status": new_status,
                                "initial_amount": float(capital_input.value or 0.0),
                                "max_stock_count": int(max_stock_count_input.value or 0),
                                "strategy_params": params,
                            }
                            if status_changed:
                                # 监控页的「已运行」以 user_strategy.update_time 起算。
                                # 状态变更时一并就地刷新，卡片才能立刻从本次变更起算，
                                # 不必等会话缓存 60s 节流后的重新拉取（与监控页启停同做法）。
                                stamp: str = cur_date_time_str()
                                row["update_time"] = stamp
                                cache_patch["update_time"] = stamp
                            UserStrategyManager().update_user_strategy(row["us_id"], cache_patch)
                        except Exception as exc:
                            logger.exception("编辑策略失败")
                            ui.notify(f"编辑策略失败: {exc}", type="negative")
                            return
                        dialog.close()
                        if status_changed:
                            ui.notify(
                                f"保存成功，状态已切换为"
                                f"「{status_options.get(new_status, new_status)}」",
                                type="positive",
                            )
                        else:
                            ui.notify("保存成功", type="positive")
                        on_saved()

                    ui.button("保存", on_click=_save).props("unelevated no-caps").classes(
                        "text-sm font-medium"
                    ).style(
                        f"background-color: {primary_color}; color: var(--qs-on-accent); "
                        "border-radius: 8px; padding: 8px 20px;"
                    )

        dialog.open()

    # 初始渲染表格
    _refresh()
