from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

# 增加系统路径变量
curPath = os.getcwd()
sys.path.append(curPath)

from factor.low_rebound_factor import (
    LowReboundFactor
)
from factor.ma_factor import (
    MaFactor
)


class TestFactor(unittest.TestCase):
    """
    因子测试
    """

    def setUp(self) -> None:
        """
        初始化测试数据
        """

        self.df: pd.DataFrame = pd.DataFrame(
            {
                "datetime": [
                    "2025-01-01",
                    "2025-01-02",
                    "2025-01-03",
                    "2025-01-04",
                    "2025-01-05",
                    "2025-01-06"
                ],
                "open": [
                    10,
                    10.5,
                    11,
                    11.5,
                    12,
                    13
                ],
                "high": [
                    10.6,
                    11,
                    11.6,
                    12,
                    13,
                    14
                ],
                "low": [
                    9.8,
                    10.2,
                    10.8,
                    11,
                    11.5,
                    12
                ],
                "close": [
                    10.5,
                    11,
                    11.5,
                    12,
                    13,
                    14
                ],
                "volume": [
                    1000,
                    1200,
                    1300,
                    1500,
                    1600,
                    1800
                ]
            }
        )
    def test_ma_factor(self) -> None:
        """
        测试 MA 因子
        """

        factor = MaFactor(days=5)

        result: float = factor.calculate(self.df)

        self.assertTrue(result > 0)

    def test_low_rebound_factor(self) -> None:
        """反弹因子：单调上涨的序列应给出正分，且与入参 days 一致。

        原用例引用的 `factor.rebound_factor.ReboundFactor` 已重命名为
        `factor.low_rebound_factor.LowReboundFactor`，此处同步更新。
        """
        factor = LowReboundFactor(days=5)
        self.assertEqual(factor.days, 5)

        score: float = factor.score(self.df)
        self.assertIsInstance(score, float)
        # 序列从 10.5 涨到 14，收盘价明显高于区间最低点 → 正分
        self.assertGreater(score, 0.0)

    def test_low_rebound_factor_insufficient_data(self) -> None:
        """数据不足时应安全返回，不抛异常。"""
        factor = LowReboundFactor(days=5)
        result = factor.calculate(self.df.head(2))
        self.assertIsInstance(result, float)

if __name__ == "__main__":
    unittest.main()