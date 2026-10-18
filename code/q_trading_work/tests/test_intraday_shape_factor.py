"""日内形态因子与策略接入。

守的是三件容易静默出错的事：

1. **算不出来 != 中性**。数据不足、前 N 段没走完时 `data_ok=False`，
   不能被读成「形态中性所以放行」。
2. **覆盖度不足必须丢**。mongo 分钟数据按订阅累积，实测有 488 只股票
   当天 bar 数中位数只有 5 根，拿它编码「日内路径」是噪声。
3. **规则与统计口径一致**。规则键用前 N 段符号，这几段在 11:30 定型后
   不再变化；用 trend/close_pos 当键会在盘中每个时刻含义不同。
"""

import sys
import types
import unittest

# talib 是项目运行依赖（需要 C 库），本机环境可能装不起来。`strategy/__init__`
# 的导入链会带出它，而本测试不碰任何 talib 计算，缺它时注入空壳即可：
# 它在模块级只被 import，属性都在函数体内访问。
if "talib" not in sys.modules:
    try:
        import talib  # noqa: F401
    except ImportError:
        sys.modules["talib"] = types.ModuleType("talib")

from factor.intraday_shape_factor import (  # noqa: E402
    SEGMENTS,
    IntradayShapeFactor,
    to_hhmm,
)


def _bar(
    stamp: str,
    close: float,
    open_price: float = 10.0,
    preclose: float = 10.0,
    high: float | None = None,
    low: float | None = None,
) -> dict:
    return {
        "code": "000001.SZ",
        "minute_time": stamp,
        "open": open_price,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": close,
        "preclose": preclose,
        "volume": 1000,
        "amount": 10000.0,
    }


def _day_bars(
    closes_by_hhmm: dict[str, float],
    open_price: float = 10.0,
    preclose: float = 10.0,
) -> list[dict]:
    """按 HH:MM -> close 构造当日 bar，high/low 取当日累计值。"""
    rows: list[dict] = []
    running_high = open_price
    running_low = open_price
    for hhmm in sorted(closes_by_hhmm):
        close = closes_by_hhmm[hhmm]
        running_high = max(running_high, close)
        running_low = min(running_low, close)
        rows.append(
            _bar(
                f"2026-10-14 {hhmm}:00",
                close,
                open_price=open_price,
                preclose=preclose,
                high=running_high,
                low=running_low,
            )
        )
    return rows


def _flat_day(offset: float = 0.0) -> dict[str, float]:
    """全天缓慢上涨/下跌的一分钟 bar，覆盖 09:30~15:00。"""
    result: dict[str, float] = {}
    minute = 9 * 60 + 30
    step = 0
    while minute <= 15 * 60:
        hour, mins = divmod(minute, 60)
        if not (11 * 60 + 30 < minute < 13 * 60):
            result[f"{hour:02d}:{mins:02d}"] = 10.0 + offset * step / 100.0
            step += 1
        minute += 1
    return result


class ToHhmmTest(unittest.TestCase):
    def test_parses_common_formats(self) -> None:
        self.assertEqual(to_hhmm("2026-10-14 09:31:00"), 931)
        self.assertEqual(to_hhmm("2026-10-14 14:59"), 1459)
        self.assertEqual(to_hhmm("09:31"), 931)
        self.assertEqual(to_hhmm("13:00:00"), 1300)

    def test_returns_minus_one_on_garbage(self) -> None:
        for bad in ("", None, "abc", "2026-10-14", "99:99"):
            self.assertEqual(to_hhmm(bad), -1, msg=f"{bad!r} 应解析失败")


class EncodeGateTest(unittest.TestCase):
    """覆盖度与价格闸门。"""

    def setUp(self) -> None:
        self.factor = IntradayShapeFactor()

    def test_rejects_too_few_bars(self) -> None:
        bars = _day_bars({"09:31": 10.0, "09:32": 10.0, "09:33": 10.0})
        self.assertIsNone(self.factor.encode(bars))

    def test_rejects_late_first_bar(self) -> None:
        # 首根 11:00 才开始，开盘半小时完全没有，形状无意义
        bars = _day_bars({f"{13 + i // 60:02d}:{i % 60:02d}": 10.0 for i in range(30)})
        self.assertIsNone(self.factor.encode(bars))

    def test_rejects_early_last_bar_in_full_mode(self) -> None:
        bars = _day_bars({f"{9 + (30 + i) // 60:02d}:{(30 + i) % 60:02d}": 10.0 for i in range(40)})
        self.assertIsNone(self.factor.encode(bars))

    def test_nan_close_is_rejected_not_passed(self) -> None:
        """末根 close 为 NaN 时必须拒绝。

        旧实现用 `nan <= 0` 判断，结果为 False，于是放行 NaN 样本，
        与今天的 gap/trend/close_pos 全变成 NaN 并进入统计（2026-07-17 的 9 个样本）。
        """
        bars = _day_bars(_flat_day(offset=0.1))
        bars[-1]["close"] = float("nan")
        self.assertIsNone(self.factor.encode(bars))

    def test_nan_preclose_is_rejected(self) -> None:
        bars = _day_bars(_flat_day(offset=0.1))
        bars[0]["preclose"] = float("nan")
        self.assertIsNone(self.factor.encode(bars))


class EncodeFullModeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.factor = IntradayShapeFactor()

    def test_all_up_gives_four_plus(self) -> None:
        bars = _day_bars(_flat_day(offset=0.5))
        features = self.factor.encode(bars)
        self.assertIsNotNone(features)
        assert features is not None
        self.assertTrue(features["complete"])
        self.assertEqual(features["shape"], "++++")
        self.assertEqual(features["completed_segments"], len(SEGMENTS))
        self.assertEqual(features["partial_index"], -1)

    def test_all_down_gives_four_minus(self) -> None:
        bars = _day_bars(_flat_day(offset=-0.5))
        features = self.factor.encode(bars)
        assert features is not None
        self.assertEqual(features["shape"], "----")

    def test_flat_day_is_flat_symbol(self) -> None:
        bars = _day_bars(_flat_day(offset=0.0))
        features = self.factor.encode(bars)
        assert features is not None
        self.assertEqual(features["shape"], "0000")
        self.assertEqual(features["gap_b"], "平开")
        self.assertEqual(features["trend_b"], "走平")

    def test_one_price_limit_puts_close_pos_middle(self) -> None:
        """一字板 high == low，跨度为零，close_pos 取 0.5 落中部。"""
        bars = _day_bars(_flat_day(offset=0.0))
        features = self.factor.encode(bars)
        assert features is not None
        self.assertAlmostEqual(features["close_pos"], 0.5)
        self.assertEqual(features["cp_b"], "收在中部")

    def test_gap_uses_open_vs_preclose(self) -> None:
        bars = _day_bars(_flat_day(offset=0.0), open_price=10.2, preclose=10.0)
        features = self.factor.encode(bars)
        assert features is not None
        self.assertAlmostEqual(features["gap"], 0.02, places=6)
        self.assertEqual(features["gap_b"], "高开")


class EncodePrefixModeTest(unittest.TestCase):
    """前缀模式：盘中唯一可得的形态口径。"""

    def setUp(self) -> None:
        self.factor = IntradayShapeFactor()
        self.bars = _day_bars(_flat_day(offset=0.5))

    def test_prefix_marks_unknown_segments(self) -> None:
        features = self.factor.encode(self.bars, as_of="11:00")
        assert features is not None
        self.assertFalse(features["complete"])
        self.assertEqual(features["shape"], "")
        self.assertEqual(features["prefix_shape"], "++__")
        self.assertEqual(features["partial_index"], 1)

    def test_prefix_before_first_segment_ends(self) -> None:
        features = self.factor.encode(self.bars, as_of="10:20")
        assert features is not None
        self.assertEqual(features["partial_index"], 0)
        self.assertEqual(features["prefix_shape"], "+___")
        self.assertEqual(features["completed_segments"], 0)

    def test_prefix_at_afternoon(self) -> None:
        # 13:30 落在第三段（1300-1400）内，该段进行中，所以只算前两段完整
        features = self.factor.encode(self.bars, as_of="13:30")
        assert features is not None
        self.assertEqual(features["prefix_shape"], "+++_")
        self.assertEqual(features["completed_segments"], 2)
        self.assertEqual(features["partial_index"], 2)

    def test_require_segments_filters_unfinished(self) -> None:
        """前两段没走完时直接丢弃，不能拿半截数据比完整数据。"""
        self.assertIsNone(
            self.factor.encode(self.bars, as_of="10:20", require_segments=2)
        )
        features = self.factor.encode(self.bars, as_of="11:30", require_segments=2)
        assert features is not None
        self.assertEqual(features["completed_segments"], 2)

    def test_prefix_bars_count_only_counts_rows_kept(self) -> None:
        features = self.factor.encode(self.bars, as_of="10:30")
        assert features is not None
        self.assertLess(features["bars"], len(self.bars))


class ScoreTest(unittest.TestCase):
    def test_neutral_by_default(self) -> None:
        """默认规则表为空，分数恒为中枢。"""
        factor = IntradayShapeFactor()
        detail = factor.calculate(_day_bars(_flat_day(offset=0.5)))
        self.assertTrue(detail["data_ok"])
        self.assertEqual(detail["score"], factor.NEUTRAL_SCORE)

    def test_path_rule_hits_prefix_head(self) -> None:
        factor = IntradayShapeFactor(path_rules={"++": 1.5})
        detail = factor.calculate(_day_bars(_flat_day(offset=0.5)))
        self.assertEqual(detail["score"], 6.5)
        self.assertTrue(any("前2段" in item for item in detail["reason"]))

    def test_path_rule_not_applied_before_segments_complete(self) -> None:
        """10:20 时只有第一段在走，规则键拿不到，不能按 ++ 加分。"""
        factor = IntradayShapeFactor(path_rules={"++": 1.5})
        bars = _day_bars(_flat_day(offset=0.5))
        detail = factor.calculate(bars, as_of="10:20")
        self.assertFalse(detail["data_ok"])
        self.assertEqual(detail["score"], factor.NEUTRAL_SCORE)

    def test_structure_rule_default_off(self) -> None:
        """结构规则默认关闭：收盘口径的三元组不适合盘中。"""
        self.assertEqual(IntradayShapeFactor.DEFAULT_STRUCTURE_RULES, {})

    def test_structure_rule_can_be_enabled(self) -> None:
        factor = IntradayShapeFactor(
            structure_rules={("平开", "走强", "收在上沿"): -1.0}
        )
        detail = factor.calculate(_day_bars(_flat_day(offset=0.5)))
        self.assertEqual(detail["score"], 4.0)

    def test_score_is_clamped(self) -> None:
        factor = IntradayShapeFactor(path_rules={"00": -20.0})
        detail = factor.calculate(_day_bars(_flat_day(offset=0.0)))
        self.assertEqual(detail["score"], 0.0)

    def test_insufficient_data_is_not_neutral(self) -> None:
        """算不出来必须是 data_ok=False，不能靠分值表达。"""
        factor = IntradayShapeFactor()
        detail = factor.calculate([])
        self.assertFalse(detail["data_ok"])
        self.assertEqual(detail["score"], factor.NEUTRAL_SCORE)

    def test_dataframe_input_supported(self) -> None:
        import pandas as pd

        factor = IntradayShapeFactor()
        df = pd.DataFrame(_day_bars(_flat_day(offset=0.5)))
        detail = factor.calculate(df)
        self.assertTrue(detail["data_ok"])
        self.assertEqual(detail["prefix_shape"], "++++")


class StrategyShapeOffsetTest(unittest.TestCase):
    """形态分到买入门槛偏移的换算（正 = 更难买）。"""

    def _strategy(self, **overrides):
        import logging

        from strategy.volume_expansion_strategy import VolumeExpansionStrategy

        # 跳过 __init__ 那一串 Api / TradeManager 依赖，只测换算逻辑
        strategy = object.__new__(VolumeExpansionStrategy)
        strategy.logger = logging.getLogger("test.shape")
        strategy.SHAPE_ENABLED = True
        strategy.SHAPE_MAX_OFFSET = 1.0
        strategy.SHAPE_MIN_SEGMENTS = 2
        from factor.intraday_shape_factor import IntradayShapeFactor as Factor

        strategy._shape_factor = Factor(rule_segments=2)
        for key, value in overrides.items():
            setattr(strategy, key, value)
        return strategy

    def test_neutral_gives_zero_offset(self) -> None:
        strategy = self._strategy()
        offset, text = strategy._shape_offset({"data_ok": True, "score": 5.0})
        self.assertEqual(offset, 0.0)
        self.assertEqual(text, "")

    def test_bad_shape_raises_threshold(self) -> None:
        strategy = self._strategy()
        offset, text = strategy._shape_offset(
            {"data_ok": True, "score": 3.0, "prefix_shape": "+-__"}
        )
        self.assertAlmostEqual(offset, 0.4)
        self.assertIn("+-", text)

    def test_worst_shape_hits_max_offset(self) -> None:
        strategy = self._strategy()
        offset, _ = strategy._shape_offset({"data_ok": True, "score": 0.0})
        self.assertAlmostEqual(offset, 1.0)

    def test_good_shape_lowers_threshold(self) -> None:
        strategy = self._strategy()
        offset, _ = strategy._shape_offset({"data_ok": True, "score": 7.5})
        self.assertAlmostEqual(offset, -0.5)

    def test_unavailable_is_fail_open(self) -> None:
        """形态算不出来时不改变门槛，与 buy_threshold_offset 的缺省语义一致。"""
        strategy = self._strategy()
        offset, text = strategy._shape_offset({"data_ok": False, "score": 0.0})
        self.assertEqual(offset, 0.0)
        self.assertEqual(text, "")

    def test_disabled_max_offset_short_circuits(self) -> None:
        strategy = self._strategy(SHAPE_MAX_OFFSET=0.0)
        offset, _ = strategy._shape_offset({"data_ok": True, "score": 0.0})
        self.assertEqual(offset, 0.0)

    def test_shape_detail_disabled_returns_not_ok(self) -> None:
        strategy = self._strategy(SHAPE_ENABLED=False)
        detail = strategy._shape_detail(_day_bars(_flat_day(offset=0.5)))
        self.assertFalse(detail["data_ok"])

    def test_shape_detail_swallows_exception(self) -> None:
        strategy = self._strategy()

        class Boom:
            NEUTRAL_SCORE = 5.0

            def calculate(self, bars, history=None):
                raise RuntimeError("boom")

        strategy._shape_factor = Boom()
        detail = strategy._shape_detail([])
        self.assertFalse(detail["data_ok"])
        self.assertEqual(detail["score"], 5.0)


if __name__ == "__main__":
    unittest.main()
