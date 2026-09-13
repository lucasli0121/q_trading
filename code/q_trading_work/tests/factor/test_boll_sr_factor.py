from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from factor.boll_support_resistance import BollSRFactor


class TestBollSRFactor(unittest.TestCase):
    """BollSRFactor 因子测试（合成数据，无外部依赖）。"""

    def _make_df(self, n: int = 60, seed: int = 0) -> pd.DataFrame:
        """构造一段有波动的价格序列（close 列）。"""
        rng = np.random.default_rng(seed)
        base = np.linspace(10.0, 12.0, n)
        noise = rng.normal(0.0, 0.3, n)
        return pd.DataFrame({"close": base + noise})

    def test_calculate_returns_levels(self) -> None:
        factor = BollSRFactor(timeperiod=15)
        result = factor.calculate(self._make_df())

        self.assertFalse(result.empty)
        for col in ["pivot", "r1", "r2", "r3", "s1", "s2", "s3"]:
            self.assertIn(col, result.columns)

        last = result.iloc[-1]
        # 阻力位应高于中轨，支撑位应低于中轨，且外推层级递增
        self.assertGreater(last["r1"], last["pivot"])
        self.assertGreater(last["r2"], last["r1"])
        self.assertGreater(last["r3"], last["r2"])
        self.assertLess(last["s1"], last["pivot"])
        self.assertLess(last["s2"], last["s1"])
        self.assertLess(last["s3"], last["s2"])

    def test_calculate_insufficient_data(self) -> None:
        factor = BollSRFactor(timeperiod=15)
        self.assertTrue(factor.calculate(self._make_df(n=10)).empty)

    def test_score_is_float_and_bounded(self) -> None:
        factor = BollSRFactor(timeperiod=15, tolerance=0.02)
        df = self._make_df()
        score = factor.score(df)
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, -10.0)
        self.assertLessEqual(score, 10.0)


if __name__ == "__main__":
    unittest.main()
