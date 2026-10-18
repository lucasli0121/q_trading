"""
Author: liguoqiang
Date: 2026-08-02
Description: 回测引擎测试 —— 日频回测使用真实 API 行情数据；
    成本模型、权益曲线、汇总统计等数学不变量使用受控数据验证。
    所有结果写入均 mock，避免污染生产数据。
"""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pandas as pd

from app_context import AppContext
from backtest.backtest_engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestSummary,
    TradeCostConfig,
    TradeResult,
)
from dao.backtest_dao import BacktestDao
from strategy.base_strategy import BaseStrategy, MarketDataFetchError
from utils.tools import load_admin_token


def setUpModule() -> None:
    AppContext().api_client.set_fallback_token(load_admin_token())


class SimpleMomentumStrategy(BaseStrategy):
    """仅依赖真实行情数据的简单动量策略（测试回测引擎用）。"""

    strategy_name = "SimpleMomentum"
    strategy_type = "选股策略"
    description = "测试策略"

    def init_factors(self) -> None:
        return None

    def is_match_strategy(
        self, stock_data: list[dict[str, Any]]
    ) -> tuple[bool, dict[str, Any]]:
        if len(stock_data) < 3:
            return False, {"reason": "data too short"}
        return bool(stock_data[-1]["close"] > stock_data[-2]["close"]), {
            "last_close": float(stock_data[-1]["close"]),
            # score/star 是真实策略 extra 的固有字段（见 _check_daily_signal_at），
            # 桩里补上才能验证「信号分值被写进 TradeResult」
            "score": 6.0,
            "star": 3.0,
        }

    def before_trading(
        self, trade_date: str, stock_codes: list[str], **kwargs: Any
    ) -> None:
        pass

    # 说明：本类**不再重载** `handle_minute_bar`，直接沿用 BaseStrategy 的生产契约
    #   `if code in select_stocks and len(position) == 0: 买入; 否则落到卖出检查`
    #
    # 历史实现为 `if position: 卖出 else: 买入`，那是「按持仓有无切换方向」的旧契约。
    # 回测引擎按 X1 与实盘 `_process_bar` 对齐、恒传空持仓 `{}`，
    # 于是旧契约下 `position` 恒为假值 → 卖出分支永远不可达 →
    # 分钟回测卖出信号永不触发（这正是 `test_run_minute_with_stub_data` 曾失败的根因）。
    # 改为继承基类后，桩策略与真实策略走同一套路由，测试才验证到生产行为。

    def handle_tick_bar(
        self, code: str, position: dict[str, Any], stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        return False, {}

    def check_minute_buy(
        self, code: str, stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        return False, {}

    def check_minute_sell(
        self, code: str, position: dict[str, Any], stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        return False, {}

    def check_tick_buy(
        self, code: str, stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        return False, {}

    def check_tick_sell(
        self, code: str, position: dict[str, Any], stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        return False, {}


def _trade(
    code: str,
    buy: str,
    sell: str,
    buy_price: float,
    sell_price: float,
    profit_pct: float,
) -> TradeResult:
    return TradeResult(
        code=code,
        strategy_name="SimpleMomentum",
        buy_date=buy,
        sell_date=sell,
        buy_price=buy_price,
        sell_price=sell_price,
        profit_pct=profit_pct,
    )


class TestBacktestEngine(unittest.TestCase):
    # ---- 信号分值透传（2026-10-11）----
    # 在此之前 TradeResult 只有价格与收益率：日频路径把 is_match_strategy 的 extra
    # 收进 `_extra` 就丢掉了，分钟路径也没采集，导出的 JSON 明细里看不到任何分值。

    def test_daily_trade_records_signal_score(self) -> None:
        """日频：选股信号的 score/star 必须落进 TradeResult。"""
        engine = BacktestEngine(config=BacktestConfig(signal_window=5, hold_days=2))
        df = pd.DataFrame(
            [
                {
                    "create_time": f"2026-01-{i + 1:02d}",
                    "open": 10.0 + i,
                    "close": 10.5 + i,
                    "high": 11.0 + i,
                    "low": 9.5 + i,
                    "volume": 1000,
                }
                for i in range(12)
            ]
        )
        trade = engine._check_daily_signal_at(
            SimpleMomentumStrategy(),
            df,
            "000001",
            "SimpleMomentum",
            i=5,
            hold_days=2,
            signal_window=5,
        )
        self.assertIsNotNone(trade)
        assert trade is not None
        self.assertEqual(trade.buy_score, 6.0)
        self.assertEqual(trade.buy_star, 3.0)
        # 日频的卖出是「持有 N 日到期」的固定卖出，不产生卖出信号
        self.assertEqual(trade.sell_score, 0.0)
        self.assertEqual(trade.sell_star, 0.0)

    def test_minute_trade_records_both_scores(self) -> None:
        """分钟：买入侧（可能跨日保留）与卖出侧的分值都要落进 TradeResult。"""
        engine = BacktestEngine()
        trade = engine._build_minute_trade(
            "成交量放大策略",
            "000988",
            {"create_time": "2026-08-10 10:09:00"},
            "2026-08-10 09:49:00",
            104.94,
            101.38,
            buy_score=5.5,
            buy_star=2.75,
            sell_score=-6.5,
            sell_star=3.0,
        )
        self.assertEqual(trade.buy_score, 5.5)
        self.assertEqual(trade.buy_star, 2.75)
        self.assertEqual(trade.sell_score, -6.5)
        self.assertEqual(trade.sell_star, 3.0)

    # ---- 信号风暴过滤（2026-10-11）----
    # 与实盘 `StrategyWorkflow._save_trade_signal` 同语义：同一天内、同方向的连续
    # 信号，距上一条通过过滤的信号不足时间间隔、或价格变化不足阈值即抑制。

    def test_storm_filter_disabled_by_default(self) -> None:
        """回测默认关闭该过滤（保持历史回测可比），实盘则常开。"""
        engine = BacktestEngine()
        last = {"action": "BUY", "price": 100.0, "time": "2026-08-10 09:49:00"}
        self.assertFalse(
            engine._storm_suppressed(last, "BUY", 100.0, "2026-08-10 09:50:00")
        )

    def test_storm_filter_suppresses_when_too_soon(self) -> None:
        """9 分钟 < 10 分钟，且价格没动（0% < 2%）⇒ 抑制。"""
        engine = BacktestEngine(
            config=BacktestConfig(storm_filter_enabled=True)
        )
        last = {"action": "BUY", "price": 100.0, "time": "2026-08-10 09:49:00"}
        self.assertTrue(
            engine._storm_suppressed(last, "BUY", 100.0, "2026-08-10 09:58:00")
        )

    def test_storm_filter_suppresses_when_price_barely_moves(self) -> None:
        """时间够（20 分钟）但价格只动 0.5% < 2% ⇒ 仍然抑制（两个条件都要达到）。"""
        engine = BacktestEngine(
            config=BacktestConfig(storm_filter_enabled=True)
        )
        last = {"action": "BUY", "price": 100.0, "time": "2026-08-10 09:49:00"}
        self.assertTrue(
            engine._storm_suppressed(last, "BUY", 100.5, "2026-08-10 10:09:00")
        )

    def test_storm_filter_passes_when_both_conditions_met(self) -> None:
        engine = BacktestEngine(
            config=BacktestConfig(storm_filter_enabled=True)
        )
        last = {"action": "BUY", "price": 100.0, "time": "2026-08-10 09:49:00"}
        self.assertFalse(
            engine._storm_suppressed(last, "BUY", 103.0, "2026-08-10 10:09:00")
        )

    def test_storm_filter_ignores_opposite_direction(self) -> None:
        """只比同方向：买→卖不受这条过滤约束（实盘亦然）。"""
        engine = BacktestEngine(
            config=BacktestConfig(storm_filter_enabled=True)
        )
        last = {"action": "BUY", "price": 100.0, "time": "2026-08-10 09:49:00"}
        self.assertFalse(
            engine._storm_suppressed(last, "SELL", 100.0, "2026-08-10 09:50:00")
        )

    def test_storm_filter_ignores_other_day(self) -> None:
        """跨日不判 —— 实盘要求 `last_dt.date() == today.date()`。"""
        engine = BacktestEngine(
            config=BacktestConfig(storm_filter_enabled=True)
        )
        last = {"action": "BUY", "price": 100.0, "time": "2026-08-10 14:57:00"}
        self.assertFalse(
            engine._storm_suppressed(last, "BUY", 100.0, "2026-08-11 09:31:00")
        )

    def test_storm_filter_ignores_empty_history(self) -> None:
        engine = BacktestEngine(
            config=BacktestConfig(storm_filter_enabled=True)
        )
        self.assertFalse(
            engine._storm_suppressed({}, "BUY", 100.0, "2026-08-10 09:50:00")
        )

    # ---- 成交间隔限制（2026-10-11）----
    # 与风暴过滤不同：这条**不分方向**，压的是「卖完又买回」这类高频进出。

    def test_min_trade_interval_disabled_by_default(self) -> None:
        engine = BacktestEngine()
        last = datetime(2026, 8, 10, 9, 49, 0)
        self.assertFalse(
            engine._trade_interval_blocked(last, "2026-08-10 09:50:00")
        )

    def test_min_trade_interval_blocks_within_gap(self) -> None:
        engine = BacktestEngine(
            config=BacktestConfig(min_trade_interval_minutes=30)
        )
        last = datetime(2026, 8, 10, 9, 49, 0)
        self.assertTrue(engine._trade_interval_blocked(last, "2026-08-10 10:00:00"))
        # 恰好等于阈值 ⇒ 放行（判定用 <）
        self.assertFalse(engine._trade_interval_blocked(last, "2026-08-10 10:19:00"))

    def test_min_trade_interval_first_trade_of_day_passes(self) -> None:
        """当日还没成交过（None）⇒ 直接放行。"""
        engine = BacktestEngine(
            config=BacktestConfig(min_trade_interval_minutes=30)
        )
        self.assertFalse(engine._trade_interval_blocked(None, "2026-08-10 09:31:00"))

    def test_min_trade_interval_ignores_direction_unlike_storm(self) -> None:
        """同一条「卖完 1 分钟就买回」：风暴过滤放行，间隔限制拦住。

        这正是两个机制的分工 —— 风暴过滤只比同方向，所以对反向信号直接放行。
        """
        engine = BacktestEngine(
            config=BacktestConfig(
                storm_filter_enabled=True, min_trade_interval_minutes=30
            )
        )
        storm_last = {
            "action": "SELL",
            "price": 100.0,
            "time": "2026-08-10 09:49:00",
        }
        self.assertFalse(
            engine._storm_suppressed(storm_last, "BUY", 100.0, "2026-08-10 09:50:00")
        )
        self.assertTrue(
            engine._trade_interval_blocked(
                datetime(2026, 8, 10, 9, 49, 0), "2026-08-10 09:50:00"
            )
        )

    # ---- 资金按标的均分（2026-10-12）----
    # 全局单一资金池下，先出现的信号会占满资金、其余标的被跳过；分片后各自独立周转。

    def test_split_capital_by_code_isolates_each_stock(self) -> None:
        """两只股票同时买入：分片后各用自己的份额，结果是一赚一亏相抵。

        全局池里 A 先成交占满资金、B 的买入被跳过 ⇒ 结果完全由 A 决定（−50%）；
        分片后 A 亏 50%、B 赚 50%，各用自己那份 ⇒ 总权益回到起点。
        """
        trades = [
            TradeResult(
                code="A", strategy_name="S", buy_date="2026-01-01",
                sell_date="2026-01-02", buy_price=10.0, sell_price=10.0,
                profit_pct=-50.0,
            ),
            TradeResult(
                code="B", strategy_name="S", buy_date="2026-01-01",
                sell_date="2026-01-03", buy_price=10.0, sell_price=10.0,
                profit_pct=50.0,
            ),
        ]

        pooled = BacktestEngine(config=BacktestConfig(initial_capital=20000.0))
        curve = pooled._compute_equity_curve(trades, 2)
        self.assertAlmostEqual(curve[0], 20000.0, places=2)
        self.assertAlmostEqual(curve[-1], 10000.0, places=2)  # 只有 A 成交

        split = BacktestEngine(
            config=BacktestConfig(
                initial_capital=20000.0, split_capital_by_code=True
            )
        )
        split_curve = split._compute_equity_curve(trades, 2)
        self.assertAlmostEqual(split_curve[0], 20000.0, places=2)
        self.assertAlmostEqual(split_curve[-1], 20000.0, places=2)  # A、B 都成交

    def test_split_capital_keeps_unused_share_idle(self) -> None:
        """池里有标的没产生交易时，它那份资金闲置但仍计入权益（不是凭空消失）。"""
        trades = [
            TradeResult(
                code="A", strategy_name="S", buy_date="2026-01-01",
                sell_date="2026-01-02", buy_price=10.0, sell_price=10.0,
                profit_pct=0.0,
            ),
        ]
        engine = BacktestEngine(
            config=BacktestConfig(initial_capital=30000.0, split_capital_by_code=True)
        )
        # 池里 3 只，只有 A 有交易 ⇒ 每份 10000，A 那份不动，另两份闲置
        curve = engine._compute_equity_curve(trades, 3)
        self.assertAlmostEqual(curve[0], 30000.0, places=2)
        self.assertAlmostEqual(curve[-1], 30000.0, places=2)

    def test_split_capital_defaults_off(self) -> None:
        """默认关闭：不传参数时沿用原来的全局资金池口径。"""
        self.assertFalse(BacktestConfig().split_capital_by_code)

    def test_run_daily_with_real_data(self) -> None:
        """真实日K数据回测：产生交易、买卖价格含滑点、持有天数正确。

        ⚠️ 取数区间必须落在**服务端保留窗口（约 3 个月）内**：
        后台只保留近 3 个月历史行情，更早的数据会被删除；库中没有的标的
        会即时回源第三方拉取（可能超时/报错）。因此这里固定用最近一个多月的
        区间（实测 2026-09-15 时 `000001` 在 `2026-08-01~2026-09-12` 有 30 根，
        而 `2026-03-01~2026-07-31` 只剩 2 根 → 用旧区间会得到 0 交易）。
        """
        config = BacktestConfig(
            initial_capital=100000,
            stock_codes=["000001"],
            start_date="2026-08-01",
            end_date="2026-09-12",
            hold_days=3,
            signal_window=5,
            trade_config=TradeCostConfig(
                buy_slippage=0.2, sell_slippage=0.2, fee_pct=0.02, fee_low=0
            ),
        )
        engine = BacktestEngine(config=config)

        trades, summary = engine.run(SimpleMomentumStrategy())

        self.assertGreater(len(trades), 0)
        df = engine._fetch_stock_data("000001", config.start_date, config.end_date)
        date_index = {
            str(ts)[:10]: i for i, ts in enumerate(df["create_time"].astype(str))
        }
        for trade in trades:
            self.assertLess(trade.buy_date[:10], trade.sell_date[:10])
            self.assertGreater(trade.buy_price, 0)
            self.assertGreater(trade.sell_price, 0)
            # 买入为信号次日开盘 + 滑点；持有期 = hold_days
            buy_idx = date_index[trade.buy_date[:10]]
            sell_idx = date_index[trade.sell_date[:10]]
            self.assertEqual(sell_idx - buy_idx, config.hold_days)
            open_price = float(df.iloc[buy_idx]["open"])
            # 有效买入价 = (开盘价 + 0.2 滑点) * (1 + 0.02% 手续费)
            self.assertAlmostEqual(
                trade.buy_price, (open_price + 0.2) * (1 + 0.02 / 100), places=3
            )
            close_price = float(df.iloc[sell_idx]["close"])
            self.assertAlmostEqual(
                trade.sell_price, (close_price - 0.2) * (1 - 0.02 / 100), places=3
            )

        self.assertGreater(summary.final_capital, 0)
        self.assertEqual(summary.total_trades, len(trades))
        self.assertEqual(
            summary.win_count + summary.loss_count,
            summary.total_trades,
        )
        self.assertIsInstance(summary.win_rate, float)
        self.assertIsInstance(summary.sharpe_ratio, float)

    def test_run_daily_with_fee_low(self) -> None:
        """最低手续费配置下回测仍能正常运行。"""
        config = BacktestConfig(
            stock_codes=["000001"],
            start_date="2026-03-01",
            end_date="2026-07-31",
            hold_days=2,
            signal_window=5,
            trade_config=TradeCostConfig(
                buy_slippage=0.2, sell_slippage=0.2, fee_pct=0.02, fee_low=5
            ),
        )
        engine = BacktestEngine(config=config)
        trades, summary = engine.run(SimpleMomentumStrategy())
        self.assertIsInstance(trades, list)
        self.assertGreater(summary.final_capital, 0)
        if trades:
            self.assertGreaterEqual(summary.total_trades, 1)

    # ---- 成本模型 ----

    def test_calc_trade_result_with_slippage_and_fee_low(self) -> None:
        engine = BacktestEngine(
            config=BacktestConfig(
                trade_config=TradeCostConfig(
                    buy_slippage=0.2, sell_slippage=0.2, fee_pct=0.02, fee_low=5
                )
            )
        )
        result = engine._calc_trade_result(buy_price=10.0, sell_price=12.0, quantity=100)
        # 买入: (100*10.2 + 5)/100 = 10.25；卖出: (100*11.8 - 5)/100 = 11.75
        self.assertAlmostEqual(result["buy_price"], 10.25)
        self.assertAlmostEqual(result["sell_price"], 11.75)
        self.assertAlmostEqual(result["profit_pct"], 14.63, places=2)

    def test_calc_trade_result_zero_cost(self) -> None:
        engine = BacktestEngine()
        result = engine._calc_trade_result(buy_price=100.0, sell_price=110.0)
        self.assertAlmostEqual(result["buy_price"], 100.0)
        self.assertAlmostEqual(result["sell_price"], 110.0)
        self.assertAlmostEqual(result["profit_pct"], 10.0)

    def test_calc_trade_result_fee_by_percentage(self) -> None:
        engine = BacktestEngine(
            config=BacktestConfig(
                trade_config=TradeCostConfig(
                    buy_slippage=0.0, sell_slippage=0.0, fee_pct=0.1, fee_low=0
                )
            )
        )
        result = engine._calc_trade_result(buy_price=100.0, sell_price=110.0, quantity=1000)
        # 买入加 0.1% 手续费，卖出减 0.1% 手续费
        self.assertAlmostEqual(result["buy_price"], 100.1, places=4)
        self.assertAlmostEqual(result["sell_price"], 109.89, places=4)

    # ---- 权益曲线 ----

    def test_equity_curve_no_double_spend(self) -> None:
        """同日两笔信号在满仓时不应重复投入资金。"""
        engine = BacktestEngine(config=BacktestConfig(position_size_pct=1.0))
        trades = [
            _trade("000001", "2026-01-05", "2026-01-10", 10, 11, 10.0),
            _trade("600000", "2026-01-05", "2026-01-12", 20, 24, 20.0),
        ]
        equity = engine._compute_equity_curve(trades)
        # 第二笔因资金被占用被跳过：100000 * 1.10
        self.assertAlmostEqual(equity[-1], 110000.0, places=2)

    def test_equity_curve_sequential_compounds(self) -> None:
        engine = BacktestEngine(config=BacktestConfig(position_size_pct=1.0))
        trades = [
            _trade("000001", "2026-01-05", "2026-01-10", 10, 11, 10.0),
            _trade("600000", "2026-01-12", "2026-01-20", 20, 24, 20.0),
        ]
        equity = engine._compute_equity_curve(trades)
        self.assertAlmostEqual(equity[-1], 132000.0, places=2)

    # ---- 汇总统计 ----

    def test_summary_zero_profit_not_counted_as_loss(self) -> None:
        engine = BacktestEngine()
        trades = [
            _trade("a", "2026-01-05", "2026-01-10", 10, 11, 10.0),
            _trade("b", "2026-01-06", "2026-01-11", 10, 10, 0.0),
            _trade("c", "2026-01-07", "2026-01-12", 10, 9.5, -5.0),
        ]
        summary = engine._compute_summary(trades, pd.DataFrame(), "SimpleMomentum")
        self.assertEqual(summary.total_trades, 3)
        self.assertEqual(summary.win_count, 1)
        self.assertEqual(summary.loss_count, 1)
        self.assertEqual(summary.win_rate, 50.0)

    def test_compute_daily_returns_simple(self) -> None:
        returns = BacktestEngine()._compute_daily_returns([100.0, 110.0, 99.0])
        self.assertAlmostEqual(returns[0], 0.1)
        self.assertAlmostEqual(returns[1], -0.1)

    def test_compute_max_drawdown(self) -> None:
        self.assertAlmostEqual(
            BacktestEngine._compute_max_drawdown([100.0, 110.0, 95.0, 105.0, 90.0]),
            18.18,
            places=1,
        )
        self.assertEqual(BacktestEngine._compute_max_drawdown([]), 0.0)
        self.assertEqual(BacktestEngine._compute_max_drawdown([100.0]), 0.0)

    def test_compute_annualized_return(self) -> None:
        self.assertAlmostEqual(
            BacktestEngine._compute_annualized_return(0.10, 252), 10.0, places=1
        )
        self.assertEqual(BacktestEngine._compute_annualized_return(0.10, 0), 0.0)
        self.assertEqual(BacktestEngine._compute_annualized_return(-2.0, 252), 0.0)

    def test_compute_sharpe(self) -> None:
        self.assertEqual(BacktestEngine._compute_sharpe([0.001] * 100), 0.0)
        result = BacktestEngine._compute_sharpe(
            [0.01, -0.005, 0.02, -0.01, 0.015] * 20
        )
        self.assertGreater(result, 0.0)
        self.assertEqual(BacktestEngine._compute_sharpe([]), 0.0)

    def test_count_trading_days(self) -> None:
        self.assertEqual(BacktestEngine._count_trading_days("2026-01-01", "2026-01-02"), 1)
        self.assertEqual(BacktestEngine._count_trading_days("", ""), 0)
        self.assertEqual(BacktestEngine._count_trading_days("bad", "2026-01-02"), 0)

    def test_summary_without_trades_still_reports_trading_days(self) -> None:
        """零交易时 trading_days 仍按回测区间估算，而不是 0。

        背景（2026-09-15 生产实测）：`_compute_summary` 原在 `if not trades`
        处提前 return，跳过了 trading_days 赋值 → 零交易回测显示
        「交易天数: 0」，让人误以为回测没跑起来。
        """
        config = BacktestConfig()
        config.start_date = "2026-01-01"
        config.end_date = "2026-12-31"
        engine = BacktestEngine(config=config)
        summary = engine._compute_summary([], pd.DataFrame(), "SimpleMomentum")
        self.assertEqual(summary.total_trades, 0)
        self.assertEqual(summary.final_capital, config.initial_capital)
        self.assertGreater(summary.trading_days, 0)

    # ---- 回测期间必须关闭 Redis（与分钟频对称） ----

    def test_run_daily_disables_redis_cache(self) -> None:
        """日频回测全程关闭 Redis 持久化，结束后恢复。

        否则回测会把 `select:` / `score:` 缓存写进实盘共用的 Redis，
        且下一次回测会读到上次残留 → 同一配置重复回测结果不可复现
        （实测同一配置得到 2 / 0 / 3 / 4 笔交易）。
        """
        from core.user_strategy_cache import UserStrategyCache

        engine = BacktestEngine()
        cache = UserStrategyCache()
        cache.set_redis_enabled(True)
        observed: list[bool] = []

        def _inner(strategy: Any) -> tuple[list[TradeResult], BacktestSummary]:
            observed.append(cache._redis_ready())
            return [], BacktestSummary()

        engine._run_daily_inner = _inner  # type: ignore[method-assign]
        engine._run_daily(SimpleMomentumStrategy())
        self.assertEqual(observed, [False])
        self.assertTrue(cache._redis_ready())

    def test_run_daily_restores_redis_on_error(self) -> None:
        """内部异常时也必须恢复 Redis 开关（由 finally 保证）。"""
        from core.user_strategy_cache import UserStrategyCache

        engine = BacktestEngine()
        cache = UserStrategyCache()
        cache.set_redis_enabled(True)
        engine._run_daily_inner = MagicMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("boom")
        )
        with self.assertRaises(RuntimeError):
            engine._run_daily(SimpleMomentumStrategy())
        self.assertTrue(cache._redis_ready())

    def test_calc_pct(self) -> None:
        self.assertEqual(BacktestEngine.calc_pct(110.0, 100.0), 10.0)
        self.assertEqual(BacktestEngine.calc_pct(90.0, 100.0), -10.0)

    # ---- 代码解析 ----

    def test_resolve_stock_codes_from_pool(self) -> None:
        config = BacktestConfig(pool_name="科技股池")
        engine = BacktestEngine(config=config)
        engine._pool_api = MagicMock()
        engine._pool_api.get_stocks.return_value = [
            {"code": "000001"},
            {"code": "600519"},
        ]
        self.assertEqual(engine._resolve_stock_codes(), ["000001", "600519"])

    def test_resolve_stock_codes_fallback_to_explicit(self) -> None:
        config = BacktestConfig(stock_codes=["000001"])
        engine = BacktestEngine(config=config)
        self.assertEqual(engine._resolve_stock_codes(), ["000001"])

    # ---- 分钟频回测（受控数据驱动引擎循环） ----

    def test_run_minute_with_stub_data(self) -> None:
        """分钟回测循环：买入/卖出事件与成本计算。"""
        rows = []
        for i in range(40):
            rows.append(
                {
                    "code": "000001",
                    "create_time": f"2026-07-15 09:{30 + i:02d}:00",
                    "price": 10.0 + i * 0.1,
                    "open": 10.0,
                    "close": 10.0 + i * 0.1,
                    "high": 10.0 + i * 0.1,
                    "low": 10.0,
                    "volume": 1000 + i,
                }
            )
        minute_df = pd.DataFrame(rows)
        config = BacktestConfig(
            stock_codes=["000001"],
            start_date="2026-07-15 09:30:00",
            end_date="2026-07-15 15:00:00",
            signal_window=5,
            frequency="minute",
            trade_config=TradeCostConfig(
                buy_slippage=0.2, sell_slippage=0.2, fee_pct=0.02, fee_low=0
            ),
        )
        engine = BacktestEngine(config=config)
        engine._fetch_stock_data = MagicMock(return_value=minute_df)
        engine._fetch_benchmark_data = MagicMock(return_value=pd.DataFrame())

        strategy = SimpleMomentumStrategy()
        strategy.select = MagicMock(
            return_value=[{"code": "000001", "matched": True}]
        )
        strategy.before_trading = MagicMock()

        # 首次检查即买入，下一根 K 线即卖出
        buy_calls = {"n": 0}

        def check_buy(code, bar):
            buy_calls["n"] += 1
            if buy_calls["n"] == 1:
                return (True, {"signal": "BUY", "current_price": bar.get("price", 0)})
            return (False, {"reason": "only first bar"})

        def check_sell(code, position, bar):
            return (True, {"signal": "SELL", "current_price": bar.get("price", 0)})

        strategy.check_minute_buy = check_buy
        strategy.check_minute_sell = check_sell

        trades, summary = engine.run(strategy)

        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade.code, "000001")
        self.assertGreater(trade.buy_price, 0)
        self.assertGreater(trade.sell_price, 0)
        self.assertGreaterEqual(buy_calls["n"], 1)

    def test_minute_backtest_always_passes_empty_position(self) -> None:
        """X1 契约：回测调用策略时恒传空持仓 `{}`，与实盘 `_process_bar` 口径一致。

        回测自己用 `position_states` 记录持仓来完成撮合，但**不向策略暴露持仓**——
        买入成交后引擎已处于持仓状态，后续每根K线仍必须传 `{}` 给策略。
        若这里退化成传真实持仓，回测信号集合会再次与实盘分叉。
        """
        rows = [
            {
                "code": "000001",
                "create_time": f"2026-07-15 09:{30 + i:02d}:00",
                "price": 10.0 + i * 0.1,
                "open": 10.0,
                "close": 10.0 + i * 0.1,
                "high": 10.0 + i * 0.1,
                "low": 10.0,
                "volume": 1000 + i,
            }
            for i in range(40)
        ]
        minute_df = pd.DataFrame(rows)
        config = BacktestConfig(
            stock_codes=["000001"],
            start_date="2026-07-15 09:30:00",
            end_date="2026-07-15 15:00:00",
            signal_window=5,
            frequency="minute",
        )
        engine = BacktestEngine(config=config)
        engine._fetch_stock_data = MagicMock(return_value=minute_df)
        engine._fetch_benchmark_data = MagicMock(return_value=pd.DataFrame())

        strategy = SimpleMomentumStrategy()
        strategy.select = MagicMock(return_value=[{"code": "000001", "matched": True}])
        strategy.before_trading = MagicMock()

        # 首根K线买入建仓 → 引擎进入持仓状态，之后每根K线都会走卖出检查
        buy_calls = {"n": 0}

        def check_buy(code: str, bar: dict[str, Any]):
            buy_calls["n"] += 1
            if buy_calls["n"] == 1:
                return True, {"signal": "BUY", "current_price": bar.get("price", 0)}
            return False, {"reason": "仅首根K线触发买入"}

        seen_positions: list[Any] = []

        def check_sell(code: str, position: dict[str, Any], bar: dict[str, Any]):
            seen_positions.append(position)
            return False, {"reason": "卖出条件不满足"}

        strategy.check_minute_buy = check_buy
        strategy.check_minute_sell = check_sell

        engine.run(strategy)

        # 持仓后仍继续检查卖出 ⇒ 断言确实覆盖了「引擎有持仓」的场景
        self.assertGreater(
            buy_calls["n"], 1, "应至少有一根K线在持仓状态下调用策略"
        )
        self.assertTrue(seen_positions, "check_minute_sell 应至少被调用一次")
        for position in seen_positions:
            self.assertEqual(
                position,
                {},
                f"回测必须传空持仓给策略（X1），实际收到: {position!r}",
            )

    # ---- 结果保存 ----

    def test_save_results_success(self) -> None:
        engine = BacktestEngine()
        engine._backtest_api = MagicMock()
        engine._backtest_api.save.return_value = "rec-1"
        engine._user_strategy_api = MagicMock()
        trades = [
            _trade("000001", "2026-01-05", "2026-01-12", 10.0, 11.0, 10.0)
        ]
        summary = BacktestSummary(
            strategy_name="SimpleMomentum",
            total_trades=1,
            win_count=1,
            total_return_pct=10.0,
            total_profit=1000.0,
            initial_capital=10000.0,
            final_capital=11000.0,
            start_date="2026-01-01",
            end_date="2026-01-31",
        )
        success = engine.save_results(
            SimpleMomentumStrategy(),
            trades,
            summary,
            strategy_id="s1",
            user_strategy_id="us-1",
        )
        self.assertTrue(success)
        # 保存经 BacktestDao（唯一数据载体）交给 BacktestApi
        engine._backtest_api.save.assert_called_once()
        dao = engine._backtest_api.save.call_args.args[0]
        self.assertIsInstance(dao, BacktestDao)
        self.assertEqual(dao.strategy_id, "s1")
        self.assertAlmostEqual(dao.backtest_return_rate, 0.10)
        self.assertAlmostEqual(dao.backtest_profit, 1000.0)
        self.assertEqual(dao.start_date, "2026-01-01")
        self.assertEqual(dao.frequency, engine.config.frequency)
        self.assertEqual(
            dao.result_data["strategy_name"], "SimpleMomentum"
        )
        self.assertEqual(len(dao.result_data["trades"]), 1)
        self.assertEqual(dao.result_data["summary"]["total_trades"], 1)
        engine._user_strategy_api.save_execution.assert_called_once()

    def test_save_results_without_strategy_id(self) -> None:
        engine = BacktestEngine()
        engine._backtest_api = MagicMock()
        success = engine.save_results(
            SimpleMomentumStrategy(), [], BacktestSummary()
        )
        self.assertFalse(success)
        engine._backtest_api.save.assert_not_called()

    def test_save_results_strategy_only(self) -> None:
        """只提供 strategy_id 时仅保存回测结果，不写执行指标。"""
        engine = BacktestEngine()
        engine._backtest_api = MagicMock()
        engine._backtest_api.save.return_value = "rec-1"
        engine._user_strategy_api = MagicMock()
        success = engine.save_results(
            SimpleMomentumStrategy(),
            [],
            BacktestSummary(strategy_name="SimpleMomentum"),
            strategy_id="s1",
        )
        self.assertTrue(success)
        engine._backtest_api.save.assert_called_once()
        engine._user_strategy_api.save_execution.assert_not_called()

    def test_save_results_falls_back_to_strategy_id(self) -> None:
        """未显式传 strategy_id 时，回退使用策略实例上的 strategy_id。"""
        engine = BacktestEngine()
        engine._backtest_api = MagicMock()
        engine._backtest_api.save.return_value = "rec-1"
        strategy = SimpleMomentumStrategy()
        strategy.strategy_id = "s-from-instance"
        success = engine.save_results(strategy, [], BacktestSummary())
        self.assertTrue(success)
        dao = engine._backtest_api.save.call_args.args[0]
        self.assertEqual(dao.strategy_id, "s-from-instance")

    def test_save_results_handles_api_error(self) -> None:
        engine = BacktestEngine()
        engine._backtest_api = MagicMock()
        engine._backtest_api.save.side_effect = Exception("api down")
        engine._user_strategy_api = MagicMock()
        success = engine.save_results(
            SimpleMomentumStrategy(),
            [],
            BacktestSummary(strategy_name="SimpleMomentum"),
            strategy_id="s1",
        )
        self.assertFalse(success)

    # ---- _bind_strategy_id：名称优先、类名回退 ----

    def test_bind_strategy_id_by_name(self) -> None:
        """名称能查到模板时按名称回填，不做类名回退。

        契约：后台模板名与本端 `strategy_name` 一致时保持历史行为。
        """
        engine = BacktestEngine()
        engine._strategy_api = MagicMock()
        engine._strategy_api.get.return_value = {"_id": "s-by-name"}
        strategy = SimpleMomentumStrategy()
        engine._bind_strategy_id(strategy)
        self.assertEqual(strategy.strategy_id, "s-by-name")
        engine._strategy_api.list.assert_not_called()

    def test_bind_strategy_id_falls_back_to_class_name(self) -> None:
        """名称查不到（404）时退化为按 class_name 匹配模板。

        背景（2026-09-15 生产实测）：后台模板显示名与本端类内 `strategy_name`
        曾不一致（本端「强势反弹策略」对应模板「强趋势策略」），只按名称查会 404
        → strategy_id 回填失败 → `run(save=True)` 静默跳过写库。
        现两处名称已对齐（本端改为「强趋势策略」），但类名回退必须保留：
        后台改模板显示名是随时可能发生的，不能让写库再次静默失败。
        """
        engine = BacktestEngine()
        engine._strategy_api = MagicMock()
        engine._strategy_api.get.side_effect = Exception("404 Not Found")
        engine._strategy_api.list.return_value = [
            {"_id": "s-other", "name": "别的策略", "class_name": "OtherStrategy"},
            {
                "_id": "s-by-class",
                "name": "强趋势策略",
                "class_name": "SimpleMomentumStrategy",
            },
        ]
        strategy = SimpleMomentumStrategy()
        engine._bind_strategy_id(strategy)
        self.assertEqual(strategy.strategy_id, "s-by-class")
        engine._strategy_api.list.assert_called_once()

    def test_bind_strategy_id_keeps_previous_when_unresolved(self) -> None:
        """名称与类名都匹配不到时保留原 strategy_id，且不抛异常。"""
        engine = BacktestEngine()
        engine._strategy_api = MagicMock()
        engine._strategy_api.get.side_effect = Exception("404 Not Found")
        engine._strategy_api.list.return_value = [
            {"_id": "s-1", "class_name": "OtherStrategy"},
        ]
        strategy = SimpleMomentumStrategy()
        strategy.strategy_id = "s-existing"
        engine._bind_strategy_id(strategy)
        self.assertEqual(strategy.strategy_id, "s-existing")

    def test_bind_strategy_id_ambiguous_class_name_takes_first(self) -> None:
        """同类名匹配到多条模板时取第一条，不抛异常。"""
        engine = BacktestEngine()
        engine._strategy_api = MagicMock()
        engine._strategy_api.get.side_effect = Exception("404 Not Found")
        engine._strategy_api.list.return_value = [
            {"_id": "s-first", "class_name": "SimpleMomentumStrategy"},
            {"_id": "s-second", "class_name": "SimpleMomentumStrategy"},
        ]
        strategy = SimpleMomentumStrategy()
        engine._bind_strategy_id(strategy)
        self.assertEqual(strategy.strategy_id, "s-first")

    def test_bind_strategy_id_handles_list_error(self) -> None:
        """类名回退时列表接口异常也不抛，保留原 strategy_id。"""
        engine = BacktestEngine()
        engine._strategy_api = MagicMock()
        engine._strategy_api.get.side_effect = Exception("404 Not Found")
        engine._strategy_api.list.side_effect = Exception("api down")
        strategy = SimpleMomentumStrategy()
        strategy.strategy_id = "s-keep"
        engine._bind_strategy_id(strategy)
        self.assertEqual(strategy.strategy_id, "s-keep")

    # ---- run(save=...) 回测结束写库 ----

    def _engine_with_stubbed_run(self) -> BacktestEngine:
        """构造一个 run 内部逻辑被打桩的引擎（不跑真实回测）。"""
        engine = BacktestEngine()
        engine._backtest_api = MagicMock()
        engine._backtest_api.save.return_value = "rec-1"
        engine._run_daily = MagicMock(  # type: ignore[method-assign]
            return_value=([], BacktestSummary(strategy_name="SimpleMomentum"))
        )
        return engine

    def test_run_without_save_does_not_persist(self) -> None:
        """默认（save=False）不回写数据库，只返回回测结果。"""
        engine = self._engine_with_stubbed_run()
        trades, summary = engine.run(SimpleMomentumStrategy())
        self.assertEqual(trades, [])
        self.assertEqual(summary.strategy_name, "SimpleMomentum")
        engine._backtest_api.save.assert_not_called()

    def test_run_with_save_persists_result(self) -> None:
        """save=True 时回测结束即写库，且显式 strategy_id 优先。"""
        engine = self._engine_with_stubbed_run()
        engine._bind_strategy_id = MagicMock()  # type: ignore[method-assign]
        strategy = SimpleMomentumStrategy()
        engine.run(strategy, save=True, strategy_id="s-explicit")
        engine._backtest_api.save.assert_called_once()
        dao = engine._backtest_api.save.call_args.args[0]
        self.assertEqual(dao.strategy_id, "s-explicit")
        # 显式传入的 id 应同时回写到策略实例（供 runner/执行指标复用）
        self.assertEqual(strategy.strategy_id, "s-explicit")

    def test_run_with_save_without_strategy_id_logs_warning(self) -> None:
        """save=True 但拿不到 strategy_id 时保存失败，但不抛异常。"""
        engine = self._engine_with_stubbed_run()
        engine._bind_strategy_id = MagicMock()  # type: ignore[method-assign]
        trades, summary = engine.run(SimpleMomentumStrategy(), save=True)
        self.assertEqual(trades, [])
        engine._backtest_api.save.assert_not_called()


class TestBacktestDataCompleteness(unittest.TestCase):
    """C 决策：回测取数失败要重试，重试后仍失败必须**中止并报错**，
    绝不把不完整数据写成一条看似正常的回测记录。

    背景：服务端 `/api/market/kline/day` 间歇性 HTTP 500（不带日期参数必现），
    旧实现把该异常吞成「no data」→ 0 候选 → 0 交易，于是同一配置重复回测
    会得到 2/0/4/3/0 笔交易（结果不可复现）。
    """

    def test_call_market_api_retries_then_succeeds(self) -> None:
        engine = BacktestEngine(
            config=BacktestConfig(fetch_retries=2, fetch_retry_interval=0)
        )
        calls = {"n": 0}

        def fetch() -> list[dict[str, Any]]:
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("HTTP 500")
            return [{"close": 1.0}]

        self.assertEqual(
            engine._call_market_api("000001 日K", fetch), [{"close": 1.0}]
        )
        self.assertEqual(calls["n"], 3)

    def test_call_market_api_raises_after_retries_exhausted(self) -> None:
        engine = BacktestEngine(
            config=BacktestConfig(fetch_retries=2, fetch_retry_interval=0)
        )
        calls = {"n": 0}

        def always_fail() -> Any:
            calls["n"] += 1
            raise RuntimeError("HTTP 500")

        with self.assertRaises(MarketDataFetchError) as ctx:
            engine._call_market_api("000001 日K", always_fail)

        # 1 次 + 2 次重试 = 3 次尝试，且消息里带足定位信息
        self.assertEqual(calls["n"], 3)
        self.assertIn("000001 日K", str(ctx.exception))
        self.assertIn("重试 3 次后仍失败", str(ctx.exception))

    def test_fetch_stock_data_returns_empty_without_exception_when_no_data(self) -> None:
        """接口正常但无数据 ≠ 取数失败：返回空表，不抛异常、不重试。"""
        engine = BacktestEngine(
            config=BacktestConfig(fetch_retries=2, fetch_retry_interval=0)
        )
        engine._market_api = MagicMock()
        engine._market_api.get_day_kline.return_value = []

        df = engine._fetch_stock_data("000001", "2026-07-01", "2026-07-31")

        self.assertTrue(df.empty)
        engine._market_api.get_day_kline.assert_called_once()

    def test_select_candidates_aborts_on_fetch_failure(self) -> None:
        """取数失败必须上抛，不能回退成「全量股票池」继续瞎算。"""
        engine = BacktestEngine(config=BacktestConfig(fetch_retries=0))
        strategy = SimpleMomentumStrategy()
        strategy.select = MagicMock(side_effect=MarketDataFetchError("取数失败"))

        with self.assertRaises(MarketDataFetchError):
            engine._select_candidates(
                strategy, ["000001"], 5, "SimpleMomentum", today_str="2026-07-15"
            )

    def test_select_candidates_keeps_full_pool_fallback_for_strategy_bug(self) -> None:
        """策略自身异常仍保留原有的「回退全量池」容错（与取数失败区分开）。"""
        engine = BacktestEngine()
        strategy = SimpleMomentumStrategy()
        strategy.select = MagicMock(side_effect=ValueError("策略 bug"))

        codes = engine._select_candidates(
            strategy, ["000001", "600519"], 5, "SimpleMomentum", today_str="2026-07-15"
        )

        self.assertEqual(codes, ["000001", "600519"])

    def test_select_candidates_requests_strict_fetch(self) -> None:
        """回测必须要求 select 严格取数（重试 + 失败即抛）。"""
        engine = BacktestEngine(
            config=BacktestConfig(fetch_retries=3, fetch_retry_interval=0.25)
        )
        strategy = SimpleMomentumStrategy()
        strategy.select = MagicMock(
            return_value=[{"code": "000001", "matched": True, "score": 1.0}]
        )

        engine._select_candidates(
            strategy, ["000001"], 5, "SimpleMomentum", today_str="2026-07-15"
        )

        kwargs = strategy.select.call_args.kwargs
        self.assertTrue(kwargs["raise_on_error"])
        self.assertEqual(kwargs["retries"], 3)
        self.assertEqual(kwargs["retry_interval"], 0.25)

    def test_run_aborts_without_saving_when_data_incomplete(self) -> None:
        """关键：数据不完整时回测中止，且**不写库**（不留下不完整记录）。"""
        engine = BacktestEngine(
            config=BacktestConfig(
                stock_codes=["000001"],
                start_date="2026-07-01",
                end_date="2026-07-31",
                fetch_retries=0,
                fetch_retry_interval=0,
            )
        )
        engine._bind_strategy_id = MagicMock()  # type: ignore[method-assign]
        engine._backtest_api = MagicMock()
        engine._run_daily = MagicMock(  # type: ignore[method-assign]
            side_effect=MarketDataFetchError("000001 日K 取数失败")
        )

        with self.assertRaises(MarketDataFetchError):
            engine.run(SimpleMomentumStrategy(), save=True, strategy_id="s-1")

        engine._backtest_api.save.assert_not_called()

    def test_benchmark_disabled_by_default(self) -> None:
        """基准默认关闭（服务端日K不支持指数代码，P1-27）—— 空 code 直接跳过取数。"""
        engine = BacktestEngine()
        self.assertEqual(engine.config.benchmark_code, "")
        engine._market_api = MagicMock()

        df = engine._fetch_benchmark_data("2026-07-01", "2026-07-31")

        self.assertTrue(df.empty)
        engine._market_api.get_day_kline.assert_not_called()


if __name__ == "__main__":
    unittest.main()
