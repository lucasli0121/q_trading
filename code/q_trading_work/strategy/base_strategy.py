# strategy/base_strategy.py
from __future__ import annotations

import datetime
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, cast

import pandas as pd

from api.client import ApiClient
from api.finance import FinanceApi
from api.strategy_select_stock import StrategySelectStockApi
from app_context import AppContext
from factor.factor_manager import FactorManager
import exchange_calendars as xcals

class BaseStrategy(ABC):
    """策略顶级基类，提供通用数据加载、选股与策略元数据保存能力。"""

    strategy_id: str = ""
    user_strategy_id: str = ""
    strategy_name: str = "BaseStrategy"
    description: str = ""
    strategy_type: str = "选股策略"

    # 分钟缓存 Redis key 过期时间（秒）：6 小时，覆盖整个交易日
    MINUTE_CACHE_TTL: int = 6 * 60 * 60

    def __init__(self) -> None:
        """初始化日志、因子管理器并注册子类因子。"""
        self.logger = logging.getLogger(__name__)
        self.factor_manager: FactorManager = AppContext().factor_manager
        self.init_factors()
        self._select_stocks_set: set[str] = set()  # 当日选中股票代码集合（由 daily_before_trading 填充）
        self._select_stocks_date: str = ""  # 缓存日期，跨天自动刷新
        self._minute_cache: dict[str, list[dict[str, Any]]] = {}  # 分钟级行情本地兜底缓存，key=code（Redis 不可用时使用）
        self._minute_cache_day: str = ""  # 当前分钟缓存日期（由最近保存的行情时间决定），用于拼接 Redis key
        self._redis_enabled: bool = AppContext().redis_exec.enabled  # Redis 是否启用（来自配置文件）
        self._redis_failed: bool = False  # Redis 不可用标记，失败一次后当天回退本地缓存
        self._stock_scores: dict[str, dict[str, Any]] = {}  # 策略对股票的评分，key=code（选股/买入打分时更新）
        self._highest_prices: dict[str, float] = {}  # 持仓期间最高价，key=code
        self.xshg = xcals.get_calendar("XSHG", start="2020-01-01")  # 上交所交易日历，用于判断交易日
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
        self._select_stocks_set.clear()
        self._select_stocks_date = ""
        self._minute_cache.clear()
        self._minute_cache_day = ""
        self._redis_failed = False  # 新的一天重新尝试 Redis
        self._stock_scores.clear()
        self._highest_prices.clear()
        self.logger.debug(
            "[%s] before_trading(trade_date=%s, codes=%d)",
            self.strategy_name, trade_date, len(stock_codes),
        )

    def handle_minute_bar(self, code: str, position: dict[str, Any], stock_data: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """处理分钟行情：先执行 _before_minute_route 钩子，再按选股列表分发到买卖检查。"""
        self._before_minute_route(code, stock_data)
        select_stocks: set[str] = self._load_select_stocks(stock_data.get("create_time", ""))
        # 如果股票在选股列表中且当前没有持仓，则检查买入条件；否则检查卖出条件。
        if code in select_stocks and len(position) == 0:
            return self.check_minute_buy(code, stock_data)
        if len(position) > 0:
            buy_time = position.get("buy_time", "")
            if buy_time and buy_time[:10] == stock_data.get("create_time", "")[:10]:
                # 当日买入的股票，暂不卖出，避免当日买入后立即卖出。
                return False, {"reason": "当日买入，暂不卖出"}
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

    def _save_score_cache(self, code: str, action: str, score: float, check_num: int, create_time: str, done: bool | None = None):
        """缓存当天的买入/卖出积分，分钟实时检测时，每次检测积分可以累计。
        
        主存储为 Redis 列表：key=code:action:score:日期（如 000001:BUY:score:2026-08-17），
        :param code: 股票代码
        :param score: 策略对股票的打分，None 时取 self._stock_scores 中的最近评分
        :param done: 已买入/卖出标记
        """
        avg_score: float = score / check_num if check_num > 0 else 0.0
        entry: dict[str, Any] = {
            "action": action,
            "score": float(score),
            "avg_score": float(avg_score),
            "create_time": create_time,
            "check_number": check_num,
            "done": bool(done) if done is not None else None
        }
        day: str = (
            create_time[:10]
            if len(create_time) >= 10
            else datetime.datetime.now().strftime("%Y-%m-%d")
        )
        # Redis 主路径：写入 code+日期 的列表，8 小时过期，并裁剪到最近 max_cache 条
        if self._redis_enabled and not self._redis_failed:
            redis_exec = AppContext().redis_exec
            key: str = self._cache_score_key(code, action, day)
            if redis_exec.set_key_string( key, json.dumps(entry), ex=self.MINUTE_CACHE_TTL):
                return
            # 写入失败：标记后回退本地缓存，当天不再重试 Redis
            self._redis_failed = True
        # 本地兜底路径（Redis 未启用或不可用）
        if code not in self._stock_scores:
            self._stock_scores[code] = entry

    def _get_score_cache(self, code: str, action: str, create_time: str) -> dict:
        """从缓存取出当天的买入/卖出积分，分钟实时检测时，每次检测积分可以累计。
                
        主存储为 Redis 列表：key=code:action:score:日期（如 000001:BUY:score:2026-08-17），
        如果redis失败则从_stock_scores中获取
        :param code: 股票代码
        """
        day: str = (
            create_time[:10]
            if len(create_time) >= 10
            else datetime.datetime.now().strftime("%Y-%m-%d")
        )
        # get redis返回空值时不设置 self._redis_failed, 因为有时redis上本来就是空值
        entries: dict[str, Any] = {}
        if self._redis_enabled and not self._redis_failed:
            redis_exec = AppContext().redis_exec
            key: str = self._cache_score_key(code, action, day)
            raw: str | None = redis_exec.get_key_string(key)
            if raw is not None:
                try:
                    entries = json.loads(raw)
                except json.JSONDecodeError:
                    self.logger.warning(
                        "[%s] 代码分数缓存 JSON 解析失败: code=%s",
                        self.strategy_name, code,
                    )
        if len(entries) == 0:
            if code in self._stock_scores:
                entries = self._stock_scores[code]
        return entries
    
    @staticmethod
    def _cache_score_key(code: str, action: str, day: str) -> str:
        """构造分钟缓存 Redis key：code + action + score + 日期（如 000001:BUY:score:2026-08-17）。

        :param code: 股票代码
        :param day: 日期字符串 YYYY-MM-DD
        :return: Redis key
        """
        return f"{code}:{action}:score:{day}"

    def _save_minute_cache(
        self,
        code: str,
        stock_data: dict[str, Any],
        max_cache: int = 60 * 4
    ) -> None:
        """缓存当前分钟行情（含 low/score/bought 字段），仅保留最近窗口防止无限增长。

        主存储为 Redis 列表：key=code+日期（如 000001:2026-08-17），
        过期时间 MINUTE_CACHE_TTL（8 小时，覆盖整个交易日），每次写入后
        裁剪到最近 max_cache 条；Redis 未启用或写入失败时回退本地内存缓存。

        :param code: 股票代码
        :param stock_data: 分钟行情数据（price/volume/amount/close/low/create_time）
        :param max_cache: 单只股票最大缓存条数
        """
        create_time: str = str(stock_data.get("create_time", ""))
        day: str = (
            create_time[:10]
            if len(create_time) >= 10
            else datetime.datetime.now().strftime("%Y-%m-%d")
        )
        if day:
            self._minute_cache_day = day
        # Redis 主路径：写入 code+日期 的列表，8 小时过期，并裁剪到最近 max_cache 条
        if self._redis_enabled and not self._redis_failed:
            redis_exec = AppContext().redis_exec
            key: str = self._minute_cache_key(code, day)
            if redis_exec.push_value_to_rlist(
                key, json.dumps(stock_data), ex=self.MINUTE_CACHE_TTL
            ):
                redis_exec.ltrim_rlist(key, -max_cache, -1)
                return
            # 写入失败：标记后回退本地缓存，当天不再重试 Redis
            self._redis_failed = True
            self.logger.warning(
                "[%s] Redis 分钟缓存写入失败，回退本地缓存: code=%s",
                self.strategy_name, code,
            )
        # 本地兜底路径（Redis 未启用或不可用）
        if code not in self._minute_cache:
            self._minute_cache[code] = []
        self._minute_cache[code].append(stock_data.copy())
        if len(self._minute_cache[code]) > max_cache:
            self._minute_cache[code] = self._minute_cache[code][-max_cache:]

    @staticmethod
    def _minute_cache_key(code: str, day: str) -> str:
        """构造分钟缓存 Redis key：code + 日期（如 000001:2026-08-17）。

        :param code: 股票代码
        :param day: 日期字符串 YYYY-MM-DD
        :return: Redis key
        """
        return f"{code}:{day}"

    def _get_minute_cache(self, code: str) -> list[dict[str, Any]]:
        """读取 code 当日分钟缓存（Redis 优先，Redis 无数据或不可用时回退本地缓存）。

        :param code: 股票代码
        :return: 分钟行情条目列表，每项含 price/volume/low/score/bought 等字段
        """
        day: str = self._minute_cache_day or datetime.datetime.now().strftime("%Y-%m-%d")
        # get redis返回值为空时，不设置self._redis_failed, 因为有时就是空值，不表示redis有问题
        if self._redis_enabled and not self._redis_failed:
            redis_exec = AppContext().redis_exec
            raw: list[str] | None = redis_exec.get_value_from_rlist(
                self._minute_cache_key(code, day), -1, False
            )
            if raw is not None:
                entries: list[dict[str, Any]] = []
                for item in raw:
                    try:
                        entries.append(json.loads(item))
                    except json.JSONDecodeError:
                        self.logger.warning(
                            "[%s] 分钟缓存 JSON 解析失败: code=%s",
                            self.strategy_name, code,
                        )
                if entries:
                    return entries
        return list(self._minute_cache.get(code, []))

    """
    function: get_previous_trading_day
    description: 获取指定日期的前一个交易日
    param {*} self
    param {str} date_str
    return {*}
    """    
    def get_previous_trading_day(self, date_str: str) -> str:
        date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        while True:
            date -= datetime.timedelta(days=1)
            if self.xshg.is_session(date):
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
        # 结束日期为昨天，起始日期为当前日期向前回溯的天数，至少为 30 天。
        # 日期减一是只取历史数据，不包含当天的未完成交易日数据。
        if not today_str:
            today_str = self.get_recent_trading_day()
        today = datetime.datetime.strptime(today_str[:10], "%Y-%m-%d").date()
        end_date = self.get_previous_trading_day(today.strftime("%Y-%m-%d"))
        # 为了让均线、回撤等因子有足够的前置历史，至少多取一段历史窗口。
        # 这里使用 `days * 2` 作为默认回溯长度，并保证不小于 30 天。
        lookback_days: int = max(days * 2, 30)
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
        """从 API 加载当日策略选中的股票代码集合。

        按日期缓存，同一天内只请求一次。

        :return: 选中股票代码集合
        """
        # 复用全局 ApiClient（已带 admin fallback token），避免每次调用创建新连接池
        admin_client: ApiClient = AppContext().api_client
        if today_str == "":
            today_str = self.get_recent_trading_day()
        today = today_str[:10]  # YYYY-MM-DD
        if self._select_stocks_date == today and self._select_stocks_set:
            return self._select_stocks_set

        if not self.strategy_id:
            self.logger.warning("[%s] strategy_id 为空，无法加载选股列表", self.strategy_name)
            return set()

        try:
            start_time: str = today
            end_time: str = today + " 23:00:00"
            select_api = StrategySelectStockApi(admin_client)
            items = select_api.list(
                strategy_id=self.strategy_id,
                start_time=start_time,
                end_time=end_time,
            )

            codes: set[str] = set()
            for item in items:
                c: str = str(item.get("code", item.get("stock_code", "")))
                if c:
                    codes.add(c)

            self._select_stocks_set = codes
            self._select_stocks_date = today
            self.logger.info(
                "[%s] 加载选股列表: strategy_id=%s, codes=%s",
                self.strategy_name, self.strategy_id, codes,
            )
            return codes
        except Exception:
            self.logger.warning(
                "[%s] 加载选股列表失败: strategy_id=%s",
                self.strategy_name, self.strategy_id, exc_info=True,
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
