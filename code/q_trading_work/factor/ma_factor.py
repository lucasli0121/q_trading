# factor/ma_factor.py
from __future__ import annotations
from typing import Any, Union
import numpy as np
import pandas as pd
import talib
from factor.base_factor import BaseFactor
from factor.factor_utils import FactorUtils

class MaFactor(BaseFactor):
    """
    移动平均线因子
    """
    factor_name: str = "MaFactor"

    def __init__(self, days: int = 5) -> None:
        self.days: int = days

    def calculate(self, stocks: pd.DataFrame) -> Any:
        """计算均线因子。

        :param stocks: 结构化 np.ndarray，含 close 字段
        :return: 最近一日 MA 值
        """
        if len(stocks) < self.days:
            return 0.0

        close_arr: np.ndarray = FactorUtils.close_array(stocks)
        ma_arr: np.ndarray = talib.MA(close_arr, timeperiod=self.days)
        latest: float = float(ma_arr[-1])

        if np.isnan(latest):
            return 0.0

        return round(latest, 2)
    
    def score(self, stock_array: pd.DataFrame) -> float:
        if len(stock_array) < 20:
            return 0.0
        score: float = 0.0
        close_arr: np.ndarray = FactorUtils.close_array(stock_array)
        latest_close: float = float(close_arr[-1])
        ma_5: float = float(talib.SMA(close_arr, timeperiod=5)[-1])
        ma_10: float = float(talib.SMA(close_arr, timeperiod=10)[-1])
        ma_20: float = float(talib.SMA(close_arr, timeperiod=20)[-1])
        if ma_5 > ma_10 > ma_20:
            score += 2
        elif ma_5 < ma_10 < ma_20:
            score -= 2
        elif ma_5 > ma_10 or ma_5 > ma_20:
            score += 1
        else:
            score -= 1
        if latest_close > ma_5:
            score += 1
        else:
            score -= 1
        return score / 4 * 10