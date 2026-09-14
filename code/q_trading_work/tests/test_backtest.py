"""
Author: liguoqiang
Date: 2026-08-02
Description: 回测引擎测试 —— 日频回测使用真实 API 行情数据；
    成本模型、权益曲线、汇总统计等数学不变量使用受控数据验证。
    所有结果写入均 mock，避免污染生产数据。
"""

from __future__ import annotations

import unittest
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
from strategy.base_strategy import BaseStrategy
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
            "last_close": float(stock_data[-1]["close"])
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
    def test_run_daily_with_real_data(self) -> None:
        """真实日K数据回测：产生交易、买卖价格含滑点、持有天数正确。"""
        config = BacktestConfig(
            initial_capital=100000,
            stock_codes=["000001"],
            start_date="2026-03-01",
            end_date="2026-07-31",
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


if __name__ == "__main__":
    unittest.main()
