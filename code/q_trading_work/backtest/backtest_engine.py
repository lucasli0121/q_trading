"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-07-08
Description: 策略回测引擎，支持股票池、基准对比、资金管理、结果持久化。
"""

from __future__ import annotations

from configparser import ConfigParser
import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from httpx import HTTPStatusError
import numpy as np
import pandas as pd

from api.client import ApiClient
from api.market import MarketApi
from api.pool import PoolApi
from api.stock_info import StockInfoApi
from api.strategy import StrategyApi
from api.user_strategy import UserStrategyApi
from app_context import AppContext
from trade.manager import TradeManager


# ============================================================
# 配置与结果数据类
# ============================================================


@dataclass
class TradeCostConfig:
    """回测中的交易成本配置（与实盘 trade.manager.TradeCostConfig 语义一致）。"""

    buy_slippage: float = 0.0  # 买入滑点（元），成交价 = 基准价 + buy_slippage
    sell_slippage: float = 0.0  # 卖出滑点（元），成交价 = 基准价 - sell_slippage
    fee_pct: float = 0.0  # 手续费百分率（%），买卖双向各收一次
    fee_low: float = 0.0  # 最低手续费（元），按比例计算不足时按此收取


@dataclass
class BacktestConfig:
    """回测配置，包含资金、基准、股票池、时间范围、交易成本等参数。

    Attributes:
        initial_capital: 初始资金
        benchmark_code: 基准股票代码，默认 000300（沪深300）
        pool_id: 股票池 ID，优先级最高，与 pool_name / stock_codes 三选一
        pool_name: 股票池名称，与 pool_id / stock_codes 三选一
        stock_codes: 显式股票代码列表，优先级最低
        start_date: 回测开始日期 YYYY-MM-DD（分钟频率时为 YYYY-MM-DD HH:MM:00）
        end_date: 回测结束日期 YYYY-MM-DD（分钟频率时为 YYYY-MM-DD HH:MM:00）
        hold_days: 持仓天数（分钟频率时表示持仓的分钟K线条数）
        signal_window: 信号检测窗口（用于策略判断的历史数据条数）
        frequency: K线频率，\"daily\" 为日K线，\"minute\" 为分钟K线
        trade_config: 交易成本配置
        position_size_pct: 每笔交易使用资金比例（1.0 = 全仓）
    """

    initial_capital: float = 100000.0
    benchmark_code: str = "000300"
    pool_id: str = ""
    pool_name: str = ""
    stock_codes: list[str] | None = None
    start_date: str = ""
    end_date: str = ""
    hold_days: int = 5
    signal_window: int = 20
    frequency: str = "daily"
    trade_config: TradeCostConfig | None = None
    position_size_pct: float = 1.0


@dataclass
class TradeResult:
    """单笔交易结果。"""

    code: str  # 股票代码
    strategy_name: str  # 策略名称
    buy_date: str  # 买入日期
    sell_date: str  # 卖出日期
    buy_price: float  # 有效买入价（含滑点/手续费）
    sell_price: float  # 有效卖出价（含滑点/手续费）
    profit_pct: float  # 收益率（%）


@dataclass
class BacktestSummary:
    """回测汇总结果，包含收益率、风险指标、基准对比等。

    Attributes:
        strategy_name: 策略名称
        total_trades: 总交易次数
        win_count: 盈利次数
        loss_count: 亏损次数
        win_rate: 胜率（%）
        avg_profit: 平均收益率（%）
        max_profit: 最大单笔收益（%）
        max_loss: 最大单笔亏损（%）
        total_return_pct: 策略总收益率（%）
        total_profit: 策略总收益金额
        annualized_return_pct: 策略年化收益率（%）
        benchmark_return_pct: 基准总收益率（%）
        benchmark_annualized_pct: 基准年化收益率（%）
        alpha: 超额收益（策略 - 基准）（%）
        sharpe_ratio: 夏普比率
        max_drawdown_pct: 最大回撤（%）
        initial_capital: 初始资金
        final_capital: 最终资金
        start_date: 回测开始日期
        end_date: 回测结束日期
        benchmark_code: 基准代码
        trading_days: 交易天数
    """

    strategy_name: str = ""
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_profit: float = 0.0
    max_profit: float = 0.0
    max_loss: float = 0.0
    total_return_pct: float = 0.0
    total_profit: float = 0.0
    annualized_return_pct: float = 0.0
    benchmark_return_pct: float = 0.0
    benchmark_annualized_pct: float = 0.0
    alpha: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    initial_capital: float = 0.0
    final_capital: float = 0.0
    start_date: str = ""
    end_date: str = ""
    benchmark_code: str = ""
    trading_days: int = 0


# ============================================================
# 回测引擎
# ============================================================


class BacktestEngine:
    """回测引擎，支持股票池、基准对比、资金管理和结果持久化。

    使用方式::

        config = BacktestConfig(
            initial_capital=100000,
            pool_name="我的股票池",
            start_date="2025-01-01",
            end_date="2025-12-31",
            hold_days=5,
        )
        engine = BacktestEngine(config)
        trades, summary = engine.run(strategy)
        engine.save_results(strategy, trades, summary)
    """

    # 无风险利率（年化），用于夏普比率计算
    RISK_FREE_RATE: float = 0.02
    # 每年交易日近似值
    TRADING_DAYS_PER_YEAR: int = 252

    def __init__(self, config: BacktestConfig | None = None) -> None:
        """初始化回测引擎。

        :param config: 回测配置，为 None 时使用默认配置
        """
        self.logger: logging.Logger = logging.getLogger(__name__)
        self.config: BacktestConfig = config or BacktestConfig()
        self.trade_config: TradeCostConfig = (
            self.config.trade_config or TradeCostConfig()
        )
        self._admin_client: ApiClient = ApiClient()
        admin_token: str = self._load_admin_token()
        if admin_token:
            self._admin_client.set_token(admin_token)
            # 同时设置全局 ApiClient 的 fallback token，确保 self._pool_api 等
            # 在后台线程中也能携带认证信息
            AppContext().api_client.set_fallback_token(admin_token)
            self.logger.info("StrategyWorkflow 已使用 admin_token 初始化独立 ApiClient")
        else:
            self.logger.warning(
                "cfg/stock.cfg [server] admin_token 未配置，"
                "后台 API 调用可能因缺少认证而失败"
            )

        self._strategy_api: StrategyApi = StrategyApi(self._admin_client)
        self._user_strategy_api: UserStrategyApi = UserStrategyApi(self._admin_client)
        self._trade_manager: TradeManager = TradeManager(api_client=self._admin_client)
        self._market_api: MarketApi = MarketApi(self._admin_client)
        self._pool_api: PoolApi = PoolApi(self._admin_client)
        self._stock_info_api: StockInfoApi = StockInfoApi(self._admin_client)

    # ---- 静态工具 ----

    @staticmethod
    def _load_admin_token() -> str:
        """从 cfg/stock.cfg 读取 admin_token。

        :return: admin_token 字符串，未配置时返回空字符串
        """
        try:
            from utils.tools import resource_path
            cp = ConfigParser()
            cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
            return cp.get("server", "admin_token", fallback="").strip()
        except Exception:
            return ""
            
    @staticmethod
    def calc_pct(sell_price: float, buy_price: float) -> float:
        """计算收益率（%）。

        :param sell_price: 卖出价
        :param buy_price: 买入价
        :return: 收益率百分比
        """
        return round((sell_price - buy_price) / buy_price * 100, 2)

    def _calc_trade_result(
        self, buy_price: float, sell_price: float, quantity: int = 100
    ) -> dict[str, float]:
        """根据滑点和手续费计算有效买入价、卖出价与收益率。

        滑点为绝对金额（元）：买入价 = 基准价 + buy_slippage，
        卖出价 = 基准价 - sell_slippage。
        手续费 = max(成交金额 * fee_pct/100, fee_low)，买卖各收一次。
        quantity 用于按最低手续费折算每股成本，默认按一手（100 股）估算；
        不设置 fee_low 时，手续费与 quantity 无关。

        :param buy_price: 原始买入价
        :param sell_price: 原始卖出价
        :param quantity: 交易数量（股），默认 100
        :return: 含 buy_price, sell_price, profit_pct 的字典
        """
        quantity = max(int(quantity), 1)
        raw_buy = buy_price + self.trade_config.buy_slippage
        raw_sell = sell_price - self.trade_config.sell_slippage

        buy_notional = raw_buy * quantity
        sell_notional = raw_sell * quantity
        buy_fee = buy_notional * self.trade_config.fee_pct / 100
        sell_fee = sell_notional * self.trade_config.fee_pct / 100
        if self.trade_config.fee_low > 0:
            buy_fee = max(buy_fee, self.trade_config.fee_low)
            sell_fee = max(sell_fee, self.trade_config.fee_low)

        effective_buy_price = (buy_notional + buy_fee) / quantity
        effective_sell_price = (sell_notional - sell_fee) / quantity

        profit_pct = self.calc_pct(effective_sell_price, effective_buy_price)
        return {
            "buy_price": round(effective_buy_price, 4),
            "sell_price": round(effective_sell_price, 4),
            "profit_pct": profit_pct,
        }

    # ---- 股票代码解析 ----

    def _resolve_stock_codes(self) -> list[str]:
        """解析股票代码：优先使用股票池 ID，其次池名称，最后显式代码列表。

        :return: 股票代码列表
        """
        # 1. 优先使用 pool_id
        if self.config.pool_id:
            try:
                pool_info: dict[str, Any] | None = (
                    self._pool_api.get_by_id(self.config.pool_id)
                )
                if pool_info:
                    pool_name: str = pool_info.get("name", "")
                    if pool_name:
                        pool_stocks: list[dict[str, Any]] = (
                            self._pool_api.get_stocks(pool_name)
                        )
                        codes: list[str] = [
                            str(s.get("code", "")) for s in pool_stocks if s.get("code")
                        ]
                        self.logger.info(
                            "从股票池 ID [%s] (名称: %s) 获取 %d 只股票",
                            self.config.pool_id,
                            pool_name,
                            len(codes),
                        )
                        return codes
                self.logger.warning(
                    "股票池 ID [%s] 未找到，回退到 pool_name", self.config.pool_id
                )
            except Exception as exc:
                self.logger.warning(
                    "通过 pool_id 解析股票池失败: %s，回退到 pool_name", exc
                )

        # 2. 回退到 pool_name
        if self.config.pool_name:
            try:
                pool_stocks: list[dict[str, Any]] = (
                    self._pool_api.get_stocks(self.config.pool_name)
                )
                codes: list[str] = [
                    str(s.get("code", "")) for s in pool_stocks if s.get("code")
                ]
                self.logger.info(
                    "从股票池 [%s] 获取 %d 只股票",
                    self.config.pool_name,
                    len(codes),
                )
                return codes
            except Exception as exc:
                self.logger.warning(
                    "读取股票池 [%s] 失败: %s，回退到显式代码列表",
                    self.config.pool_name,
                    exc,
                )

        # 3. 回退到显式代码列表
        return self.config.stock_codes or []

    # ---- 数据获取 ----

    def _fetch_stock_data(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """根据配置的频率获取股票K线数据。

        :param code: 股票代码
        :param start_date: 开始日期
        :param end_date: 结束日期
        :return: K线 DataFrame，至少包含 open/close/create_time 列
        """
        try:
            if self.config.frequency == "minute":
                raw: list[dict[str, Any]] = self._market_api.get_minute_kline(
                    code=code, start=start_date, end=end_date
                )
            else:
                raw = self._market_api.get_day_kline(
                    code=code, start=start_date, end=end_date
                )
            if not raw:
                return pd.DataFrame()
            df = pd.DataFrame(raw)
            # 统一列名
            if "date" in df.columns and "create_time" not in df.columns:
                df["create_time"] = df["date"]
            if "code" not in df.columns:
                df["code"] = code
            return df
        except Exception as exc:
            self.logger.warning(
                "获取 %s %sK线数据失败: %s",
                code,
                "分钟" if self.config.frequency == "minute" else "日",
                exc,
            )
            return pd.DataFrame()

    def _fetch_benchmark_data(
        self, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取基准（如沪深300）的K线数据，频率与回测配置一致。

        :param start_date: 开始日期
        :param end_date: 结束日期
        :return: 基准K线 DataFrame
        """
        if not self.config.benchmark_code:
            return pd.DataFrame()
        try:
            if self.config.frequency == "minute":
                raw: list[dict[str, Any]] = self._market_api.get_minute_kline(
                    code=self.config.benchmark_code,
                    start=start_date,
                    end=end_date,
                )
            else:
                raw = self._market_api.get_day_kline(
                    code=self.config.benchmark_code,
                    start=start_date,
                    end=end_date,
                )
            if not raw:
                self.logger.warning("基准 %s 无数据", self.config.benchmark_code)
                return pd.DataFrame()
            df = pd.DataFrame(raw)
            if "date" in df.columns and "create_time" not in df.columns:
                df["create_time"] = df["date"]
            return df
        except Exception as exc:
            self.logger.warning("获取基准数据失败: %s", exc)
            return pd.DataFrame()

    # ---- 主回测逻辑 ----

    def run(
        self, strategy: Any
    ) -> tuple[list[TradeResult], BacktestSummary]:
        """执行回测，根据配置的频率分发到日频或分钟频回测。

        :param strategy: 策略实例或策略类（类会被自动实例化）
        :return: (交易记录列表, 回测汇总)
        """
        # 如果传入的是类而非实例，自动实例化
        if inspect.isclass(strategy):
            strategy = strategy()
        # 按策略名称查询策略模板，把 strategy_id 回填到策略实例，
        # 供 handle_minute_bar 内部按 id 加载当日选股列表并路由买入/卖出检查
        self._bind_strategy_id(strategy)
        if self.config.frequency == "minute":
            return self._run_minute(strategy)
        return self._run_daily(strategy)

    def _bind_strategy_id(self, strategy: Any) -> None:
        """按策略名称查询策略模板，将 strategy_id 回填到策略实例。

        查询失败或返回空数据时保持策略原有 strategy_id 不变，
        不中断回测流程（仅在日志中记录告警）。

        :param strategy: 策略实例
        """
        strategy_name: str = getattr(strategy, "strategy_name", "") or ""
        if not strategy_name:
            return
        try:
            info: dict[str, Any] = self._strategy_api.get(strategy_name)
        except Exception as exc:
            self.logger.warning(
                "按名称查询策略 [%s] 失败，跳过回填 strategy_id: %s",
                strategy_name,
                exc,
            )
            return
        if not info:
            self.logger.warning(
                "按名称未查询到策略 [%s]，跳过回填 strategy_id", strategy_name
            )
            return
        strategy_id: str = str(
            info.get("id") or info.get("_id") or info.get("strategy_id") or ""
        )
        if not strategy_id:
            self.logger.warning(
                "策略 [%s] 查询结果缺少 id 字段，跳过回填 strategy_id", strategy_name
            )
            return
        strategy.strategy_id = strategy_id
        self.logger.info(
            "策略 [%s] 已回填 strategy_id=%s", strategy_name, strategy_id
        )

    def _run_daily(
        self, strategy: Any
    ) -> tuple[list[TradeResult], BacktestSummary]:
        """日频回测：逐日模拟每天开盘前操作。

        流程：
        1. 解析股票代码（股票池或显式列表）
        2. 逐日遍历 start_date ~ end_date 的每个交易日：
           - 调用 strategy.before_trading 模拟盘前准备
           - 调用 strategy.select 模拟盘前选股，得到当日候选股票
           - 查询当日日K数据，对候选股票按滑动窗口 + is_match_strategy 检测信号
        3. 信号次日开盘买入，持有 hold_days 个完整交易日后收盘卖出
        4. 计算权益曲线和基准对比，生成汇总

        :param strategy: 策略实例
        :return: (交易记录列表, 回测汇总)
        """
        codes: list[str] = self._resolve_stock_codes()
        if not codes:
            self.logger.warning("无股票代码可用于回测")
            return [], BacktestSummary()

        start_date: str = self.config.start_date
        end_date: str = self.config.end_date
        hold_days: int = self.config.hold_days
        signal_window: int = self.config.signal_window
        strategy_name: str = getattr(strategy, "strategy_name", "Unknown")

        # 获取基准数据
        benchmark_df: pd.DataFrame = self._fetch_benchmark_data(start_date, end_date)

        all_trades: list[TradeResult] = []
        # code -> 全区间日K数据缓存，逐日定位当日信号
        daily_dfs: dict[str, pd.DataFrame] = {}

        # 逐日模拟：每天盘前 before_trading + select，再查询当日日K进行策略判断
        for day in self._iter_trading_days(strategy, start_date, end_date):
            # 1. 盘前准备
            try:
                strategy.before_trading(trade_date=day, stock_codes=codes)
            except Exception as exc:
                self.logger.debug("before_trading 调用异常: %s", exc)

            # 2. 盘前选股，得到当日候选股票
            matched_codes: list[str] = self._select_candidates(
                strategy, codes, signal_window, strategy_name, today_str=day
            )
            self._set_strategy_select_stocks(strategy, matched_codes, day)
            if not matched_codes:
                self.logger.debug("%s 当日无符合条件的股票，跳过", day)
                continue

            # 3. 查询当日日K，对候选股票检测买入信号
            for code in matched_codes:
                df: pd.DataFrame | None = daily_dfs.get(code)
                if df is None:
                    df = self._fetch_stock_data(code, start_date, end_date)
                    if df.empty:
                        continue
                    for col in ("open", "close", "high", "low"):
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors="coerce")
                    daily_dfs[code] = df
                i: int | None = self._find_date_index(df, day)
                # 需要足够的信号历史窗口，且买入后能持有 hold_days 个交易日
                if (
                    i is None
                    or i < signal_window
                    or i + 1 + hold_days >= len(df)
                ):
                    continue
                trade: TradeResult | None = self._check_daily_signal_at(
                    strategy, df, code, strategy_name, i, hold_days, signal_window
                )
                if trade is not None:
                    all_trades.append(trade)

        # 按买入日期排序
        all_trades.sort(key=lambda t: t.buy_date)

        # 生成汇总
        summary: BacktestSummary = self._compute_summary(
            trades=all_trades,
            benchmark_df=benchmark_df,
            strategy_name=strategy_name,
        )

        self._print_summary(summary)
        return all_trades, summary

    def _check_daily_signal_at(
        self,
        strategy: Any,
        df: pd.DataFrame,
        code: str,
        strategy_name: str,
        i: int,
        hold_days: int,
        signal_window: int,
    ) -> TradeResult | None:
        """判断第 i 日是否触发信号，若触发则生成一笔交易。"""
        window: pd.DataFrame = df.iloc[i - signal_window : i + 1]
        window_data: list[dict[str, Any]] = cast(
            "list[dict[str, Any]]",
            window.to_dict(orient="records"),
        )
        try:
            matched, _extra = strategy.is_match_strategy(window_data)
        except Exception as exc:
            self.logger.debug("%s is_match_strategy 异常: %s", code, exc)
            return None
        if not matched:
            return None

        # 买入：信号次日开盘价
        # 卖出：i+1 开盘买入，持有 hold_days 个完整交易日后收盘卖出
        sell_idx: int = i + 1 + hold_days
        if sell_idx >= len(df):
            return None
        buy_row = df.iloc[i + 1]
        buy_price_raw: float = float(buy_row.get("open", 0))
        if buy_price_raw <= 0:
            return None
        sell_row = df.iloc[sell_idx]
        sell_price_raw: float = float(sell_row.get("close", 0))
        if sell_price_raw <= 0:
            return None

        result = self._calc_trade_result(
            buy_price=buy_price_raw, sell_price=sell_price_raw
        )
        buy_date_str = str(buy_row.get("create_time", buy_row.get("date", "")))
        sell_date_str = str(sell_row.get("create_time", sell_row.get("date", "")))
        return TradeResult(
            code=code,
            strategy_name=strategy_name,
            buy_date=buy_date_str,
            sell_date=sell_date_str,
            buy_price=result["buy_price"],
            sell_price=result["sell_price"],
            profit_pct=result["profit_pct"],
        )

    def _run_minute(
        self, strategy: Any
    ) -> tuple[list[TradeResult], BacktestSummary]:
        """分钟频回测：逐日模拟每天开盘前操作。

        流程：
        1. 解析股票代码（股票池或显式列表）
        2. 逐日遍历 start_date ~ end_date 的每个交易日：
           - 调用 strategy.before_trading 模拟盘前准备
           - 调用 strategy.select 模拟盘前选股，得到当日候选股票（并注入选股缓存，
             使 handle_minute_bar 按当日选股列表路由买入/卖出检查）
           - 查询当日分钟K线，对候选股票及持仓股票调用 handle_minute_bar：
             根据返回的 signal（BUY/SELL）进行建仓买入或平仓卖出
        3. 持仓状态跨交易日保留
        4. 生成汇总

        :param strategy: 策略实例，需实现 select / handle_minute_bar
        :return: (交易记录列表, 回测汇总)
        """
        codes: list[str] = self._resolve_stock_codes()
        if not codes:
            self.logger.warning("无股票代码可用于回测")
            return [], BacktestSummary()

        start_date: str = self.config.start_date
        end_date: str = self.config.end_date
        signal_window: int = self.config.signal_window
        strategy_name: str = getattr(strategy, "strategy_name", "Unknown")

        # 获取基准数据
        benchmark_df: pd.DataFrame = self._fetch_benchmark_data(start_date, end_date)

        all_trades: list[TradeResult] = []
        # code -> 跨交易日持仓状态（in_position/cost_price/buy_time）
        position_states: dict[str, dict[str, Any]] = {}

        # 逐日模拟：每天盘前 before_trading + select，再查询当日分钟K线进行策略判断
        for day in self._iter_trading_days(strategy, start_date, end_date):
            # 1. 盘前准备
            try:
                strategy.before_trading(trade_date=day, stock_codes=codes)
            except Exception as exc:
                self.logger.debug("before_trading 调用异常: %s", exc)

            # 2. 盘前选股，得到当日候选股票并注入选股缓存
            matched_codes: list[str] = self._select_candidates(
                strategy, codes, signal_window, strategy_name, today_str=day
            )
            self._set_strategy_select_stocks(strategy, matched_codes, day)

            # 当日处理的股票 = 当日候选股票 ∪ 已有持仓股票（持仓需检查卖出）
            held_codes: list[str] = [
                c for c, st in position_states.items() if st["in_position"]
            ]
            codes_today: list[str] = list(dict.fromkeys(matched_codes + held_codes))
            if not codes_today:
                self.logger.debug("%s 无候选股票且无持仓，跳过", day)
                continue

            day_start: str = f"{day} 09:30:00"
            day_end: str = f"{day} 15:00:00"

            # 3. 查询当日分钟K线，调用 handle_minute_bar 进行策略判断
            for code in codes_today:
                state: dict[str, Any] = position_states.setdefault(
                    code,
                    {"in_position": False, "cost_price": 0.0, "buy_time": ""},
                )
                all_trades.extend(
                    self._run_minute_for_stock(
                        strategy,
                        code,
                        day_start,
                        day_end,
                        signal_window,
                        strategy_name,
                        state,
                    )
                )

        # 按买入日期排序
        all_trades.sort(key=lambda t: t.buy_date)

        # 生成汇总
        summary: BacktestSummary = self._compute_summary(
            trades=all_trades,
            benchmark_df=benchmark_df,
            strategy_name=strategy_name,
        )

        self._print_summary(summary)
        return all_trades, summary

    def _select_candidates(
        self,
        strategy: Any,
        codes: list[str],
        signal_window: int,
        strategy_name: str,
        today_str: str = "",
    ) -> list[str]:
        """调用策略 select 按日线数据筛选当日候选股票。"""
        try:
            if not hasattr(strategy, "select"):
                self.logger.info(f"策略:{strategy_name}，缺少select方法")
            select_results: list[dict[str, Any]] = strategy.select(
                codes=codes, today_str=today_str, days=signal_window
            )
            matched_codes: list[str] = [
                str(r["code"]) for r in select_results if r.get("matched", False)
            ]
            for result in select_results:
                if not result.get("matched", False) and "reason" in result:
                    self.logger.info(result.get("reason", ""))
            self.logger.info(
                "select 筛选 %s: %d 只股票 → %d 只符合条件",
                today_str or "-",
                len(codes),
                len(matched_codes),
            )
        except Exception as exc:
            self.logger.warning("select 筛选异常: %s，使用全量股票池", exc)
            matched_codes = list(codes)
        return matched_codes

    def _iter_trading_days(
        self, strategy: Any, start_date: str, end_date: str
    ) -> list[str]:
        """生成回测区间内的每个交易日（YYYY-MM-DD，含起止日）。

        优先使用策略自带的交易所交易日历（xshg）判断，
        不可用时回退为跳过周末。
        """
        try:
            d1 = datetime.strptime(str(start_date)[:10], "%Y-%m-%d")
            d2 = datetime.strptime(str(end_date)[:10], "%Y-%m-%d")
        except ValueError:
            self.logger.warning("回测日期解析失败: %s ~ %s", start_date, end_date)
            return []
        xshg: Any = getattr(strategy, "xshg", None)
        days: list[str] = []
        current: datetime = d1
        while current <= d2:
            day_str: str = current.strftime("%Y-%m-%d")
            is_session: bool = True
            if xshg is not None and hasattr(xshg, "is_session"):
                try:
                    is_session = bool(xshg.is_session(day_str))
                except Exception:
                    is_session = current.weekday() < 5
            else:
                is_session = current.weekday() < 5
            if is_session:
                days.append(day_str)
            current += timedelta(days=1)
        return days

    @staticmethod
    def _find_date_index(df: pd.DataFrame, day: str) -> int | None:
        """在日K DataFrame 中定位指定日期（YYYY-MM-DD）的行索引。"""
        if df is None or df.empty:
            return None
        time_col: str = "create_time" if "create_time" in df.columns else "date"
        if time_col not in df.columns:
            return None
        for idx, raw in enumerate(df[time_col].astype(str)):
            if str(raw)[:10] == day:
                return idx
        return None

    def _set_strategy_select_stocks(
        self, strategy: Any, matched_codes: list[str], day: str
    ) -> None:
        """把当日 select 选股结果注入策略选股缓存。

        StrategyWorkflow 每日盘前会把 select 结果保存到服务端，
        handle_minute_bar 再按选股列表路由买入/卖出检查；回测中不落库，
        直接写入策略缓存以模拟同样的路由效果。
        """
        try:
            strategy._select_stocks_set = set(matched_codes)
            strategy._select_stocks_date = day
        except Exception as exc:
            self.logger.debug("注入策略选股列表失败: %s", exc)

    def _run_minute_for_stock(
        self,
        strategy: Any,
        code: str,
        start_date: str,
        end_date: str,
        signal_window: int,
        strategy_name: str,
        state: dict[str, Any] | None = None,
    ) -> list[TradeResult]:
        """分钟回测单只股票单个交易日：按 StrategyWorkflow 模式处理分钟K线。

        每根K线调用一次策略的 handle_minute_bar（无持仓时传入空持仓，
        有持仓时传入 cost_price/buy_time 等持仓信息），根据返回信号：
        - BUY 且当前无持仓 → 以当前分钟价格买入建仓
        - SELL 且当前有持仓 → 以当前分钟价格卖出平仓，生成一笔交易
        持仓状态（in_position/cost_price/buy_time）通过 state 跨交易日保留。
        """
        trades: list[TradeResult] = []
        if state is None:
            state = {"in_position": False, "cost_price": 0.0, "buy_time": ""}
        try:
            df: pd.DataFrame = self._fetch_stock_data(code, start_date, end_date)
            if df.empty or len(df) < signal_window:
                self.logger.debug(
                    "%s %s 分钟数据不足: 需至少 %d 条，实际 %d 条",
                    code,
                    start_date[:10],
                    signal_window,
                    len(df),
                )
                return trades

            # 确保数值列类型正确
            for col in ("open", "close", "high", "low", "price"):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            in_position: bool = bool(state["in_position"])
            cost_price: float = float(state["cost_price"])
            buy_time: str = str(state["buy_time"])

            # 当日所有分钟K线均参与策略判断（与 StrategyWorkflow handle_bar 一致）
            for i in range(len(df)):
                current_bar: dict[str, Any] = cast(
                    "dict[str, Any]", df.iloc[i].to_dict()
                )
                # 持仓信息：无持仓传空字典，有持仓传成本价/买入时间等
                # （与策略卖出检查约定一致，如 _check_sell_conditions 读取 cost_price/buy_time）
                position: dict[str, Any] = (
                    {
                        "cost_price": cost_price,
                        "buy_time": buy_time,
                        "quantity": 100,
                    }
                    if in_position
                    else {}
                )

                matched, extra = self._check_minute_bar(
                    strategy, code, position, current_bar
                )
                if not matched:
                    continue

                signal_type: str = str(extra.get("signal", ""))
                if signal_type == "BUY" and not in_position:
                    # 买入：以当前分钟价格成交建仓
                    buy_price_raw: float = float(
                        current_bar.get("price", current_bar.get("close", 0))
                    )
                    if buy_price_raw <= 0:
                        continue
                    cost_price = buy_price_raw
                    buy_time = str(
                        current_bar.get("create_time", current_bar.get("date", ""))
                    )
                    in_position = True
                elif signal_type == "SELL" and in_position:
                    # 卖出：以当前分钟价格成交平仓
                    sell_price_raw: float = float(
                        current_bar.get("price", current_bar.get("close", 0))
                    )
                    if sell_price_raw <= 0:
                        continue
                    trade: TradeResult | None = self._build_minute_trade(
                        strategy_name,
                        code,
                        current_bar,
                        buy_time,
                        cost_price,
                        sell_price_raw,
                    )
                    if trade is not None:
                        trades.append(trade)
                    in_position = False
                    cost_price = 0.0
                    buy_time = ""
                else:
                    self.logger.debug(
                        "%s 信号与持仓状态不匹配，跳过: signal=%s, in_position=%s",
                        code,
                        signal_type,
                        in_position,
                    )

            # 写回跨交易日持仓状态
            state["in_position"] = in_position
            state["cost_price"] = cost_price
            state["buy_time"] = buy_time
        except Exception as exc:
            self.logger.warning("分钟回测 %s 异常: %s", code, exc)
        return trades

    def _check_minute_bar(
        self,
        strategy: Any,
        code: str,
        position: dict[str, Any],
        current_bar: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """按 StrategyWorkflow 方式调用策略 handle_minute_bar 获取买卖信号。

        :param strategy: 策略实例
        :param code: 股票代码
        :param position: 当前持仓信息（无持仓传空字典）
        :param current_bar: 当前分钟K线
        :return: (是否触发信号, 详细信息字典，含 signal=BUY/SELL)
        """
        try:
            matched, extra = strategy.handle_minute_bar(code, position, current_bar)
        except Exception as exc:
            self.logger.debug("%s handle_minute_bar 异常: %s", code, exc)
            return False, {}
        if not matched:
            reason: str = extra.get("reason", "")
            if reason:
                self.logger.info(reason)
            return False, extra
        return True, extra

    def _build_minute_trade(
        self,
        strategy_name: str,
        code: str,
        sell_bar: dict[str, Any],
        buy_time: str,
        cost_price: float,
        sell_price_raw: float,
    ) -> TradeResult:
        """根据买入/卖出分钟数据生成一笔交易记录。"""
        result = self._calc_trade_result(
            buy_price=cost_price, sell_price=sell_price_raw
        )
        sell_date_str = str(
            sell_bar.get("create_time", sell_bar.get("date", ""))
        )
        return TradeResult(
            code=code,
            strategy_name=strategy_name,
            buy_date=buy_time,
            sell_date=sell_date_str,
            buy_price=result["buy_price"],
            sell_price=result["sell_price"],
            profit_pct=result["profit_pct"],
        )

    # ---- 汇总计算 ----

    def _compute_summary(
        self,
        trades: list[TradeResult],
        benchmark_df: pd.DataFrame,
        strategy_name: str,
    ) -> BacktestSummary:
        """根据交易记录和基准数据计算回测汇总指标。

        :param trades: 交易记录列表
        :param benchmark_df: 基准日线 DataFrame
        :param strategy_name: 策略名称
        :return: BacktestSummary 实例
        """
        summary = BacktestSummary()
        summary.strategy_name = strategy_name
        summary.initial_capital = self.config.initial_capital
        summary.start_date = self.config.start_date
        summary.end_date = self.config.end_date
        summary.benchmark_code = self.config.benchmark_code

        if not trades:
            summary.final_capital = self.config.initial_capital
            return summary

        # 基本统计：盈亏为 0 的交易既不算盈利也不算亏损
        profits: list[float] = [t.profit_pct for t in trades]
        summary.total_trades = len(trades)
        summary.win_count = sum(1 for p in profits if p > 0)
        summary.loss_count = sum(1 for p in profits if p < 0)
        decided: int = summary.win_count + summary.loss_count
        summary.win_rate = (
            round(summary.win_count / decided * 100, 2)
            if decided > 0
            else 0.0
        )
        summary.avg_profit = round(sum(profits) / summary.total_trades, 2)
        summary.max_profit = round(max(profits), 2)
        summary.max_loss = round(min(profits), 2)

        # 资金曲线：顺序执行每笔交易
        equity_curve: list[float] = self._compute_equity_curve(trades)
        summary.final_capital = round(equity_curve[-1], 2) if equity_curve else self.config.initial_capital
        total_return = (
            (summary.final_capital - summary.initial_capital)
            / summary.initial_capital
        )
        summary.total_return_pct = round(total_return * 100, 2)
        summary.total_profit = round(
            summary.final_capital - summary.initial_capital, 2
        )

        # 交易天数
        trading_days: int = self._count_trading_days(
            self.config.start_date, self.config.end_date
        )
        summary.trading_days = trading_days

        # 年化收益
        summary.annualized_return_pct = self._compute_annualized_return(
            total_return, trading_days
        )

        # 最大回撤
        summary.max_drawdown_pct = self._compute_max_drawdown(equity_curve)

        # 夏普比率
        daily_returns: list[float] = self._compute_daily_returns(equity_curve)
        summary.sharpe_ratio = self._compute_sharpe(daily_returns)

        # 基准对比
        if not benchmark_df.empty:
            benchmark_return: float = self._compute_benchmark_return(benchmark_df)
            summary.benchmark_return_pct = round(benchmark_return * 100, 2)
            summary.benchmark_annualized_pct = self._compute_annualized_return(
                benchmark_return, trading_days
            )
            summary.alpha = round(
                summary.total_return_pct - summary.benchmark_return_pct, 2
            )

        return summary

    def _compute_equity_curve(self, trades: list[TradeResult]) -> list[float]:
        """计算权益曲线：事件驱动、资金受限的资金管理模型。

        按买入/卖出日期推进时间轴：
        - 买入时从可用资金中划出 position_size_pct 比例的资金（不可重叠占用）；
        - 卖出时释放本金并按 profit_pct 结算盈亏；
        - 可用资金不足时跳过后续买入信号，避免“串行全仓复利”虚高收益。

        :param trades: 交易记录列表（自动按买入日期排序）
        :return: 权益曲线列表（初始资金 + 每个事件后的权益值）
        """
        position_pct: float = self.config.position_size_pct
        ordered: list[TradeResult] = sorted(
            trades, key=lambda t: (t.buy_date, t.sell_date)
        )

        # 构造 (日期, 事件类型, 交易序号) 时间轴；同日买入先于卖出处理
        events: list[tuple[str, int, int]] = []
        for idx, trade in enumerate(ordered):
            events.append((trade.buy_date, 0, idx))
            events.append((trade.sell_date, 1, idx))
        events.sort(key=lambda e: (e[0], e[1]))

        free_cash: float = self.config.initial_capital
        open_invested: dict[int, float] = {}
        equity: list[float] = [free_cash]

        for _date, event_type, idx in events:
            if event_type == 1:  # 卖出：结算盈亏并释放本金
                invested = open_invested.pop(idx, None)
                if invested is not None:
                    free_cash += invested * (1 + ordered[idx].profit_pct / 100)
            else:  # 买入：划出资金
                if idx in open_invested or free_cash <= 1e-9:
                    continue
                invested = min(free_cash * position_pct, free_cash)
                free_cash -= invested
                open_invested[idx] = invested
            equity.append(free_cash + sum(open_invested.values()))

        return equity

    def _compute_daily_returns(self, equity_curve: list[float]) -> list[float]:
        """从权益曲线计算收益率序列（相邻事件权益变化）。"""
        if len(equity_curve) < 2:
            return []
        daily_returns: list[float] = []
        for i in range(1, len(equity_curve)):
            prev: float = equity_curve[i - 1]
            curr: float = equity_curve[i]
            if prev > 0:
                daily_returns.append((curr - prev) / prev)
        return daily_returns

    @staticmethod
    def _compute_sharpe(daily_returns: list[float]) -> float:
        """计算夏普比率。

        :param daily_returns: 日收益率序列
        :return: 年化夏普比率
        """
        if not daily_returns or len(daily_returns) < 2:
            return 0.0
        arr: np.ndarray = np.array(daily_returns, dtype=np.float64)
        daily_rf: float = BacktestEngine.RISK_FREE_RATE / BacktestEngine.TRADING_DAYS_PER_YEAR
        excess: np.ndarray = arr - daily_rf
        mean_excess: float = float(np.mean(excess))
        std_excess: float = float(np.std(excess, ddof=1))
        # 标准差极小（收益率几乎恒定）时 Sharpe 无意义，返回 0
        if std_excess < 1e-10:
            return 0.0
        sharpe: float = (mean_excess / std_excess) * np.sqrt(
            BacktestEngine.TRADING_DAYS_PER_YEAR
        )
        return round(sharpe, 4)

    @staticmethod
    def _compute_max_drawdown(equity_curve: list[float]) -> float:
        """计算最大回撤（%）。

        :param equity_curve: 权益曲线
        :return: 最大回撤百分比
        """
        if not equity_curve or len(equity_curve) < 2:
            return 0.0
        peak: float = equity_curve[0]
        max_dd: float = 0.0
        for val in equity_curve:
            if val > peak:
                peak = val
            dd: float = (peak - val) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return round(max_dd * 100, 2)

    @staticmethod
    def _compute_annualized_return(total_return: float, trading_days: int) -> float:
        """计算年化收益率（%）。

        :param total_return: 总收益率（小数形式，如 0.15 = 15%）
        :param trading_days: 实际交易天数
        :return: 年化收益率百分比
        """
        if trading_days <= 0 or total_return <= -1:
            return 0.0
        years: float = trading_days / BacktestEngine.TRADING_DAYS_PER_YEAR
        if years <= 0:
            return 0.0
        annualized: float = (1 + total_return) ** (1.0 / years) - 1
        return round(annualized * 100, 2)

    @staticmethod
    def _count_trading_days(start_date: str, end_date: str) -> int:
        """估算起止日期之间的交易日数。

        :param start_date: 开始日期 YYYY-MM-DD
        :param end_date: 结束日期 YYYY-MM-DD
        :return: 估算交易日数
        """
        if not start_date or not end_date:
            return 0
        try:
            d1 = datetime.strptime(start_date, "%Y-%m-%d")
            d2 = datetime.strptime(end_date, "%Y-%m-%d")
            calendar_days: int = (d2 - d1).days
            if calendar_days <= 0:
                return 0
            # 近似：日历天数的 5/7 为交易日
            return max(int(calendar_days * 5 / 7), 1)
        except ValueError:
            return 0

    def _compute_benchmark_return(self, benchmark_df: pd.DataFrame) -> float:
        """计算基准的买入持有收益率。

        使用数据中的第一个收盘价作为买入价，最后一个收盘价作为卖出价。

        :param benchmark_df: 基准日线 DataFrame
        :return: 基准收益率（小数形式）
        """
        if benchmark_df.empty:
            return 0.0
        close_col: str = "close"
        if close_col not in benchmark_df.columns:
            return 0.0
        closes: pd.Series = pd.to_numeric(
            benchmark_df[close_col], errors="coerce"
        ).dropna()
        if len(closes) < 2:
            return 0.0
        first_close: float = float(closes.iloc[0])
        last_close: float = float(closes.iloc[-1])
        if first_close <= 0:
            return 0.0
        return (last_close - first_close) / first_close

    # ---- 结果持久化 ----

    def save_results(
        self,
        strategy: Any,
        trades: list[TradeResult],
        summary: BacktestSummary,
        strategy_id: str = "",
        user_strategy_id: str = "",
    ) -> bool:
        """将回测结果通过 API 保存到服务器。

        回测结果按策略模板 ID 关联，写入 /api/strategy/{strategy_id}/backtest；
        若同时提供 user_strategy_id，再同步一份策略执行指标（供策略页面展示）。

        :param strategy: 策略实例
        :param trades: 交易记录列表
        :param summary: 回测汇总
        :param strategy_id: 策略模板 ID（StrategyDao._id），为空时尝试取 strategy.strategy_id
        :param user_strategy_id: 用户策略关联 ID（可选，用于同步执行指标）
        :return: 是否保存成功
        """
        strategy_name: str = getattr(strategy, "strategy_name", "Unknown")
        if not strategy_id:
            strategy_id = str(getattr(strategy, "strategy_id", "") or "")
        success: bool = True

        # 保存完整回测结果
        if strategy_id:
            try:
                result_data: dict[str, Any] = {
                    "strategy_name": summary.strategy_name,
                    "backtest_return_rate": summary.total_return_pct / 100,
                    "backtest_profit": summary.total_profit,
                    "benchmark_return_rate": summary.benchmark_return_pct / 100,
                    "start_date": summary.start_date,
                    "end_date": summary.end_date,
                    "initial_amount": summary.initial_capital,
                    "max_drawdown": summary.max_drawdown_pct / 100,
                    "frequency": self.config.frequency,
                    "trades": [
                        {
                            "code": t.code,
                            "buy_date": t.buy_date,
                            "sell_date": t.sell_date,
                            "buy_price": t.buy_price,
                            "sell_price": t.sell_price,
                            "profit_pct": t.profit_pct,
                        }
                        for t in trades
                    ],
                    "summary": {
                        "total_trades": summary.total_trades,
                        "win_rate": summary.win_rate,
                        "avg_profit": summary.avg_profit,
                        "max_profit": summary.max_profit,
                        "max_loss": summary.max_loss,
                        "annualized_return_pct": summary.annualized_return_pct,
                        "sharpe_ratio": summary.sharpe_ratio,
                        "max_drawdown_pct": summary.max_drawdown_pct,
                        "alpha": summary.alpha,
                    },
                }
                self._strategy_api.save_backtest(strategy_id, result_data)
                self.logger.info(
                    "回测结果已保存: %s (strategy_id=%s)", strategy_name, strategy_id
                )
            except HTTPStatusError as e:
                self.logger.error("保存回测结果失败: %s", e.response.text)
                success = False
            except Exception as exc:
                self.logger.error("保存回测结果失败: %s", exc)
                success = False
        else:
            self.logger.warning(
                "缺少 strategy_id，跳过保存回测结果: %s", strategy_name
            )
            success = False

        # 可选：同步策略执行指标（需 user_strategy_id）
        if user_strategy_id:
            try:
                self._user_strategy_api.save_execution(
                    user_strategy_id=user_strategy_id,
                    current_return_rate=summary.total_return_pct / 100,
                    current_profit=summary.total_profit,
                    annualized_return_rate=summary.annualized_return_pct / 100,
                    benchmark_return_rate=summary.benchmark_return_pct / 100,
                    positions=[],
                    initial_amount=summary.initial_capital,
                    remaining_cash=0.0,
                    start_date=summary.start_date,
                    execution_days=summary.trading_days,
                )
                self.logger.info("策略执行指标已保存: %s (user_strategy_id=%s)", strategy_name, user_strategy_id)
            except Exception as exc:
                self.logger.error("保存策略执行指标失败: %s", exc)
                success = False

        return success

    # ---- 控制台输出 ----

    @staticmethod
    def _print_summary(summary: BacktestSummary) -> None:
        """打印回测汇总结果到控制台。

        :param summary: 回测汇总
        """
        print("=" * 60)
        print(f"  策略: {summary.strategy_name}")
        print(f"  回测区间: {summary.start_date} ~ {summary.end_date}")
        print(f"  交易天数: {summary.trading_days}")
        print("-" * 60)
        print(f"  初始资金: {summary.initial_capital:,.2f}")
        print(f"  最终资金: {summary.final_capital:,.2f}")
        print(f"  总收益: {summary.total_profit:,.2f}")
        print(f"  总收益率: {summary.total_return_pct}%")
        print(f"  年化收益率: {summary.annualized_return_pct}%")
        print("-" * 60)
        print(f"  总交易次数: {summary.total_trades}")
        print(f"  盈利次数: {summary.win_count}")
        print(f"  亏损次数: {summary.loss_count}")
        print(f"  胜率: {summary.win_rate}%")
        print(f"  平均收益: {summary.avg_profit}%")
        print(f"  最大单笔收益: {summary.max_profit}%")
        print(f"  最大单笔亏损: {summary.max_loss}%")
        print("-" * 60)
        print(f"  夏普比率: {summary.sharpe_ratio}")
        print(f"  最大回撤: {summary.max_drawdown_pct}%")
        print("-" * 60)
        if summary.benchmark_code:
            print(f"  基准 ({summary.benchmark_code}):")
            print(f"    基准收益: {summary.benchmark_return_pct}%")
            print(f"    基准年化: {summary.benchmark_annualized_pct}%")
            print(f"    超额收益 (Alpha): {summary.alpha}%")
        print("=" * 60)
