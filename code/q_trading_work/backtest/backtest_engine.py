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
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, cast

from httpx import HTTPStatusError
import numpy as np
import pandas as pd

from api.backtest import BacktestApi
from api.client import ApiClient
from api.market import MarketApi
from api.pool import PoolApi
from api.stock_info import StockInfoApi
from api.strategy import StrategyApi
from api.user_strategy import UserStrategyApi
from app_context import AppContext
from dao.backtest_dao import BacktestDao
from dao.local_hq_dao import (
    KIND_DAY,
    KIND_MINUTE,
    MODE_OFF,
    MODE_ONLY,
    VALID_MODES,
    LocalHqDao,
    LocalHqError,
)
from strategy.base_strategy import MarketDataFetchError
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
        benchmark_code: 基准代码，**默认关闭（空字符串）** —— 服务端日K接口
            暂不支持指数代码（P1-27），填了只会拿到取数失败；后台接入指数行情后
            在 cfg/stock.cfg [benchmark] code= 配置即可启用（与 TradeManager 口径一致）
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
        fetch_retries: 行情取数失败的额外重试次数（回测数据完整性要求，默认重试 2 次）
        fetch_retry_interval: 取数重试间隔秒数
    """

    initial_capital: float = 100000.0
    # 基准默认关闭：服务端日K不支持指数代码（P1-27），默认填代码只会取数失败。
    # 与 TradeManager.DEFAULT_BENCHMARK_CODE 保持一致；后台有指数行情后再启用。
    benchmark_code: str = ""
    pool_id: str = ""
    pool_name: str = ""
    stock_codes: list[str] | None = None
    start_date: str = ""
    end_date: str = ""
    hold_days: int = 5
    # 信号检测窗口（日线根数）。2026-09-28 由 20 放大到 40：MultiTrendFactor 的
    # MACD 分项需要 34 根出值（26+9−1），判定又要与上一根比较 ⇒ 实际需 35 根才成立，
    # 20 根时恒为 NaN，趋势分上限静默折损 3 分。取 40 留余量。
    # 必须与 StrategyWorkflow.SELECT_KLINE_DAYS 同值，否则实盘选股与回测的
    # 趋势分不可比（同一策略在两条链路上选出的股票集合会分叉）。
    signal_window: int = 40
    frequency: str = "daily"
    trade_config: TradeCostConfig | None = None
    position_size_pct: float = 1.0
    # 回测必须保证数据完整：取数失败先重试，重试后仍失败即中止回测并报错，
    # 绝不静默降级成空表继续算（否则会写出一条「看似正常」的不完整回测记录）。
    # 实时链路相反 —— 一次失败即记 warning + 透出 fetch_failed，不做重试阻塞。
    fetch_retries: int = 2
    fetch_retry_interval: float = 1.0
    # 账户级回撤阈值（%）。None 表示取策略实例的 `RISK_MAX_DRAWDOWN_PCT`
    # （默认 -10.0）；显式传值可覆盖（CLI `--max-drawdown`）。0 或正数表示关闭。
    #
    # 语义（2026-09-29 改）：账户已实现权益自峰值回撤达阈值时，**当日收盘卖出全部在持仓位**
    # ——把回撤当作卖出条件，而不是冻结开新仓。原「熔断停手」实现在实测中被证伪：
    # 权益按成本计价、只在卖出结算时变动，因此回撤是离散跳跃的，只要单笔亏损可能超过
    # 阈值，「回撤 ≤ 阈值」在数学上就不成立；且熔断后不再交易、权益不再变动，回撤也无法
    # 收窄，等于一次触发即永久停手（实测阈值 -5 与 -10 给出完全相同的结果）。
    #
    # 判定口径与汇总的 `max_drawdown_pct` 同源 —— 都基于 `_compute_equity_curve`
    # 的「可用现金 + 持仓成本」权益。
    max_drawdown_pct: float | None = None
    # ---- 信号风暴过滤（2026-10-11）----
    # 与实盘 `StrategyWorkflow._save_trade_signal` 同语义：**同一天内、同方向**的连续
    # 信号，若「距上一条通过过滤的信号 < storm_time_gap_minutes」**或**「价格变化 <
    # storm_price_change_pct」则被抑制（两个条件都得达到才放行），不产生成交。
    # 实盘该过滤常开，参数在 `cfg/stock.cfg [signal_filter]`（默认 10 分钟 / 2.0%）；
    # 回测默认 **关闭**以免历史回测数字失去可比性，用 CLI `--storm-filter` 显式开启。
    #
    # 只在分钟频生效：日频同一股票一天只有一个信号，同方向连续信号必然跨日，
    # 而该过滤（与实盘一致）只比同一天，所以日频天然不会被触发。
    storm_filter_enabled: bool = False
    storm_time_gap_minutes: float = 10.0
    storm_price_change_pct: float = 2.0
    # 任意两次**成交**之间的最小间隔（分钟）。0 = 关闭（默认）。
    #
    # 与上面的「信号风暴过滤」是**两个不同机制**，别混：
    #   风暴过滤 —— 只对**同方向**连续信号生效（防同一方向重复下单），
    #                因此压不住「刚卖出又买回」这种跨方向的高频进出；
    #   本开关   —— 不分方向，直接限制成交频率，才是「每次买卖间隔」的语义。
    min_trade_interval_minutes: float = 0.0
    # ---- 盘中买入门槛（2026-10-14）----
    # 每只股票缓存**上一次符合买入条件**的价格与时刻（`_buy_dip_blocked` 的 probe）。
    # 本次价格跌破该价格、且距上一次不足 `buy_dip_gap_minutes` 分钟时，本次买入信号
    # 被抑制。**被抑制也更新缓存**，于是阴跌过程中每来一个买点都会把窗口清零，
    # 不会出现「等满 10 分钟就补一单」；只有价格重新站上上次买点（或间隔足够）
    # 才放行。只作用于买入，卖出不受影响；跨日缓存必然超过窗口，等于自动放行。
    #
    # 与实盘同语义（`StrategyWorkflow._save_trade_signal` 的 `_last_buy_probe`），
    # 窗口值与 `cfg/stock.cfg [signal_filter] time_gap_minutes` 同源。
    # 默认关闭，用 CLI `--buy-dip-gate` 显式开启（与其余过滤保持同一约定）。
    buy_dip_gate_enabled: bool = False
    # 窗口分钟数；<= 0 视为关闭
    buy_dip_gap_minutes: float = 10.0
    # 资金按标的均分（2026-10-12）：开启后把 `initial_capital` 平均分给回测涉及的
    # 每只股票（`universe_size` 只），各自独立周转、互不挤占。
    #
    # 关闭时是「全局单一资金池」：先出现的信号占满资金、其余标的的信号被大量跳过 ——
    # 实测 6 只持仓池 413 笔信号只有 255 笔成交，且同一只票单跑成交 36 笔、进池后
    # 只剩 14 笔。那种结果既不反映各标的自身表现，也会随标的顺序漂移。
    split_capital_by_code: bool = False
    # ---- 每日门槛复算（2026-10-13）----
    # 实盘的「盘后个股阀门 → 次日门槛」链路不在策略内部，而在 `StrategyWorkflow`：
    # 盘后按当日信号时间 + 分钟缓存算阀门入库，次日盘前注入。回测没有这条链路，
    # 因此在 `_run_minute_inner` 里就地复刻一份同样口径的（见 `_inject_daily_gates`）。
    #
    # 默认**关闭**以免历史回测数字失去可比性：开启后同一份数据的结果会变，
    # 只有显式打开才说明「这是带门槛的结果」。
    #
    # 注意：日线趋势门槛（TREND_GATE_ENABLED）**不**受本开关控制 ——
    # 它在策略 `before_trading` 中自行判定，回测调 `before_trading` 时即已生效。
    enable_daily_gates: bool = False
    # 涨跌停家数 CSV（列：date,limit_up,limit_down；date 为 YYYYMMDD）。
    #
    # 非空即启用「市场级门槛偏移」与「极端利空禁买」。CSV 查不到的交易日按
    # 「取数失败」处理（偏移 0、不触发禁买），与实盘 fail-open 一致 ——
    # 真实可得区间只有最近约 30 个交易日（akshare 限制），更早的日期在 CSV 里
    # 是空值，因此回测前半段这段机制天然不生效。
    market_breadth_csv: str = ""
    # ---- 本地归档行情（2026-10-15）----
    # 服务端日/周/月K 与分钟统一只保留 90 天，其中分钟没有回源路径，出窗即永久
    # 丢失。q_trading_archive 把这些数据按日转存成 parquet，开启后回测改读本地：
    # 区间不再被 90 天窗口锁死，且跨时段重跑结果可复现 —— 服务端会对缺失区间
    # 回源补齐，同一历史区间的返回内容会随时间变化，本地文件不会。
    #
    # root 为空即完全不启用，行为与改动前一致。cfg/stock.cfg 的 [local_hq] 节
    # 提供默认值，CLI 参数（--local-hq-dir / --local-hq-mode）优先级更高。
    local_hq_root: str = ""
    # off / prefer / only，语义见 dao/local_hq_dao.py 的模块说明。
    #   prefer：本地覆盖完整才用本地；覆盖不足则整体回落 HTTP
    #   only  ：覆盖不足或读取失败直接中止回测，绝不静默降级成空表
    local_hq_mode: str = MODE_OFF


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
    # 原始买入价（不含滑点/手续费）。回撤重放需要按新的卖出日重新计算有效价与收益率，
    # 而 `_calc_trade_result` 的入参是原始价，因此必须留一份原始值（见 _apply_drawdown_exit）。
    buy_price_raw: float = 0.0
    # 信号分值（10 分制）与星级（score/2 夹到 0~5），取自触发信号那根 K 线的 extra。
    # 买入侧两条路径（日频选股 / 分钟触发）都能拿到；卖出侧只有分钟频有信号 ——
    # 日频是「持有 N 日到期」的固定卖出，不产生信号 ⇒ sell_score / sell_star 恒为 0.0。
    buy_score: float = 0.0
    buy_star: float = 0.0
    sell_score: float = 0.0
    sell_star: float = 0.0


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
    # 账户级回撤触发出场的笔数（0 = 未启用或从未触发）。
    # 与 `max_drawdown_pct` 的区别：后者是事后统计的最大回撤，前者是实际被回撤条件
    # 提前卖出（未到期即平仓）的交易笔数。
    drawdown_exit_trades: int = 0


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

    或一步到位（回测结束即写库）::

        trades, summary = engine.run(strategy, save=True, strategy_id="s-xxx")
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
        self._backtest_api: BacktestApi = BacktestApi(self._admin_client)
        self._user_strategy_api: UserStrategyApi = UserStrategyApi(self._admin_client)
        self._trade_manager: TradeManager = TradeManager(api_client=self._admin_client)
        self._market_api: MarketApi = MarketApi(self._admin_client)
        self._pool_api: PoolApi = PoolApi(self._admin_client)
        self._stock_info_api: StockInfoApi = StockInfoApi(self._admin_client)

        # ---- 本地归档行情读取器（None = 未启用，取数全走 HTTP）----
        # 模式非法时直接报错而不是退化成 off：写错了模式的意图一定是想用本地数据，
        # 静默退回 HTTP 会得到「看起来正常但来源不对」的回测结果。
        if self.config.local_hq_mode not in VALID_MODES:
            raise ValueError(
                f"local_hq_mode 非法: {self.config.local_hq_mode!r}，可选 {VALID_MODES}"
            )
        if self.config.local_hq_root and self.config.local_hq_mode == MODE_OFF:
            self.logger.warning(
                "配置了 local_hq_root=%s 但 local_hq_mode=off，本地归档不会被读取",
                self.config.local_hq_root,
            )
        self._local_hq: LocalHqDao | None = (
            LocalHqDao(self.config.local_hq_root)
            if self.config.local_hq_root and self.config.local_hq_mode != MODE_OFF
            else None
        )
        if self._local_hq is not None:
            self.logger.info(
                "本地归档行情已启用: root=%s mode=%s",
                self.config.local_hq_root,
                self.config.local_hq_mode,
            )
        # 涨跌停分档配置惰性缓存（None = 尚未读取，见 _market_bands_config）
        self._market_bands_cache: tuple[
            tuple[tuple[float, float], ...], float, float
        ] | None = None

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

    def _call_market_api(self, description: str, fetch: Callable[[], Any]) -> Any:
        """带重试地调用行情接口；重试后仍失败则抛 `MarketDataFetchError`。

        回测是离线计算：「没取到数据」与「该股确实没有数据」必须严格区分。
        接口抛异常说明这次没取到，**不能**降级成空表继续算 ——
        否则整段回测会基于不完整数据得出失真结论，并写成一条看似正常的
        回测记录（这正是「同一配置结果不可复现」的成因之一）。

        :param description: 出错信息里的描述（如 "000001 日K"）
        :param fetch: 无参可调用对象，内部完成一次行情请求
        :return: 接口返回的原始数据
        :raises MarketDataFetchError: 重试后仍失败
        """
        attempts: int = max(int(self.config.fetch_retries), 0) + 1
        interval: float = max(float(self.config.fetch_retry_interval), 0.0)
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return fetch()
            except Exception as exc:
                last_exc = exc
                if attempt < attempts:
                    self.logger.warning(
                        "%s 取数失败(第 %d/%d 次): %s，%.1fs 后重试",
                        description, attempt, attempts, exc, interval,
                    )
                    if interval > 0:
                        time.sleep(interval)
                else:
                    self.logger.error(
                        "%s 取数失败(第 %d/%d 次): %s",
                        description, attempt, attempts, exc,
                    )

        error_msg: str = (
            f"{description} 取数失败（重试 {attempts} 次后仍失败）: {last_exc}"
            " —— 数据不完整，回测中止，不产出回测记录"
        )
        raise MarketDataFetchError(error_msg) from last_exc

    def _try_local_kline(
        self, code: str, start_date: str, end_date: str
    ) -> list[dict[str, Any]] | None:
        """尝试从本地归档取 K 线。

        返回 None 表示「本次不用本地，调用方继续走 HTTP」；返回列表（可能为空）
        表示本地已覆盖该区间，以本地为准。空列表意味着这只股票在这段时间里确实
        没有记录（停牌、未上市，或归档时它的行情没被订阅到），而不是取数失败。

        为什么本地覆盖完整时不再回落 HTTP：逐只股票混用两个来源，会让同一次
        回测里一部分数据来自本地、一部分来自服务端，而服务端对缺失区间会回源
        补齐，返回内容随调用时间变化，结果就不可复现了。

        :raises LocalHqError: local_hq_mode=only 且本地覆盖不足
        """
        dao = self._local_hq
        if dao is None:
            return None

        kind = KIND_MINUTE if self.config.frequency == "minute" else KIND_DAY
        # 分钟频率传进来的起点带时分，覆盖判断按自然日做
        start, end = start_date[:10], end_date[:10]

        ok, reason = dao.coverage(kind, start, end)
        if not ok:
            dao.stats.coverage_miss += 1
            message = f"本地 {kind} 归档未覆盖 {start} ~ {end}：{reason}"
            if self.config.local_hq_mode == MODE_ONLY:
                # fail-closed：宁可中止，也不产出一条来源不完整的回测记录
                raise LocalHqError(message + "（local_hq_mode=only）")
            self.logger.warning("%s，整体回落服务端 HTTP", message)
            return None

        records = dao.get_kline(kind, code, start, end)
        if records:
            dao.stats.local_hits += 1
            return records

        dao.stats.local_empty += 1
        self.logger.info(
            "本地 %s 归档无 %s 记录（%s ~ %s），按无数据处理",
            kind,
            code,
            start,
            end,
        )
        return []

    def _fetch_stock_data(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """根据配置的频率获取股票K线数据。

        取数失败（接口异常）按配置重试；重试后仍失败抛 `MarketDataFetchError`，
        由上层中止整个回测。接口正常返回空列表表示「无数据」，返回空表即可。

        启用本地归档（`local_hq_root`）时优先读本地 parquet，命中判断见
        `_try_local_kline`。

        :param code: 股票代码
        :param start_date: 开始日期
        :param end_date: 结束日期
        :return: K线 DataFrame，至少包含 open/close/create_time 列
        :raises MarketDataFetchError: 行情接口重试后仍失败
        :raises LocalHqError: local_hq_mode=only 且本地覆盖不足
        """
        freq_label: str = "分钟" if self.config.frequency == "minute" else "日"

        # 本地优先。返回 None 表示「本次不用本地」，继续走服务端。
        local_records = self._try_local_kline(code, start_date, end_date)

        if local_records is None:

            def _fetch() -> Any:
                if self.config.frequency == "minute":
                    return self._market_api.get_minute_kline(
                        code=code, start=start_date, end=end_date
                    )
                return self._market_api.get_day_kline(
                    code=code, start=start_date, end=end_date
                )

            raw: Any = self._call_market_api(f"{code} {freq_label}K", _fetch)
        else:
            raw = local_records

        if not raw:
            # 接口正常但无数据：不是取数失败，由调用方按「无数据」处理
            self.logger.info("%s %sK线无数据: %s~%s", code, freq_label, start_date, end_date)
            return pd.DataFrame()
        df = pd.DataFrame(raw)
        # 统一列名
        if "date" in df.columns and "create_time" not in df.columns:
            df["create_time"] = df["date"]
        if "code" not in df.columns:
            df["code"] = code
        return df

    def _fetch_benchmark_data(
        self, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取基准（如沪深300）的K线数据，频率与回测配置一致。

        基准未配置（`benchmark_code` 为空）时直接返回空表 —— 服务端日K
        接口当前不支持指数代码，基准默认关闭。一旦配置了基准，取数失败同样
        按「重试 → 仍失败即中止」处理：基准不完整会让超额收益失真。

        :param start_date: 开始日期
        :param end_date: 结束日期
        :return: 基准K线 DataFrame
        :raises MarketDataFetchError: 基准行情接口重试后仍失败
        """
        if not self.config.benchmark_code:
            return pd.DataFrame()

        freq_label: str = "分钟" if self.config.frequency == "minute" else "日"

        def _fetch() -> Any:
            if self.config.frequency == "minute":
                return self._market_api.get_minute_kline(
                    code=self.config.benchmark_code,
                    start=start_date,
                    end=end_date,
                )
            return self._market_api.get_day_kline(
                code=self.config.benchmark_code,
                start=start_date,
                end=end_date,
            )

        raw: Any = self._call_market_api(
            f"基准 {self.config.benchmark_code} {freq_label}K", _fetch
        )
        if not raw:
            self.logger.warning("基准 %s 无数据", self.config.benchmark_code)
            return pd.DataFrame()
        df = pd.DataFrame(raw)
        if "date" in df.columns and "create_time" not in df.columns:
            df["create_time"] = df["date"]
        return df

    # ---- 主回测逻辑 ----

    def run(
        self,
        strategy: Any,
        save: bool = False,
        strategy_id: str = "",
        user_strategy_id: str = "",
    ) -> tuple[list[TradeResult], BacktestSummary]:
        """执行回测，根据配置的频率分发到日频或分钟频回测。

        回测结束后**默认不写库**；需要持久化时显式传 `save=True`
        （或自行调用 `save_results()`），此时会构建 `BacktestDao` 并经
        `BacktestApi` 写入 `POST /api/strategy/{strategy_id}/backtest`。

        :param strategy: 策略实例或策略类（类会被自动实例化）
        :param save: 是否在回测结束后保存结果到服务端
        :param strategy_id: 策略模板 ID（StrategyDao._id），为空时用回填到策略实例的值
        :param user_strategy_id: 用户策略关联 ID（可选，额外同步策略执行指标）
        :return: (交易记录列表, 回测汇总)
        :raises MarketDataFetchError: 行情取数重试后仍失败 —— 回测中止且不写库
        """
        # 如果传入的是类而非实例，自动实例化
        if inspect.isclass(strategy):
            strategy = strategy()
        # 按策略名称查询策略模板，把 strategy_id 回填到策略实例，
        # 供 handle_minute_bar 内部按 id 加载当日选股列表并路由买入/卖出检查
        self._bind_strategy_id(strategy)
        if strategy_id:
            # 显式传入的 strategy_id 优先级最高（覆盖按名称回填的结果）
            strategy.strategy_id = strategy_id

        try:
            if self.config.frequency == "minute":
                trades, summary = self._run_minute(strategy)
            else:
                trades, summary = self._run_daily(strategy)
        except MarketDataFetchError:
            # C 决策：行情数据不完整即中止，并且**不写库** ——
            # 绝不落一条「看似正常」的不完整回测记录（那会让同一配置的
            # 重复回测结果不可复现，且把数据故障伪装成策略表现）
            self.logger.error(
                "回测中止：行情数据不完整（策略: %s），本次不保存回测结果",
                getattr(strategy, "strategy_name", "Unknown"),
                exc_info=True,
            )
            raise

        if save:
            if not self.save_results(
                strategy,
                trades,
                summary,
                strategy_id=strategy_id,
                user_strategy_id=user_strategy_id,
            ):
                self.logger.warning(
                    "回测结果保存失败（策略: %s）",
                    getattr(strategy, "strategy_name", "Unknown"),
                )
        return trades, summary

    def _run_minute(self, strategy: Any) -> tuple[list[TradeResult], BacktestSummary]:
        """分钟频回测入口：关闭缓存 Redis 持久化后执行，结束后恢复。

        回测是离线计算：历史日期写入实盘共用 Redis 既无意义又会污染缓存，
        还可能让 `_get_minute_cache` 读到实盘当日行情，因此这里临时关闭
        `UserStrategyCache` 的 Redis 通道，只使用进程内快照。
        """
        from core.user_strategy_cache import UserStrategyCache

        cache: UserStrategyCache = UserStrategyCache()
        cache.set_redis_enabled(False)
        try:
            return self._run_minute_inner(strategy)
        finally:
            cache.set_redis_enabled(True)

    def _bind_strategy_id(self, strategy: Any) -> None:
        """按策略名称（失败则回退类名）查询策略模板，把 strategy_id 回填到策略实例。

        匹配顺序：

        1. **策略名称** `strategy_name`（沿用历史行为，按
           `/api/strategy/{name}` 查询）；
        2. 名称未命中时退化为**类名** `class_name` —— 服务端策略模板的
           `class_name` 字段与本端策略类名一致，但模板的显示名（`name`）
           可能与类内 `strategy_name` 不同（实测：本端「强势反弹策略」对应
           后台模板「强趋势策略」，本端「低频波段交易策略」对应「波段交易策略」）。
           只按名称查会 404，导致 strategy_id 回填失败、`run(save=True)`
           静默跳过写库，分钟频回测也无法按 id 取当日选股列表。

        查询失败或返回空数据时保持策略原有 strategy_id 不变，
        不中断回测流程（仅在日志中记录告警）。

        :param strategy: 策略实例
        """
        strategy_name: str = getattr(strategy, "strategy_name", "") or ""
        class_name: str = type(strategy).__name__

        info: dict[str, Any] | None = None
        matched_by: str = ""
        if strategy_name:
            info = self._query_template_by_name(strategy_name)
            if info:
                matched_by = f"名称[{strategy_name}]"
        if not info and class_name:
            info = self._query_template_by_class_name(class_name)
            if info:
                matched_by = f"类名[{class_name}]"

        if not info:
            self.logger.warning(
                "未查询到策略模板（名称=%s，类名=%s），跳过回填 strategy_id",
                strategy_name or "-",
                class_name or "-",
            )
            return

        strategy_id: str = str(
            info.get("id") or info.get("_id") or info.get("strategy_id") or ""
        )
        if not strategy_id:
            self.logger.warning(
                "策略模板（按%s匹配）缺少 id 字段，跳过回填 strategy_id", matched_by
            )
            return
        strategy.strategy_id = strategy_id
        self.logger.info(
            "策略已回填 strategy_id=%s（按%s匹配）", strategy_id, matched_by
        )

    def _query_template_by_name(self, name: str) -> dict[str, Any] | None:
        """按策略名称查询模板（/api/strategy/{name}），未命中返回 None。

        :param name: 策略名称（本端类内 strategy_name）
        :return: 策略模板字典，未命中或查询失败返回 None
        """
        try:
            info: Any = self._strategy_api.get(name)
        except Exception as exc:
            # 名称对不上时会 404，属预期路径，降级为 debug 避免噪音
            self.logger.debug("按名称查询策略 [%s] 未命中: %s", name, exc)
            return None
        return dict(info) if info else None

    def _query_template_by_class_name(
        self, class_name: str
    ) -> dict[str, Any] | None:
        """按类名在策略模板列表中查找（class_name 精确匹配）。

        :param class_name: 本端策略类名，如 StrongReboundStrategy
        :return: 策略模板字典，未命中或查询失败返回 None
        """
        try:
            templates: list[dict[str, Any]] = self._strategy_api.list()
        except Exception as exc:
            self.logger.warning("查询策略模板列表失败: %s", exc)
            return None
        matched: list[dict[str, Any]] = [
            t
            for t in (templates or [])
            if str(t.get("class_name") or "") == class_name
        ]
        if not matched:
            return None
        if len(matched) > 1:
            self.logger.warning(
                "类名 [%s] 匹配到 %d 条策略模板，取第一条", class_name, len(matched)
            )
        return matched[0]

    def _run_daily(
        self, strategy: Any
    ) -> tuple[list[TradeResult], BacktestSummary]:
        """日频回测入口：关闭缓存 Redis 持久化后执行，结束后恢复。

        与 `_run_minute` 对称 —— 回测是离线计算：历史日期写进实盘共用的 Redis
        既无意义，又会污染 `select:` / `score:` 键；更严重的是**进程内快照与
        Redis 会被下一次回测读到**，使同一配置重复回测得到不同结果
        （实测同一配置得到 2 / 0 / 3 / 4 笔交易）。因此日频同样只走进程内快照。

        :param strategy: 策略实例
        :return: (交易记录列表, 回测汇总)
        """
        from core.user_strategy_cache import UserStrategyCache

        cache: UserStrategyCache = UserStrategyCache()
        cache.set_redis_enabled(False)
        try:
            return self._run_daily_inner(strategy)
        finally:
            cache.set_redis_enabled(True)

    def _run_daily_inner(
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

        # 账户级回撤阈值：达阈值时当日收盘卖出在持仓位（不是冻结开新仓，见 _apply_drawdown_exit）
        breaker_pct: float = self._resolve_breaker_pct(strategy)
        trading_days: list[str] = self._iter_trading_days(strategy, start_date, end_date)

        # 逐日模拟：每天盘前 before_trading + select，再查询当日日K进行策略判断
        for day in trading_days:
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

        # 账户级回撤约束：达阈值即卖出在持仓位。放在全部信号生成之后重放
        # （不改建仓与到期卖出的规则，只把回撤触发日之前的仓位提前平掉）。
        all_trades, exit_trades = self._apply_drawdown_exit(
            all_trades, daily_dfs, breaker_pct, trading_days
        )

        # 按买入日期排序
        all_trades.sort(key=lambda t: t.buy_date)

        # 生成汇总
        summary: BacktestSummary = self._compute_summary(
            trades=all_trades,
            benchmark_df=benchmark_df,
            strategy_name=strategy_name,
            universe_size=len(codes),
        )
        summary.drawdown_exit_trades = exit_trades

        self._print_summary(summary)
        return all_trades, summary

    # ---- 账户级回撤约束（达阈值 → 卖出，不冻结开新仓）----

    def _resolve_breaker_pct(self, strategy: Any) -> float:
        """解析账户级回撤阈值（%，负值有效；0 或正数表示关闭）。

        优先级：`BacktestConfig.max_drawdown_pct`（CLI 显式指定）> 策略实例的
        `RISK_MAX_DRAWDOWN_PCT`（策略参数，默认 -10.0）。

        :param strategy: 策略实例
        :return: 回撤阈值（%）
        """
        configured: float | None = self.config.max_drawdown_pct
        if configured is None:
            configured = float(getattr(strategy, "RISK_MAX_DRAWDOWN_PCT", 0.0) or 0.0)
        return float(configured or 0.0)

    def _drawdown_at(
        self, trades: list[TradeResult], day: str, universe_size: int = 0
    ) -> float | None:
        """计算 `day` 当日的账户已实现权益自峰值回撤（%）；无法判定时返回 None。

        只统计 `sell_date <= day` 的交易，它们才是已结算的。口径与 `_compute_summary`
        的权益曲线一致 —— `_compute_equity_curve` 把持仓按成本计价，未平仓交易在
        `day` 时刻占用的资金对权益值没有贡献，因此这里与最终汇总的
        `max_drawdown_pct` 同源。

        :param trades: 已生成的交易记录
        :param day: 当前交易日
        :return: 回撤百分数（<= 0）；无已结算交易或峰值非正时返回 None
        """
        day_key: str = str(day)[:10]
        closed: list[TradeResult] = [
            t for t in trades if str(t.sell_date)[:10] <= day_key
        ]
        if not closed:
            return None
        curve: list[float] = self._compute_equity_curve(closed, universe_size)
        peak: float = max(curve)
        if peak <= 0:
            return None
        return (curve[-1] - peak) / peak * 100

    def _drawdown_breached(
        self, trades: list[TradeResult], day: str, threshold_pct: float
    ) -> bool:
        """判定当日账户回撤是否已达阈值。

        :param trades: 已生成的交易记录
        :param day: 当前交易日
        :param threshold_pct: 阈值（%），>= 0 表示关闭
        :return: 是否已达阈值
        """
        if threshold_pct >= 0:
            return False
        drawdown_pct: float | None = self._drawdown_at(trades, day)
        return drawdown_pct is not None and drawdown_pct <= threshold_pct

    def _apply_drawdown_exit(
        self,
        trades: list[TradeResult],
        daily_dfs: dict[str, pd.DataFrame],
        threshold_pct: float,
        trading_days: list[str],
    ) -> tuple[list[TradeResult], int]:
        """账户回撤达阈值时，把在持仓位改为当日收盘卖出。

        语义（用户 2026-09-29 定）：回撤约束落在**卖出**侧，不冻结开新仓。
        逐日推进，若某日账户已实现权益自峰值回撤已达 `threshold_pct`，则当日以
        收盘价卖出全部处于持仓期（买入日 < 当日 < 原卖出日）的仓位，把它们的
        卖出日提前到当日。

        提前卖出会在当天一次性确认浮亏、改变权益路径，因此需要重放若干轮直到
        不再产生新的提前卖出（一般 1~2 轮即收敛）。

        只改「卖出日」，不改任何建仓规则与到期卖出价格：未触发回撤的仓位结果
        与不启用该约束时完全一致。

        :param trades: 逐日信号生成的交易记录（会被就地修改）
        :param daily_dfs: code -> 全区间日K，用于取提前卖出日的收盘价
        :param threshold_pct: 回撤阈值（%），>= 0 表示关闭
        :param trading_days: 回测区间内的交易日（YYYY-MM-DD）
        :return: (交易记录, 被回撤提前卖出的笔数)
        """
        if threshold_pct >= 0 or not trades:
            return trades, 0

        exited: set[int] = set()
        max_rounds: int = 5
        for _round in range(max_rounds):
            changed: bool = False
            for day in trading_days:
                if not self._drawdown_breached(trades, day, threshold_pct):
                    continue
                hit: list[str] = []
                for trade in trades:
                    # 严格在持仓期内：买入当日不卖（当天开盘建仓），到期日按原规则卖
                    if not (
                        str(trade.buy_date)[:10]
                        < day
                        < str(trade.sell_date)[:10]
                    ):
                        continue
                    df: pd.DataFrame | None = daily_dfs.get(trade.code)
                    if df is None or df.empty:
                        continue
                    idx: int | None = self._find_date_index(df, day)
                    if idx is None:
                        continue
                    close_raw: float = float(df.iloc[idx].get("close", 0) or 0)
                    if close_raw <= 0 or trade.buy_price_raw <= 0:
                        continue
                    # 有效价含滑点与手续费，必须重算，不能按收盘价比例缩放
                    result = self._calc_trade_result(
                        buy_price=trade.buy_price_raw, sell_price=close_raw
                    )
                    trade.sell_date = day
                    trade.sell_price = result["sell_price"]
                    trade.profit_pct = result["profit_pct"]
                    exited.add(id(trade))
                    hit.append(trade.code)
                if not hit:
                    continue
                changed = True
                self.logger.info(
                    "账户级回撤达阈值（%s，回撤 %.2f%% <= %.2f%%）："
                    "当日收盘卖出 %d 笔在持仓位 %s",
                    day,
                    self._drawdown_at(trades, day) or 0.0,
                    threshold_pct,
                    len(hit),
                    "、".join(hit),
                )
            if not changed:
                break
        return trades, len(exited)

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
            matched, extra = strategy.is_match_strategy(window_data)
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
            buy_price_raw=buy_price_raw,
            buy_score=float(extra.get("score", 0.0) or 0.0),
            buy_star=float(extra.get("star", 0.0) or 0.0),
        )

    def _run_minute_inner(
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

        # ---- 每日门槛复算状态（2026-10-13）----
        # 与实盘 `StrategyWorkflow` 同口径：个股阀门**盘后算、次日盘前生效**，
        # 因此跨日传递 `pending_valves`；市场偏移与禁买看**前一交易日**的跌停家数。
        gates_enabled: bool = bool(self.config.enable_daily_gates)
        breadth: dict[str, int] = (
            self._load_market_breadth(self.config.market_breadth_csv)
            if (gates_enabled or self.config.market_breadth_csv)
            else {}
        )
        pending_valves: dict[str, dict[str, Any]] = {}
        prev_day: str = ""
        prev_limit_down: int | None = None

        # 逐日模拟：每天盘前 before_trading + select，再查询当日分钟K线进行策略判断
        for day in self._iter_trading_days(strategy, start_date, end_date):
            # 0. 注入账户当前回撤（账户级量，策略自己拿不到）——
            #    与实盘 `StrategyWorkflow._process_bar` 一致。分钟频下策略的卖出判定
            #    会据此计入 risk.drawdown_score 分值（达阈值即卖出，不冻结开新仓）。
            if hasattr(strategy, "set_account_drawdown"):
                strategy.set_account_drawdown(
                    self._drawdown_at(all_trades, day, len(codes)) or 0.0
                )

            # 0.5 注入前一日算出的门槛（个股阀门 + 市场偏移 + 极端利空禁买）。
            #     实盘由 `StrategyWorkflow._apply_daily_gates` 在盘前完成，回测就地复刻。
            #     必须在 `before_trading`/`select` 之前：精选流里的 `add_daily_trend`
            #     是增量写入，晚注入不会覆盖，但门槛要在当日第一根K线前就位。
            if gates_enabled:
                self._inject_daily_gates(
                    strategy, pending_valves, prev_day, prev_limit_down
                )

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
                # 仍要推进「前一交易日」：否则下一日的市场偏移会错用更早的跌停家数
                prev_day, prev_limit_down = day, breadth.get(day.replace("-", ""))
                continue

            day_start: str = f"{day} 09:30:00"
            day_end: str = f"{day} 15:00:00"

            # 当日各股票的买卖**信号**时间（阀门复算用）。注意记的是信号而非成交：
            # 实盘阀门取自「已写库的交易信号」，与是否真的建仓无关（回测也按 X1 传空持仓）。
            day_signals: dict[str, dict[str, list[str]]] = {}

            # 3. 查询当日分钟K线，调用 handle_minute_bar 进行策略判断
            for code in codes_today:
                state: dict[str, Any] = position_states.setdefault(
                    code,
                    {
                        "in_position": False,
                        "cost_price": 0.0,
                        "buy_time": "",
                        "buy_score": 0.0,
                        "buy_star": 0.0,
                    },
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
                        day_signals.setdefault(code, {"buy": [], "sell": []})
                        if gates_enabled
                        else None,
                    )
                )

            # 3.5 盘后复算当日个股阀门，供次日盘前注入。必须赶在下面对
            #     `clear_minute_day(day)` 之前 —— `compute_daily_valve` 依赖当日分钟缓存。
            if gates_enabled:
                pending_valves = self._compute_daily_valves(
                    strategy, codes_today, day, day_signals
                )
            prev_day = day
            prev_limit_down = breadth.get(day.replace("-", ""))

            # 释放当日分钟行情内存快照：长周期回测逐日累计会占用大量内存，
            # 且跨日数据本就不应复用（缓存 key 含日期）
            try:
                from core.user_strategy_cache import UserStrategyCache

                UserStrategyCache().clear_minute_day(day)
            except Exception as exc:
                self.logger.debug("清理当日分钟行情缓存失败 day=%s: %s", day, exc)

        # 按买入日期排序
        all_trades.sort(key=lambda t: t.buy_date)

        # 生成汇总
        summary: BacktestSummary = self._compute_summary(
            trades=all_trades,
            benchmark_df=benchmark_df,
            strategy_name=strategy_name,
            universe_size=len(codes),
        )

        self._print_summary(summary)
        return all_trades, summary

    # ---- 每日门槛复算（2026-10-13，实盘 StrategyWorkflow 同口径）----

    def _load_market_breadth(self, csv_path: str) -> dict[str, int]:
        """读取涨跌停家数 CSV → ``{YYYYMMDD: limit_down}``。

        只收有值的行：akshare 的跌停池接口只覆盖最近约 30 个交易日，CSV 里更早的
        日期 `limit_down` 为空。这些日期缺席 ⇒ 市场偏移 0、不触发禁买，
        与实盘「取不到按不限制兜底」的 fail-open 语义一致。

        :param csv_path: CSV 路径（列 ``date,limit_up,limit_down``；date 为 YYYYMMDD）
        :return: 日期 → 跌停家数；路径为空/不存在/解析失败时返回空字典
        """
        if not csv_path:
            return {}
        from pathlib import Path

        path = Path(csv_path)
        if not path.exists():
            self.logger.warning(
                "涨跌停家数 CSV 不存在: %s ⇒ 市场偏移与极端利空禁买关闭", csv_path
            )
            return {}
        counts: dict[str, int] = {}
        try:
            import csv as _csv

            with path.open("r", encoding="utf-8-sig", newline="") as fh:
                for row in _csv.DictReader(fh):
                    raw_date: str = str(row.get("date", "") or "").strip()
                    raw_down: str = str(row.get("limit_down", "") or "").strip()
                    if not raw_date or not raw_down:
                        continue
                    try:
                        counts[raw_date] = int(float(raw_down))
                    except (TypeError, ValueError):
                        continue
        except Exception as exc:  # noqa: BLE001 - defensive
            self.logger.warning(
                "解析涨跌停家数 CSV 失败(%s): %s ⇒ 市场偏移与禁买关闭", csv_path, exc
            )
            return {}
        if counts:
            self.logger.info(
                "涨跌停家数已加载 %d 个交易日（%s ~ %s）",
                len(counts), min(counts), max(counts),
            )
        else:
            self.logger.warning("涨跌停家数 CSV 无有效数据(%s) ⇒ 市场偏移与禁买关闭", csv_path)
        return counts

    def _market_bands_config(
        self,
    ) -> tuple[tuple[tuple[float, float], ...], float, float]:
        """读取 ``cfg/stock.cfg [market_breadth]`` → ``(分档, 卖出侧上限, 禁买阈值)``。

        只读一次并缓存。与实盘 `StrategyWorkflow._load_market_breadth_config` 同源，
        保证回测里的市场偏移幅度与实盘一致（否则两边的「+1.0 分」含义不同）。
        配置缺失/非法一律回退代码默认值（`DEFAULT_BANDS` 等）。
        """
        if self._market_bands_cache is not None:
            return self._market_bands_cache
        from pathlib import Path

        from utils.market_breadth import (
            DEFAULT_BANDS,
            DEFAULT_STOP_BUY_LIMIT_DOWN,
            MAX_SELL_OFFSET,
            parse_bands,
        )

        bands: tuple[tuple[float, float], ...] = DEFAULT_BANDS
        cap: float = MAX_SELL_OFFSET
        stop: float = DEFAULT_STOP_BUY_LIMIT_DOWN
        try:
            cfg_path: Path = Path(__file__).resolve().parent.parent / "cfg" / "stock.cfg"
            cp: ConfigParser = ConfigParser()
            if cfg_path.exists() and cp.read(str(cfg_path), encoding="utf-8"):
                if cp.has_section("market_breadth"):
                    parsed = parse_bands(cp.get("market_breadth", "bands", fallback=""))
                    if parsed:
                        bands = parsed
                    cap = cp.getfloat(
                        "market_breadth", "max_sell_offset", fallback=MAX_SELL_OFFSET
                    )
                    stop = cp.getfloat(
                        "market_breadth",
                        "stop_buy_limit_down",
                        fallback=DEFAULT_STOP_BUY_LIMIT_DOWN,
                    )
        except Exception as exc:  # noqa: BLE001 - defensive
            self.logger.warning("读取 market_breadth 配置失败，使用默认分档: %s", exc)
        self._market_bands_cache = (bands, float(cap), float(stop))
        return self._market_bands_cache

    def _inject_daily_gates(
        self,
        strategy: Any,
        valves: dict[str, dict[str, Any]],
        prev_day: str,
        prev_limit_down: int | None,
    ) -> None:
        """盘前注入「个股阀门 + 市场偏移 + 禁买名单」（实盘 `_apply_daily_gates` 同口径）。

        :param strategy: 策略实例
        :param valves: 前一交易日盘后算出的阀门 ``{code: {"buy_valve","sell_valve"}}``
        :param prev_day: 阀门来源交易日 YYYY-MM-DD（仅日志）
        :param prev_limit_down: 前一交易日跌停家数；None = 取数失败（不偏移、不禁买）
        """
        from utils.market_breadth import resolve_market_offset

        bands, max_sell, stop_buy = self._market_bands_config()

        if valves:
            strategy.set_daily_valves(valves, prev_day)
        else:
            strategy.clear_daily_valves()

        buy_offset, sell_offset = resolve_market_offset(prev_limit_down, bands, max_sell)
        strategy.set_market_offset(buy_offset, sell_offset)
        if buy_offset or sell_offset:
            self.logger.info(
                "回测盘前市场宽度：源交易日 %s 跌停 %s 家 ⇒ 买入门槛 %+.1f、卖出门槛 %+.1f",
                prev_day or "-", prev_limit_down, buy_offset, sell_offset,
            )

        # 极端利空禁买：前一交易日跌停家数 > stop_buy **且** 该股阀门达 VALVE_STOP_TIER 档
        # （跌破 S3）。fail-open 分支与实盘一致：开关关闭 / 阈值关闭 / 数据缺失 / 未超阈值。
        if (
            not getattr(strategy, "BLOCK_BUY_ENABLED", True)
            or stop_buy <= 0
            or prev_limit_down is None
            or prev_limit_down <= stop_buy
        ):
            strategy.clear_buy_blocked()
            return
        stop_value: float = float(getattr(strategy, "VALVE_STOP_TIER", 3)) * float(
            getattr(strategy, "VALVE_STEP", 1.0)
        )
        if stop_value <= 0:
            strategy.clear_buy_blocked()
            return
        blocked: list[str] = []
        for code, record in valves.items():
            try:
                buy_valve: float = abs(float(record.get("buy_valve", 0.0) or 0.0))
            except (TypeError, ValueError):
                continue
            if buy_valve >= stop_value:
                blocked.append(code)
        strategy.set_buy_blocked(blocked)
        if blocked:
            self.logger.warning(
                "回测极端利空禁买：前一交易日跌停 %s 家 > %.0f 家，"
                "且 %d 只跌破 S3 ⇒ 当日只卖不买（%s）",
                prev_limit_down, stop_buy, len(blocked),
                ",".join(sorted(blocked)[:10]),
            )

    def _compute_daily_valves(
        self,
        strategy: Any,
        codes: list[str],
        day: str,
        signals: dict[str, dict[str, list[str]]],
    ) -> dict[str, dict[str, Any]]:
        """盘后复算当日个股阀门（实盘 `daily_after_trading` 同口径）。

        只保留「买入或卖出阀门非 0」的股票 —— 阀门为 0 不会改变任何门槛，
        存进次日名单只会多刷日志。返回的字典直接喂给 `set_daily_valves`。

        调用点必须在 `clear_minute_day(day)` 之前：`compute_daily_valve` 依赖当日
        分钟缓存。单只计算异常按「无阀门」跳过，不中断整轮回测。
        """
        valves: dict[str, dict[str, Any]] = {}
        for code in codes:
            bucket: dict[str, list[str]] | None = signals.get(code)
            if not bucket or not (bucket.get("buy") or bucket.get("sell")):
                continue
            try:
                result: dict[str, Any] = strategy.compute_daily_valve(
                    code, day, bucket["buy"], bucket["sell"]
                )
            except Exception as exc:  # noqa: BLE001 - defensive
                self.logger.warning("回测盘后阀门计算失败 code=%s: %s", code, exc)
                continue
            if not result:
                continue
            buy_valve: float = float(result.get("buy_valve", 0.0) or 0.0)
            sell_valve: float = float(result.get("sell_valve", 0.0) or 0.0)
            if not (buy_valve or sell_valve):
                continue
            valves[code] = {"buy_valve": buy_valve, "sell_valve": sell_valve}
            self.logger.debug(
                "回测盘后阀门 %s %s: 买+%.1f / 卖+%.1f", day, code, buy_valve, sell_valve
            )
        if valves:
            self.logger.info("回测盘后阀门 %s：%d 只触发门槛上调", day, len(valves))
        return valves

    def _select_candidates(
        self,
        strategy: Any,
        codes: list[str],
        signal_window: int,
        strategy_name: str,
        today_str: str = "",
    ) -> list[str]:
        """调用策略 select 按日线数据筛选当日候选股票。

        **回测数据完整性（C）**：select 以 `raise_on_error=True` 调用，
        取数失败按 `fetch_retries` 重试，仍失败则抛 `MarketDataFetchError`，
        这里直接上抛让整个回测中止 —— 绝不降级成「全量股票池」继续算
        （旧实现会把取数失败吞掉后回退全量池，等于把一次数据故障变成了
        一堆凭空产生的交易）。策略自身异常仍保留原有的宽返回容错。

        :param strategy: 策略实例
        :param codes: 候选股票池
        :param signal_window: 日线窗口长度
        :param strategy_name: 策略名（日志用）
        :param today_str: 交易日 YYYY-MM-DD
        :return: 命中的股票代码列表
        :raises MarketDataFetchError: 行情取数重试后仍失败
        """
        try:
            if not hasattr(strategy, "select"):
                self.logger.info(f"策略:{strategy_name}，缺少select方法")
            select_results: list[dict[str, Any]] = strategy.select(
                codes=codes,
                today_str=today_str,
                days=signal_window,
                retries=self.config.fetch_retries,
                retry_interval=self.config.fetch_retry_interval,
                raise_on_error=True,
            )
            matched_codes: list[str] = [
                str(r["code"]) for r in select_results if r.get("matched", False)
            ]
            # 取数失败单独统计并告警：reason 实际在 extra 里，
            # 旧实现取顶层 `"reason" in result` 恒为 False、日志全丢
            fetch_failed_codes: list[str] = [
                str(r["code"]) for r in select_results if r.get("fetch_failed", False)
            ]
            if fetch_failed_codes:
                self.logger.warning(
                    "select 筛选 %s: %d 只股票取数失败（候选集不完整）: %s",
                    today_str or "-", len(fetch_failed_codes), fetch_failed_codes[:20],
                )
            for result in select_results:
                if result.get("matched", False):
                    continue
                reason: str = str((result.get("extra") or {}).get("reason", ""))
                if reason:
                    self.logger.info("[%s] %s", result.get("code", ""), reason)
            self.logger.info(
                "select 筛选 %s: %d 只股票 → %d 只符合条件（取数失败 %d 只）",
                today_str or "-",
                len(codes),
                len(matched_codes),
                len(fetch_failed_codes),
            )
        except MarketDataFetchError:
            # 数据不完整 → 中止回测，交由 run() 上抛给调用方（不写库）
            raise
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
        直接写入 `UserStrategyCache` 的选股缓存以模拟同样的路由效果
        （缓存改动后策略侧的选股列表统一由 UserStrategyCache 提供，
        不再使用旧的 `_select_stocks_set` / `_select_stocks_date` 实例属性）。

        :param strategy: 策略实例
        :param matched_codes: 当日命中的股票代码
        :param day: 交易日 YYYY-MM-DD
        """
        try:
            from core.user_strategy_cache import UserStrategyCache

            usid: str = str(getattr(strategy, "user_strategy_id", "") or "")
            UserStrategyCache().save_select_stocks(usid, day[:10], set(matched_codes))
        except Exception as exc:
            self.logger.debug("注入策略选股列表失败: %s", exc)

    def _push_minute_bar_to_cache(self, code: str, bar: dict[str, Any]) -> None:
        """把当前分钟K线写入缓存，供策略内部读取（与实盘 workflow 写入方式一致）。

        实盘链路由 `StrategyWorkflow.handle_bar` 单点写入分钟行情，
        策略的 `_get_minute_cache` 才能取到数据；回测必须做同样的事，
        否则量能/VWAP/趋势类因子会拿到空列表（策略改由 workflow 统一写入后，
        旧的 `_before_minute_route` 写入点已移除）。

        :param code: 股票代码
        :param bar: 当前分钟K线
        """
        try:
            from core.user_strategy_cache import UserStrategyCache

            UserStrategyCache().push_minute_bar(code, bar)
        except Exception as exc:
            self.logger.debug("写入分钟行情缓存失败 code=%s: %s", code, exc)

    def _run_minute_for_stock(
        self,
        strategy: Any,
        code: str,
        start_date: str,
        end_date: str,
        signal_window: int,
        strategy_name: str,
        state: dict[str, Any] | None = None,
        signal_times: dict[str, list[str]] | None = None,
    ) -> list[TradeResult]:
        """分钟回测单只股票单个交易日：按 StrategyWorkflow 模式处理分钟K线。

        每根K线：
        1. 先写入分钟行情缓存（与实盘 workflow 单点写入一致，供策略内部因子读取）；
        2. 再调用策略的 handle_minute_bar 取信号。**按 X1 决策统一传空持仓 `{}`**，
           与实盘 `_process_bar` 口径一致（实盘以「尽可能产出信号」为目标，
           不向策略暴露真实持仓）；买卖的实际执行由本方法按自己维护的
           `in_position` / `cost_price` 状态完成，因此回测的交易撮合逻辑不受影响，
           只有「传给策略的持仓视图」与实盘对齐 —— 这样回测信号集合才与实盘可比。

        - BUY 且当前无持仓 → 以当前分钟价格买入建仓
        - SELL 且当前有持仓 → 以当前分钟价格卖出平仓，生成一笔交易
        持仓状态（in_position/cost_price/buy_time）通过 state 跨交易日保留。
        """
        trades: list[TradeResult] = []
        if state is None:
            state = {
                "in_position": False,
                "cost_price": 0.0,
                "buy_time": "",
                "buy_score": 0.0,
                "buy_star": 0.0,
            }
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
            # 建仓信号的分值必须随持仓跨日保留：买入与卖出可能不在同一天，
            # 只在 SELL 分支现取 extra 会丢掉买入那根 K 线的分值。
            buy_score: float = float(state.get("buy_score", 0.0))
            buy_star: float = float(state.get("buy_star", 0.0))
            # 当日已**通过**风暴过滤的最近一条信号（用于同方向连续信号的抑制判定）。
            # 只在本日内有效 —— 与实盘一致，跨日不判；因此不需要跨日保留。
            last_signal: dict[str, Any] = {}
            # 当日最近一次**成交**时间（成交间隔限制用，0 = 关闭）。同样只需日内维护：
            # 跨日的第一次成交隔夜远超任何合理阈值，天然放行。
            last_trade_at: datetime | None = None
            # 当日该股票的上一次**买点**（价格 + 时刻），供盘中买入门槛判定。
            # 它记录的不是「上一次成交」而是「上一次符合买入条件」，被抑制的买点也会
            # 写进来（见下方调用点），否则窗口不会被刷新、压不住阴跌中的连续买点。
            last_buy_probe: dict[str, Any] = {}

            # 当日所有分钟K线均参与策略判断（与 StrategyWorkflow handle_bar 一致）
            for i in range(len(df)):
                current_bar: dict[str, Any] = cast(
                    "dict[str, Any]", df.iloc[i].to_dict()
                )
                # X1：与实盘一致，始终传空持仓给策略
                self._push_minute_bar_to_cache(code, current_bar)

                matched, extra = self._check_minute_bar(
                    strategy, code, {}, current_bar
                )
                if not matched:
                    continue

                signal_type: str = str(extra.get("signal", ""))
                # 信号风暴过滤（与实盘同语义）。被抑制的信号既不成交、也不更新
                # last_signal —— 实盘里被抑制的信号同样不写库，于是下一次仍与更早
                # 那条「通过过滤」的信号比较（抑制会延续）。默认关闭，见 BacktestConfig。
                bar_time: str = str(
                    current_bar.get("create_time", current_bar.get("date", ""))
                )
                bar_price: float = float(
                    current_bar.get("price", current_bar.get("close", 0)) or 0.0
                )
                if self._storm_suppressed(
                    last_signal, signal_type, bar_price, bar_time
                ):
                    continue
                # 成交间隔限制（不分方向，用来压「卖完又买回」）。被跳过的信号同样不
                # 更新 last_signal，避免它参与后续的风暴过滤比较。
                if self._trade_interval_blocked(last_trade_at, bar_time):
                    continue
                # 盘中买入门槛（2026-10-14）：只作用于买入。被抑制时**仍然更新**
                # last_buy_probe（这就是「依然缓存」），因此下一次判定以最新买点为基准，
                # 阴跌过程中每个买点都会把窗口清零。被抑制的信号同样不更新 last_signal
                # （与另外两条过滤一致：实盘里它不写库，也就不参与后续风暴比较）。
                if signal_type == "BUY":
                    dip_blocked: bool = self._buy_dip_blocked(
                        last_buy_probe, bar_price, bar_time
                    )
                    last_buy_probe.update(price=bar_price, time=bar_time)
                    if dip_blocked:
                        continue
                last_signal.update(action=signal_type, price=bar_price, time=bar_time)
                # 记录已通过过滤的信号时间（供盘后阀门复算，见 enable_daily_gates）。
                # 位置选在过滤之后：实盘阀门取自「已写库的交易信号」，被抑制的信号
                # 同样不写库，两边口径一致。与是否真的建仓无关 —— 回测按 X1 传空持仓，
                # 实盘的买入信号也照写不误。
                if signal_times is not None:
                    slot: str = "buy" if signal_type == "BUY" else (
                        "sell" if signal_type == "SELL" else ""
                    )
                    if slot:
                        signal_times.setdefault(slot, []).append(bar_time)

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
                    buy_score = float(extra.get("score", 0.0) or 0.0)
                    buy_star = float(extra.get("star", 0.0) or 0.0)
                    in_position = True
                    last_trade_at = self._bar_datetime(bar_time) or last_trade_at
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
                        buy_score=buy_score,
                        buy_star=buy_star,
                        sell_score=float(extra.get("score", 0.0) or 0.0),
                        sell_star=float(extra.get("star", 0.0) or 0.0),
                    )
                    if trade is not None:
                        trades.append(trade)
                    in_position = False
                    cost_price = 0.0
                    buy_time = ""
                    buy_score = 0.0
                    buy_star = 0.0
                    last_trade_at = self._bar_datetime(bar_time) or last_trade_at
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
            state["buy_score"] = buy_score
            state["buy_star"] = buy_star
        except MarketDataFetchError:
            # 取数失败必须中止整个回测，不能被「单只股票异常」的兜底吞掉
            raise
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

    def _storm_suppressed(
        self,
        last_signal: dict[str, Any],
        action: str,
        price: float,
        bar_time: str,
    ) -> bool:
        """信号风暴过滤：判定本条信号是否应被抑制。

        与实盘 `StrategyWorkflow._save_trade_signal` 同语义，逐条对应：

        | 实盘 | 这里 |
        | --- | --- |
        | 查最近一次同股票+同策略信号（不限方向） | `last_signal` |
        | `last_action == action`（连续同方向） | 同 |
        | `last_dt.date() == today.date()`（只比同日） | 时间戳前 10 位相同 |
        | `gap < time_gap_minutes` → 抑制 | 同 |
        | `change_pct < price_threshold` → 抑制 | 同 |
        | 被抑制的信号不写库（不进下一次比较） | 被抑制时不更新 `last_signal` |

        两个条件都要达到才放行（任一不满足即抑制），与配置注释
        「需同时满足以下条件才发出新信号」一致。

        :param last_signal: 当日最近一条**通过过滤**的信号（空字典表示还没有）
        :param action: 本次信号方向（BUY / SELL）
        :param price: 本次信号价格
        :param bar_time: 本次信号时间（YYYY-MM-DD HH:MM:SS）
        :return: True 表示抑制（不成交）
        """
        if not self.config.storm_filter_enabled or not last_signal:
            return False
        if str(last_signal.get("action", "")) != action:
            return False
        last_price = float(last_signal.get("price", 0.0) or 0.0)
        last_time = str(last_signal.get("time", "") or "")
        if last_price <= 0 or price <= 0 or not last_time:
            return False
        # 只比同一天：跨日不判（实盘亦然）
        if last_time[:10] != bar_time[:10]:
            return False
        try:
            gap_minutes: float = (
                datetime.strptime(bar_time[:19], "%Y-%m-%d %H:%M:%S")
                - datetime.strptime(last_time[:19], "%Y-%m-%d %H:%M:%S")
            ).total_seconds() / 60.0
        except ValueError:
            return False
        change_pct: float = abs(price - last_price) / last_price * 100

        reasons: list[str] = []
        if gap_minutes < self.config.storm_time_gap_minutes:
            reasons.append(
                f"间隔={gap_minutes:.1f}分钟 < {self.config.storm_time_gap_minutes}分钟"
            )
        if change_pct < self.config.storm_price_change_pct:
            reasons.append(
                f"涨幅={change_pct:.2f}% < {self.config.storm_price_change_pct}%"
            )
        if not reasons:
            return False
        self.logger.info(
            "信号风暴过滤: code 略, action=%s, price=%.2f, last_price=%.2f, "
            "last_time=%s, %s, 抑制",
            action,
            price,
            last_price,
            last_time,
            ", ".join(reasons),
        )
        return True

    @staticmethod
    def _bar_datetime(bar_time: str) -> datetime | None:
        """把分钟 K 线时间解析成 datetime；解析不了返回 None。"""
        try:
            return datetime.strptime(bar_time[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def _trade_interval_blocked(
        self, last_trade_at: datetime | None, bar_time: str
    ) -> bool:
        """任意两次**成交**之间的最小间隔（分钟）；0 表示关闭。

        与 `_storm_suppressed` 的区别：那条只对**同方向**连续信号生效，回测里
        成交序列天然是买、卖交替的，所以它几乎不会触发；本开关不分方向，
        用来压住「卖完 1 分钟又买回」这类高频进出。

        `last_trade_at` 只在单日内维护：跨日的第一次成交天然满足间隔（隔夜远超
        任何合理阈值），不必跨日保留。

        :param last_trade_at: 当日最近一次成交的时间，None 表示当日还没成交过
        :param bar_time: 本次信号时间（YYYY-MM-DD HH:MM:SS）
        :return: True 表示该信号因间隔不足被跳过
        """
        if self.config.min_trade_interval_minutes <= 0 or last_trade_at is None:
            return False
        now: datetime | None = self._bar_datetime(bar_time)
        if now is None:
            return False
        gap_minutes: float = (now - last_trade_at).total_seconds() / 60.0
        if gap_minutes < self.config.min_trade_interval_minutes:
            self.logger.info(
                "交易间隔限制: 距上次成交 %.1f 分钟 < %.1f 分钟，跳过该信号",
                gap_minutes,
                self.config.min_trade_interval_minutes,
            )
            return True
        return False

    def _buy_dip_blocked(
        self, probe: dict[str, Any], price: float, bar_time: str
    ) -> bool:
        """盘中买入门槛：本次买点是否因「跌破上一次买点且间隔不足」被抑制。

        判定顺序（全部满足才抑制）：

        1. 开关打开且窗口 > 0；
        2. `probe` 里有上一次符合买入条件的价格与时刻；
        3. 本次价格 **严格低于** 上一次的价格；
        4. 距上一次的间隔 **小于** 窗口分钟数。

        调用方在判定之后**无论结果都更新 `probe`**（被抑制也缓存），这是本机制的
        关键：否则每次被抑制都不会刷新窗口，等于只是「10 分钟内最多一单」，压不住
        阴跌过程中的连续买点。

        跨日不必特判：`probe` 在 `_run_minute_for_stock` 里按股票、按交易日维护，
        天然跨日清零；即便保留，隔夜间隔也远超窗口。

        :param probe: 当日该股票的上一次买点（含 price / time），空字典表示还没有
        :param price: 本次买点价格
        :param bar_time: 本次买点时间（YYYY-MM-DD HH:MM:SS）
        :return: True 表示抑制本次买入信号
        """
        if not self.config.buy_dip_gate_enabled:
            return False
        gap_window: float = float(self.config.buy_dip_gap_minutes or 0.0)
        if gap_window <= 0 or price <= 0 or not probe:
            return False
        prev_price: float = float(probe.get("price", 0.0) or 0.0)
        prev_time: str = str(probe.get("time", "") or "")
        if prev_price <= 0 or not prev_time:
            return False
        # 未跌破：放行
        if price >= prev_price:
            return False
        prev_dt: datetime | None = self._bar_datetime(prev_time)
        now_dt: datetime | None = self._bar_datetime(bar_time)
        if prev_dt is None or now_dt is None:
            return False
        gap_minutes: float = (now_dt - prev_dt).total_seconds() / 60.0
        if gap_minutes >= gap_window:
            return False
        self.logger.info(
            "盘中买入门槛: 价格 %.2f 跌破上次买点 %.2f，间隔 %.1f 分钟 < %.1f 分钟，"
            "抑制本次买入（缓存仍更新）",
            price,
            prev_price,
            gap_minutes,
            gap_window,
        )
        return True

    def _build_minute_trade(
        self,
        strategy_name: str,
        code: str,
        sell_bar: dict[str, Any],
        buy_time: str,
        cost_price: float,
        sell_price_raw: float,
        buy_score: float = 0.0,
        buy_star: float = 0.0,
        sell_score: float = 0.0,
        sell_star: float = 0.0,
    ) -> TradeResult:
        """根据买入/卖出分钟数据生成一笔交易记录。

        `buy_score` / `buy_star` 来自建仓那根 K 线的信号（可能已跨日保留在 state 里），
        `sell_score` / `sell_star` 来自平仓那根。
        """
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
            buy_score=buy_score,
            buy_star=buy_star,
            sell_score=sell_score,
            sell_star=sell_star,
        )

    # ---- 汇总计算 ----

    def _compute_summary(
        self,
        trades: list[TradeResult],
        benchmark_df: pd.DataFrame,
        strategy_name: str,
        universe_size: int = 0,
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

        # 交易天数：与是否有交易无关，始终按回测区间估算。
        # 放在无交易早退之前，否则「零交易」回测会显示「交易天数: 0」，
        # 让人误以为回测没跑起来。
        trading_days: int = self._count_trading_days(
            self.config.start_date, self.config.end_date
        )
        summary.trading_days = trading_days

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
        equity_curve: list[float] = self._compute_equity_curve(trades, universe_size)
        summary.final_capital = round(equity_curve[-1], 2) if equity_curve else self.config.initial_capital
        total_return = (
            (summary.final_capital - summary.initial_capital)
            / summary.initial_capital
        )
        summary.total_return_pct = round(total_return * 100, 2)
        summary.total_profit = round(
            summary.final_capital - summary.initial_capital, 2
        )

        # 年化收益（trading_days 已在函数开头统一计算）
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

    def _compute_equity_curve(
        self, trades: list[TradeResult], universe_size: int = 0
    ) -> list[float]:
        """计算权益曲线：事件驱动、资金受限的资金管理模型。

        按买入/卖出日期推进时间轴：
        - 买入时从可用资金中划出 position_size_pct 比例的资金（不可重叠占用）；
        - 卖出时释放本金并按 profit_pct 结算盈亏；
        - 可用资金不足时跳过后续买入信号，避免“串行全仓复利”虚高收益。

        **资金按标的均分（`split_capital_by_code`）**：开启后把初始资金平均分给
        `universe_size` 只股票（未传时退化为「产生过交易的股票数」），每只独立周转、
        互不挤占。关闭时是「全局单一资金池」—— 先出现的信号会把资金占满，其余标的的
        信号被大量跳过，结果既不反映各标的自身表现、也随标的顺序漂移。

        :param trades: 交易记录列表（自动按买入日期排序）
        :param universe_size: 回测涉及的股票数（仅分片模式使用；<=0 时按 trades 里的 code 数）
        :return: 权益曲线列表（初始资金 + 每个事件后的权益值）
        """
        position_pct: float = self.config.position_size_pct
        split: bool = self.config.split_capital_by_code
        codes: list[str] = sorted({t.code for t in trades})

        free_cash: float = self.config.initial_capital
        free_by_code: dict[str, float] = {}
        if split:
            parts: int = max(int(universe_size) or len(codes), 1)
            share: float = self.config.initial_capital / parts
            for code in codes:
                free_by_code[code] = share
            # 没产生交易的标的（universe_size 更大时）那份一直闲置，但仍计入权益
            free_cash -= share * len(codes)

        ordered: list[TradeResult] = sorted(
            trades, key=lambda t: (t.buy_date, t.sell_date)
        )

        # 构造 (日期, 事件类型, 交易序号) 时间轴；同日买入先于卖出处理
        events: list[tuple[str, int, int]] = []
        for idx, trade in enumerate(ordered):
            events.append((trade.buy_date, 0, idx))
            events.append((trade.sell_date, 1, idx))
        events.sort(key=lambda e: (e[0], e[1]))

        # 交易序号 -> (代码, 占用资金)；持仓按成本计价
        open_invested: dict[int, tuple[str, float]] = {}
        equity: list[float] = [free_cash + sum(free_by_code.values())]

        for _date, event_type, idx in events:
            trade: TradeResult = ordered[idx]
            if event_type == 1:  # 卖出：结算盈亏并释放本金
                entry = open_invested.pop(idx, None)
                if entry is not None:
                    code, invested = entry
                    proceeds: float = invested * (1 + trade.profit_pct / 100)
                    if split:
                        free_by_code[code] += proceeds
                    else:
                        free_cash += proceeds
            else:  # 买入：划出资金
                if idx in open_invested:
                    continue
                available: float = (
                    free_by_code.get(trade.code, 0.0) if split else free_cash
                )
                if available <= 1e-9:
                    continue
                invested = min(available * position_pct, available)
                if split:
                    free_by_code[trade.code] -= invested
                else:
                    free_cash -= invested
                open_invested[idx] = (trade.code, invested)
            equity.append(
                free_cash
                + sum(free_by_code.values())
                + sum(amount for _, amount in open_invested.values())
            )

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

        回测汇总与交易明细先装入 `BacktestDao`（本端回测结果唯一数据载体），
        再由 `BacktestApi` 写入 `POST /api/strategy/{strategy_id}/backtest`；
        若同时提供 user_strategy_id，再同步一份策略执行指标（供策略页面展示）。

        :param strategy: 策略实例（或策略类）
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
                dao: BacktestDao = BacktestDao.from_summary(
                    strategy_id=strategy_id,
                    summary=summary,
                    trades=list(trades or []),
                    frequency=self.config.frequency,
                    strategy_name=str(summary.strategy_name or strategy_name),
                    create_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                record_id: str = self._backtest_api.save(dao)
                self.logger.info(
                    "回测结果已保存: %s (strategy_id=%s, id=%s)",
                    strategy_name,
                    strategy_id,
                    record_id,
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
        if summary.drawdown_exit_trades > 0:
            print(f"  回撤提前卖出: {summary.drawdown_exit_trades} 笔")
        print("-" * 60)
        if summary.benchmark_code:
            print(f"  基准 ({summary.benchmark_code}):")
            print(f"    基准收益: {summary.benchmark_return_pct}%")
            print(f"    基准年化: {summary.benchmark_annualized_pct}%")
            print(f"    超额收益 (Alpha): {summary.alpha}%")
        print("=" * 60)
