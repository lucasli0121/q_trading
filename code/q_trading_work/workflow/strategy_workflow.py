"""
Author: liguoqiang
Date: 2026-07-05
LastEditors: liguoqiang
LastEditTime: 2026-08-01
Description: 策略工作流
    继承 BaseWorkflow，实现策略的加载、管理、执行、交易信号记录和订单生成。
    通过线程池并发执行策略实例，在 handle_bar / handle_tick 中处理行情数据，
    调用策略的 handle_minute_bar 获取买卖信号，记录信号后按用户策略生成交易订单。

    架构：
    - _strategy_instances: 策略实例（按 strategy_id 唯一，去重）
    - _user_strategies: 用户策略配置（按 strategy_id 分组）
    - 策略决定信号 → 记录 /api/trade_signal/add → 对应用户股票池下单
"""
from __future__ import annotations

import importlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from api.client import ApiClient
from api.pool import PoolApi
from api.stock_info import StockInfoApi
from api.strategy import StrategyApi
from api.trade_signal import TradeSignalApi
from api.strategy_select_stock import StrategySelectStockApi
from api.user_strategy import UserStrategyApi
from api.workflow_service_user_strategy import WorkflowServiceUserStrategyApi
from app_context import AppContext
from dao.order_dao import OrderDao, OrderStatus
from dao.user_strategy_dao import StrategyStatus, UserStrategyDao
from mq.mqtt_client import MqttTopic
from strategy.base_strategy import BaseStrategy
from trade.manager import TradeExecutionResult, TradeManager
from trade.notifier import TradeNotifier
from utils import tools
import exchange_calendars as xcals
from workflow.base_workflow import BaseWorkflow


@dataclass
class StrategyState:
    """策略运行时状态。

    包含策略实例、运行状态、股票池、持仓信息、资金信息等。
    通过 user_strategy_id 关联 UserStrategyDao，通过 strategy_id 关联 StrategyDao。
    """

    strategy_id: str  # 全局策略模板 ID（StrategyDao._id）
    user_strategy_id: str  # 用户策略关联 ID（UserStrategyDao._id）
    strategy_name: str  # 策略名称
    class_path: str  # 策略类模块路径
    class_name: str  # 策略类名
    instance: BaseStrategy | None = None  # 策略实例对象
    status: str = "stopped"  # 运行状态: running / stopped / paused
    stock_pool: list[str] = field(default_factory=list)  # 关注的股票池
    pool_id: str = ""  # 关联的股票池 ID，用于每日重新加载股票池
    positions: dict[str, dict[str, Any]] = field(default_factory=dict)  # code -> 持仓信息
    load_error: str = ""  # 加载失败时的错误信息
    initial_amount: float = 0.0  # 初始资金（来自 UserStrategyDao.initial_amount）
    current_profit: float = 0.0  # 已实现盈利（来自 StrategyExecutionDao.current_profit）
    max_stock_count: int = 4  # 最大持仓数量（来自 UserStrategyDao.max_stock_count）
    available_cash: float = 0.0  # 剩余可用资金（初始资金 + 已实现盈利 - 持仓成本）
    pending_codes: set[str] = field(default_factory=set)  # 在途订单股票代码（去重）
    blacklist_codes: set[str] = field(default_factory=set)  # 用户黑名单股票代码（策略信号/选股时过滤）
    _lock: threading.Lock = field(default_factory=threading.Lock)  # 线程安全锁，保护并发访问


class StrategyWorkflow(BaseWorkflow):
    """策略工作流。

    继承 BaseWorkflow，在分钟行情 handle_bar 中执行策略逻辑，
    调用 handle_minute_bar 获取买卖信号，记录后按用户策略生成交易订单。

    使用方式:
        workflow = StrategyWorkflow(pool_size=8)
        workflow.start()          # 启动工作流，加载策略并订阅行情
        workflow.run_strategy("user_strategy_id_xxx")   # 按 user_strategy_id 启动
        workflow.stop_strategy("user_strategy_id_xxx")  # 按 user_strategy_id 停止
        workflow.stop()           # 停止工作流
    """

    # 单笔买入金额上限（元），避免单笔一次性动用全部资金
    MAX_SINGLE_ORDER_AMOUNT: float = 100000.0

    def __init__(self, pool_size: int | None = None) -> None:
        """初始化策略工作流。

        使用独立的 ApiClient 实例（admin token），与 Web 会话的 ApiClient 隔离，
        确保后台线程中也能以管理员身份访问所有用户的策略数据。

        :param pool_size: 线程池大小，默认使用基类的 DEFAULT_POOL_SIZE
        """
        super().__init__(pool_size=pool_size)
        self.logger: logging.Logger = logging.getLogger(__name__)

        # 策略实例（按 strategy_id 唯一，去重）
        self._strategy_instances: dict[str, BaseStrategy] = {}
        # 用户策略配置（按 strategy_id 分组）
        self._user_strategies: dict[str, list[StrategyState]] = {}

        # 后台独立 ApiClient：读取配置文件中的 admin_token
        self._admin_client: ApiClient = ApiClient()
        admin_token: str = tools.load_admin_token()
        if admin_token:
            self._admin_client.set_token(admin_token)
            AppContext().api_client.set_fallback_token(admin_token)
            self.logger.info("StrategyWorkflow 已使用 admin_token 初始化独立 ApiClient")
        else:
            self.logger.warning(
                "cfg/stock.cfg [server] admin_token 未配置，"
                "后台 API 调用可能因缺少认证而失败"
            )

        self._strategy_api: StrategyApi = StrategyApi(self._admin_client)
        self._user_strategy_api: UserStrategyApi = UserStrategyApi(self._admin_client)
        self._trade_signal_api: TradeSignalApi = TradeSignalApi(self._admin_client)
        self._strategy_select_stock_api: StrategySelectStockApi = StrategySelectStockApi(self._admin_client)
        self._workflow_service_api: WorkflowServiceUserStrategyApi = WorkflowServiceUserStrategyApi(
            self._admin_client
        )
        self._trade_manager: TradeManager = TradeManager(api_client=self._admin_client)
        self._notifier: TradeNotifier = TradeNotifier(api_client=self._admin_client)
        self._pool_api: PoolApi = PoolApi(self._admin_client)
        self._stock_info_api: StockInfoApi = StockInfoApi(self._admin_client)
        self._scheduler: Any = None
        self._service_name: str = ""  # 当前 workflow 微服务名称
        # 初始化上交所交易日历（用于判断交易日），从 2020-01-01 开始
        self.xshg = xcals.get_calendar("XSHG", start="2020-01-01")  # 上交所交易日历，用于判断交易日

    # ---- 工作流初始化（_init_workflow 钩子） ----

    def _init_workflow(self) -> None:
        """在 MQTT 订阅前执行初始化：加载 service 配置、策略模板和用户策略。

        1. 从配置文件读取 service_name
        2. 调用 load_strategies_from_db() 加载策略模板和用户策略
        3. 收集所有 pool_id 到 self._pool_ids，供基类 pool 级 MQTT 订阅
        """
        # 步骤1：读取 service_name
        self._service_name = tools.load_service_name()
        if self._service_name:
            self.logger.info("当前 workflow 微服务名称: %s", self._service_name)
        else:
            self.logger.warning("未配置 service_name，将以兼容模式运行（加载全部策略）")

        # 步骤2：加载策略模板和分配给本 service 的用户策略
        loaded: list[StrategyState] = self.load_strategies_from_db()
        self.logger.info("共加载 %d 个用户策略配置", len(loaded))

        # 步骤3：收集 pool_ids
        self._pool_ids = set()
        for states in self._user_strategies.values():
            for state in states:
                if state.pool_id:
                    self._pool_ids.add(state.pool_id)

        if self._pool_ids:
            self.logger.info(
                "收集到 %d 个 pool_id: %s", len(self._pool_ids), self._pool_ids
            )
        else:
            self.logger.warning("未收集到任何 pool_id，将使用全局 MQTT 订阅")

    # ---- 策略加载 ----

    def _load_strategy_class(
        self, class_path: str, class_name: str
    ) -> type[BaseStrategy] | None:
        """动态加载策略类。"""
        try:
            module = importlib.import_module(class_path)
            strategy_cls: type = getattr(module, class_name)
            if not issubclass(strategy_cls, BaseStrategy):
                self.logger.error("%s.%s 不是 BaseStrategy 的子类", class_path, class_name)
                return None
            return strategy_cls
        except (ImportError, AttributeError) as exc:
            self.logger.error("加载策略类失败 %s.%s: %s", class_path, class_name, exc)
            return None

    def _instantiate_strategy(
        self, strategy_cls: type[BaseStrategy]
    ) -> BaseStrategy | None:
        """实例化策略对象。"""
        try:
            instance: BaseStrategy = strategy_cls()
            return instance
        except Exception as exc:
            self.logger.error("实例化策略 %s 失败: %s", strategy_cls.__name__, exc, exc_info=True)
            return None

    def _calc_available_cash(self, state: StrategyState) -> float:
        """根据初始资金、已实现盈利和持仓成本计算剩余可用资金。

        资金模型：总资产 = 初始资金 + 已实现盈利，
        剩余可用资金 = 总资产 - 持仓成本（不小于 0）。
        """
        total_cost: float = 0.0
        for pos in state.positions.values():
            cost_price: float = float(pos.get("cost_price", 0))
            quantity: int = int(pos.get("quantity", 0))
            total_cost += cost_price * quantity
        available: float = (
            state.initial_amount + state.current_profit - total_cost
        )
        return max(available, 0.0)

    def _load_execution_data(self, state: StrategyState) -> None:
        """加载策略执行数据，填充初始资金、持仓和剩余资金。"""
        if not state.user_strategy_id:
            return
        try:
            exec_data: dict[str, Any] = self._user_strategy_api.get_latest_execution(
                state.user_strategy_id
            )
        except Exception as exc:
            self.logger.debug("加载策略 %s 执行数据失败: %s", state.strategy_name, exc)
            # 即使 API 异常，也确保 available_cash 被初始化
            state.available_cash = self._calc_available_cash(state)
            return
        if not exec_data:
            # 无执行数据时（新策略），仍需要初始化 available_cash
            state.available_cash = self._calc_available_cash(state)
            return
        # initial_amount 优先使用 UserStrategyDao 的值（已在 state 初始化时设置），
        # 执行数据中的值仅作为兜底
        ia: float = float(exec_data.get("initial_amount", 0))
        if ia > 0 and state.initial_amount <= 0:
            state.initial_amount = ia
        state.current_profit = float(exec_data.get("current_profit", 0) or 0)
        positions_list: list[dict[str, Any]] = exec_data.get("positions", [])
        if positions_list:
            state.positions = {
                p.get("code", ""): p for p in positions_list if p.get("code")
            }
        # 最新剩余资金优先取最近一条执行记录的 remaining_cash（按 user_strategy_id
        # 查询最近的 StrategyExecutionDao），无该字段时回退为本地计算
        if "remaining_cash" in exec_data:
            state.available_cash = float(exec_data.get("remaining_cash", 0) or 0)
        else:
            state.available_cash = self._calc_available_cash(state)

    def _load_blacklist_for_state(self, state: StrategyState) -> None:
        """加载用户黑名单股票代码到 state.blacklist_codes。

        :param state: 策略运行时状态
        """
        if not state.user_strategy_id:
            return
        try:
            us_data: dict[str, Any] = self._user_strategy_api.get(state.user_strategy_id)
            user_id: str = us_data.get("user_id", "")
            if not user_id:
                return
            # 通过 admin client 查询用户黑名单
            blacklist: list[dict[str, Any]] = []
            try:
                result: Any = self._admin_client.get(
                    "/api/blacklist/list",
                    params={"user_id": user_id},
                )
                if isinstance(result, list):
                    blacklist = result
            except Exception:
                pass
            state.blacklist_codes = {
                str(item.get("code", "")) for item in blacklist if item.get("code")
            }
            if state.blacklist_codes:
                self.logger.info(
                    "[%s] 已加载 %d 只黑名单股票",
                    state.strategy_name, len(state.blacklist_codes),
                )
        except Exception as exc:
            self.logger.debug(
                "[%s] 加载黑名单失败: %s", state.strategy_name, exc,
            )

    def _is_blacklisted(self, state: StrategyState, code: str) -> bool:
        """检查股票代码是否在用户黑名单中。

        :param state: 策略运行时状态
        :param code: 股票代码
        :return: 是否在黑名单中
        """
        if code in state.blacklist_codes:
            self.logger.debug(
                "[%s] code=%s 在黑名单中，过滤", state.strategy_name, code,
            )
            return True
        return False

    def _is_code_blacklisted_for_all(self, strategy_id: str, code: str) -> bool:
        """检查某股票代码是否被该策略模板下所有运行中的用户策略列入黑名单。

        用于 handle_bar / handle_tick 的早期过滤：仅当所有运行中用户策略
        都将该 code 列入黑名单时才返回 True（此时可跳过整个 _process_bar 调度）。

        :param strategy_id: 策略模板 ID
        :param code: 股票代码
        :return: 所有运行中用户策略均将 code 列入黑名单时返回 True
        """
        running_states: list[StrategyState] = [
            s for s in self._user_strategies.get(strategy_id, [])
            if s.status == "running"
        ]
        if not running_states:
            return False
        return all(code in s.blacklist_codes for s in running_states)

    def update_strategy_stock_pool(self, state: StrategyState, pool_id: str) -> None:
        """根据 pool_id 解析股票池，填充 state.stock_pool。"""
        if not pool_id:
            return
        try:
            pool_info: dict[str, Any] | None = self._pool_api.get_by_id(pool_id)
            if pool_info is None:
                self.logger.warning("策略 %s 关联的股票池 %s 不存在", state.strategy_name, pool_id)
                return
            pool_name: str = pool_info.get("name", "")
            if not pool_name:
                return
            pool_stocks: list[dict[str, Any]] = self._pool_api.get_stocks(pool_name)
            codes: list[str] = [
                str(s.get("code", "")) for s in pool_stocks if s.get("code")
            ]
            # 过滤黑名单股票
            before: int = len(codes)
            codes = [c for c in codes if not self._is_blacklisted(state, c)]
            if before != len(codes):
                self.logger.info(
                    "策略 %s 股票池已过滤 %d 只黑名单股票", state.strategy_name, before - len(codes),
                )
            state.stock_pool = codes
            self.logger.info(
                "策略 %s 股票池已加载: pool=%s (%s), codes=%s",
                state.strategy_name, pool_name, pool_id, codes,
            )
        except Exception as exc:
            self.logger.error("解析策略 %s 股票池失败: %s", state.strategy_name, exc, exc_info=True)

    def _reload_stock_pool(self, state: StrategyState) -> None:
        """重新加载策略关联的股票池（从 API 拉取最新股票列表）。"""
        if not state.pool_id:
            return
        try:
            pool_info: dict[str, Any] | None = self._pool_api.get_by_id(state.pool_id)
            if pool_info is None:
                return
            pool_name: str = pool_info.get("name", "")
            if not pool_name:
                return
            pool_stocks: list[dict[str, Any]] = self._pool_api.get_stocks(pool_name)
            codes: list[str] = [
                str(s.get("code", "")) for s in pool_stocks if s.get("code")
            ]
            state.stock_pool = codes
        except Exception as exc:
            self.logger.error("重新加载策略 %s 股票池失败: %s", state.strategy_name, exc, exc_info=True)

    # ---- 新架构：加载策略实例 + 用户策略配置 ----

    def _build_strategy_instance(self, template: dict[str, Any]) -> BaseStrategy | None:
        """根据策略模板构建唯一的策略实例。

        :param template: 策略模板字典
        :return: 策略实例，失败返回 None
        """
        name: str = template.get("name", "")
        class_path: str = template.get("class_path", "")
        class_name: str = template.get("class_name", "")
        strategy_id: str = str(template.get("id") or template.get("_id") or "")

        if not class_path or not class_name or not strategy_id:
            self.logger.warning("策略 %s 缺少必要字段，跳过", name)
            return None

        strategy_cls = self._load_strategy_class(class_path, class_name)
        if strategy_cls is None:
            return None

        instance = self._instantiate_strategy(strategy_cls)
        if instance is not None:
            instance.strategy_id = strategy_id
        return instance

    def load_strategies_from_db(self) -> list[StrategyState]:
        """加载策略：模板实例化一次 + 用户配置分组。

        1. 从 /api/strategy/list 获取策略模板，每模板实例化一次
        2. 从 /api/user_strategy/all 获取用户策略关联，按 strategy_id 分组
        3. 加载每个用户策略的股票池和持仓数据

        :return: 所有加载成功的 StrategyState 列表
        """
        if not self._load_strategy_instances_from_db():
            return []
        return self._build_user_strategy_states()

    def _load_strategy_instances_from_db(self) -> bool:
        """从 /api/strategy/list 加载策略模板并实例化（每个模板一次）。"""
        try:
            db_strategies: list[dict[str, Any]] = self._strategy_api.list()
        except Exception as exc:
            self.logger.error("从数据库加载策略模板列表失败: %s", exc)
            return False

        if not db_strategies:
            self.logger.info("数据库中没有已保存的策略模板")
            return False

        self._strategy_instances.clear()
        for s in db_strategies:
            strategy_id: str = str(s.get("id") or s.get("_id") or "")
            if not strategy_id:
                continue
            if strategy_id in self._strategy_instances:
                continue  # 已加载，跳过重复
            instance = self._build_strategy_instance(s)
            if instance is not None:
                self._strategy_instances[strategy_id] = instance
                self.logger.info(
                    "策略实例加载: %s (strategy_id=%s)", instance.strategy_name, strategy_id,
                )

        self.logger.info("共加载 %d 个策略实例", len(self._strategy_instances))
        return True

    def _build_user_strategy_states(self) -> list[StrategyState]:
        """从 /api/user_strategy/all 加载用户策略关联并按 strategy_id 分组。

        当 _service_name 非空时，仅加载分配给当前 workflow 微服务的 user_strategy。
        """
        # 获取分配给本 service 的 user_strategy_id 列表
        assigned_ids: set[str] = set()
        if self._service_name:
            try:
                records: list[dict[str, Any]] = self._workflow_service_api.list(
                    service_name=self._service_name
                )
                for record in records:
                    ids: list[str] = record.get("user_strategy_ids", [])
                    assigned_ids.update(str(uid) for uid in ids)
                self.logger.info(
                    "Service [%s] 分配的 user_strategy_ids: %s",
                    self._service_name,
                    assigned_ids,
                )
            except Exception as exc:
                self.logger.error(
                    "获取 service [%s] 分配的 user_strategy 列表失败: %s",
                    self._service_name,
                    exc,
                )
                return []

            if not assigned_ids:
                self.logger.warning(
                    "Service [%s] 未分配到任何 user_strategy，工作流将空转",
                    self._service_name,
                )
                return []

        try:
            user_strategies: list[dict[str, Any]] = self._user_strategy_api.list_all()
        except Exception as exc:
            self.logger.error("从数据库加载用户策略关联列表失败: %s", exc)
            return []

        self._user_strategies.clear()
        loaded: list[StrategyState] = []

        for us_item in user_strategies:
            user_strategy_id: str = str(us_item.get("id") or us_item.get("_id") or "")
            us_sid: str = str(us_item.get("strategy_id", ""))
            if not us_sid or not user_strategy_id:
                continue
            # 按 service 分配的 user_strategy_id 列表过滤
            if assigned_ids and user_strategy_id not in assigned_ids:
                continue

            # 获取对应的策略实例
            instance = self._strategy_instances.get(us_sid)
            if instance is None:
                self.logger.warning(
                    "用户策略 %s 关联的 strategy_id=%s 无对应实例，跳过",
                    user_strategy_id, us_sid,
                )
                continue

            pool_id: str = str(us_item.get("pool_id", ""))
            db_status: str = str(us_item.get("status", "stopped"))
            # 从 UserStrategyDao 读取初始资金和最大持仓数
            max_stocks: int = int(us_item.get("max_stock_count", 4) or 4)
            initial_amt: float = float(us_item.get("initial_amount", 0) or 0)

            state = StrategyState(
                strategy_id=us_sid,
                user_strategy_id=user_strategy_id,
                strategy_name=instance.strategy_name,
                class_path=instance.__class__.__module__,
                class_name=instance.__class__.__name__,
                instance=instance,
                pool_id=pool_id,
                status=db_status if db_status in ("running", "stopped", "paused") else "stopped",
                initial_amount=initial_amt,
                max_stock_count=max_stocks,
            )

            # 解析股票池
            if pool_id:
                self.update_strategy_stock_pool(state, pool_id)

            # 加载执行数据（同步持仓、执行中的 initial_amount 等）
            self._load_execution_data(state)
            # 加载用户黑名单（信号/选股时过滤）
            self._load_blacklist_for_state(state)

            # 按 strategy_id 分组
            if us_sid not in self._user_strategies:
                self._user_strategies[us_sid] = []
            self._user_strategies[us_sid].append(state)
            loaded.append(state)

            self.logger.info(
                "用户策略加载: name=%s, user_strategy_id=%s, strategy_id=%s",
                state.strategy_name, user_strategy_id, us_sid,
            )

        self.logger.info(
            "共加载 %d 个用户策略配置（%d 个策略模板）",
            len(loaded), len(self._user_strategies),
        )
        return loaded

    # ---- 策略管理（兼容旧接口） ----

    def update_strategy_status(self, user_strategy_id: str, status: str) -> bool:
        if status == StrategyStatus.RUNNING.value:
            return self.run_strategy(user_strategy_id)
        elif status == StrategyStatus.STOPPED.value:
            return self.stop_strategy(user_strategy_id)
        elif status == StrategyStatus.PAUSED.value:
            return self.pause_strategy(user_strategy_id)
        return False

    def _find_state(self, user_strategy_id: str) -> StrategyState | None:
        """在所有用户策略配置中查找指定 user_strategy_id 的 State。

        :param user_strategy_id: 用户策略关联 ID
        :return: 策略状态，未找到返回 None
        """
        for states in self._user_strategies.values():
            for s in states:
                if s.user_strategy_id == user_strategy_id:
                    return s
        return None

    def run_strategy(self, user_strategy_id: str) -> bool:
        """启动指定策略（按 user_strategy_id 定位）。"""
        state: StrategyState | None = self._find_state(user_strategy_id)
        if state is None:
            self.logger.error("策略 %s 未找到", user_strategy_id)
            return False
        if state.instance is None:
            self.logger.error("策略 %s 实例为空", user_strategy_id)
            return False
        if state.status == "running":
            self.logger.warning("策略 %s (%s) 已在运行中", state.strategy_name, user_strategy_id)
            return False
        state.status = "running"
        try:
            self._user_strategy_api.update(state.user_strategy_id, status="running")
        except Exception as exc:
            self.logger.warning("同步策略状态失败: %s", exc)
        self._save_runlog(state, f"策略 {state.strategy_name} 已启动", "INFO")
        self.logger.info("策略 %s (%s) 已启动", state.strategy_name, user_strategy_id)
        return True

    def stop_strategy(self, user_strategy_id: str) -> bool:
        """停止指定策略。"""
        state: StrategyState | None = self._find_state(user_strategy_id)
        if state is None:
            self.logger.error("策略 %s 未找到", user_strategy_id)
            return False
        if state.status != "running":
            self.logger.warning("策略 %s (%s) 未在运行中", state.strategy_name, user_strategy_id)
            return False
        state.status = "stopped"
        try:
            self._user_strategy_api.update(state.user_strategy_id, status="stopped")
        except Exception as exc:
            self.logger.warning("同步策略状态失败: %s", exc)
        self._save_runlog(state, f"策略 {state.strategy_name} 已停止", "INFO")
        self.logger.info("策略 %s (%s) 已停止", state.strategy_name, user_strategy_id)
        return True

    def pause_strategy(self, user_strategy_id: str) -> bool:
        """暂停指定策略。"""
        state: StrategyState | None = self._find_state(user_strategy_id)
        if state is None:
            self.logger.error("策略 %s 未找到", user_strategy_id)
            return False
        if state.status != "running":
            self.logger.warning("策略 %s (%s) 未在运行中", state.strategy_name, user_strategy_id)
            return False
        state.status = "paused"
        try:
            self._user_strategy_api.update(state.user_strategy_id, status="paused")
        except Exception as exc:
            self.logger.warning("同步策略状态失败: %s", exc)
        self._save_runlog(state, f"策略 {state.strategy_name} 已暂停", "INFO")
        self.logger.info("策略 %s (%s) 已暂停", state.strategy_name, user_strategy_id)
        return True

    def add_user_strategy(self, user_strategy: UserStrategyDao) -> bool:
        """添加用户策略关联，加载到本地缓存。"""
        strategy_id: str = user_strategy.strategy_id
        user_id: str = user_strategy.user_id
        pool_id: str = user_strategy.pool_id
        status: str = user_strategy.status or "stopped"

        if not strategy_id or not user_id:
            self.logger.error("add_user_strategy: strategy_id 和 user_id 不能为空")
            return False

        user_strategy_id: str = user_strategy.id
        if not user_strategy_id:
            self.logger.error("用户策略缺少 ID")
            return False

        if self._find_state(user_strategy_id) is not None:
            self.logger.warning("策略 %s 已在缓存中", user_strategy_id)
            return True

        # 获取或创建策略实例
        instance = self._strategy_instances.get(strategy_id)
        if instance is None:
            try:
                template: dict[str, Any] = self._strategy_api.get_by_id(strategy_id)
            except Exception as exc:
                self.logger.error("获取策略模板失败: %s", exc)
                return False
            if not template:
                self.logger.error("策略模板 %s 不存在", strategy_id)
                return False
            instance = self._build_strategy_instance(template)
            if instance is None:
                return False
            self._strategy_instances[strategy_id] = instance

        state = StrategyState(
            strategy_id=strategy_id,
            user_strategy_id=user_strategy_id,
            strategy_name=instance.strategy_name,
            class_path=instance.__class__.__module__,
            class_name=instance.__class__.__name__,
            instance=instance,
            pool_id=pool_id,
            status=status if status in ("running", "stopped", "paused") else "stopped",
            initial_amount=user_strategy.initial_amount,
            max_stock_count=user_strategy.max_stock_count,
        )

        if pool_id:
            self.update_strategy_stock_pool(state, pool_id)

        if strategy_id not in self._user_strategies:
            self._user_strategies[strategy_id] = []
        self._user_strategies[strategy_id].append(state)

        self._save_runlog(state, f"策略 {state.strategy_name} 已添加 (user_id={user_id})", "INFO")
        self.logger.info(
            "add_user_strategy 成功: name=%s, user_id=%s, user_strategy_id=%s",
            state.strategy_name, user_id, user_strategy_id,
        )
        return True

    def delete_strategy(self, user_strategy_id: str) -> bool:
        """删除策略（从内存移除，同时调用 API 删除）。"""
        state: StrategyState | None = self._find_state(user_strategy_id)
        if state is None:
            self.logger.error("策略 %s 不存在", user_strategy_id)
            return False

        # 从 _user_strategies 中移除
        sid: str = state.strategy_id
        if sid in self._user_strategies:
            self._user_strategies[sid] = [
                s for s in self._user_strategies[sid] if s.user_strategy_id != user_strategy_id
            ]
            # 如果没有用户了，移除策略实例
            if not self._user_strategies[sid]:
                del self._user_strategies[sid]
                self._strategy_instances.pop(sid, None)
                self.logger.info("策略实例 %s 已无用户，已移除", sid)

        try:
            self._user_strategy_api.delete(user_strategy_id)
        except Exception as exc:
            self.logger.error("删除用户策略关联记录失败: %s", exc)

        self._save_runlog(state, f"策略 {state.strategy_name} 已删除", "INFO")
        return True

    def get_strategy_state(self, user_strategy_id: str) -> StrategyState | None:
        """获取指定策略的运行时状态。"""
        return self._find_state(user_strategy_id)

    def get_all_strategies(self) -> dict[str, StrategyState]:
        """获取所有已加载的策略状态。

        :return: user_strategy_id → StrategyState 的映射
        """
        result: dict[str, StrategyState] = {}
        for states in self._user_strategies.values():
            for s in states:
                result[s.user_strategy_id] = s
        return result

    def set_stock_pool(self, user_strategy_id: str, codes: list[str]) -> bool:
        """设置策略的关注股票池。"""
        state: StrategyState | None = self._find_state(user_strategy_id)
        if state is None:
            self.logger.error("策略 %s 未加载", user_strategy_id)
            return False
        state.stock_pool = list(codes)
        self.logger.info(
            "策略 %s (%s) 股票池已更新: %d 只股票",
            state.strategy_name, user_strategy_id, len(codes),
        )
        return True

    def _is_strategy_running(self, strategy_id: str) -> bool:
        """判断某个 strategy_id 下是否有用户策略处于运行状态。

        :param strategy_id: 策略模板 ID
        :return: 是否有运行中的用户策略
        """
        for us in self._user_strategies.get(strategy_id, []):
            if us.status == "running":
                return True
        return False

    # ---- 行情回调 ----

    def handle_bar(self, topic: str, payload: Any) -> None:
        """处理分钟行情消息。

        遍历策略实例 → 对每只股票调用 _process_bar。
        仅调度有运行中用户策略的实例，且在调度前过滤黑名单股票。

        :param topic: MQTT topic
        :param payload: 分钟行情数据列表 list[dict]，每项含 code 字段
        """
        if not isinstance(payload, list) and not isinstance(payload, dict):
            self.logger.warning("handle_bar: payload 格式异常，期望 list or dict，实际 %s", type(payload))
            return

        running_count: int = 0
        blacklist_skip_count: int = 0

        if not isinstance(payload, list):
            payload = [payload]
        for bar_data in payload:
            if not isinstance(bar_data, dict):
                continue
            code: str = bar_data.get("code", "")
            if not code:
                continue

            for strategy_id, instance in self._strategy_instances.items():
                if not self._is_strategy_running(strategy_id):
                    continue
                # 黑名单过滤：如果所有运行中的用户策略都将该 code 列入黑名单，则跳过
                if self._is_code_blacklisted_for_all(strategy_id, code):
                    blacklist_skip_count += 1
                    continue
                running_count += 1
                self.submit(self._process_bar, strategy_id, instance, code, bar_data)

        if running_count > 0 or blacklist_skip_count > 0:
            self.logger.debug(
                "handle_bar: %d stocks, dispatched %d tasks, skipped %d blacklisted",
                len(payload), running_count, blacklist_skip_count,
            )

    def handle_tick(self, topic: str, payload: Any) -> None:
        """处理实时行情消息。

        遍历策略实例 → 对每只股票调用 _process_tick。
        仅调度有运行中用户策略的实例，且在调度前过滤黑名单股票。

        :param topic: MQTT topic
        :param payload: 实时行情数据列表 list[dict]，每项含 code 字段
        """
        if not isinstance(payload, list) and not isinstance(payload, dict):
            self.logger.warning("handle_tick: payload 格式异常，期望 list or dict，实际 %s", type(payload))
            return

        running_count: int = 0
        blacklist_skip_count: int = 0
        if not isinstance(payload, list):
            payload = [payload]

        for tick_data in payload:
            if not isinstance(tick_data, dict):
                continue
            code: str = tick_data.get("code", "")
            if not code:
                continue

            for strategy_id, instance in self._strategy_instances.items():
                if not self._is_strategy_running(strategy_id):
                    continue
                # 黑名单过滤：如果所有运行中的用户策略都将该 code 列入黑名单，则跳过
                if self._is_code_blacklisted_for_all(strategy_id, code):
                    blacklist_skip_count += 1
                    continue
                running_count += 1
                self.submit(self._process_tick, strategy_id, instance, code, tick_data)

        if running_count > 0 or blacklist_skip_count > 0:
            self.logger.debug(
                "handle_tick: %d stocks, dispatched %d tasks, skipped %d blacklisted",
                len(payload), running_count, blacklist_skip_count,
            )

    # ---- 策略执行核心逻辑 ----

    def _process_bar(
        self, strategy_id: str, instance: BaseStrategy, code: str, bar_data: dict[str, Any]
    ) -> None:
        """在线程池中执行策略的分钟级检查逻辑。

        1. 查找运行中用户策略的持仓信息，传递给 handle_minute_bar
        2. 调用 instance.handle_minute_bar() 获取买卖信号
        3. 记录信号通过 MQTT 发送
        4. 对每个匹配的用户策略，按信号创建订单

        并发安全：每个 StrategyState 持有独立锁（_lock），
        确保同一 state 在不同 code 并发处理时不产生竞态条件。

        :param strategy_id: 策略模板 ID
        :param instance: 策略实例
        :param code: 股票代码
        :param bar_data: 分钟行情数据
        """
        try:

            # 1. 调用策略的 handle_minute_bar 获取信号
            matched, extra = instance.handle_minute_bar(code, {}, bar_data)
            if not matched:
                # 未匹配：记录原因日志
                reason: str = extra.get("reason", "")
                if reason:
                    self.logger.info(
                        "code=%s 未触发信号, reason:%s", code, reason
                    )
                return  # 条件不符合直接返回

            # 2. 记录交易信号（含信号风暴过滤，被抑制则跳过订单创建）
            if not self._save_trade_signal(strategy_id, code, extra):
                return

            # 3. 对每个用户策略，检查 code 是否在其股票池中，创建订单
            for us_state in self._user_strategies.get(strategy_id, []):
                # 黑名单过滤
                if self._is_blacklisted(us_state, code):
                    continue
                if us_state.status != "running":
                    continue
                if us_state.stock_pool and code not in us_state.stock_pool:
                    continue

                signal_type: str = extra.get("signal", "")

                # 使用 state 级锁保护并发临界区：
                # pending_codes 检查/修改、持仓加载、资金修改、订单创建
                with us_state._lock:
                    positions: dict[str, dict[str, Any]] = self._load_positions(us_state)

                    if signal_type == "BUY":
                        if code not in positions:
                            # 检查是否已有在途订单（避免重复下单）
                            if code in us_state.pending_codes:
                                self.logger.debug(
                                    "[%s] code=%s 已有在途买入订单，跳过",
                                    us_state.strategy_name, code,
                                )
                            elif len(positions) >= us_state.max_stock_count:
                                # 检查持仓数量是否已达上限
                                self.logger.debug(
                                    "[%s] code=%s 持仓数 %d 已达上限 %d，跳过买入",
                                    us_state.strategy_name, code,
                                    len(positions),
                                    us_state.max_stock_count,
                                )
                            else:
                                self._create_buy_order(us_state, code, extra)
                        else:
                            self.logger.debug(
                                "[%s] code=%s 已有持仓，跳过买入",
                                us_state.strategy_name, code,
                            )
                    elif signal_type == "SELL":
                        if code in positions:
                            if code in us_state.pending_codes:
                                self.logger.debug(
                                    "[%s] code=%s 已有在途卖出订单，跳过",
                                    us_state.strategy_name, code,
                                )
                            else:
                                pos = positions[code]
                                buy_time = pos.get("buy_time", "")
                                stock_create_time = bar_data.get("create_time", "")
                                if len(stock_create_time) >= 10:
                                    stock_create_day = stock_create_time[:10]
                                    if buy_time == stock_create_day:
                                        self.logger.info(f"买入当天不能立马卖出T+1规则, 发出买入信号时间:{buy_time}, 股票实际时间:{stock_create_day}")
                                self._create_sell_order(
                                    us_state, code, float(pos.get("cost_price", 0)), extra
                                )
                        else:
                            self.logger.debug(
                                "[%s] code=%s 无持仓，跳过卖出",
                                us_state.strategy_name, code,
                            )
                    else:
                        self.logger.debug(
                            "[%s] code=%s 信号类型未知: %s",
                            us_state.strategy_name, code, signal_type,
                        )

        except Exception as exc:
            self.logger.error(
                "[%s] _process_bar(%s) 异常: %s",
                instance.strategy_name, code, exc, exc_info=True,
            )

    def _process_tick(
        self, strategy_id: str, instance: BaseStrategy, code: str, tick_data: dict[str, Any]
    ) -> None:
        """在线程池中执行策略的实时级检查逻辑（同 _process_bar 模式）。

        并发安全：每个 StrategyState 持有独立锁（_lock），
        确保同一 state 在不同 code 并发处理时不产生竞态条件。

        :param strategy_id: 策略模板 ID
        :param instance: 策略实例
        :param code: 股票代码
        :param tick_data: 实时行情数据
        """
        try:
            matched, extra = instance.handle_tick_bar(code, {}, tick_data)

            if not matched:
                # 未匹配：记录原因日志
                reason: str = extra.get("reason", "")
                if reason:
                    self.logger.debug(
                        "code=%s 未触发信号, reason:%s", code, reason
                    )
                return  # 条件不符合直接返回

            # 记录交易信号（含信号风暴过滤，被抑制则跳过订单创建）
            if not self._save_trade_signal(strategy_id, code, extra):
                return

            for us_state in self._user_strategies.get(strategy_id, []):
                if us_state.status != "running":
                    continue
                # 黑名单过滤
                if self._is_blacklisted(us_state, code):
                    continue
                if us_state.stock_pool and code not in us_state.stock_pool:
                    continue

                signal_type: str = extra.get("signal", "")

                # 使用 state 级锁保护并发临界区
                with us_state._lock:
                    positions: dict[str, dict[str, Any]] = self._load_positions(us_state)

                    if signal_type == "BUY" and code not in positions and code not in us_state.pending_codes:
                        if len(positions) >= us_state.max_stock_count:
                            self.logger.debug(
                                "[%s] code=%s 持仓数 %d 已达上限 %d，跳过买入",
                                us_state.strategy_name, code,
                                len(positions), us_state.max_stock_count,
                            )
                            continue
                        self._create_buy_order(us_state, code, extra)
                    elif signal_type == "SELL" and code in positions and code not in us_state.pending_codes:
                        pos = positions[code]
                        self._create_sell_order(
                            us_state, code, float(pos.get("cost_price", 0)), extra
                        )

        except Exception as exc:
            self.logger.error(
                "[%s] _process_tick(%s) 异常: %s",
                instance.strategy_name, code, exc, exc_info=True,
            )

    # ---- 交易信号记录 ----

    def _load_signal_filter_config(self) -> tuple[float, int]:
        """从配置文件加载信号风暴过滤参数。

        :return: (price_change_threshold(%), time_gap_minutes(分钟))
        """
        try:
            from configparser import ConfigParser
            from utils.tools import resource_path
            cp = ConfigParser()
            cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
            price_threshold: float = float(
                cp.get("signal_filter", "price_change_threshold", fallback="2.0")
            )
            time_gap: int = int(
                cp.get("signal_filter", "time_gap_minutes", fallback="10")
            )
            return price_threshold, time_gap
        except Exception:
            return 2.0, 10

    def _save_trade_signal(
        self, strategy_id: str, code: str, extra: dict[str, Any]
    ) -> bool:
        """保存交易信号到 /api/trade_signal/add。

        卖出时先通过 /api/trade_signal/latest 查询最近一次买入记录，
        计算 profit_rate 和 profit_amount。
        信号风暴过滤：连续相同方向信号若 时间间隔 < time_gap_minutes
        或 价格变化 < price_change_threshold 则抑制。

        :param strategy_id: 策略模板 ID
        :param code: 股票代码
        :param extra: handle_minute_bar 返回的详细信息（含 signal/current_price 等）
        :return: True 表示信号已保存，False 表示信号被抑制
        """
        signal_type: str = extra.get("signal", "")
        price: float = float(extra.get("current_price", extra.get("price", 0)))
        action: str = "买入" if signal_type == "BUY" else "卖出"
        profit_rate: float = 0.0
        profit_amount: float = 0.0
        reason: str = extra.get("reason", "")
        strategy_dao = self._strategy_api.get_by_id(strategy_id)
        strategy_name = strategy_dao.get("name", "")

        # 查询最近一次同股票+同策略的交易信号（不限方向），用于信号风暴过滤
        try:
            latest_any: Any = self._trade_signal_api.latest(strategy_id, code)
            if latest_any and isinstance(latest_any, dict):
                last_action: str = str(latest_any.get("action", ""))
                last_price: float = float(latest_any.get("trade_price", 0))
                last_time: str = str(latest_any.get("create_time", ""))
                # 连续相同方向：检查时间间隔和价格变化
                if last_action == action and last_price > 0 and price > 0:
                    price_threshold, time_gap_minutes = self._load_signal_filter_config()
                    suppressed: bool = False
                    suppress_reasons: list[str] = []
                    # 检查时间间隔
                    if last_time:
                        #先检查是否在同一天内，两个机制条件判断都必须是同一天的时间间隔才有效
                        try:
                            last_dt = datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S")
                            if last_dt.date() == datetime.now().date():
                                gap = (datetime.now() - last_dt).total_seconds() / 60.0
                                if gap < time_gap_minutes:
                                    suppressed = True
                                    suppress_reasons.append(
                                    f"间隔={gap:.1f}分钟 < {time_gap_minutes}分钟"
                                )
                                # 检查价格变化
                                change_pct: float = abs(price - last_price) / last_price * 100
                                if change_pct < price_threshold:
                                    suppressed = True
                                    suppress_reasons.append(
                                        f"涨幅={change_pct:.2f}% < {price_threshold}%"
                                    )
                        except ValueError:
                            pass
                    if suppressed:
                        self.logger.info(
                            "信号风暴过滤: code=%s, action=%s, price=%.2f, "
                            "last_price=%.2f, last_time=%s, %s, 抑制",
                            code, action, price, last_price, last_time,
                            ", ".join(suppress_reasons),
                        )
                        return False
        except Exception as exc:
            self.logger.debug("查询最近信号失败（忽略过滤） code=%s: %s", code, exc)

        # 卖出时查询最近一次买入记录，计算盈亏
        if signal_type == "SELL":
            try:
                latest_result: Any = self._trade_signal_api.latest(
                    strategy_id, code, action="买入"
                )
                if latest_result and isinstance(latest_result, dict):
                    buy_price: float = float(latest_result.get("trade_price", 0))
                    if buy_price > 0:
                        profit_rate = (price - buy_price) / buy_price * 100
                        profit_amount = price - buy_price
                        self.logger.info(
                            "卖出信号计算: code=%s, buy_price=%.2f, sell_price=%.2f, "
                            "profit_rate=%.2f%%, profit_amount=%.2f",
                            code, buy_price, price, profit_rate, profit_amount,
                        )
            except Exception as exc:
                self.logger.warning(
                    "查询最近买入记录失败 code=%s: %s", code, exc, exc_info=True,
                )

        try:
            
            # 1. 先通过 API 保存交易信号到服务器
            self._trade_signal_api.add(
                strategy_id=strategy_id,
                stock_code=code,
                trade_price=price,
                action=action,
                profit_rate=profit_rate,
                profit_amount=profit_amount,
                reason=reason,
            )
            self.logger.info(
                "交易信号已保存: strategy_id=%s, code=%s, action=%s, price=%.2f, "
                "profit_rate=%.2f%%, profit_amount=%.2f, reason=%s",
                strategy_id, code, action, price, profit_rate, profit_amount, reason,
            )

            # 2. 通过 MQTT 发送交易信号通知
            signal_data: dict[str, Any] = {
                "strategy_id": strategy_id,
                "strategy_name": strategy_name,
                "stock_code": code,
                "trade_price": price,
                "action": action,
                "profit_rate": round(profit_rate, 2),
                "profit_amount": round(profit_amount, 2),
                "reason": reason,
            }
            mqtt_client = AppContext().mqtt_client
            payload: str = json.dumps(signal_data, ensure_ascii=False)
            publish_ok: bool = mqtt_client.publish(
                MqttTopic.STOCK_TRADING_SIGNAL, payload
            )
            if publish_ok:
                self.logger.info(
                    "交易信号 MQTT 通知已发送: topic=%s, data=%s",
                    MqttTopic.STOCK_TRADING_SIGNAL,
                    signal_data,
                )
            else:
                self.logger.warning(
                    "交易信号 MQTT 通知发送失败: code=%s, action=%s", code, action
                )

            # 3. 通过企业微信/微信发送交易信号推送通知
            notify_msg: str = (
                f"交易信号: {action} code={code} price={price:.2f} "
                f"reason={reason}"
            )
            if signal_type == "SELL":
                notify_msg += (
                    f" 收益率={profit_rate:.2f}% 收益={profit_amount:.2f}"
                )
            self._notifier.notify(
                notify_msg,
                recipient=strategy_name,
                user_strategy_id="",
            )

            return True
        except Exception as exc:
            self.logger.error("保存交易信号失败: %s", exc, exc_info=True)
            return False

    # ---- 订单创建 ----

    def _create_buy_order(
        self, state: StrategyState, code: str, extra: dict[str, Any]
    ) -> None:
        """根据买入信号生成买入订单。"""
        strategy_name: str = state.strategy_name
        current_price: float = float(extra.get("current_price", 0))
        if current_price <= 0:
            self.logger.warning("[%s] 买入信号缺少有效价格: code=%s", strategy_name, code)
            return
        if code in state.pending_codes:
            self.logger.debug("[%s] code=%s 已有在途订单，跳过重复买入", strategy_name, code)
            return
        buy_qty: int = self._calc_buy_quantity(state, current_price)
        if buy_qty <= 0:
            self.logger.warning(
                "[%s] 可用资金不足（不足一手或余额为 0），跳过买入: code=%s, available=%.2f",
                strategy_name, code, state.available_cash,
            )
            return
        estimated_cost: float = current_price * buy_qty
        # 先标记在途，避免并发重复下单；扣款在提交失败时回滚
        state.pending_codes.add(code)
        state.available_cash -= estimated_cost

        order = OrderDao(
            user_strategy_id=state.user_strategy_id,
            stock_code=code,
            entrust_quantity=buy_qty,
            trade_price=current_price,
            action="买入",
            status=OrderStatus.ENTRUST.value,
            create_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        ok: bool = self._submit_trade(order, state, rollback_amount=estimated_cost)
        if not ok:
            state.pending_codes.discard(code)
            state.available_cash += estimated_cost
            return
        self._save_runlog(
            state,
            f"买入信号: code={code}, price={current_price:.2f}, "
            f"qty={buy_qty}, cost≈{estimated_cost:.2f}, available={state.available_cash:.2f}",
            "INFO",
        )

    def _create_sell_order(
        self, state: StrategyState, code: str, entry_price: float, extra: dict[str, Any]
    ) -> None:
        """根据卖出信号生成卖出订单。"""
        strategy_name: str = state.strategy_name
        positions: dict[str, dict[str, Any]] = self._load_positions(state)
        pos = positions.get(code, {})
        hold_quantity: int = int(pos.get("quantity", 0))
        if hold_quantity <= 0:
            self.logger.warning("[%s] 卖出信号但无持仓: code=%s", strategy_name, code)
            return
        if code in state.pending_codes:
            self.logger.debug("[%s] code=%s 已有在途订单，跳过重复卖出", strategy_name, code)
            return
        current_price: float = float(extra.get("current_price", 0))
        if current_price <= 0:
            self.logger.warning("[%s] 卖出信号缺少有效价格: code=%s", strategy_name, code)
            return
        # 预估卖出回笼资金（订单完成后由 _load_positions 按实际成交价修正）
        estimated_proceeds: float = current_price * hold_quantity
        # 先标记在途，避免并发重复下单；回笼资金在提交失败时回滚
        state.pending_codes.add(code)
        state.available_cash += estimated_proceeds
        order = OrderDao(
            user_strategy_id=state.user_strategy_id,
            stock_code=code,
            entrust_quantity=hold_quantity,
            trade_price=current_price,
            position_price=entry_price,
            action="卖出",
            status=OrderStatus.ENTRUST.value,
            create_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        ok: bool = self._submit_trade(order, state, rollback_amount=estimated_proceeds)
        if not ok:
            state.pending_codes.discard(code)
            state.available_cash -= estimated_proceeds
            return
        self._save_runlog(
            state,
            f"卖出信号: code={code}, price={current_price:.2f}, "
            f"entry={entry_price:.2f}, qty={hold_quantity}, "
            f"proceeds≈{estimated_proceeds:.2f}, available={state.available_cash:.2f}",
            "INFO",
        )

    # ---- 交易订单 ----

    def _submit_trade(
        self,
        order: OrderDao,
        state: StrategyState,
        on_complete: Callable[[TradeExecutionResult], None] | None = None,
        rollback_amount: float = 0.0,
    ) -> bool:
        """提交交易订单到 TradeManager。

        :param order: 待提交订单
        :param state: 用户策略状态
        :param on_complete: 订单完成后的额外回调
        :param rollback_amount: 提交前预估变更的资金金额（失败/订单失败时回滚）
        :return: 是否成功提交
        """
        strategy_name: str = state.strategy_name

        def on_order_complete(result: TradeExecutionResult) -> None:
            # 无论成败都移除在途标记；失败时回滚预估资金
            state.pending_codes.discard(result.order.stock_code)
            if result.status == OrderStatus.FAILED.value and rollback_amount != 0:
                if result.action == "卖出":
                    state.available_cash -= rollback_amount
                else:
                    state.available_cash += rollback_amount
            self.logger.info(
                "[%s] 订单完成: code=%s, action=%s, status=%s, exec_price=%.2f, profit=%.2f",
                strategy_name, result.order.stock_code, result.action,
                result.status, result.execution_price, result.order.profit_amount,
            )
            self._load_positions(state, force_refresh=True)
            if on_complete is not None:
                on_complete(result)

        try:
            self._trade_manager.submit_order(
                order=order, callback=on_order_complete, delay_seconds=0.0,
            )
            self.logger.info(
                "[%s] 订单已提交: code=%s, action=%s, qty=%d, price=%.2f",
                strategy_name, order.stock_code, order.action,
                order.entrust_quantity, order.trade_price,
            )
            return True
        except Exception as exc:
            self.logger.error("[%s] 订单提交失败: %s", strategy_name, exc, exc_info=True)
            return False

    def _calc_buy_quantity(self, state: StrategyState, price: float) -> int:
        """根据剩余可用资金和价格计算买入数量（100 股整数倍）。

        单笔买入金额上限 MAX_SINGLE_ORDER_AMOUNT（默认 10 万元），
        避免单笔一次性动用全部资金；可用资金已包含已实现盈利。
        """
        if price <= 0:
            return 0
        available: float = state.available_cash
        if available <= 0:
            self.logger.warning("[%s] 可用资金不足，无法买入", state.strategy_name)
            return 0
        max_amount: float = min(available, self.MAX_SINGLE_ORDER_AMOUNT)
        quantity: int = int(max_amount / price / 100) * 100
        if quantity < 100:
            return 0
        return quantity

    # ---- 持仓管理 ----

    def _load_positions(
        self, state: StrategyState, force_refresh: bool = False
    ) -> dict[str, dict[str, Any]]:
        """加载策略的当前持仓信息。"""
        if not force_refresh and state.positions:
            return dict(state.positions)
        if not state.user_strategy_id:
            return {}
        try:
            exec_data: dict[str, Any] = self._user_strategy_api.get_latest_execution(
                state.user_strategy_id
            )
            positions_list: list[dict[str, Any]] = exec_data.get("positions", [])
            positions: dict[str, dict[str, Any]] = {
                p.get("code", ""): p for p in positions_list if p.get("code")
            }
            state.positions = positions
            ia: float = float(exec_data.get("initial_amount", 0))
            if ia > 0:
                state.initial_amount = ia
            state.current_profit = float(exec_data.get("current_profit", 0) or 0)
            if "remaining_cash" in exec_data:
                state.available_cash = float(exec_data.get("remaining_cash", 0) or 0)
            else:
                state.available_cash = self._calc_available_cash(state)
            return positions
        except Exception as exc:
            self.logger.debug(
                "加载持仓信息失败 user_strategy_id=%s: %s", state.user_strategy_id, exc
            )
            return {}

    # ---- 日志记录 ----

    def _save_runlog(
        self, state: StrategyState, message: str, level: str = "INFO"
    ) -> None:
        """保存策略运行日志到服务器。"""
        if not state.user_strategy_id:
            return
        try:
            self._user_strategy_api.save_runlog(
                user_strategy_id=state.user_strategy_id,
                log_content=message,
                level=level,
            )
        except Exception as exc:
            self.logger.warning("保存运行日志失败: %s", exc)

    # ---- 定时器 ----

    def _setup_scheduler(self) -> None:
        """设置定时器，每个交易日盘前运行 daily_before_trading。"""
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        hour: int = 8
        minute: int = 0
        try:
            from configparser import ConfigParser
            from utils.tools import resource_path
            cp = ConfigParser()
            cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
            if cp.has_section("scheduler"):
                hour = cp.getint("scheduler", "before_trading_hour", fallback=9)
                minute = cp.getint("scheduler", "before_trading_minute", fallback=0)
        except Exception as exc:
            self.logger.warning("读取 scheduler 配置失败，使用默认值 8:00: %s", exc)

        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self.daily_before_trading,
            CronTrigger(hour=hour, minute=minute, day_of_week="mon-fri"),
            id="daily_before_trading",
            name="每日盘前策略筛选",
            misfire_grace_time=300,
        )
        self.logger.info("定时器已设置: 每个交易日 %02d:%02d 运行盘前筛选", hour, minute)

    def daily_before_trading(self) -> None:
        """每日盘前定时任务。

        对每个策略实例执行 before_trading，然后 select 筛选，
        结果按 strategy_id 保存到 /api/strategy_select_stocks/add。
        """
        trade_date: str = datetime.now().strftime("%Y-%m-%d")
        if not self.xshg.is_session(trade_date):
            self.logger.info("今日 %s 为节假日，跳过盘前筛选", trade_date)
            return
        self.logger.info("===== 每日盘前筛选开始 trade_date=%s =====", trade_date)

        # 查询热门行业股票代码
        industry_codes: list[str] = self._collect_hot_industry_codes()

        for strategy_id, instance in self._strategy_instances.items():
            if not self._is_strategy_running(strategy_id):
                continue
            self._run_daily_selection_for_strategy(
                strategy_id, instance, industry_codes, trade_date
            )

        self.logger.info("===== 每日盘前筛选结束 trade_date=%s =====", trade_date)

    def _collect_hot_industry_codes(self) -> list[str]:
        """查询热门行业下的股票代码列表。"""
        industry_codes: list[str] = []
        try:
            hot_industries: list[dict[str, Any]] = self._stock_info_api.get_hot_industries()
            self.logger.info("获取到 %d 个热门行业", len(hot_industries))
            industry_names: set[str] = set()
            for ind in hot_industries:
                name: str = str(ind.get("name", ind.get("industry", ind.get("industry_name", ""))))
                if name and name not in industry_names:
                    industry_names.add(name)
            for iname in industry_names:
                try:
                    stocks: list[dict[str, Any]] = self._stock_info_api.get_by_industry(iname)
                    for s in stocks:
                        scode: str = str(s.get("code", s.get("stock_code", "")))
                        if scode and scode not in industry_codes:
                            industry_codes.append(scode)
                except Exception:
                    self.logger.warning("查询行业「%s」股票列表失败", iname, exc_info=True)
            self.logger.info("热门行业共 %d 只股票", len(industry_codes))
        except Exception:
            self.logger.warning("查询热门行业失败", exc_info=True)
        return industry_codes

    def _run_daily_selection_for_strategy(
        self,
        strategy_id: str,
        instance: BaseStrategy,
        industry_codes: list[str],
        trade_date: str,
    ) -> None:
        """对单个策略执行盘前筛选并保存结果。"""
        sname: str = instance.strategy_name

        # 收集所有用户此策略的股票池代码
        pool_codes: list[str] = []
        for us_state in self._user_strategies.get(strategy_id, []):
            self._reload_stock_pool(us_state)
            pool_codes.extend(us_state.stock_pool)

        # 去重
        pool_codes = list(dict.fromkeys(pool_codes))

        # 1. before_trading
        try:
            instance.before_trading(trade_date=trade_date, stock_codes=pool_codes)
            self.logger.info("[%s] before_trading 完成", sname)
        except Exception as exc:
            self.logger.error("[%s] before_trading 异常: %s", sname, exc, exc_info=True)

        # 2. 合并股票池 + 热门行业
        combined: list[str] = list(dict.fromkeys(pool_codes + industry_codes))
        self.logger.info(
            "[%s] 股票池 %d 只 + 热门行业 %d 只 = 合并 %d 只",
            sname, len(pool_codes), len(industry_codes), len(combined),
        )

        if not combined:
            self.logger.info("[%s] 合并后股票列表为空，跳过筛选", sname)
            return

        # 3. select 筛选
        try:
            results: list[dict[str, Any]] = instance.select(combined, today_str=trade_date, days=20)
        except Exception as exc:
            self.logger.error("[%s] select 筛选异常: %s", sname, exc, exc_info=True)
            return

        matched_codes: list[str] = [
            str(r["code"]) for r in results if r.get("matched", False)
        ]
        # 收集该策略模板下所有用户的黑名单并过滤
        blacklist_all: set[str] = set()
        for us_state in self._user_strategies.get(strategy_id, []):
            blacklist_all |= us_state.blacklist_codes
        if blacklist_all:
            before: int = len(matched_codes)
            matched_codes = [c for c in matched_codes if c not in blacklist_all]
            if before != len(matched_codes):
                self.logger.info(
                    "[%s] 盘前筛选已过滤 %d 只黑名单股票", sname, before - len(matched_codes),
                )
        self.logger.info(
            "[%s] 盘前筛选完成: 筛选 %d 只, 符合策略 %d 只",
            sname, len(combined), len(matched_codes),
        )

        # 4. 保存选股结果
        if matched_codes:
            try:
                self._strategy_select_stock_api.add(strategy_id, matched_codes)
                self.logger.info(
                    "[%s] 策略选股结果已保存: codes=%s", sname, matched_codes,
                )
            except Exception as exc:
                self.logger.error("[%s] 保存策略选股结果失败: %s", sname, exc, exc_info=True)

        # 5. 记录日志（按用户策略）
        for us_state in self._user_strategies.get(strategy_id, []):
            self._save_runlog(
                us_state,
                f"盘前筛选: trade_date={trade_date}, "
                f"筛选前 {len(combined)} 只, 符合策略 {len(matched_codes)} 只",
                "INFO",
            )

    # ---- 生命周期 ----

    def on_start(self) -> None:
        """工作流启动钩子：启动定时器，自动激活运行中状态的策略。

        策略模板和用户配置已在 _init_workflow() 中加载完成。
        """
        self.logger.info("策略工作流启动，定时器初始化...")
        self._setup_scheduler()
        if self._scheduler is not None:
            self._scheduler.start()
            self.logger.info("每日盘前定时器已启动")


    def on_stop(self) -> None:
        """工作流停止钩子。"""
        self.logger.info("策略工作流停止中...")
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        for states in self._user_strategies.values():
            for state in states:
                if state.status == "running":
                    state.status = "paused"
                    self._save_runlog(state, f"策略 {state.strategy_name} 已暂停（工作流关闭）", "INFO")
        self._strategy_instances.clear()
        self._user_strategies.clear()
