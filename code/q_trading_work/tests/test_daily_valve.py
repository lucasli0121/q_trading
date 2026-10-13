"""盘后个股阀门 + 盘前市场门槛偏移的测试（2026-10-13 新增）。

两项机制（用户口径）：
1. **盘后个股阀门**：当日出现买入信号后，若该股在剩余分钟里收盘价跌破当日 S1 支撑位
   达到 `VALVE_MIN_BREAKS`（默认 2）次，次日该股买入门槛上调 `VALVE_STEP`；卖出侧
   对称 —— 卖出信号后价格反复站上 R1 阻力位（卖飞）则次日卖出门槛上调。
   阀门按「股票维度」存在服务端每日阀门表。
2. **盘前市场偏移**：以前一交易日跌停家数分档映射成全局门槛偏移，跌停多则
   买入门槛上调、卖出门槛下调（更容易卖）。

本文件守住的是「错了不会报错、只会静默跑偏」的几处：分档边界、阀门方向
（正=更难）、`>0` / `<0` 基准门槛在偏移下的换算、以及 workflow 的两个注入点。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import pandas as pd

import strategy.swing_trading_strategy as swing_module
import workflow.strategy_workflow as strategy_workflow
from strategy.base_strategy import BaseStrategy
from strategy.swing_trading_strategy import SwingTradingStrategy
from strategy.valve import compute_stock_valve, count_level_breaks
from tests.test_config_reconcile import make_workflow
from utils.market_breadth import (
    fetch_limit_counts,
    limit_down_offset,
    resolve_market_offset,
)
from workflow.strategy_workflow import StrategyState


def _minutes(*pairs: tuple[str, float]) -> list[tuple[str, float]]:
    return list(pairs)


class TestCountLevelBreaks(unittest.TestCase):
    """`count_level_breaks`：锚点之后穿破点位的分钟计数。"""

    def test_counts_only_after_anchor(self) -> None:
        points = _minutes(
            ("2026-10-13 09:31:00", 9.5),
            ("2026-10-13 10:00:00", 9.9),
            ("2026-10-13 10:01:00", 9.8),
            ("2026-10-13 10:02:00", 10.1),
        )
        # 锚点 10:00，其后的分钟内 <10.0 的有 10:00(9.9) 与 10:01(9.8)
        self.assertEqual(count_level_breaks(points, "2026-10-13 10:00:00", 10.0, True), 2)

    def test_anchor_minute_itself_counts(self) -> None:
        points = _minutes(("2026-10-13 10:00:00", 9.0))
        self.assertEqual(count_level_breaks(points, "2026-10-13 10:00:00", 10.0, True), 1)

    def test_above_mode(self) -> None:
        points = _minutes(
            ("2026-10-13 10:00:00", 11.0),
            ("2026-10-13 10:01:00", 12.0),
            ("2026-10-13 10:02:00", 9.0),
        )
        self.assertEqual(count_level_breaks(points, "2026-10-13 10:00:00", 10.5, False), 2)

    def test_invalid_level_or_anchor_returns_zero(self) -> None:
        points = _minutes(("2026-10-13 10:00:00", 9.0))
        self.assertEqual(count_level_breaks(points, "2026-10-13 10:00:00", 0.0, True), 0)
        self.assertEqual(count_level_breaks(points, "2026-10-13 10:00:00", float("nan"), True), 0)
        self.assertEqual(count_level_breaks(points, "", 10.0, True), 0)

    def test_bad_close_values_are_skipped(self) -> None:
        points = _minutes(
            ("2026-10-13 10:00:00", 9.0),
            ("2026-10-13 10:01:00", float("nan")),
            ("2026-10-13 10:02:00", 8.0),
        )
        self.assertEqual(count_level_breaks(points, "2026-10-13 10:00:00", 10.0, True), 2)


class TestComputeStockValve(unittest.TestCase):
    """`compute_stock_valve`：两侧阀门与其方向。"""

    _POINTS = _minutes(
        ("2026-10-13 09:30:00", 10.5),
        ("2026-10-13 10:00:00", 9.4),
        ("2026-10-13 10:01:00", 9.3),
        ("2026-10-13 10:30:00", 11.2),
        ("2026-10-13 10:31:00", 11.4),
    )

    def test_no_signals_means_no_valve(self) -> None:
        result = compute_stock_valve(self._POINTS, [], [], support=10.0, resistance=11.0)
        self.assertEqual(result["buy_valve"], 0.0)
        self.assertEqual(result["sell_valve"], 0.0)

    def test_buy_valve_triggered_at_threshold(self) -> None:
        result = compute_stock_valve(
            self._POINTS, ["2026-10-13 10:00:00"], [], support=10.0, resistance=11.0,
            step=1.0, min_breaks=2,
        )
        self.assertEqual(result["support_break_count"], 2)
        self.assertEqual(result["buy_valve"], 1.0)
        self.assertEqual(result["sell_valve"], 0.0)

    def test_single_break_does_not_trigger(self) -> None:
        result = compute_stock_valve(
            self._POINTS, ["2026-10-13 10:01:00"], [], support=10.0, resistance=11.0,
            step=1.0, min_breaks=2,
        )
        self.assertEqual(result["support_break_count"], 1)
        self.assertEqual(result["buy_valve"], 0.0)

    def test_sell_valve_uses_resistance(self) -> None:
        result = compute_stock_valve(
            self._POINTS, [], ["2026-10-13 10:30:00"], support=10.0, resistance=11.0,
            step=1.5, min_breaks=2,
        )
        self.assertEqual(result["resistance_break_count"], 2)
        self.assertEqual(result["sell_valve"], 1.5)
        self.assertEqual(result["buy_valve"], 0.0)

    def test_both_sides_independent(self) -> None:
        result = compute_stock_valve(
            self._POINTS,
            ["2026-10-13 10:00:00"],
            ["2026-10-13 10:30:00"],
            support=10.0,
            resistance=11.0,
            step=1.0,
            min_breaks=2,
        )
        self.assertEqual(result["buy_valve"], 1.0)
        self.assertEqual(result["sell_valve"], 1.0)

    def test_valve_capped_by_max(self) -> None:
        result = compute_stock_valve(
            self._POINTS, ["2026-10-13 10:00:00"], [], support=10.0, resistance=11.0,
            step=99.0, min_breaks=2, max_valve=5.0,
        )
        self.assertEqual(result["buy_valve"], 5.0)

    def test_earliest_signal_is_the_anchor(self) -> None:
        """锚点取「最早的一条同向信号」：晚到的信号不会把先前的跌破排除掉。"""
        result = compute_stock_valve(
            self._POINTS,
            ["2026-10-13 10:01:00", "2026-10-13 10:00:00"],
            [],
            support=10.0,
            resistance=11.0,
        )
        self.assertEqual(result["support_break_count"], 2)


class TestMarketBreadth(unittest.TestCase):
    """涨跌停家数 → 门槛偏移。"""

    def test_band_boundaries(self) -> None:
        cases: list[tuple[int, float]] = [
            (0, 0.0),
            (5, 0.0),
            (6, 0.5),
            (15, 0.5),
            (16, 1.0),
            (30, 1.0),
            (31, 2.0),
            (300, 2.0),
        ]
        for value, expected in cases:
            with self.subTest(limit_down=value):
                self.assertEqual(limit_down_offset(value), expected)

    def test_missing_data_is_unrestricted(self) -> None:
        self.assertEqual(limit_down_offset(None), 0.0)
        self.assertEqual(limit_down_offset(-3), 0.0)
        self.assertEqual(limit_down_offset("abc"), 0.0)  # type: ignore[arg-type]

    def test_buy_and_sell_offsets_have_opposite_signs(self) -> None:
        buy_offset, sell_offset = resolve_market_offset(20)
        self.assertEqual(buy_offset, 1.0)
        self.assertEqual(sell_offset, -1.0)

    def test_fetch_falls_back_when_akshare_missing(self) -> None:
        """akshare 不可用时返回 (None, None, 原因)，由调用方按「不限制」兜底。"""
        with mock.patch.dict(sys.modules, {"akshare": None}):
            limit_up, limit_down, error = fetch_limit_counts("20261012")
        self.assertIsNone(limit_up)
        self.assertIsNone(limit_down)
        self.assertTrue(error)


class TestThresholdOffsets(unittest.TestCase):
    """`BaseStrategy` 的门槛偏移口径：正 = 更难。"""

    def _strategy(self) -> SwingTradingStrategy:
        strategy = SwingTradingStrategy()
        strategy.user_strategy_name = "测试策略"
        return strategy

    def test_default_is_zero(self) -> None:
        strategy = self._strategy()
        self.assertEqual(strategy.buy_threshold_offset("600183.SH"), 0.0)
        self.assertEqual(strategy.sell_threshold_offset("600183.SH"), 0.0)

    def test_stock_valve_combines_with_market_offset(self) -> None:
        strategy = self._strategy()
        strategy.set_daily_valves(
            {"600183.SH": {"buy_valve": 1.5, "sell_valve": 2.0}}, "2026-10-12"
        )
        strategy.set_market_offset(buy_offset=1.0, sell_offset=-1.0)
        self.assertAlmostEqual(strategy.buy_threshold_offset("600183.SH"), 2.5)
        self.assertAlmostEqual(strategy.sell_threshold_offset("600183.SH"), 1.0)

    def test_unaffected_code_keeps_market_offset_only(self) -> None:
        strategy = self._strategy()
        strategy.set_daily_valves({"600183.SH": {"buy_valve": 1.5}}, "2026-10-12")
        strategy.set_market_offset(buy_offset=1.0, sell_offset=0.0)
        self.assertAlmostEqual(strategy.buy_threshold_offset("000988.SZ"), 1.0)

    def test_valve_value_is_clamped(self) -> None:
        strategy = self._strategy()
        strategy.set_daily_valves({"600183.SH": {"buy_valve": 999.0}}, "2026-10-12")
        self.assertEqual(strategy.buy_threshold_offset("600183.SH"), strategy.VALVE_MAX)

    def test_disabled_valve_still_applies_market_offset(self) -> None:
        strategy = self._strategy()
        strategy.VALVE_ENABLED = False
        strategy.set_daily_valves({"600183.SH": {"buy_valve": 3.0}}, "2026-10-12")
        strategy.set_market_offset(buy_offset=1.0, sell_offset=0.0)
        self.assertEqual(strategy.buy_threshold_offset("600183.SH"), 1.0)

    def test_clear_resets_stock_valves(self) -> None:
        strategy = self._strategy()
        strategy.set_daily_valves({"600183.SH": {"buy_valve": 2.0}}, "2026-10-12")
        strategy.clear_daily_valves()
        self.assertEqual(strategy.buy_threshold_offset("600183.SH"), 0.0)
        self.assertEqual(strategy.daily_valve_date, "")


class TestSwingThresholdIntegration(unittest.TestCase):
    """驱动真实买卖路径，确认阀门确实改变了触发与否。"""

    def _strategy(self) -> SwingTradingStrategy:
        strategy = SwingTradingStrategy()
        strategy.user_strategy_name = "测试波段策略"
        strategy.user_strategy_id = "us-test"
        strategy.account_drawdown_pct = 0.0
        return strategy

    @staticmethod
    def _buy_inputs() -> dict[str, object]:
        return {
            "code": "600183.SH",
            "name": "测试股",
            "price": 100.0,
            "preclose": 100.0,
            "high": 100.0,
            "create_time": "2026-10-12 10:24:00",
        }

    def _run_buy(self, strategy: SwingTradingStrategy) -> tuple[bool, dict]:
        with (
            mock.patch.object(strategy, "_get_minute_cache", return_value=[{"close": 100.0}]),
            mock.patch.object(strategy, "_get_sr_points", return_value=pd.DataFrame()),
            mock.patch.object(swing_module, "InDaySRFactor") as inday_cls,
            mock.patch.object(swing_module, "BollSRFactor") as boll_cls,
        ):
            inday_cls.return_value.score_with_sr.return_value = 2.0
            boll_cls.return_value.score.return_value = 2.0
            return strategy._check_buy_conditions("600183.SH", self._buy_inputs())

    def test_buy_triggers_without_valve(self) -> None:
        triggered, _extra = self._run_buy(self._strategy())
        self.assertTrue(triggered)

    def test_buy_valve_blocks_weak_signal(self) -> None:
        """基准门槛是「> 0」，买入阀门 2.5 时平均分 2.0 不再触发。"""
        strategy = self._strategy()
        strategy.set_daily_valves({"600183.SH": {"buy_valve": 2.5}}, "2026-10-12")
        triggered, extra = self._run_buy(strategy)
        self.assertFalse(triggered)
        self.assertIn("平均分值:2.00", str(extra.get("reason", "")))

    def _run_sell(self, strategy: SwingTradingStrategy) -> tuple[bool, dict]:
        position: dict[str, object] = {
            "cost_price": 130.53,
            "buy_time": "2026-10-09 09:35:00",
            "quantity": 100,
        }
        stock_data: dict[str, object] = {
            "code": "600183.SH",
            "name": "测试股",
            "price": 100.0,
            "preclose": 100.0,
            "high": 100.0,
            "create_time": "2026-10-12 10:24:00",
        }
        with (
            mock.patch.object(strategy, "_sell_baseline_buy_time", return_value="2026-10-09 09:35:00"),
            mock.patch.object(strategy, "_get_minute_cache", return_value=[{"close": 100.0}]),
            mock.patch.object(strategy, "_get_sr_points", return_value=pd.DataFrame()),
            mock.patch.object(strategy, "_resolve_highest_price", return_value=100.0),
            mock.patch.object(swing_module, "InDaySRFactor") as inday_cls,
            mock.patch.object(swing_module, "BollSRFactor") as boll_cls,
        ):
            inday_cls.return_value.score_with_sr.return_value = -2.0
            boll_cls.return_value.score.return_value = -2.0
            return strategy._check_sell_conditions(
                code="600183.SH", position=position, stock_data=stock_data
            )

    def test_sell_triggers_without_valve(self) -> None:
        triggered, _extra = self._run_sell(self._strategy())
        self.assertTrue(triggered)

    def test_sell_valve_raises_bar(self) -> None:
        """基准门槛是「< 0」，卖出阀门 3.0 后分数 −2.0 不再触发（更难卖）。"""
        strategy = self._strategy()
        strategy.set_daily_valves({"600183.SH": {"sell_valve": 3.0}}, "2026-10-12")
        triggered, _extra = self._run_sell(strategy)
        self.assertFalse(triggered)

    def test_market_offset_makes_sell_easier(self) -> None:
        """市场偏移取负（跌停多）时，卖出门槛降低 ⇒ 较弱的负分也能触发。"""
        strategy = self._strategy()
        strategy.set_market_offset(buy_offset=1.0, sell_offset=-3.0)
        triggered, _extra = self._run_sell(strategy)
        # 门槛变成 0 - (-3) = 3 ⇒ final_score < 3 即触发，−2.0 自然满足
        self.assertTrue(triggered)


class TestStrategyWiringGuard(unittest.TestCase):
    """源码级守卫：三个策略的买卖条件都必须经过统一的门槛偏移。"""

    _STRATEGY_DIR: Path = Path(__file__).resolve().parents[1] / "strategy"

    def test_every_strategy_applies_threshold_offset(self) -> None:
        offenders: list[str] = []
        for path in sorted(self._STRATEGY_DIR.glob("*_strategy.py")):
            text: str = path.read_text(encoding="utf-8")
            if "buy_threshold_offset(" not in text:
                offenders.append(f"{path.name}: 缺 buy_threshold_offset")
            if "sell_threshold_offset(" not in text:
                offenders.append(f"{path.name}: 缺 sell_threshold_offset")
        self.assertEqual(offenders, [], msg="策略未接入门槛偏移")


class TestWorkflowDailyValve(unittest.TestCase):
    """盘后阀门计算 → 入库的编排。"""

    def _state(self) -> StrategyState:
        instance = MagicMock(spec=BaseStrategy)
        instance.VALVE_MIN_BREAKS = 2
        return StrategyState(
            strategy_id="s1",
            user_strategy_id="us1",
            strategy_name="强趋势策略",
            class_path="strategy.strong_rebound_strategy",
            class_name="StrongReboundStrategy",
            instance=instance,
            status="running",
        )

    def test_valve_saved_for_triggered_stock(self) -> None:
        wf = make_workflow()
        state = self._state()
        wf._trade_signal_api = MagicMock()
        wf._trade_signal_api.list.return_value = [
            {"stock_code": "600183.SH", "action": "买入", "create_time": "2026-10-13 10:00:00"},
            {"stock_code": "600183.SH", "action": "买入", "create_time": "2026-10-13 10:05:00"},
        ]
        state.instance.compute_daily_valve.return_value = {
            "buy_valve": 1.0,
            "sell_valve": 0.0,
            "support_break_count": 3,
            "resistance_break_count": 0,
        }
        wf._strategy_daily_valve_api = MagicMock()
        wf._strategy_daily_valve_api.add.return_value = [{"stock_code": "600183.SH"}]

        wf._run_daily_valve_for_state(state, "2026-10-13")

        wf._strategy_daily_valve_api.add.assert_called_once()
        usid, items = wf._strategy_daily_valve_api.add.call_args[0]
        self.assertEqual(usid, "us1")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["stock_code"], "600183.SH")
        self.assertEqual(items[0]["buy_valve"], 1.0)
        self.assertEqual(items[0]["trade_date"], "2026-10-13")
        # 买卖信号时间按方向分组后传给策略
        _code, _date, buys, sells = state.instance.compute_daily_valve.call_args[0]
        self.assertEqual(_code, "600183.SH")
        self.assertEqual(_date, "2026-10-13")
        self.assertEqual(len(buys), 2)
        self.assertEqual(sells, [])

    def test_no_signals_skips_save(self) -> None:
        wf = make_workflow()
        state = self._state()
        wf._trade_signal_api = MagicMock()
        wf._trade_signal_api.list.return_value = []
        wf._strategy_daily_valve_api = MagicMock()

        wf._run_daily_valve_for_state(state, "2026-10-13")

        wf._strategy_daily_valve_api.add.assert_not_called()

    def test_stock_without_breaks_is_not_saved(self) -> None:
        wf = make_workflow()
        state = self._state()
        wf._trade_signal_api = MagicMock()
        wf._trade_signal_api.list.return_value = [
            {"stock_code": "600183.SH", "action": "买入", "create_time": "2026-10-13 10:00:00"},
        ]
        state.instance.compute_daily_valve.return_value = {
            "buy_valve": 0.0,
            "sell_valve": 0.0,
            "support_break_count": 0,
            "resistance_break_count": 0,
        }
        wf._strategy_daily_valve_api = MagicMock()

        wf._run_daily_valve_for_state(state, "2026-10-13")

        wf._strategy_daily_valve_api.add.assert_not_called()

    def test_holiday_skips_whole_job(self) -> None:
        wf = make_workflow()
        wf.xshg.is_session.return_value = False
        wf._snapshot_states = MagicMock(return_value=[self._state()])
        wf._strategy_daily_valve_api = MagicMock()

        wf.daily_after_trading()

        wf._strategy_daily_valve_api.add.assert_not_called()


class TestWorkflowApplyDailyGates(unittest.TestCase):
    """盘前注入：个股阀门 + 市场偏移。"""

    def _state(self) -> StrategyState:
        return StrategyState(
            strategy_id="s1",
            user_strategy_id="us1",
            strategy_name="强趋势策略",
            class_path="strategy.strong_rebound_strategy",
            class_name="StrongReboundStrategy",
            instance=MagicMock(spec=BaseStrategy),
            status="running",
        )

    def test_valves_and_market_offset_injected(self) -> None:
        wf = make_workflow()
        state = self._state()
        wf._strategy_daily_valve_api = MagicMock()
        wf._strategy_daily_valve_api.list.return_value = [
            {"stock_code": "600183.SH", "trade_date": "2026-10-12", "buy_valve": 1.0, "sell_valve": 0.0},
        ]
        wf._market_breadth_api = MagicMock()
        with mock.patch.object(
            strategy_workflow, "fetch_limit_counts", return_value=(60, 20, "")
        ):
            wf._apply_daily_gates(state, "2026-10-13")

        state.instance.set_daily_valves.assert_called_once()
        valves, source_date = state.instance.set_daily_valves.call_args[0]
        self.assertIn("600183.SH", valves)
        self.assertEqual(source_date, "2026-10-12")
        # 跌停 20 家 ⇒ 买入 +1.0、卖出 −1.0
        state.instance.set_market_offset.assert_called_once_with(1.0, -1.0)
        # 阀门按「前一交易日」精确查询
        call_args = wf._strategy_daily_valve_api.list.call_args
        self.assertEqual(call_args[0][0], "us1")
        self.assertEqual(call_args[1].get("trade_date"), "2026-10-12")

    def test_no_valve_clears_previous(self) -> None:
        wf = make_workflow()
        state = self._state()
        wf._strategy_daily_valve_api = MagicMock()
        wf._strategy_daily_valve_api.list.return_value = []
        wf._market_breadth_api = MagicMock()
        with mock.patch.object(
            strategy_workflow, "fetch_limit_counts", return_value=(60, 3, "")
        ):
            wf._apply_daily_gates(state, "2026-10-13")

        state.instance.clear_daily_valves.assert_called_once()
        # 跌停 3 家 ⇒ 不限制
        state.instance.set_market_offset.assert_called_once_with(0.0, 0.0)

    def test_market_fetch_failure_is_unrestricted(self) -> None:
        """取不到涨跌停数据时偏移落 0（不限制），不阻断交易。"""
        wf = make_workflow()
        state = self._state()
        wf._strategy_daily_valve_api = MagicMock()
        wf._strategy_daily_valve_api.list.return_value = []
        wf._market_breadth_api = MagicMock()
        with mock.patch.object(
            strategy_workflow, "fetch_limit_counts", return_value=(None, None, "超时")
        ):
            wf._apply_daily_gates(state, "2026-10-13")

        state.instance.set_market_offset.assert_called_once_with(0.0, 0.0)

    def test_market_offset_cached_per_day(self) -> None:
        """同一交易日内多个策略共用一次取数，不重复打第三方接口。"""
        wf = make_workflow()
        wf._strategy_daily_valve_api = MagicMock()
        wf._strategy_daily_valve_api.list.return_value = []
        wf._market_breadth_api = MagicMock()
        with mock.patch.object(
            strategy_workflow, "fetch_limit_counts", return_value=(60, 20, "")
        ) as fetch:
            wf._apply_daily_gates(self._state(), "2026-10-13")
            wf._apply_daily_gates(self._state(), "2026-10-13")
        self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
