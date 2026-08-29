# factor/pct_change_factor.py
from __future__ import annotations
from typing import Any, Union
import numpy as np
import pandas as pd
import talib
from factor.base_factor import BaseFactor
from factor.factor_utils import FactorUtils

class ROCFactor(BaseFactor):
    """
    变动率指标因子，计算最近 self.days 的涨幅百分比
    采用 ta-lib ROC 指标，适配 DataFrame 输入
    可用于检测顶背离，拐头向下 返回的值越小越不能买
    默认周期:5，短周期
    """
    factor_name: str = "ROCFactor"

    def __init__(self, days: int = 5) -> None:
        self.days: int = days

    def calculate(self, stocks: pd.DataFrame) -> Any:
        """计算涨幅因子。

        :param stocks: 结构化 np.ndarray，含 close 字段
        :return: 最近交易日涨幅百分比
        """
        if len(stocks) < self.days + 1:
            return 0.0  # 数据不足

        close_arr: np.ndarray = FactorUtils.close_array(stocks)

        # 使用 ta-lib ROC 计算涨幅
        roc: np.ndarray = talib.ROC(close_arr, timeperiod=self.days)
        latest: float = float(roc[-1])

        # NaN 检查
        if np.isnan(latest):
            return 0.0

        return round(latest, 2)
    
    def score(self, stock_array: pd.DataFrame) -> float:
        if len(stock_array) < self.days + 1:
            return 0.0  # 数据不足

        close_arr: np.ndarray = FactorUtils.close_array(stock_array)

        # 使用 ta-lib ROC 计算涨幅
        roc: np.ndarray = talib.ROC(close_arr, timeperiod=self.days)
        if len(roc) > 0:
            latest_roc: float = float(roc[-1])
            return latest_roc
        return 0.0
        