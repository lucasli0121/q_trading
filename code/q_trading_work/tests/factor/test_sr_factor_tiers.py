"""支撑/阻力因子的档位契约测试（合成数据，无外部依赖）。

覆盖 2026-09-28 检视报告里的两条修复：

- R5 `KlineSRFactor.score`：档位随支撑/阻力档位个数变化，±7.5 只在前者 >= 3 档时
  出现；阻力侧按「越过几档」与支撑侧对称（取 `r[-2]` 而非 `r[1]`）。
- R6 `BollSRFactor`：三档轨道改按 1.0σ/1.5σ/2.0σ 定义，n>=5 时全部可达；
  零波动窗口（std=0）不再恒判 −10。

原实现里这两条都是「代码写着的档位永远取不到」，靠静态阅读看不出来，所以固化成
断言：档位表一旦被改动而变得不可达，这里会直接失败。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from factor.boll_support_resistance import BollSRFactor
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


if __name__ == "__main__":
    unittest.main()
