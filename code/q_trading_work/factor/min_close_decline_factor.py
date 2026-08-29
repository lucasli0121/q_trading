"""
Author: liguoqiang
Date: 2026-07-19
Description: 跌幅因子 — 计算N日内最高点到最低收盘点的跌幅百分比
"""
from __future__ import annotations

from typing import Any, Union

import numpy as np
import pandas as pd

from factor.base_factor import BaseFactor
from factor.factor_utils import FactorUtils


class MinCloseDeclineFactor(BaseFactor):
    """N日内最高点到最低收盘点的跌幅因子。

    计算 (min_close - max_high) / max_high * 100。
    负数越大表示区间内回撤越深，负值表示下跌。
    """

    factor_name: str = "MinCloseDeclineFactor"

    def __init__(self, days: int = 15) -> None:
        """初始化跌幅因子。

        :param days: 回看天数
        """
        self.days: int = days

    def calculate(self, stocks: pd.DataFrame) -> Any:
        """计算N日内最高点到最低收盘点的跌幅百分比。

        要求最高价出现的日期在最低收盘价日期之前，
        即先见顶后见底，否则返回 0.0。

        :param stocks: 结构化 np.ndarray 或 pd.DataFrame，含 high/close 字段
        :return: 跌幅百分比（非负值）
        """
        if len(stocks) < self.days:
            return 0.0

        high_arr: np.ndarray = FactorUtils.high_array(stocks)
        close_arr: np.ndarray = FactorUtils.close_array(stocks)

        window_high: np.ndarray = high_arr[-self.days:]
        window_close: np.ndarray = close_arr[-self.days:]

        max_high_idx: int = int(np.argmax(window_high))
        min_close_idx: int = int(np.argmin(window_close))

        # 最高价必须出现在最低收盘价之前
        if max_high_idx >= min_close_idx:
            return 0.0

        max_high: float = float(window_high[max_high_idx])
        min_close: float = float(window_close[min_close_idx])

        if max_high == 0:
            return 0.0

        decline_pct: float = (min_close - max_high) / max_high * 100
        return round(decline_pct, 2)

    def score(self, stock_array: pd.DataFrame) -> float:
        return self.calculate(stock_array)