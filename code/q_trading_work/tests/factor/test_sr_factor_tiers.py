"""支撑/阻力因子的档位契约测试（合成数据，无外部依赖）。

覆盖 2026-09-28 检视报告里的两条修复：

- R5 `KlineSRFactor.score`：档位随支撑/阻力档位个数变化，±7.5 只在前者 >= 3 档时
  出现；阻力侧按「越过几档」与支撑侧对称（取 `r[-2]` 而非 `r[1]`）。
- R6 `BollSRFactor`：三档轨道改按 1.0σ/1.5σ/2.0σ 定义，n>=5 时全部可达；
  零波动窗口（std=0）不再恒判 −10。

以及 2026-09-29 的第三条：

- R14 `InDaySRFactor`：档位由经典枢轴点（`r1=2P−L`、`r2=P+D`、`r3=P+2D`）
  改为按昨日振幅比例 `pivot ± m·D`（m = 0.33 / 0.66 / 1.00）。
  原口径下 ±10 档实测只有 0.7%/1.3% 的命中率、0 分档占 64.3%，
  与布林带因子等权平均后本因子的实际影响接近于零。

原实现里这三条都是「代码写着的档位永远取不到 / 永远取到」，靠静态阅读看不出来，
所以固化成断言：档位表一旦被改动而变得不可达，这里会直接失败。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from factor.boll_support_resistance import BollSRFactor
from factor.in_day_support_resistance import InDaySRFactor
from factor.kline_support_resistance import KlineSRFactor


def _price_df(price: float) -> pd.DataFrame:
    """构造一行行情表（`score()` 只取最后一行的 close）。"""
    return pd.DataFrame({"close": [price]})


def _boll_record(**overrides: float) -> pd.DataFrame:
    """基准轨道：中轨 10.0、σ=1.0，即 r1/r2/r3 = 11.0/11.5/12.0，s1/s2/s3 = 9.0/8.5/8.0。"""
    record: dict[str, float] = {
        "pivot": 10.0,
        "sigma": 1.0,
        "r1": 11.0,
        "r2": 11.5,
        "r3": 12.0,
        "s1": 9.0,
        "s2": 8.5,
        "s3": 8.0,
    }
    record.update(overrides)
    return pd.DataFrame([record])


class TestKlineSRFactorTiers(unittest.TestCase):
    """R5：档位个数不同时可达的分值集合不同。"""

    # 4 档价位（升序），用于验证「档位 > 3」时的对称性
    SUPPORT_4: list[float] = [8.65, 9.25, 9.85, 10.45]
    RESISTANCE_4: list[float] = [11.0, 11.6, 12.2, 12.8]

    def _score(self, price: float, support: list[float], resistance: list[float]) -> float:
        levels: dict[str, list[float]] = {"support": list(support), "resistance": list(resistance)}
        with patch.object(KlineSRFactor, "calculate", return_value=levels):
            return KlineSRFactor(days=5).score(_price_df(price))

    def test_single_level_has_no_middle_tiers(self) -> None:
        # 只有 1 档：只可能出现 ±10 与 0
        self.assertEqual(self._score(8.0, [9.0], []), 10.0)
        self.assertEqual(self._score(9.5, [9.0], []), 0.0)
        self.assertEqual(self._score(13.0, [], [12.0]), -10.0)
        self.assertEqual(self._score(11.5, [], [12.0]), 0.0)

    def test_two_levels_have_five_but_not_seven_five(self) -> None:
        support: list[float] = [9.0, 10.0]
        self.assertEqual(self._score(8.5, support, []), 10.0)
        self.assertEqual(self._score(9.5, support, []), 5.0)  # 只低于最高一档
        self.assertEqual(self._score(10.5, support, []), 0.0)
        resistance: list[float] = [11.0, 12.0]
        self.assertEqual(self._score(12.5, [], resistance), -10.0)
        self.assertEqual(self._score(11.5, [], resistance), -5.0)
        self.assertEqual(self._score(10.5, [], resistance), 0.0)

    def test_three_levels_reach_seven_five(self) -> None:
        # 原实现里 ±7.5 的 elif 分支被 ±5.0 包含，恒不可达
        support: list[float] = [9.0, 10.0, 11.0]
        self.assertEqual(self._score(8.5, support, []), 10.0)
        self.assertEqual(self._score(9.5, support, []), 7.5)
        self.assertEqual(self._score(10.5, support, []), 5.0)
        resistance: list[float] = [11.0, 12.0, 13.0]
        self.assertEqual(self._score(13.5, [], resistance), -10.0)
        self.assertEqual(self._score(12.5, [], resistance), -7.5)
        self.assertEqual(self._score(11.5, [], resistance), -5.0)

    def test_four_levels_are_symmetric_between_sides(self) -> None:
        # 支撑侧：跌破全部 → 10、只差最高一档 → 7.5、只低于最高一档 → 5
        support: list[float] = self.SUPPORT_4
        self.assertEqual(self._score(8.0, support, []), 10.0)
        self.assertEqual(self._score(9.0, support, []), 7.5)
        self.assertEqual(self._score(10.0, support, []), 5.0)
        # 阻力侧对称：越过全部 → −10、只差最高一档 → −7.5、越过两档仍只是 −5
        resistance: list[float] = self.RESISTANCE_4
        self.assertEqual(self._score(12.9, [], resistance), -10.0)
        self.assertEqual(self._score(12.3, [], resistance), -7.5)
        self.assertEqual(self._score(11.7, [], resistance), -5.0)
        self.assertEqual(self._score(11.3, [], resistance), -5.0)

    def test_abnormal_price_returns_zero(self) -> None:
        # price <= 0 时 `price <= s[0]` 会假成立，必须挡在打分之前
        self.assertEqual(self._score(0.0, [9.0, 10.0], []), 0.0)
        self.assertEqual(self._score(-1.0, [9.0, 10.0], []), 0.0)


class TestBollSRFactorTiers(unittest.TestCase):
    """R6：三档轨道可达，零波动窗口不再恒判 −10。"""

    def _score(self, price: float, **overrides: float) -> float:
        with patch.object(BollSRFactor, "calculate", return_value=_boll_record(**overrides)):
            return BollSRFactor(timeperiod=5).score(_price_df(price))

    def test_all_tiers_reachable(self) -> None:
        self.assertEqual(self._score(12.5), -10.0)
        self.assertEqual(self._score(12.0), -10.0)
        self.assertEqual(self._score(11.5), -7.5)
        self.assertEqual(self._score(11.0), -5.0)
        self.assertEqual(self._score(10.5), 0.0)
        self.assertEqual(self._score(9.5), 0.0)
        self.assertEqual(self._score(9.0), 5.0)
        self.assertEqual(self._score(8.5), 7.5)
        self.assertEqual(self._score(8.0), 10.0)

    def test_zero_volatility_returns_zero(self) -> None:
        # 一字板：σ=0 时三轨与中轨重合，原实现下 `price >= r3` 恒成立 → 恒 −10
        self.assertEqual(
            self._score(
                10.0, sigma=0.0, r1=10.0, r2=10.0, r3=10.0, s1=10.0, s2=10.0, s3=10.0
            ),
            0.0,
        )

    def test_abnormal_price_returns_zero(self) -> None:
        self.assertEqual(self._score(0.0), 0.0)
        self.assertEqual(self._score(-1.0), 0.0)

    def test_bands_are_sigma_multiples(self) -> None:
        # 轨道定义：r_k / s_k = 中轨 ± k·σ（k = 1.0 / 1.5 / 2.0），n=5 时 2σ 恰好可达
        factor = BollSRFactor(timeperiod=15)
        close: list[float] = [10.0 + (0.1 if i % 2 else -0.1) for i in range(20)]
        result = factor.calculate(pd.DataFrame({"close": close}))
        last = result.iloc[-1]
        self.assertGreater(last["sigma"], 0.0)
        self.assertAlmostEqual(last["r1"] - last["pivot"], last["sigma"], places=6)
        self.assertAlmostEqual(last["r2"] - last["pivot"], 1.5 * last["sigma"], places=6)
        self.assertAlmostEqual(last["r3"] - last["pivot"], 2.0 * last["sigma"], places=6)
        self.assertAlmostEqual(last["pivot"] - last["s3"], 2.0 * last["sigma"], places=6)


class TestInDaySRFactorTiers(unittest.TestCase):
    """R14：枢轴点档位按昨日振幅比例定义，三档可达且无「恒 0 / 恒 −10」形态。

    基准构造：H=11 / L=8 / C=11（收在最高），pivot=(11+8+11)/3=10、D=3。
    档位 = 10 ± m·3，即 r1/r2/r3 = 10.99/11.98/13.00，s1/s2/s3 = 9.01/8.02/7.00。
    """

    def _sr(self, high: float = 11.0, low: float = 8.0, close: float = 11.0) -> pd.DataFrame:
        return InDaySRFactor().pivot_points(
            pd.DataFrame([{"high": high, "low": low, "close": close}])
        )

    def _score(self, price: float, sr_df: pd.DataFrame) -> float:
        return InDaySRFactor().score_with_sr(sr_df, pd.DataFrame([{"price": price}]))

    def test_tiers_follow_amplitude_ratios(self) -> None:
        """档位 = pivot ± m·(high − low)，且阻力/支撑用同一套倍数（左右对称）。"""
        sr = self._sr()
        row = sr.iloc[-1]
        self.assertAlmostEqual(row["pivot"], 10.0, places=6)
        for m, key in (
            (InDaySRFactor.R1_RATIO, "r1"),
            (InDaySRFactor.R2_RATIO, "r2"),
            (InDaySRFactor.R3_RATIO, "r3"),
        ):
            self.assertAlmostEqual(row[key], 10.0 + m * 3.0, places=6)
        for m, key in (
            (InDaySRFactor.R1_RATIO, "s1"),
            (InDaySRFactor.R2_RATIO, "s2"),
            (InDaySRFactor.R3_RATIO, "s3"),
        ):
            self.assertAlmostEqual(row[key], 10.0 - m * 3.0, places=6)

    def test_all_tiers_reachable(self) -> None:
        """逐档扫一遍：三档正负两侧都必须能被某个价格命中。

        原实现下 `r3 = P + 2D` 需要偏离枢轴点 6%（实测中位），命中率仅 1.3%。
        """
        sr = self._sr()
        self.assertEqual(self._score(13.00, sr), -10.0)
        self.assertEqual(self._score(11.98, sr), -7.5)
        self.assertEqual(self._score(10.99, sr), -5.0)
        self.assertEqual(self._score(10.50, sr), 0.0)
        self.assertEqual(self._score(9.50, sr), 0.0)
        self.assertEqual(self._score(9.01, sr), 5.0)
        self.assertEqual(self._score(8.02, sr), 7.5)
        self.assertEqual(self._score(7.00, sr), 10.0)

    def test_tiers_reachable_inside_prev_range(self) -> None:
        """档位必须能在「价格不突破昨日区间」时触及，否则退化成破位档。

        价格落在 [L, H] 内时 |price − pivot| <= 2D/3；本例收在最高，
        故 H = pivot + D/3、L = pivot − 2D/3。向上只差 D/3，
        R1_RATIO = 0.33 <= 1/3 ⇒ 昨日最高价即触及阻力一档；向下的 2D/3
        落在第二档（0.66D）与第三档（1.0D）之间 ⇒ 命中第二档。
        """
        sr = self._sr()
        self.assertEqual(self._score(11.0, sr), -5.0)  # 昨日最高价 → 阻力一档
        self.assertEqual(self._score(8.0, sr), 7.5)  # 昨日最低价 → 支撑二档

    def test_ratio_contract_keeps_tiers_reachable(self) -> None:
        """倍数契约：0 < R1 < R2 < R3，且 R1 <= 2/3。

        R1 一旦超过 2/3，第一档就会要求价格突破昨日高低点 —— 实测
        0.40/0.80/1.20 组仍有 52.4% 的采样点落在 0 分档（0.33/0.66/1.00 组为 45.4%）。
        """
        self.assertGreater(InDaySRFactor.R1_RATIO, 0.0)
        self.assertLess(InDaySRFactor.R1_RATIO, InDaySRFactor.R2_RATIO)
        self.assertLess(InDaySRFactor.R2_RATIO, InDaySRFactor.R3_RATIO)
        self.assertLessEqual(InDaySRFactor.R1_RATIO, 2.0 / 3.0)

    def test_zero_range_returns_zero(self) -> None:
        """一字板 / 停牌无成交（H=L）：D=0 时三档与枢轴点重合，不得恒判 −10。

        原实现下 `price >= r3`（等价于 price >= pivot）恒成立，
        涨停一字板会被判成「极度超买」而给出卖点。
        """
        sr = self._sr(high=10.0, low=10.0, close=10.0)
        self.assertEqual(self._score(10.0, sr), 0.0)
        self.assertEqual(self._score(9.5, sr), 0.0)

    def test_abnormal_price_returns_zero(self) -> None:
        sr = self._sr()
        self.assertEqual(self._score(0.0, sr), 0.0)
        self.assertEqual(self._score(-1.0, sr), 0.0)

    def test_empty_inputs_return_zero(self) -> None:
        """空表（日线取不到）不得抛 IndexError —— 那会让整只股票在该策略下永不产信号。"""
        factor = InDaySRFactor()
        self.assertEqual(factor.score_with_sr(pd.DataFrame(), pd.DataFrame([{"price": 10.0}])), 0.0)
        self.assertEqual(factor.score_with_sr(self._sr(), pd.DataFrame()), 0.0)


if __name__ == "__main__":
    unittest.main()
