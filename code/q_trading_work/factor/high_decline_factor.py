# factor/drawdown_factor.py
from __future__ import annotations
from typing import Any, Union
import numpy as np
import pandas as pd
from factor.base_factor import BaseFactor
from factor.factor_utils import FactorUtils


class HighDeclineFactor(BaseFactor):
    """最近 N 日回撤因子。

    计算最近 N周期内最高点到当前收盘价的跌幅百分比。
    结果为负值表示下跌，负数越小跌幅越大。
    """

    factor_name: str = "HighDeclineFactor"

    def __init__(self, days: int = 5) -> None:
        """初始化回撤因子。

        :param days: 回看天数
        """
        self.days: int = days

    def calculate(self, stocks: pd.DataFrame) -> Any:
        """计算 N 日内最高点到当前收盘的回撤百分比。

        :param stocks: 结构化 np.ndarray，含 high/close 字段
        :return: 回撤百分比（负值=下跌，正值=当前超过区间最高点）
        """
        if len(stocks) < self.days:
            return 0.0  # 数据不足

        high_arr: np.ndarray = FactorUtils.high_array(stocks)
        close_arr: np.ndarray = FactorUtils.close_array(stocks)

        # 最近 self.days 的最高价
        recent_high: float = float(np.max(high_arr[-self.days:]))
        latest_close: float = float(close_arr[-1])

        if recent_high == 0:
            return 0.0

        drawdown_pct: float = (latest_close - recent_high) / recent_high * 100
        return round(drawdown_pct, 2)

    def score(self, stock_array: pd.DataFrame) -> float:
        return self.calculate(stock_array)