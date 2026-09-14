# strategy/base_strategy.py
from __future__ import annotations

import datetime
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, cast

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

class BaseStrategy(ABC):
    """策略顶级基类，提供通用数据加载、选股与策略元数据保存能力。"""

    strategy_id: str = ""
    user_strategy_id: str = ""
    strategy_name: str = "BaseStrategy"
    description: str = ""
    strategy_type: str = "选股策略"

    # 策略在 cfg/strategy.yaml 中的配置节名，为空时按类名蛇形命名推导
    # （如 StrongReboundStrategy -> strong_rebound_strategy）
    config_section: str = ""

    # 分钟缓存 Redis key 过期时间（秒）：6 小时，覆盖整个交易日
    MINUTE_CACHE_TTL: int = 6 * 60 * 60

    def __init__(
        self,
        user_strategy_id: str = "",
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
        self.strategy_id = strategy_id
        self.strategy_type = strategy_type
        self.strategy_params: dict[str, Any] = {}  # 已加载的策略参数（来源见 param_source）
        self.param_source: str = ""  # 参数来源描述（user_strategy/strategy/yaml）
        self._highest_prices: dict[str, float] = {}  # 持仓期间最高价，key=code
        # 全局缓存单例：当日行情（由 workflow 单点写入）、策略打分、选股结果。
        # 原来由本类维护的行情/评分缓存已迁出，见 core/user_strategy_cache.py。
        self._cache: UserStrategyCache = UserStrategyCache()
        self.xshg = xcals.get_calendar("XSHG", start="2020-01-01")  # 上交所交易日历，用于判断交易日
        # 先加载策略参数再注册因子，保证 init_factors 使用加载后的参数：
        # 优先级 user_strategy.strategy_params > strategy.default_params > cfg/strategy.yaml
        if user_strategy_params is not None or default_params is not None:
            # workflow 已批量查询参数，直接三级选择（含 yaml 兜底），避免重复请求
            self._apply_resolved_params(user_strategy_params, default_params)
        else:
            self.load_strategy_params()
        self.init_factors()

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
        self.apply_params(self.strategy_params)
        self.logger.info(
            "[%s] 策略参数加载完成: source=%s, params=%s",
            self.strategy_name, source, self.strategy_params,
        )

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
        if code in select_stocks and len(position) == 0:
            match, extra = self.check_minute_buy(code, stock_data)
            if match:
                return match, extra
        return self.check_minute_sell(code, position, stock_data)

    def _before_minute_route(self, code: str, stock_data: dict[str, Any]) -> None:
        """分钟路由前钩子：子类可在此保存分钟缓存等。"""
        pass

    def handle_tick_bar(self, code: str, position: dict[str, Any], stock_data: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """处理实时 tick 行情：按选股列表分发到买入/卖出检查。"""
        select_stocks: set[str] = self._load_select_stocks(stock_data.get("create_time", ""))
        if code in select_stocks:
            return self.check_tick_buy(code, stock_data)
        return self.check_tick_sell(code, position, stock_data)

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

    # ---- 通用方法 ----

    def load_his_daily_data(self, code: str, today_str: str, days: int) -> pd.DataFrame:
        """使用行情接口加载指定股票的日线数据，并返回最近 days 条记录。"""
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

        try:
            if market_api and hasattr(market_api, "get_day_kline"):
                raw_data = market_api.get_day_kline(
                    code=code,
                    start=start_date,
                    end=end_date,
                )
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.debug("market_api.get_day_kline error: %s", exc)

        if not raw_data:
            return pd.DataFrame(columns=pd.Index(["code", "open", "close", "volume", "create_time"]))

        df = pd.DataFrame(raw_data)
        if df.empty:
            return pd.DataFrame(columns=pd.Index(["code", "open", "close", "volume", "create_time"]))

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

    def select(self, codes: list[str], today_str: str, days: int = 15) -> list[dict[str, Any]]:
        """遍历股票代码，加载日线数据并判断策略是否命中。"""
        results: list[dict[str, Any]] = []
        for code in codes:
            try:
                df = self.load_his_daily_data(code=code, today_str=today_str, days=days)
                if df.empty:
                    results.append({"code": code, "matched": False, "extra": {"reason": "no data"}})
                    continue

                matched, extra = self.is_match_strategy(
                    cast(list[dict[str, Any]], df.to_dict(orient="records"))
                )
                results.append({"code": code, "matched": matched, "extra": extra})
            except Exception as exc:  # pragma: no cover - defensive fallback
                self.logger.warning("[%s] select(%s) error: %s", self.strategy_name, code, exc)
                results.append({"code": code, "matched": False, "extra": {"reason": str(exc)}})

        return results

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
