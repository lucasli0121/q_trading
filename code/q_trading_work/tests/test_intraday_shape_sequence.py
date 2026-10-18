"""日内路径判据（冲高回落）、跨日形态序列与跨日趋势判据。

守的是四件事：

1. **gap 判不出「高开低走」**。用户举的反例是平开、9:30–10:30 冲高形成全天高点、
   之后一路下跌、收盘低于开盘。按 `open/preclose` 判它是「平开」，按
   `close/open` 判只是「走弱」，都不足以表达「上方有派发」。判据必须是
   峰值时段 + 峰值相对开盘的涨幅 + 从峰值的回撤。
2. **「开盘即最高、一路阴跌」不是冲高回落**。两者的 `peak_seg` 都是首段、
   `fall_from_peak` 都很深，只靠这两项会把所有单边下跌日都归成「高开低走」，
   于是「低开低走」这个标签永远取不到。
3. **跨日序列只能含已定型的历史日**。昨日及更早的形态是隔夜可得信息，
   当日标签在盘中会变，放进去就是未来函数。
4. **趋势计数与标签序列相互独立**。两者数据源不同（日K vs 分钟归档），
   规则表各自命中、累加进同一个 `sequence_adjust`；任一方无数据不影响另一方。

统计结论（2026-07-17~10-13，39 个交易日 / 1514 个股票-日）见
`CODE_方案文档_2026-10-18_日内形态因子接入.md` 第七节：路径编码口径成立，
但「连弱 ⇒ 进一步走弱」在次日与当日两个尺度上都不显著，因此默认规则表为空。
趋势判据（低于均线次数、高点低于前日次数）2026-10-19 引入，同样默认空表。
"""

import sys
import types
import unittest

if "talib" not in sys.modules:
    try:
        import talib  # noqa: F401
    except ImportError:
        sys.modules["talib"] = types.ModuleType("talib")

from factor.intraday_shape_factor import (  # noqa: E402
    DAY_TYPE_FLAT,
    DAY_TYPE_HIGH_HIGH,
    DAY_TYPE_HIGH_LOW,
    DAY_TYPE_LOW_HIGH,
    DAY_TYPE_LOW_LOW,
    DAY_TYPE_STRONG,
    DAY_TYPE_WEAK,
    IntradayShapeFactor,
)

# 交易时段分钟序号：09:30~11:30、13:00~15:00（含两端）
MINUTES: list[int] = [m for m in range(9 * 60 + 30, 11 * 60 + 31)] + [
    m for m in range(13 * 60, 15 * 60 + 1)
]


def _hhmm(minute: int) -> str:
    hour, mins = divmod(minute, 60)
    return f"{hour:02d}:{mins:02d}"


def _interp_day(
    points: list[tuple[str, float]],
    open_price: float,
    preclose: float,
) -> list[dict]:
    """按关键点做线性插值，生成全天每分钟的 close（时间正序）。

    :param points: [(HH:MM, close)]，必须覆盖 09:30 与 15:00
    """
    def to_minute(stamp: str) -> int:
        hour, mins = stamp.split(":")
        return int(hour) * 60 + int(mins)

    keys = [(to_minute(stamp), price) for stamp, price in points]
    rows: list[dict] = []
    running_high = open_price
    running_low = open_price
    for minute in MINUTES:
        if minute <= keys[0][0]:
            close = keys[0][1]
        elif minute >= keys[-1][0]:
            close = keys[-1][1]
        else:
            close = keys[-1][1]
            for (left_t, left_p), (right_t, right_p) in zip(keys, keys[1:]):
                if left_t <= minute <= right_t:
                    span = right_t - left_t
                    ratio = (minute - left_t) / span if span else 0.0
                    close = left_p + (right_p - left_p) * ratio
                    break
        running_high = max(running_high, close)
        running_low = min(running_low, close)
        rows.append(
            {
                "code": "000001.SZ",
                "minute_time": f"2026-10-14 {_hhmm(minute)}:00",
                "open": open_price,
                "high": running_high,
                "low": running_low,
                "close": close,
                "preclose": preclose,
                "volume": 1000,
                "amount": 10000.0,
            }
        )
    return rows


class PeakPathTest(unittest.TestCase):
    """日内高点定位与「冲高回落」判据。"""

    def setUp(self) -> None:
        self.factor = IntradayShapeFactor()

    def test_flat_open_dive_is_high_open_low_close(self) -> None:
        """用户的原始反例：平开、早盘冲高、之后一路下跌。

        gap 是「平开」，但它就是「高开低走」——这正是旧口径判不出来的场景。
        """
        bars = _interp_day(
            [("09:30", 10.0), ("10:20", 10.40), ("15:00", 9.90)],
            open_price=10.0,
            preclose=10.0,
        )
        features = self.factor.encode(bars)
        assert features is not None
        self.assertEqual(features["gap_b"], "平开")
        self.assertEqual(features["peak_seg"], 0)  # 峰值落在 9:30~10:30
        self.assertTrue(features["dive"])
        self.assertEqual(features["day_type"], DAY_TYPE_HIGH_LOW)
        self.assertLess(features["fall_from_peak"], -0.02)

    def test_low_open_dive_is_also_high_open_low_close(self) -> None:
        """低开后冲高再跌破开盘，仍算「高开低走」——用户明确说过不要求 gap > 0。"""
        bars = _interp_day(
            [("09:30", 9.70), ("10:00", 10.10), ("15:00", 9.55)],
            open_price=9.70,
            preclose=10.0,
        )
        features = self.factor.encode(bars)
        assert features is not None
        self.assertEqual(features["gap_b"], "低开")
        self.assertTrue(features["dive"])
        self.assertEqual(features["day_type"], DAY_TYPE_HIGH_LOW)

    def test_open_is_day_high_is_low_open_low_close(self) -> None:
        """开盘即最高、一路阴跌没有「冲高」，不能算冲高回落。"""
        bars = _interp_day(
            [("09:30", 9.80), ("15:00", 9.50)],
            open_price=9.80,
            preclose=10.0,
        )
        features = self.factor.encode(bars)
        assert features is not None
        self.assertEqual(features["peak_seg"], 0)
        # 回撤很深（9.50/9.80-1 = -3.1%），但没有「冲高」，dive 必须为假
        self.assertLess(features["fall_from_peak"], -0.03)
        self.assertFalse(features["dive"])
        self.assertEqual(features["day_type"], DAY_TYPE_LOW_LOW)

    def test_high_open_steady_rise_is_not_high_open_low_close(self) -> None:
        bars = _interp_day(
            [("09:30", 10.20), ("15:00", 10.60)],
            open_price=10.20,
            preclose=10.0,
        )
        features = self.factor.encode(bars)
        assert features is not None
        self.assertEqual(features["gap_b"], "高开")
        self.assertFalse(features["dive"])
        self.assertEqual(features["day_type"], DAY_TYPE_HIGH_HIGH)

    def test_low_open_steady_rise(self) -> None:
        bars = _interp_day(
            [("09:30", 9.80), ("15:00", 10.30)],
            open_price=9.80,
            preclose=10.0,
        )
        features = self.factor.encode(bars)
        assert features is not None
        self.assertEqual(features["day_type"], DAY_TYPE_LOW_HIGH)

    def test_flat_open_steady_fall_is_weak(self) -> None:
        """平开、全天缓跌：既不是冲高回落，也不属于低开/高开。"""
        bars = _interp_day(
            [("09:30", 10.0), ("15:00", 9.85)],
            open_price=10.0,
            preclose=10.0,
        )
        features = self.factor.encode(bars)
        assert features is not None
        self.assertFalse(features["dive"])
        self.assertEqual(features["day_type"], DAY_TYPE_WEAK)

    def test_flat_close_is_flat(self) -> None:
        bars = _interp_day(
            [("09:30", 10.0), ("10:00", 10.05), ("15:00", 10.02)],
            open_price=10.0,
            preclose=10.0,
        )
        features = self.factor.encode(bars)
        assert features is not None
        self.assertEqual(features["day_type"], DAY_TYPE_FLAT)

    def test_prefix_mode_has_no_day_type(self) -> None:
        """盘中未收盘时不给日标签：峰值还会后移、回撤还会变。"""
        bars = _interp_day(
            [("09:30", 10.0), ("10:20", 10.40), ("15:00", 9.90)],
            open_price=10.0,
            preclose=10.0,
        )
        features = self.factor.encode(bars, as_of="14:30")
        assert features is not None
        self.assertEqual(features["day_type"], "")
        # 盘中判据本身可用：14:30 时已从峰值回落 4% 以上
        self.assertTrue(features["dive"])

    def test_dive_rule_only_hits_when_dive(self) -> None:
        """规则表按回落档匹配，且必须 `dive=True` 才生效。"""
        factor = IntradayShapeFactor(dive_rules={"深回落": -2.0})
        dive_bars = _interp_day(
            [("09:30", 10.0), ("10:20", 10.40), ("15:00", 9.90)],
            open_price=10.0,
            preclose=10.0,
        )
        result = factor.calculate(dive_bars)
        self.assertAlmostEqual(result["intraday_adjust"], -2.0)
        self.assertEqual(result["intraday_score"], 3.0)

        # 亏损相同但「开盘即最高」：不是冲高，不该命中
        drift_bars = _interp_day(
            [("09:30", 10.40), ("15:00", 9.90)],
            open_price=10.40,
            preclose=10.40,
        )
        result = factor.calculate(drift_bars)
        self.assertFalse(result["dive"])
        self.assertEqual(result["intraday_adjust"], 0.0)


class SequenceFeatureTest(unittest.TestCase):
    """跨日序列特征（纯计算）。"""

    def setUp(self) -> None:
        self.factor = IntradayShapeFactor()

    def test_weak_streak_counts_from_yesterday(self) -> None:
        features = self.factor.sequence_features(
            [DAY_TYPE_LOW_LOW, DAY_TYPE_HIGH_LOW, DAY_TYPE_LOW_HIGH]
        )
        self.assertEqual(features["weak_streak"], 2)
        self.assertEqual(features["prev_day_type"], DAY_TYPE_LOW_LOW)
        self.assertAlmostEqual(features["decay_weak"], 1.0 + 0.6, places=3)

    def test_empty_label_breaks_streak(self) -> None:
        """算不出来的那天不能假定它延续了弱势。"""
        features = self.factor.sequence_features(
            [DAY_TYPE_LOW_LOW, "", DAY_TYPE_HIGH_LOW]
        )
        self.assertEqual(features["weak_streak"], 1)
        # 空串不计分，但权重位置照常按时间推进：第三天的弱势只能拿 0.6^2。
        # 若把空串丢掉、让「前一日」顶上，等于把「中间缺一天」读成「连着两天」。
        self.assertAlmostEqual(features["decay_weak"], 1.0 + 0.6**2, places=3)

    def test_strong_streak(self) -> None:
        features = self.factor.sequence_features(
            [DAY_TYPE_STRONG, DAY_TYPE_HIGH_HIGH, DAY_TYPE_FLAT]
        )
        self.assertEqual(features["strong_streak"], 2)
        self.assertEqual(features["weak_streak"], 0)

    def test_sequence_is_joined(self) -> None:
        features = self.factor.sequence_features([DAY_TYPE_HIGH_LOW, DAY_TYPE_WEAK])
        self.assertEqual(features["seq"], f"{DAY_TYPE_HIGH_LOW}|{DAY_TYPE_WEAK}")
        self.assertEqual(features["days"], 2)

    def test_weak_streak_rule_picks_largest_key(self) -> None:
        factor = IntradayShapeFactor(weak_streak_rules={2: -1.0, 3: -2.0})
        bars = _interp_day(
            [("09:30", 10.0), ("15:00", 10.3)],
            open_price=10.0,
            preclose=10.0,
        )
        result = factor.calculate(
            bars,
            history=[DAY_TYPE_LOW_LOW, DAY_TYPE_HIGH_LOW, DAY_TYPE_WEAK],
        )
        self.assertTrue(result["sequence_ok"])
        self.assertAlmostEqual(result["sequence_adjust"], -2.0)
        self.assertEqual(result["weak_streak"], 3)

    def test_sequence_rule_longest_match_wins(self) -> None:
        factor = IntradayShapeFactor(
            sequence_rules={
                f"{DAY_TYPE_LOW_LOW}|{DAY_TYPE_HIGH_LOW}": -1.5,
                f"{DAY_TYPE_LOW_LOW}|{DAY_TYPE_HIGH_LOW}|{DAY_TYPE_WEAK}": -3.0,
            }
        )
        bars = _interp_day(
            [("09:30", 10.0), ("15:00", 10.3)],
            open_price=10.0,
            preclose=10.0,
        )
        result = factor.calculate(
            bars, history=[DAY_TYPE_LOW_LOW, DAY_TYPE_HIGH_LOW, DAY_TYPE_WEAK]
        )
        self.assertAlmostEqual(result["sequence_adjust"], -3.0)

    def test_prev_day_rule(self) -> None:
        factor = IntradayShapeFactor(prev_day_rules={DAY_TYPE_LOW_LOW: -1.0})
        bars = _interp_day(
            [("09:30", 10.0), ("15:00", 10.3)],
            open_price=10.0,
            preclose=10.0,
        )
        result = factor.calculate(bars, history=[DAY_TYPE_LOW_LOW])
        self.assertAlmostEqual(result["sequence_adjust"], -1.0)


class SequenceWithIntradayTest(unittest.TestCase):
    """两层分开返回，且当日层不可用时跨日层仍然可用。"""

    def setUp(self) -> None:
        self.factor = IntradayShapeFactor(weak_streak_rules={2: -1.0})

    def test_layers_are_reported_separately(self) -> None:
        bars = _interp_day(
            [("09:30", 10.0), ("15:00", 10.3)],
            open_price=10.0,
            preclose=10.0,
        )
        result = self.factor.calculate(
            bars, history=[DAY_TYPE_LOW_LOW, DAY_TYPE_HIGH_LOW]
        )
        self.assertTrue(result["intraday_ok"])
        self.assertTrue(result["sequence_ok"])
        self.assertEqual(result["intraday_adjust"], 0.0)
        self.assertAlmostEqual(result["sequence_adjust"], -1.0)
        # 合计分仍是 5 + 两层调整，供观测
        self.assertAlmostEqual(result["score"], 4.0)

    def test_intraday_unavailable_keeps_sequence(self) -> None:
        """早盘 9:40 当日 bar 不够，但昨天的形态已经定型。"""
        result = self.factor.calculate(
            [], history=[DAY_TYPE_LOW_LOW, DAY_TYPE_HIGH_LOW]
        )
        self.assertFalse(result["data_ok"])
        self.assertTrue(result["sequence_ok"])
        self.assertAlmostEqual(result["sequence_adjust"], -1.0)
        self.assertAlmostEqual(result["score"], 4.0)

    def test_no_history_means_sequence_off(self) -> None:
        bars = _interp_day(
            [("09:30", 10.0), ("15:00", 10.3)],
            open_price=10.0,
            preclose=10.0,
        )
        result = self.factor.calculate(bars)
        self.assertFalse(result["sequence_ok"])
        self.assertEqual(result["sequence_adjust"], 0.0)
        self.assertAlmostEqual(result["score"], 5.0)


class TrendFeatureTest(unittest.TestCase):
    """跨日趋势判据：收盘 vs 均线、最高价 vs 前日（纯计算）。

    序列口径：**升序**（最早在前），与 `ShapeHistory.daily_series` 的返回值
    一致。均线按可用K线的位置算，不按自然日对齐。
    """

    # 9 天序列（升序）。设计意图：
    #   d1~d4 均线算不出来（不足 5 根）；d5~d7 收盘低于均线；d8~d9 高于均线
    #   d1~d4 高点持平（不计）；d5 高点低于前日；d6 打平（不计）；d7~d9 高于前日
    CLOSES: list[float] = [10.0, 10.0, 10.0, 10.0, 9.0, 9.0, 9.0, 11.0, 12.0]
    HIGHS: list[float] = [10.0, 10.0, 10.0, 10.0, 9.5, 9.5, 9.6, 11.0, 12.0]

    def _series(self) -> list[dict]:
        return [
            {"date": f"2026-10-{index + 1:02d}", "close": close, "high": high}
            for index, (close, high) in enumerate(zip(self.CLOSES, self.HIGHS))
        ]

    def setUp(self) -> None:
        self.factor = IntradayShapeFactor()

    def test_counts_over_full_series(self) -> None:
        trend = self.factor.trend_features(self._series())
        self.assertEqual(trend["days"], 9)
        # 前 4 天算不出均线，不参与均线计数（不是判成「在均线上」）
        self.assertEqual(trend["below_ma"], 3)
        self.assertEqual(trend["above_ma"], 2)
        # 第 1 天没有前日高点，不参与高低点计数
        self.assertEqual(trend["lower_high"], 1)
        self.assertEqual(trend["higher_high"], 3)

    def test_window_limits_to_tail(self) -> None:
        trend = self.factor.trend_features(self._series(), window=5)
        self.assertEqual(trend["days"], 5)
        self.assertEqual(trend["below_ma"], 3)
        self.assertEqual(trend["above_ma"], 2)
        self.assertEqual(trend["lower_high"], 1)
        self.assertEqual(trend["higher_high"], 3)

    def test_window_two_days_only(self) -> None:
        trend = self.factor.trend_features(self._series(), window=2)
        self.assertEqual(trend["days"], 2)
        # 只评估最后两天（d8、d9）：均线之上、高点高于前日
        self.assertEqual(trend["below_ma"], 0)
        self.assertEqual(trend["above_ma"], 2)
        self.assertEqual(trend["lower_high"], 0)
        self.assertEqual(trend["higher_high"], 2)

    def test_equal_counts_as_neither(self) -> None:
        """收在均线上、高点打平不计入任何一侧：均衡不是偏强也不是偏弱。"""
        flat = [{"date": f"d{index}", "close": 10.0, "high": 10.0} for index in range(6)]
        trend = self.factor.trend_features(flat, window=5)
        self.assertEqual(trend["days"], 5)
        self.assertEqual(trend["below_ma"], 0)
        self.assertEqual(trend["above_ma"], 0)
        self.assertEqual(trend["lower_high"], 0)
        self.assertEqual(trend["higher_high"], 0)

    def test_insufficient_data_skips_ma_only(self) -> None:
        """不足一个均线周期时均线计数全跳过，高低点计数照算。"""
        short = [
            {"date": "d1", "close": 10.0, "high": 10.0},
            {"date": "d2", "close": 9.0, "high": 9.5},
            {"date": "d3", "close": 9.5, "high": 9.8},
        ]
        trend = self.factor.trend_features(short)
        self.assertEqual(trend["days"], 3)
        self.assertEqual(trend["below_ma"], 0)
        self.assertEqual(trend["above_ma"], 0)
        self.assertEqual(trend["lower_high"], 1)
        self.assertEqual(trend["higher_high"], 1)

    def test_bad_rows_are_skipped(self) -> None:
        series = self._series() + [
            {"date": "bad", "close": "x", "high": 1.0},
            {"date": "bad2", "high": 1.0},
            {"date": "bad3", "close": -1.0, "high": 1.0},
            "not-a-dict",
        ]
        trend = self.factor.trend_features(series)
        # 坏行整体跳过，不挤占位置
        self.assertEqual(trend["days"], 9)
        self.assertEqual(trend["below_ma"], 3)

    def test_empty_series_is_zero_days(self) -> None:
        for empty in ([], None, "nope"):
            with self.subTest(daily=empty):
                trend = self.factor.trend_features(empty)
                self.assertEqual(trend["days"], 0)
                self.assertEqual(trend["below_ma"], 0)
                self.assertEqual(trend["lower_high"], 0)

    def test_ma_period_changes_window(self) -> None:
        factor = IntradayShapeFactor(ma_period=3)
        trend = factor.trend_features(self._series(), window=5)
        # 窗口 = 最后 5 天。MA3：d5/d6 收盘低于均线，d7 收盘恰好等于均线（不计），
        # d8/d9 高于均线。与 MA5 的差别正来自「恰好相等」那天被排除的方式不同。
        self.assertEqual(trend["below_ma"], 2)
        self.assertEqual(trend["above_ma"], 2)
        self.assertEqual(trend["ma_period"], 3)


class TrendRuleTest(unittest.TestCase):
    """趋势规则表与跨日层的合并。"""

    def _bars(self) -> list[dict]:
        return _interp_day(
            [("09:30", 10.0), ("15:00", 10.3)],
            open_price=10.0,
            preclose=10.0,
        )

    def _series(self) -> list[dict]:
        # 末尾 5 天里 3 天收盘低于 5 日线、1 天高点低于前日
        closes = [10.0, 10.0, 10.0, 10.0, 9.0, 9.0, 9.0, 11.0, 12.0]
        highs = [10.0, 10.0, 10.0, 10.0, 9.5, 9.5, 9.6, 11.0, 12.0]
        return [
            {"date": f"2026-10-{index + 1:02d}", "close": close, "high": high}
            for index, (close, high) in enumerate(zip(closes, highs))
        ]

    def test_below_ma_rule_picks_largest_key(self) -> None:
        factor = IntradayShapeFactor(below_ma_rules={2: -1.0, 3: -2.0})
        result = factor.calculate(self._bars(), daily=self._series(), trend_window=5)
        self.assertTrue(result["trend_ok"])
        self.assertEqual(result["below_ma"], 3)
        self.assertAlmostEqual(result["trend_adjust"], -2.0)
        # 趋势分累加进跨日层，无历史标签时跨日层仅由趋势激活
        self.assertTrue(result["sequence_ok"])
        self.assertAlmostEqual(result["sequence_adjust"], -2.0)

    def test_strong_side_rule(self) -> None:
        factor = IntradayShapeFactor(higher_high_rules={3: 1.0})
        result = factor.calculate(self._bars(), daily=self._series(), trend_window=5)
        self.assertEqual(result["higher_high"], 3)
        self.assertAlmostEqual(result["trend_adjust"], 1.0)
        self.assertAlmostEqual(result["sequence_adjust"], 1.0)

    def test_labels_and_trend_accumulate(self) -> None:
        """两个子源各自命中，累加进同一个 sequence_adjust。"""
        factor = IntradayShapeFactor(
            weak_streak_rules={2: -1.0}, below_ma_rules={3: -2.0}
        )
        result = factor.calculate(
            self._bars(),
            history=[DAY_TYPE_LOW_LOW, DAY_TYPE_HIGH_LOW],
            daily=self._series(),
            trend_window=5,
        )
        self.assertEqual(result["weak_streak"], 2)
        self.assertAlmostEqual(result["sequence_adjust"], -3.0)
        self.assertAlmostEqual(result["trend_adjust"], -2.0)
        self.assertAlmostEqual(result["score"], 2.0)  # 5 - 1 - 2

    def test_trend_only_without_labels(self) -> None:
        """分钟归档缺数据（无标签）时日K 趋势层仍然生效。"""
        factor = IntradayShapeFactor(below_ma_rules={3: -2.0})
        result = factor.calculate([], daily=self._series(), trend_window=5)
        self.assertFalse(result["data_ok"])  # 当日层不可用
        self.assertTrue(result["sequence_ok"])  # 跨日层可用
        self.assertEqual(result["seq"], "")  # 没有标签
        self.assertEqual(result["below_ma"], 3)
        self.assertAlmostEqual(result["sequence_adjust"], -2.0)

    def test_no_daily_means_trend_off(self) -> None:
        factor = IntradayShapeFactor(below_ma_rules={3: -2.0})
        result = factor.calculate(self._bars())
        self.assertFalse(result["trend_ok"])
        self.assertEqual(result["below_ma"], 0)
        self.assertAlmostEqual(result["sequence_adjust"], 0.0)
        self.assertAlmostEqual(result["score"], 5.0)

    def test_empty_daily_list_means_trend_off(self) -> None:
        factor = IntradayShapeFactor(below_ma_rules={3: -2.0})
        result = factor.calculate(self._bars(), daily=[])
        self.assertFalse(result["trend_ok"])
        self.assertAlmostEqual(result["sequence_adjust"], 0.0)


class StrategyTwoLayerOffsetTest(unittest.TestCase):
    """两层偏移合并规则：取较大者，不叠加。"""

    def _strategy(self, **overrides):
        import logging

        from factor.intraday_shape_factor import IntradayShapeFactor as Factor
        from strategy.volume_expansion_strategy import VolumeExpansionStrategy

        strategy = object.__new__(VolumeExpansionStrategy)
        strategy.logger = logging.getLogger("test.shape.seq")
        strategy.SHAPE_ENABLED = True
        strategy.SHAPE_MAX_OFFSET = 1.0
        strategy.SHAPE_MIN_SEGMENTS = 2
        strategy.SHAPE_SEQ_ENABLED = True
        strategy.SHAPE_SEQ_MAX_OFFSET = 1.0
        strategy.SHAPE_SEQ_DAYS = 5
        strategy.SHAPE_MA_PERIOD = 5
        strategy.SHAPE_DIVE_SCORE = 0.0
        strategy.SHAPE_SEQ_RULES = {}
        strategy._shape_factor = Factor(rule_segments=2)
        for key, value in overrides.items():
            setattr(strategy, key, value)
        return strategy

    def test_takes_larger_layer_not_sum(self) -> None:
        strategy = self._strategy()
        detail = {
            "data_ok": True,
            "intraday_ok": True,
            "intraday_adjust": -2.0,  # 当日形态 3 分 -> +0.4
            "intraday_score": 3.0,
            "sequence_ok": True,
            "sequence_adjust": -4.0,  # 跨日 1 分 -> +0.8
            "seq": "低开低走|高开低走",
            "weak_streak": 2,
        }
        offset, text = strategy._shape_offset(detail)
        # 取较大者：0.8，而不是 0.4 + 0.8 = 1.2
        self.assertAlmostEqual(offset, 0.8)
        self.assertIn("连弱", text)

    def test_sequence_layer_off_by_default(self) -> None:
        strategy = self._strategy(SHAPE_SEQ_MAX_OFFSET=0.0)
        detail = {
            "intraday_ok": False,
            "sequence_ok": True,
            "sequence_adjust": -5.0,
            "seq": "低开低走",
        }
        self.assertEqual(strategy._shape_offset(detail), (0.0, ""))
        self.assertEqual(strategy._sequence_offset(detail), 0.0)

    def test_sequence_offset_positive_when_weak(self) -> None:
        strategy = self._strategy()
        detail = {"sequence_ok": True, "sequence_adjust": 2.0}
        # 形态分高于中性 = 形态好，收紧为负；当前结构下不产生额外买入
        self.assertAlmostEqual(strategy._sequence_offset(detail), -0.4)

    def test_build_factor_uses_dive_and_sequence_rules(self) -> None:
        strategy = self._strategy(
            SHAPE_DIVE_SCORE=-2.0,
            SHAPE_SEQ_RULES={"weak_streak": {2: -1.0}, "prev_day": {"低开低走": -0.5}},
        )
        factor = strategy._build_shape_factor()
        self.assertEqual(factor.dive_rules, {"深回落": -2.0})
        self.assertEqual(factor.weak_streak_rules, {2: -1.0})
        self.assertEqual(factor.prev_day_rules, {"低开低走": -0.5})

    def test_build_factor_ignores_bad_rule_entry(self) -> None:
        strategy = self._strategy(SHAPE_SEQ_RULES={"weak_streak": {"x": "bad"}})
        factor = strategy._build_shape_factor()
        self.assertEqual(factor.weak_streak_rules, {})

    def test_build_factor_uses_trend_rules(self) -> None:
        strategy = self._strategy(
            SHAPE_SEQ_RULES={
                "below_ma": {3: -1.0},
                "above_ma": {3: 1.0},
                "lower_high": {3: -0.5},
                "higher_high": {"bad": "x"},
            }
        )
        factor = strategy._build_shape_factor()
        self.assertEqual(factor.below_ma_rules, {3: -1.0})
        self.assertEqual(factor.above_ma_rules, {3: 1.0})
        self.assertEqual(factor.lower_high_rules, {3: -0.5})
        # 坏条目静默跳过，不拖垮整个因子构建
        self.assertEqual(factor.higher_high_rules, {})
        self.assertEqual(factor.ma_period, 5)

    def test_build_factor_passes_ma_period(self) -> None:
        strategy = self._strategy(SHAPE_MA_PERIOD=10)
        factor = strategy._build_shape_factor()
        self.assertEqual(factor.ma_period, 10)

    def test_offset_text_includes_trend_counts(self) -> None:
        strategy = self._strategy()
        detail = {
            "sequence_ok": True,
            "sequence_adjust": -4.0,
            "seq": "低开低走|高开低走",
            "weak_streak": 2,
            "below_ma": 4,
            "lower_high": 3,
            "ma_period": 5,
        }
        offset, text = strategy._shape_offset(detail)
        self.assertAlmostEqual(offset, 0.8)
        self.assertIn("低于5日线4次", text)
        self.assertIn("高点低于前日3次", text)


if __name__ == "__main__":
    unittest.main()
