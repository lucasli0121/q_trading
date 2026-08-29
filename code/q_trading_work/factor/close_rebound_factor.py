"""
Author: liguoqiang
Date: 2026-07-19
Description: 收盘价反弹因子 — 计算N日内最低收盘点到最新收盘价的涨幅百分比
"""
from __future__ import annotations

from typing import Any, Union

import numpy as np
import pandas as pd

from factor.base_factor import BaseFactor
from factor.factor_utils import FactorUtils


class CloseReboundFactor(BaseFactor):
    """N日内最低收盘价到最新收盘价的反弹因子。

    计算 (latest_close - min_close) / min_close * 100。
    正值表示反弹上涨，负值表示继续下跌。
    """

    factor_name: str = "CloseReboundFactor"

    def __init__(self, days: int = 15) -> None:
        """初始化收盘价反弹因子。

        :param days: 回看天数
        """
        self.days: int = days

    def calculate(self, stocks: pd.DataFrame) -> Any:
        """计算N日内最低收盘点到最新收盘价的涨幅百分比。

        :param stocks: 结构化 np.ndarray 或 pd.DataFrame，含 close 字段
        :return: 涨幅百分比
        """
        if len(stocks) < self.days:
            return 0.0

        close_arr: np.ndarray = FactorUtils.close_array(stocks)

        min_close: float = float(np.min(close_arr[-self.days:]))
        latest_close: float = float(close_arr[-1])

        if min_close == 0:
            return 0.0
        rebound_pct: float = (latest_close - min_close) / min_close * 100
        return round(rebound_pct, 2)

    def score(self, stock_array: pd.DataFrame) -> float:
        result = self.calculate(stock_array)
        return result