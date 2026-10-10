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
    - _strategy_templates: 策略模板元数据（按 strategy_id 缓存）
    - _strategy_instances: 策略模板代表实例（按 strategy_id，用于模板级选股）
    - _user_strategies: 用户策略配置（按 strategy_id 分组，每个 state 持有独立策略实例）
    - 策略决定信号 → 记录 /api/trade_signal/add → 对应用户股票池下单
"""
from __future__ import annotations

import importlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from api.client import ApiClient, is_not_found
from api.pool import PoolApi
from api.stock_info import StockInfoApi
from api.strategy import StrategyApi
from api.trade_signal import TradeSignalApi
from api.strategy_select_stock import StrategySelectStockApi
from api.user_strategy import UserStrategyApi
from api.workflow_service_user_strategy import WorkflowServiceUserStrategyApi
from app_context import AppContext
from core.user_strategy_cache import UserStrategyCache
from dao.order_dao import OrderDao, OrderStatus
from dao.user_strategy_dao import (
    DEFAULT_INITIAL_AMOUNT as DAO_DEFAULT_INITIAL_AMOUNT,
    DEFAULT_MAX_STOCK_COUNT as DAO_DEFAULT_MAX_STOCK_COUNT,
    StrategyStatus,
    UserStrategyDao,
    parse_initial_amount,
    parse_max_stock_count,
)
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
    strategy_name: str  # 策略名称（策略模板/策略类名，如「波段交易策略」）
    class_path: str  # 策略类模块路径
    class_name: str  # 策略类名
    # 用户策略名称（UserStrategyDao.name，用户在管理端给自己的策略起的名字）。
    # 交易信号推送、通知的 recipient 一律用它，不再用 strategy_name：同一个模板
    # 下用户可能建多个策略，只有用户自定义名才能区分是哪一条策略发出的信号。
    user_strategy_name: str = ""
    instance: BaseStrategy | None = None  # 策略实例对象
    status: str = "stopped"  # 运行状态: running / stopped / paused
    stock_pool: list[str] = field(default_factory=list)  # 关注的股票池
    pool_id: str = ""  # 关联的股票池 ID，用于每日重新加载股票池
    positions: dict[str, dict[str, Any]] = field(default_factory=dict)  # code -> 持仓信息
    load_error: str = ""  # 加载失败时的错误信息
    # 初始资金（来自 UserStrategyDao.initial_amount）。
    # 默认值 10 万与服务端默认一致；仅当服务端字段缺失时才落到这个默认。
    initial_amount: float = 100000.0
    # 用户策略是否显式配置了 `initial_amount`（值为 0 也算配置）。
    # 配置 0 是有效语义 —— 表示限制买入（2026-09-24 语义确认）。
    # True 时禁止用历史执行记录里的 initial_amount 覆盖，否则配置的 0 会被
    # 旧记录里的正数「洗白」，限制失效（见 `_load_execution_data` / `_load_positions`）。
    initial_amount_configured: bool = False
    current_profit: float = 0.0  # 已实现盈利（来自 StrategyExecutionDao.current_profit）
    # 最大持仓数量（来自 UserStrategyDao.max_stock_count）。
    # 默认 -1 = 不限制持仓数量；仅当服务端字段缺失/非法时才落到这个默认。
    # 0 不是默认值，它是「限制买入」的有效配置（2026-09-24 语义确认）。
    max_stock_count: int = -1
    available_cash: float = 0.0  # 剩余可用资金（初始资金 + 已实现盈利 - 持仓成本）
    pending_codes: set[str] = field(default_factory=set)  # 在途订单股票代码（去重）
    blacklist_codes: set[str] = field(default_factory=set)  # 用户黑名单股票代码（策略信号/选股时过滤）
    # 是否已提示过「该用户策略尚无执行记录」（仅用于日志去重，见 _load_positions）
    no_execution_record_noted: bool = False
    # 账户权益峰值（初始资金 + 已实现盈利的历史最高值），用于账户级回撤判定。
    # 进程重启后由历史执行记录重建（见 _load_execution_data）。
    peak_asset: float = 0.0
    # 账户回撤是否已按 WARNING 记过一次日志（避免逐笔重复告警；回撤回到阈值内复位）
    drawdown_breaker_noted: bool = False
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

    # 选股时加载的日线根数（`instance.select(..., days=...)`）。
    # 2026-09-28 由 20 放大到 40：MultiTrendFactor 的 MACD 分项需要 34 根才有值
    # （26+9−1），而判定是「红柱比上一根放大」，所以实际要 **35 根**才能成立 ——
    # 20 根时该分项恒为 NaN，趋势分上限被静默折损 3 分（那 3 分从来没拿到过）。
    # 取 40 留 5 根余量。回测的 `signal_window` 必须与它保持一致，否则选股链路与
    # 回测链路的趋势分不可比（BacktestConfig.signal_window 同值）。
    SELECT_KLINE_DAYS: int = 40

    # 用户策略未配置 `max_stock_count` / `initial_amount` 时的默认值，
    # 与服务端 UserStrategyCreateRequest / UserStrategyItem 的默认值一致：
    # 不限制持仓数量 + 10 万初始资金。
    # 这里直接引用 dao 模块常量，不在本处重写字面量 —— 两处默认值一旦漂移，
    # 「配置生效与否」会随加载路径不同而不同，很难查。
    # 注意 `0` 仍是「限制买入」的有效配置语义（2026-09-24 确认），不是默认值 ——
    # 只有字段缺失或非法时才落回这两个默认。
    DEFAULT_MAX_STOCK_COUNT: int = DAO_DEFAULT_MAX_STOCK_COUNT
    DEFAULT_INITIAL_AMOUNT: float = DAO_DEFAULT_INITIAL_AMOUNT

    # 多实例分片：查询本实例负责的 user_strategy_ids 的重试次数与间隔
    # （覆盖启动瞬间后台尚未就绪的情况；查询失败一律空转，见
    #  `_load_assigned_user_strategy_ids`）
    SERVICE_ASSIGN_RETRIES: int = 3
    SERVICE_ASSIGN_RETRY_INTERVAL_SECONDS: float = 2.0

    # ---- 配置对账（热更新）----
    # 这些配置原先只在进程启动时读一次，界面上改了参数或股票池要重启才生效。
    # 触发有两条：服务端写库后 publish 的变更通知（即时），以及下面的定时轮询
    # （兜底：漏发通知、进程启动早于服务端、MQTT 掉线重连期间）。
    RECONCILE_INTERVAL_SECONDS: int = 60  # 交易时段的对账周期
    RECONCILE_INTERVAL_OFFHOURS_SECONDS: int = 300  # 非交易时段放长，省 HTTP
    # 节流阈值相对周期留出的余量比例（见 `_reconcile_job`）。
    # 必须 < 1：时间戳记在单轮对账**结束**时刻，若阈值等于周期，
    # 下一轮触发时"距上次"恒为「周期 − 单轮耗时」，永远差那零点几秒。
    RECONCILE_THROTTLE_RATIO: float = 0.9
    # 盘中改池/改参数后补跑当日选股的最小间隔（秒）。
    # 补跑要对全池取日 K，是这里最贵的一步；用户连续调几次参数时不能每次都跑。
    SELECTION_REFRESH_MIN_INTERVAL_SECONDS: float = 300.0
    # 热门行业代码的缓存时间（秒）。补跑选股要用它拼候选集，查一次要按行业逐个
    # 取股票列表，属于批量 HTTP，不能每次补跑都重查。
    HOT_INDUSTRY_CACHE_TTL_SECONDS: float = 1800.0

    def __init__(self, pool_size: int | None = None) -> None:
        """初始化策略工作流。

        使用独立的 ApiClient 实例（admin token），与 Web 会话的 ApiClient 隔离，
        确保后台线程中也能以管理员身份访问所有用户的策略数据。

        :param pool_size: 线程池大小，默认使用基类的 DEFAULT_POOL_SIZE
        """
        super().__init__(pool_size=pool_size)
        self.logger: logging.Logger = logging.getLogger(__name__)

        # 策略实例（按 strategy_id 保留代表实例，用于模板级选股）
        self._strategy_instances: dict[str, BaseStrategy] = {}
        # 策略模板元数据（按 strategy_id 缓存 class_path/class_name/default_params 等）
        self._strategy_templates: dict[str, dict[str, Any]] = {}
        # 用户策略配置（按 strategy_id 分组）
        self._user_strategies: dict[str, list[StrategyState]] = {}
        # 注册表锁：保护 _user_strategies / _strategy_instances 的增删改查，
        # 避免行情回调线程迭代时被增删操作破坏（RuntimeError: dict changed size）
        self._registry_lock: threading.RLock = threading.RLock()
        # 全局缓存单例：行情（本类唯一写者）、打分、选股结果
        self._cache: UserStrategyCache = UserStrategyCache()
        # 实时 tick 派发开关（cfg/stock.cfg [workflow] enable_handle_tick）
        self._enable_handle_tick: bool = tools.load_enable_handle_tick()
        # ---- P2-23：行情派发背压（合并派发） ----
        # 待处理行情：(user_strategy_id, code) -> (state, code, 最新一根行情, 是否 tick)。
        # 同一 key 只保留**最新**一根，避免整批行情把线程池的无界队列打满后
        # 还在计算早已过期的数据。多个 key 由单一 drain 任务在池内串行消费。
        self._pending_bars: dict[
            tuple[str, str], tuple[StrategyState, str, dict[str, Any], bool]
        ] = {}
        self._pending_lock: threading.Lock = threading.Lock()
        self._draining: bool = False
        self._max_pending_bars: int = tools.load_order_queue_size()
        self._dropped_bar_tasks: int = 0  # 因背压被丢弃的行情任务数（仅计数）

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
        # ---- 配置对账（热更新）状态 ----
        # 同一时刻只允许一轮对账：MQTT 通知与定时轮询可能同时触发，而对账里
        # 「新建实例」和「订阅 MQTT topic」都不幂等，并发跑会重复订阅（行情翻倍）。
        self._reconcile_lock: threading.Lock = threading.Lock()
        self._last_reconcile_ts: float = 0.0  # 上一次对账完成时间（非交易时段节流用）
        # 每个 user_strategy 上一次补跑选股的时间，避免连续调参反复跑全池选股
        self._selection_refresh_ts: dict[str, float] = {}
        # 热门行业代码缓存（补跑选股用）：(时间戳, 代码列表)
        self._hot_industry_cache: tuple[float, list[str]] | None = None
        # 初始化上交所交易日历（用于判断交易日），从 2020-01-01 开始
        self.xshg = xcals.get_calendar("XSHG", start="2020-01-01")  # 上交所交易日历，用于判断交易日

    # ---- 注册表并发访问（P1-19） ----

    def _enqueue_bar(
        self,
        state: StrategyState,
        code: str,
        bar_data: dict[str, Any],
        is_tick: bool = False,
    ) -> None:
        """把「某用户策略 × 某股票 × 最新一根行情」放入待处理表（P2-23）。

        原实现直接 `submit(self._process_bar, ...)`，而 `ThreadPoolExecutor`
        的队列是无界的：一批几百只股票 × N 个用户会瞬间灌入上千个任务，
        行情突发时任务会堆积到几分钟后才执行，处理的却已是过期数据。

        改为「**合并派发**」：
        - 同一 `(user_strategy_id, code)` 只保留最新一根行情（后到覆盖先到）；
        - 整张待处理表最多 `order_queue_size` 条，超限时丢弃**最早入队**的
          （即最旧的行情），保证始终在处理最新数据；
        - 由**单个** drain 任务在池内循环消费，因此线程池中与行情相关的
          排队任务数从 O(股票数×用户数) 降到 O(1)。

        :param state: 用户策略运行时状态
        :param code: 股票代码
        :param bar_data: 分钟行情 / 实时行情数据
        :param is_tick: True 表示实时 tick 行情（走 `_process_tick`）
        """
        key: tuple[str, str] = (state.user_strategy_id, code)
        need_schedule: bool = False
        with self._pending_lock:
            # 先删后插：让 dict 的插入顺序 = 最近更新时间顺序，便于淘汰最旧数据
            self._pending_bars.pop(key, None)
            self._pending_bars[key] = (state, code, bar_data, is_tick)
            overflow: int = len(self._pending_bars) - self._max_pending_bars
            if overflow > 0:
                for stale_key in list(self._pending_bars.keys())[:overflow]:
                    if stale_key == key:
                        continue
                    self._pending_bars.pop(stale_key, None)
                    self._dropped_bar_tasks += 1
                self.logger.warning(
                    "行情派发背压：待处理队列已满(%d)，丢弃 %d 条最旧行情",
                    self._max_pending_bars, overflow,
                )
            if not self._draining:
                self._draining = True
                need_schedule = True

        if need_schedule:
            try:
                self.submit(self._drain_pending_bars)
            except RuntimeError as exc:
                # 线程池已关闭（工作流停止中）：放回标志，避免后续行情永远不派发
                with self._pending_lock:
                    self._draining = False
                self.logger.warning("派发行情任务失败（线程池可能已关闭）: %s", exc)

    def _drain_pending_bars(self) -> None:
        """循环消费待处理行情表（P2-23）。

        每轮在锁内取走整张表并清空，然后在锁外逐条执行；
        期间新到的行情会继续填入表中，由下一轮循环消费。
        表中为空时清除 `_draining` 标志并退出，等待下一次 `_enqueue_bar` 重新调度。
        """
        while True:
            with self._pending_lock:
                if not self._pending_bars:
                    self._draining = False
                    return
                batch = list(self._pending_bars.values())
                self._pending_bars.clear()
            self._run_pending_batch(batch)

    def _run_pending_batch(
        self, batch: list[tuple[StrategyState, str, dict[str, Any], bool]]
    ) -> None:
        """执行一批待处理行情。

        :param batch: `(state, code, 行情数据, 是否 tick)` 列表
        """
        for state, code, bar_data, is_tick in batch:
            try:
                if is_tick:
                    self._process_tick(state, code, bar_data)
                else:
                    self._process_bar(state, code, bar_data)
            except Exception as exc:  # pragma: no cover - 内部已兜底
                self.logger.error(
                    "处理待派发行情异常: code=%s: %s", code, exc, exc_info=True
                )

    def flush_pending_bars(self) -> int:
        """同步消费所有待处理行情（供停止流程与测试使用，P2-23）。

        :return: 本轮处理的行情条数
        """
        total: int = 0
        while True:
            with self._pending_lock:
                if not self._pending_bars:
                    self._draining = False
                    return total
                batch = list(self._pending_bars.values())
                self._pending_bars.clear()
            self._run_pending_batch(batch)
            total += len(batch)

    def _snapshot_states(self) -> list[StrategyState]:
        """在注册表锁保护下返回所有 StrategyState 的快照列表。

        行情回调线程在迭代运行中策略时会与「新增/删除用户策略」的线程并发，
        直接迭代 `self._user_strategies` 会抛
        `RuntimeError: dictionary changed size during iteration`。
        这里先在锁内把 state 引用收集成一个独立 list，之后在锁外迭代，
        既避免迭代被破坏，也避免持锁期间调用网络 IO（P2-24）。

        :return: StrategyState 快照列表（元素为引用，非深拷贝）
        """
        try:
            with self._registry_lock:
                return [
                    s for states in self._user_strategies.values() for s in states
                ]
        except RuntimeError:
            # 极端情况下的兜底：返回空快照，本轮不派发，下一轮行情会重试
            self.logger.warning("_snapshot_states 获取策略快照失败，本轮跳过派发")
            return []

    # ---- 工作流初始化（_init_workflow 钩子） ----

    def _init_workflow(self) -> None:
        """在 MQTT 订阅前执行初始化：加载 service 配置、策略模板和用户策略。

        1. 从配置文件读取 service_name
        2. 调用 load_strategies_from_db() 加载策略模板和用户策略

        **不在这里写 `_subscribed_pool_ids`**：那是由 `_collect_startup_pool_ids()`
        交给基类订阅后回填的「已订阅」登记表（见 BaseWorkflow._subscribed_pool_ids 注释）。
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

    def _collect_startup_pool_ids(self) -> list[str]:
        """收集启动时需要订阅的股票池 ID（供基类 pool 级 MQTT 订阅）。

        :return: 去重后的 pool_id 列表；为空表示没有策略绑定股票池，走全局通配订阅
        """
        pool_ids: set[str] = set()
        for states in self._user_strategies.values():
            for state in states:
                if state.pool_id:
                    pool_ids.add(state.pool_id)

        if pool_ids:
            self.logger.info("收集到 %d 个 pool_id: %s", len(pool_ids), pool_ids)
        else:
            self.logger.warning("未收集到任何 pool_id，将使用全局 MQTT 订阅")
        return sorted(pool_ids)

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
        self,
        strategy_cls: type[BaseStrategy],
        user_strategy_id: str = "",
        user_strategy_name: str = "",
        strategy_id: str = "",
        strategy_type: str = "",
        user_strategy_params: dict[str, Any] | None = None,
        default_params: dict[str, Any] | None = None,
    ) -> BaseStrategy | None:
        """实例化策略对象，并传入 user_strategy_id / strategy_id 及参数用于初始化。"""
        try:
            instance: BaseStrategy = strategy_cls(
                user_strategy_id=user_strategy_id,
                user_strategy_name=user_strategy_name,
                strategy_id=strategy_id,
                strategy_type=strategy_type,
                user_strategy_params=user_strategy_params,
                default_params=default_params,
            )
            return instance
        except Exception as exc:
            self.logger.error("实例化策略 %s 失败: %s", strategy_cls.__name__, exc, exc_info=True)
            return None

    def _calc_available_cash(self, state: StrategyState) -> float:
        """根据初始资金、已实现盈利与持仓成本计算剩余可用资金。

        资金模型（与 TradeManager._refresh_execution_metrics 保持同一口径）：
            总资产 = 初始资金 + 已实现盈利
            剩余可用资金 = 总资产 - 持仓成本（数量 × 成本价）

        其中 `cost_price` **已含买入手续费**（`(成交金额 + 手续费) / 数量`），
        因此不能再单独扣减一次手续费，否则会重复计费。
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

    # ---- 账户级回撤（卖出分值的来源）----

    def _account_asset(self, state: StrategyState) -> float:
        """当前账户权益（初始资金 + 已实现盈利）。

        与 `_calc_available_cash` 的「总资产」以及回测
        `BacktestEngine._compute_equity_curve` 同源：都是**已实现口径**，
        不含持仓浮动盈亏 —— 因此熔断判定与回测汇总的最大回撤可直接对照。
        """
        return float(state.initial_amount or 0) + float(state.current_profit or 0)

    def _update_peak_asset(self, state: StrategyState) -> None:
        """刷新账户权益峰值（幂等，只增不减）。"""
        asset: float = self._account_asset(state)
        if asset > state.peak_asset:
            state.peak_asset = asset

    def _restore_peak_asset(self, state: StrategyState) -> None:
        """从历史执行记录重建账户权益峰值，再与当前权益取大。

        执行记录里没有 total_asset 字段，「权益」= `initial_amount + current_profit`。
        重建失败不阻塞策略：退化为以当前权益为峰值，熔断从此刻起算（宁可晚触发，
        不可因取数故障把策略卡死）。

        :param state: 用户策略运行时状态
        """
        if not state.user_strategy_id:
            self._update_peak_asset(state)
            return
        try:
            history: list[dict[str, Any]] = self._user_strategy_api.get_execution(
                state.user_strategy_id
            )
        except Exception as exc:
            if is_not_found(exc):
                # 尚无执行记录 = 新策略的正常状态（404 表示无数据），不是故障
                self._update_peak_asset(state)
                return
            self.logger.warning(
                "[%s] 重建账户权益峰值失败（熔断以当前权益为峰值起算）: %s",
                state.strategy_name, exc,
            )
            self._update_peak_asset(state)
            return
        for rec in history or []:
            asset: float = float(rec.get("initial_amount", 0) or 0) + float(
                rec.get("current_profit", 0) or 0
            )
            if asset > state.peak_asset:
                state.peak_asset = asset
        self._update_peak_asset(state)

    def _refresh_account_drawdown(self, state: StrategyState) -> float:
        """计算账户当前回撤（%）并注入策略实例，供其卖出判定使用。

        语义（2026-09-29 用户定）：回撤达阈值时**计入卖出分值**
        （`BaseStrategy.SELL_RISK_DRAWDOWN_SCORE`），**不冻结开新仓** ——
        因此本方法不再返回阻断原因，只负责把回撤喂给策略，由策略的
        `_check_sell_conditions` 决定是否据此卖出。
        阈值取策略实例的 `RISK_MAX_DRAWDOWN_PCT`（%，负值有效；>= 0 表示关闭），
        回撤计算式与回测 `BacktestEngine._drawdown_at` 一致。

        :param state: 用户策略运行时状态
        :return: 账户当前回撤（%，<= 0；无峰值或未绑定时为 0.0）
        """
        instance: BaseStrategy | None = state.instance
        if instance is None:
            return 0.0
        peak: float = float(state.peak_asset or 0)
        drawdown_pct: float = 0.0
        if peak > 0:
            drawdown_pct = (self._account_asset(state) - peak) / peak * 100
        instance.set_account_drawdown(drawdown_pct)

        threshold: float = float(getattr(instance, "RISK_MAX_DRAWDOWN_PCT", 0.0) or 0.0)
        if threshold < 0 and drawdown_pct <= threshold:
            if not state.drawdown_breaker_noted:
                state.drawdown_breaker_noted = True
                self.logger.warning(
                    "[%s] 账户回撤 %.2f%% 已达阈值 %.2f%%：卖出判定计入 %.2f 分"
                    "（不冻结开新仓）",
                    state.strategy_name, drawdown_pct, threshold,
                    float(getattr(instance, "SELL_RISK_DRAWDOWN_SCORE", 0.0) or 0.0),
                )
        else:
            # 回撤回到阈值内 → 复位告警标记，下次触发能再记一次 WARNING
            state.drawdown_breaker_noted = False
        return drawdown_pct

    def _load_execution_data(self, state: StrategyState) -> None:
        """加载策略执行数据，填充初始资金、持仓和剩余资金。

        「无执行记录」（新策略）与「取数失败」都只影响启动时的一次性初始化：
        两者都回退到本地资金口径计算，不阻塞策略运行（下单前的持仓判定由
        `_load_positions` 把关，那里才做 fail-closed）。区别在于日志级别——
        前者是正常状态（DEBUG），后者是真故障（WARNING）。
        """
        if not state.user_strategy_id:
            return
        try:
            exec_data: dict[str, Any] = self._user_strategy_api.get_latest_execution(
                state.user_strategy_id
            )
        except Exception as exc:
            # 真实故障（超时/5xx/连接失败）；「无执行记录」的 404 已在 API 层收敛为 {}，
            # 不会走到这里。启动阶段故障只降级为本地资金推算，不阻塞策略运行。
            self.logger.warning(
                "加载策略执行数据失败（回退本地资金口径）%s: %s",
                state.strategy_name, exc,
            )
            # 即使 API 异常，也确保 available_cash 被初始化（并建立权益峰值基线）
            state.available_cash = self._calc_available_cash(state)
            self._update_peak_asset(state)
            return
        if not exec_data:
            # 无执行数据时（新策略），仍需要初始化 available_cash
            self.logger.debug(
                "策略 %s 尚无执行记录，按初始资金口径初始化可用资金",
                state.strategy_name,
            )
            state.available_cash = self._calc_available_cash(state)
            self._update_peak_asset(state)
            return
        # initial_amount 以用户策略配置为真源（已在 state 初始化时设置），
        # 执行数据中的值只在「配置里没有该字段」时兜底。
        # 配置为 0 表示限制买入（2026-09-24 语义确认），不能被历史记录里的正数覆盖。
        ia: float = float(exec_data.get("initial_amount", 0))
        if ia > 0 and state.initial_amount <= 0:
            if state.initial_amount_configured:
                self.logger.info(
                    "[%s] 策略配置 initial_amount=0（0 表示限制买入），"
                    "忽略执行记录中的历史 initial_amount=%.2f",
                    state.strategy_name, ia,
                )
            else:
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
        # 峰值必须在这里重建：进程重启后 state 里没有历史，若以「当前权益」为峰值
        # 起步，回撤会被系统性低估，熔断等于失效。
        self._restore_peak_asset(state)

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

    def _is_signal_in_user_scope(self, state: StrategyState, code: str) -> bool:
        """**X2**：判断信号的股票是否落在该用户的可见范围内（股票池 + 黑名单）。

        设计决策：信号与推送应**只针对用户股票池内**的标的——用户看不到的股票
        出现在信号列表 / MQTT 推送里只会造成干扰。因此本判定必须在
        `_save_trade_signal`（写库 + 推送）**之前**执行，而不是只拦订单。

        语义保持与原「订单前过滤」一致：
        - `state.stock_pool` 为空 → 视为未配置股票池，不做池过滤（放行）；
        - `state.stock_pool` 非空且 code 不在其中 → 过滤；
        - 黑名单命中 → 过滤（dispatch 阶段已过滤一次，此处为二次兜底，
          因为股票池可能在派发后被 `_reload_stock_pool` 刷新）。

        :param state: 策略运行时状态
        :param code: 股票代码
        :return: True 表示在用户可见范围内，可以写库/推送/下单
        """
        if self._is_blacklisted(state, code):
            return False
        if state.stock_pool and code not in state.stock_pool:
            self.logger.debug(
                "[%s] code=%s 不在股票池内，信号不记录不推送",
                state.strategy_name, code,
            )
            return False
        return True

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
            # 与 update_strategy_stock_pool 保持一致：重载时同样剔除黑名单股票
            codes = [c for c in codes if not self._is_blacklisted(state, c)]
            state.stock_pool = codes
        except Exception as exc:
            self.logger.error("重新加载策略 %s 股票池失败: %s", state.strategy_name, exc, exc_info=True)

    # ---- 新架构：加载策略实例 + 用户策略配置 ----

    def _build_strategy_instance(
        self,
        template: dict[str, Any],
        user_strategy_id: str = "",
        user_strategy_name: str = "",
        user_strategy_params: dict[str, Any] | None = None,
    ) -> BaseStrategy | None:
        """根据策略模板构建策略实例。

        :param template: 策略模板字典
        :param user_strategy_id: 用户策略关联 ID，传入后绑定到策略实例
        :param user_strategy_params: 用户策略参数（UserStrategyDao.strategy_params）
        :return: 策略实例，失败返回 None
        """
        name: str = template.get("name", "")
        class_path: str = template.get("class_path", "")
        class_name: str = template.get("class_name", "")
        strategy_id: str = str(template.get("id") or template.get("_id") or "")
        strategy_type: str = template.get("strategy_type", "")

        if not class_path or not class_name or not strategy_id:
            self.logger.warning("策略 %s 缺少必要字段，跳过", name)
            return None

        strategy_cls = self._load_strategy_class(class_path, class_name)
        if strategy_cls is None:
            return None

        instance = self._instantiate_strategy(
            strategy_cls,
            user_strategy_id=user_strategy_id,
            user_strategy_name=user_strategy_name,
            strategy_id=strategy_id,
            strategy_type=strategy_type,
            user_strategy_params=user_strategy_params if user_strategy_params is not None else {},
            default_params=template.get("default_params"),
        )
        if instance is not None:
            instance.strategy_id = strategy_id
        return instance

    def load_strategies_from_db(self) -> list[StrategyState]:
        """加载策略：按用户策略逐个实例化（每个 user_strategy 一个独立实例）。

        1. 从 /api/strategy/list 获取策略模板元数据
        2. 从 /api/user_strategy/all 获取用户策略关联，按 strategy_id 分组
        3. 为每个用户策略实例化独立策略实例（传入 user_strategy_id 与参数），
           并加载股票池和持仓数据

        :return: 所有加载成功的 StrategyState 列表
        """
        if not self._load_strategy_instances_from_db():
            return []
        return self._build_user_strategy_states()

    def _load_strategy_instances_from_db(self) -> bool:
        """从 /api/strategy/list 加载策略模板元数据到 _strategy_templates（不实例化）。

        策略实例化延迟到 _build_user_strategy_states 中按 user_strategy 逐个创建，
        以便为每个用户策略传入独立的 user_strategy_id 与参数。
        """
        try:
            db_strategies: list[dict[str, Any]] = self._strategy_api.list()
        except Exception as exc:
            self.logger.error("从数据库加载策略模板列表失败: %s", exc)
            return False

        if not db_strategies:
            self.logger.info("数据库中没有已保存的策略模板")
            return False

        self._strategy_templates.clear()
        for s in db_strategies:
            strategy_id: str = str(s.get("id") or s.get("_id") or "")
            if not strategy_id:
                continue
            self._strategy_templates[strategy_id] = s

        self.logger.info("共加载 %d 个策略模板", len(self._strategy_templates))
        return True

    def _collect_user_strategy_ids(
        self, records: list[dict[str, Any]]
    ) -> set[str]:
        """从分配关系记录中提取 user_strategy_id 集合（兼容多种字段形态）。

        :param records: /api/workflow_service_user_strategy/* 返回的记录列表
        :return: user_strategy_id 集合
        """
        ids: set[str] = set()
        for record in records or []:
            if not isinstance(record, dict):
                continue
            values: Any = record.get("user_strategy_ids")
            if isinstance(values, list):
                ids.update(str(uid) for uid in values if uid)
                continue
            # 兼容「一条记录只带一个 user_strategy_id」的返回形态
            single: Any = record.get("user_strategy_id") or record.get("us_id")
            if single:
                ids.add(str(single))
        return ids

    def _load_assigned_user_strategy_ids(self) -> set[str]:
        """查询后台分配给当前 workflow 实例的 user_strategy_ids（多实例分片）。

        多实例部署时后台按 user_strategy_id 平均分配，实例标识取自
        `cfg/stock.cfg [workflow] service_name`。优先使用后台文档给出的
        `/api/workflow_service_user_strategy/service/{service_name}`；
        该接口取不到数据时回退到 `?service_name=` 查询参数形式，兼容老版本后台。

        **查询失败一律空转（fail-closed）**：宁可本实例不跑策略，也不能退化成
        「加载全部策略」——那会让多个实例同时为同一个 user_strategy 下单。

        :return: 分配给本实例的 user_strategy_id 集合（查询失败返回空集合）
        """
        attempts: int = max(int(self.SERVICE_ASSIGN_RETRIES), 1)
        for attempt in range(1, attempts + 1):
            assigned: set[str] = set()
            # 主接口：后台文档给出的 /api/.../service/{service_name}
            try:
                records: list[dict[str, Any]] = (
                    self._workflow_service_api.list_by_service(self._service_name)
                )
                assigned = self._collect_user_strategy_ids(records)
            except Exception as exc:
                self.logger.error(
                    "查询 service [%s] 主接口 /service/{name} 失败"
                    "（第 %d/%d 次）: %s",
                    self._service_name,
                    attempt,
                    attempts,
                    exc,
                )
            # 回退接口：?service_name= 查询参数形式（老版本后台，或主接口不可用）
            if not assigned:
                try:
                    fallback: list[dict[str, Any]] = self._workflow_service_api.list(
                        service_name=self._service_name
                    )
                    assigned = self._collect_user_strategy_ids(fallback)
                    if assigned:
                        self.logger.warning(
                            "Service [%s] 主接口 /service/{name} 未取到数据，"
                            "已回退到 /list?service_name= 获取到 %d 个 user_strategy",
                            self._service_name,
                            len(assigned),
                        )
                except Exception as exc:
                    self.logger.error(
                        "查询 service [%s] 回退接口 /list?service_name= 失败"
                        "（第 %d/%d 次）: %s",
                        self._service_name,
                        attempt,
                        attempts,
                        exc,
                    )
            if assigned:
                self.logger.info(
                    "Service [%s] 分配 %d 个 user_strategy_ids: %s",
                    self._service_name,
                    len(assigned),
                    sorted(assigned),
                )
                return assigned
            self.logger.warning(
                "Service [%s] 第 %d/%d 次查询未取到分配关系",
                self._service_name,
                attempt,
                attempts,
            )
            if attempt < attempts:
                time.sleep(self.SERVICE_ASSIGN_RETRY_INTERVAL_SECONDS)
        return set()

    def _build_user_strategy_states(self) -> list[StrategyState]:
        """从 /api/user_strategy/all 加载用户策略关联并按 strategy_id 分组。

        当 _service_name 非空时，仅加载分配给当前 workflow 微服务的 user_strategy。
        """
        assigned_ids: set[str] = set()
        if self._service_name:
            assigned_ids = self._load_assigned_user_strategy_ids()
            if not assigned_ids:
                self.logger.warning(
                    "Service [%s] 未分配到任何 user_strategy，工作流将空转"
                    "（查询失败时也会走到这里，请核对 service_name 与后台分配表）",
                    self._service_name,
                )
                return []

        try:
            user_strategies: list[dict[str, Any]] = self._user_strategy_api.list_all()
        except Exception as exc:
            self.logger.error("从数据库加载用户策略关联列表失败: %s", exc)
            return []

        # 注册表整体重建：加锁后清空，避免与并发的行情派发/增删冲突（P1-19）
        with self._registry_lock:
            self._user_strategies.clear()
        loaded: list[StrategyState] = []

        for us_item in user_strategies:
            user_strategy_id: str = str(us_item.get("id") or us_item.get("_id") or "")
            if not user_strategy_id:
                continue
            # 按 service 分配的 user_strategy_id 列表过滤
            if assigned_ids and user_strategy_id not in assigned_ids:
                continue
            state: StrategyState | None = self._create_state_from_user_strategy(us_item)
            if state is not None:
                loaded.append(state)

        self.logger.info(
            "共加载 %d 个用户策略配置（%d 个策略模板）",
            len(loaded), len(self._user_strategies),
        )
        return loaded

    def _create_state_from_user_strategy(
        self, us_item: dict[str, Any]
    ) -> StrategyState | None:
        """由一条用户策略记录构建 StrategyState 并注册进运行表。

        **启动加载与运行中对账共用这一条路径**。两处各写一份的话，「实例化传哪些
        参数」「哪些字段取自服务端」「要不要加载持仓和黑名单」这些细节迟早分叉，
        而分叉的表现是「重启后才生效」「热更新后的参数与启动时不一致」这类只有
        对比两份日志才看得出来的问题。

        :param us_item: /api/user_strategy/all 返回的一条记录
        :return: 构建成功的 StrategyState；模板缺失或实例化失败时返回 None
        """
        user_strategy_id: str = str(us_item.get("id") or us_item.get("_id") or "")
        user_strategy_name: str = us_item.get("name", "")
        us_sid: str = str(us_item.get("strategy_id", ""))
        if not us_sid or not user_strategy_id:
            return None

        # 获取策略模板元数据
        template: dict[str, Any] | None = self._strategy_templates.get(us_sid)
        if template is None:
            self.logger.warning(
                "用户策略 %s 关联的 strategy_id=%s 无对应模板，跳过",
                user_strategy_id, us_sid,
            )
            return None

        # 为当前用户策略实例化独立策略实例，并传入 user_strategy_id 及参数
        instance = self._build_strategy_instance(
            template,
            user_strategy_id=user_strategy_id,
            user_strategy_name=user_strategy_name,
            user_strategy_params=us_item.get("strategy_params"),
        )
        if instance is None:
            self.logger.warning(
                "用户策略 %s 实例化策略 %s 失败，跳过",
                user_strategy_id, us_sid,
            )
            return None
        # 保留该策略模板的代表实例（供模板级选股使用）
        with self._registry_lock:
            if us_sid not in self._strategy_instances:
                self._strategy_instances[us_sid] = instance

        pool_id: str = str(us_item.get("pool_id", ""))
        db_status: str = str(us_item.get("status", "stopped"))
        # 从 UserStrategyDao 读取初始资金和最大持仓数
        # P0-6 + 2026-09-24 语义确认：`max_stock_count=0` 与 `initial_amount=0`
        # 都表示**限制买入**（不允许新建持仓或加仓），`-1` 才是不限制持仓数量。
        # 2026-09-28：字段缺失/非法时的默认值改为「不限制持仓 + 10 万初始资金」
        # （与服务端默认值一致），不再回退 4 / 0。
        max_stocks: int = self._parse_max_stock_count(us_item.get("max_stock_count"))
        raw_initial: Any = us_item.get("initial_amount")
        initial_amt: float = self._parse_initial_amount(raw_initial)
        # 「显式配置 0」与「字段缺失」必须区分：0 是限制买入的有效语义，
        # 不能被历史执行记录里的值覆盖（见 _load_execution_data）。
        # 空字符串等同于未配置，与服务端写入行为对齐。
        initial_configured: bool = raw_initial not in (None, "")

        state = StrategyState(
            strategy_id=us_sid,
            user_strategy_id=user_strategy_id,
            strategy_name=instance.strategy_name,
            class_path=instance.__class__.__module__,
            class_name=instance.__class__.__name__,
            user_strategy_name=user_strategy_name,
            instance=instance,
            pool_id=pool_id,
            status=db_status if db_status in ("running", "stopped", "paused") else "stopped",
            initial_amount=initial_amt,
            initial_amount_configured=initial_configured,
            max_stock_count=max_stocks,
        )

        # 配置级限制买入在加载期就提示一次：这类策略会持续产出信号但永不创建订单，
        # 只在「跳过买入」那一刻记 INFO 的话，运维得先翻到那条行情才看得出来。
        if max_stocks == 0 or initial_amt <= 0:
            self.logger.info(
                "[%s] 该策略配置为限制买入（max_stock_count=%d, initial_amount=%.2f，"
                "均以 0 表示限制买入），不会创建买单；卖出不受影响",
                instance.strategy_name, max_stocks, initial_amt,
            )

        # 解析股票池
        if pool_id:
            self.update_strategy_stock_pool(state, pool_id)

        # 加载执行数据（同步持仓、执行中的 initial_amount 等）
        self._load_execution_data(state)
        # 加载用户黑名单（信号/选股时过滤）
        self._load_blacklist_for_state(state)

        # 按 strategy_id 分组
        with self._registry_lock:
            if us_sid not in self._user_strategies:
                self._user_strategies[us_sid] = []
            self._user_strategies[us_sid].append(state)

        self.logger.info(
            "用户策略加载: name=%s, user_strategy_id=%s, strategy_id=%s",
            state.strategy_name, user_strategy_id, us_sid,
        )
        return state

    # ---- 配置对账（热更新）----

    def on_config_change(self, payload: Any) -> None:
        """配置变更钩子：收到服务端通知即对账一次。

        载荷只用于日志 —— 判断「该同步什么」靠重新读服务端当前状态，不靠消息里的
        scope/action。这样即使某个写入点漏发通知、或字段名对不上，后果只是延迟到
        下一次定时轮询，不会漏同步。

        :param payload: 解析后的消息体
        """
        self.reconcile_user_strategies(reason="mqtt")

    def reconcile_user_strategies(self, reason: str = "manual") -> dict[str, Any]:
        """与服务端对账用户策略配置，把变更增量应用到运行中的实例。

        覆盖四类变更：用户策略参数（热更新已实例）、股票池内容与池绑定（刷新
        `state.stock_pool` 并按需重订阅 MQTT）、用户策略的增删、执行相关字段
        （持仓上限 / 初始资金 / 启停状态）。顺带清理通知器的推送星级门槛缓存，
        让「用户偏好」这一项配置也按同一个心跳生效（见 `_reconcile_once` 第 8 步）。

        并发保护用非阻塞锁：MQTT 通知与定时轮询可能同时到，而对账里「新建实例」
        和「订阅 topic」都不幂等，宁可跳过本轮（下一轮 60 秒后还会来）。

        :param reason: 触发来源（mqtt / scheduler / manual），只用于日志
        :return: 变更摘要，供测试与排查使用
        """
        if not self._reconcile_lock.acquire(blocking=False):
            self.logger.info("已有对账在进行中，跳过本次触发（reason=%s）", reason)
            return {"skipped": "busy"}
        try:
            summary: dict[str, Any] = self._reconcile_once(reason)
            summary["reason"] = reason
            return summary
        except Exception as exc:
            self.logger.error("配置对账异常（reason=%s）: %s", reason, exc, exc_info=True)
            return {"reason": reason, "error": str(exc)}
        finally:
            self._last_reconcile_ts = time.time()
            self._reconcile_lock.release()

    def _reconcile_once(self, reason: str) -> dict[str, Any]:
        """对账主体：拉服务端状态 → 逐项 diff → 增量应用。"""
        # 1) 策略模板（新建策略要实例化，模板默认参数变化也要能被检测到）
        self._refresh_strategy_templates()

        # 2) 本实例负责的 user_strategy_ids
        assigned_ids, assign_ok = self._fetch_assigned_ids()
        if not assign_ok:
            self.logger.warning(
                "对账：分配关系查询失败，本轮只做已有策略的字段热更新，"
                "不新增也不删除（fail-closed：宁可不跑，也不能多实例同时下单）"
            )

        # 3) 用户策略全量
        try:
            user_strategies: list[dict[str, Any]] = self._user_strategy_api.list_all()
        except Exception as exc:
            self.logger.warning("对账：拉取用户策略失败，本轮跳过: %s", exc)
            return {"error": "list_all_failed"}
        if user_strategies is None:
            user_strategies = []

        desired: dict[str, dict[str, Any]] = {}
        for item in user_strategies:
            if not isinstance(item, dict):
                continue
            usid: str = str(item.get("id") or item.get("_id") or "")
            if not usid:
                continue
            # 分配查询成功时按分配过滤；失败时保留全部（只用于更新，见下方闸门）
            if assign_ok and self._service_name and usid not in assigned_ids:
                continue
            desired[usid] = item

        current: dict[str, StrategyState] = self.get_all_strategies()

        added: list[str] = []
        removed: list[str] = []
        updated: list[str] = []
        selection_refreshed: list[str] = []

        # 4) 新增（分配查询失败时不新增）
        if assign_ok:
            for usid, item in desired.items():
                if usid in current:
                    continue
                state: StrategyState | None = self._create_state_from_user_strategy(item)
                if state is None:
                    continue
                added.append(usid)
                if state.pool_id:
                    self.subscribe_pool(state.pool_id)

        # 5) 删除（同样受 assign_ok 保护）
        if assign_ok:
            for usid, state in current.items():
                if usid in desired:
                    continue
                self._drop_state(state)
                removed.append(usid)

        # 6) 更新已存在的策略
        for usid, state in current.items():
            item = desired.get(usid)
            if item is None:
                continue
            changed, selection_affected = self._sync_state_from_us_item(state, item)
            if not changed:
                continue
            updated.append(usid)
            if selection_affected and self._maybe_refresh_selection(state, reason):
                selection_refreshed.append(usid)

        # 7) 没有任何策略再引用的股票池，退订其行情 topic
        self._prune_pool_subscriptions()

        # 8) 推送/下单星级门槛也在用户偏好里，而偏好不在对账范围内。
        #    对账是「界面改了配置要跟随」的唯一心跳，顺手让偏好缓存过期，
        #    把「改了门槛最多等 300 秒（通知器的 TTL）」缩到「最多 60 秒」。
        #    只是清缓存，下一次推送/下单才会重新拉，不产生周期性请求。
        try:
            self._notifier.invalidate_preferences_cache()
        except Exception as exc:
            self.logger.debug("清理推送星级门槛缓存失败: %s", exc)

        summary: dict[str, Any] = {
            "added": added,
            "removed": removed,
            "updated": updated,
            "selection_refreshed": selection_refreshed,
            "assign_ok": assign_ok,
        }
        if added or removed or updated:
            self.logger.info(
                "配置对账完成（reason=%s）: 新增 %d, 移除 %d, 更新 %d（补跑选股 %d）: %s",
                reason, len(added), len(removed), len(updated),
                len(selection_refreshed), summary,
            )
        else:
            self.logger.debug("配置对账完成（reason=%s）: 无变更", reason)
        return summary

    def _refresh_strategy_templates(self) -> None:
        """刷新策略模板缓存。

        只做「增」和「改」，不做「删」：还在运行的 state 引用着旧模板条目，
        删掉只会让后续判断（如模板默认参数）无从下手，而模板被删后服务端
        也不会再把它分配给任何用户策略。
        """
        try:
            db_strategies: list[dict[str, Any]] = self._strategy_api.list()
        except Exception as exc:
            self.logger.warning("对账：拉取策略模板失败，沿用本地缓存: %s", exc)
            return
        if not db_strategies:
            return
        fresh: dict[str, dict[str, Any]] = {}
        for s in db_strategies:
            if not isinstance(s, dict):
                continue
            sid: str = str(s.get("id") or s.get("_id") or "")
            if sid:
                fresh[sid] = s
        if fresh:
            self._strategy_templates.update(fresh)

    def _fetch_assigned_ids(self) -> tuple[set[str], bool]:
        """查询分配给本实例的 user_strategy_ids。

        与 `_load_assigned_user_strategy_ids` 的差别只在返回值：这里必须区分
        **「查询失败」与「确实没有分配」** —— 前者的正确反应是什么都不动，后者要把
        本地多余的策略移除。启动路径上两者都走「空转」，所以那边不需要这个区分。

        :return: (分配集合, 查询结果是否可信)。`service_name` 为空（兼容模式）时
            返回 `(set(), True)`：兼容模式的语义就是「加载全部」，不按分配过滤。
        """
        if not self._service_name:
            return set(), True
        got_response: bool = False
        runners: list[tuple[str, Any]] = [
            (
                "/service/{name}",
                lambda: self._workflow_service_api.list_by_service(self._service_name),
            ),
            (
                "/list?service_name=",
                lambda: self._workflow_service_api.list(service_name=self._service_name),
            ),
        ]
        for label, fetch in runners:
            try:
                records: list[dict[str, Any]] = fetch()
                # 无异常即视为「服务端给了答复」：哪怕是空列表，也是「没分配给本实例」
                got_response = True
                ids: set[str] = self._collect_user_strategy_ids(records)
                if ids:
                    return ids, True
            except Exception as exc:
                self.logger.warning("对账：查询分配关系 %s 失败: %s", label, exc)
        return set(), got_response

    def _drop_state(self, state: StrategyState) -> None:
        """把一条用户策略从运行表移除（服务端已删除或已改分配给别的实例）。"""
        sid: str = state.strategy_id
        usid: str = state.user_strategy_id
        with self._registry_lock:
            states: list[StrategyState] | None = self._user_strategies.get(sid)
            if states:
                self._user_strategies[sid] = [
                    s for s in states if s.user_strategy_id != usid
                ]
                if not self._user_strategies[sid]:
                    del self._user_strategies[sid]
                    self._strategy_instances.pop(sid, None)
        # 清掉该用户的缓存快照：策略删了又建（同名同 ID 复用）时，旧选股/打分
        # 结果会让新策略第一天就命中过期数据
        try:
            self._cache.clear_user(usid)
        except Exception as exc:
            self.logger.debug("清理已删除策略 %s 的缓存失败: %s", usid, exc)
        self.logger.info(
            "配置对账：用户策略已移除 name=%s, user_strategy_id=%s",
            state.user_strategy_name or state.strategy_name, usid,
        )

    def _sync_state_from_us_item(
        self, state: StrategyState, item: dict[str, Any]
    ) -> tuple[bool, bool]:
        """把服务端记录的最新字段应用到本地 state。

        :param state: 本地运行时状态
        :param item: /api/user_strategy/all 里的对应记录
        :return: (是否有任何字段变化, 变化是否会影响「当日选股结果」)
        """
        changed: list[str] = []
        selection_affected: bool = False

        # 1) 用户策略名（交易信号推送的 recipient 用它）
        new_name: str = str(item.get("name", "") or "")
        if new_name and new_name != state.user_strategy_name:
            changed.append(f"name:{state.user_strategy_name}->{new_name}")
            state.user_strategy_name = new_name
            if state.instance is not None:
                state.instance.user_strategy_name = new_name

        # 2) 启停状态。工作流未运行时不接管：on_stop 会把本地状态改成 paused 但
        #    不写服务端，此时若按服务端值改回去，界面显示运行中、实际已停。
        new_status: str = str(item.get("status", "") or "")
        if (
            self._running
            and new_status in ("running", "stopped", "paused")
            and new_status != state.status
        ):
            changed.append(f"status:{state.status}->{new_status}")
            state.status = new_status
            # 由停到跑：当日选股是启动时就该有的输入，但热启动时可能压根没选过
            if new_status == "running":
                selection_affected = True

        # 3) 持仓上限
        new_max: int = self._parse_max_stock_count(item.get("max_stock_count"))
        if new_max != state.max_stock_count:
            changed.append(f"max_stock_count:{state.max_stock_count}->{new_max}")
            state.max_stock_count = new_max

        # 4) 初始资金。`initial_amount_configured` 表示「界面上显式配了值（0 也算）」，
        #    它决定 `_load_execution_data` 能否用执行记录里的金额覆盖本配置。
        raw_initial: Any = item.get("initial_amount")
        new_initial: float = self._parse_initial_amount(raw_initial)
        new_configured: bool = raw_initial not in (None, "")
        if new_configured != state.initial_amount_configured or (
            new_configured and new_initial != state.initial_amount
        ):
            changed.append(
                f"initial_amount:{state.initial_amount}->{new_initial}"
                f"(configured={new_configured})"
            )
            state.initial_amount = new_initial
            state.initial_amount_configured = new_configured
            state.available_cash = self._calc_available_cash(state)

        # 5) 策略参数（用户策略参数 / 模板默认参数）
        if self._sync_params_if_changed(state, item):
            changed.append("strategy_params")
            selection_affected = True

        # 6) 股票池绑定与池内容
        pool_changed = self._sync_pool_if_changed(state, item)
        if pool_changed:
            changed.append("stock_pool")
            selection_affected = True

        if changed:
            self.logger.info(
                "[%s] 配置热更新 user_strategy_id=%s: %s",
                state.user_strategy_name or state.strategy_name,
                state.user_strategy_id, ", ".join(changed),
            )
        return bool(changed), selection_affected

    def _sync_params_if_changed(
        self, state: StrategyState, item: dict[str, Any]
    ) -> bool:
        """参数有变化时热更新策略实例；返回是否发生了更新。

        判断「有没有变」用**解析后的结果**而不是原始字典：`strategy_params` 的
        三级回退是「择一」，界面上清空用户参数会让生效值整体落到模板默认值上，
        此时原始字典变了、生效参数却没变（或反过来），只有比最终参数才对得上。
        """
        instance: BaseStrategy | None = state.instance
        if instance is None:
            return False

        template: dict[str, Any] = self._strategy_templates.get(state.strategy_id, {})
        default_params: Any = template.get("default_params")
        user_params: Any = item.get("strategy_params")
        if user_params is None:
            user_params = {}

        new_params: dict[str, Any] = self._resolve_params_for_compare(
            instance, user_params, default_params
        )
        if new_params == (instance.strategy_params or {}):
            return False

        self.logger.info(
            "[%s] 检测到策略参数变化，热更新 user_strategy_id=%s: %s -> %s",
            state.user_strategy_name or state.strategy_name,
            state.user_strategy_id, instance.strategy_params, new_params,
        )
        try:
            instance.reload_params(user_params, default_params)
        except Exception as exc:
            self.logger.error(
                "[%s] 热更新策略参数失败: %s",
                state.user_strategy_name or state.strategy_name, exc, exc_info=True,
            )
            return False
        return True

    @staticmethod
    def _resolve_params_for_compare(
        instance: BaseStrategy, user_params: Any, default_params: Any
    ) -> dict[str, Any]:
        """按 `_apply_resolved_params` 的同一套优先级算出「生效参数」。

        与 `BaseStrategy._apply_resolved_params` 的选择顺序保持一致：用户策略
        参数非空 → 模板默认参数非空 → cfg/strategy.yaml。这里只做预演（不写实例），
        用于判断参数是否真的需要重载。
        """
        raw: Any = None
        if isinstance(user_params, dict) and user_params:
            raw = user_params
        elif isinstance(default_params, dict) and default_params:
            raw = default_params
        else:
            try:
                raw = instance._load_yaml_params()
            except Exception:
                raw = {}
        try:
            normalized: Any = BaseStrategy._normalize_params(raw)
        except Exception:
            normalized = raw
        return normalized if isinstance(normalized, dict) else {}

    def _sync_pool_if_changed(self, state: StrategyState, item: dict[str, Any]) -> bool:
        """同步股票池绑定与池内股票；返回是否有变化。

        两种情况都要处理：

        - **换了池**（pool_id 变化）：旧池的行情会被 `handle_bar` 里的
          `topic_pool_id != state.pool_id` 挡掉，新池又没订阅过，必须重订阅；
        - **同一个池里增删了股票**：池内容原先只在盘前定时任务里刷新一次，
          盘中加进去的股票收不到行情（`topic_stock_code not in state.stock_pool`）。
        """
        new_pool_id: str = str(item.get("pool_id", "") or "")
        if new_pool_id == state.pool_id:
            if not state.pool_id:
                return False
            before: list[str] = list(state.stock_pool)
            self._reload_stock_pool(state)
            if before == state.stock_pool:
                return False
            before_set: set[str] = set(before)
            now_set: set[str] = set(state.stock_pool)
            self.logger.info(
                "[%s] 股票池内容变化: 新增 %s, 移除 %s",
                state.user_strategy_name or state.strategy_name,
                sorted(now_set - before_set), sorted(before_set - now_set),
            )
            return True

        old_pool_id: str = state.pool_id
        state.pool_id = new_pool_id
        if new_pool_id:
            self.subscribe_pool(new_pool_id)
            self.update_strategy_stock_pool(state, new_pool_id)
        else:
            state.stock_pool = []
            self.logger.info(
                "[%s] 已解除股票池绑定，股票列表清空",
                state.user_strategy_name or state.strategy_name,
            )
        self.logger.info(
            "[%s] 股票池绑定变化: %s -> %s",
            state.user_strategy_name or state.strategy_name, old_pool_id, new_pool_id,
        )
        return True

    def _prune_pool_subscriptions(self) -> None:
        """退订已无任何策略引用的股票池行情。

        用户换池后旧池不会再产生有效行情（`handle_bar` 按 pool_id 过滤），
        继续订阅只是白白多收一路消息。
        """
        active: set[str] = {
            s.pool_id for s in self.get_all_strategies().values() if s.pool_id
        }
        for pool_id in self.subscribed_pool_ids:
            if pool_id not in active:
                self.unsubscribe_pool(pool_id)

    def _maybe_refresh_selection(self, state: StrategyState, reason: str) -> bool:
        """配置变更后补跑当日选股（仅盘中、仅依赖选股的策略）。

        为什么需要：策略的买入判定读的是「当日选股结果」（基类
        `handle_minute_bar` 里 `code in select_stocks`）。盘中往池里加了股票、
        或把策略从停止改成运行，当日选股结果还是变更前那一份，那只股票要等到
        第二天盘前才会被选进来 —— 表现就是「池子加了票，当天一直不买」。

        三条限制：只在交易时段跑（其余时间下一次盘前任务会重选）、只对
        `requires_daily_selection` 的策略跑（补跑要全池取日 K，最贵的一步）、
        同一策略 `SELECTION_REFRESH_MIN_INTERVAL_SECONDS` 内不重复跑。

        :param state: 目标策略状态
        :param reason: 触发来源，用于日志
        :return: 是否真的执行了补跑
        """
        if state.instance is None or state.status != "running":
            return False
        if not getattr(state.instance, "requires_daily_selection", True):
            self.logger.info(
                "[%s] 该策略不依赖当日选股，跳过补跑 (reason=%s)",
                state.user_strategy_name or state.strategy_name, reason,
            )
            return False
        if not self._is_trading_hours():
            return False
        now: float = time.time()
        last: float = self._selection_refresh_ts.get(state.user_strategy_id, 0.0)
        if now - last < self.SELECTION_REFRESH_MIN_INTERVAL_SECONDS:
            self.logger.info(
                "[%s] 距上次补跑选股不足 %.0f 秒，本次跳过 (reason=%s)",
                state.user_strategy_name or state.strategy_name,
                self.SELECTION_REFRESH_MIN_INTERVAL_SECONDS, reason,
            )
            return False
        trade_date: str = datetime.now().strftime("%Y-%m-%d")
        if not self.xshg.is_session(trade_date):
            return False
        self._selection_refresh_ts[state.user_strategy_id] = now
        self.logger.info(
            "[%s] 配置变更后补跑当日选股 (user_strategy_id=%s, reason=%s)",
            state.user_strategy_name or state.strategy_name,
            state.user_strategy_id, reason,
        )
        # reset_cache=False：不清当日分钟缓存与区间最高价 —— 那是行情数据，
        # 与配置无关，清掉等于让当天的量能/VWAP/回撤判定缺数据。
        self._run_daily_selection_for_state(
            state, self._hot_industry_codes(), trade_date, reset_cache=False
        )
        return True

    def _hot_industry_codes(self) -> list[str]:
        """带缓存的热门行业代码（补跑选股用）。"""
        cache: tuple[float, list[str]] | None = self._hot_industry_cache
        if (
            cache is not None
            and time.time() - cache[0] < self.HOT_INDUSTRY_CACHE_TTL_SECONDS
        ):
            return cache[1]
        codes: list[str] = self._collect_hot_industry_codes()
        self._hot_industry_cache = (time.time(), codes)
        return codes

    def _is_trading_hours(self, now: datetime | None = None) -> bool:
        """当前是否处于 A 股连续竞价时段（含集合竞价，9:15–15:05）。

        :param now: 用于测试注入的时间，默认取当前时间
        :return: 是否在交易时段（非交易日一律 False）
        """
        moment: datetime = now or datetime.now()
        if not self.xshg.is_session(moment.strftime("%Y-%m-%d")):
            return False
        minutes: int = moment.hour * 60 + moment.minute
        return (9 * 60 + 15) <= minutes <= (15 * 60 + 5)

    def _reconcile_job(self) -> None:
        """定时对账任务（兜底通道）。

        非交易时段按更长的间隔跑：那时候行情没有变化，对账只用于「第二天开盘前
        把界面上的改动带上」，没必要每分钟发一轮 HTTP。

        节流阈值取周期的 `RECONCILE_THROTTLE_RATIO`（0.9）倍，**不能取满**：
        `_last_reconcile_ts` 记在 `reconcile_user_strategies` 的 finally 里，即
        单轮对账的**结束**时刻，而单轮要跑 0.3~0.4 秒（3 次基础 HTTP，行情好的
        时候更久）。阈值取满 60 秒时，下一轮触发距离上次结束恰好是
        `60 − 0.4 = 59.6 < 60`，于是稳定 return —— 实际变成"一轮跑一轮跳"，
        周期翻倍到 120 秒（非交易时段 600 秒），而 apscheduler 两轮都报
        executed successfully，光看调度器日志看不出被跳过。留 10% 余量后，
        只要单轮耗时不超过周期的 10%（6 秒）就能每轮都跑。
        """
        interval: float = (
            self.RECONCILE_INTERVAL_SECONDS
            if self._is_trading_hours()
            else self.RECONCILE_INTERVAL_OFFHOURS_SECONDS
        )
        elapsed: float = time.time() - self._last_reconcile_ts
        throttled_at: float = interval * self.RECONCILE_THROTTLE_RATIO
        if elapsed < throttled_at:
            # 用 INFO 而不是 DEBUG：root logger 是 INFO，DEBUG 记录压根不产生
            # （console handler 虽是 DEBUG，但被 root 的级别拦在前面），
            # 被跳过就只剩 apscheduler 那条耗时 0ms 的 executed 可反推。
            # 正常情况每轮都跑，这条只在单轮耗时超过周期 10% 时才会出现。
            self.logger.info(
                "配置对账跳过（距上次结束 %.1fs，不足节流阈值 %.1fs）",
                elapsed, throttled_at,
            )
            return
        self.reconcile_user_strategies(reason="scheduler")

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
        for s in self._snapshot_states():
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
        user_strategy_name: str = user_strategy.name
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

        # 为当前用户策略实例化独立策略实例（传入 user_strategy_id 与用户级参数）
        try:
            template: dict[str, Any] = self._strategy_api.get_by_id(strategy_id)
        except Exception as exc:
            self.logger.error("获取策略模板失败: %s", exc)
            return False
        if not template:
            self.logger.error("策略模板 %s 不存在", strategy_id)
            return False
        instance = self._build_strategy_instance(
            template,
            user_strategy_id=user_strategy_id,
            user_strategy_name=user_strategy_name,
            user_strategy_params=user_strategy.strategy_params,
        )
        if instance is None:
            return False
        # 保留代表实例（供模板级选股使用）
        with self._registry_lock:
            if strategy_id not in self._strategy_instances:
                self._strategy_instances[strategy_id] = instance

        state = StrategyState(
            strategy_id=strategy_id,
            user_strategy_id=user_strategy_id,
            strategy_name=instance.strategy_name,
            class_path=instance.__class__.__module__,
            class_name=instance.__class__.__name__,
            user_strategy_name=user_strategy_name,
            instance=instance,
            pool_id=pool_id,
            status=status if status in ("running", "stopped", "paused") else "stopped",
            initial_amount=user_strategy.initial_amount,
            initial_amount_configured=user_strategy.initial_amount_configured,
            # 与 _load_user_strategy 走同一套解析：`0` = 限制买入，`-1` = 不限制持仓数量
            max_stock_count=self._parse_max_stock_count(user_strategy.max_stock_count),
        )

        if pool_id:
            self.update_strategy_stock_pool(state, pool_id)

        with self._registry_lock:
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

        # 从 _user_strategies 中移除（加锁，避免行情线程正在迭代）
        sid: str = state.strategy_id
        with self._registry_lock:
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
        for s in self._snapshot_states():
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
        with self._registry_lock:
            states: list[StrategyState] = list(self._user_strategies.get(strategy_id, []))
        return any(us.status == "running" for us in states)

    # ---- 行情回调 ----

    def handle_bar(self, topic: str, payload: Any) -> None:
        """处理分钟行情消息。

        遍历所有运行中的用户策略 → 用各自策略实例对每只股票调用 _process_bar。
        调度前按用户过滤黑名单股票。

        :param topic: MQTT topic
        :param payload: 分钟行情数据列表 list[dict]，每项含 code 字段
        """
        if not isinstance(payload, list) and not isinstance(payload, dict):
            self.logger.warning("handle_bar: payload 格式异常，期望 list or dict，实际 %s", type(payload))
            return

        topic_pool_id, topic_stock_code = MqttTopic.decode_stock_minute_topic(topic)
        if not isinstance(payload, list):
            payload = [payload]

        # 1) 按 code 去重，同一批内同一只股票只保留最后一根（最新）bar，
        #    避免整批行情（可能几百只）展开成上千个任务后仍在计算过期数据
        latest_bars: dict[str, dict[str, Any]] = {}
        for bar_data in payload:
            if not isinstance(bar_data, dict):
                continue
            code: str = str(bar_data.get("code", ""))
            if code:
                latest_bars[code] = bar_data

        running_count: int = 0
        blacklist_skip_count: int = 0
        for code, bar_data in latest_bars.items():
            # 2) 行情缓存由 workflow **单点写入**（每 code 只写一次）：
            #    原来由每个策略实例在自己的 _before_minute_route 里各写一次，
            #    N 个用户会把同一根 bar 写 N 遍，导致量能/VWAP/趋势因子看到被放大的数据。
            self._cache.push_minute_bar(code, bar_data)

            # 3) 派发到各运行中的用户策略（取快照迭代，避免迭代期间被增删）
            for state in self._snapshot_states():
                if state.status != "running" or state.instance is None:
                    continue
                # 黑名单过滤：该用户已将 code 列入黑名单则跳过
                if self._is_blacklisted(state, code):
                    blacklist_skip_count += 1
                    continue
                if topic_pool_id != "" and topic_pool_id != state.pool_id:
                    continue
                if topic_stock_code != "" and topic_stock_code not in state.stock_pool:
                    continue
                running_count += 1
                # P2-23：不再直接 submit，改为放入合并队列（每 code 只保留最新 bar）
                self._enqueue_bar(state, code, bar_data)

        if running_count > 0 or blacklist_skip_count > 0:
            self.logger.debug(
                "handle_bar: %d stocks, dispatched %d tasks, skipped %d blacklisted",
                len(latest_bars), running_count, blacklist_skip_count,
            )

    def handle_tick(self, topic: str, payload: Any) -> None:
        """处理实时行情消息。

        遍历所有运行中的用户策略 → 用各自策略实例对每只股票调用 _process_tick。
        调度前按用户过滤黑名单股票。

        **默认不派发**：由 cfg/stock.cfg [workflow] enable_handle_tick 控制。
        内置策略的 check_tick_buy / check_tick_sell 均未实现（恒返回 False），
        无差别派发只会用空转任务占满线程池并延迟真实的分钟信号。

        :param topic: MQTT topic
        :param payload: 实时行情数据列表 list[dict]，每项含 code 字段
        """
        if not self._enable_handle_tick:
            return
        if not isinstance(payload, list) and not isinstance(payload, dict):
            self.logger.warning("handle_tick: payload 格式异常，期望 list or dict，实际 %s", type(payload))
            return

        topic_pool_id, topic_stock_code = MqttTopic.decode_stock_realtime_topic(topic)
        running_count: int = 0
        blacklist_skip_count: int = 0
        if not isinstance(payload, list):
            payload = [payload]

        # 与 handle_bar 一致：按 code 去重，只保留最新一笔
        latest_ticks: dict[str, dict[str, Any]] = {}
        for tick_data in payload:
            if not isinstance(tick_data, dict):
                continue
            code: str = str(tick_data.get("code", ""))
            if code:
                latest_ticks[code] = tick_data

        for code, tick_data in latest_ticks.items():
            for state in self._snapshot_states():
                if state.status != "running" or state.instance is None:
                    continue
                # 黑名单过滤：该用户已将 code 列入黑名单则跳过
                if self._is_blacklisted(state, code):
                    blacklist_skip_count += 1
                    continue
                if topic_pool_id != "" and topic_pool_id != state.pool_id:
                    continue
                if topic_stock_code != "" and topic_stock_code not in state.stock_pool:
                    continue
                running_count += 1
                self._enqueue_bar(state, code, tick_data, is_tick=True)

        if running_count > 0 or blacklist_skip_count > 0:
            self.logger.debug(
                "handle_tick: %d stocks, dispatched %d tasks, skipped %d blacklisted",
                len(latest_ticks), running_count, blacklist_skip_count,
            )

    # ---- 策略执行核心逻辑 ----

    def _process_bar(
        self, state: StrategyState, code: str, bar_data: dict[str, Any]
    ) -> None:
        """在线程池中执行策略的分钟级检查逻辑。

        1. 调用 state.instance.handle_minute_bar() 获取买卖信号
        2. 记录信号通过 MQTT 发送
        3. 按信号为当前用户策略创建订单

        **设计约定（信号优先）**：本方法调用策略时有意传入空持仓 `{}`。
        系统定位是「尽可能产出买卖信号」的辅助决策工具：信号先行，订单与持仓
        都由信号派生，最终由交易员判断是否真的买卖。因此策略在实时链路下
        不依赖真实持仓运行，持仓状态只在后续下单环节（_load_positions）才参与判断。
        回测链路（`backtest_engine._run_minute_for_stock`）已按 **X1 决策**对齐同一口径，
        也恒传空持仓 —— 两侧信号集合可比，回测指标可代表实盘。

        并发安全：每个 StrategyState 持有独立锁（_lock），
        确保同一 state 在不同 code 并发处理时不产生竞态条件。

        :param state: 用户策略运行时状态
        :param code: 股票代码
        :param bar_data: 分钟行情数据
        """
        instance: BaseStrategy | None = state.instance
        if instance is None:
            return
        try:

            # 0. 注入账户当前回撤：策略卖出判定里有一条「账户回撤达阈值」分项
            #    （risk.drawdown_score）。回撤是账户级量，策略只看得到单只股票的行情，
            #    必须在这里算好喂进去；未启用时值为 0，不影响原有卖出行为。
            self._refresh_account_drawdown(state)

            # 1. 调用策略的 handle_minute_bar 获取信号
            # 设计约定：此处有意传空持仓 {} —— 实时链路以「尽可能产出买卖信号」为目标，
            # 不依赖持仓状态，策略侧以「无持仓」语义运行（卖出基准由策略按最近 2 个
            # 交易日兜底），订单与持仓都产生在信号之后，最终由交易员判断，系统只做辅助建议。
            # 回测链路（backtest_engine）已按 X1 对齐，同样恒传空持仓。
            matched, extra = instance.handle_minute_bar(code, {}, bar_data)
            # matched = True
            # extra["signal"] = "BUY"
            if not matched:
                # 未匹配：记录原因日志
                reason: str = extra.get("reason", "")
                if reason:
                    self.logger.info(
                        "code=%s 未触发信号, reason:%s", code, reason
                    )
                return  # 条件不符合直接返回

            # 2. **X2**：股票池 / 黑名单过滤前移——不在用户可见范围内的信号
            #    既不写库也不推送 MQTT（原实现把过滤放在信号记录之后，
            #    导致「不在该用户池内的信号也会被记录并推送」）。
            if not self._is_signal_in_user_scope(state, code):
                return

            # 3. 记录交易信号（含信号风暴过滤与星级门槛，未通过则跳过订单创建）
            if not self._save_trade_signal(
                state.strategy_id, state.user_strategy_id, code, extra,
                strategy_name=state.strategy_name,
                user_strategy_name=state.user_strategy_name,
            ):
                return

            signal_type: str = extra.get("signal", "")

            # P1-18：持仓拉取（网络 IO）放在 `state._lock` **之外**，避免持锁做 HTTP。
            positions: dict[str, dict[str, Any]] | None = self._load_positions(state)
            if positions is None:
                self.logger.warning(
                    "[%s] code=%s 持仓信息获取失败，跳过本次下单（fail-closed）",
                    state.strategy_name, code,
                )
                return

            # P2-24：锁内只做判定 + 占位（纯内存，毫秒级），
            # 订单提交（HTTP）在锁外执行，避免一个慢请求阻塞该用户所有股票。
            reserved: tuple[OrderDao, float] | None = None
            with state._lock:
                # 下面每个「跳过」分支都必须记 INFO：信号已写库/MQTT 已推送，但订单
                # 没创建 —— 这是「信号成功却无持仓」的唯一解释来源。原来记 DEBUG，
                # 而 cfg/log.yaml 的 info.log 是 INFO 级，等于对运维完全不可见
                # （2026-09-24 线上就是这个现象：只能看到信号，看不到为什么没下单）。
                if signal_type == "BUY":
                    if code in positions:
                        self.logger.info(
                            "[%s] code=%s 已有持仓，跳过买入",
                            state.strategy_name, code,
                        )
                    elif code in state.pending_codes:
                        self.logger.info(
                            "[%s] code=%s 已有在途买入订单，跳过",
                            state.strategy_name, code,
                        )
                    else:
                        # 配置级限制（max_stock_count=0 / initial_amount=0）与持仓数
                        # 达上限都在这里统一给出原因，记 INFO 供运维直接定位
                        block_reason: str = self._buy_block_reason(state, positions)
                        if block_reason:
                            self.logger.info(
                                "[%s] code=%s 跳过买入：%s",
                                state.strategy_name, code, block_reason,
                            )
                        else:
                            reserved = self._reserve_buy_order(state, code, extra)
                elif signal_type == "SELL":
                    if code not in positions:
                        self.logger.info(
                            "[%s] code=%s 无持仓，跳过卖出",
                            state.strategy_name, code,
                        )
                    elif code in state.pending_codes:
                        self.logger.info(
                            "[%s] code=%s 已有在途卖出订单，跳过",
                            state.strategy_name, code,
                        )
                    else:
                        pos = positions[code]
                        # T+1 提示（设计约定 D-2：仅提示不拦截，订单仍创建供交易员参考）
                        self._log_t1_advisory(
                            state, code, pos, bar_data.get("create_time", "")
                        )
                        reserved = self._reserve_sell_order(
                            state, code, float(pos.get("cost_price", 0)), extra,
                            stock_time=bar_data.get("create_time", ""),
                        )
                else:
                    self.logger.warning(
                        "[%s] code=%s 信号类型未知，跳过下单: %r",
                        state.strategy_name, code, signal_type,
                    )

            # 锁外提交订单
            if reserved is not None:
                order, reserved_amount = reserved
                self._commit_reserved_order(state, code, order, reserved_amount)

        except Exception as exc:
            self.logger.error(
                "[%s] _process_bar(%s) 异常: %s",
                state.strategy_name, code, exc, exc_info=True,
            )

    def _process_tick(
        self, state: StrategyState, code: str, tick_data: dict[str, Any]
    ) -> None:
        """在线程池中执行策略的实时级检查逻辑（同 _process_bar 模式）。

        并发安全：每个 StrategyState 持有独立锁（_lock），
        确保同一 state 在不同 code 并发处理时不产生竞态条件。

        :param state: 用户策略运行时状态
        :param code: 股票代码
        :param tick_data: 实时行情数据
        """
        instance: BaseStrategy | None = state.instance
        if instance is None:
            return
        try:
            # 同 _process_bar：有意传空持仓 {}，实时链路以产出信号优先，详见 _process_bar 注释。
            matched, extra = instance.handle_tick_bar(code, {}, tick_data)

            if not matched:
                # 未匹配：记录原因日志
                reason: str = extra.get("reason", "")
                if reason:
                    self.logger.debug(
                        "code=%s 未触发信号, reason:%s", code, reason
                    )
                return  # 条件不符合直接返回

            # **X2**：股票池 / 黑名单过滤前移，与 _process_bar 保持一致
            if not self._is_signal_in_user_scope(state, code):
                return

            # 记录交易信号（含信号风暴过滤与星级门槛，未通过则跳过订单创建）
            if not self._save_trade_signal(
                state.strategy_id, state.user_strategy_id, code, extra,
                strategy_name=state.strategy_name,
                user_strategy_name=state.user_strategy_name,
            ):
                return

            signal_type: str = extra.get("signal", "")

            # P1-18：持仓拉取（网络 IO）放在 `state._lock` 之外
            positions: dict[str, dict[str, Any]] | None = self._load_positions(state)
            if positions is None:
                self.logger.warning(
                    "[%s] code=%s 持仓信息获取失败，跳过本次下单（fail-closed）",
                    state.strategy_name, code,
                )
                return

            # P2-24：锁内判定 + 占位，锁外提交
            reserved: tuple[OrderDao, float] | None = None
            # 分支结构与 _process_bar 完全对齐（含每个「跳过」的 INFO 原因日志）：
            # 信号已写库/已推送却没有订单时，必须能从日志看到是哪一条规则拦下的。
            with state._lock:
                if signal_type == "BUY":
                    if code in positions:
                        self.logger.info(
                            "[%s] code=%s 已有持仓，跳过买入", state.strategy_name, code,
                        )
                    elif code in state.pending_codes:
                        self.logger.info(
                            "[%s] code=%s 已有在途买入订单，跳过", state.strategy_name, code,
                        )
                    else:
                        # 配置级限制（max_stock_count=0 / initial_amount=0）与持仓数
                        # 达上限都在这里统一给出原因，记 INFO 供运维直接定位
                        block_reason: str = self._buy_block_reason(state, positions)
                        if block_reason:
                            self.logger.info(
                                "[%s] code=%s 跳过买入：%s",
                                state.strategy_name, code, block_reason,
                            )
                        else:
                            reserved = self._reserve_buy_order(state, code, extra)
                elif signal_type == "SELL":
                    if code not in positions:
                        self.logger.info(
                            "[%s] code=%s 无持仓，跳过卖出", state.strategy_name, code,
                        )
                    elif code in state.pending_codes:
                        self.logger.info(
                            "[%s] code=%s 已有在途卖出订单，跳过", state.strategy_name, code,
                        )
                    else:
                        pos = positions[code]
                        # 与 _process_bar 保持一致：T+1 仅提示不拦截（设计约定 D-2）
                        self._log_t1_advisory(
                            state, code, pos, tick_data.get("create_time", "")
                        )
                        reserved = self._reserve_sell_order(
                            state, code, float(pos.get("cost_price", 0)), extra,
                            stock_time=tick_data.get("create_time", ""),
                        )
                else:
                    self.logger.warning(
                        "[%s] code=%s 信号类型未知，跳过下单: %r",
                        state.strategy_name, code, signal_type,
                    )

            if reserved is not None:
                order, reserved_amount = reserved
                self._commit_reserved_order(state, code, order, reserved_amount)

        except Exception as exc:
            self.logger.error(
                "[%s] _process_tick(%s) 异常: %s",
                state.strategy_name, code, exc, exc_info=True,
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
        self,
        strategy_id: str,
        user_strategy_id: str,
        code: str,
        extra: dict[str, Any],
        strategy_name: str = "",
        user_strategy_name: str = "",
    ) -> bool:
        """保存交易信号到 /api/trade_signal/add，并推送 MQTT / 通知。

        卖出时通过 /api/trade_signal/latest 查询最近一次买入记录，计算 profit_rate。
        信号风暴过滤：连续相同方向信号若 时间间隔 < time_gap_minutes
        或 价格变化 < price_change_threshold 则抑制。

        **P1-15**：信号记录中不再写 `profit_amount`。
        原因是此处只能拿到「每股价差」（`price - buy_price`），把每股差价当作
        「收益金额」写库属于单位错配；真正的收益金额需要持仓数量，只有
        订单（OrderDao.profit_amount）与持仓（positions[].profit_amount）
        才能正确计算，TradeManager 已在卖出时算好。

        **P1-21**：`strategy_name` 由调用方（`StrategyState.strategy_name`）传入，
        不再每个信号都发一次 `/api/strategy/get`。仅当调用方未传时做一次兜底查询
        以兼容旧调用点。

        **通知用 `user_strategy_name`（2026-09-28）**：`trade notify` 的 recipient
        与 MQTT 载荷都改用用户策略名（`UserStrategyDao.name`）。同一个模板下用户
        可能建多条策略，用策略类名（如「波段交易策略」）无法区分是哪一条发出的信号。
        `strategy_name`（模板名）保留在载荷里做兼容，但**不再作为 recipient 的回退值**：
        未传时兜底查一次用户策略名，查不到则 recipient 为空（通知器显示 default）。

        `extra` 里的 `score` / `star` 会一并写入信号记录（服务端
        `TradeSignalCreateRequest` 已有这两个字段，此前本端一直没传，恒为 0）。

        **星级门槛（2026-09-30）**：用户偏好 `push_buy_min_star` /
        `push_sell_min_star` 决定「几星以上才算有效信号」（0 = 不限，比较含边界）。
        未达门槛的信号只写库，不发 MQTT、不推送、不创建订单，并返回 False。
        返回 False 的语义因此是「信号已写库但未通过过滤」——调用方据此跳过下单，
        与信号风暴过滤共用同一条退出路径（理由都记 INFO，便于从日志反推
        「有信号无订单」的原因）。

        :param strategy_id: 策略模板 ID
        :param user_strategy_id: 用户策略关联 ID
        :param code: 股票代码
        :param extra: handle_minute_bar 返回的详细信息（含 signal/current_price 等）
        :param strategy_name: 策略名称（来自 StrategyState，避免逐信号 HTTP 查询）
        :param user_strategy_name: 用户策略名称（来自 StrategyState，用于通知与载荷）
        :return: True 表示信号已保存且通过过滤（调用方可以下单）；
            False 表示信号被抑制（风暴/星级）或保存失败，调用方不应创建订单
        """
        signal_type: str = extra.get("signal", "")
        price: float = float(extra.get("current_price", extra.get("price", 0)))
        action: str = "买入" if signal_type == "BUY" else "卖出"
        profit_rate: float = 0.0
        # 打分与星级：策略在 extra 里给出（10 分制打分、0~5 星），缺失时为 0
        try:
            score: float = float(extra.get("score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        try:
            star: float = float(extra.get("star", 0.0) or 0.0)
        except (TypeError, ValueError):
            star = 0.0
        # 推送/下单星级门槛：用户偏好 push_buy_min_star / push_sell_min_star
        # （0 = 不限，比较含边界）。未达门槛的信号**仍然写库**——界面按星级筛
        # 历史信号要靠它——但三个出口共用这一个判定：不发 MQTT 实时流、
        # 不推送企业微信/微信、也不创建订单（返回 False 让调用方跳过下单）。
        # 三处分别判一次必然分叉，所以这里只算一次往下传。
        min_star: float = self._notifier.get_push_min_star(user_strategy_id, action)
        star_passed: bool = TradeNotifier.star_passed(star, min_star)
        reason: str = extra.get("reason", "")
        if not isinstance(reason, str):
            # 防御（2026-09-15 线上 422 根因）：历史 bug 曾把 reason 写成单元素
            # tuple（`extra["reason"] = (f"..."),`，多了一个逗号），reason 会污染
            # 日志与 MQTT 载荷。这里统一转成文本；API 边界也会再归一化一次。
            if isinstance(reason, (tuple, list)):
                reason = ", ".join(str(item) for item in reason)
            else:
                reason = str(reason or "")
        # P1-21：优先用调用方传入的策略名；仅在缺失时兜底查询
        if not strategy_name:
            strategy_name = str(extra.get("strategy_name", ""))
        if not strategy_name:
            try:
                strategy_dao = self._strategy_api.get_by_id(strategy_id)
                # 兜底路径也要强制转 str：该值会进入 MQTT payload（json.dumps），
                # 若接口返回结构异常拿到 None / 非字符串，会直接抛
                # "not JSON serializable" 并导致整条信号保存失败。
                strategy_name = str(strategy_dao.get("name", "") or "")
            except Exception as exc:
                self.logger.debug("查询策略 %s 名称失败: %s", strategy_id, exc)

        # recipient 一律用**用户策略名**（UserStrategyDao.name）。StrategyState 在加载时
        # 已带上，正常路径不发请求；旧调用点未传时才兜底查一次。
        # 不回退到 strategy_name（策略模板名）：同一模板下用户可以建多条策略，
        # 模板名区分不开是哪一条发出的信号。
        if not user_strategy_name:
            try:
                us_info: Any = self._user_strategy_api.get(user_strategy_id)
                if isinstance(us_info, dict):
                    user_strategy_name = str(us_info.get("name", "") or "")
            except Exception as exc:
                self.logger.debug(
                    "查询用户策略 %s 名称失败: %s", user_strategy_id, exc
                )

        # 查询最近一次同股票+同策略的交易信号（不限方向），用于信号风暴过滤
        try:
            latest_any: Any = self._trade_signal_api.latest(strategy_id, user_strategy_id, code)
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

        # 卖出时查询最近一次买入记录，计算收益率（P1-15：只算比率，不算金额）
        if signal_type == "SELL":
            try:
                latest_result: Any = self._trade_signal_api.latest(
                    strategy_id, user_strategy_id, code, action="买入"
                )
                if latest_result and isinstance(latest_result, dict):
                    buy_price: float = float(latest_result.get("trade_price", 0))
                    if buy_price > 0:
                        profit_rate = (price - buy_price) / buy_price * 100
                        self.logger.info(
                            "卖出信号计算: code=%s, buy_price=%.2f, sell_price=%.2f, "
                            "profit_rate=%.2f%% (每股价差=%.4f，不计入信号记录)",
                            code, buy_price, price, profit_rate, price - buy_price,
                        )
            except Exception as exc:
                self.logger.warning(
                    "查询最近买入记录失败 code=%s: %s", code, exc, exc_info=True,
                )

        try:
            # 1. 先通过 API 保存交易信号到服务器（P1-15：不写 profit_amount）
            self._trade_signal_api.add(
                strategy_id=strategy_id,
                user_strategy_id=user_strategy_id,
                stock_code=code,
                trade_price=price,
                action=action,
                profit_rate=profit_rate,
                reason=reason,
                score=score,
                star=star,
            )
            self.logger.info(
                "交易信号已保存: strategy_id=%s, user_strategy_id=%s, strategy_name=%s, "
                "user_strategy_name=%s, code=%s, action=%s, price=%.2f, "
                "profit_rate=%.2f%%, score=%.2f, star=%.1f, reason=%s",
                strategy_id, user_strategy_id, strategy_name, user_strategy_name,
                code, action, price, profit_rate, score, star, reason,
            )

            # 2. 通过 MQTT 发送交易信号通知（字段与服务端信号记录保持一致）
            if star_passed:
                signal_data: dict[str, Any] = {
                    "strategy_id": strategy_id,
                    "user_strategy_id": user_strategy_id,
                    "strategy_name": strategy_name,
                    "user_strategy_name": user_strategy_name,
                    "stock_code": code,
                    "trade_price": price,
                    "action": action,
                    "profit_rate": round(profit_rate, 2),
                    "score": round(score, 2),
                    "star": round(star, 1),
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
            else:
                self.logger.info(
                    "星级未达门槛，不发 MQTT、不推送、不下单（信号已写库）: "
                    "action=%s, code=%s, star=%.1f, min_star=%.1f",
                    action, code, star, min_star,
                )

            # 3. 通过企业微信/微信发送交易信号推送通知
            # 文案统一由 TradeNotifier 按结构化字段拼装（2026-09-28）：
            # 「买入002050.SZ(三花智控)，买入价:35.42,买入条件：…，买入推荐: 3颗星」。
            # 不再在这里手拼字符串 —— 股票名称、星级格式都属于展示层，
            # 且此处拼的文案无法覆盖「名称要查接口」这类需求。
            # 卖出传 profit_rate（买入传 None，不显示收益率段）。
            # 门槛判定在通知器里再走一次（传已解析好的 min_star，不重复解析）：
            # 直接调用 notify_signal 的其它入口也受同一套门槛约束。
            self._notifier.notify_signal(
                action=action,
                code=code,
                price=price,
                reason=reason,
                star=star,
                # recipient 用**用户策略名**（不是策略模板名）：同一模板下可能有多条
                # 用户策略，只有用户自定义名才能对上号
                recipient=user_strategy_name,
                user_strategy_id=user_strategy_id,
                profit_rate=profit_rate if signal_type == "SELL" else None,
                min_star=min_star,
            )

            # 未达门槛时返回 False —— 与信号风暴过滤同一语义：
            # 调用方（_process_bar / _process_tick）据此跳过订单创建。
            return star_passed
        except Exception as exc:
            self.logger.error("保存交易信号失败: %s", exc, exc_info=True)
            return False

    # ---- 订单创建 ----

    @staticmethod
    def _make_client_order_id(user_strategy_id: str, code: str, action: str) -> str:
        """生成客户端订单幂等键（P1-17）。

        格式：`{user_strategy_id}:{code}:{action}:{yyyyMMddHHmmss}:{uuid4前8位}`。
        服务端若支持该字段可作为唯一键去重；不支持时 TradeManager 用它做本地去重，
        避免「创建订单超时 → 重试」导致重复委托。

        :param user_strategy_id: 用户策略关联 ID
        :param code: 股票代码
        :param action: 交易方向（买入/卖出）
        :return: 幂等键字符串
        """
        import uuid

        return (
            f"{user_strategy_id}:{code}:{action}:"
            f"{datetime.now().strftime('%Y%m%d%H%M%S')}:{uuid.uuid4().hex[:8]}"
        )

    def _reserve_buy_order(
        self, state: StrategyState, code: str, extra: dict[str, Any]
    ) -> tuple[OrderDao, float] | None:
        """买入占位：**纯内存操作**（须在 `state._lock` 内调用，P2-24）。

        只做「校验 + 扣款 + 标记在途 + 构造订单」，不发任何网络请求。
        真正的提交由 `_commit_reserved_order` 在锁外完成，
        避免 HTTP 往返期间长时间持有 `state._lock`（原实现在锁内做 HTTP，
        一个慢请求会阻塞该用户所有股票的信号处理）。

        :param state: 用户策略运行时状态
        :param code: 股票代码
        :param extra: 策略返回信息（含 current_price）
        :return: (订单对象, 预估占用资金)；不满足条件返回 None
        """
        strategy_name: str = state.strategy_name
        current_price: float = float(extra.get("current_price", extra.get("price", 0.0)))
        if current_price <= 0:
            self.logger.warning("[%s] 买入信号缺少有效价格: code=%s", strategy_name, code)
            return None
        if code in state.pending_codes:
            self.logger.debug("[%s] code=%s 已有在途订单，跳过重复买入", strategy_name, code)
            return None
        buy_qty: int = self._calc_buy_quantity(state, current_price)
        if buy_qty <= 0:
            self.logger.warning(
                "[%s] 可用资金不足（不足一手或余额为 0），跳过买入: code=%s, available=%.2f",
                strategy_name, code, state.available_cash,
            )
            return None
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
            client_order_id=self._make_client_order_id(
                state.user_strategy_id, code, "买入"
            ),
        )
        return order, estimated_cost

    def _reserve_sell_order(
        self,
        state: StrategyState,
        code: str,
        entry_price: float,
        extra: dict[str, Any],
        stock_time: Any = "",
    ) -> tuple[OrderDao, float] | None:
        """卖出占位：**纯内存操作**（须在 `state._lock` 内调用，P2-24）。

        持仓数量从 `state.positions` 读取（`_load_positions` 已写回），
        X3 的 T+1 可卖数量收敛也在此完成；真正提交在锁外。

        :param state: 用户策略运行时状态
        :param code: 股票代码
        :param entry_price: 持仓成本价
        :param extra: 策略返回信息（含 current_price）
        :param stock_time: 触发信号时的行情时间（X3 可卖数量判定）
        :return: (订单对象, 预估回笼资金)；不满足条件返回 None
        """
        strategy_name: str = state.strategy_name
        pos: dict[str, Any] = state.positions.get(code, {})
        hold_quantity: int = int(pos.get("quantity", 0))
        if hold_quantity <= 0:
            self.logger.warning("[%s] 卖出信号但无持仓: code=%s", strategy_name, code)
            return None
        sell_quantity: int = self._resolve_sellable_quantity(pos, stock_time)
        if sell_quantity <= 0:
            self.logger.info(
                "[%s] code=%s 持仓 %d 股均为当日买入（T+1 不可卖），"
                "本轮不生成卖出订单（信号已发出）: t1_qty=%s, t1_day=%s",
                strategy_name, code, hold_quantity,
                pos.get("t1_qty", 0), pos.get("t1_day", ""),
            )
            return None
        if code in state.pending_codes:
            self.logger.debug("[%s] code=%s 已有在途订单，跳过重复卖出", strategy_name, code)
            return None
        current_price: float = float(extra.get("current_price", extra.get("price", 0.0)))
        if current_price <= 0:
            self.logger.warning("[%s] 卖出信号缺少有效价格: code=%s", strategy_name, code)
            return None
        estimated_proceeds: float = current_price * sell_quantity
        # 先标记在途，避免并发重复下单；回笼资金在提交失败时回滚
        state.pending_codes.add(code)
        state.available_cash += estimated_proceeds
        order = OrderDao(
            user_strategy_id=state.user_strategy_id,
            stock_code=code,
            entrust_quantity=sell_quantity,
            trade_price=current_price,
            position_price=entry_price,
            action="卖出",
            status=OrderStatus.ENTRUST.value,
            create_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            client_order_id=self._make_client_order_id(
                state.user_strategy_id, code, "卖出"
            ),
        )
        return order, estimated_proceeds

    def _commit_reserved_order(
        self,
        state: StrategyState,
        code: str,
        order: OrderDao,
        reserved_amount: float,
    ) -> bool:
        """提交已占位的订单（**须在 state._lock 之外调用**，P2-24）。

        提交失败时回滚占位：移除在途标记 + 撤销资金变动。

        :param state: 用户策略运行时状态
        :param code: 股票代码
        :param order: 已占位构造好的订单
        :param reserved_amount: 占位时变更的资金金额
        :return: 是否成功提交
        """
        if order.action == "卖出":
            log_line = (
                f"卖出信号: code={code}, price={order.trade_price:.2f}, "
                f"entry={order.position_price:.2f}, qty={order.entrust_quantity}, "
                f"proceeds≈{reserved_amount:.2f}"
            )
        else:
            log_line = (
                f"买入信号: code={code}, price={order.trade_price:.2f}, "
                f"qty={order.entrust_quantity}, cost≈{reserved_amount:.2f}"
            )
        ok: bool = self._submit_trade(order, state, rollback_amount=reserved_amount)
        if not ok:
            with state._lock:
                state.pending_codes.discard(code)
                if order.action == "卖出":
                    state.available_cash -= reserved_amount
                else:
                    state.available_cash += reserved_amount
            self.logger.warning(
                "[%s] 订单提交失败，已回滚资金与在途标记: code=%s, action=%s",
                state.strategy_name, code, order.action,
            )
            return False
        self._save_runlog(
            state,
            f"{log_line}, available={state.available_cash:.2f}",
            "INFO",
        )
        return True

    def _create_buy_order(
        self, state: StrategyState, code: str, extra: dict[str, Any]
    ) -> None:
        """根据买入信号生成买入订单（占位 + 提交一体，供测试与直接调用）。

        失败回滚由 `_commit_reserved_order` 统一处理，此处不重复回滚。
        """
        reserved = self._reserve_buy_order(state, code, extra)
        if reserved is None:
            return
        order, estimated_cost = reserved
        self._commit_reserved_order(state, code, order, estimated_cost)

    def _log_t1_advisory(
        self,
        state: StrategyState,
        code: str,
        position: dict[str, Any],
        stock_time: Any,
    ) -> None:
        """T+1 提示日志（设计约定 D-2：只提示，不拦截下单）。

        卖出信号涉及「当日买入」的标的时，按 A 股 T+1 规则当日不可卖出。
        但本系统的订单只是给交易员的参考（信号优先、人做最终决策），
        因此此处仅输出提示日志，订单仍会创建；若券商侧必须硬拦截，
        请在调用本方法之后补 `return`。

        注意：`position["buy_time"]` 由 TradeManager 写成 "YYYY-MM-DD HH:MM:SS"，
        与行情时间比较必须按日期前缀截断，否则 `==` 恒为 False、日志永不触发。

        :param state: 用户策略运行时状态
        :param code: 股票代码
        :param position: 该股票的持仓信息（含 buy_time）
        :param stock_time: 触发信号时的行情时间（分钟 bar / tick 的 create_time）
        """
        buy_time: str = str(position.get("buy_time", ""))
        bar_day: str = str(stock_time)[:10]
        if not bar_day or buy_time[:10] != bar_day:
            return
        self.logger.info(
            "[%s] code=%s 卖出信号涉及当日买入标的（T+1 不可卖出），"
            "订单仍创建仅供交易员参考: buy_time=%s, bar_time=%s",
            state.strategy_name, code, buy_time, bar_day,
        )

    def _resolve_sellable_quantity(self, position: dict[str, Any], stock_time: Any) -> int:
        """计算 T+1 规则下的**当日可卖数量**（X3）。

        设计约定 D-2 规定订单只是给交易员的参考、不做硬拦截，但卖出数量本身
        应当是「实际可卖的那部分」，否则订单数量是错的。因此这里做数量收敛：

        - `position["t1_qty"]`：当日买入、当日尚不可卖的数量（TradeManager 写入）
        - `position["t1_day"]`：`t1_qty` 归属的交易日，`YYYY-MM-DD`

        只有当 `t1_day` 等于当前行情日时，才从总量中扣减 `t1_qty`；
        `t1_day` 是更早的日期说明那部分已经解禁，无需扣减。

        :param position: 该股票的持仓信息
        :param stock_time: 触发信号时的行情时间（分钟 bar / tick 的 create_time）
        :return: 可卖数量（>= 0）
        """
        total: int = int(position.get("quantity", 0) or 0)
        t1_qty: int = int(position.get("t1_qty", 0) or 0)
        if t1_qty <= 0:
            return total
        bar_day: str = str(stock_time)[:10]
        t1_day: str = str(position.get("t1_day", ""))[:10]
        # 无法判定买入日（无行情时间或无 t1_day）时保守处理：不做扣减，
        # 保持与 D-3「按最近 2 个交易日兜底」一致的宽松口径
        if not bar_day or not t1_day:
            return total
        if t1_day == bar_day:
            return max(total - t1_qty, 0)
        return total

    def _create_sell_order(
        self,
        state: StrategyState,
        code: str,
        entry_price: float,
        extra: dict[str, Any],
        stock_time: Any = "",
    ) -> None:
        """根据卖出信号生成卖出订单（占位 + 提交一体，供测试与直接调用）。

        :param state: 用户策略运行时状态
        :param code: 股票代码
        :param entry_price: 持仓成本价
        :param extra: 策略返回的详细信息（含 current_price）
        :param stock_time: 触发信号时的行情时间，用于 T+1 可卖数量判定（X3）
        """
        positions: dict[str, dict[str, Any]] | None = self._load_positions(state)
        # P1-18：持仓拉取失败时 fail-closed，避免把「读不到持仓」当成「无持仓」
        if positions is None:
            self.logger.warning(
                "[%s] code=%s 持仓信息获取失败，跳过卖出下单（fail-closed）",
                state.strategy_name, code,
            )
            return
        reserved = self._reserve_sell_order(state, code, entry_price, extra, stock_time)
        if reserved is None:
            return
        order, estimated_proceeds = reserved
        self._commit_reserved_order(state, code, order, estimated_proceeds)

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
            self.logger.warning(
                "[%s] 可用资金为 0，无法买入"
                "（initial_amount=0 表示限制买入；建仓后资金耗尽属正常情况）",
                state.strategy_name,
            )
            return 0
        max_amount: float = min(available, self.MAX_SINGLE_ORDER_AMOUNT)
        quantity: int = int(max_amount / price / 100) * 100
        if quantity < 100:
            return 0
        return quantity

    def _parse_initial_amount(self, raw: Any) -> float:
        """解析 `initial_amount`，判定逻辑委托 `dao.user_strategy_dao.parse_initial_amount`。

        `0` 是**有效语义**（限制买入），原样保留；只有字段缺失或非法
        （None / "" / 非数字）才回退默认 `DEFAULT_INITIAL_AMOUNT`（10 万元）。
        原实现是 `float(raw_initial or 0)` —— 字段缺失时得到 0，而 0 在本项目语义里
        恰好是「限制买入」，等于让未配置的策略静默不下单（`6a58d62b…` 0/0 即此类）。

        只做转调、不另起一套判定，保证加载链路与 DAO 用的是同一份口径。

        :param raw: 服务端/DAO 返回的原始值
        :return: 规范化后的初始资金
        """
        return parse_initial_amount(raw)

    def _parse_max_stock_count(self, raw: Any) -> int:
        """解析 `max_stock_count`（P0-6），判定逻辑委托 `dao.user_strategy_dao.parse_max_stock_count`。

        语义（2026-09-24 最终确认）：`-1`（或任何负数）= 不限制持仓数量；
        `0` = **限制买入**（不允许新建持仓，也不允许对已有持仓加仓）；正数 = 持仓上限。
        字段缺失或非法（None / "" / 非数字）时才回退默认 `DEFAULT_MAX_STOCK_COUNT`
        （2026-09-28 由 4 改为 **-1 不限制**）—— `0` 是有效值，不会被改写成默认值。

        :param raw: 服务端/DAO 返回的原始值
        :return: 规范化后的最大持仓数量
        """
        return parse_max_stock_count(raw)

    def _buy_block_reason(
        self, state: StrategyState, positions: dict[str, dict[str, Any]]
    ) -> str:
        """返回禁止买入的原因文本；返回空字符串表示允许买入。

        2026-09-24 语义确认（原 P0-6 争议项的最终结论）：
        `max_stock_count = 0` 与 `initial_amount = 0` **都表示限制买入** —— 既不允许
        新建持仓，也不允许对已有持仓加仓。卖出不受影响（卖出路径只依赖持仓，
        不读这两个字段），因此「限制买入」的策略仍可正常清仓。

        判定顺序（先命中先返回，原因文案要能直接回答「为什么有信号没订单」）：

        1. `max_stock_count == 0` → 限制买入
        2. `initial_amount <= 0` → 限制买入
        3. `max_stock_count > 0` 且持仓数已达上限 → 已达上限
        4. 其余 → 允许（`max_stock_count < 0` 时不限制持仓数量）

        账户级回撤**不在**本方法的判定范围内（2026-09-29 语义变更）：回撤达阈值时的
        动作是「卖出判定加分」（见 `_check_sell_conditions` 的 `risk.drawdown_score`），
        不是拦截买入。回撤值由 `_refresh_account_drawdown` 在调用策略前注入。

        判定必须放在 `_process_bar` / `_process_tick` 的买入分支里，与其它跳过原因
        一样记 INFO —— `cfg/log.yaml` 的 info.log 是 INFO 级，记 DEBUG 等于运维看不见。

        :param state: 用户策略运行时状态
        :param positions: 当前持仓（code → 持仓信息）
        :return: 禁止买入的原因；可买入时为空字符串
        """
        limit: int = int(state.max_stock_count or 0)
        if limit == 0:
            return (
                "策略配置 max_stock_count=0（0 表示限制买入，"
                "不允许新建持仓或加仓）"
            )
        if float(state.initial_amount or 0) <= 0:
            return (
                "策略配置 initial_amount=0（0 表示限制买入，"
                "不允许新建持仓或加仓）"
            )
        if limit > 0 and len(positions) >= limit:
            return f"持仓数 {len(positions)} 已达上限 {limit}"
        return ""

    # ---- 持仓管理 ----

    def _load_positions(
        self, state: StrategyState, force_refresh: bool = False
    ) -> dict[str, dict[str, Any]] | None:
        """加载策略的当前持仓信息。

        三种结果必须严格区分（2026-09-24 线上事故的根因就在前两者被混为一谈）：

        1. **无执行记录** → 返回 `{}`（空持仓）。服务端在「该策略还没有任何执行
           记录」时返回 404，`UserStrategyApi.get_latest_execution` 已把它收敛成
           空字典。这是新策略的正常初始状态，可以下单。
        2. **取数失败** → 返回 `None`（P1-18 fail-closed）。调用方必须把 None 视为
           「持仓未知」并跳过下单，绝不能当成「无持仓」，否则会把已有持仓当成新
           标的重复买入。
        3. 正常返回 → code → 持仓信息 的映射。

        若把 (1) 也当 (2) 处理会形成**死锁**：执行记录只能由订单成交后写入，而
        订单又要先读到持仓才创建 ⇒ 新策略永远下不了第一单，永远 404。
        """
        if not force_refresh and state.positions:
            self._update_peak_asset(state)
            return dict(state.positions)
        if not state.user_strategy_id:
            # 无 user_strategy_id 说明该 state 未绑定用户策略，视为无持仓
            return {}
        try:
            exec_data: dict[str, Any] = self._user_strategy_api.get_latest_execution(
                state.user_strategy_id
            )
            if not exec_data and not state.no_execution_record_noted:
                # 首次发现「尚无执行记录」时提示一次：该策略此前从未成交，
                # 系统按空持仓 + 初始资金运行，第一笔订单成交后即会出现执行记录。
                state.no_execution_record_noted = True
                self.logger.info(
                    "[%s] user_strategy_id=%s 尚无执行记录，按空持仓处理"
                    "（初始资金 %.2f，成交后会自动产生执行记录）",
                    state.strategy_name, state.user_strategy_id,
                    state.initial_amount,
                )
            positions_list: list[dict[str, Any]] = exec_data.get("positions", [])
            positions: dict[str, dict[str, Any]] = {
                p.get("code", ""): p for p in positions_list if p.get("code")
            }
            state.positions = positions
            ia: float = float(exec_data.get("initial_amount", 0))
            if ia > 0 and not state.initial_amount_configured:
                # 配置里没有 initial_amount 时才用执行记录兜底；
                # 配置 0 表示限制买入，不能被历史执行记录洗白（见 _load_execution_data）
                state.initial_amount = ia
            state.current_profit = float(exec_data.get("current_profit", 0) or 0)
            if "remaining_cash" in exec_data:
                state.available_cash = float(exec_data.get("remaining_cash", 0) or 0)
            else:
                state.available_cash = self._calc_available_cash(state)
            # 每笔订单成交后都会 force_refresh 到这里，顺势刷新权益峰值，
            # 保证账户回撤判定用的是最新权益（不是启动时的旧值）
            self._update_peak_asset(state)
            return positions
        except Exception as exc:
            # 只有真实取数故障才会走到这里（超时 / 5xx / 连接失败）；
            # 「无执行记录」的 404 已在 API 层收敛为 {}，不会触发 fail-closed。
            self.logger.warning(
                "加载持仓信息失败（取数故障，本次跳过下单）user_strategy_id=%s: %s",
                state.user_strategy_id, exc,
            )
            return None

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
        """设置定时器：每个交易日盘前运行 daily_before_trading + 配置对账轮询。"""
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger

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

        # 配置对账轮询（兜底）：主通道是服务端的变更通知，这里覆盖
        # 「通知漏发 / MQTT 掉线重连期间 / 进程晚于服务端启动」这几种情况。
        # 间隔按秒固定注册，交易时段与其它时段用不同周期在 _reconcile_job 里判断。
        self._scheduler.add_job(
            self._reconcile_job,
            IntervalTrigger(seconds=self.RECONCILE_INTERVAL_SECONDS),
            id="reconcile_config",
            name="配置对账（热更新）",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,
        )
        self.logger.info(
            "定时器已设置: 每 %d 秒对账一次（非交易时段 %d 秒）",
            self.RECONCILE_INTERVAL_SECONDS, self.RECONCILE_INTERVAL_OFFHOURS_SECONDS,
        )

    def daily_before_trading(self) -> None:
        """每日盘前定时任务（按用户策略逐个执行）。

        对每个 **运行中** 的用户策略：
        1. 重新加载其股票池
        2. 调用其独立策略实例的 before_trading（清理本用户缓存）
        3. 用「该用户股票池 + 热门行业」调用 select 筛选
        4. 过滤该用户黑名单后，按 user_strategy_id 保存选股结果

        变更说明（P1-20 / P2-25）：原实现按策略模板（strategy_id）执行一次并
        复用同一份选股结果，但服务端选股结果已改为按 user_strategy_id 归属，
        且不同用户的 strategy_params 不同、选股结果本就应该不同，
        因此改为按用户策略逐个执行。代价是同一模板 N 个用户会执行 N 次
        select（选股结果写入 UserStrategyCache，避免策略侧再次 HTTP 回查）。
        """
        trade_date: str = datetime.now().strftime("%Y-%m-%d")
        if not self.xshg.is_session(trade_date):
            self.logger.info("今日 %s 为节假日，跳过盘前筛选", trade_date)
            return
        self.logger.info("===== 每日盘前筛选开始 trade_date=%s =====", trade_date)

        # 查询热门行业股票代码（所有用户共用）
        industry_codes: list[str] = self._collect_hot_industry_codes()

        for state in self._snapshot_states():
            if state.status != "running" or state.instance is None:
                continue
            self._run_daily_selection_for_state(state, industry_codes, trade_date)

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

    def _run_daily_selection_for_state(
        self,
        state: StrategyState,
        industry_codes: list[str],
        trade_date: str,
        reset_cache: bool = True,
    ) -> None:
        """对单个**用户策略**执行盘前筛选并保存结果。

        :param state: 用户策略运行时状态（含独立策略实例、股票池、黑名单）
        :param industry_codes: 热门行业股票代码（所有用户共用）
        :param trade_date: 交易日 YYYY-MM-DD
        :param reset_cache: 是否先调 `before_trading` 清该用户的缓存。
            盘前任务传 True（新的一天要清干净）；盘中因配置变更补跑时必须传
            **False** —— 那会清掉当日的选股/打分快照与持仓最高价，等于把今天
            已经跑过的行情作废。
        """
        instance: BaseStrategy | None = state.instance
        if instance is None:
            return
        sname: str = state.strategy_name
        usid: str = state.user_strategy_id

        # 1. 重新加载该用户的股票池
        self._reload_stock_pool(state)
        pool_codes: list[str] = list(dict.fromkeys(state.stock_pool))

        # 2. before_trading：清理该策略实例的缓存（用该用户自己的股票池）
        if reset_cache:
            try:
                instance.before_trading(trade_date=trade_date, stock_codes=pool_codes)
            except Exception as exc:
                self.logger.error(
                    "[%s] before_trading 异常 (user_strategy_id=%s): %s",
                    sname, usid, exc, exc_info=True,
                )
            self.logger.info("[%s] before_trading 完成 (user_strategy_id=%s)", sname, usid)

        # 3. 合并股票池 + 热门行业
        combined: list[str] = list(dict.fromkeys(pool_codes + industry_codes))
        self.logger.info(
            "[%s] 股票池 %d 只 + 热门行业 %d 只 = 合并 %d 只 (user_strategy_id=%s)",
            sname, len(pool_codes), len(industry_codes), len(combined), usid,
        )

        if not combined:
            self.logger.info("[%s] 合并后股票列表为空，跳过筛选 (user_strategy_id=%s)", sname, usid)
            return

        # 4. select 筛选（按用户策略执行；选股期间不持任何全局锁，
        #    避免阻塞行情派发 —— P2-24）
        try:
            results: list[dict[str, Any]] = instance.select(
                combined, today_str=trade_date, days=self.SELECT_KLINE_DAYS
            )
        except Exception as exc:
            self.logger.error("[%s] select 筛选异常: %s", sname, exc, exc_info=True)
            return

        matched_results: list[dict[str, Any]] = [
            r for r in results if r.get("matched", False)
        ]
        matched_codes: list[str] = [str(r["code"]) for r in matched_results]
        # 代码 → 策略打分：服务端选股表已新增 score 字段，保存时必须带上
        score_map: dict[str, float] = {
            str(r["code"]): float(r.get("score", 0.0) or 0.0) for r in matched_results
        }

        # 4.1 把「取数失败」与「正常不满足条件」分开统计并显式告警 ——
        #     取数失败（行情接口异常）时候选集不完整，绝不能静默变成「0 交易」
        fetch_failed_codes: list[str] = [
            str(r["code"]) for r in results if r.get("fetch_failed", False)
        ]
        if fetch_failed_codes:
            self.logger.warning(
                "[%s] 盘前筛选有 %d/%d 只股票**取数失败**（行情接口异常，非「不满足条件」），"
                "当日候选集不完整，建议人工复核: %s (user_strategy_id=%s)",
                sname, len(fetch_failed_codes), len(results),
                fetch_failed_codes[:20], usid,
            )

        # 5. 过滤该用户的股票池外与黑名单股票
        #    （选股结果只保留该用户真正关注的标的，避免把全市场结果推给用户）
        if pool_codes:
            pool_set: set[str] = set(pool_codes)
            outside: int = sum(1 for c in matched_codes if c not in pool_set)
            if outside:
                self.logger.info(
                    "[%s] 盘前筛选结果中有 %d 只不在用户股票池内（保留，供信号推送过滤使用）",
                    sname, outside,
                )
        if state.blacklist_codes:
            before_black: int = len(matched_codes)
            matched_codes = [c for c in matched_codes if c not in state.blacklist_codes]
            if before_black != len(matched_codes):
                self.logger.info(
                    "[%s] 盘前筛选已过滤 %d 只黑名单股票",
                    sname, before_black - len(matched_codes),
                )
        self.logger.info(
            "[%s] 盘前筛选完成: 筛选 %d 只, 符合策略 %d 只, 取数失败 %d 只 (user_strategy_id=%s)",
            sname, len(combined), len(matched_codes), len(fetch_failed_codes), usid,
        )

        # 6. 保存选股结果（按 user_strategy_id 归属，P1-20；携带 score）
        if matched_codes:
            stocks: list[dict[str, Any]] = [
                {"code": code, "score": score_map.get(code, 0.0)}
                for code in matched_codes
            ]
            try:
                self._strategy_select_stock_api.add(usid, stocks)
                self.logger.info(
                    "[%s] 策略选股结果已保存: user_strategy_id=%s, stocks=%s",
                    sname, usid, stocks,
                )
            except Exception as exc:
                self.logger.error("[%s] 保存策略选股结果失败: %s", sname, exc, exc_info=True)
            # 同步写入本地缓存，策略侧 _load_select_stocks 可直接命中，省一次 HTTP 回查
            try:
                self._cache.save_select_stocks(usid, trade_date, matched_codes)
            except Exception as exc:
                self.logger.debug("[%s] 选股结果写入本地缓存失败: %s", sname, exc)

        # 7. 记录日志（按用户策略）
        self._save_runlog(
            state,
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
        # P1-16：补投上次进程遗留的执行记录（save_execution 失败的补偿队列）
        try:
            flushed: int = self._trade_manager.flush_pending_executions()
            if flushed:
                self.logger.info("已补投 %d 条遗留的执行记录", flushed)
        except Exception as exc:
            self.logger.warning("补投遗留执行记录失败: %s", exc)


    def on_stop(self) -> None:
        """工作流停止钩子。"""
        self.logger.info("策略工作流停止中...")
        # P2-23：停止前排空合并队列，避免最后一批行情被丢弃
        try:
            drained: int = self.flush_pending_bars()
            if drained:
                self.logger.info("停止前已处理 %d 条待派发行情", drained)
        except Exception as exc:
            self.logger.warning("排空待派发行情失败: %s", exc)
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        for state in self._snapshot_states():
            if state.status == "running":
                state.status = "paused"
                self._save_runlog(state, f"策略 {state.strategy_name} 已暂停（工作流关闭）", "INFO")
        with self._registry_lock:
            self._strategy_instances.clear()
            self._strategy_templates.clear()
            self._user_strategies.clear()
