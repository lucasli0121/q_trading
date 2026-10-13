"""盘后个股阀门 + 盘前市场门槛偏移 + 日线趋势门槛 + 极端利空禁买的测试。

四项机制（用户口径）：

1. **盘后个股阀门**：当日出现买入信号后，若该股在剩余分钟里收盘价跌破当日支撑位
   达到 `VALVE_MIN_BREAKS`（默认 2）次，次日该股买入门槛上调；**按触达的最深档
   决定幅度**（破 S1 ⇒ +1×step、S2 ⇒ +2×、S3 ⇒ +3×，档位不叠加）。卖出侧
   对称 —— 卖出信号后价格反复站上 R 阻力档（卖飞）则次日卖出门槛上调。
   阀门按「股票维度」存在服务端每日阀门表。
2. **盘前市场偏移**：以前一交易日跌停家数分档映射成全局门槛偏移，跌停多则
   买入门槛上调、卖出门槛下调（更容易卖，但单独设上限）。
3. **日线趋势门槛**（2026-10-13 二次修订）：趋势向下 ⇒ 买入门槛上调、趋势向上 ⇒
   卖出门槛上调。swing 原先是「趋势向下直接跳过买入判定」的**硬闸门**，改成门槛。
4. **极端利空禁买**（2026-10-13 二次修订）：前一交易日跌停家数超阈值 **且** 该股
   跌破 S3 时，当日该股**只卖不买**（硬性拦截，不受门槛偏移影响）。

本文件守住的是「错了不会报错、只会静默跑偏」的几处：分档边界、阀门方向
（正=更难）、档位取最深而非累加、`>0` / `<0` 基准门槛在偏移下的换算、
趋势门槛的方向与开关、禁买的两个条件必须**同时**成立、以及 workflow 的注入点。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import pandas as pd

import strategy.swing_trading_strategy as swing_module
import strategy.volume_expansion_strategy as volume_module
import workflow.strategy_workflow as strategy_workflow
from strategy.base_strategy import BaseStrategy
from strategy.strong_rebound_strategy import StrongReboundStrategy
from strategy.swing_trading_strategy import SwingTradingStrategy
from strategy.valve import compute_stock_valve, count_level_breaks
from strategy.volume_expansion_strategy import VolumeExpansionStrategy
from tests.test_config_reconcile import make_workflow
from utils.market_breadth import (
    DEFAULT_BANDS,
    DEFAULT_STOP_BUY_LIMIT_DOWN,
    MAX_SELL_OFFSET,
    fetch_limit_counts,
    limit_down_offset,
    parse_bands,
    resolve_market_offset,
)
from workflow.strategy_workflow import StrategyState

_PROJECT_ROOT = Path(__file__).resolve().parent.parent



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

    def test_single_tier_is_tier_one(self) -> None:
        """只传单档时不带档位倍数（向后兼容：幅度 = step × 1）。"""
        result = compute_stock_valve(
            self._POINTS, ["2026-10-13 10:00:00"], [], support=10.0, resistance=11.0,
            step=1.0, min_breaks=2,
        )
        self.assertEqual(result["buy_tier"], 1)
        self.assertEqual(result["support_break_counts"], [2])


class TestMultiTierValve(unittest.TestCase):
    """多档支撑/阻力：按**触达的最深档**决定幅度，档位不叠加。"""

    # s1=10.0 / s2=9.5 / s3=9.0；r1=11.0 / r2=11.5 / r3=12.0
    _SUP: list[float] = [10.0, 9.5, 9.0]
    _RES: list[float] = [11.0, 11.5, 12.0]

    @staticmethod
    def _points(*pairs: tuple[str, float]) -> list[tuple[str, float]]:
        return list(pairs)

    def _run(self, points: list[tuple[str, float]], side: str = "buy", **kwargs):
        buy = ["2026-10-13 10:00:00"] if side == "buy" else []
        sell = ["2026-10-13 10:00:00"] if side == "sell" else []
        return compute_stock_valve(
            points,
            buy,
            sell,
            step=kwargs.pop("step", 1.0),
            min_breaks=kwargs.pop("min_breaks", 2),
            max_valve=kwargs.pop("max_valve", 5.0),
            support_tiers=self._SUP,
            resistance_tiers=self._RES,
        )

    def test_only_s1_broken_gives_one_step(self) -> None:
        points = self._points(
            ("2026-10-13 10:01:00", 9.8),  # 破 s1
            ("2026-10-13 10:02:00", 9.7),  # 破 s1
        )
        result = self._run(points)
        self.assertEqual(result["buy_tier"], 1)
        self.assertEqual(result["support_break_counts"], [2, 0, 0])
        self.assertEqual(result["buy_valve"], 1.0)

    def test_s2_broken_gives_two_steps(self) -> None:
        points = self._points(
            ("2026-10-13 10:01:00", 9.4),  # 破 s1, s2
            ("2026-10-13 10:02:00", 9.3),  # 破 s1, s2
        )
        result = self._run(points)
        self.assertEqual(result["buy_tier"], 2)
        self.assertEqual(result["support_break_counts"], [2, 2, 0])
        self.assertEqual(result["buy_valve"], 2.0)

    def test_s3_broken_gives_three_steps(self) -> None:
        points = self._points(
            ("2026-10-13 10:01:00", 8.8),  # 破 s1, s2, s3
            ("2026-10-13 10:02:00", 8.7),  # 破 s1, s2, s3
        )
        result = self._run(points)
        self.assertEqual(result["buy_tier"], 3)
        self.assertEqual(result["support_break_counts"], [2, 2, 2])
        # 档位不叠加：不是 1+2+3=6
        self.assertEqual(result["buy_valve"], 3.0)

    def test_deep_tier_needs_threshold_itself(self) -> None:
        """S3 只被穿 1 次（不到阈值）时按次深档算，不能直接给最高档。"""
        points = self._points(
            ("2026-10-13 10:01:00", 8.8),  # 破 s1, s2, s3
            ("2026-10-13 10:02:00", 9.4),  # 破 s1, s2
        )
        result = self._run(points)
        self.assertEqual(result["support_break_counts"], [2, 2, 1])
        self.assertEqual(result["buy_tier"], 2)
        self.assertEqual(result["buy_valve"], 2.0)

    def test_tier_valve_uses_step_multiplier(self) -> None:
        points = self._points(
            ("2026-10-13 10:01:00", 8.8),
            ("2026-10-13 10:02:00", 8.7),
        )
        result = self._run(points, step=1.5)
        self.assertEqual(result["buy_valve"], 4.5)

    def test_tier_valve_is_capped(self) -> None:
        points = self._points(
            ("2026-10-13 10:01:00", 8.8),
            ("2026-10-13 10:02:00", 8.7),
        )
        result = self._run(points, step=99.0, max_valve=5.0)
        self.assertEqual(result["buy_valve"], 5.0)

    def test_sell_side_is_symmetric(self) -> None:
        points = self._points(
            ("2026-10-13 10:01:00", 12.1),  # 站上 r1, r2, r3
            ("2026-10-13 10:02:00", 12.2),  # 站上 r1, r2, r3
        )
        result = self._run(points, side="sell")
        self.assertEqual(result["sell_tier"], 3)
        self.assertEqual(result["resistance_break_counts"], [2, 2, 2])
        self.assertEqual(result["sell_valve"], 3.0)
        self.assertEqual(result["buy_valve"], 0.0)

    def test_missing_tier_falls_back_to_shallower(self) -> None:
        """S3 取不到（0）时档位表退化为 S1/S2，最深只到 2。"""
        points = self._points(
            ("2026-10-13 10:01:00", 8.8),
            ("2026-10-13 10:02:00", 8.7),
        )
        result = compute_stock_valve(
            points, ["2026-10-13 10:00:00"], [],
            support_tiers=[10.0, 9.5, 0.0],
            resistance_tiers=[11.0, 11.5, 12.0],
            step=1.0, min_breaks=2,
        )
        self.assertEqual(result["buy_tier"], 2)
        self.assertEqual(result["support_break_counts"], [2, 2])
        self.assertEqual(result["buy_valve"], 2.0)


class TestMarketBreadth(unittest.TestCase):
    """涨跌停家数 → 门槛偏移。"""

    # 分档基数来自实测（2026-09-14 ~ 10-13，跌停家数 0~56，90 分位 27）
    _OBSERVED: tuple[int, ...] = (0, 1, 2, 3, 4, 8, 8, 9, 10, 13, 13, 13, 16, 19, 27, 56)

    def test_band_boundaries(self) -> None:
        cases: list[tuple[int, float]] = [
            (0, 0.0),
            (5, 0.0),
            (6, 0.5),
            (10, 0.5),
            (11, 1.0),
            (20, 1.0),
            (21, 2.0),  # 用户口径：>20 已经很利空
            (30, 2.0),
            (31, 3.0),
            (56, 3.0),  # 实测最大值
            (300, 3.0),
        ]
        for value, expected in cases:
            with self.subTest(limit_down=value):
                self.assertEqual(limit_down_offset(value), expected)

    def test_bands_are_ascending_and_cover_observed_range(self) -> None:
        """分档必须升序且以 inf 收口，否则实测值会落到兜底分支被静默当成 0。"""
        uppers: list[float] = [upper for upper, _ in DEFAULT_BANDS]
        self.assertEqual(uppers, sorted(uppers))
        self.assertEqual(uppers[-1], float("inf"))
        for value in self._OBSERVED:
            with self.subTest(limit_down=value):
                self.assertGreater(limit_down_offset(value), -1.0)
                # 用实测值反查档位表能命中，且结果单调不减
                self.assertIsInstance(limit_down_offset(value), float)
        values = [limit_down_offset(v) for v in sorted(self._OBSERVED)]
        self.assertEqual(values, sorted(values))

    def test_missing_data_is_unrestricted(self) -> None:
        self.assertEqual(limit_down_offset(None), 0.0)
        self.assertEqual(limit_down_offset(-3), 0.0)
        self.assertEqual(limit_down_offset("abc"), 0.0)  # type: ignore[arg-type]

    def test_out_of_range_clamps_to_last_band(self) -> None:
        """超出最后一档按最后一档取值 —— 回落 0 会在最需要收紧的极端日把机制关掉。"""
        narrow = ((5.0, 0.0), (10.0, 0.5))
        self.assertEqual(limit_down_offset(3, narrow), 0.0)
        self.assertEqual(limit_down_offset(10, narrow), 0.5)
        self.assertEqual(limit_down_offset(56, narrow), 0.5)

    def test_parse_bands(self) -> None:
        self.assertEqual(
            parse_bands("5:0,10:0.5,20:1,30:2,9999:3"),
            ((5.0, 0.0), (10.0, 0.5), (20.0, 1.0), (30.0, 2.0), (9999.0, 3.0)),
        )
        # 允许空白与省略项
        self.assertEqual(parse_bands(" 5:0 , 10:0.5 "), ((5.0, 0.0), (10.0, 0.5)))
        self.assertEqual(parse_bands([(5.0, 0.0)]), ((5.0, 0.0),))
        # 非法配置一律回退默认分档，不能静默变成「不限制」
        for bad in ("", "abc", "5", "10:0.5,5:0", "5:0,10:abc"):
            with self.subTest(raw=bad):
                self.assertEqual(parse_bands(bad), DEFAULT_BANDS)

    def test_config_bands_are_used(self) -> None:
        """workflow 注入的 cfg 分档必须真正生效（而不是被模块默认值覆盖）。"""
        wf = make_workflow()
        wf._market_breadth_api = MagicMock()
        wf._market_bands = ((5.0, 0.0), (20.0, 0.5))
        wf._market_max_sell_offset = 0.25
        with mock.patch.object(
            strategy_workflow, "fetch_limit_counts", return_value=(60, 19, "")
        ):
            buy_offset, sell_offset = wf._resolve_market_offset("2026-10-13")
        self.assertEqual(buy_offset, 0.5)
        self.assertEqual(sell_offset, -0.25)

    def test_buy_and_sell_offsets_have_opposite_signs(self) -> None:
        buy_offset, sell_offset = resolve_market_offset(20)
        self.assertEqual(buy_offset, 1.0)
        self.assertEqual(sell_offset, -1.0)

    def test_sell_offset_is_capped(self) -> None:
        """卖出侧放宽有上限：60 家跌停也最多只放宽 MAX_SELL_OFFSET。"""
        buy_offset, sell_offset = resolve_market_offset(600)
        self.assertEqual(buy_offset, 3.0)
        self.assertEqual(sell_offset, -MAX_SELL_OFFSET)

    def test_sell_cap_can_be_disabled(self) -> None:
        _buy, sell_offset = resolve_market_offset(56, max_sell_offset=0)
        self.assertEqual(sell_offset, -3.0)

    def test_example_cfg_bands_are_parseable(self) -> None:
        """随包发布的 `cfg/stock.cfg.example` 分档必须能解析 —— 否则部署机上改的是死配置。"""
        from configparser import ConfigParser

        path = Path(__file__).resolve().parents[1] / "cfg" / "stock.cfg.example"
        cp = ConfigParser()
        cp.read(path, encoding="utf-8")
        self.assertTrue(cp.has_section("market_breadth"), "示例配置缺 [market_breadth] 段")
        raw: str = cp.get("market_breadth", "bands", fallback="")
        self.assertTrue(raw, "示例配置缺 bands")
        parsed = parse_bands(raw)
        # 解析失败会静默回退默认分档，这里用「与默认值不同 + 端点值正确」双重确认
        self.assertNotEqual(parsed, tuple(DEFAULT_BANDS), f"示例分档解析失败(回退默认): {raw}")
        self.assertEqual(parsed[0], (5.0, 0.0))
        self.assertEqual(parsed[-1][1], 3.0)
        self.assertGreaterEqual(
            cp.getfloat("market_breadth", "max_sell_offset", fallback=-1.0), 0.0
        )

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


class TestComputeDailyValveTiers(unittest.TestCase):
    """`BaseStrategy.compute_daily_valve`：六档点位 → 按最深档给幅度。"""

    def _strategy(self) -> SwingTradingStrategy:
        strategy = SwingTradingStrategy()
        strategy.user_strategy_name = "测试波段策略"
        return strategy

    def _run(self, strategy: SwingTradingStrategy, closes: list[float]) -> dict:
        bars = [
            {"create_time": f"2026-10-13 10:{i:02d}:00", "close": c}
            for i, c in enumerate(closes)
        ]
        levels = {"s1": 10.0, "s2": 9.5, "s3": 9.0, "r1": 11.0, "r2": 11.5, "r3": 12.0}
        with (
            mock.patch.object(strategy, "_get_minute_cache", return_value=bars),
            mock.patch.object(strategy, "_daily_sr_levels", return_value=levels),
        ):
            return strategy.compute_daily_valve(
                "600183.SH", "2026-10-13", ["2026-10-13 10:00:00"], []
            )

    def test_returns_all_six_levels(self) -> None:
        result = self._run(self._strategy(), [10.5, 9.8, 9.7])
        for key in ("s1", "s2", "s3", "r1", "r2", "r3"):
            self.assertIn(key, result)
        self.assertEqual(result["s3"], 9.0)
        self.assertEqual(result["r3"], 12.0)

    def test_deep_break_raises_valve_more(self) -> None:
        """同样是「跌破 2 分钟」，破到 S3 的阀门是只破 S1 的 3 倍。"""
        shallow = self._run(self._strategy(), [10.5, 9.8, 9.7])
        deep = self._run(self._strategy(), [10.5, 8.8, 8.7])
        self.assertEqual(shallow["buy_valve"], 1.0)
        self.assertEqual(deep["buy_valve"], 3.0)
        self.assertEqual(deep["buy_tier"], 3)

    def test_no_minute_cache_returns_empty(self) -> None:
        strategy = self._strategy()
        with mock.patch.object(strategy, "_get_minute_cache", return_value=[]):
            self.assertEqual(
                strategy.compute_daily_valve("600183.SH", "2026-10-13", ["x"], []), {}
            )

    def test_disabled_valve_returns_empty(self) -> None:
        strategy = self._strategy()
        strategy.VALVE_ENABLED = False
        self.assertEqual(
            strategy.compute_daily_valve("600183.SH", "2026-10-13", ["x"], []), {}
        )


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
            "buy_tier": 1,
            "sell_tier": 0,
            "support_break_count": 3,
            "resistance_break_count": 0,
            "support_break_counts": [3, 0, 0],
            "resistance_break_counts": [0, 0, 0],
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
        # reason 必须带上各档明细与最深档，否则事后无法判断这笔阀门从哪来
        reason: str = str(items[0]["reason"])
        self.assertIn("S1/S2/S3=3/0/0", reason)
        self.assertIn("R1/R2/R3=0/0/0", reason)
        self.assertIn("最深S1", reason)
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


class TestDailyTrendGate(unittest.TestCase):
    """日线趋势门槛（2026-10-13）：向下 ⇒ 买入更难，向上 ⇒ 卖出更难。"""

    def _strategy(self) -> SwingTradingStrategy:
        strategy = SwingTradingStrategy()
        strategy.user_strategy_name = "测试趋势门槛"
        return strategy

    def test_default_is_zero(self) -> None:
        strategy = self._strategy()
        self.assertEqual(strategy.daily_trend_direction("600183.SH"), 0)
        self.assertEqual(strategy.daily_trend_buy_offset("600183.SH"), 0.0)
        self.assertEqual(strategy.daily_trend_sell_offset("600183.SH"), 0.0)

    def test_each_direction_moves_only_its_own_side(self) -> None:
        """趋势向下只抬买入、向上只抬卖出 —— 两侧互不串台。"""
        strategy = self._strategy()
        strategy.set_daily_trend(
            down_codes=["600183.SH"], up_codes=["000988.SZ"], trade_date="2026-10-13"
        )
        self.assertEqual(strategy.daily_trend_direction("600183.SH"), -1)
        self.assertEqual(strategy.daily_trend_direction("000988.SZ"), 1)
        self.assertAlmostEqual(
            strategy.buy_threshold_offset("600183.SH"), strategy.TREND_BUY_OFFSET
        )
        self.assertEqual(strategy.sell_threshold_offset("600183.SH"), 0.0)
        self.assertEqual(strategy.buy_threshold_offset("000988.SZ"), 0.0)
        self.assertAlmostEqual(
            strategy.sell_threshold_offset("000988.SZ"), strategy.TREND_SELL_OFFSET
        )

    def test_trend_stacks_with_valve_and_market(self) -> None:
        strategy = self._strategy()
        strategy.set_daily_valves(
            {"600183.SH": {"buy_valve": 2.0, "sell_valve": 1.0}}, "2026-10-12"
        )
        strategy.set_market_offset(buy_offset=1.0, sell_offset=-0.5)
        strategy.set_daily_trend(
            down_codes=["600183.SH"], up_codes=["600183.SH"], trade_date="2026-10-13"
        )
        # 买入 = 阀门 2.0 + 市场 1.0 + 趋势 1.0
        self.assertAlmostEqual(strategy.buy_threshold_offset("600183.SH"), 4.0)
        # 卖出 = 阀门 1.0 + 市场 −0.5 + 趋势 1.0
        self.assertAlmostEqual(strategy.sell_threshold_offset("600183.SH"), 1.5)

    def test_gate_can_be_disabled(self) -> None:
        strategy = self._strategy()
        strategy.TREND_GATE_ENABLED = False
        strategy.set_daily_trend(
            down_codes=["600183.SH"], up_codes=["600183.SH"], trade_date="2026-10-13"
        )
        # 关掉总开关后方向仍可查（便于排查），但不产生偏移
        self.assertEqual(strategy.daily_trend_direction("600183.SH"), -1)
        self.assertEqual(strategy.buy_threshold_offset("600183.SH"), 0.0)
        self.assertEqual(strategy.sell_threshold_offset("600183.SH"), 0.0)

    def test_clear_resets_direction(self) -> None:
        strategy = self._strategy()
        strategy.set_daily_trend(
            down_codes=["600183.SH"], up_codes=[], trade_date="2026-10-13"
        )
        strategy.clear_daily_trend()
        self.assertEqual(strategy.daily_trend_direction("600183.SH"), 0)
        self.assertEqual(strategy.daily_trend_date, "")

    def test_direction_requires_both_adx_and_di(self) -> None:
        """只有 ADX 或只有 DI 都不足以定向；NaN 一律判不出。"""
        strategy = self._strategy()
        threshold: float = float(strategy.TREND_ADX_THRESHOLD)
        cases: list[tuple[dict, int]] = [
            # ADX 不到门槛 ⇒ 无方向（震荡市里 DI 频繁交叉，不能据此定向）
            ({"adx": threshold - 1, "+DI": 10.0, "-DI": 30.0}, 0),
            # ADX 达门槛且 −DI 占优 ⇒ 向下
            ({"adx": threshold + 1, "+DI": 10.0, "-DI": 30.0}, -1),
            # ADX 达门槛且 +DI 占优 ⇒ 向上
            ({"adx": threshold + 1, "+DI": 30.0, "-DI": 10.0}, 1),
            # NaN（数据不足 / 次新股 / 停牌复牌）⇒ 判不出，不得当成趋势
            ({"adx": float("nan"), "+DI": 30.0, "-DI": 10.0}, 0),
            ({"adx": threshold + 1, "+DI": float("nan"), "-DI": 10.0}, 0),
            ({}, 0),
        ]
        for detail, expected in cases:
            with self.subTest(detail=detail):
                self.assertEqual(strategy._daily_trend_direction(detail), expected)

    def test_load_daily_trend_is_fail_open(self) -> None:
        """取数失败 ⇒ 不产生偏移（门槛是附加收紧项，缺失不该改变原有行为）。"""
        strategy = self._strategy()
        strategy.load_his_daily_data = MagicMock(side_effect=RuntimeError("取数失败"))
        strategy.load_daily_trend(["600183.SH"], "2026-10-13")
        self.assertEqual(strategy.daily_trend_direction("600183.SH"), 0)
        self.assertEqual(strategy.buy_threshold_offset("600183.SH"), 0.0)


class TestBuyBlock(unittest.TestCase):
    """极端利空禁买：市场跌停超阈值 **且** 个股跌破 S3 ⇒ 只卖不买。"""

    def _state(self) -> StrategyState:
        instance = SwingTradingStrategy()
        instance.user_strategy_name = "测试禁买"
        return StrategyState(
            strategy_id="s1",
            user_strategy_id="us1",
            strategy_name="测试禁买",
            class_path="strategy.swing_trading_strategy",
            class_name="SwingTradingStrategy",
            instance=instance,
            status="running",
        )

    def _apply(
        self,
        wf: strategy_workflow.StrategyWorkflow,
        state: StrategyState,
        limit_down: int | None,
        valves: list[dict],
    ) -> None:
        wf._strategy_daily_valve_api = MagicMock()
        wf._strategy_daily_valve_api.list.return_value = valves
        wf._market_breadth_api = MagicMock()
        with mock.patch.object(
            strategy_workflow,
            "fetch_limit_counts",
            return_value=(60, limit_down, "" if limit_down is not None else "超时"),
        ):
            wf._apply_daily_gates(state, "2026-10-13")

    def test_extreme_market_with_s3_blocks(self) -> None:
        wf = make_workflow()
        state = self._state()
        self._apply(
            wf, state, 56,
            [{"stock_code": "600183.SH", "trade_date": "2026-10-12",
              "buy_valve": 3.0, "sell_valve": 0.0}],
        )
        self.assertTrue(state.instance.is_buy_blocked("600183.SH"))
        self.assertFalse(state.instance.is_buy_blocked("000988.SZ"))

    def test_extreme_market_with_shallow_valve_does_not_block(self) -> None:
        """只破 S2（+2.0）不算「跌破 S3」，仍可买入（门槛已抬高）。"""
        wf = make_workflow()
        state = self._state()
        self._apply(
            wf, state, 56,
            [{"stock_code": "600183.SH", "buy_valve": 2.0, "sell_valve": 0.0}],
        )
        self.assertFalse(state.instance.is_buy_blocked("600183.SH"))

    def test_market_at_threshold_does_not_block(self) -> None:
        """阈值是「严格大于」：恰好 30 家不触发。"""
        wf = make_workflow()
        state = self._state()
        self._apply(
            wf, state, 30, [{"stock_code": "600183.SH", "buy_valve": 3.0}]
        )
        self.assertFalse(state.instance.is_buy_blocked("600183.SH"))

    def test_extreme_market_at_default_threshold_blocks(self) -> None:
        """阈值取自配置，默认 30（`DEFAULT_STOP_BUY_LIMIT_DOWN`）。"""
        wf = make_workflow()
        state = self._state()
        self.assertEqual(wf._market_stop_buy_limit_down, DEFAULT_STOP_BUY_LIMIT_DOWN)
        self._apply(
            wf, state, int(DEFAULT_STOP_BUY_LIMIT_DOWN) + 1,
            [{"stock_code": "600183.SH", "buy_valve": 3.0}],
        )
        self.assertTrue(state.instance.is_buy_blocked("600183.SH"))

    def test_fetch_failure_never_blocks(self) -> None:
        """取不到跌停家数 ⇒ 不触发禁买（不许一次接口抖动停掉整个实例的买入）。"""
        wf = make_workflow()
        state = self._state()
        self._apply(wf, state, None, [{"stock_code": "600183.SH", "buy_valve": 3.0}])
        self.assertFalse(state.instance.is_buy_blocked("600183.SH"))

    def test_config_switch_disables_block(self) -> None:
        """`stop_buy_limit_down = 0` 关闭联动，只保留门槛偏移。"""
        wf = make_workflow()
        state = self._state()
        wf._market_stop_buy_limit_down = 0
        wf._market_limit_down = 300
        wf._apply_buy_block(state.instance, {"600183.SH": {"buy_valve": 5.0}})
        self.assertFalse(state.instance.is_buy_blocked("600183.SH"))

    def test_block_is_replaced_not_accumulated(self) -> None:
        """名单整体替换：条件消失（或换股）时旧名单必须清掉，否则会跨日残留。"""
        wf = make_workflow()
        state = self._state()
        instance = state.instance
        wf._market_stop_buy_limit_down = 30
        wf._market_limit_down = 56
        wf._apply_buy_block(instance, {"600183.SH": {"buy_valve": 3.0}})
        self.assertTrue(instance.is_buy_blocked("600183.SH"))
        wf._apply_buy_block(instance, {"000001.SZ": {"buy_valve": 3.0}})
        self.assertFalse(instance.is_buy_blocked("600183.SH"))
        self.assertTrue(instance.is_buy_blocked("000001.SZ"))
        wf._apply_buy_block(instance, {})
        self.assertFalse(instance.is_buy_blocked("000001.SZ"))

    def test_block_can_be_disabled_on_strategy(self) -> None:
        wf = make_workflow()
        state = self._state()
        state.instance.BLOCK_BUY_ENABLED = False
        wf._market_stop_buy_limit_down = 30
        wf._market_limit_down = 56
        wf._apply_buy_block(state.instance, {"600183.SH": {"buy_valve": 3.0}})
        self.assertFalse(state.instance.is_buy_blocked("600183.SH"))

    def test_sell_side_is_unaffected(self) -> None:
        """「只卖不买」：禁买不得顺手把卖出门槛也改掉。"""
        wf = make_workflow()
        state = self._state()
        instance = state.instance
        instance.set_market_offset(buy_offset=3.0, sell_offset=-1.5)
        before: float = instance.sell_threshold_offset("600183.SH")
        wf._market_stop_buy_limit_down = 30
        wf._market_limit_down = 56
        wf._apply_buy_block(instance, {"600183.SH": {"buy_valve": 3.0}})
        self.assertTrue(instance.is_buy_blocked("600183.SH"))
        self.assertAlmostEqual(instance.sell_threshold_offset("600183.SH"), before)


class TestBuyBlockInStrategies(unittest.TestCase):
    """禁买必须在**三个策略**的买入判定入口硬拦截（源码级 + 功能级）。"""

    _MINUTE_BAR: dict = {
        "price": 10.0,
        "open": 9.9,
        "preclose": 9.8,
        "create_time": "2026-10-13 10:00:00",
    }

    def test_every_strategy_checks_buy_block(self) -> None:
        """源码级守卫：每个策略的买入判定都必须先查 `is_buy_blocked`。

        漏掉一个策略 ⇒ 该策略在极端行情里照常开仓，而日志完全正常。
        """
        for rel in (
            "strategy/swing_trading_strategy.py",
            "strategy/volume_expansion_strategy.py",
            "strategy/strong_rebound_strategy.py",
        ):
            with self.subTest(module=rel):
                text: str = (_PROJECT_ROOT / rel).read_text(encoding="utf-8")
                self.assertIn("is_buy_blocked(code)", text)

    def test_blocked_code_is_rejected_by_all_strategies(self) -> None:
        factories = (
            ("swing", SwingTradingStrategy),
            ("volume", VolumeExpansionStrategy),
            ("strong", StrongReboundStrategy),
        )
        for label, factory in factories:
            with self.subTest(strategy=label):
                strategy = factory()
                strategy.user_strategy_name = f"测试{label}"
                strategy.set_buy_blocked(["600183.SH"])
                matched, detail = strategy._check_buy_conditions(
                    "600183.SH", dict(self._MINUTE_BAR)
                )
                self.assertFalse(matched)
                self.assertIn("禁买", str(detail.get("reason", "")))

    def test_unblocked_code_still_walks_normal_path(self) -> None:
        """没进禁买名单的股票不能因为新增判断被判死。"""
        strategy = SwingTradingStrategy()
        strategy.user_strategy_name = "测试未禁买"
        strategy.set_buy_blocked(["600183.SH"])
        _matched, detail = strategy._check_buy_conditions("000988.SZ", dict(self._MINUTE_BAR))
        self.assertNotIn("禁买", str(detail.get("reason", "")))

    def test_block_can_be_disabled_by_class_flag(self) -> None:
        strategy = SwingTradingStrategy()
        strategy.user_strategy_name = "测试关禁买"
        strategy.set_buy_blocked(["600183.SH"])
        strategy.BLOCK_BUY_ENABLED = False
        _matched, detail = strategy._check_buy_conditions("600183.SH", dict(self._MINUTE_BAR))
        self.assertNotIn("禁买", str(detail.get("reason", "")))


class TestTrendGateWiringGuard(unittest.TestCase):
    """趋势闸门从「硬闸门」改成「门槛」的源码级守卫。"""

    def test_swing_minute_route_no_longer_skips_downtrend(self) -> None:
        """`handle_minute_bar` 不能再短路趋势向下的股票 —— 短路了门槛就永不生效。

        同时守住「方向集合唯一真源」：子类不得再维护 `_downtrend_codes` 那类私有副本，
        否则门槛读基类集合、填充写私有集合，偏移恒为 0 且没有任何报错。
        """
        text: str = (_PROJECT_ROOT / "strategy/swing_trading_strategy.py").read_text(
            encoding="utf-8"
        )
        self.assertNotRegex(text, r"self\._downtrend_codes\s*[:=.]")
        self.assertNotIn("if not downtrend and not break_max", text)
        self.assertIn("self.add_daily_trend(", text)

    def test_swing_selection_no_longer_drops_downtrend_stocks(self) -> None:
        """选股条件 0 也不能再剔除：剔除会让门槛拿不到这些股票。"""
        text: str = (_PROJECT_ROOT / "strategy/swing_trading_strategy.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("波段策略不参与", text)

    def test_swing_injects_trend_direction(self) -> None:
        text: str = (_PROJECT_ROOT / "strategy/swing_trading_strategy.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("self.set_daily_trend(", text)

    def test_volume_strategy_loads_trend_before_trading(self) -> None:
        """成交量策略原先没有 before_trading，趋势门槛必须新增盘前判定。"""
        text: str = (_PROJECT_ROOT / "strategy/volume_expansion_strategy.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("def before_trading", text)
        self.assertIn("self.load_daily_trend(", text)

    def test_volume_strategy_does_not_fetch_daily_kline_intraday(self) -> None:
        """趋势判定必须在盘前完成：盘中取日K（HTTP）会拖慢交易线程。"""
        text: str = (_PROJECT_ROOT / "strategy/volume_expansion_strategy.py").read_text(
            encoding="utf-8"
        )
        buy_body: str = text.split("def _check_buy_conditions", 1)[1]
        self.assertNotIn("load_daily_trend", buy_body.split("def _check_sell_conditions", 1)[0])

    def test_every_strategy_has_the_offset_in_buy_and_sell(self) -> None:
        """三个策略的买卖判定都要走统一的 `buy/sell_threshold_offset`。"""
        for rel in (
            "strategy/swing_trading_strategy.py",
            "strategy/volume_expansion_strategy.py",
            "strategy/strong_rebound_strategy.py",
        ):
            with self.subTest(module=rel):
                text: str = (_PROJECT_ROOT / rel).read_text(encoding="utf-8")
                self.assertIn("buy_threshold_offset(", text)
                self.assertIn("sell_threshold_offset(", text)


if __name__ == "__main__":
    unittest.main()
