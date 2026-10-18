# strategy/base_strategy.py
from __future__ import annotations

import datetime
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Iterable, cast

import pandas as pd
import yaml

from api.client import ApiClient
from api.finance import FinanceApi
from api.strategy import StrategyApi
from api.strategy_select_stock import StrategySelectStockApi
from api.user_strategy import UserStrategyApi
from app_context import AppContext
from core.user_strategy_cache import UserStrategyCache
from factor.factor_manager import FactorManager
import exchange_calendars as xcals


class MarketDataFetchError(RuntimeError):
    """行情取数失败 —— 与「接口正常返回但没有行情数据」严格区分。

    触发场景：行情接口抛异常 / 连接超时 / 服务端 5xx（例如
    `/api/market/kline/day` 不带日期或第三方回源超时导致的间歇性 500）。

    **语义**：这只说明「这次没取到数据」，**不代表该股票没有行情、更不代表
    它不符合策略条件**。因此调用方不得把它当成「正常不满足条件」静默忽略：
    - 实时链路（workflow）：至少要落到 warning 并在 select 结果里标出
      `fetch_failed=True`，避免出现「静默 0 交易」；
    - 回测链路：必须重试后仍失败即**中止回测并报错**，绝不允许把不完整的
      数据继续算下去、写出一条看似正常的回测记录。
    """


class BaseStrategy(ABC):
    """策略顶级基类，提供通用数据加载、选股与策略元数据保存能力。"""

    strategy_id: str = ""
    user_strategy_id: str = ""
    strategy_name: str = "BaseStrategy"
    user_strategy_name: str = ""
    description: str = ""
    strategy_type: str = "选股策略"

    # 策略在 cfg/strategy.yaml 中的配置节名，为空时按类名蛇形命名推导
    # （如 StrongReboundStrategy -> strong_rebound_strategy）
    config_section: str = ""

    # 分钟缓存 Redis key 过期时间（秒）：6 小时，覆盖整个交易日
    MINUTE_CACHE_TTL: int = 6 * 60 * 60

    # 日K取数失败重试次数（额外重试次数，总尝试 = 1 + FETCH_RETRIES）。
    # 实时链路默认 **不重试**：盘前选股期间不能 sleep 阻塞行情派发，
    # 一次失败即记 warning 并把 fetch_failed 透出（见 MarketDataFetchError）。
    # 回测链路会显式放大（见 BacktestConfig.fetch_retries）且要求失败即中止。
    FETCH_RETRIES: int = 0
    # 重试间隔（秒）；<=0 表示立即重试
    FETCH_RETRY_INTERVAL: float = 1.0

    # 账户级回撤阈值（%）：账户（已实现）权益自峰值回撤达到此值时，**在卖出判定中计入
    # `SELL_RISK_DRAWDOWN_SCORE` 分值**（把回撤当作一条卖出理由），不冻结开新仓。
    # 0 或正数表示关闭。
    # 权益口径 = 初始资金 + 已实现盈利，与 `TradeManager._refresh_execution_metrics`
    # 和 `BacktestEngine` 的权益曲线同源（见 workflow/strategy_workflow.py::_account_asset）。
    # 基类默认 0.0（关闭）：是否启用由各策略自己的参数决定，避免悄悄改变已有策略行为。
    #
    # 语义沿革（2026-09-29）：原实现是「回撤超阈值即停止开新仓」（账户级熔断）。实测发现
    # 回撤是离散跳跃的（持仓按成本计价，权益只在卖出结算时变动），只要单笔亏损可能超过
    # 阈值，「回撤 ≤ 阈值」在数学上就不成立；而且熔断后不再交易、权益不再变动，回撤也
    # 无法收窄，等于一次触发即永久停手。用户据此改为「把回撤当作卖出判断分值」。
    RISK_MAX_DRAWDOWN_PCT: float = 0.0

    # 账户回撤达到 `RISK_MAX_DRAWDOWN_PCT` 时，计入卖出判定的分值（10 分制，取负）。
    # 只有策略自己的 `_check_sell_conditions` 主动调用 `_risk_drawdown_score()` 才生效 ——
    # 基类不做隐式拦截，避免悄悄改变已有策略的卖出行为。
    SELL_RISK_DRAWDOWN_SCORE: float = -6.0

    # ---- 买入信号价止损（2026-10-14）----
    #
    # 语义：**连续 N 个分钟 bar 的现价都低于「买入信号价」** ⇒ 直接卖出（硬止损）。
    # 基准是**本策略发出买入信号时的价格**，不是持仓最高价、也不是成交后的成本价。
    #
    # 为什么替换掉原「自持仓最高点回撤 N%」：实时链路按设计约定 D-1 恒传空持仓
    # （见 `_sell_baseline_buy_time`），卖出侧拿不到真实买入日 ⇒ 最高价回退成
    # 「最近 2 个交易日最高价」。买「缩量回调」类标的时，该股此刻往往已经比前两日
    # 高点低 6% 以上，于是**买入瞬间就接近卖出条件**（实测 10-09 的 605499 / 600875
    # 连续 6 笔卖出理由里的回撤值全是 −11.79%，一字不差，共用同一个缓存峰值）。
    # 换成「相对买入信号价」之后，判定基准与建仓时点对齐，不再有这个偏差。
    #
    # 达成方式是**硬止损**（直接返回卖出信号），不并入卖出分累加 —— 止损要的是
    # 果断退出，不该被「枢轴点分 0 / 布林带分 0」的中间态稀释，也不该被门槛偏移
    # 二次抬高。`SELL_BUY_PRICE_BREAK_ENABLED=False` 可整体关闭。
    SELL_BUY_PRICE_BREAK_ENABLED: bool = True
    SELL_BUY_PRICE_BREAK_COUNT: int = 3
    # 硬止损在日志 / 推送里**等效的卖出分值**（10 分制，取负）。它同时决定星级：
    # star = score_to_star(本值)，默认 −6.0 ⇒ 3.0 星，与原「回撤 −6.0 分」同强度。
    # 之所以不取「刚好跨过门槛」的分值：门槛会被阀门与市场偏移抬高，取门槛值会让
    # 极端行情下的硬止损星级掉到 0，被下游推送星级门槛二次拦掉 —— 那时恰恰最需要卖。
    SELL_BUY_PRICE_BREAK_SCORE: float = -6.0

    # ---- 日内熔断（2026-10-14）----
    #
    # 要解决的问题是硬止损的**空转**：止损把仓位清零之后，「缩量回调」这个买入条件
    # 往往仍然成立，成交间隔一到就买回同一只票、再被同一个止损打出来。v5 回测
    # （27 组，2026-07-21~10-13）里成交量策略这个循环最严重：「止损后 2 小时内再
    # 入场」的笔数占止损笔数的 60%~100%，其中 **93% / 81% / 80%** 又被同一条件
    # 二次止损。止损因此只把大亏切成小亏，总亏损没有改变，笔数却涨到 2.4~2.5 倍。
    #
    # 熔断在**当日剩余时间里关掉该股的一侧信号**，把「一天内来回 N 次」压成
    # 「一天最多一次」。作用域是**单只股票**，不是整条策略 —— 触发原因来自这只票
    # 自身的走势，没理由连带停掉同策略里其它标的。
    #
    # 为什么熔断 ≠ 抬高门槛：抬高门槛只是「更难触发」，在阴跌行情里分值照样能
    # 跨过来；熔断是硬否决，与门槛偏移链无关，也不会被阀门二次影响。
    #
    # `INTRADAY_BREAKER_ENABLED=False` 可整体关闭（对照用，见回测 CLI
    # `--no-intraday-breaker`）。
    INTRADAY_BREAKER_ENABLED: bool = True

    # 买入侧：某股**连续** `INTRADAY_BUY_BLOCK_LOSS_SELLS` 次亏损卖出后，当日不再
    # 对该股发买入信号（次日自动恢复，不需要显式清除）。
    #
    # 「亏损」的判定口径：**卖出价 < 买入信号价**。之所以不用成交成本口径 ——
    # 策略按设计约定恒收空持仓（X1），拿不到 `cost_price`，也拿不到真实成交价；
    # 它自己记录的「本笔建仓的买入信号价」是链路里唯一可用的建仓基准。
    # 回测里成交价与信号价出自同一根 bar 的 `price`，两者只差滑点
    # （0.02 元 ≈ 0.03%），因此这个代理在正负 0.03% 附近会误判，量级可忽略。
    #
    # 计数**跨日累计**，只在出现一笔盈利卖出（卖出价 >= 买入信号价）时清零；
    # 「连续」因此是「连续亏损往返」而不是「当日连续亏损往返」。
    INTRADAY_BUY_BLOCK_LOSS_SELLS: int = 3

    # 卖出侧：某股**连续** `INTRADAY_SELL_BLOCK_FLY_SELLS` 次卖出后价格都涨过该次
    # 卖出价（卖飞），则当日不再对该股发卖出信号（次日自动恢复）。
    #
    # 观察窗口是**当个交易日内**：卖出后到当日收盘都没涨过卖出价，视为「没卖飞」，
    # 连续计数清零。这与盘后阀门里的 R1/R2/R3 是两套东西 —— 那个是「盘后算、
    # 次日抬卖出门槛」，作用在日与日之间；本规则作用在当日盘中。
    INTRADAY_SELL_BLOCK_FLY_SELLS: int = 3

    # 买入是否依赖「当日选股结果」。
    #
    # 基类 `handle_minute_bar` / `handle_tick_bar` 用 `_load_select_stocks()` 决定
    # 是否走买入分支（选中的票才查买入条件），所以默认为 True。重写了
    # `handle_minute_bar` 且不读选股结果的策略（如波段策略，做 T 的信号要覆盖全池）
    # 必须显式置 False。
    #
    # 用途只有一个：盘中配置变更（改了股票池/参数）后，workflow 靠它判断要不要
    # 补跑一次当日选股。补跑要对全池取日 K，成本高，对不依赖选股的策略跑等于白跑。
    requires_daily_selection: bool = True

    # select 结果里的 reason_code 取值：用于把「取数失败」与「正常不满足条件」区分开
    REASON_MATCHED: str = "matched"
    REASON_NOT_MATCHED: str = "not_matched"
    REASON_NO_DATA: str = "no_data"
    REASON_FETCH_FAILED: str = "fetch_failed"
    REASON_STRATEGY_ERROR: str = "strategy_error"

    def __init__(
        self,
        user_strategy_id: str = "",
        user_strategy_name: str = "",
        strategy_id: str = "",
        strategy_type: str = "",
        user_strategy_params: dict[str, Any] | None = None,
        default_params: dict[str, Any] | None = None,
    ) -> None:
        """初始化日志、因子管理器并注册子类因子。

        :param user_strategy_id: 用户策略关联 ID（UserStrategyDao._id），
            传入后初始化时会查询数据库加载用户级参数 strategy_params
        :param strategy_id: 策略模板 ID（StrategyDao._id），
            用于加载策略模板默认参数 default_params
        :param user_strategy_params: 已查询的用户策略参数（UserStrategyDao.strategy_params），
            由 workflow 批量加载时传入以避免重复请求；为 None 时初始化时自行查询
        :param default_params: 已查询的策略模板默认参数（StrategyDao.default_params），
            由 workflow 批量加载时传入；为 None 时初始化时自行查询
        """
        self.logger = logging.getLogger(__name__)
        # 因子管理器改为**实例私有**：原实现取 AppContext 的全局单例，而 register 是无条件覆盖，
        # 导致多用户策略实例用各自的参数互相覆盖同一 key 的因子（原 P0-5）。
        self.factor_manager: FactorManager = FactorManager()
        self.user_strategy_id = user_strategy_id
        self.user_strategy_name = user_strategy_name
        self.strategy_id = strategy_id
        self.strategy_type = strategy_type
        self.strategy_params: dict[str, Any] = {}  # 已加载的策略参数（来源见 param_source）
        self.param_source: str = ""  # 参数来源描述（user_strategy/strategy/yaml）
        self._highest_prices: dict[str, float] = {}  # 持仓期间最高价，key=code
        # 全局缓存单例：当日行情（由 workflow 单点写入）、策略打分、选股结果。
        # 原来由本类维护的行情/评分缓存已迁出，见 core/user_strategy_cache.py。
        self._cache: UserStrategyCache = UserStrategyCache()
        # 账户当前回撤（%，负值），由 workflow / 回测在调用策略前注入（见 set_account_drawdown）。
        # 必须在 `_apply_resolved_params` 之前建好：基类构造会回调子类 `apply_params`，
        # 子类若在其中读到本属性，晚赋值就会 AttributeError（见「实例化契约」的失效方式）。
        self.account_drawdown_pct: float = 0.0
        # 每日阀门与市场偏移（盘前由 workflow 注入，算法见 strategy/valve.py）。
        # 同样必须在 `_apply_resolved_params` 之前建好：基类构造回调子类 `apply_params`，
        # 子类若在其中读到这些属性，晚赋值就会 AttributeError（见类 docstring 的
        # 「实例化契约」失效方式）。
        self._daily_buy_valve: dict[str, float] = {}   # code -> 买入门槛上调幅度（>=0）
        self._daily_sell_valve: dict[str, float] = {}  # code -> 卖出门槛上调幅度（>=0）
        self._market_buy_offset: float = 0.0   # 市场级买入门槛偏移（>0 = 更难买）
        self._market_sell_offset: float = 0.0  # 市场级卖出门槛偏移（>0 = 更难卖）
        self.daily_valve_date: str = ""        # 当前生效阀门来自哪个交易日
        # 日线趋势闸门（2026-10-13）：趋势向下记买入侧、向上记卖出侧，作为**门槛偏移**
        # 参与 `buy/sell_threshold_offset`。原 swing 的硬闸门（直接跳过买入判定）
        # 已按用户要求改成门槛，因此这里只存方向、不再短路判定。
        self._trend_down_codes: set[str] = set()
        self._trend_up_codes: set[str] = set()
        self.daily_trend_date: str = ""        # 趋势判定基于哪个交易日
        # 极端利空禁买名单（2026-10-13）：由 workflow 盘前判定后注入，**只卖不买**。
        self._buy_blocked_codes: set[str] = set()
        # 买入信号价止损的基准与计数（2026-10-14，见 `_buy_signal_break_stop`）。
        # **刻意不随 `before_trading` 清空** —— 持仓跨日时基准必须跟着跨日，
        # 否则隔夜回来第一根 bar 就退化成「无基准 ⇒ 不判定」，止损在最需要它的
        # 场景（隔夜跳空）恰好失效。清理只发生在卖出信号发出时。
        self._buy_signal_prices: dict[str, float] = {}      # code -> 本次建仓的买入信号价
        self._below_buy_signal_counts: dict[str, int] = {}  # code -> 连续低于次数
        # 日内熔断状态（2026-10-14，见 `_sync_signal_state` 与常量注释）。
        # 前两个是**计数**（跨日累计，盈利卖出清零）；后两个是**封锁日期**
        # （记录「在哪一天被封的」，只在当天生效，次日靠日期不等自动失效）。
        # `_sell_fly_watch` 是卖出后的观察位：code -> (卖出日期, 卖出价)。
        self._loss_sell_streak: dict[str, int] = {}          # code -> 连续亏损卖出次数
        self._fly_sell_streak: dict[str, int] = {}           # code -> 连续卖飞次数
        self._buy_block_dates: dict[str, str] = {}           # code -> 买入被封锁的日期
        self._sell_block_dates: dict[str, str] = {}          # code -> 卖出被封锁的日期
        self._sell_fly_watch: dict[str, tuple[str, float]] = {}  # code -> (日期, 卖出价)
        self.xshg = xcals.get_calendar("XSHG", start="2020-01-01")  # 上交所交易日历，用于判断交易日
        # 先加载策略参数再注册因子，保证 init_factors 使用加载后的参数：
        # 优先级 user_strategy.strategy_params > strategy.default_params > cfg/strategy.yaml
        if user_strategy_params is not None or default_params is not None:
            # workflow 已批量查询参数，直接三级选择（含 yaml 兜底），避免重复请求
            self._apply_resolved_params(user_strategy_params, default_params)
        else:
            self.load_strategy_params()
        self.init_factors()

    # ---- 账户回撤（卖出分值来源）----

    def set_account_drawdown(self, drawdown_pct: float) -> None:
        """注入账户当前回撤（%，负值），由 workflow / 回测在调用策略前设置。

        策略本身拿不到账户权益（它只看到单只股票的行情），因此账户级回撤只能由
        外部（`StrategyWorkflow` / `BacktestEngine`）算好注入。未注入时保持 0.0，
        即「无回撤」，`_risk_drawdown_score()` 恒返回 0.0，不改变原有卖出行为。

        :param drawdown_pct: 账户权益自峰值的回撤（%），0 表示无回撤或未启用
        """
        self.account_drawdown_pct = float(drawdown_pct or 0.0)

    def _risk_drawdown_score(self) -> float:
        """账户回撤触发的卖出分值（10 分制，负值）；未触发返回 0.0。

        触发条件是「阈值已配置（负值）」且「账户当前回撤 <= 阈值」。
        阈值取 `RISK_MAX_DRAWDOWN_PCT`，回撤取 `set_account_drawdown` 注入的值。
        """
        threshold: float = float(self.RISK_MAX_DRAWDOWN_PCT or 0.0)
        if threshold >= 0:
            return 0.0
        if float(self.account_drawdown_pct or 0.0) <= threshold:
            return float(self.SELL_RISK_DRAWDOWN_SCORE)
        return 0.0

    # ---- 策略参数加载 ----

    def load_strategy_params(self) -> dict[str, Any]:
        """加载数据库策略参数，按三级优先级回退（自查路径，供独立实例化使用）。

        1. 用户策略参数：通过 user_strategy_id 查询 UserStrategyDao.strategy_params
        2. 策略模板默认参数：通过 strategy_id 查询 StrategyDao.default_params
        3. cfg/strategy.yaml 配置文件（节名见 config_section）

        任一级别查询失败或参数为空时回退到下一级；加载成功后调用
        apply_params 将参数应用到策略属性。

        :return: 最终加载的参数字典
        """
        user_strategy_params: dict[str, Any] | None = self._query_user_strategy_params()
        default_params: dict[str, Any] | None = self._query_default_params()
        self._apply_resolved_params(user_strategy_params, default_params)
        return self.strategy_params

    def _query_user_strategy_params(self) -> dict[str, Any] | None:
        """查询 UserStrategyDao.strategy_params（自查路径，user_strategy_id 为空时返回 None）。

        :return: 用户策略参数字典，查询失败或为空时返回 None
        """
        if not self.user_strategy_id:
            return None
        try:
            # 复用全局 ApiClient（已带 admin fallback token），后台线程可访问所有用户数据
            admin_client: ApiClient = AppContext().api_client
            us_api: UserStrategyApi = UserStrategyApi(admin_client)
            us_data: dict[str, Any] = us_api.get(self.user_strategy_id)
            raw_params: Any = us_data.get("strategy_params") if us_data else None
            if isinstance(raw_params, dict) and raw_params:
                return raw_params
        except Exception as exc:
            self.logger.warning(
                "[%s] 查询用户策略参数失败 user_strategy_id=%s: %s",
                self.strategy_name, self.user_strategy_id, exc,
            )
        return None

    def _query_default_params(self) -> dict[str, Any] | None:
        """查询 StrategyDao.default_params（自查路径，strategy_id 为空时返回 None）。

        :return: 策略模板默认参数字典，查询失败或为空时返回 None
        """
        if not self.strategy_id:
            return None
        try:
            admin_client: ApiClient = AppContext().api_client
            s_api: StrategyApi = StrategyApi(admin_client)
            s_data: dict[str, Any] = s_api.get_by_id(self.strategy_id)
            raw_params: Any = s_data.get("default_params") if s_data else None
            if isinstance(raw_params, dict) and raw_params:
                return raw_params
        except Exception as exc:
            self.logger.warning(
                "[%s] 查询策略模板默认参数失败 strategy_id=%s: %s",
                self.strategy_name, self.strategy_id, exc,
            )
        return None

    def _apply_resolved_params(
        self,
        user_strategy_params: dict[str, Any] | None,
        default_params: dict[str, Any] | None,
    ) -> None:
        """按三级优先级选择参数并应用到策略属性。

        优先级：user_strategy.strategy_params > strategy.default_params > cfg/strategy.yaml。
        选中后保存到 self.strategy_params / self.param_source 并调用 apply_params 映射。

        :param user_strategy_params: 用户策略参数（可为 None）
        :param default_params: 策略模板默认参数（可为 None）
        """
        params: dict[str, Any] = {}
        source: str = ""
        if isinstance(user_strategy_params, dict) and user_strategy_params:
            params = user_strategy_params
            source = f"user_strategy({self.user_strategy_id}).strategy_params"
        elif isinstance(default_params, dict) and default_params:
            params = default_params
            source = f"strategy({self.strategy_id}).default_params"
        else:
            params = self._load_yaml_params()
            source = f"cfg/strategy.yaml[{self._resolve_config_section()}]"

        # 统一解包 {desc, value} 包装格式（strategy.default_params 采用该结构），
        # 保证 apply_params 拿到的是纯值结构
        params = self._normalize_params(params)

        self.strategy_params = params if isinstance(params, dict) else {}
        self.param_source = source
        self._apply_params_with_restore(self.strategy_params)
        self.logger.info(
            "[%s] 策略参数加载完成: source=%s, params=%s",
            self.strategy_name, source, self.strategy_params,
        )

    def _apply_params_with_restore(self, params: dict[str, Any]) -> None:
        """调用子类 `apply_params`，并先还原上一轮被写入的属性。

        子类 `apply_params` 的回填写法是 `params.get(key, self.KEY)`。第一次调用
        时 `self.KEY` 读的是类属性，但赋值之后 `self.KEY` 变成实例属性；第二次调用
        （参数热更新）就回填成**上一次的值**而不是类默认值 —— 用户在界面上删掉某个
        参数（期望恢复默认）会静默失效，参数只增不减。

        这里在每轮 apply 之前，把上一轮写进来的属性还原成「上一轮 apply 之前的
        样子」（通常是删掉，让类属性重新可见），使每轮 apply 都以类默认值为基准。

        按「上一轮 diff 出来的键」而不是按类属性全集还原：类属性里混着
        `strategy_id`、`user_strategy_id` 这类**构造参数**，按全集删除会破坏实例状态。

        :param params: 已解包为纯值的参数字典
        """
        previous: dict[str, tuple[bool, Any]] = (
            getattr(self, "_param_overrides", None) or {}
        )
        for name, (existed, old_value) in previous.items():
            if existed:
                self.__dict__[name] = old_value
            else:
                self.__dict__.pop(name, None)

        before_keys: set[str] = set(self.__dict__)
        self.apply_params(params)
        # apply_params 写入的新键即「参数属性」，记录足够还原它们的信息
        self._param_overrides = {
            name: (False, None) for name in self.__dict__ if name not in before_keys
        }

    def reload_params(
        self,
        user_strategy_params: dict[str, Any] | None = None,
        default_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """按最新配置重新解析并应用策略参数（参数热更新入口）。

        与构造期走同一条 `_apply_resolved_params`，保证「启动时加载」与「运行中
        重载」不会出现两套解析规则（包括「用户策略参数非空时整体忽略模板默认值」
        这个择一语义）。加载后重新调用 `init_factors`，因为子类可能按参数决定注册
        哪些因子。

        **只换参数，不动实例的其它运行状态** —— 分钟缓存、区间最高价、账户回撤
        峰值、持仓都保留。这是与「重建实例」的关键差别：重建会把当日缓存清空，
        盘中换参数等于当天已经跑过的行情全部作废。

        :param user_strategy_params: 用户策略 strategy_params（None/空表示回落到下一级）
        :param default_params: 策略模板 default_params
        :return: 应用后的参数字典
        """
        self._apply_resolved_params(user_strategy_params, default_params)
        self.init_factors()
        # 打分缓存 key 是 score:{usid}:{code}:{action}:{day}，不含参数，同一天内
        # 换参数后旧参数算出的分仍会命中缓存（只在部分股票上表现为「改了没生效」）。
        # 这里清该用户的打分缓存（内存 + Redis），分钟行情与区间最高价保留。
        self._cache.clear_user_scores(self.user_strategy_id)
        return self.strategy_params

    @staticmethod
    def _normalize_params(params: dict[str, Any]) -> dict[str, Any]:
        """递归解包 {desc, value} 包装格式为纯值。

        数据库 strategy.default_params 采用 {"desc": ..., "value": 实际值} 结构描述参数，
        而 user_strategy.strategy_params 与 cfg/strategy.yaml 采用纯值结构。
        此方法将两种格式统一为纯值，保证 apply_params 拿到统一结构。

        :param params: 参数字典（可能是包装格式或纯值格式）
        :return: 解包后的纯值参数字典
        """
        if not isinstance(params, dict):
            return params
        result: dict[str, Any] = {}
        for key, val in params.items():
            if isinstance(val, dict) and "value" in val:
                # 参数描述格式 {"desc": ..., "value": 实际值} → 取实际值
                result[key] = val.get("value")
            elif isinstance(val, dict):
                result[key] = BaseStrategy._normalize_params(val)
            else:
                result[key] = val
        return result

    def apply_params(self, params: dict[str, Any]) -> None:
        """将参数字典应用到策略属性，子类可覆盖实现参数映射。

        默认实现不做属性映射（参数已保存在 self.strategy_params 中）；
        子类覆盖时应使用 params.get(key, 当前属性值) 的方式回填，
        保证参数缺失时保留类属性默认值。

        :param params: 策略参数字典
        """
        # 默认无属性映射，参数已保存在 self.strategy_params 中

    def _load_yaml_params(self) -> dict[str, Any]:
        """从 cfg/strategy.yaml 加载本策略配置节的参数。

        配置节名优先取 config_section 类属性，未设置时按类名蛇形命名推导
        （如 StrongReboundStrategy -> strong_rebound_strategy）。

        :return: 配置节参数字典，文件或配置节不存在时返回空字典
        """
        section: str = self._resolve_config_section()
        try:
            from utils.tools import resource_path
            config_path: str = resource_path("cfg/strategy.yaml")
            with open(config_path, "r", encoding="utf-8") as f:
                config: Any = yaml.safe_load(f)
            if not isinstance(config, dict):
                return {}
            section_params: Any = config.get(section, {})
            return section_params if isinstance(section_params, dict) else {}
        except FileNotFoundError:
            self.logger.warning("配置文件 cfg/strategy.yaml 未找到，使用默认参数")
            return {}
        except Exception as exc:
            self.logger.error("加载配置文件 cfg/strategy.yaml 失败: %s", exc)
            return {}

    def _resolve_config_section(self) -> str:
        """解析策略在 cfg/strategy.yaml 中的配置节名。

        :return: 配置节名，如 strong_rebound_strategy
        """
        if self.config_section:
            return self.config_section
        return self._class_name_to_snake()

    def _class_name_to_snake(self) -> str:
        """将类名转换为蛇形命名（StrongReboundStrategy -> strong_rebound_strategy）。

        :return: 蛇形命名的配置节名
        """
        name: str = self.__class__.__name__
        return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

    # ---- 子类必须实现 ----

    @abstractmethod
    def init_factors(self) -> None:
        """子类实现，注册策略所需因子。"""
        raise NotImplementedError

    @abstractmethod
    def is_match_strategy(self, stock_data: list[dict[str, Any]]) -> tuple[bool, dict[str, Any]]:
        """判断输入行情数据是否满足策略信号。

        :param stock_data: 股票行情数据列表，每项为 dict 形式（含 code/close/open 等字段）
        :return: (是否匹配, 详细信息字典)
        """
        raise NotImplementedError

    def before_trading(self, trade_date: str, stock_codes: list[str], **kwargs: Any) -> None:
        """每天交易前准备工作，子类可覆盖。

        默认清理选股列表与分钟级缓存，保证新一天以干净状态开始。

        :param trade_date: 交易日日期字符串 YYYY-MM-DD
        :param stock_codes: 当日关注的股票代码列表
        :param kwargs: 扩展参数（如初始资金、仓位配置等）
        """
        # 清理本用户策略的打分与选股缓存（行情缓存按日期存放，由 workflow 维护，此处不动）
        self._cache.clear_user(self.user_strategy_id)
        # 新的一天重新尝试 Redis（前一天可能因抖动被标记为不可用）
        self._cache.reset_redis_failure()
        self._highest_prices.clear()
        self.logger.debug(
            "[%s] before_trading(trade_date=%s, codes=%d)",
            self.strategy_name, trade_date, len(stock_codes),
        )

    def handle_minute_bar(self, code: str, position: dict[str, Any], stock_data: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """
        处理分钟行情：先执行 _before_minute_route 钩子，再按选股列表分发到买卖检查。
        子类可重载此函数
        """
        self._before_minute_route(code, stock_data)
        select_stocks: set[str] = self._load_select_stocks(stock_data.get("create_time", ""))
        # 如果股票在选股列表中且当前没有持仓，则检查买入条件；否则检查卖出条件。
        #
        # 收尾统一走 `_sync_signal_state`：它既同步止损基准，也执行日内熔断的封锁
        # 判定。被封锁的买入信号返回 False，与「没匹配到」同路，落到下面的卖出
        # 分支上去 —— 不能在这里提前 return：实时链路恒传空持仓，持仓股的卖出
        # 判定正是靠「买入不成立 ⇒ 继续走卖出分支」才跑得到的。
        #
        # 但落到卖出分支后 `extra` 会被卖出判定的结果覆盖，熔断原因就丢了，引擎
        # 日志里只会看到「卖出条件不满足」，与「没信号」无法区分。所以单独留存，
        # 在卖出也没信号时把它还回去（有卖出信号则以外层卖出结果为准）。
        blocked_buy: dict[str, Any] | None = None
        if code in select_stocks and len(position) == 0:
            match, extra = self.check_minute_buy(code, stock_data)
            match, extra = self._sync_signal_state(code, match, extra, stock_data)
            if match:
                return match, extra
            if extra.get("intraday_blocked") == "buy":
                blocked_buy = extra
        matched, extra = self.check_minute_sell(code, position, stock_data)
        matched, extra = self._sync_signal_state(code, matched, extra, stock_data)
        if blocked_buy is not None and not matched:
            return False, blocked_buy
        return matched, extra

    def _before_minute_route(self, code: str, stock_data: dict[str, Any]) -> None:
        """分钟路由前钩子：子类可在此保存分钟缓存等。"""
        pass

    def handle_tick_bar(self, code: str, position: dict[str, Any], stock_data: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """处理实时 tick 行情：按选股列表分发到买入/卖出检查。"""
        select_stocks: set[str] = self._load_select_stocks(stock_data.get("create_time", ""))
        matched: bool
        extra: dict[str, Any]
        if code in select_stocks:
            matched, extra = self.check_tick_buy(code, stock_data)
        else:
            matched, extra = self.check_tick_sell(code, position, stock_data)
        return self._sync_signal_state(code, matched, extra, stock_data)

    def check_minute_buy(self, code: str, stock_data: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """分钟级买入检查，默认委托给子类的 _check_buy_conditions。"""
        return self._check_buy_conditions(code, stock_data)

    def check_minute_sell(self, code: str, position: dict[str, Any], stock_data: dict[str, Any]) -> tuple[bool, dict]:
        """分钟级卖出检查，默认委托给子类的 _check_sell_conditions。"""
        return self._check_sell_conditions(code, position, stock_data)

    def check_tick_buy(self, code: str, stock_data: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """实时 tick 买入检查：默认不触发，子类可按需覆盖。

        :param stock_data: 多股票实时行情列表，每项为 StockRealTimeDao 的 dict 形式
        :return: (是否触发买入, 详细信息)
        """
        return False, {}

    def check_tick_sell(self, code: str, position: dict[str, Any], stock_data: dict[str, Any]) -> tuple[bool, dict]:
        """实时 tick 卖出检查：默认不触发，子类可按需覆盖。"""
        return False, {}

    def _check_buy_conditions(
        self, code: str, stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """当日分钟级买入条件检查，子类实现；默认不触发。"""
        return False, {
            "reason": f"{self.strategy_name} 未实现 _check_buy_conditions",
            "code": code,
        }

    def _check_sell_conditions(
        self, code: str, position: dict[str, Any], stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """当日分钟级卖出条件检查，子类实现；默认不触发。"""
        return False, {
            "reason": f"{self.strategy_name} 未实现 _check_sell_conditions",
            "code": code,
        }

    def _save_score_cache(
        self,
        code: str,
        action: str,
        score: float,
        check_num: int,
        create_time: str,
        done: bool | None = None,
    ) -> None:
        """缓存当天的买入/卖出积分，分钟实时检测时，每次检测积分可以累计。

        实际存储交由 UserStrategyCache（key 含 user_strategy_id，多用户互相隔离），
        内存快照 + Redis 双重缓存，Redis 不可用时自动降级为纯内存。

        :param code: 股票代码
        :param action: BUY / SELL
        :param score: 策略对股票的累计打分
        :param check_num: 累计检查次数（用于计算平均分）
        :param create_time: 行情时间，用于推导日期
        :param done: 已买入/卖出标记
        """
        avg_score: float = score / check_num if check_num > 0 else 0.0
        entry: dict[str, Any] = {
            "action": action,
            "score": float(score),
            "avg_score": float(avg_score),
            "create_time": create_time,
            "check_number": check_num,
            "done": bool(done) if done is not None else None,
        }
        self._cache.save_score(
            self.user_strategy_id,
            code,
            action,
            entry,
            UserStrategyCache.day_of(create_time),
        )

    def _get_score_cache(self, code: str, action: str, create_time: str) -> dict:
        """从缓存取出当天的买入/卖出积分，分钟实时检测时，每次检测积分可以累计。

        key 由 user_strategy_id + code + action + 日期 组成，多用户互相隔离。

        :param code: 股票代码
        :param action: BUY / SELL
        :param create_time: 行情时间，用于推导日期
        :return: 打分条目，无记录返回空字典
        """
        return self._cache.get_score(
            self.user_strategy_id,
            code,
            action,
            UserStrategyCache.day_of(create_time),
        )

    def _save_minute_cache(
        self,
        code: str,
        stock_data: dict[str, Any],
        max_cache: int = UserStrategyCache.MAX_MINUTE_CACHE,
    ) -> None:
        """缓存一根分钟行情。

        **注意**：行情缓存现在统一由 `StrategyWorkflow` 单点写入（每个 code 只写一次），
        策略侧只读。保留本方法仅为兼容既有调用点/测试，正常链路不应再由策略调用。

        :param code: 股票代码
        :param stock_data: 分钟行情数据
        :param max_cache: 单只股票最大缓存条数
        """
        self._cache.push_minute_bar(code, stock_data, max_cache=max_cache)

    @staticmethod
    def _minute_cache_key(code: str, day: str) -> str:
        """构造分钟行情缓存的 key：bar:{code}:{day}。

        :param code: 股票代码
        :param day: 日期字符串 YYYY-MM-DD
        :return: key 字符串
        """
        return UserStrategyCache.minute_key(code, day)

    def _get_minute_cache(self, code: str, length: int = 0) -> list[dict[str, Any]]:
        """读取 code 当日分钟行情。

        数据由 `UserStrategyCache` 提供（进程内快照优先，未命中才读 Redis 并回填），
        同一进程中同一 (code, 日期) 最多只访问一次 Redis，避免单次检查内反复 IO。

        :param code: 股票代码
        :param length: 需要的条数；<=0 表示取当日全部
        :return: 分钟行情条目列表（时间正序），无数据返回空列表
        """
        return self._cache.get_minute_bars(code, length=length)

    """
    function: get_previous_trading_day
    description: 获取指定日期的前一个交易日
    param {*} self
    param {str} date_str
    return {*}
    """    
    def get_previous_trading_day(self, today_str: str, days: int = 1) -> str:
        time_str: str = ""
        if len(today_str) >= 11:
            time_str = today_str[10:].strip()
            date = datetime.datetime.strptime(today_str, "%Y-%m-%d %H:%M:%S").date()
        else:
            date = datetime.datetime.strptime(today_str, "%Y-%m-%d").date()
        while True:
            date -= datetime.timedelta(days=1)
            if self.xshg.is_session(date):
                days -= 1
                if days == 0:
                    if len(time_str) > 0:
                        return date.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        return date.strftime("%Y-%m-%d")

    """
    function: get_recent_trading_day
    description: 获取最近一个交易日
    param {*} self
    return {*}
    """    
    def get_recent_trading_day(self) -> str:
        today = datetime.datetime.now().date()
        while True:
            if self.xshg.is_session(today):
                return today.strftime("%Y-%m-%d")
            today -= datetime.timedelta(days=1)

    # ---- 打分与星级 ----

    STAR_MAX: float = 5.0
    """星级上限。服务端 `TradeSignalCreateRequest.star` 的字段约束是 0~5.0
    （`web_api/routers/trade_signal.py::_validate_star_value`，越界直接 400）。"""

    @classmethod
    def score_to_star(cls, score: float) -> float:
        """把 10 分制打分折算成 5 星制星级，**夹到 [0, 5]** 并保留一位小数。

        为什么必须在策略侧夹：服务端对 `star` 做了范围校验，越界返回 400，
        而 `_save_trade_signal` 里 `add()` 一旦抛异常，整条信号连同
        MQTT 推送、企业微信/短信推送、订单创建会**一起被跳过**（同一个 try 块）。
        也就是说一个越界的星级会让深跌卖出信号彻底丢失，不只是「星级记错」。

        卖出侧尤其容易越界：分值是「技术分 + 持仓回撤扣分(+账户回撤扣分)」的叠加，
        波段策略实测可到 −12.25（`(枢轴 −5 + 布林 −7.5)/2 −6`），
        按 `−score/2` 折算得 6.1 星，超过上限。
        （2026-10-12 线上 400 的直接原因。）

        :param score: 10 分制打分（买入为正、卖出为负）
        :return: 0~5 的星级，保留一位小数
        """
        value: float = float(score or 0.0)
        if value != value:  # NaN：夹取对它无效，显式落 0
            return 0.0
        return round(max(0.0, min(abs(value) / 2.0, cls.STAR_MAX)), 1)

    # ---- 每日阀门与市场偏移（门槛换算）----

    VALVE_ENABLED: bool = True
    """是否启用盘后个股阀门。关闭时 `buy_threshold_offset` 恒返回市场偏移部分。"""

    VALVE_STEP: float = 1.0
    """阀门**单档**的上调幅度（10 分制）；实际幅度 = `VALVE_STEP × 最深触发档序号`
    （破 S1 ⇒ 1×、破 S2 ⇒ 2×、破 S3 ⇒ 3×，档位不叠加）。"""

    VALVE_MIN_BREAKS: int = 2
    """判定「反复跌穿/站上」所需的最少分钟数（各档同一阈值）。"""

    VALVE_MAX: float = 5.0
    """单侧阀门上限，避免异常数据把门槛抬到不可达。"""

    VALVE_STOP_TIER: int = 3
    """阀门达到该档序号时视为「跌穿 S3 / 站上 R3」（2026-10-13 新增）。

    与市场极端条件（前一交易日跌停家数超过 `stop_buy_limit_down`）**同时成立**
    才触发禁买，见 `is_buy_blocked` 与 workflow 的 `_apply_daily_gates`。
    触发值是 `VALVE_STOP_TIER × VALVE_STEP`（默认 3.0）。
    """

    TREND_GATE_ENABLED: bool = True
    """日线趋势闸门总开关（2026-10-13 新增）。关闭时趋势不产生门槛偏移。"""

    TREND_BUY_OFFSET: float = 1.0
    """日线趋势**向下**时的买入门槛上调幅度（10 分制，正 = 更难买）。

    语义：下跌趋势里支撑位会被逐个跌穿，贴着支撑位买入等于逆势接刀。
    2026-09-29 原实现是**硬闸门**（趋势向下的股票当天直接跳过买入判定），
    2026-10-13 按用户要求改成**门槛** —— 只抬高、不封死，极强信号仍可买。
    """

    TREND_SELL_OFFSET: float = 1.0
    """日线趋势**向上**时的卖出门槛上调幅度（10 分制，正 = 更难卖）。

    语义：上升趋势里阻力位会被逐个突破，「贴近阻力位」不再等于「该卖」，
    抬高门槛避免卖飞。与买入侧方向相反、口径对称。
    """

    BLOCK_BUY_ENABLED: bool = True
    """极端利空禁买总开关（2026-10-13 新增）。关闭时忽略盘前注入的禁买名单。"""

    TREND_DAYS_LIMIT: int = 14
    """日线趋势判定的 ADX 周期（基类默认值，子类可覆盖）。"""

    TREND_ADX_THRESHOLD: float = 25.0
    """ADX 强度门槛：达到才算「趋势确立」（基类默认值，子类可覆盖）。"""

    TREND_KLINE_DAYS: int = 40
    """趋势判定所需日K根数（ADX lookback = 2×周期，基类默认值）。"""

    SR_LEVEL_KEYS: tuple[str, ...] = ("s1", "s2", "s3", "r1", "r2", "r3")
    """枢轴点的六个档位键（浅 → 深），阀门计算与入库共用同一顺序。"""

    def set_daily_valves(
        self, valves: dict[str, dict[str, Any]], trade_date: str = ""
    ) -> None:
        """注入当日个股阀门（盘前由 workflow 从服务端读取后调用）。

        :param valves: ``{code: {"buy_valve": float, "sell_valve": float}}``
        :param trade_date: 阀门来源交易日（仅用于日志/可追溯）
        """
        buy_map: dict[str, float] = {}
        sell_map: dict[str, float] = {}
        limit: float = abs(float(self.VALVE_MAX))
        for code, item in (valves or {}).items():
            if not code or not isinstance(item, dict):
                continue
            buy_valve: float = abs(float(item.get("buy_valve", 0.0) or 0.0))
            sell_valve: float = abs(float(item.get("sell_valve", 0.0) or 0.0))
            buy_map[str(code)] = min(buy_valve, limit)
            sell_map[str(code)] = min(sell_valve, limit)
        self._daily_buy_valve = buy_map
        self._daily_sell_valve = sell_map
        self.daily_valve_date = str(trade_date or "")
        if buy_map or sell_map:
            self.logger.info(
                "[%s] 已加载每日阀门 %d 只 (来源交易日 %s)",
                self.user_strategy_name, max(len(buy_map), len(sell_map)),
                self.daily_valve_date or "?",
            )

    def clear_daily_valves(self) -> None:
        """清空个股阀门（市场偏移不受影响）。"""
        self._daily_buy_valve = {}
        self._daily_sell_valve = {}
        self.daily_valve_date = ""

    def set_market_offset(self, buy_offset: float, sell_offset: float) -> None:
        """注入市场级门槛偏移（盘前按涨跌停家数算，见 utils/market_breadth.py）。

        :param buy_offset: 买入门槛偏移，``>0`` 表示更难买
        :param sell_offset: 卖出门槛偏移，``>0`` 表示更难卖、``<0`` 表示更容易卖
        """
        self._market_buy_offset = float(buy_offset or 0.0)
        self._market_sell_offset = float(sell_offset or 0.0)

    def buy_threshold_offset(self, code: str) -> float:
        """买入门槛的额外偏移量（正 = 更难买）。

        三项相加，都是**收紧**方向：

        1. 个股买入阀门（盘后算、次日生效，见 `strategy/valve.py`）；
        2. 市场偏移（前一交易日跌停家数分档，见 `utils/market_breadth.py`）；
        3. 日线趋势向下（`TREND_BUY_OFFSET`，2026-10-13 由原硬闸门改成门槛）。
        """
        stock: float = float(self._daily_buy_valve.get(str(code), 0.0)) if self.VALVE_ENABLED else 0.0
        return stock + float(self._market_buy_offset) + self.daily_trend_buy_offset(code)

    def sell_threshold_offset(self, code: str) -> float:
        """卖出门槛的额外偏移量（正 = 更难卖）。

        个股卖出阀门（卖飞了就抬门槛）与市场偏移（跌停多时取负 ⇒ 更容易卖，
        风控优先）是两套独立口径；2026-10-13 再叠加日线趋势向上项
        （`TREND_SELL_OFFSET`，趋势向上时不轻易卖飞）。
        """
        stock: float = float(self._daily_sell_valve.get(str(code), 0.0)) if self.VALVE_ENABLED else 0.0
        return stock + float(self._market_sell_offset) + self.daily_trend_sell_offset(code)

    # ---- 日线趋势闸门（门槛口径，2026-10-13） ----

    def set_daily_trend(
        self,
        down_codes: Iterable[str],
        up_codes: Iterable[str],
        trade_date: str = "",
    ) -> None:
        """注入当日日线趋势方向（盘前由子类判定）。

        :param down_codes: 日线趋势向下的股票（次日买入门槛上调）
        :param up_codes: 日线趋势向上的股票（次日卖出门槛上调）
        :param trade_date: 判定基于哪个交易日（仅用于日志/可追溯）
        """
        self._trend_down_codes = {str(c) for c in (down_codes or ()) if c}
        self._trend_up_codes = {str(c) for c in (up_codes or ()) if c}
        self.daily_trend_date = str(trade_date or "")
        if self._trend_down_codes or self._trend_up_codes:
            self.logger.info(
                "[%s] 日线趋势门槛：向下 %d 只（买入门槛 +%.1f）/ 向上 %d 只（卖出门槛 +%.1f）",
                self.user_strategy_name,
                len(self._trend_down_codes), self.TREND_BUY_OFFSET,
                len(self._trend_up_codes), self.TREND_SELL_OFFSET,
            )

    def clear_daily_trend(self) -> None:
        """清空日线趋势方向。"""
        self._trend_down_codes = set()
        self._trend_up_codes = set()
        self.daily_trend_date = ""

    def add_daily_trend(self, code: str, direction: int) -> None:
        """**增量**记录一只股票的趋势方向（``-1`` 向下 / ``1`` 向上 / 其余忽略）。

        与 `set_daily_trend` 的区别：那个是整体替换（盘前批量判定用），这个是
        单只追加（选股链路用）。选股只能看到手里这只股票，用替换接口会把
        盘前批量判定的结果整个抹掉。

        方向集合是**唯一真源** —— 策略不得再维护自己的副本（``_downtrend_codes``
        那类私有集合曾与这里并存，改一处忘一处就静默失效：门槛读基类集合、
        填的是私有集合，偏移恒为 0 且没有任何报错）。
        """
        key: str = str(code or "")
        if not key:
            return
        if direction < 0:
            self._trend_down_codes.add(key)
            self._trend_up_codes.discard(key)
        elif direction > 0:
            self._trend_up_codes.add(key)
            self._trend_down_codes.discard(key)

    def daily_trend_direction(self, code: str) -> int:
        """该股当日日线趋势方向：``-1`` 向下 / ``1`` 向上 / ``0`` 无趋势或未判定。"""
        key: str = str(code)
        if key in self._trend_down_codes:
            return -1
        if key in self._trend_up_codes:
            return 1
        return 0

    def daily_trend_buy_offset(self, code: str) -> float:
        """日线趋势带来的买入门槛偏移（趋势向下 ⇒ 正 = 更难买；否则 0）。"""
        if not self.TREND_GATE_ENABLED:
            return 0.0
        if str(code) in self._trend_down_codes:
            return float(self.TREND_BUY_OFFSET)
        return 0.0

    def daily_trend_sell_offset(self, code: str) -> float:
        """日线趋势带来的卖出门槛偏移（趋势向上 ⇒ 正 = 更难卖；否则 0）。"""
        if not self.TREND_GATE_ENABLED:
            return 0.0
        if str(code) in self._trend_up_codes:
            return float(self.TREND_SELL_OFFSET)
        return 0.0

    def _daily_trend_direction(self, detail: dict[str, Any]) -> int:
        """由 ADX 明细判定趋势方向：``-1`` 向下 / ``1`` 向上 / ``0`` 判不出。

        方向成立需要两个条件同时满足（与 swing 原 `_is_downtrend` 同一口径）：

        - ``ADX >= TREND_ADX_THRESHOLD``：只说明「有趋势」，不足以定向；
        - ``-DI`` 与 ``+DI`` 的大小关系才是方向。

        只看 DI 交叉会把震荡市里随机交叉的股票剔掉一半；ADX / DI 为 NaN
        （数据不足、次新股、停牌复牌）时比较恒为 False ⇒ 判不出趋势 ⇒ 不偏移。
        """
        adx: float = float(detail.get("adx") or 0.0)
        if pd.isna(adx) or adx < float(self.TREND_ADX_THRESHOLD):
            return 0
        plus_di: float = float(detail.get("+DI") or 0.0)
        minus_di: float = float(detail.get("-DI") or 0.0)
        if pd.isna(plus_di) or pd.isna(minus_di):
            return 0
        if minus_di > plus_di:
            return -1
        if plus_di > minus_di:
            return 1
        return 0

    def _adx_detail(self, klines: pd.DataFrame) -> dict[str, Any]:
        """日线 ADX 明细（方向 + 强度）；数据不足或计算异常时返回空字典。

        返回空字典表示「判不出趋势」，调用方按「不构成趋势」处理 ——
        数据缺口不该被当成趋势信号把股票整个禁掉。
        """
        from factor.adx_trend_factor import AdxTrendFactor

        if klines is None or len(klines) < 2 * int(self.TREND_DAYS_LIMIT):
            return {}
        try:
            return AdxTrendFactor(days=self.TREND_DAYS_LIMIT).adx_trend(klines)
        except Exception as exc:  # noqa: BLE001 - defensive
            self.logger.warning(
                "[%s] 日线趋势计算失败: %s", self.user_strategy_name, exc
            )
            return {}

    def _trend_kline_days(self) -> int:
        """趋势判定所需日K根数（至少 2×ADX周期 + 1）。

        ADX 的 lookback 是 `2×period`（talib 口径），少一根末值就是 NaN，
        而 `NaN > 阈值` 恒为 False ⇒ 判不出趋势 ⇒ 门槛静默失效，故取够再留余量。
        """
        return max(
            int(self.TREND_KLINE_DAYS),
            2 * int(self.TREND_DAYS_LIMIT) + 1,
        )

    def load_daily_trend(self, stock_codes: Iterable[str], trade_date: str) -> None:
        """盘前按日K判定股票池的趋势方向并注入（供子类 `before_trading` 调用）。

        取数失败 / 数据不足一律按「判不出趋势」处理（不偏移）—— 门槛是**附加收紧项**，
        缺失时不该改变原有行为。

        :param stock_codes: 当日股票池
        :param trade_date: 交易日 YYYY-MM-DD
        """
        down: set[str] = set()
        up: set[str] = set()
        days: int = self._trend_kline_days()
        for code in stock_codes or ():
            if not code:
                continue
            try:
                klines: pd.DataFrame = self.load_his_daily_data(code, trade_date, days)
            except Exception as exc:  # noqa: BLE001 - defensive
                self.logger.warning(
                    "[%s] 趋势判定取日K失败 code=%s: %s", self.user_strategy_name, code, exc
                )
                continue
            direction: int = self._daily_trend_direction(self._adx_detail(klines))
            if direction < 0:
                down.add(str(code))
            elif direction > 0:
                up.add(str(code))
        self.set_daily_trend(down, up, trade_date)

    # ---- 极端利空禁买（2026-10-13） ----

    def set_buy_blocked(self, codes: Iterable[str]) -> None:
        """注入当日禁买名单（整体替换，避免跨日累积）。

        语义是「只卖不买」：买入判定直接不成立，卖出与风控判定照常执行。
        """
        self._buy_blocked_codes = {str(c) for c in (codes or ()) if c}
        if self._buy_blocked_codes:
            # 记 INFO 而非 WARNING：触发原因（市场跌停家数、破 S3 只数）由调用方
            # `StrategyWorkflow._apply_buy_block` 记 WARNING，这里只登记名单。
            self.logger.info(
                "[%s] 禁买名单 %d 只（当日只卖不买）：%s",
                self.user_strategy_name, len(self._buy_blocked_codes),
                ",".join(sorted(self._buy_blocked_codes)[:10]),
            )

    def clear_buy_blocked(self) -> None:
        """清空禁买名单。"""
        self._buy_blocked_codes = set()

    def is_buy_blocked(self, code: str) -> bool:
        """该股当日是否禁止买入（见 `set_buy_blocked`）。"""
        if not self.BLOCK_BUY_ENABLED:
            return False
        return str(code) in self._buy_blocked_codes


    # ---- 盘后阀门计算 ----

    def compute_daily_valve(
        self,
        code: str,
        trade_date: str,
        buy_signal_times: list[str],
        sell_signal_times: list[str],
    ) -> dict[str, Any]:
        """盘后计算单只股票当日阀门（语义见 strategy/valve.py 模块 docstring）。

        基础实现 = 「当日分钟缓存 + 上一交易日枢轴点 S1/S2/S3、R1/R2/R3」：
        买入信号后收盘价**跌破的最深支撑档**决定次日买入门槛上调幅度
        （破 S1 ⇒ +`VALVE_STEP`，破 S2 ⇒ +2×、破 S3 ⇒ +3×）；
        卖出信号后站上的最深阻力档决定次日卖出门槛上调幅度，口径对称。

        数据不足（无分钟缓存 / 取不到日K）时返回空字典 —— 调用方按「无阀门」处理。
        阀门是**附加收紧项**，缺失时不该改变原有门槛（fail-open），否则一次取数
        抖动就会静默放宽/收紧整个策略。

        :param code: 股票代码
        :param trade_date: 交易日 YYYY-MM-DD
        :param buy_signal_times: 当日买入信号时间列表
        :param sell_signal_times: 当日卖出信号时间列表
        :return: 阀门结果字典；数据不足时为空字典
        """
        if not self.VALVE_ENABLED or not code:
            return {}
        points: list[tuple[str, float]] = []
        for bar in self._get_minute_cache(code):
            ts: str = str(bar.get("create_time") or "")
            raw: Any = bar.get("close", bar.get("price"))
            if not ts or raw is None:
                continue
            try:
                points.append((ts, float(raw)))
            except (TypeError, ValueError):
                continue
        if not points:
            return {}
        levels: dict[str, float] = self._daily_sr_levels(code, trade_date)
        from strategy.valve import compute_stock_valve

        result: dict[str, Any] = compute_stock_valve(
            points,
            buy_signal_times,
            sell_signal_times,
            support=levels["s1"],
            resistance=levels["r1"],
            step=self.VALVE_STEP,
            min_breaks=self.VALVE_MIN_BREAKS,
            max_valve=self.VALVE_MAX,
            support_tiers=[levels["s1"], levels["s2"], levels["s3"]],
            resistance_tiers=[levels["r1"], levels["r2"], levels["r3"]],
        )
        for key in self.SR_LEVEL_KEYS:
            result[key] = round(float(levels[key]), 4)
        return result

    def _daily_sr_levels(self, code: str, trade_date: str) -> dict[str, float]:
        """取当日枢轴点的三档支撑位与三档阻力位（基于上一交易日日线）。

        :return: ``{"s1","s2","s3","r1","r2","r3"}``；取数/计算失败时六项全为
            ``0.0``（0 会被 `count_level_breaks` 判为无效点位，等价于不做判定）
        """
        empty: dict[str, float] = {key: 0.0 for key in self.SR_LEVEL_KEYS}
        from factor.in_day_support_resistance import InDaySRFactor

        klines: pd.DataFrame
        try:
            klines = self.load_his_daily_data(code, trade_date, 3)
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.warning(
                "[%s] 阀门取日K失败 code=%s: %s", self.user_strategy_name, code, exc
            )
            return empty
        if klines is None or klines.empty:
            return empty
        try:
            points = InDaySRFactor().pivot_points(klines)
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.warning(
                "[%s] 阀门算枢轴点失败 code=%s: %s", self.user_strategy_name, code, exc
            )
            return empty
        if points is None or points.empty:
            return empty
        last = points.iloc[-1]

        def _to_float(value: Any) -> float:
            try:
                result = float(value)
            except (TypeError, ValueError):
                return 0.0
            if result != result:  # NaN
                return 0.0
            return result

        return {key: _to_float(last.get(key, 0.0)) for key in self.SR_LEVEL_KEYS}

    # ---- 涨跌停幅度与阈值 ----

    @staticmethod
    def price_limit_pct(code: str, name: str = "") -> float:
        """返回该股票的涨跌停幅度（%）。

        原实现把「接近涨跌停」硬编码成 ±9%（按 10% 涨跌停留 1% 余量），
        对池内的创业板/科创板（20%）与 ST（5%）都会误判：
        创业板涨到 +9.5% 会被当成「接近涨停」而挡掉卖出，
        ST 封在 +5% 却仍会去挂卖单。

        判定依据（按优先级）：
        - 名称含 `ST`（含 `*ST`）→ 5%
        - 代码前缀 300/301/302（创业板）、688/689（科创板）→ 20%
        - 其余（主板、中小板）→ 10%

        :param code: 股票代码
        :param name: 股票名称（分钟/实时行情里的 `name` 字段），为空时不判断 ST
        :return: 涨跌停幅度（%）
        """
        text: str = str(name or "").upper().replace(" ", "")
        if "ST" in text:
            return 5.0
        prefix: str = str(code or "").strip()
        if prefix[:3] in ("300", "301", "302", "688", "689"):
            return 20.0
        return 10.0

    def _near_price_limit_pct(
        self, code: str, name: str = "", margin: float = 1.0
    ) -> float:
        """接近涨跌停的判定阈值（%）= 涨跌停幅度 − 余量（默认 1 个百分点）。

        :param code: 股票代码
        :param name: 股票名称
        :param margin: 距离涨跌停价的余量（百分点）
        :return: 涨跌停幅度减余量，最低 1.0（避免余量大于幅度时得到非正数）
        """
        return max(self.price_limit_pct(code, name) - abs(margin), 1.0)

    @staticmethod
    def _change_percent(
        stock_data: dict[str, Any], current_price: float, preclose: float
    ) -> float:
        """取当前涨跌幅（百分数，如 −3.25 表示跌 3.25%）。

        **量纲约定**：行情快照里的 `change_percent` 是**小数比率**（0.0325 = +3.25%），
        CLI 两处渲染与小程序接口契约都按比率处理，因此这里统一 ×100。
        若换了实时数据源而该字段变成百分数，这里会立刻退化成
        「任何下跌都不买、任何上涨都不卖」，换源时必须重新核对。

        字段缺失、为 0（服务端部分兜底分支会写 0）或非数字时，用昨收自算。
        `preclose <= 0`（数据不全）时不做除法，直接返回 0，避免除零异常
        —— 原实现直接 `(current_price - preclose) / preclose * 100`，
        被外层 `except Exception` 兜住后表现为反复刷「检查异常」warning。

        :param stock_data: 分钟/实时行情快照
        :param current_price: 当前价
        :param preclose: 昨收价
        :return: 涨跌幅（百分数）
        """
        try:
            raw: Any = stock_data.get("change_percent")
            chg: float = float(raw) * 100.0 if raw is not None else 0.0
        except (TypeError, ValueError):
            chg = 0.0
        if chg == 0.0 and preclose > 0:
            chg = (current_price - preclose) / preclose * 100.0
        return chg

    # ---- 通用方法 ----

    @staticmethod
    def _empty_kline_frame(
        fetch_failed: bool = False, fetch_error: str = ""
    ) -> pd.DataFrame:
        """构造统一列名的空日K表，并用 `attrs` 标记是否属于「取数失败」。

        `attrs` 是 pandas 官方提供的 DataFrame 元数据容器，用它而不是加列，
        是为了不污染下游因子的列判断（`load_his_daily_data` 的返回值会被
        直接喂给因子计算）。

        :param fetch_failed: True 表示这是**取数失败**，而非「查询成功但无数据」
        :param fetch_error: 取数失败原因（供日志/异常消息使用）
        :return: 空 DataFrame，`attrs["fetch_failed"]` 标记失败语义
        """
        df = pd.DataFrame(
            columns=pd.Index(["code", "open", "close", "volume", "create_time"])
        )
        if fetch_failed:
            df.attrs["fetch_failed"] = True
            df.attrs["fetch_error"] = fetch_error
        return df

    def load_his_daily_data(
        self,
        code: str,
        today_str: str,
        days: int,
        retries: int | None = None,
        retry_interval: float | None = None,
        raise_on_error: bool = False,
    ) -> pd.DataFrame:
        """使用行情接口加载指定股票的日线数据，并返回最近 days 条记录。

        **必须带日期查询**：服务端只保留约 3 个月历史行情，超出部分会被清理；
        而它的行情来自第三方，若库里没有该股历史会即时回源第三方拉取
        （可能超时/报错）。因此这里恒定传入 `start` / `end`，
        避免「不带日期」触发的服务端异常分支。

        **取数失败的语义**（见 `MarketDataFetchError`）：
        - 接口抛异常 → 重试 `retries` 次；仍失败时
          `raise_on_error=True` 抛 `MarketDataFetchError`，
          否则返回带 `attrs["fetch_failed"]=True` 的空表 + warning 日志。
        - 接口正常返回但列表为空 → 属于「无数据」，不是取数失败。

        :param code: 股票代码
        :param today_str: 行情时间或日期，为空时取最近交易日
        :param days: 需要返回的日K条数（取数区间会按 2 倍回溯）
        :param retries: 额外重试次数；None 时取类属性 `FETCH_RETRIES`
        :param retry_interval: 重试间隔秒数；None 时取类属性 `FETCH_RETRY_INTERVAL`
        :param raise_on_error: 取数失败时是否抛 `MarketDataFetchError`
            （回测链路必须为 True —— 数据不完整就中止，不产出回测记录）
        :return: 日K DataFrame；取数失败/无数据时返回空表（失败时带 attrs 标记）
        """
        # 计算起始和结束日期，确保至少有足够的历史数据用于因子计算。
        # 结束日期为昨天，起始日期为当前日期向前回溯的天数，至少为 15 天。
        # 日期减一是只取历史数据，不包含当天的未完成交易日数据。
        if not today_str:
            today_str = self.get_recent_trading_day()
        today = datetime.datetime.strptime(today_str[:10], "%Y-%m-%d").date()
        end_date = self.get_previous_trading_day(today.strftime("%Y-%m-%d"))
        # 为了让均线、回撤等因子有足够的前置历史，至少多取一段历史窗口。
        # 这里使用 `days * 2` 作为默认回溯长度，并保证不小于 15 天。
        lookback_days: int = max(days * 2, 15)
        start_date: str = (
            datetime.datetime.strptime(end_date, "%Y-%m-%d") - datetime.timedelta(days=lookback_days)
        ).strftime("%Y-%m-%d")

        market_api = AppContext().market_api
        raw_data: list[dict[str, Any]] = []

        attempts: int = (self.FETCH_RETRIES if retries is None else int(retries)) + 1
        attempts = max(attempts, 1)
        interval: float = (
            self.FETCH_RETRY_INTERVAL if retry_interval is None else float(retry_interval)
        )

        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                if market_api and hasattr(market_api, "get_day_kline"):
                    raw_data = market_api.get_day_kline(
                        code=code,
                        start=start_date,
                        end=end_date,
                    )
                last_exc = None
                break
            except Exception as exc:  # pragma: no cover - defensive
                last_exc = exc
                if attempt < attempts:
                    # 重试过程记 warning：原实现只在 debug 记录，线上等于静默
                    self.logger.warning(
                        "[%s] 日K取数失败(第 %d/%d 次) code=%s %s~%s: %s，%.1fs 后重试",
                        self.strategy_name, attempt, attempts, code,
                        start_date, end_date, exc, max(interval, 0.0),
                    )
                    if interval > 0:
                        time.sleep(interval)

        if last_exc is not None:
            error_msg: str = (
                f"日K取数失败 code={code} range={start_date}~{end_date} "
                f"attempts={attempts}: {last_exc}"
            )
            # 原实现是 logger.debug，线上无法察觉 —— 提升为 warning，
            # 并显式声明这不是「无数据」，避免被误判成「正常不满足条件」
            self.logger.warning(
                "[%s] %s —— 这是**取数失败**，不是「无数据」，"
                "不能当作正常不满足条件忽略",
                self.strategy_name, error_msg,
            )
            if raise_on_error:
                raise MarketDataFetchError(error_msg) from last_exc
            return self._empty_kline_frame(fetch_failed=True, fetch_error=str(last_exc))

        if not raw_data:
            return self._empty_kline_frame()

        df = pd.DataFrame(raw_data)
        if df.empty:
            return self._empty_kline_frame()

        if not df.empty and "create_time" not in df.columns:
            if "date" in df.columns:
                df["create_time"] = df["date"]
            else:
                df["create_time"] = pd.Series(range(len(df)), index=df.index)

        if "code" not in df.columns:
            df["code"] = code

        df = df.copy()
        df = df.tail(max(days, 1))
        return df

    def select(
        self,
        codes: list[str],
        today_str: str,
        days: int = 15,
        retries: int | None = None,
        retry_interval: float | None = None,
        raise_on_error: bool = False,
    ) -> list[dict[str, Any]]:
        """遍历股票代码，加载日线数据并判断策略是否命中。

        每条结果除 `code` / `matched` / `extra` 外，还额外给出三个字段，
        用于把「取数失败」与「正常不满足条件」明确区分开：

        - `score`：策略打分（命中时取 `extra["score"]`，缺失记 0.0）。
          服务端选股表已新增 score 字段，保存选股结果时直接取此值。
        - `fetch_failed`：True 仅表示**行情取数失败**（接口异常，重试后仍失败），
          此时该股**是否满足条件未知**，候选集不完整；
        - `reason_code`：`matched` / `not_matched` / `no_data` / `fetch_failed`
          / `strategy_error`（前两者为正常判定，`no_data` 为接口正常但无数据）。

        :param codes: 股票代码列表
        :param today_str: 行情时间或日期，用于推算日线取数区间
        :param days: 日线窗口长度
        :param retries: 取数失败额外重试次数（None 用类属性默认值）
        :param retry_interval: 重试间隔秒数（None 用类属性默认值）
        :param raise_on_error: True 时遇到取数失败立即抛 `MarketDataFetchError`；
            回测链路必须为 True（数据不完整即中止，绝不产出回测记录）
        :return: 每只股票一条结果字典
        """
        results: list[dict[str, Any]] = []
        for code in codes:
            # ---- 1. 取数：严格区分「取数失败」与「无数据」----
            try:
                df = self.load_his_daily_data(
                    code=code,
                    today_str=today_str,
                    days=days,
                    retries=retries,
                    retry_interval=retry_interval,
                    raise_on_error=raise_on_error,
                )
            except MarketDataFetchError:
                # raise_on_error=True：直接上抛，由回测链路中止并报错
                raise
            except Exception as exc:  # pragma: no cover - defensive fallback
                # 子类重写了 load_his_daily_data 且未按新契约实现时的兜底：
                # 归为取数失败而非「不满足条件」
                self.logger.warning(
                    "[%s] select(%s) 取数异常: %s", self.strategy_name, code, exc
                )
                if raise_on_error:
                    raise MarketDataFetchError(
                        f"日K取数失败 code={code}: {exc}"
                    ) from exc
                results.append(
                    {
                        "code": code,
                        "matched": False,
                        "score": 0.0,
                        "fetch_failed": True,
                        "reason_code": self.REASON_FETCH_FAILED,
                        "extra": {"reason": f"取数失败: {exc}"},
                    }
                )
                continue

            fetch_failed: bool = bool(getattr(df, "attrs", {}).get("fetch_failed", False))
            if fetch_failed:
                error_msg: str = str(
                    getattr(df, "attrs", {}).get("fetch_error", "") or "未知取数错误"
                )
                if raise_on_error:
                    raise MarketDataFetchError(f"日K取数失败 code={code}: {error_msg}")
                results.append(
                    {
                        "code": code,
                        "matched": False,
                        "score": 0.0,
                        "fetch_failed": True,
                        "reason_code": self.REASON_FETCH_FAILED,
                        "extra": {"reason": f"取数失败: {error_msg}"},
                    }
                )
                continue

            if df.empty:
                # 接口正常返回但没有日线数据：属于「无数据」，不是取数失败
                results.append(
                    {
                        "code": code,
                        "matched": False,
                        "score": 0.0,
                        "fetch_failed": False,
                        "reason_code": self.REASON_NO_DATA,
                        "extra": {"reason": "no data"},
                    }
                )
                continue

            # ---- 2. 策略判定 ----
            try:
                matched, extra = self.is_match_strategy(
                    cast(list[dict[str, Any]], df.to_dict(orient="records"))
                )
            except Exception as exc:  # pragma: no cover - defensive fallback
                # 策略自身异常：与取数失败分开标记，便于定位
                self.logger.warning(
                    "[%s] select(%s) is_match_strategy 异常: %s",
                    self.strategy_name, code, exc,
                )
                results.append(
                    {
                        "code": code,
                        "matched": False,
                        "score": 0.0,
                        "fetch_failed": False,
                        "reason_code": self.REASON_STRATEGY_ERROR,
                        "extra": {"reason": str(exc)},
                    }
                )
                continue

            extra_dict: dict[str, Any] = extra if isinstance(extra, dict) else {}
            results.append(
                {
                    "code": code,
                    "matched": bool(matched),
                    "score": self._extract_score(extra_dict),
                    "fetch_failed": False,
                    "reason_code": (
                        self.REASON_MATCHED if matched else self.REASON_NOT_MATCHED
                    ),
                    "extra": extra_dict,
                }
            )

        return results

    @staticmethod
    def _extract_score(extra: dict[str, Any]) -> float:
        """从策略返回的 extra 中提取打分，容错处理缺失/None/非数值。

        :param extra: 策略 is_match_strategy 返回的附加信息
        :return: 打分（float），取不到时返回 0.0
        """
        try:
            value: Any = extra.get("score", 0.0)
            return float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _load_select_stocks(self, today_str: str) -> set[str]:
        """加载用户策略当日选中的股票代码集合。

        选股结果按 **user_strategy_id + 日期** 缓存（内存快照 + Redis，见 UserStrategyCache），
        因此同一天内同一用户策略只请求一次；服务端接口参数已由 strategy_id 改为
        user_strategy_id，不同用户参数不同、选股结果也不再共用。

        :param today_str: 行情时间或日期，为空时取最近交易日
        :return: 选中股票代码集合
        """
        if today_str == "":
            today_str = self.get_recent_trading_day()
        today: str = today_str[:10]  # YYYY-MM-DD

        cached: set[str] | None = self._cache.get_select_stocks(self.user_strategy_id, today)
        if cached is not None:
            return cached

        if not self.user_strategy_id:
            self.logger.warning(
                "[%s] user_strategy_id 为空，无法加载选股列表", self.strategy_name
            )
            return set()

        try:
            # 复用全局 ApiClient（已带 admin fallback token），避免每次调用创建新连接池
            admin_client: ApiClient = AppContext().api_client
            select_api = StrategySelectStockApi(admin_client)
            items = select_api.list(
                user_strategy_id=self.user_strategy_id,
                start_time=today,
                end_time=today + " 23:00:00",
            )

            codes: set[str] = set()
            for item in items:
                c: str = str(item.get("code", item.get("stock_code", "")))
                if c:
                    codes.add(c)

            self._cache.save_select_stocks(self.user_strategy_id, today, codes)
            self.logger.info(
                "[%s] 加载选股列表: user_strategy_id=%s, codes=%s",
                self.strategy_name, self.user_strategy_id, codes,
            )
            return codes
        except Exception:
            self.logger.warning(
                "[%s] 加载选股列表失败: user_strategy_id=%s",
                self.strategy_name, self.user_strategy_id, exc_info=True,
            )
            return set()

    # ---- 信号收尾：买入信号价基准 + 日内熔断（2026-10-14）----

    def _sync_signal_state(
        self,
        code: str,
        matched: bool,
        extra: dict[str, Any],
        stock_data: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """信号收尾：同步买卖基准与熔断状态，并执行当日封锁。

        **所有重写 `handle_minute_bar` / `handle_tick_bar` 的子类都必须在返回前
        调用本方法，并用它的返回值代替原来的 `matched, extra`。** 基类那次改动
        不会自动覆盖子类 —— 波段策略就重写了 `handle_minute_bar`（为了做 T 的
        买卖顺序），只在基类里接一次的结果是它的基准永远为空、止损静默失效。
        `tests/test_buy_signal_stop.py::TestHandleMinuteBarWiring` 与
        `tests/test_intraday_breaker.py::TestWiring` 有源码级守卫。

        执行顺序是刻意的：

        1. **先观察卖飞**（用本 bar 的价格对上一笔卖出价）。必须在处理本 bar 的
           新信号之前 —— 否则同一根 bar 上「价格涨过卖出价」与「又发一个卖出
           信号」会互相覆盖，卖飞永远数不到；
        2. 再处理本 bar 的信号。卖出信号先判当日是否被封锁，**被封锁时直接否决、
           且不记入任何计数**（那笔卖出没发生，记进去会让亏损计数凭空虚增）；
        3. 买入信号同理，被封锁时不记基准；未被封锁时记基准（止损用）。
        4. 被否决的信号**不反馈给调用方之外的任何状态**，因此不会污染计数。

        :param code: 股票代码
        :param matched: 本次是否产出信号
        :param extra: 信号明细（`signal` 取值 BUY / SELL）
        :param stock_data: 当前行情（取价格与交易日）
        :return: (是否放行本次信号, 明细)；被熔断否决时返回 (False, extra)
        """
        self._observe_sell_fly(code, stock_data)
        if not matched:
            return False, extra
        if str(extra.get("signal", "")) == "SELL":
            if self.intraday_sell_blocked(code, stock_data):
                extra["intraday_blocked"] = "sell"
                extra["reason"] = (
                    f"{self.user_strategy_name},{code} 当日卖出被日内熔断封锁"
                    f"（连续 {int(self._fly_sell_streak.get(str(code), 0))} 次卖飞）"
                )
                return False, extra
            # 判定盈亏要用基准，所以必须在清除基准之前做。
            self._note_sell_outcome(code, stock_data)
            # 卖出信号即作废基准：下一笔建仓必须重新记录，否则它会拿上一笔的
            # 买点当止损基准（回测里同一只票在区间内会反复买卖几十次）。
            self._clear_buy_signal_price(code)
            return True, extra
        if self.intraday_buy_blocked(code, stock_data):
            extra["intraday_blocked"] = "buy"
            extra["reason"] = (
                f"{self.user_strategy_name},{code} 当日买入被日内熔断封锁"
                f"（连续 {int(self._loss_sell_streak.get(str(code), 0))} 次亏损卖出）"
            )
            return False, extra
        self._record_buy_signal_price(code, stock_data)
        return True, extra

    # ---- 日内熔断（2026-10-14）----

    def intraday_buy_blocked(self, code: str, stock_data: dict[str, Any]) -> bool:
        """当日是否已封锁该股的买入信号（见 `INTRADAY_BUY_BLOCK_LOSS_SELLS`）。"""
        return self._intraday_blocked(code, self._buy_block_dates, stock_data)

    def intraday_sell_blocked(self, code: str, stock_data: dict[str, Any]) -> bool:
        """当日是否已封锁该股的卖出信号（见 `INTRADAY_SELL_BLOCK_FLY_SELLS`）。"""
        return self._intraday_blocked(code, self._sell_block_dates, stock_data)

    def _intraday_blocked(
        self, code: str, dates: dict[str, str], stock_data: dict[str, Any]
    ) -> bool:
        """封锁只在**设置它的那个交易日**内生效，跨日靠日期不等自动失效。

        取不到交易日时不封锁（fail-open）：宁可漏熔断一次，也不要因为行情缺时间
        字段就把某只票整天锁死。

        :param code: 股票代码
        :param dates: 封锁表（买入或卖出）
        :param stock_data: 当前行情
        :return: 是否封锁
        """
        if not self.INTRADAY_BREAKER_ENABLED:
            return False
        today: str = self._bar_date(stock_data)
        if not today:
            return False
        return dates.get(str(code), "") == today

    def _note_sell_outcome(self, code: str, stock_data: dict[str, Any]) -> None:
        """记录一笔卖出的结果：更新亏损计数与卖飞观察位。

        只有**有建仓基准**的卖出才算一笔卖出结果。策略收空持仓（X1），无法知道
        自己是否真的持有；实时链路与回测都会对未持仓的股票跑一遍卖出判定
        （波段策略尤其如此，它把整个池当交易域），那些信号不会成交。用
        「有没有基准」区分它们：有基准说明此前发过买入信号且尚未卖出平掉，
        这笔卖出才对应一次真实往返。

        没有基准的信号既**不计数也不清零**。把它当盈利卖出会错误地打断「连续亏损」
        的累计，当亏损卖出会凭空虚增计数。

        :param code: 股票代码
        :param stock_data: 当前行情
        """
        if not self.INTRADAY_BREAKER_ENABLED:
            return
        key: str = str(code)
        reference: float = float(self._buy_signal_prices.get(key, 0.0) or 0.0)
        price: float = self._bar_price(stock_data)
        if reference <= 0 or price <= 0:
            return
        today: str = self._bar_date(stock_data)
        if price < reference:
            streak: int = int(self._loss_sell_streak.get(key, 0)) + 1
            self._loss_sell_streak[key] = streak
            required: int = max(int(self.INTRADAY_BUY_BLOCK_LOSS_SELLS), 1)
            if streak >= required:
                # 用 `>=` 而不是 `==`：封锁次日解除后计数仍然 ≥ 阈值，此时再亏一笔
                # 必须重新封锁，否则「跨日累计」会退化成「只封锁第一次」。
                self._buy_block_dates[key] = today
                if streak == required:
                    # 只在**首次达到阈值**那一刻记一条：被否决的买入信号每分钟都会
                    # 重现，在否决处打日志会刷满整天。
                    self.logger.info(
                        "[%s] 日内熔断：%s 连续 %d 次亏损卖出 ⇒ 当日不再买入该股",
                        self.user_strategy_name, key, streak,
                    )
        else:
            self._loss_sell_streak[key] = 0
        if today:
            # 没有交易日时**不挂观察位**：观察位要靠「日期相同」判断是否同日，
            # 空日期会让它跟任何一天都「同日」，从而在隔天错误地判出卖飞。
            # 计数本身照做（封锁靠日期比较，空日期下 `_intraday_blocked` fail-open）。
            self._sell_fly_watch[key] = (today, price)

    def _observe_sell_fly(self, code: str, stock_data: dict[str, Any]) -> None:
        """观察上一笔卖出之后价格有没有涨过卖出价（卖飞），维护连续计数。

        - 当日价格涨过卖出价（严格 `>`，与「突破」语义一致）⇒ 记一次卖飞，
          连续计数 +1，达到阈值即封锁当日卖出。观察位在第一次突破时消费掉，
          同一笔卖出只计一次。
        - 跨日仍未涨过 ⇒ 视为「没卖飞」，连续计数清零。
        - 缺少交易日或价格信息时**不判定也不清零**：数据不全不该改写累计状态。

        :param code: 股票代码
        :param stock_data: 当前行情
        """
        if not self.INTRADAY_BREAKER_ENABLED:
            return
        key: str = str(code)
        watched: tuple[str, float] | None = self._sell_fly_watch.get(key)
        if not watched:
            return
        watch_date, sell_price = watched
        today: str = self._bar_date(stock_data)
        price: float = self._bar_price(stock_data)
        if not today or price <= 0:
            return
        if today != watch_date:
            self._sell_fly_watch.pop(key, None)
            self._fly_sell_streak[key] = 0
            return
        if price > sell_price:
            self._sell_fly_watch.pop(key, None)
            streak: int = int(self._fly_sell_streak.get(key, 0)) + 1
            self._fly_sell_streak[key] = streak
            required: int = max(int(self.INTRADAY_SELL_BLOCK_FLY_SELLS), 1)
            if streak >= required:
                # 与买入侧同理用 `>=`：封锁次日解除后计数仍 ≥ 阈值，再卖飞一笔要
                # 重新封锁。日志只在首次达到阈值时打，避免刷屏。
                self._sell_block_dates[key] = today
                if streak == required:
                    self.logger.info(
                        "[%s] 日内熔断：%s 连续 %d 次卖飞 ⇒ 当日不再卖出该股",
                        self.user_strategy_name, key, streak,
                    )

    @staticmethod
    def _bar_date(stock_data: dict[str, Any]) -> str:
        """从行情里取交易日（YYYY-MM-DD）；取不到时返回空串。"""
        raw: Any = stock_data.get("create_time", stock_data.get("date", ""))
        return str(raw or "")[:10]

    @staticmethod
    def _bar_price(stock_data: dict[str, Any]) -> float:
        """从行情里取成交参考价；取不到或非正数时返回 0.0。"""
        try:
            return float(stock_data.get("price", stock_data.get("close", 0)) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _record_buy_signal_price(self, code: str, stock_data: dict[str, Any]) -> None:
        """记录本次建仓的买入信号价（**只在没有记录时写**）。

        只在无记录时写是刻意的：`handle_minute_bar` 判断「是否查买入条件」用的是
        `len(position) == 0`，而实时链路按设计约定恒传空持仓 ⇒ 持仓期间仍会不断
        判出买入信号。若每次都覆盖，基准会被持仓途中的新买点反复改写 —— 而
        「缩量回调」类买点恰恰出现在下跌途中，基准会被越压越低，止损随之失效。
        基准的语义定为「**最近一次建仓的买入信号价**」，卖出信号发出时清除。

        :param code: 股票代码
        :param stock_data: 当前分钟行情，价格取 `price`（缺失时回退 `close`）
        """
        key: str = str(code)
        if key in self._buy_signal_prices:
            return
        try:
            price: float = float(
                stock_data.get("price", stock_data.get("close", 0)) or 0.0
            )
        except (TypeError, ValueError):
            return
        if price > 0:
            self._buy_signal_prices[key] = price
            self._below_buy_signal_counts[key] = 0

    def set_buy_signal_price(self, code: str, price: float) -> None:
        """外部注入买入信号价（实盘成交后由 workflow 调用）。

        策略自己记录已够用；本接口是给「进程重启后基准丢失」留的补写入口 ——
        重启会清掉内存里的 `_buy_signal_prices`，此时止损退化为「无基准、不判定」
        （fail-open，不会误卖）。注意本接口**不做持久化**，若要做到重启不丢，
        需要由调用方从服务端最近一条买入信号回填。

        :param code: 股票代码
        :param price: 买入信号价（<=0 时忽略）
        """
        key: str = str(code)
        value: float = float(price or 0.0)
        if value > 0:
            self._buy_signal_prices[key] = value
            self._below_buy_signal_counts[key] = 0

    def _clear_buy_signal_price(self, code: str) -> None:
        """清除某只股票的买入信号价与连续跌破计数（发出卖出信号时调用）。"""
        key: str = str(code)
        self._buy_signal_prices.pop(key, None)
        self._below_buy_signal_counts.pop(key, None)

    def _buy_signal_break_stop(
        self, code: str, current_price: float
    ) -> tuple[bool, dict[str, Any]]:
        """连续 `SELL_BUY_PRICE_BREAK_COUNT` 个 bar 低于买入信号价 ⇒ 硬止损。

        计数规则：本次现价 **低于** 基准 ⇒ 计数 +1；现价 **不低于** 基准 ⇒ 计数清零。
        达到次数即返回 True（由调用方直接产卖出信号，不再并入分值）。
        每次调用算「一次」，调用频率 = 分钟 bar 频率。

        fail-open 的三种情形（一律不触发，宁可不止损也不误卖）：
        ① `SELL_BUY_PRICE_BREAK_ENABLED=False`；② 没有该股的买入信号价基准
        （从未建仓 / 进程重启后丢失）；③ 现价或基准非正数。

        :param code: 股票代码
        :param current_price: 当前分钟价格
        :return: (是否触发止损, 明细)，明细含 break_count / buy_signal_price
        """
        if not self.SELL_BUY_PRICE_BREAK_ENABLED:
            return False, {}
        key: str = str(code)
        reference: float = float(self._buy_signal_prices.get(key, 0.0) or 0.0)
        price: float = float(current_price or 0.0)
        if reference <= 0 or price <= 0:
            return False, {}
        if price >= reference:
            self._below_buy_signal_counts[key] = 0
            return False, {}
        count: int = int(self._below_buy_signal_counts.get(key, 0)) + 1
        self._below_buy_signal_counts[key] = count
        detail: dict[str, Any] = {
            "break_count": count,
            "buy_signal_price": round(reference, 2),
            "below_pct": round((price - reference) / reference * 100, 2),
        }
        required: int = max(int(self.SELL_BUY_PRICE_BREAK_COUNT), 1)
        return count >= required, detail

    # ---- 卖出侧通用基准（持仓最高价 / 回撤口径）----

    def _sell_baseline_buy_time(
        self, position: dict[str, Any], create_time: str
    ) -> str:
        """卖出侧「持仓期间最高价」区间的起始日。

        有持仓时取真实买入日 `position["buy_time"]`；实时链路恒传空持仓
        （设计约定 D-1），此时回退到「最近 2 个交易日」—— 与其它策略同口径，
        避免同一份行情在不同策略里算出不同的回撤基准。

        回退时用 `create_time`（行情时间）而不是 `datetime.now()` 推算交易日：
        回放/回测里行情时间是历史值，用当前时间会推错基准日。

        :param position: 持仓信息（实时链路恒为空字典）
        :param create_time: 当前行情时间，空串时退回最近交易日
        :return: 区间起始日（原样返回 buy_time，或回退得到的日期字符串）
        """
        buy_time: str = str(position.get("buy_time", "") or "")
        if buy_time:
            return buy_time
        today_str: str = self.get_recent_trading_day()
        if create_time and len(create_time) > 10:
            today_str = create_time
        return self.get_previous_trading_day(today_str=today_str, days=2)

    def _resolve_highest_price(
        self, code: str, buy_time: str, today_str: str, day_high: float, cost_price: float
    ) -> float:
        """持仓期间最高价：日K区间最高价与当日最高价取大。

        查询区间为 `[buy_time, today_str]`，结果按
        `hprice:{code}:{buy_date}:{day}` 缓存（TTL 6 小时），未命中才回源服务端。
        `buy_time` 由 `_sell_baseline_buy_time` 给出（有持仓为真实买入日，
        空持仓退化为最近 2 个交易日）。

        查询失败或区间无数据时回退到 `max(当日最高价, 成本价)`，此时回撤口径
        退化为日内回撤（判定仍然可用，只是区间更短）。

        本方法原为 `VolumeExpansionStrategy` 私有实现，2026-09-29 上移到基类供
        波段策略复用 —— 两处各写一份时，查询区间与缓存 key 一旦漂移，
        同一持仓在两个策略里会得到不同的回撤数字，而两边日志都「看起来正常」。

        :param code: 股票代码
        :param buy_time: 买入基准日（YYYY-MM-DD 或带时间）
        :param today_str: 当前行情时间，作为区间结束日
        :param day_high: 当日最高价
        :param cost_price: 持仓成本价（无持仓时为 0）
        :return: 持仓期间最高价；完全取不到数据时为 max(day_high, cost_price)
        """
        if not buy_time:
            return max(day_high, cost_price)
        buy_date: str = buy_time[:10]
        cache_day: str = UserStrategyCache.day_of(today_str)
        cached: float = self._cache.get_highest_price(code, buy_date, cache_day)
        if cached > 0:
            return max(cached, day_high)
        try:
            klines: list[dict[str, Any]] = AppContext().market_api.get_day_kline(
                code=code, start=buy_date, end=today_str,
            )
            if klines:
                db_highest: float = max(float(k.get("high", 0) or 0) for k in klines)
                if db_highest > 0:
                    self._cache.save_highest_price(code, buy_date, cache_day, db_highest)
                    return max(db_highest, day_high)
        except Exception as exc:
            self.logger.warning(
                "[%s] 查询持仓最高价失败(%s): %s", self.user_strategy_name, code, exc
            )
        return max(day_high, cost_price)

    def _match_pe_profit(self, code: str, pe_limit: float, profit_grow: float) -> tuple[bool, dict[str, Any]]:
        score: float = 0.0
        try:
            # 复用全局 ApiClient（已带 admin fallback token），避免每次调用创建新连接池
            admin_client: ApiClient = AppContext().api_client
            finance_api = FinanceApi(admin_client)
            valuation_map: dict[str, dict[str, Any]] = finance_api.get_valuation(codes=code)
            val_data: dict[str, Any] | None = (
                self._lookup_by_code(valuation_map, code) if valuation_map else None
            )
            ttm_pe: float = float(val_data.get("ttm_pe", 0)) if val_data else 0.0
            if ttm_pe <= 0:
                return False, {
                    "reason": (
                        f"{self.strategy_name}, func:match_strategy,"
                        f"条件1不满足: 市盈率数据异常或为亏损(ttm_pe={ttm_pe})"
                    ),
                }
            if ttm_pe >= pe_limit:
                return False, {
                    "reason": (
                        f"{self.strategy_name}, func:match_strategy,"
                        f"条件1不满足: 市盈率{ttm_pe:.1f} >= 300"
                    ),
                }
            score += 1
        except Exception as exc:
            self.logger.warning("查询市盈率失败 code=%s: %s", code, exc, exc_info=True)
            return False, {
                "reason": (
                    f"{self.strategy_name}, func:match_strategy,"
                    f"条件1不满足: 查询市盈率失败"
                ),
            }

        try:
            profit_map: dict[str, dict[str, Any]] = finance_api.get_profit(codes=code)
            profit_data: dict[str, Any] | None = (
                self._lookup_by_code(profit_map, code) if profit_map else None
            )
            profit_growth: float = float(profit_data.get("net_profit_growth_rate", 0)) if profit_data else 0.0
            if profit_growth <= profit_grow:
                return False, {
                    "reason": (
                        f"{self.strategy_name}, func:match_strategy,"
                        f"条件1不满足: 利润增长率{profit_growth:.2f}% <= 10%"
                    ),
                }
            score += 1
        except Exception as exc:
            self.logger.warning("查询利润增长率失败 code=%s: %s", code, exc, exc_info=True)
            return False, {
                "reason": (
                    f"{self.strategy_name}, func:match_strategy,"
                    f"条件1不满足: 查询利润增长率失败"
                ),
            }
        return True, {"score": round(score, 2)}

    @staticmethod
    def _lookup_by_code(
        data_map: dict[str, dict[str, Any]], code: str
    ) -> dict[str, Any] | None:
        """在返回的行情/财务字典中查找指定股票代码。

        服务端返回的 key 可能带交易所后缀（如 000001.SZ / 600519.SH），
        因此先按精确代码查找，失败时再按前缀/后缀匹配。
        """
        if not data_map:
            return None
        if code in data_map:
            return data_map[code]
        for key, value in data_map.items():
            key_str: str = str(key)
            if key_str.endswith(code) or key_str.startswith(code):
                return value
        return None

    def _parse_time(self, time_str: str) -> datetime.datetime | None:
        """解析时间字符串为 datetime 对象。

        支持多种常见格式。

        :param time_str: 时间字符串
        :return: datetime 对象，解析失败返回 None
        """
        if not time_str:
            return None
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
        ):
            try:
                return datetime.datetime.strptime(time_str, fmt)
            except ValueError:
                continue
        try:
            return datetime.datetime.fromisoformat(time_str)
        except (ValueError, TypeError):
            return None
            
    def save_to_db(self, pool_id: str = "", status: str = "stopped") -> tuple[bool, str | None]:
        """将策略模板和用户策略关联保存到服务器。

        先通过 StrategyApi.create() 创建策略模板，
        再通过 UserStrategyApi.create() 创建用户策略关联。

        :param pool_id: 关联股票池 ID（可选）
        :param status: 策略状态，默认 stopped
        :return: (是否成功, user_strategy_id 或错误信息)
        """
        try:
            # 1. 创建策略模板
            result = AppContext().strategy_api.create(
                name=self.strategy_name,
                strategy_type=self.strategy_type,
                description=self.description,
                class_path=self.__class__.__module__,
                class_name=self.__class__.__name__,
            )
            if not result:
                return False, None
            strategy_id = result.get("id") or result.get("strategy_id") or result.get("_id")
            if not strategy_id:
                return False, None
            strategy_id = str(strategy_id)

            # 2. 创建用户策略关联
            us_result = AppContext().user_strategy_api.create(
                strategy_id=strategy_id,
                pool_id=pool_id,
                status=status,
            )
            user_strategy_id = (
                us_result.get("id") or us_result.get("user_strategy_id") or us_result.get("_id")
            )
            return True, str(user_strategy_id) if user_strategy_id is not None else None
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.logger.warning("[%s] save_to_db error: %s", self.strategy_name, exc)
            return False, str(exc)
