"""
Author: liguoqiang
Date: 2026-08-02
Description: 回测命令行入口测试 —— 参数解析、策略解析、配置构建、
    JSON 导出与主流程（引擎侧 mock，避免真实回测耗时）。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import backtest.main as cli
from backtest.backtest_engine import BacktestSummary, TradeResult


def _fake_trade() -> TradeResult:
    return TradeResult(
        code="000001",
        strategy_name="强势反弹策略",
        buy_date="2026-03-02",
        sell_date="2026-03-05",
        buy_price=10.2,
        sell_price=10.8,
        profit_pct=5.88,
    )


class TestBacktestCli(unittest.TestCase):
    def test_build_parser_defaults(self) -> None:
        args = cli.build_parser().parse_args(["--codes", "000001"])
        self.assertEqual(args.strategy, "strong")
        self.assertEqual(args.capital, 100000.0)
        self.assertEqual(args.hold_days, 5)
        # 2026-09-28：日线窗口由 20 放大到 40，让 MultiTrendFactor 的 MACD 分项
        # （talib.MACD 需 34 根出值，再要 1 根比较「红柱放大」⇒ 实际 35 根）
        # 在回测链路里真正参与打分。
        self.assertEqual(args.signal_window, 40)
        self.assertEqual(args.frequency, "daily")
        # 2026-10-14：滑点由 0.2 降到 0.02 元 —— 0.2 元/股的往返成本（0.4 元）
        # 对 90 元出头的成交均价约合 0.44%，而原口径中位持仓只有 5 分钟，
        # 滑点几乎吃掉整笔的平均盈亏。
        self.assertEqual(args.buy_slip, 0.02)
        self.assertEqual(args.sell_slip, 0.02)
        self.assertEqual(args.fee_pct, 0.02)
        self.assertEqual(args.fee_low, 5.0)
        # 盘中买入门槛（2026-10-14）：默认关闭，窗口默认 10 分钟（同 cfg）
        self.assertFalse(args.buy_dip_gate)
        self.assertEqual(args.buy_dip_gap_minutes, 10.0)

    def test_signal_window_default_matches_engine_config(self) -> None:
        """CLI 默认窗口必须与 BacktestConfig.signal_window 同源。

        两处一旦漂移，回测窗口与实盘选股窗口（SELECT_KLINE_DAYS）不一致，
        同一策略在两条链路上的趋势分不可比，选出的股票集合会分叉。
        """
        from backtest.backtest_engine import BacktestConfig

        args = cli.build_parser().parse_args(["--codes", "000001"])
        self.assertEqual(args.signal_window, BacktestConfig.signal_window)
        self.assertEqual(BacktestConfig.signal_window, 40)

    def test_resolve_strategy_aliases_and_module(self) -> None:
        from strategy.strong_rebound_strategy import StrongReboundStrategy
        from strategy.swing_trading_strategy import SwingTradingStrategy

        self.assertIs(cli.resolve_strategy("strong"), StrongReboundStrategy)
        self.assertIs(cli.resolve_strategy("rebound"), StrongReboundStrategy)
        self.assertIs(cli.resolve_strategy("swing"), SwingTradingStrategy)
        self.assertIs(
            cli.resolve_strategy(
                "strategy.strong_rebound_strategy:StrongReboundStrategy"
            ),
            StrongReboundStrategy,
        )

    def test_resolve_strategy_error(self) -> None:
        with self.assertRaises(ValueError):
            cli.resolve_strategy("unknown")
        with self.assertRaises(ValueError):
            cli.resolve_strategy("no.such.module:Class")
        with self.assertRaises(ValueError):
            cli.resolve_strategy("backtest.backtest_engine:BacktestEngine")

    def test_build_backtest_config(self) -> None:
        args = cli.build_parser().parse_args(
            [
                "--codes", "000001, 600519",
                "--start", "2026-03-01",
                "--end", "2026-07-31",
                "--hold-days", "3",
                "--buy-slip", "0.3",
                "--fee-low", "3",
            ]
        )
        config = cli.build_backtest_config(args)
        self.assertEqual(config.stock_codes, ["000001", "600519"])
        self.assertEqual(config.hold_days, 3)
        self.assertEqual(config.trade_config.buy_slippage, 0.3)
        self.assertEqual(config.trade_config.fee_low, 3.0)

    def test_build_backtest_config_requires_codes(self) -> None:
        args = cli.build_parser().parse_args([])
        with self.assertRaises(ValueError):
            cli.build_backtest_config(args)

    def test_export_json(self) -> None:
        args = cli.build_parser().parse_args(
            ["--codes", "000001", "--output", "x.json"]
        )
        config = cli.build_backtest_config(args)
        summary = BacktestSummary(strategy_name="强势反弹策略", total_trades=1)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "result.json"
            cli.export_json(str(out), config, [_fake_trade()], summary)
            payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(payload["summary"]["total_trades"], 1)
        self.assertEqual(payload["trades"][0]["code"], "000001")
        self.assertEqual(payload["config"]["hold_days"], 5)

    @patch("backtest.main.BacktestEngine")
    def test_main_runs_and_exports(self, mock_engine_cls: MagicMock) -> None:
        engine = mock_engine_cls.return_value
        summary = BacktestSummary(strategy_name="强势反弹策略", total_trades=1)
        engine.run.return_value = ([_fake_trade()], summary)

        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "result.json")
            code = cli.main(
                [
                    "--strategy", "strong",
                    "--codes", "000001",
                    "--start", "2026-03-01",
                    "--end", "2026-07-31",
                    "--output", out,
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue(Path(out).exists())
        mock_engine_cls.assert_called_once()
        engine.run.assert_called_once()

    @patch("backtest.main.BacktestEngine")
    def test_main_save_requires_strategy_id(self, mock_engine_cls: MagicMock) -> None:
        engine = mock_engine_cls.return_value
        engine.run.return_value = ([], BacktestSummary())
        code = cli.main(["--codes", "000001", "--save"])
        self.assertEqual(code, 2)
        engine.save_results.assert_not_called()

    @patch("backtest.main.BacktestEngine")
    def test_main_save_success(self, mock_engine_cls: MagicMock) -> None:
        engine = mock_engine_cls.return_value
        engine.run.return_value = ([_fake_trade()], BacktestSummary(total_trades=1))
        engine.save_results.return_value = True
        code = cli.main(
            ["--codes", "000001", "--save", "--strategy-id", "s1"]
        )
        self.assertEqual(code, 0)
        engine.save_results.assert_called_once()
        self.assertEqual(engine.save_results.call_args.kwargs["strategy_id"], "s1")

    @patch("backtest.main.BacktestEngine")
    def test_main_handles_run_error(self, mock_engine_cls: MagicMock) -> None:
        engine = mock_engine_cls.return_value
        engine.run.side_effect = Exception("api down")
        code = cli.main(["--codes", "000001"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
