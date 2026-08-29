from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

# 增加系统路径变量
curPath = os.getcwd()
sys.path.append(curPath)

from factor.rebound_factor import (
    ReboundFactor
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

if __name__ == "__main__":
    unittest.main()