#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-09-17
Description: 策略回测记录页面 —— 查看某个策略的历史回测结果与逐笔成交明细。

入口（两处，都指向本页，各自带上自己的来源标识 ``?source=...``）：
    - 策略监控页卡片右下角「详情回测 >」  → source=monitor
    - 策略列表页操作列的「回测记录」图标按钮 → source=strategies
返回：左上角「← 返回XX」按钮**跟随入口**（从策略列表进来就回策略列表），
来源标识见 core.routes.BACKTEST_SOURCE_KEYS；缺失或非法时回退策略监控。
侧边栏高亮同样跟随来源（由 main.py 把 source 归一化后作为 page_key 传入）。

数据来源：``GET /api/strategy/{strategy_id}/backtest``
（api/strategy.py::StrategyApi.get_backtest），返回该策略**模板**的全部回测记录::

    {"_id": "...", "strategy_id": "<策略模板ID>", "create_time": "2026-09-15 00:05:56",
     "result_data": {"strategy_name": "强势反弹策略",
                     "backtest_return_rate": 0.0543,      # 比例，0.0543 = 5.43%
                     "backtest_profit": 5430.0,
                     "benchmark_return_rate": 0.0,
                     "start_date": "2026-06-01", "end_date": "2026-09-12",
                     "initial_amount": 100000.0,
                     "max_drawdown": 0.0,                 # 比例
                     "frequency": "daily",
                     "trades": [{code, buy_date, sell_date, buy_price, sell_price, profit_pct}],
                     "summary": {total_trades, win_count, ..., sharpe_ratio, alpha, ...}}}

**指标在 result_data 里，不在记录顶层**（dao/backtest_dao.py 的字段说明与此一致）。
页面对两种形态都做兼容：result_data 取不到时回退记录顶层同名字段。

页面结构（2026-09-17 需求方确认）：
    1. 顶部：返回 + 标题（策略名 / 记录条数）+ 刷新；
    2. 左栏「回测历史」：该策略的全部记录按 create_time **倒序**，点击切换；
    3. 右栏详情：关键指标卡（收益率/收益额/基准/最大回撤/年化/夏普）
       + 回测汇总（summary 全字段）+ 交易明细表（trades）。

刻意不做的事（不是漏做）：
    - **不画资金曲线**：后端只给逐笔 trades，没有每日净值序列，画曲线等于编数据；
    - **不做自动轮询**：回测记录只在跑完一次回测时新增，不像实时信号那样每秒变化，
      因此只提供手动「刷新」，不用 PollingTask。

语义提醒：回测按**策略模板 ID** 关联，因此同一个模板被多个用户策略复用时，
这些策略的「详情回测」看到的是**同一份**记录 —— 这是接口设计决定的。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from nicegui import ui

from app_context import AppContext
from core.concurrency import run_blocking
from core.routes import DEFAULT_BACKTEST_SOURCE, backtest_source, route_for_page_key
from utils.timeutils import str_to_date_time

logger = logging.getLogger(__name__)

# 左栏「回测历史」宽度（定宽，不随窗口伸缩；右栏详情吃掉剩余宽度）
HISTORY_WIDTH_PX: int = 320

# 来源页面标识 → 返回按钮文案。
# 只放文案，返回目标由 route_for_page_key(key) 从路由表推导，避免两处各写一份路径。
_BACK_LABELS: dict[str, str] = {
    "monitor": "返回策略监控",
    "strategies": "返回策略列表",
}

# 回测频率文案
_FREQUENCY_TEXTS: dict[str, str] = {
    "daily": "每日",
    "weekly": "每周",
    "monthly": "每月",
}

# 汇总区字段表：(显示标签, summary 里的键, 值类型)
# 值类型决定格式化方式与配色：pct=百分数、money=金额、int=整数、num=小数、text=文本
_SUMMARY_FIELDS: list[tuple[str, str, str]] = [
    ("总交易次数", "total_trades", "int"),
    ("盈利次数", "win_count", "int"),
    ("亏损次数", "loss_count", "int"),
    ("胜率", "win_rate", "pct"),
    ("平均单笔盈亏", "avg_profit", "pct"),
    ("最大单笔盈利", "max_profit", "pct"),
    ("最大单笔亏损", "max_loss", "pct"),
    ("年化收益率", "annualized_return_pct", "pct"),
    ("基准年化", "benchmark_annualized_pct", "pct"),
    ("夏普比率", "sharpe_ratio", "num"),
    ("最大回撤", "max_drawdown_pct", "pct"),
    ("Alpha", "alpha", "num"),
    ("初始资金", "initial_capital", "money"),
    ("期末资金", "final_capital", "money"),
    ("交易日数", "trading_days", "int"),
    ("基准代码", "benchmark_code", "text"),
]


# ---------------------------------------------------------------- 纯函数
# 下面这些函数不碰 UI、不碰主题，单独可测（tests/strategy_backtest_smokecheck.py）。


def _to_float(value: Any) -> float:
    """把任意值安全转成 float。

    :param value: 待转换的值（可能是 None / 空串 / 字符串数字）
    :return: 浮点数；无法转换时为 0.0
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_record(raw: dict[str, Any]) -> dict[str, Any]:
    """把一条后端回测记录归一化成扁平结构。

    兼容两种形态：``result_data`` 包裹（数据库原样，当前后端就是这种）与打平形态。

    :param raw: 后端返回的单条记录
    :return: 含固定键的扁平记录；缺失字段退化为空串 / 0.0，页面不会因此报错
    """
    record: dict[str, Any] = dict(raw or {})
    data: Any = record.get("result_data")
    if not isinstance(data, dict):
        data = {}

    def pick(key: str, default: Any = "") -> Any:
        """先取 result_data，再回退记录顶层。"""
        value: Any = data.get(key)
        if value in (None, ""):
            value = record.get(key)
        return default if value in (None, "") else value

    summary: Any = data.get("summary")
    if not isinstance(summary, dict):
        summary = record.get("summary")
    if not isinstance(summary, dict):
        summary = {}

    trades: Any = data.get("trades")
    if not isinstance(trades, list):
        trades = record.get("trades")
    if not isinstance(trades, list):
        trades = []

    return {
        "record_id": str(record.get("_id", "") or record.get("id", "")),
        # create_time 取**记录顶层**：那是这条回测记录的落库时间；
        # result_data 里也有一份（回测自身的生成时间），仅在顶层缺失时兜底。
        "create_time": str(record.get("create_time") or data.get("create_time") or ""),
        "strategy_id": str(pick("strategy_id")),
        "strategy_name": str(pick("strategy_name")),
        "return_rate": _to_float(pick("backtest_return_rate", 0.0)),
        "profit": _to_float(pick("backtest_profit", 0.0)),
        "benchmark_rate": _to_float(pick("benchmark_return_rate", 0.0)),
        "initial_amount": _to_float(pick("initial_amount", 0.0)),
        "max_drawdown": _to_float(pick("max_drawdown", 0.0)),
        "start_date": str(pick("start_date")),
        "end_date": str(pick("end_date")),
        "frequency": str(pick("frequency")),
        "trades": [t for t in trades if isinstance(t, dict)],
        "summary": summary,
    }


def _sort_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按创建时间**倒序**排列（最新的回测在最前）。

    时间缺失或不可解析的记录排到最后（但仍保留，不丢数据）。

    :param records: 归一化后的记录列表
    :return: 新列表（不改动入参顺序）
    """
    def _key(record: dict[str, Any]) -> tuple[int, datetime]:
        parsed: datetime | None = str_to_date_time(str(record.get("create_time", "")))
        return (1 if parsed else 0, parsed or datetime.min)

    return sorted(records, key=_key, reverse=True)


def _fmt_pct(value: float, *, signed: bool = True, digits: int = 2) -> str:
    """格式化百分数（传入的是**已经是百分数**的值，如 5.43 表示 5.43%）。

    :param value: 百分数
    :param signed: 是否强制带符号（+/-）
    :param digits: 小数位
    :return: 如 "+5.43%" / "0.00%"
    """
    if signed:
        return f"{value:+.{digits}f}%"
    return f"{value:.{digits}f}%"


def _fmt_money(value: float) -> str:
    """格式化金额（原型样式：+¥5,430 / -¥840 / ¥0）。

    :param value: 金额（元）
    :return: 带符号与千分位的金额文案
    """
    if value > 0:
        return f"+¥{value:,.0f}"
    if value < 0:
        return f"-¥{abs(value):,.0f}"
    return "¥0"


def _fmt_number(value: float, digits: int = 3) -> str:
    """格式化小数（去掉无意义的尾随零）。

    :param value: 数值
    :param digits: 最多保留的小数位
    :return: 字符串
    """
    text: str = f"{value:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _sign_token(value: float) -> str:
    """按 A 股惯例取「涨跌」配色令牌：正=红(positive)、负=绿(negative)、零=中性。

    :param value: 数值
    :return: 主题令牌名
    """
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "muted"


def _key_metrics(record: dict[str, Any]) -> list[tuple[str, str, str]]:
    """构建详情区顶部的关键指标卡。

    :param record: 归一化后的记录
    :return: [(标签, 已格式化的值, 配色令牌名)]
    """
    summary: dict[str, Any] = record.get("summary") or {}
    return_pct: float = _to_float(record.get("return_rate")) * 100.0
    profit: float = _to_float(record.get("profit"))
    benchmark_pct: float = _to_float(record.get("benchmark_rate")) * 100.0
    drawdown_pct: float = _to_float(record.get("max_drawdown")) * 100.0
    annual_pct: float = _to_float(summary.get("annualized_return_pct"))
    sharpe: float = _to_float(summary.get("sharpe_ratio"))
    return [
        ("回测收益率", _fmt_pct(return_pct), _sign_token(return_pct)),
        ("回测收益额", _fmt_money(profit), _sign_token(profit)),
        ("基准收益率", _fmt_pct(benchmark_pct), _sign_token(benchmark_pct)),
        # 回撤是风险指标：越大越差 ⇒ 走 negative（绿），为 0 时中性
        ("最大回撤", _fmt_pct(drawdown_pct, signed=False), "negative" if drawdown_pct > 0 else "muted"),
        ("年化收益率", _fmt_pct(annual_pct), _sign_token(annual_pct)),
        ("夏普比率", _fmt_number(sharpe), "text"),
    ]


def _summary_items(summary: dict[str, Any]) -> list[tuple[str, str, str]]:
    """把 summary 转成汇总区要展示的 (标签, 值, 配色令牌) 列表。

    缺失字段显示 "-"（而不是 0），避免把"后端没给"误读成"结果是 0"。

    :param summary: 归一化记录里的 summary 字典
    :return: [(标签, 已格式化的值, 配色令牌名)]
    """
    items: list[tuple[str, str, str]] = []
    for label, key, kind in _SUMMARY_FIELDS:
        if key not in summary or summary.get(key) in (None, ""):
            items.append((label, "-", "muted"))
            continue
        raw: Any = summary.get(key)
        if kind == "text":
            items.append((label, str(raw) or "-", "text"))
        elif kind == "int":
            items.append((label, f"{int(_to_float(raw))}", "text"))
        elif kind == "money":
            value: float = _to_float(raw)
            items.append((label, _fmt_money(value), _sign_token(value)))
        elif kind == "pct":
            value = _to_float(raw)
            items.append((label, _fmt_pct(value), _sign_token(value)))
        else:  # num
            value = _to_float(raw)
            items.append((label, _fmt_number(value), "text"))
    return items


def _trade_rows(trades: list[dict[str, Any]]) -> list[dict[str, str]]:
    """把 trades 转成表格行（值已格式化，颜色交给渲染层）。

    :param trades: 逐笔成交列表
    :return: [{序号/code/buy_date/sell_date/buy_price/sell_price/profit_pct/token}]
    """
    rows: list[dict[str, str]] = []
    for index, trade in enumerate(trades, 1):
        profit_pct: float = _to_float(trade.get("profit_pct"))
        rows.append({
            "index": str(index),
            "code": str(trade.get("code", "") or "--"),
            "buy_date": str(trade.get("buy_date", "") or "")[:10] or "--",
            "sell_date": str(trade.get("sell_date", "") or "")[:10] or "--",
            "buy_price": f"¥{_to_float(trade.get('buy_price')):.2f}",
            "sell_price": f"¥{_to_float(trade.get('sell_price')):.2f}",
            "profit_pct": _fmt_pct(profit_pct),
            "token": _sign_token(profit_pct),
        })
    return rows


# ---------------------------------------------------------------- 页面


def show_strategy_backtest_page(strategy_id: str = "", source: str = "") -> None:
    """策略回测记录页面入口。

    :param strategy_id: 策略模板 ID（由查询参数 ``?strategy_id=...`` 传入）
    :param source: 来源页面标识（由查询参数 ``?source=...`` 传入），
        决定左上角「返回」按钮的文案与去向；缺失或非法时回退策略监控
    """
    theme = AppContext().theme_manager
    bg_color: str = theme.var("page")
    card_bg: str = theme.var("surface")
    input_bg: str = theme.var("surface-alt")
    border_color: str = theme.var("border")
    text_color: str = theme.var("text")
    heading_color: str = theme.var("heading")
    muted_color: str = theme.var("muted")
    faint_color: str = theme.var("faint")
    link_color: str = theme.var("link")
    negative_color: str = theme.var("negative")

    # 页面级 CSS。刻意用**普通字符串**而非 f-string：
    # 下面有 CSS 花括号，f-string 里的裸 {} 会被当表达式求值（运行时 NameError，
    # 而 py_compile 只查语法抓不到），这个坑在监控页踩过一次。
    ui.add_css("""
        /* 回测历史条目：可点击、悬停高亮 */
        .bt-history-item {
            cursor: pointer;
            transition: background-color 0.15s;
        }
        .bt-history-item:hover {
            background-color: rgba(var(--qs-primary-strong-rgb), 0.10);
        }
    """)

    # 页面状态：records = 归一化+排序后的记录，selected_id = 当前选中的记录 _id
    state: dict[str, Any] = {"records": [], "selected_id": ""}

    sid: str = str(strategy_id or "").strip()

    # 返回目标跟随入口：来源标识经白名单归一化后，文案查本模块的表、
    # 路径查路由表（不在这里拼字符串，路由表是路径的唯一来源）。
    back_key: str = backtest_source(source)
    back_label: str = _BACK_LABELS.get(back_key, _BACK_LABELS[DEFAULT_BACKTEST_SOURCE])
    back_route: str = route_for_page_key(back_key)

    def _load_records() -> list[dict[str, Any]]:
        """拉取该策略模板的全部回测记录（阻塞；调用方负责切线程）。

        :return: 原始记录列表
        """
        raw: Any = AppContext().strategy_api.get_backtest(sid)
        if isinstance(raw, dict):
            # 后端若只返回单条（不是列表），也按一条处理
            raw = [raw]
        return [dict(item) for item in (raw or []) if isinstance(item, dict)]

    def _strategy_name() -> str:
        """策略名称：模板缓存优先 → 记录里的 strategy_name → 策略 ID。

        :return: 用于副标题的策略名
        """
        try:
            templates: dict[str, dict[str, Any]] = AppContext().strategy_cache.get_many([sid])
            name: str = str(templates.get(sid, {}).get("name", "") or "")
        except Exception:
            logger.warning("查询策略模板失败: %s", sid, exc_info=True)
            name = ""
        if name:
            return name
        for record in state["records"]:
            if record.get("strategy_name"):
                return str(record["strategy_name"])
        return sid or "未知策略"

    def _go_back() -> None:
        """返回**入口页面**（策略监控或策略列表，由 source 查询参数决定）。"""
        ui.navigate.to(back_route)

    def _selected_record() -> dict[str, Any] | None:
        """取当前选中的记录；没有则返回 None。

        :return: 归一化记录或 None
        """
        for record in state["records"]:
            if str(record.get("record_id", "")) == state["selected_id"]:
                return record
        return None

    def _render_empty_hint(text: str, icon: str = "inbox") -> None:
        """在当前上下文中渲染空态提示。

        :param text: 提示文案
        :param icon: 图标名
        """
        with ui.column().classes("w-full items-center justify-center").style("padding: 48px;"):
            ui.icon(icon).style(f"color: {faint_color}; font-size: 40px;")
            ui.label(text).classes("text-sm").style(f"color: {muted_color}; margin-top: 8px;")

    def _select_record(record_id: str) -> None:
        """切换选中的回测记录并重绘（历史列表选中态 + 右侧详情）。

        :param record_id: 记录 _id
        """
        if state["selected_id"] == record_id:
            return
        state["selected_id"] = record_id
        _render_history()
        _render_detail()

    def _history_item(record: dict[str, Any]) -> None:
        """渲染左栏的一条回测历史。

        :param record: 归一化记录
        """
        record_id: str = str(record.get("record_id", ""))
        selected: bool = record_id == state["selected_id"]
        return_pct: float = _to_float(record.get("return_rate")) * 100.0
        start: str = str(record.get("start_date", "")) or "--"
        end: str = str(record.get("end_date", "")) or "--"
        trade_count: int = len(record.get("trades", []) or [])

        # 选中态：左侧强调边 + 主色半透明底；未选中留一条透明边占位（避免行内容左右跳动）。
        # 注意 f-string 内层用了单引号：'primary' —— 外层是双引号 f-string，
        # 内层再写双引号在 Python < 3.12 是语法错误（PEP 701 才放开）。
        item_style: str = (
            f"padding: 12px 16px 12px 13px; border-bottom: 1px solid {border_color}; "
            "border-left: 3px solid "
            + (f"{link_color}; background-color: {theme.var_rgba('primary', 0.18)};"
               if selected else "transparent;")
        )
        with ui.column().classes("w-full gap-1 bt-history-item").style(item_style).on(
            "click", lambda _id=record_id: _select_record(_id)
        ):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(str(record.get("create_time", ""))[:16] or "--").classes(
                    "text-xs font-medium"
                ).style(f"color: {text_color};")
                ui.label(_fmt_pct(return_pct)).classes("text-xs font-bold").style(
                    f"color: {theme.var(_sign_token(return_pct))};"
                )
            ui.label(f"{start} ~ {end} · {trade_count} 笔").classes("text-xs").style(
                f"color: {faint_color};"
            )

    def _render_history() -> None:
        """渲染左栏「回测历史」列表。"""
        records: list[dict[str, Any]] = state["records"]
        history_count.set_text(f"{len(records)} 条")
        history_container.clear()
        with history_container:
            if not records:
                _render_empty_hint("暂无回测记录")
                return
            for record in records:
                _history_item(record)

    def _render_trade_table(trades: list[dict[str, Any]]) -> None:
        """渲染交易明细表。

        :param trades: 逐笔成交列表
        """
        with ui.card().classes("w-full gap-0 overflow-hidden").style(
            f"background-color: {card_bg}; border: 1px solid {border_color}; "
            "border-radius: 12px; margin-top: 16px;"
        ):
            with ui.row().classes("w-full items-center justify-between").style(
                f"padding: 16px 24px; border-bottom: 1px solid {border_color};"
            ):
                ui.label("交易明细").classes("text-base font-bold").style(f"color: {heading_color};")
                ui.label(f"共 {len(trades)} 笔").classes("text-xs").style(f"color: {muted_color};")

            if not trades:
                _render_empty_hint("本次回测没有产生任何委托成交", icon="inbox")
                return

            with ui.row().classes("w-full items-center").style(
                f"background-color: {input_bg}; border-bottom: 1px solid {border_color}; "
                "padding: 12px 24px;"
            ):
                for header, flex in [
                    ("序号", 0.6), ("股票代码", 1.1), ("买入日期", 1.3), ("卖出日期", 1.3),
                    ("买入价", 1), ("卖出价", 1), ("盈亏率", 1),
                ]:
                    ui.label(header).classes(
                        "text-xs font-semibold uppercase tracking-wider"
                    ).style(f"color: {muted_color}; flex: {flex};")

            rows: list[dict[str, str]] = _trade_rows(trades)
            for index, row in enumerate(rows):
                row_bg: str = input_bg if index % 2 == 0 else card_bg
                with ui.row().classes("w-full items-center").style(
                    f"padding: 12px 24px; background-color: {row_bg};"
                ):
                    ui.label(row["index"]).classes("text-sm").style(f"color: {faint_color}; flex: 0.6;")
                    ui.label(row["code"]).classes("text-sm font-medium").style(
                        f"color: {text_color}; flex: 1.1;"
                    )
                    ui.label(row["buy_date"]).classes("text-sm").style(f"color: {muted_color}; flex: 1.3;")
                    ui.label(row["sell_date"]).classes("text-sm").style(f"color: {muted_color}; flex: 1.3;")
                    ui.label(row["buy_price"]).classes("text-sm").style(f"color: {text_color}; flex: 1;")
                    ui.label(row["sell_price"]).classes("text-sm").style(f"color: {text_color}; flex: 1;")
                    ui.label(row["profit_pct"]).classes("text-sm font-bold").style(
                        f"color: {theme.var(row['token'])}; flex: 1;"
                    )

    def _render_summary(record: dict[str, Any]) -> None:
        """渲染「回测汇总」卡片（区间/频率/本金 + summary 全字段）。

        :param record: 归一化记录
        """
        start: str = str(record.get("start_date", "")) or "--"
        end: str = str(record.get("end_date", "")) or "--"
        frequency: str = str(record.get("frequency", "") or "")
        frequency_text: str = _FREQUENCY_TEXTS.get(frequency, frequency or "未知频率")
        initial: float = _to_float(record.get("initial_amount"))

        with ui.card().classes("w-full gap-0 overflow-hidden").style(
            f"background-color: {card_bg}; border: 1px solid {border_color}; "
            "border-radius: 12px; margin-top: 16px;"
        ):
            with ui.row().classes("w-full items-center justify-between").style(
                f"padding: 16px 24px; border-bottom: 1px solid {border_color};"
            ):
                with ui.row().classes("items-center gap-3"):
                    ui.label("回测汇总").classes("text-base font-bold").style(f"color: {heading_color};")
                    ui.label(
                        f"{start} ~ {end} · {frequency_text} · 初始资金 {_fmt_money(initial)}"
                    ).classes("text-xs").style(f"color: {muted_color};")
                ui.label(f"创建时间 {record.get('create_time', '') or '--'}").classes(
                    "text-xs"
                ).style(f"color: {faint_color};")

            with ui.row().classes("w-full").style("flex-wrap: wrap; padding: 8px 12px 16px 12px;"):
                for label, value, token in _summary_items(record.get("summary") or {}):
                    with ui.column().classes("gap-0").style("flex: 1 0 150px; padding: 12px;"):
                        ui.label(label).classes("text-xs").style(f"color: {muted_color};")
                        ui.label(value).classes("text-sm font-semibold").style(
                            f"color: {theme.var(token)}; margin-top: 4px;"
                        )

    def _record_detail(record: dict[str, Any]) -> None:
        """渲染右栏：关键指标卡 + 汇总 + 交易明细。

        :param record: 归一化记录
        """
        with ui.row().classes("w-full gap-4"):
            for label, value, token in _key_metrics(record):
                with ui.card().classes("gap-0").style(
                    f"flex: 1; min-width: 120px; background-color: {card_bg}; "
                    f"border: 1px solid {border_color}; border-radius: 12px; padding: 16px 20px;"
                ):
                    ui.label(label).classes("text-xs font-bold").style(
                        f"color: {muted_color}; letter-spacing: 0.05em;"
                    )
                    ui.label(value).classes("text-xl font-bold").style(
                        f"color: {theme.var(token)}; margin-top: 6px;"
                    )

        _render_summary(record)
        _render_trade_table(list(record.get("trades", []) or []))

    def _render_detail() -> None:
        """渲染右栏详情区（含各种空态）。"""
        detail_container.clear()
        with detail_container:
            record: dict[str, Any] | None = _selected_record()
            if record is None:
                with ui.card().classes("w-full gap-0").style(
                    f"background-color: {card_bg}; border: 1px solid {border_color}; "
                    "border-radius: 12px; padding: 24px;"
                ):
                    _render_empty_hint("该策略还没有回测记录", icon="history_toggle_off")
                return
            _record_detail(record)

    def _render_error(exc: Exception) -> None:
        """渲染加载失败态：给出原因与「重试」入口。

        不回退成空白页 —— 否则用户分不清"没有记录"还是"请求失败"。

        :param exc: 异常对象
        """
        detail_container.clear()
        with detail_container, ui.card().classes("w-full gap-0 items-center justify-center").style(
            f"background-color: {card_bg}; border: 1px solid {border_color}; "
            "border-radius: 12px; padding: 48px;"
        ):
            ui.icon("error").style(f"color: {negative_color}; font-size: 40px;")
            ui.label("回测记录加载失败").classes("text-base font-bold").style(
                f"color: {heading_color}; margin-top: 12px;"
            )
            ui.label(str(exc)).classes("text-xs").style(f"color: {muted_color}; margin-top: 6px;")
            ui.button("重试", icon="refresh", on_click=_reload).props("flat no-caps").style(
                f"color: {link_color}; margin-top: 12px;"
            )

    def _apply_records(raw_records: list[dict[str, Any]]) -> None:
        """归一化 + 排序 + 渲染（尽量保持当前选中项）。

        :param raw_records: 后端返回的原始记录列表
        """
        records: list[dict[str, Any]] = _sort_records([_normalize_record(r) for r in raw_records])
        state["records"] = records
        record_ids: list[str] = [str(r.get("record_id", "")) for r in records]
        if state["selected_id"] not in record_ids:
            # 默认选中最新一条（倒序后的第一条）
            state["selected_id"] = record_ids[0] if record_ids else ""
        subtitle_label.set_text(f"{_strategy_name()}｜共 {len(records)} 条回测记录")
        _render_history()
        _render_detail()

    async def _reload() -> None:
        """刷新 / 重试：线程池拉取，UI 线程渲染。

        HTTP 调用必须离开事件循环（core.concurrency.run_blocking 还会带上请求
        上下文，工作线程才能读到本会话的 token）。
        """
        try:
            raw_records: list[dict[str, Any]] = await run_blocking(_load_records)
        except Exception as exc:  # noqa: BLE001
            logger.warning("拉取回测记录失败: %s", sid, exc_info=True)
            state["records"] = []
            _render_history()
            _render_error(exc)
            ui.notify(f"回测记录加载失败：{exc}", type="negative")
            return
        _apply_records(raw_records)
        ui.notify("回测记录已刷新", type="positive")

    def _render_missing_id() -> None:
        """没带 strategy_id 进页面时的空态（例如登录回跳丢了查询串）。"""
        subtitle_label.set_text("未指定策略")
        history_count.set_text("0 条")
        history_container.clear()
        with history_container:
            _render_empty_hint("未指定策略")
        detail_container.clear()
        with detail_container, ui.card().classes("w-full gap-0 items-center justify-center").style(
            f"background-color: {card_bg}; border: 1px solid {border_color}; "
            "border-radius: 12px; padding: 48px;"
        ):
            ui.icon("link_off").style(f"color: {faint_color}; font-size: 40px;")
            ui.label("未指定要查看的策略").classes("text-base font-bold").style(
                f"color: {heading_color}; margin-top: 12px;"
            )
            ui.label("请从「策略监控」或「策略列表」的策略条目上点击「详情回测」进入本页。").classes(
                "text-xs"
            ).style(f"color: {muted_color}; margin-top: 6px;")
            ui.button(back_label, icon="arrow_back", on_click=_go_back).props(
                "flat no-caps"
            ).style(f"color: {link_color}; margin-top: 12px;")

    # ---- 页面骨架 ----

    with ui.column().classes("w-full gap-0").style(f"padding: 32px; background-color: {bg_color};"):

        # 顶部：返回 + 标题 + 刷新
        with ui.row().classes("w-full items-center justify-between").style("margin-bottom: 24px;"):
            with ui.row().classes("items-center gap-4"):
                ui.button(back_label, icon="arrow_back", on_click=_go_back).props(
                    "flat no-caps"
                ).style(f"color: {link_color};")
                with ui.column().classes("gap-0"):
                    ui.label("策略回测记录").classes("text-xl font-bold").style(
                        f"color: {heading_color};"
                    )
                    subtitle_label: ui.label = ui.label("").classes("text-xs").style(
                        f"color: {muted_color}; margin-top: 2px;"
                    )
            ui.button("刷新", icon="refresh", on_click=_reload).props("flat no-caps").style(
                f"color: {link_color};"
            )

        # 主体：左栏历史 + 右栏详情
        with ui.row().classes("w-full items-start gap-6"):
            with ui.card().classes("gap-0 overflow-hidden").style(
                f"flex: 0 0 {HISTORY_WIDTH_PX}px; width: {HISTORY_WIDTH_PX}px; "
                f"background-color: {card_bg}; border: 1px solid {border_color}; border-radius: 12px;"
            ):
                with ui.row().classes("w-full items-center justify-between").style(
                    f"padding: 16px 20px; border-bottom: 1px solid {border_color};"
                ):
                    ui.label("回测历史").classes("text-base font-bold").style(f"color: {heading_color};")
                    history_count: ui.label = ui.label("").classes("text-xs").style(
                        f"color: {muted_color};"
                    )
                history_container: ui.element = ui.element("div").classes("w-full")
            detail_container: ui.element = ui.element("div").classes("w-full").style(
                "flex: 1; min-width: 0;"
            )

    # ---- 首屏 ----

    if not sid:
        _render_missing_id()
    else:
        try:
            _apply_records(_load_records())
        except Exception as exc:  # noqa: BLE001
            # 页面构建阶段本就在线程池里，直接同步拉取即可
            logger.warning("拉取回测记录失败: %s", sid, exc_info=True)
            state["records"] = []
            _render_history()
            _render_error(exc)
