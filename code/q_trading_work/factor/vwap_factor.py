# factor/ma_factor.py
from __future__ import annotations
from typing import Any, Union
import numpy as np
import pandas as pd
from factor.base_factor import BaseFactor
from factor.factor_utils import FactorUtils

class VwapFactor(BaseFactor):
    """
    成交量加权平均价（Volume Weighted Average Price）
    能反应真实成交成本的均价,用于计算N周期内均价
    prices: 成交价格数组
    volumes: 对应成交量数组
    """
    factor_name: str = "VwapFactor"

    def __init__(self, days: int = 5) -> None:
        self.days: int = days

    def calculate(self, stocks: pd.DataFrame) -> Any:
        """
        df 需包含: price, volume
        """
        df = pd.DataFrame(stocks)
        if df.empty or "price" not in df.columns or "volume" not in df.columns:
            return 0.0
        volume_sum: float = float(df["volume"].sum())
        if volume_sum == 0:
            return 0.0  # 成交量异常/为零，避免除零
        result = (df["price"] * df["volume"]).sum() / volume_sum

        return round(result, 2)

    def score(self, stock_array: pd.DataFrame) -> float:
        avg_price = self.calculate(stock_array)
        if avg_price == 0.0:
            return 0.0
        price_arr = FactorUtils.price_array(stock_array)
        current_price = price_arr[-1]
        score = (current_price - avg_price) / avg_price * 100
        return score