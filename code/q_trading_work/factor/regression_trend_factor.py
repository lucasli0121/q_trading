# factor/rebound_factor.py
from __future__ import annotations
from typing import Any, cast
import numpy as np
import pandas as pd
from factor.base_factor import BaseFactor
from factor.factor_utils import FactorUtils
from scipy import stats

class RegressionTrendFactor(BaseFactor):
    """
    线性回归斜率趋势因子
    斜率 > 0 且 R² 高 = 趋势可靠

    适用场景: 趋势跟踪
    period: 周期或者天数

    """
    factor_name: str = "RegressionTrendFactor"

    def __init__(self, days: int = 15) -> None:
        self.days: int = days

    def regress_trend(self, stock_data: pd.DataFrame) -> dict[str, str]:
        """
        线性回归趋势判断

        :param stock_data: 结构化 pd.DataFrame，含 price 或 close 字段
        :return: 趋势方向与强度，格式 {"trend": "up"/"down", "level": "strong"/"moderate"/"weak/sideways"}
        """
        prices_arr: np.ndarray
        if "price" in stock_data:
            prices_arr = FactorUtils.price_array(stock_data)
        elif "close" in stock_data:
            prices_arr = FactorUtils.close_array(stock_data)
        x = np.arange(self.days)
        slope, _, r_value, _, _ = cast(
            tuple[float, float, float, float, float],
            stats.linregress(x, prices_arr[-self.days:]),
        )

        if slope > 0 and r_value**2 > 0.7:
            return {"trend": "up", "level": "strong"}
        elif slope < 0 and r_value**2 > 0.7:
            return {"trend": "down", "level": "strong"}
        return {"trend": "up" if slope >= 0 else "down", "level": "weak"}

    def calculate(self, stock_array: pd.DataFrame) -> Any:
        if len(stock_array) < self.days:
            return 0.0
        result = self.regress_trend(stock_array)
        return result

    def score(self, stock_array: pd.DataFrame) -> float:
        """
        根据calculate计算结果进行判断打分
        """
        if len(stock_array) < self.days:
            return 0.0
        score: float = 0.0
        result = self.calculate(stock_array)
        direction = result.get("trend", "")
        score = score + 1 if direction == "up" else score - 1
        level = result.get("level", "")
        if level == "strong":
            score = score * 2
        elif level == "weak":
            score = score * 1.5
        else:
            score = score * 0.5

        return score / 2 * 10