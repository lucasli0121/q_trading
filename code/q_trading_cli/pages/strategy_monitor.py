#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-08-12
Description: 策略监控页面 — 策略卡片 + 实时交易信号日志展示
    - 顶部状态栏（实时更新指示灯）
    - 信号统计卡片（全部 / 买入 / 卖出 / 今日）
    - 策略卡片区（结构对齐 doc/prototypes/strategy-monitor.html）：
      **横向排列、可左右滚动**，每张卡片展示该策略的「已运行时长 / 信号触发 /
      当前持仓 / 浮动盈亏 / 最新一条信号」，并提供「启动 / 暂停 / 停止」三个
      操作按钮（写回 user_strategy.status）
    - 卡片右下角「详情回测 >」→ 策略回测记录页（/strategy-backtest?strategy_id=…）
    - 信号筛选标签（全部 / 买入 / 卖出）
    - 实时信号日志表格（时间 / 策略 / 股票代码 / 信号类型 / 成交价格 / 盈亏率 / 原因）
      其中「策略」列显示**用户策略名称**（``user_strategy.name``），「股票代码」列
      代码在上、股票名称在下（2026-09-23 需求方定：去掉盈亏额列、补股票名称）

与原型的差异（均为需求方明确指定，不是实现偏差）：
    1. 卡片排列：原型为 2 列网格，本实现改为**横向排列 + 左右滚动**。
    2. 操作按钮：原型只有「暂停 / 终止」两个，本实现补齐为
       「启动 / 暂停 / 停止」三个，并按当前状态决定各自可用性。
    3. 顶部保留原实现的 4 个信号统计卡（原型没有这一排），位置在策略卡片上方。

卡片字段的数据来源（原型所需字段后端并不都现成，此处说明取舍）：
    - 已运行时长：依赖 ``user_strategy.update_time``（后端在每次状态变更时刷新）。
      **仅当 status == running 时**展示「已运行: X天 Y小时 Z分」；其余状态展示
      状态文案（已停止 / 已暂停）。字段缺失时展示 ``已运行: --``，不臆造时长。
    - 浮动盈亏 = **已实现 + 未实现** 两段之和（需求方 2026-09-23 澄清的口径）：
        · 已实现段 = 该策略历次卖出累计的收益，即最新一条执行记录的 ``current_profit``，
          与 ``user_strategy.total_profit`` 是同一个数（见 q_trading_work/trade/manager.py
          的 ``_sync_user_strategy_total_profit``：卖出时把 current_profit 同步过去）。
          这里取**执行记录**的值而不是会话缓存里的 total_profit，原因是会话缓存的
          ``ensure_loaded()`` 只在**页面进入时**跑（60s 节流），而本页是常驻轮询页，
          停在同一页时该缓存会一直停在进页那一刻；执行记录跟随 30s 快照一起刷新，
          且与持仓同源。记录里缺该字段时（历史数据）回退到 ``user_strategy.total_profit``。
        · 未实现段 = Σ(实时价 − 成本价) × 持仓数量，**现算**。不取持仓里的
          ``profit_rate`` / ``profit_amount``（成交时点值：买入时写入、持仓期间后端
          不参与计算、直到卖出才重算）；持仓期内恒定可用的只有 ``cost_price`` 与 ``quantity``。
      任一持仓缺实时价 ⇒ 该卡片显示 ``--``：不用剩余持仓凑一个会被误读成全仓浮盈的数字，
      也不单独显示已实现段（那同样是残缺值）。
    - 当前持仓：取该策略最新一条执行记录（strategy_execution）的 positions 长度。
      为免逐策略请求，改为 ``list_executions()`` 一次拉取后在内存按
      user_strategy_id 归并，并按 EXECUTION_CACHE_TTL_SECONDS 节流（默认 30s）；
      实时价请求共用同一 TTL 窗口。
    - 信号触发：内存中该策略的信号条数（SignalManager 只保留最近 12 小时、
      每策略上限 MAX_SIGNALS），即「近 12 小时触发次数」，不额外请求后端。

性能说明（对应代码审查清单 CR-01 / CR-03 / CR-04 / CR-16）：
    - 数据获取放在线程池（run_blocking），不再阻塞 asyncio 事件循环；
      轮询改用 PollingTask（带重入保护 + 最小间隔），避免原 ui.timer 在
      回调耗时超过周期时因 sleep(负数) 退化为连续执行、把后端打爆。
    - 策略名称走 AppContext().strategy_cache（进程级缓存，策略模板是全局公共
      数据，多用户共享安全），不再对**每条信号**发起一次 get_by_id() 请求
      （原实现单次刷新最多上千个请求）。信号表按 ``user_strategy_id`` 取
      **用户策略名称**：信号里的 ``strategy_id`` 是策略模板 ID，同模板多实例
      会显示成同一个名字；股票名称走 core.holdings.fetch_stock_name_map
      （进程级缓存，名称是静态数据，稳定后不再产生请求）。
    - 策略卡片按「渲染签名」去重：数据没变就不重建 DOM —— 卡片上带启停按钮，
      若按 1 秒的轮询频率无脑重建，hover 态会被反复重置、观感闪烁。
    - 提示：get_signals_by_user_strategy 的 limit 是**每策略**上限。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from nicegui import ui

from app_context import AppContext
from core.concurrency import PollingTask, run_blocking
from core.holdings import (
    collect_position_codes,
    fetch_price_map,
    fetch_stock_name_map,
    latest_execution_map,
    lookup_stock_name,
    unrealized_profit,
)
from core.routes import backtest_route
from core.user_strategy_manager import UserStrategyManager
from utils import tools

logger = logging.getLogger(__name__)

# 每个策略最多参与展示的信号条数（get_signals_by_user_strategy 的 limit 为每策略上限）
PER_STRATEGY_LIMIT: int = 200

# 轮询周期（秒）
POLL_PERIOD_SECONDS: float = 1.0

# 执行记录（当前持仓）的拉取节流窗口（秒）：
# 持仓只在成交时变化，没必要跟着 1 秒的轮询频率去打后端
EXECUTION_CACHE_TTL_SECONDS: float = 30.0

# 策略卡片宽度（横向滚动，定宽不伸缩；宽度随容器拉伸会让滚动失去意义）
CARD_WIDTH_PX: int = 440

# 目标状态 → 按钮文案
_STATE_LABELS: dict[str, str] = {
    "running": "启动",
    "paused": "暂停",
    "stopped": "停止",
}

# 状态 → 允许迁入的目标状态（决定卡片上三个按钮各自是否可用）
# running：可暂停、可停止；paused：可恢复运行、可停止；stopped：只能启动
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "running": frozenset({"paused", "stopped"}),
    "paused": frozenset({"running", "stopped"}),
    "stopped": frozenset({"running"}),
    "error": frozenset({"running", "stopped"}),
}

# 非 running 时卡片副标题展示的状态文案
_STATUS_TEXTS: dict[str, str] = {
    "running": "运行中",
    "paused": "已暂停",
    "stopped": "已停止",
    "error": "异常",
}

# 卡片图标配色：令牌名 → 默认图标，按卡片序号循环取用。
# 用 dict 而不是 [(令牌, 图标)] 列表：后者是 (str, str) 元组，
# 会被 theme_selfcheck 的 "(标签, 令牌) 对" 扫描误判成颜色通道名。
_CARD_ACCENTS: dict[str, str] = {
    "link": "trending_up",
    "purple": "layers",
    "orange": "insights",
    "negative": "query_stats",
}

# 策略类型 → 卡片图标（命中时覆盖 _CARD_ACCENTS 里的默认图标）
_TYPE_ICONS: dict[str, str] = {
    "选股策略": "filter_alt",
    "盯盘策略": "visibility",
    "复盘策略": "history",
}

# 后端时间字符串的容错格式（首个即本模块写回 update_time 时使用的格式）
_TIME_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
)


def _parse_time(value: str) -> datetime | None:
    """解析后端返回的时间字符串（容错多种格式）。

    :param value: 时间字符串
    :return: datetime；无法解析时返回 None
    """
    text: str = str(value or "").strip()
    if not text:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)  # noqa: DTZ007
        except ValueError:
            continue
    return None


def _format_duration(seconds: float) -> str:
    """把秒数格式化为「X天 Y小时 Z分」（原型文案样式）。

    :param seconds: 时长（秒）
    :return: 时长文案
    """
    total: int = int(max(0.0, seconds))
    if total < 60:
        return "不足 1 分钟"
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes: int = rest // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days}天")
    if days or hours:
        parts.append(f"{hours}小时")
    parts.append(f"{minutes}分")
    return " ".join(parts)


def _runtime_text(card: dict[str, Any]) -> str:
    """构建卡片副标题：运行中展示已运行时长，其余状态展示状态文案。

    运行时长依赖 user_strategy.update_time（后端在每次状态变更时刷新），
    因此只有 status == running 时这个时长才有意义。

    :param card: 策略卡片数据
    :return: 副标题文案
    """
    status: str = str(card.get("status", "stopped"))
    if status != "running":
        return _STATUS_TEXTS.get(status, "未知状态")
    started: datetime | None = _parse_time(str(card.get("update_time", "") or ""))
    if started is None:
        # 后端尚未上线 update_time 字段（或值不可解析）时不臆造时长
        return "已运行: --"
    elapsed: float = (datetime.now() - started).total_seconds()  # noqa: DTZ005
    return f"已运行: {_format_duration(elapsed)}"


def _fmt_profit(value: float) -> str:
    """格式化浮动盈亏（原型样式：+¥4,520 / -¥840）。

    :param value: 盈亏金额（元）
    :return: 带符号与千分位的金额文案
    """
    if value > 0:
        return f"+¥{value:,.0f}"
    if value < 0:
        return f"-¥{abs(value):,.0f}"
    return "¥0"


def _latest_signal_text(card: dict[str, Any]) -> tuple[str, str]:
    """构建卡片底部「最新信号」文案与颜色令牌。

    :param card: 策略卡片数据
    :return: (文案, 颜色令牌名)；无信号时为 ("--", "muted")
    """
    sig: dict[str, Any] | None = card.get("latest_signal")
    if not sig:
        return "--", "muted"
    action: str = str(sig.get("action", "") or "")
    code: str = str(sig.get("stock_code", "") or "")
    time_str: str = str(sig.get("create_time", "") or "")[11:19]
    text: str = " ".join(part for part in (action, code) if part) or "--"
    if time_str:
        text = f"{text} ({time_str})"
    # 中国股市惯例：买入 = 红（positive），卖出 = 绿（negative）
    if action == "买入":
        token: str = "positive"
    elif action == "卖出":
        token = "negative"
    else:
        token = "muted"
    return text, token


def show_strategy_monitor_page() -> None:
    """策略监控页面入口。

    会话策略缓存与内存历史信号由外层 main_page_with_content() 统一准备
    （本页经它注册，见 main.py 的 /strategy-monitor），本函数只负责渲染。
    """
    theme = AppContext().theme_manager
    bg_color: str = theme.var("page")
    font_color: str = theme.var("text")
    heading_color: str = theme.var("heading")
    border_color: str = theme.var("border")
    muted_color: str = theme.var("muted")
    positive_color: str = theme.var("positive")
    negative_color: str = theme.var("negative")
    link_color: str = theme.var("link")
    card_bg: str = theme.var("surface")
    input_bg: str = theme.var("surface-alt")

    # 页面级 CSS。
    # 注意这里刻意用**普通字符串**而不是 f-string：下面要写 CSS 花括号，
    # f-string 里的裸 {} 会被当作表达式求值，运行时抛 NameError（页面 500），
    # 而 py_compile 只查语法抓不到。
    ui.add_css("""
        .log-row:nth-child(even) {
            background-color: var(--qs-surface-alt);
        }
        /* 策略卡片横向滚动区：细化滚动条（原型为 6px 细条） */
        .monitor-card-row::-webkit-scrollbar { height: 6px; }
        .monitor-card-row::-webkit-scrollbar-track { background-color: transparent; }
        .monitor-card-row::-webkit-scrollbar-thumb {
            background-color: var(--qs-border);
            border-radius: 3px;
        }
        /* 卡片上的启停按钮：默认中性；hover 按语义变色（原型：暂停→橙、停止→红） */
        .monitor-card-btn {
            color: var(--qs-muted);
            background-color: var(--qs-surface-alt);
            border-radius: 8px;
            min-width: 40px;
            min-height: 40px;
            padding: 8px;
            transition: color 0.15s, background-color 0.15s;
        }
        .monitor-card-btn:hover { color: var(--qs-link); }
        .monitor-card-btn.monitor-card-btn-paused:hover { color: var(--qs-accent); }
        .monitor-card-btn.monitor-card-btn-stopped:hover { color: var(--qs-positive); }
    """)

    filter_state: dict[str, str] = {"action": "全部"}

    # 最近一次拉取的数据，供筛选切换 / 状态变更时立即重渲染（不必等下一轮轮询）
    last_data: dict[str, Any] = {
        "signals": [],
        "us_names": {},
        "cards": [],
        "stock_names": {},
        "card_sig": None,
    }

    # 执行记录（当前持仓 + 浮动盈亏）的节流状态
    exec_state: dict[str, Any] = {"at": 0.0, "snapshot": {}}

    # ---- 数据获取 ----

    # 实时价的批量查询与「未实现盈亏」现算逻辑已提到 core.holdings 成为公共口径
    # （概要页的资金曲线末点与近期更新策略表也在用同一套算法，避免两处漂移）；
    # 本页只负责收集持仓代码 + 复用同一 TTL 窗口，不再自带实现。

    def _fetch_quote_map(holdings: dict[str, list[dict[str, Any]]]) -> dict[str, float]:
        """批量取持仓股票的实时价（所有策略去重后一次请求）。

        :param holdings: {user_strategy_id: 持仓列表}
        :return: {股票代码: 实时价}；查询失败或无有效价时为空映射
        """
        return fetch_price_map(collect_position_codes(holdings.values()))

    def _fetch_execution_snapshot() -> dict[str, dict[str, Any]]:
        """拉取各用户策略的最新执行快照（持仓只数 + 已实现/未实现盈亏），带 TTL 节流。

        为避免逐策略调用 get_latest_execution（N 次请求），这里用
        list_executions() 一次取回，再在内存里按 user_strategy_id 归并出最新一条。
        该接口返回当前用户的**全部**执行记录（服务端按用户策略 ID 列表一次查完，
        无分页），因此某个策略不在结果里就只意味着它从未成交过。

        未实现盈亏**用实时价与成本价现算**，不取持仓里的 profit_rate / profit_amount
        ——后者是成交时点值（买入时写入、持仓期间后端不参与计算、直到卖出才重算），
        拿它当浮盈等于显示建仓当刻的收益。已实现盈亏取记录的 ``current_profit``
        （与 user_strategy.total_profit 同义，但跟随本快照一起刷新；原因见模块头部说明）。
        实时价另用一次批量行情请求补齐（与执行记录共用同一个 TTL 窗口，避免 1 秒轮询
        把行情接口也打爆）。

        :return: {user_strategy_id: {"count": 持仓只数,
                                     "realized": 累计已实现收益 或 None,
                                     "unrealized": 持仓未实现盈亏 或 None}}
                 ``unrealized`` 为 None 表示算不出（有持仓但缺实时价）；
                 某策略**不在**返回值里表示本次拉取没有它的执行记录。
        """
        now: float = time.monotonic()
        if exec_state["at"] and now - float(exec_state["at"]) < EXECUTION_CACHE_TTL_SECONDS:
            return {k: dict(v) for k, v in exec_state["snapshot"].items()}

        snapshot: dict[str, dict[str, Any]] = {}
        holdings: dict[str, list[dict[str, Any]]] = {}
        try:
            executions: list[dict[str, Any]] = AppContext().user_strategy_api.list_executions()
        except Exception:
            # 失败时沿用上一轮结果，并**照常推进时间戳**：
            # 否则 1 秒轮询会每秒重试一次，把后端打爆。
            logger.warning("查询策略执行结果失败", exc_info=True)
            exec_state["at"] = now
            return {k: dict(v) for k, v in exec_state["snapshot"].items()}

        for us_id, ex in latest_execution_map(executions).items():
            positions: list[dict[str, Any]] = list(ex.get("positions", []) or [])
            holdings[us_id] = positions
            # current_profit 缺失（历史记录）时留 None，由调用方回退到 total_profit
            realized_raw: Any = ex.get("current_profit")
            snapshot[us_id] = {
                "count": len(positions),
                "realized": None if realized_raw is None else tools.to_float(realized_raw),
                "unrealized": None,
            }

        price_map: dict[str, float] = _fetch_quote_map(holdings)
        for us_id, positions in holdings.items():
            snapshot[us_id]["unrealized"] = unrealized_profit(positions, price_map)
        exec_state["snapshot"] = snapshot
        exec_state["at"] = now
        return {k: dict(v) for k, v in snapshot.items()}

    def _fetch_all() -> tuple[
        list[dict[str, Any]], dict[str, str], list[dict[str, Any]], dict[str, str],
    ]:
        """拉取信号、策略名称与策略卡片数据（在线程池中执行，避免阻塞事件循环）。

        策略名称统一走 strategy_cache 批量获取，稳定后不再产生后端请求。

        :return: (按时间倒序的信号列表, {用户策略ID: 用户策略名称},
                  策略卡片数据列表, {股票代码: 股票名称})
        """
        user_strategies: list[dict[str, Any]] = UserStrategyManager().list_user_strategies()
        user_strategy_ids: list[str] = [
            uid for uid in (
                str(us.get("_id", "") or us.get("id", "")) for us in user_strategies
            ) if uid
        ]

        signals: list[dict[str, Any]] = AppContext().signal_manager.get_signals_by_user_strategy(
            user_strategy_ids=user_strategy_ids, limit=PER_STRATEGY_LIMIT,
        )

        # 模板 ID 要同时覆盖「有信号的策略」与「尚无信号但已创建的策略」
        # —— 后者也要在卡片区出现，只是信号数为 0。
        template_ids: set[str] = {
            str(us.get("strategy_id", "") or "") for us in user_strategies
        } | {str(s.get("strategy_id", "") or "") for s in signals}
        template_ids.discard("")
        templates: dict[str, dict[str, Any]] = AppContext().strategy_cache.get_many(
            sorted(template_ids)
        )
        tpl_names: dict[str, str] = {
            sid: str(tpl.get("name", "") or "") for sid, tpl in templates.items()
        }

        # 信号日志的「策略」列显示**用户策略名称**（user_strategy.name）。
        # 信号里的 strategy_id 是**策略模板 ID**，同模板多实例会显示成同一个名字，
        # 必须按 user_strategy_id 归并；名称回退链与卡片一致：自定义名 → 模板名 → ID。
        us_names: dict[str, str] = {}
        for us in user_strategies:
            us_id: str = str(us.get("_id", "") or us.get("id", ""))
            if not us_id:
                continue
            sid: str = str(us.get("strategy_id", "") or "")
            us_names[us_id] = str(
                us.get("name") or tpl_names.get(sid, "") or sid or ""
            )

        # 股票名称：按信号里出现的代码批量取（进程级缓存，名称是静态数据，
        # 稳定后不再产生请求）。取不到的代码在表格里显示 "--"。
        stock_names: dict[str, str] = fetch_stock_name_map(
            str(s.get("stock_code", "") or "") for s in signals
        )

        # 信号按 user_strategy_id 归组。
        # 注意：信号里的 strategy_id 是**策略模板 ID**，多个用户策略共用同一模板时
        # 会撞车，必须用 user_strategy_id 才能分清"这属于哪张策略卡片"。
        # _collect() 已按时间倒序排好，逐条 append 后各组内仍是有序的。
        by_us: dict[str, list[dict[str, Any]]] = {}
        for sig in signals:
            us_id = str(sig.get("user_strategy_id", "") or "")
            if us_id:
                by_us.setdefault(us_id, []).append(sig)

        snapshot: dict[str, dict[str, Any]] = _fetch_execution_snapshot()
        cards: list[dict[str, Any]] = []
        for index, us in enumerate(user_strategies):
            us_id = str(us.get("_id", "") or us.get("id", ""))
            if not us_id:
                continue
            sid = str(us.get("strategy_id", "") or "")
            tpl: dict[str, Any] = templates.get(sid, {})
            items: list[dict[str, Any]] = by_us.get(us_id, [])
            # 浮动盈亏 = 已实现（执行记录的 current_profit）+ 未实现（实时价现算）。
            # 没有执行记录（从未成交）或任一持仓缺实时价时留 None ⇔ 卡片显示 "--"：
            # 只显示其中一段会被读成完整浮盈，比不显示更有害。
            snap: dict[str, Any] = snapshot.get(us_id) or {}
            unrealized: float | None = snap.get("unrealized")
            float_profit: float | None = None
            if unrealized is not None:
                realized: Any = snap.get("realized")
                if realized is None:
                    # 历史执行记录缺 current_profit，回退到同义的 total_profit。
                    # 走 tools.to_float：会话缓存是与策略列表页共享的，那个键可能被写成
                    # 展示格式（"12.5%" / "+¥1,234"），裸 float() 会抛 ValueError 打断整页渲染。
                    realized = us.get("total_profit", 0.0)
                float_profit = tools.to_float(realized) + float(unrealized)
            cards.append({
                "us_id": us_id,
                # 策略模板 ID：回测记录就是按它关联的（/api/strategy/{id}/backtest），
                # 「详情回测」入口要用它构造跳转 URL
                "strategy_id": sid,
                # 名称回退链与策略列表页保持一致：自定义名称 → 模板名 → 策略 ID
                "name": str(us.get("name") or tpl.get("name", "") or sid or "未命名策略"),
                "stype": str(tpl.get("strategy_type", "") or ""),
                "status": str(us.get("status", "stopped") or "stopped"),
                "update_time": str(us.get("update_time", "") or ""),
                # 每张卡片展示**自身**的信号（items 首条即最新）
                "signal_count": len(items),
                "latest_signal": items[0] if items else None,
                "position_count": int(snap.get("count", 0)),
                "float_profit": float_profit,
                "accent_index": index,
            })
        return signals, us_names, cards, stock_names

    # ---- 状态变更（启动 / 暂停 / 停止） ----

    def _apply_status_sync(user_strategy_id: str, target: str) -> None:
        """写入策略状态并同步会话缓存（在线程池中执行，继承会话上下文）。

        :param user_strategy_id: 用户策略 ID
        :param target: 目标状态（running / paused / stopped）
        """
        AppContext().user_strategy_api.update(
            user_strategy_id=user_strategy_id, status=target,
        )
        # 后端会在状态变更时刷新 user_strategy.update_time；这里就地写入同一时刻，
        # 让卡片上的"已运行"立刻从本次启动起算，不必等下一次会话缓存刷新（60s 节流）。
        UserStrategyManager().update_user_strategy(
            user_strategy_id,
            {
                "status": target,
                "update_time": datetime.now().strftime(_TIME_FORMATS[0]),  # noqa: DTZ005
            },
        )

    async def _change_status(card: dict[str, Any], target: str) -> None:
        """变更策略状态并立即重渲染卡片。

        :param card: 策略卡片数据
        :param target: 目标状态（running / paused / stopped）
        """
        us_id: str = str(card.get("us_id", ""))
        action_label: str = _STATE_LABELS.get(target, target)
        try:
            await run_blocking(_apply_status_sync, us_id, target)
        except Exception as exc:  # noqa: BLE001
            logger.exception("策略状态变更失败: %s", us_id)
            ui.notify(f"{action_label}失败：{exc}", type="negative")
            return

        # 就地更新本地数据后强制重渲染（下一轮轮询会用后端数据兜底校正）。
        # 这里按 us_id 重新查一次而不是直接用入参 card：等待 HTTP 期间
        # 轮询可能已经用新列表替换了 last_data["cards"]。
        now_text: str = datetime.now().strftime(_TIME_FORMATS[0])  # noqa: DTZ005
        for item in last_data["cards"]:
            if str(item.get("us_id", "")) == us_id:
                item["status"] = target
                item["update_time"] = now_text
                break
        _render_cards(last_data["cards"], force=True)
        ui.notify(f"「{card.get('name', '')}」已{action_label}", type="positive")

    def _status_button(card: dict[str, Any], target: str, icon: str, label: str) -> None:
        """构建卡片上的一个启停按钮。

        可用性由当前状态决定（见 _ALLOWED_TRANSITIONS），不可用时置为 disabled，
        避免出现「对已停止的策略点暂停」这类无意义请求。

        :param card: 策略卡片数据
        :param target: 目标状态（running / paused / stopped）
        :param icon: 图标名
        :param label: 按钮文案（用于 tooltip）
        """
        status: str = str(card.get("status", "stopped"))
        enabled: bool = target in _ALLOWED_TRANSITIONS.get(status, frozenset({"running"}))
        # 变体类用于 hover 配色：暂停→橙、停止→红（与原型一致）
        # 用集合而不是元组，避免被 theme_selfcheck 的 "(标签, 令牌) 对" 扫描误判
        variant: str = f" monitor-card-btn-{target}" if target in {"paused", "stopped"} else ""

        button: ui.button = ui.button(icon=icon)
        with button:
            ui.tooltip(label)
        button.props("flat dense")
        button.classes(f"monitor-card-btn{variant}")
        if enabled:
            button.on("click", lambda _c=card, _t=target: _change_status(_c, _t))
        else:
            button.disable()

    # ---- 策略卡片渲染 ----

    def _open_backtest(card: dict[str, Any]) -> None:
        """打开该策略的回测记录页。

        ⚠ 回测记录按**策略模板 ID** 关联（GET /api/strategy/{strategy_id}/backtest），
        而卡片代表的是**用户策略**：同一个模板被多个用户策略复用时，它们会看到
        同一份回测记录。这是接口与数据模型决定的，不是串数据。

        模板 ID 缺失（历史数据）时不跳转，给一句提示 —— 跳到没有 ID 的页面上
        只会让用户看到空态，反而更像故障。

        :param card: 策略卡片数据
        """
        sid: str = str(card.get("strategy_id", "") or "").strip()
        if not sid:
            ui.notify("该策略未关联策略模板，无法查看回测记录", type="warning")
            return
        ui.navigate.to(backtest_route(sid, "monitor"))

    def _build_strategy_card(card: dict[str, Any]) -> None:
        """构建单张策略卡片（结构对齐 doc/prototypes/strategy-monitor.html）。

        :param card: 策略卡片数据
        """
        accent_tokens: list[str] = list(_CARD_ACCENTS)
        accent_token: str = accent_tokens[
            int(card.get("accent_index", 0)) % len(accent_tokens)
        ]
        accent: str = theme.var(accent_token)
        accent_bg: str = theme.var_rgba(accent_token, 0.25)
        icon_name: str = _TYPE_ICONS.get(str(card.get("stype", "")), _CARD_ACCENTS[accent_token])

        signal_text, signal_token = _latest_signal_text(card)
        raw_profit: Any = card.get("float_profit")
        profit: float | None = None if raw_profit is None else float(raw_profit or 0.0)
        # 中国股市惯例：盈利 = 红（positive），亏损 = 绿（negative）。
        # profit 为 None（缺实时价算不出未实现段 / 没有执行记录）时显示 "--" 并置灰，
        # 不退回任何快照值、也不只显示已实现段。
        profit_text: str = "--" if profit is None else _fmt_profit(profit)
        profit_color: str = (
            muted_color if profit is None
            else (positive_color if profit > 0 else (negative_color if profit < 0 else muted_color))
        )

        # 卡片定宽：横向滚动区内不允许伸缩，否则卡片会被挤扁、滚动条也不出现
        with ui.card().classes("gap-0").style(
            f"flex: 0 0 {CARD_WIDTH_PX}px; width: {CARD_WIDTH_PX}px; "
            f"max-width: {CARD_WIDTH_PX}px; background-color: {card_bg}; "
            f"border: 1px solid {border_color}; border-radius: 12px; padding: 24px;"
        ):
            # ---- 头部：图标 + 名称/运行时长 + 启停按钮 ----
            with ui.row().classes("w-full items-start justify-between").style(
                "margin-bottom: 24px;"
            ):
                with ui.row().classes("items-center gap-4"):
                    with ui.element("div").classes(
                        "w-12 h-12 rounded-lg flex items-center justify-center"
                    ).style(
                        f"background-color: {accent_bg}; color: {accent}; flex: 0 0 auto;"
                    ):
                        ui.icon(icon_name).style("font-size: 24px;")
                    with ui.column().classes("gap-0"):
                        ui.label(str(card.get("name", ""))).classes(
                            "text-base font-bold"
                        ).style(f"color: {heading_color};")
                        ui.label(_runtime_text(card)).classes("text-xs").style(
                            f"color: {muted_color}; margin-top: 2px;"
                        )
                with ui.row().classes("items-center gap-2"):
                    _status_button(card, "running", "play_arrow", "启动")
                    _status_button(card, "paused", "pause", "暂停")
                    _status_button(card, "stopped", "stop", "停止")

            # ---- 三列指标：信号触发 / 当前持仓 / 浮动盈亏 ----
            with ui.row().classes("w-full items-center").style(
                f"border-top: 1px solid {border_color}; border-bottom: 1px solid {border_color}; "
                "padding: 16px 0; margin-bottom: 16px;"
            ):
                metrics: list[tuple[str, str, str]] = [
                    ("信号触发", f"{int(card.get('signal_count', 0))} 次", font_color),
                    ("当前持仓", f"{int(card.get('position_count', 0))} 只", font_color),
                    ("浮动盈亏", profit_text, profit_color),
                ]
                for position, (label, value, value_color) in enumerate(metrics):
                    divider: str = (
                        f"border-right: 1px solid {border_color};"
                        if position < len(metrics) - 1 else ""
                    )
                    with ui.column().classes("items-center gap-0").style(f"flex: 1; {divider}"):
                        ui.label(label).classes("text-xs font-bold").style(
                            f"color: {muted_color}; letter-spacing: 0.05em;"
                        )
                        ui.label(value).classes("text-lg font-bold").style(
                            f"color: {value_color}; margin-top: 4px;"
                        )

            # ---- 底部：最新信号 + 详情入口 ----
            with ui.row().classes("w-full items-center justify-between"):
                with ui.row().classes("items-center gap-1"):
                    ui.label("最新信号:").classes("text-xs").style(f"color: {muted_color};")
                    ui.label(signal_text).classes("text-xs font-medium").style(
                        f"color: {theme.var(signal_token)};"
                    )
                # 「详情回测」跳转到策略回测记录页（/strategy-backtest?strategy_id=<模板ID>），
                # 该页左上角可返回本页；回测数据来自 /api/strategy/{id}/backtest
                ui.label("详情回测 >").classes("text-xs cursor-pointer").style(
                    f"color: {link_color};"
                ).on("click", lambda _c=card: _open_backtest(_c))

    def _render_cards(cards: list[dict[str, Any]], *, force: bool = False) -> None:
        """渲染策略卡片区（横向排列 + 左右滚动）。

        带「渲染签名」去重：数据没变化就不重建 DOM —— 卡片上有启停按钮，
        按 1 秒的轮询频率无脑重建会让 hover 态反复重置、观感闪烁。

        :param cards: 策略卡片数据列表
        :param force: 为 True 时忽略签名，强制重建（状态变更后使用）
        """
        signature: tuple[Any, ...] = tuple(
            (
                card.get("us_id"),
                card.get("status"),
                card.get("signal_count"),
                card.get("position_count"),
                card.get("float_profit"),
                _runtime_text(card),
                str((card.get("latest_signal") or {}).get("create_time", "")),
            )
            for card in cards
        )
        if not force and signature == last_data["card_sig"]:
            return
        last_data["card_sig"] = signature

        cards_container.clear()
        with cards_container:
            if not cards:
                with ui.column().classes("w-full items-center justify-center").style(
                    f"background-color: {card_bg}; border: 1px solid {border_color}; "
                    "border-radius: 12px; padding: 40px;"
                ):
                    ui.icon("inbox").style("color: var(--qs-faint); font-size: 36px;")
                    ui.label("暂无策略，请先在策略列表创建").classes("text-sm").style(
                        f"color: {muted_color}; margin-top: 8px;"
                    )
                return
            # nowrap + overflow-x: auto → 卡片横向排列并左右滚动。
            # 这里的宽度交给 w-full 即可：容器在页面「有界宽度」的 flex 链内，
            # 不像 ui.scroll_area 的内容层那样收缩到内容宽度。
            with ui.row().classes("w-full monitor-card-row").style(
                "flex-wrap: nowrap; overflow-x: auto; overflow-y: hidden; "
                "gap: 24px; padding-bottom: 8px; align-items: stretch;"
            ):
                for card in cards:
                    _build_strategy_card(card)

    # ---- 筛选标签 ----

    @ui.refreshable
    def filter_tags() -> None:
        """筛选标签组（可刷新，保证选中态与 filter_state 一致）。"""
        with ui.row().classes("gap-2"):
            for lbl, token in [
                ("全部", "link"),
                ("买入", "positive"),
                ("卖出", "negative"),
            ]:
                _filter_tag(lbl, token, filter_state, on_change=_on_filter_change)

    def _on_filter_change() -> None:
        """筛选条件变化：刷新标签选中态，并用已拉取的数据立即重渲染。"""
        filter_tags.refresh()
        _render(
            last_data["signals"], last_data["us_names"],
            last_data["cards"], last_data["stock_names"],
        )

    with ui.column().classes("w-full gap-0").style(f"padding: 32px; background-color: {bg_color};"):

        # ---- 顶部标题栏 ----
        with ui.row().classes("w-full items-center justify-between").style("margin-bottom: 24px;"), ui.row().classes("items-center gap-4"):
            ui.label("策略监控").classes("text-xl font-bold").style("color: var(--qs-heading);")
            with ui.row().classes("items-center gap-1.5").style(
                "padding: 4px 12px; background-color: rgba(var(--qs-negative-rgb), 0.2); border-radius: 9999px;"
            ):
                ui.element("span").classes("w-1.5 h-1.5 rounded-full").style(
                    "background-color: var(--qs-negative); animation: pulse 2s infinite;"
                )
                ui.label("实时更新中").classes("text-xs font-bold").style("color: var(--qs-negative);")

        # ---- 信号统计卡片 ----
        _build_signal_stats_row(card_bg, border_color, font_color, positive_color, negative_color)

        # ---- 策略卡片区（横向滚动，见 _render_cards） ----
        cards_container = ui.element("div").classes("w-full").style("margin-top: 24px;")

        # ---- 信号日志表格 ----
        with ui.card().classes("w-full gap-0 overflow-hidden").style(
            f"background-color: {card_bg}; border: 1px solid {border_color}; "
            f"border-radius: 12px; margin-top: 24px;"
        ):
            # 表头 + 筛选标签
            with ui.row().classes("w-full items-center justify-between").style(
                f"padding: 16px 24px; border-bottom: 1px solid {border_color};"
            ):
                ui.label("实时信号日志").classes("text-lg font-bold").style("color: var(--qs-heading);")
                filter_tags()

            # 表格容器（可刷新）
            table_container = ui.element("div").classes("w-full")

            def _render(
                signals: list[dict[str, Any]],
                us_names: dict[str, str],
                cards: list[dict[str, Any]],
                stock_names: dict[str, str],
            ) -> None:
                """渲染策略卡片与信号表格（在 UI 线程执行，仅做绘制）。

                :param signals: 按时间倒序的信号列表
                :param us_names: 用户策略 ID → 用户策略名称
                :param cards: 策略卡片数据列表
                :param stock_names: 股票代码 → 股票名称
                """
                _render_cards(cards)

                table_container.clear()
                with table_container:
                    action_filter: str = filter_state["action"]
                    if action_filter != "全部":
                        signals = [s for s in signals if s.get("action", "") == action_filter]

                    # 表头
                    with ui.row().classes("w-full items-center").style(
                        f"background-color: {input_bg}; border-bottom: 1px solid {border_color}; "
                        f"padding: 12px 24px;"
                    ):
                        for hdr, flex in [
                            ("时间", 1.5), ("策略", 1.2), ("股票代码", 1.2),
                            ("信号类型", 1), ("成交价格", 1),
                            ("盈亏率", 1), ("原因", 2),
                        ]:
                            ui.label(hdr).classes("text-xs font-semibold uppercase tracking-wider item-center").style(
                                f"color: var(--qs-muted); flex: {flex};"
                            )

                    if not signals:
                        with ui.column().classes("w-full items-center justify-center").style("padding: 60px;"):
                            ui.icon("inbox").style("color: var(--qs-faint); font-size: 48px;")
                            ui.label("暂无交易信号").classes("text-sm").style("color: var(--qs-muted); margin-top: 12px;")
                    else:
                        for i, sig in enumerate(signals):
                            action: str = sig.get("action", "")
                            is_buy: bool = action == "买入"
                            action_color: str = positive_color if is_buy else (negative_color if action else font_color)
                            time_str: str = str(sig.get("create_time", ""))[:19]
                            # 策略列按 user_strategy_id 取用户策略名称：信号里的
                            # strategy_id 是模板 ID，同模板多实例会显示成同一个名字
                            us_id: str = str(sig.get("user_strategy_id", "") or "")
                            sname: str = us_names.get(us_id, "")
                            code: str = str(sig.get("stock_code", ""))
                            stock_name: str = lookup_stock_name(code, stock_names)
                            price: str = _fmt_price(sig.get("trade_price", ""))
                            profit_rate: float = float(sig.get("profit_rate", 0) or 0)
                            reason: str = str(sig.get("reason", "") or "")

                            # 盈亏率颜色（中国股市：红涨绿跌）
                            pr_color: str = positive_color if profit_rate > 0 else (negative_color if profit_rate < 0 else font_color)

                            row_bg: str = input_bg if i % 2 == 0 else card_bg
                            with ui.row().classes("w-full items-center log-row").style(
                                f"padding: 12px 24px; background-color: {row_bg};"
                            ):
                                ui.label(time_str or "--").classes("text-sm item-center").style("color: var(--qs-muted); flex: 1.5;")
                                ui.label(sname or "--").classes("text-sm item-center").style("color: var(--qs-muted); flex: 1.2;")
                                # 代码在上、名称在下（同一列两行）
                                with ui.column().classes("gap-0 item-center").style("flex: 1.2;"):
                                    ui.label(code or "--").classes("text-sm font-medium").style(f"color: {font_color};")
                                    ui.label(stock_name or "--").classes("text-xs").style("color: var(--qs-faint);")
                                ui.label(action or "--").classes("text-sm font-bold uppercase item-center").style(f"color: {action_color}; flex: 1;")
                                ui.label(price).classes("text-sm item-center").style(f"color: {font_color}; flex: 1;")
                                ui.label(f"{profit_rate:+.2f}%").classes("text-sm font-medium item-center").style(f"color: {pr_color}; flex: 1;")
                                ui.label(reason or "-").classes("text-sm truncate").style("color: var(--qs-faint); flex: 2;")

            async def _poll() -> None:
                """轮询任务：线程池拉取数据，UI 线程渲染。"""
                signals, us_names, cards, stock_names = await run_blocking(_fetch_all)
                last_data["signals"] = signals
                last_data["us_names"] = us_names
                last_data["cards"] = cards
                last_data["stock_names"] = stock_names
                _render(signals, us_names, cards, stock_names)

            # 首屏渲染（页面构建阶段本就在线程池中，不必再切线程）
            initial_signals, initial_us_names, initial_cards, initial_stock_names = _fetch_all()
            last_data["signals"] = initial_signals
            last_data["us_names"] = initial_us_names
            last_data["cards"] = initial_cards
            last_data["stock_names"] = initial_stock_names
            _render(initial_signals, initial_us_names, initial_cards, initial_stock_names)

            # 定时刷新（PollingTask：不阻塞事件循环 + 最小间隔保护）
            PollingTask(POLL_PERIOD_SECONDS, _poll, name="策略监控信号表").start()


def _build_signal_stats_row(
    card_bg: str,
    border_color: str,
    font_color: str,
    positive_color: str,
    negative_color: str,
) -> None:
    """构建信号统计卡片行（数据均来自内存，无后端请求）。

    :param card_bg: 卡片背景
    :param border_color: 边框颜色
    :param font_color: 字体颜色
    :param positive_color: 正向色
    :param negative_color: 负向色
    """
    user_strategy_ids: list[str] = UserStrategyManager().get_user_strategy_ids()
    mgr = AppContext().signal_manager
    signals: list[dict[str, Any]] = mgr.get_signals_by_user_strategy(
        user_strategy_ids=list(user_strategy_ids), limit=500,
    )
    total: int = len(signals)
    buy_count: int = sum(1 for s in signals if s.get("action") == "买入")
    sell_count: int = sum(1 for s in signals if s.get("action") == "卖出")
    today_count: int = mgr.get_today_count()

    cards: list[dict[str, Any]] = [
        {"icon": "notifications", "icon_bg": "rgba(var(--qs-primary-rgb), 0.4)", "icon_color": "var(--qs-link)",
         "label": "全部信号", "value": str(total)},
        {"icon": "trending_up", "icon_bg": "rgba(var(--qs-negative-rgb), 0.4)", "icon_color": "var(--qs-negative)",
         "label": "买入信号", "value": str(buy_count)},
        {"icon": "trending_down", "icon_bg": "rgba(var(--qs-positive-rgb), 0.4)", "icon_color": "var(--qs-positive)",
         "label": "卖出信号", "value": str(sell_count)},
        {"icon": "today", "icon_bg": "rgba(var(--qs-orange-rgb), 0.4)", "icon_color": "var(--qs-orange)",
         "label": "今日信号", "value": str(today_count)},
    ]

    with ui.row().classes("w-full gap-6"):
        for card in cards:
            with ui.card().classes("gap-0").style(
                f"flex: 1; min-width: 180px; background-color: {card_bg}; "
                f"border: 1px solid {border_color}; border-radius: 12px; padding: 20px;"
            ), ui.row().classes("w-full items-start justify-between").style("margin-bottom: 12px;"):
                with ui.element("div").classes("w-10 h-10 rounded-lg flex items-center justify-center").style(
                    f"background-color: {card['icon_bg']}; color: {card['icon_color']};"
                ):
                    ui.icon(card["icon"]).style("font-size: 24px;")
                ui.label(card["label"]).classes("text-sm").style("color: var(--qs-muted);")
                ui.label(card["value"]).classes("text-2xl font-bold").style(
                    "color: var(--qs-heading); margin-top: 4px;"
                )


def _filter_tag(
    label: str,
    token: str,
    state: dict[str, str],
    on_change: Any = None,
) -> None:
    """构建一个筛选标签按钮。

    颜色按**令牌名**传入（而不是色值字符串），选中态用同一令牌的半透明底色，
    这样深浅两套主题下标签底色都跟随主色调，且无需解析色值。

    :param label: 标签文字
    :param token: 主题令牌名（如 "positive" / "negative" / "link"）
    :param state: 筛选状态字典
    :param on_change: 状态变化后的回调（用于刷新标签选中态与表格）
    """
    theme = AppContext().theme_manager
    is_active: bool = state["action"] == label
    accent: str = theme.var(token)
    bg: str = theme.var_rgba(token, 0.25) if is_active else "transparent"
    text_color: str = accent if is_active else theme.var("muted")

    ui.label(label).classes("text-xs font-medium rounded px-2 py-1 cursor-pointer").style(
        f"color: {text_color}; background-color: {bg}; "
        f"border: 1px solid {accent if is_active else 'transparent'};"
    ).on("click", lambda _l=label: _set_filter(_l, state, on_change))


def _set_filter(label: str, state: dict[str, str], on_change: Any = None) -> None:
    """设置筛选状态并触发回调。

    原实现只改 state、不触发任何刷新，导致标签选中态永远停在高亮"全部"。

    :param label: 标签文字
    :param state: 筛选状态字典
    :param on_change: 状态变化后的回调
    """
    if state["action"] == label:
        return
    state["action"] = label
    if on_change is not None:
        on_change()


def _fmt_price(value: Any) -> str:
    """安全格式化价格。

    :param value: 价格值
    :return: 格式化字符串
    """
    if value in (None, ""):
        return "--"
    try:
        v: float = float(value)
        return f"¥{v:.2f}"
    except (ValueError, TypeError):
        return str(value)
