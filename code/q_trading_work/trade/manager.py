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
        self._refresh_execution_metrics(execution, order, user_strategy_id)
        # 仅在卖出时（profit_amount 非零）同步 total_profit，避免买入时无意义的 API 调用
        if float(order.profit_amount or 0) != 0:
            self._sync_user_strategy_total_profit(user_strategy_id, execution.current_profit)

    def _apply_order_to_positions(
        self, execution: StrategyExecutionDao, order: OrderDao
    ) -> None:
        """按成交订单增/减持仓（买入加仓加权成本，卖出减仓或清仓）。"""
        positions: dict[str, dict[str, Any]] = {
            p.get("code", ""): p for p in execution.positions
        }
        stock_code: str = order.stock_code
        trade_quantity: int = int(order.trade_quantity or 0)
        execution_price: float = float(order.trade_price or 0)
        now_str: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if order.action == "买入":
            if stock_code in positions:
                # 加仓：加权平均成本，更新当前价
                old = positions[stock_code]
                old_qty: int = int(old.get("quantity", 0))
                old_cost: float = float(old.get("cost_price", 0))
                new_qty: int = old_qty + trade_quantity
                new_cost: float = ((old_qty * old_cost) + (trade_quantity * execution_price)) / new_qty
                old["quantity"] = new_qty
                old["cost_price"] = round(new_cost, 4)
                old["current_price"] = execution_price
            else:
                # 新建持仓：填充完整字段
                positions[stock_code] = PositionItem(
                    code=stock_code,
                    name=self._get_stock_name(stock_code),
                    quantity=trade_quantity,
                    cost_price=execution_price,
                    current_price=execution_price,
                    profit_rate=0.0,
                    profit_amount=0.0,
                    buy_time=order.create_time or now_str,
                ).to_dict()
        elif order.action == "卖出":
            if stock_code in positions:
                old = positions[stock_code]
                old_qty: int = int(old.get("quantity", 0))
                new_qty: int = old_qty - trade_quantity
                if new_qty <= 0:
                    # 全部卖出：清除持仓
                    del positions[stock_code]
                else:
                    old["quantity"] = new_qty
                    old["current_price"] = execution_price

        execution.positions = list(positions.values())

    def _refresh_execution_metrics(
        self,
        execution: StrategyExecutionDao,
        order: OrderDao,
        user_strategy_id: str,
    ) -> None:
        """刷新执行数据：目前收益（已实现盈利累计）、持仓未实现盈亏、剩余资金与收益率。

        每次持仓变动都会重新计算 remaining_cash 与 current_profit，
        并通过 save_execution 入库产生一条新的执行记录。
        """
        # 累计已实现收益（来自订单的卖出盈亏）
        execution.current_profit += float(order.profit_amount or 0)

        # 更新每只持仓的未实现盈亏
        total_invested: float = 0.0
        for pos in execution.positions:
            qty: int = int(pos.get("quantity", 0))
            cost: float = float(pos.get("cost_price", 0))
            cur: float = float(pos.get("current_price", cost))
            total_invested += qty * cost
            if qty > 0 and cost > 0:
                pos["profit_amount"] = round((cur - cost) * qty, 2)
                pos["profit_rate"] = round((cur - cost) / cost * 100, 2)
            else:
                pos["profit_amount"] = 0.0
                pos["profit_rate"] = 0.0

        # 计算剩余可用资金：初始资金 + 已实现盈利 - 持仓成本
        execution.remaining_cash = max(
            execution.initial_amount + execution.current_profit - total_invested,
            0.0,
        )

        # 计算总收益率（已实现收益 / 初始资金）
        base_amount: float = execution.initial_amount
        if base_amount <= 0:
            base_amount = total_invested
        if base_amount > 0:
            execution.current_return_rate = execution.current_profit / base_amount
        else:
            execution.current_return_rate = 0.0

        execution.update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            self._user_strategy_api.save_execution(
                user_strategy_id=user_strategy_id,
                current_return_rate=execution.current_return_rate,
                current_profit=execution.current_profit,
                annualized_return_rate=execution.annualized_return_rate,
                benchmark_return_rate=execution.benchmark_return_rate,
                positions=execution.positions,
                initial_amount=execution.initial_amount,
                remaining_cash=execution.remaining_cash,
                start_date=execution.start_date,
                execution_days=execution.execution_days,
            )
            self.logger.info(
                "strategy execution updated via API, user_strategy_id=%s", user_strategy_id
            )
        except Exception as err:
            self.logger.exception("failed to update strategy execution via API: %s", err)

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

    def _save_order_via_api(self, order: OrderDao) -> str:
        """通过 API 创建订单并返回服务端分配的订单 ID。

        :param order: 待保存的订单对象
        :return: 服务端返回的订单 ID，失败时返回空字符串
        """
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
            )
            order_id = str(resp.get("id", ""))
            self.logger.info("order created via API, id=%s", order_id)
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
