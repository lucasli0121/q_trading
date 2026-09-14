"""
Author: liguoqiang
Date: 2026-07-04
Description: 交易管理模块，负责接收订单、计算执行价格、扣除手续费，并回调通知。
"""

from __future__ import annotations

from datetime import datetime
import logging
import threading
import time
from configparser import ConfigParser
from dataclasses import dataclass
from typing import Any, Callable

from api.client import ApiClient
from api.order import OrderApi
from api.strategy import StrategyApi
from api.user_strategy import UserStrategyApi
from dao.order_dao import OrderDao, OrderStatus
from dao.strategy_execution_dao import PositionItem, StrategyExecutionDao
from trade.notifier import TradeNotifier


@dataclass
class TradeCostConfig:
    """交易成本配置。

    与 cfg/stock.cfg [cost_config] 节的语义一致：
    - buy_slippage: 买入滑点（元），成交价 = 基准价 + buy_slippage
    - sell_slippage: 卖出滑点（元），成交价 = 基准价 - sell_slippage
    - fee_pct: 手续费百分率（%），如 0.02 表示 0.02%
    - fee_low: 最低手续费（元），按比例计算不足时按此收取
    """

    buy_slippage: float = 0.0  # 买入滑点（元）
    sell_slippage: float = 0.0  # 卖出滑点（元）
    fee_pct: float = 0.0  # 手续费百分率（%）
    fee_low: float = 0.0  # 最低手续费（元）


@dataclass
class TradeExecutionResult:
    """订单执行结果。"""

    order: OrderDao  # 对应的订单对象
    execution_price: float  # 实际成交价格
    commission_fee: float  # 实际扣除的手续费
    status: str  # 执行后的订单状态
    action: str  # 交易方向，买入或卖出


class TradeManager:
    """交易管理器，负责调度订单执行与结果通知。"""

    # ---- P1-16：执行记录保存重试与补偿队列 ----
    SAVE_EXECUTION_RETRIES: int = 3  # 最多尝试次数（含首次）
    SAVE_EXECUTION_RETRY_INTERVAL: float = 0.5  # 重试基础间隔（秒），按次数递增
    PENDING_EXECUTION_FILE: str = "log/pending_execution.jsonl"  # 补偿队列文件

    # ---- P1-17：订单本地幂等 ----
    ORDER_KEY_TTL_SECONDS: float = 24 * 60 * 60  # 幂等键保留时长
    MAX_SUBMITTED_ORDER_KEYS: int = 20000  # 幂等键数量上限
    SUBMITTED_ORDER_KEYS_FILE: str = "log/submitted_order_keys.jsonl"  # 幂等键落盘文件

    # ---- P1-27：基准标的 ----
    # 默认**关闭**：后台目前没有获取任何指数行情，配成代码只会周期性打无效请求。
    # 需要启用时在 cfg/stock.cfg [benchmark] code= 填标的代码（指数或 ETF 代理）。
    DEFAULT_BENCHMARK_CODE: str = ""
    BENCHMARK_RETRY_INTERVAL_SECONDS: float = 30 * 60  # 基准不可用后的重试间隔

    def __init__(
        self,
        cost_config: TradeCostConfig | None = None,
        config_path: str = "",
        api_client: ApiClient | None = None,
    ) -> None:
        """初始化管理器。

        :param cost_config: 交易成本配置
        :param config_path: 配置文件路径，空字符串表示使用默认路径 cfg/stock.cfg
        :param api_client: 外部传入的 ApiClient（如 StrategyWorkflow 的 admin client），
                           为 None 时自动创建新实例。
        """
        self.logger = logging.getLogger(__name__)
        from utils.tools import resource_path
        _cfg_path: str = config_path or resource_path("cfg/stock.cfg")
        self.cost_config = cost_config or self._load_cost_config(_cfg_path)

        self._lock = threading.Lock()

        self._api_client = api_client if api_client else ApiClient()
        self._order_api: OrderApi = OrderApi(self._api_client)
        self._strategy_api: StrategyApi = StrategyApi(self._api_client)
        self._user_strategy_api: UserStrategyApi = UserStrategyApi(self._api_client)
        self._notifier: TradeNotifier = TradeNotifier(
            api_client=self._api_client, config_path=_cfg_path
        )
        self._strategy_name_cache: dict[str, str] = {}
        self._stock_name_cache: dict[str, str] = {}
        # 交易日历缓存（P1-27，懒加载）
        self._calendar: Any = None
        self._calendar_checked: bool = False
        # 基准指数代码（P1-27），可在 cfg/stock.cfg [benchmark] code= 覆盖；
        # 显式配置为空字符串表示关闭基准计算
        self._benchmark_code: str = self._load_benchmark_code(_cfg_path)
        # 基准不可用（取数失败/数据不足）后的静默截止时间戳，避免逐笔重试
        self._benchmark_unavailable_until: float = 0.0
        # 本地订单幂等表（P1-17）：client_order_id -> 首次提交时间（epoch 秒）
        # 使用 epoch 秒（而非 monotonic）是为了能落盘并在重启后继续生效。
        self._submitted_order_keys: dict[str, float] = {}
        self._submitted_keys_appended: int = 0  # 已追加到文件的条数（用于触发整理）
        self._submitted_keys_lock: threading.Lock = threading.Lock()
        self._load_submitted_order_keys()

    @staticmethod
    def _load_benchmark_code(config_path: str) -> str:
        """从 cfg/stock.cfg [benchmark] code= 读取基准标的代码（P1-27）。

        取值语义：
        - 配置项缺失 → 默认**关闭**（返回空字符串，与 `DEFAULT_BENCHMARK_CODE` 一致）；
        - 配置项存在但为空（`code=`）→ 关闭基准计算；
        - 配置项存在且非空 → 使用配置值（可填指数代码，或 ETF 代理如 `510300`）。
        当前后台尚未提供指数行情，故默认关闭；后续接入后填上代码即可启用。

        :param config_path: 配置文件路径
        :return: 基准标的代码，空字符串表示关闭
        """
        parser = ConfigParser()
        parser.read(config_path, encoding="utf-8")
        if parser.has_option("benchmark", "code"):
            return parser.get("benchmark", "code").strip()
        return TradeManager.DEFAULT_BENCHMARK_CODE

    def execute_order(self, order: OrderDao) -> TradeExecutionResult:
        """执行订单，应用滑点与手续费，计算盈亏并更新订单状态。

        滑点为绝对金额（元）：买入贵 buy_slippage 元，卖出便宜 sell_slippage 元。
        手续费 = max(成交金额 * fee_pct/100, fee_low)。
        """
        with self._lock:
            action = order.action or "买入"
            base_price = float(order.trade_price or 0.0)
            trade_quantity = int(order.entrust_quantity or 0)
            if action == "卖出":
                execution_price = base_price - self.cost_config.sell_slippage
            else:
                execution_price = base_price + self.cost_config.buy_slippage
            # 成交价不能为小于等于 0 的值
            if execution_price <= 0:
                self.logger.error(
                    "无效成交价: base=%s action=%s", base_price, action
                )
                execution_price = max(base_price, 0.01)

            turnover = trade_quantity * execution_price
            commission_fee = turnover * self.cost_config.fee_pct / 100
            if self.cost_config.fee_low > 0:
                commission_fee = max(commission_fee, self.cost_config.fee_low)

            # 计算盈亏：卖出时根据持仓均价计算收益
            if action == "卖出":
                position_price = float(order.position_price or 0.0)
                cost_basis = position_price * trade_quantity
                revenue = execution_price * trade_quantity
                profit_amount = revenue - cost_basis - commission_fee
                if cost_basis > 0 and trade_quantity > 0:
                    profit_rate = profit_amount / cost_basis
                else:
                    profit_rate = 0.0
            else:
                profit_amount = 0.0
                profit_rate = 0.0

            # 补全订单字段
            order.status = OrderStatus.SUCCESS.value
            order.trade_quantity = trade_quantity
            order.trade_price = execution_price
            order.commission_fee = commission_fee
            order.profit_amount = round(profit_amount, 4)
            order.profit_rate = round(profit_rate, 6)
            order.create_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 买入时持仓均价即成交价
            if action == "买入":
                order.position_price = execution_price

            return TradeExecutionResult(
                order=order,
                execution_price=execution_price,
                commission_fee=commission_fee,
                status=order.status,
                action=action,
            )

    def submit_order(
        self,
        order: OrderDao,
        callback: Callable[[TradeExecutionResult], None] | None = None,
        delay_seconds: float = 0.0,
    ) -> TradeExecutionResult | None:
        """提交订单并在延迟后由定时器执行，初始状态为委托。

        通过 API 创建订单以获取订单 ID，然后根据延迟执行或立即成交。
        """
        with self._lock:
            order.status = OrderStatus.ENTRUST.value

        # 通过 API 保存委托订单，获取服务端分配的订单 ID
        order_id = self._save_order_via_api(order)
        if order_id:
            order.id = order_id

        if delay_seconds <= 0:
            return self._execute_and_notify(order, callback)

        timer_thread = threading.Thread(
            target=self._delayed_execute,
            args=(order, callback, delay_seconds),
            daemon=True,
        )
        timer_thread.start()
        return None

    def _delayed_execute(
        self,
        order: OrderDao,
        callback: Callable[[TradeExecutionResult], None] | None,
        delay_seconds: float,
    ) -> None:
        """后台定时器执行订单。"""
        time.sleep(delay_seconds)
        self._execute_and_notify(order, callback)

    def _execute_and_notify(
        self,
        order: OrderDao,
        callback: Callable[[TradeExecutionResult], None] | None,
    ) -> TradeExecutionResult:
        """执行订单，通过 API 更新成交结果，然后调用回调通知。"""
        result = self.execute_order(order)

        # 执行后通过 API 更新订单（包含盈亏字段）
        if order.id:
            self._update_order_via_api(order)
        else:
            order_id = self._save_order_via_api(order)
            if order_id:
                order.id = order_id

        # 更新策略执行结果（持仓、总收益等）
        self._update_strategy_execution(order)

        strategy_name = self._get_strategy_name(order.user_strategy_id)

        usid: str = order.user_strategy_id
        if result.action == "卖出":
            self._notifier.notify(
                f"时间:{result.order.create_time} 股票:{result.order.stock_code} {result.action} "
                f"数量 {result.order.trade_quantity} "
                f"持仓价 {result.order.position_price} "
                f"成交价 {result.order.trade_price} 状态 {result.status} "
                f"收益 {result.order.profit_amount:.2f}({result.order.profit_rate:.4%})",
                recipient=strategy_name,
                user_strategy_id=usid,
            )
        else:
            self._notifier.notify(
                f"时间:{result.order.create_time} 股票:{result.order.stock_code} {result.action} "
                f"数量 {result.order.trade_quantity} "
                f"成交价 {result.order.trade_price} 状态 {result.status} ",
                recipient=strategy_name,
                user_strategy_id=usid,
            )
        if callback is not None:
            callback(result)
        return result

    def _get_stock_name(self, code: str) -> str:
        """根据股票代码获取股票名称，结果缓存避免重复 API 调用。

        :param code: 股票代码
        :return: 股票名称，查询失败返回空字符串
        """
        if not code:
            return ""
        if code in self._stock_name_cache:
            return self._stock_name_cache[code]
        try:
            # 尝试通过 stock_info_api 查询
            from app_context import AppContext
            info_list: list[dict[str, Any]] = AppContext().stock_info_api.get_by_codes(code)
            if info_list:
                name: str = info_list[0].get("name", "") or ""
                self._stock_name_cache[code] = name
                return name
        except Exception:
            pass
        self._stock_name_cache[code] = ""
        return ""

    def _update_strategy_execution(self, order: OrderDao) -> None:
        """根据成交订单更新策略执行结果：持仓、目前收益、剩余资金，并新增一条执行记录。

        每次持仓变动（买入/卖出）都会计算 current_profit 与 remaining_cash 并入库
        产生一条新的 StrategyExecutionDao 记录；卖出股票时把已实现盈亏计入
        current_profit，并同步到 UserStrategyDao.total_profit（策略运行以来的总收益）。
        最新剩余资金可通过最近一条执行记录获取。
        完善 PositionItem 全部字段：name / buy_time / current_price / profit_rate / profit_amount。
        资金模型：总资产 = 初始资金 + 目前收益（已实现盈利累计）；
        remaining_cash = max(初始资金 + 目前收益 - 持仓成本, 0)。

        :param order: 已执行的订单对象
        """
        user_strategy_id: str = order.user_strategy_id
        exec_data: dict[str, Any] = {}
        try:
            exec_data = self._user_strategy_api.get_latest_execution(user_strategy_id)
        except Exception:
            self.logger.debug(
                "加载最近执行记录失败 user_strategy_id=%s", user_strategy_id, exc_info=True
            )

        execution = StrategyExecutionDao(user_strategy_id=user_strategy_id)
        if exec_data:
            execution.from_db(exec_data)

        # 无历史记录（或加载失败）时，从 UserStrategyDao 补齐初始资金与累计收益，
        # 保证第一条执行记录的 remaining_cash 计算正确，且不抹掉已有的 total_profit。
        user_strategy_data: dict[str, Any] = {}
        need_user_strategy: bool = (
            execution.initial_amount <= 0
            or (not exec_data and execution.current_profit == 0.0)
        )
        if need_user_strategy:
            try:
                user_strategy_data = self._user_strategy_api.get(user_strategy_id)
            except Exception:
                self.logger.debug(
                    "加载用户策略信息失败 user_strategy_id=%s", user_strategy_id, exc_info=True
                )
        if execution.initial_amount <= 0 and isinstance(user_strategy_data, dict):
            execution.initial_amount = float(
                user_strategy_data.get("initial_amount", 0.0) or 0.0
            )
        if (
            not exec_data
            and execution.current_profit == 0.0
            and isinstance(user_strategy_data, dict)
        ):
            execution.current_profit = float(
                user_strategy_data.get("total_profit", 0.0) or 0.0
            )

        # 首次买入时设置起始日期
        if not execution.start_date and order.action == "买入":
            execution.start_date = (
                order.create_time[:10]
                if order.create_time
                else datetime.now().strftime("%Y-%m-%d")
            )

        self._apply_order_to_positions(execution, order)
        # P1-28：按最新分钟行情刷新全部持仓的 current_price
        self._refresh_current_prices(execution, order)
        self._refresh_execution_metrics(execution, order, user_strategy_id)
        # 仅在卖出（profit_amount 非零）时同步 total_profit，避免买入时无意义的 API 调用
        if float(order.profit_amount or 0) != 0:
            self._sync_user_strategy_total_profit(user_strategy_id, execution.current_profit)

    def _refresh_current_prices(
        self, execution: StrategyExecutionDao, order: OrderDao
    ) -> None:
        """按最新分钟行情刷新**全部**持仓的 `current_price`（P1-28）。

        原实现只在成交标的上更新 `current_price`，其余持仓长期停留在上次成交价，
        导致 `profit_amount` / `profit_rate` / 总市值都滞后。
        这里复用 `UserStrategyCache` 的分钟行情快照（workflow 单点写入，无额外 IO），
        取不到当日行情时保留原值并标注 `price_source=按上次成交价估算`。

        :param execution: 策略执行记录（原地修改 positions）
        :param order: 本次成交的订单
        """
        now_str: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ref_day: str = (order.create_time or now_str)[:10]
        from core.user_strategy_cache import UserStrategyCache

        cache: UserStrategyCache = UserStrategyCache()
        from_market: int = 0
        estimated: int = 0

        for pos in execution.positions:
            code: str = str(pos.get("code", "") or "")
            if not code:
                continue
            if code == order.stock_code:
                # 本次成交的标的：成交价就是最新价，比行情快照更准确
                trade_price: float = float(order.trade_price or 0)
                if trade_price > 0:
                    pos["current_price"] = trade_price
                    pos["price_source"] = "本次成交价"
                    continue
            latest_price: float = 0.0
            try:
                bars: list[dict[str, Any]] = cache.get_minute_bars(code, length=1)
                if bars:
                    bar: dict[str, Any] = bars[-1]
                    # 只接受与本次交易日相同的行情，避免把上一交易日的收盘价当现价
                    bar_day: str = UserStrategyCache.day_of(
                        str(bar.get("create_time", ""))
                    )
                    if not ref_day or bar_day == ref_day:
                        latest_price = float(
                            bar.get("price", bar.get("close", 0)) or 0
                        )
            except Exception as exc:  # pragma: no cover - 防御性
                self.logger.debug("刷新持仓现价失败 code=%s: %s", code, exc)
            if latest_price > 0:
                pos["current_price"] = latest_price
                pos["price_source"] = "最新分钟行情"
                from_market += 1
            else:
                pos["price_source"] = "按上次成交价估算"
                estimated += 1

        if execution.positions:
            self.logger.debug(
                "刷新持仓现价完成 user_strategy_id=%s: 行情 %d 只, 估算 %d 只",
                execution.user_strategy_id, from_market, estimated,
            )

    def _refresh_execution_metrics(
        self,
        execution: StrategyExecutionDao,
        order: OrderDao,
        user_strategy_id: str,
    ) -> None:
        """刷新执行数据：目前收益（已实现盈利累计）、持仓未实现盈亏、剩余资金与收益率。

        资金口径（三处统一：本方法 / StrategyWorkflow._calc_available_cash /
        服务端返回的 remaining_cash）：
            剩余可用资金 = 初始资金 + 已实现盈利 - Σ(持仓数量 × 成本价)
        其中 `cost_price` **含买入手续费**（`(成交金额 + 手续费) / 数量`），
        与历史数据、服务端口径完全一致，因此不需要再单独扣减手续费。

        P1-27：补 `execution_days`（交易日历口径）与 `annualized_return_rate`，
        并尝试接入基准标的同期涨幅作为 `benchmark_return_rate`
        （基准默认关闭，见 `cfg/stock.cfg [benchmark] code=`）。

        :param execution: 策略执行记录
        :param order: 本次成交的订单
        :param user_strategy_id: 用户策略关联 ID
        """
        # 累计已实现收益 = 订单卖出盈亏（订单侧已扣卖出费；持仓成本价含买入费）
        execution.current_profit += float(order.profit_amount or 0)

        # 更新每只持仓的未实现盈亏（成本价已含买入手续费，无需再单独扣费）
        total_invested: float = 0.0
        total_fee: float = 0.0
        for pos in execution.positions:
            qty: int = int(pos.get("quantity", 0))
            cost: float = float(pos.get("cost_price", 0))
            cur: float = float(pos.get("current_price", cost))
            total_invested += qty * cost
            total_fee += float(pos.get("fee", 0) or 0)
            cost_basis: float = qty * cost
            if cost_basis > 0:
                net_profit: float = (cur - cost) * qty
                pos["profit_amount"] = round(net_profit, 2)
                pos["profit_rate"] = round(net_profit / cost_basis * 100, 2)
            else:
                pos["profit_amount"] = 0.0
                pos["profit_rate"] = 0.0

        # 剩余可用资金 = 初始资金 + 已实现盈利 - 持仓成本（成本价已含买入手续费）
        execution.remaining_cash = max(
            execution.initial_amount
            + execution.current_profit
            - total_invested,
            0.0,
        )
        if total_fee > 0:
            self.logger.debug(
                "持仓累计买入手续费（仅审计，已含在成本价中）: %.4f", total_fee
            )

        # 计算总收益率（已实现收益 / 初始资金）
        base_amount: float = execution.initial_amount
        if base_amount <= 0:
            base_amount = total_invested
        if base_amount > 0:
            execution.current_return_rate = execution.current_profit / base_amount
        else:
            execution.current_return_rate = 0.0

        # P1-27：执行天数与年化收益率
        execution.execution_days = self._resolve_execution_days(execution.start_date)
        execution.annualized_return_rate = self._annualize_return_rate(
            execution.current_return_rate, execution.execution_days
        )
        benchmark: float | None = self._resolve_benchmark_return_rate(
            execution.start_date
        )
        if benchmark is not None:
            execution.benchmark_return_rate = benchmark

        execution.update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self._save_execution_with_retry(execution, user_strategy_id)

    def _resolve_execution_days(self, start_date: str) -> int:
        """计算执行天数（P1-27）。

        优先按**交易日历**统计 start_date 至今天的交易日个数（含首尾）；
        `exchange_calendars` 不可用或日期越界时退回自然日口径 `(today - start).days + 1`。

        :param start_date: 策略起始日期 YYYY-MM-DD
        :return: 执行天数，无法计算时返回 0
        """
        day: str = str(start_date or "")[:10]
        if not day:
            return 0
        try:
            start = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            self.logger.debug("start_date 格式非法，无法计算执行天数: %r", start_date)
            return 0
        today = datetime.now().date()
        if today < start:
            return 0
        calendar = self._get_trading_calendar()
        if calendar is not None:
            try:
                sessions = calendar.sessions_in_range(start, today)
                count: int = len(sessions)
                if count > 0:
                    return count
            except Exception as exc:  # pragma: no cover - 日期越界等
                self.logger.debug("交易日历计算执行天数失败，回退自然日: %s", exc)
        return (today - start).days + 1

    def _get_trading_calendar(self) -> Any:
        """获取上交所交易日历（懒加载并缓存；不可用时返回 None）。"""
        if self._calendar_checked:
            return self._calendar
        self._calendar_checked = True
        try:
            import exchange_calendars as xcals

            self._calendar = xcals.get_calendar("XSHG", start="2020-01-01")
        except Exception as exc:  # pragma: no cover - 依赖缺失/日历越界
            self.logger.debug("加载交易日历失败，改用自然日口径: %s", exc)
            self._calendar = None
        return self._calendar

    @staticmethod
    def _annualize_return_rate(
        return_rate: float,
        execution_days: int,
        min_days: int = 20,
        trading_days_per_year: int = 250,
    ) -> float:
        """把区间收益率折算为年化收益率（P1-27）。

        公式：`annualized = (1 + r) ** (250 / days) - 1`。

        **最小样本期保护**：`days < min_days`（默认 20 个交易日）时不计算，返回 0。
        原因是指数放大会把短期噪声无限放大 —— 例如 1 个交易日赚 10%，
        按公式年化可达上亿百分比，对前端展示和用户判断都是有害的。
        样本期不足时返回 0（表示「暂不可计算」），而非给出一个失真的数字。

        :param return_rate: 区间收益率（比率，如 0.05 表示 5%）
        :param execution_days: 执行天数
        :param min_days: 允许计算年化的最小执行天数
        :param trading_days_per_year: 年交易日数（A 股约 250）
        :return: 年化收益率（比率），无法计算时返回 0.0
        """
        if execution_days < max(min_days, 1):
            return 0.0
        base: float = 1.0 + float(return_rate or 0.0)
        if base <= 0:
            # 本金亏光（或更差）时年化定义为 -100%
            return -1.0
        try:
            annualized: float = base ** (trading_days_per_year / execution_days) - 1.0
        except (OverflowError, ValueError):  # pragma: no cover - 极端数值
            return 0.0
        # 兜底夹取，防止极端值污染前端展示
        return round(max(min(annualized, 100.0), -1.0), 6)

    def _resolve_benchmark_return_rate(self, start_date: str) -> float | None:
        """获取基准指数（默认沪深 300）自 start_date 至今的涨幅（P1-27）。

        指数代码可在 `cfg/stock.cfg` 的 `[benchmark] code=` 配置（默认 000300）。

        **降级策略（服务端日K暂不支持指数代码）**：取数失败或数据不足时返回 None，
        调用方保持原有 benchmark_return_rate 不变（不写 0、不写坏数据），并记录
        warning。同一进程内失败后 `BENCHMARK_RETRY_INTERVAL_SECONDS` 内不再重试，
        避免每笔执行记录都打一次注定失败的请求；到期后自动重试一次，
        这样服务端补齐指数支持后无需重启即可恢复。

        若显式把 `[benchmark] code=` 配成空值，则表示**主动关闭**基准计算。

        :param start_date: 策略起始日期 YYYY-MM-DD
        :return: 涨幅比率（如 0.052 表示上涨 5.2%），取不到返回 None
        """
        day: str = str(start_date or "")[:10]
        if not day or not self._benchmark_code:
            return None
        now_ts: float = time.time()
        if now_ts < self._benchmark_unavailable_until:
            return None  # 已知基准不可用，跳过（等待下次重试点）
        end_day: str = datetime.now().strftime("%Y-%m-%d")
        rows: list[dict[str, Any]] = []
        try:
            from api.market import MarketApi

            market_api = MarketApi(self._api_client)
            rows = market_api.get_day_kline(
                code=self._benchmark_code, start=day, end=end_day
            )
        except Exception as exc:
            self.logger.warning(
                "获取基准(%s)行情失败，benchmark_return_rate 保持原值: %s",
                self._benchmark_code, exc,
            )
            self._benchmark_unavailable_until = (
                now_ts + self.BENCHMARK_RETRY_INTERVAL_SECONDS
            )
            return None
        if not isinstance(rows, list) or len(rows) < 2:
            self.logger.warning(
                "基准(%s)日K数据不足（%d 根），benchmark_return_rate 保持原值。"
                "服务端日K暂不支持指数代码，可把 cfg/stock.cfg [benchmark] code= "
                "改为 ETF 代理（如 510300），或置空以关闭基准计算",
                self._benchmark_code, len(rows) if isinstance(rows, list) else 0,
            )
            self._benchmark_unavailable_until = (
                now_ts + self.BENCHMARK_RETRY_INTERVAL_SECONDS
            )
            return None
        closes: list[float] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                close_price: float = float(row.get("close", 0) or 0)
            except (TypeError, ValueError):
                continue
            if close_price > 0:
                closes.append(close_price)
        if len(closes) < 2 or closes[0] <= 0:
            return None
        self._benchmark_unavailable_until = 0.0  # 本次成功，清除不可用标记
        return round((closes[-1] - closes[0]) / closes[0], 6)

    # ---- 执行记录持久化（P1-16：重试 + 补偿队列） ----

    def _save_execution_with_retry(
        self, execution: StrategyExecutionDao, user_strategy_id: str
    ) -> bool:
        """保存执行记录：有限次重试，仍失败则落盘补偿队列（P1-16）。

        原实现失败只记一条日志，该次持仓/资金变动就永久丢失，
        下一次执行记录会基于旧的快照计算，导致 remaining_cash、current_profit 漂移。
        现改为：最多重试 `SAVE_EXECUTION_RETRIES` 次（间隔递增），
        全部失败后把请求体写入 `log/pending_execution.jsonl`，
        由 `flush_pending_executions()` 在下次启动时补投。

        :param execution: 执行记录
        :param user_strategy_id: 用户策略关联 ID
        :return: 是否保存成功（含写入补偿队列后返回 False）
        """
        payload: dict[str, Any] = {
            "user_strategy_id": user_strategy_id,
            "current_return_rate": execution.current_return_rate,
            "current_profit": execution.current_profit,
            "annualized_return_rate": execution.annualized_return_rate,
            "benchmark_return_rate": execution.benchmark_return_rate,
            "positions": execution.positions,
            "initial_amount": execution.initial_amount,
            "remaining_cash": execution.remaining_cash,
            "start_date": execution.start_date,
            "execution_days": execution.execution_days,
        }
        attempts: int = max(int(self.SAVE_EXECUTION_RETRIES), 1)
        last_err: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                self._user_strategy_api.save_execution(**payload)
                self.logger.info(
                    "strategy execution updated via API, user_strategy_id=%s (第 %d 次)",
                    user_strategy_id, attempt,
                )
                return True
            except Exception as err:
                last_err = err
                self.logger.warning(
                    "保存执行记录第 %d/%d 次失败 user_strategy_id=%s: %s",
                    attempt, attempts, user_strategy_id, err,
                )
                if attempt < attempts:
                    # 递增退避，给服务端短暂的恢复窗口
                    time.sleep(self.SAVE_EXECUTION_RETRY_INTERVAL * attempt)

        self.logger.error(
            "保存执行记录连续 %d 次失败，转入补偿队列 user_strategy_id=%s: %s",
            attempts, user_strategy_id, last_err,
        )
        self._enqueue_pending_execution(payload)
        return False

    def _enqueue_pending_execution(self, payload: dict[str, Any]) -> None:
        """把失败的执行记录请求体追加到补偿队列文件（JSON Lines）。"""
        import json
        import os

        try:
            from utils.tools import resource_path

            path: str = resource_path(self.PENDING_EXECUTION_FILE)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            record: dict[str, Any] = {
                "enqueue_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "payload": payload,
            }
            with open(path, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.logger.info(
                "执行记录已写入补偿队列: file=%s, user_strategy_id=%s",
                path, payload.get("user_strategy_id", ""),
            )
        except Exception as exc:  # pragma: no cover - 落盘失败只能记日志
            self.logger.error("写入补偿队列失败: %s", exc, exc_info=True)

    def flush_pending_executions(self) -> int:
        """补投补偿队列中遗留的执行记录（进程启动时调用）。

        逐条重新提交；成功的记录移除，失败的保留以便下次再试。
        多条记录中同一 user_strategy_id 只补投**最后一条**（最早的中间态已无意义）。

        :return: 成功补投的记录条数
        """
        import json
        import os

        from utils.tools import resource_path

        path: str = resource_path(self.PENDING_EXECUTION_FILE)
        if not os.path.exists(path):
            return 0
        records: list[dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item: Any = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict) and isinstance(item.get("payload"), dict):
                        records.append(item)
        except Exception as exc:
            self.logger.error("读取补偿队列失败: %s", exc, exc_info=True)
            return 0
        if not records:
            return 0

        # 同一用户策略只保留最后一条（后续记录包含更新的持仓与资金状态）
        latest_by_user: dict[str, dict[str, Any]] = {}
        for item in records:
            usid: str = str(item["payload"].get("user_strategy_id", ""))
            latest_by_user[usid] = item

        succeeded: int = 0
        failed: list[dict[str, Any]] = []
        for usid, item in latest_by_user.items():
            try:
                self._user_strategy_api.save_execution(**item["payload"])
                succeeded += 1
                self.logger.info("补偿队列补投成功 user_strategy_id=%s", usid)
            except Exception as exc:
                failed.append(item)
                self.logger.warning("补偿队列补投失败 user_strategy_id=%s: %s", usid, exc)

        # 重写队列：只保留仍然失败的记录
        try:
            if failed:
                with open(path, "w", encoding="utf-8") as fp:
                    for item in failed:
                        fp.write(json.dumps(item, ensure_ascii=False) + "\n")
            else:
                os.remove(path)
                self.logger.info("补偿队列已清空: file=%s", path)
        except Exception as exc:  # pragma: no cover
            self.logger.error("重写补偿队列失败: %s", exc, exc_info=True)
        return succeeded

    def _apply_order_to_positions(
        self, execution: StrategyExecutionDao, order: OrderDao
    ) -> float:
        """按成交订单增/减持仓（买入加仓加权成本，卖出减仓或清仓）。

        P0-26：买入手续费**不计入 cost_price**，而是累加到持仓的 `fee` 字段，
        使「资金占用 = quantity * cost_price + fee」；卖出时按卖出比例分摊
        扣减 `fee`，并把扣减掉的手续费通过返回值交给调用方，
        由调用方在已实现收益中扣除（买入费 + 卖出费共同影响最终盈亏）。

        X3：买入时记录 `t1_qty` / `t1_day`（当日买入、不可卖部分）；
        卖出（次日）时先清空上一交易日的 `t1_qty`，因为那部分已解禁。

        :param execution: 策略执行记录（会被原地修改 positions）
        :param order: 已执行的订单
        :return: 本次卖出释放（应计入成本冲减）的买入手续费，买入时为 0.0
        """
        positions: dict[str, dict[str, Any]] = {
            p.get("code", ""): p for p in execution.positions
        }
        stock_code: str = order.stock_code
        trade_quantity: int = int(order.trade_quantity or 0)
        execution_price: float = float(order.trade_price or 0)
        commission_fee: float = float(order.commission_fee or 0)
        now_str: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        trade_day: str = (order.create_time or now_str)[:10]

        if order.action == "买入":
            if stock_code in positions:
                # 加仓：加权平均成本（含本次买入手续费）
                old = positions[stock_code]
                old_qty: int = int(old.get("quantity", 0))
                old_cost: float = float(old.get("cost_price", 0))
                new_qty: int = old_qty + trade_quantity
                if new_qty <= 0:
                    return
                new_cost: float = (
                    old_qty * old_cost
                    + trade_quantity * execution_price
                    + commission_fee
                ) / new_qty
                old["quantity"] = new_qty
                old["cost_price"] = round(new_cost, 4)
                old["current_price"] = execution_price
                # 累计买入手续费（仅审计，已含在成本价中）
                old["fee"] = round(float(old.get("fee", 0) or 0) + commission_fee, 4)
                # X3：当日买入部分不可卖；若 t1_day 不是今天说明是隔日加仓，重新起算
                prev_t1_day: str = str(old.get("t1_day", "") or "")[:10]
                prev_t1_qty: int = int(old.get("t1_qty", 0) or 0)
                base_t1: int = prev_t1_qty if prev_t1_day == trade_day else 0
                old["t1_qty"] = base_t1 + trade_quantity
                old["t1_day"] = trade_day
            else:
                # 新建持仓：成本价含买入手续费
                unit_cost: float = execution_price
                if trade_quantity > 0:
                    unit_cost = (
                        trade_quantity * execution_price + commission_fee
                    ) / trade_quantity
                positions[stock_code] = PositionItem(
                    code=stock_code,
                    name=self._get_stock_name(stock_code),
                    quantity=trade_quantity,
                    cost_price=round(unit_cost, 4),
                    current_price=execution_price,
                    profit_rate=0.0,
                    profit_amount=0.0,
                    buy_time=order.create_time or now_str,
                    fee=round(commission_fee, 4),
                    t1_qty=trade_quantity,
                    t1_day=trade_day,
                ).to_dict()
            execution.positions = list(positions.values())
            return

        # ---- 卖出 ----
        if order.action == "卖出" and stock_code in positions:
            old = positions[stock_code]
            old_qty: int = int(old.get("quantity", 0))
            # X3：卖出发生在新的交易日时，上一日买入的部分已经解禁
            prev_t1_day = str(old.get("t1_day", "") or "")[:10]
            if prev_t1_day and prev_t1_day != trade_day:
                old["t1_qty"] = 0
                old["t1_day"] = prev_t1_day
            new_qty: int = old_qty - trade_quantity
            if new_qty <= 0:
                # 全部卖出：清除持仓
                del positions[stock_code]
            else:
                old["quantity"] = new_qty
                old["current_price"] = execution_price
                # 按卖出比例结转剩余持仓的审计用累计手续费（不参与资金计算）
                if old_qty > 0 and trade_quantity > 0 and new_qty > 0:
                    old_fee: float = float(old.get("fee", 0) or 0)
                    old["fee"] = round(old_fee * (new_qty / old_qty), 4)

        execution.positions = list(positions.values())

    def _sync_user_strategy_total_profit(
        self, user_strategy_id: str, current_profit: float
    ) -> None:
        """同步 UserStrategyDao.total_profit（策略运行以来的总收益）。

        卖出股票时把已实现盈亏计入 current_profit，同时同步到 total_profit；
        total_profit 等于该策略所有 StrategyExecutionDao 记录收益的总和
        （即最新一条执行记录的累计 current_profit）。
        """
        try:
            self._user_strategy_api.update(
                user_strategy_id=user_strategy_id,
                total_profit=round(current_profit, 2),
            )
            self.logger.info(
                "UserStrategyDao.total_profit 已同步: user_strategy_id=%s, total_profit=%.2f",
                user_strategy_id,
                current_profit,
            )
        except Exception as err:
            self.logger.warning("同步 UserStrategyDao.total_profit 失败: %s", err)

    def _prune_submitted_order_keys(self) -> None:
        """清理过期的本地订单幂等键（需在 `_submitted_keys_lock` 内调用）。"""
        now: float = time.time()
        expired: list[str] = [
            key
            for key, ts in self._submitted_order_keys.items()
            if now - ts > self.ORDER_KEY_TTL_SECONDS
        ]
        for key in expired:
            self._submitted_order_keys.pop(key, None)
        # 极端情况下键数量仍超限时，按时间淘汰最旧的一批
        overflow: int = len(self._submitted_order_keys) - self.MAX_SUBMITTED_ORDER_KEYS
        if overflow > 0:
            oldest = sorted(self._submitted_order_keys.items(), key=lambda kv: kv[1])
            for key, _ in oldest[:overflow]:
                self._submitted_order_keys.pop(key, None)

    def _load_submitted_order_keys(self) -> None:
        """**P1-17**：启动时载入历史幂等键，使幂等保护跨进程重启仍然有效。

        服务端 `/api/order/create` 不接收、也不按 `client_order_id` 去重，
        幂等完全依赖本端。若幂等表只在内存，重启后同一订单（例如补偿队列补投、
        人工重试）会再次 POST 形成重复下单。因此这里从 `log/submitted_order_keys.jsonl`
        恢复最近 TTL 内的键。
        """
        import json
        import os

        from utils.tools import resource_path

        path: str = resource_path(self.SUBMITTED_ORDER_KEYS_FILE)
        if not os.path.exists(path):
            return
        now: float = time.time()
        loaded: int = 0
        try:
            with open(path, "r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item: dict[str, Any] = json.loads(line)
                    except Exception:
                        continue  # 跳过损坏行，不影响启动
                    key: str = str(item.get("k", "") or "")
                    try:
                        ts: float = float(item.get("t", 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    if not key or now - ts > self.ORDER_KEY_TTL_SECONDS:
                        continue  # 空键或已过期
                    self._submitted_order_keys[key] = max(
                        ts, self._submitted_order_keys.get(key, 0.0)
                    )
                    loaded += 1
        except Exception as exc:
            self.logger.warning("载入本地订单幂等键失败: %s", exc)
            return
        self._submitted_keys_appended = loaded
        with self._submitted_keys_lock:
            self._prune_submitted_order_keys()
        if self._submitted_order_keys:
            self.logger.info(
                "载入本地订单幂等键 %d 条（TTL %.0f 秒，文件 %s）",
                len(self._submitted_order_keys), self.ORDER_KEY_TTL_SECONDS, path,
            )

    def _append_order_key_to_file(self, client_order_id: str, ts: float) -> None:
        """把单个幂等键**只追加**落盘（失败不阻断下单，仅退化为内存幂等）。"""
        import json
        import os

        from utils.tools import resource_path

        try:
            path: str = resource_path(self.SUBMITTED_ORDER_KEYS_FILE)
            parent: str = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fp:
                fp.write(json.dumps({"k": client_order_id, "t": ts}) + "\n")
        except Exception as exc:
            self.logger.warning("本地订单幂等键落盘失败（本次仅内存生效）: %s", exc)
            return
        with self._submitted_keys_lock:
            self._submitted_keys_appended += 1

    def _compact_order_keys_file(self) -> None:
        """把幂等键文件重写为「当前仍然有效」的键，避免只追加导致无限增长。

        触发条件：累计追加行数超过 `MAX_SUBMITTED_ORDER_KEYS`。
        重写使用临时文件 + 原子替换，避免中途崩溃损坏文件。
        """
        import json
        import os

        from utils.tools import resource_path

        with self._submitted_keys_lock:
            if self._submitted_keys_appended < self.MAX_SUBMITTED_ORDER_KEYS:
                return
            snapshot: dict[str, float] = dict(self._submitted_order_keys)
        path: str = resource_path(self.SUBMITTED_ORDER_KEYS_FILE)
        tmp: str = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fp:
                for key, ts in snapshot.items():
                    fp.write(json.dumps({"k": key, "t": ts}) + "\n")
            os.replace(tmp, path)
        except Exception as exc:
            self.logger.warning("本地订单幂等键文件整理失败: %s", exc)
            return
        with self._submitted_keys_lock:
            self._submitted_keys_appended = len(snapshot)

    def _mark_order_key_submitted(self, client_order_id: str) -> bool:
        """登记订单幂等键；若该键已提交过则返回 False（P1-17）。

        **先登记后提交**，即「至多一次」语义：即使 POST 因超时失败，
        后续针对同一 client_order_id 的重试也不会再次落单，
        宁可偶尔少一笔订单，也不能重复下单。

        登记后立即追加落盘，使该语义在进程重启后依然成立。

        :param client_order_id: 客户端幂等键
        :return: True 表示首次提交（可以发请求），False 表示重复（应跳过）
        """
        if not client_order_id:
            # 未生成幂等键时不做本地去重（兼容旧调用点）
            return True
        with self._submitted_keys_lock:
            self._prune_submitted_order_keys()
            if client_order_id in self._submitted_order_keys:
                return False
            ts: float = time.time()
            self._submitted_order_keys[client_order_id] = ts
        # 文件 IO 放在锁外，避免拖慢并发下单路径
        self._append_order_key_to_file(client_order_id, ts)
        self._compact_order_keys_file()
        return True

    def _save_order_via_api(self, order: OrderDao) -> str:
        """通过 API 创建订单并返回服务端分配的订单 ID。

        P1-17 幂等保护：同一 `client_order_id` 只会真正 POST 一次。
        原实现中 `submit_order` 与 `_execute_and_notify` 都会在 `order.id` 为空时
        调用本方法，若第一次 POST 超时（服务端其实已创建）就会产生**重复订单**；
        现在第二次调用会被本地幂等表拦下（记录一条 warning，返回空 ID）。

        :param order: 待保存的订单对象
        :return: 服务端返回的订单 ID，失败或重复提交时返回空字符串
        """
        client_order_id: str = str(getattr(order, "client_order_id", "") or "")
        if not self._mark_order_key_submitted(client_order_id):
            self.logger.warning(
                "订单已提交过，跳过重复创建: client_order_id=%s, code=%s, action=%s",
                client_order_id, order.stock_code, order.action,
            )
            return ""
        try:
            resp = self._order_api.create(
                user_strategy_id=order.user_strategy_id,
                stock_code=order.stock_code,
                entrust_quantity=order.entrust_quantity,
                trade_price=order.trade_price,
                trade_quantity=order.trade_quantity,
                position_price=order.position_price,
                profit_rate=order.profit_rate,
                profit_amount=order.profit_amount,
                status=order.status,
                create_time=order.create_time,
                action=order.action,
                client_order_id=client_order_id,
            )
            order_id = str(resp.get("id", ""))
            self.logger.info(
                "order created via API, id=%s, client_order_id=%s",
                order_id, client_order_id,
            )
            return order_id
        except Exception as err:
            self.logger.exception("failed to save order via API: %s", err)
            return ""

    def _update_order_via_api(self, order: OrderDao) -> bool:
        """通过 API 更新已存在的订单全部字段。

        :param order: 待更新的订单对象（须包含有效的 id）
        :return: 更新成功返回 True，失败返回 False
        """
        if not order.id:
            return False
        try:
            self._order_api.update(order.id, order)
            self.logger.info("order updated via API, id=%s", order.id)
            return True
        except Exception as err:
            self.logger.exception("failed to update order via API: %s", err)
            return False

    def _get_strategy_name(self, user_strategy_id: str) -> str:
        """通过 user_strategy_id 解析策略名称，优先使用缓存。

        先通过 UserStrategyApi 获取 user_strategy 记录，
        再通过其 strategy_id 获取 StrategyDao 中的策略名称。

        :param user_strategy_id: 用户策略关联 ID
        :return: 策略名称，解析失败时返回 user_strategy_id 本身
        """
        if user_strategy_id in self._strategy_name_cache:
            return self._strategy_name_cache[user_strategy_id]

        name: str = user_strategy_id
        try:
            # 先查用户策略关联，获取 strategy_id
            us_info: dict[str, Any] = self._user_strategy_api.get(user_strategy_id)
            strategy_id: str = us_info.get("strategy_id", "")
            if strategy_id:
                # 再按 ID 查策略模板，获取策略名称
                info: dict[str, Any] = self._strategy_api.get_by_id(strategy_id)
                name = info.get("name", user_strategy_id) or user_strategy_id
        except Exception:
            self.logger.debug(
                "解析策略名称失败 user_strategy_id=%s", user_strategy_id, exc_info=True
            )

        self._strategy_name_cache[user_strategy_id] = name
        return name

    @staticmethod
    def _load_cost_config(config_path: str) -> TradeCostConfig:
        trade_config = TradeCostConfig()
        parser = ConfigParser()
        parser.read(config_path, encoding="utf-8")
        # 滑点（元）/ 手续费百分率（%）/ 最低手续费（元）
        buy_slip = parser.get("cost_config", "buy_slip", fallback="0.0")
        sell_slip = parser.get("cost_config", "sell_slip", fallback="0.0")
        fee_pct = parser.get("cost_config", "fee_pct", fallback="0.0")
        fee_low = parser.get("cost_config", "fee_low", fallback="0.0")
        trade_config.buy_slippage = float(buy_slip)
        trade_config.sell_slippage = float(sell_slip)
        trade_config.fee_pct = float(fee_pct)
        trade_config.fee_low = float(fee_low)
        return trade_config
    
    # 通知推送已移至 TradeNotifier，通过 self._notifier.notify(...) 调用
