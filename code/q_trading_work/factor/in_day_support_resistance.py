# factor/rebound_factor.py
from __future__ import annotations
from typing import Any, Union
import numpy as np
import pandas as pd
from factor.base_factor import BaseFactor
from factor.factor_utils import FactorUtils

class InDaySRFactor(BaseFactor):
    """
    一天日内交易中判断阻力和支撑位
    用于做T操作
    """
    factor_name: str = "InDaySRFactor"

    def __init__(self, days: int = 1) -> None:
        self.days: int = days

    def pivot_points(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        经典枢轴点计算法
        df需包含: high, low, close
        """
        # 使用前一日数据，显式转为 float 以兼容 pylance 类型检查
        prev_high: "pd.Series[float]" = df["high"].shift(1).astype(float)
        prev_low: "pd.Series[float]" = df["low"].shift(1).astype(float)
        prev_close: "pd.Series[float]" = df["close"].shift(1).astype(float)

        # 枢轴点
        pivot: "pd.Series[float]" = (prev_high + prev_low + prev_close) / 3.0

        # 阻力位
        r1: "pd.Series[float]" = 2.0 * pivot - prev_low
        r2: "pd.Series[float]" = pivot + (prev_high - prev_low)
        r3: "pd.Series[float]" = pivot + 2.0 * (prev_high - prev_low)

        # 支撑位
        s1: "pd.Series[float]" = 2.0 * pivot - prev_high
        s2: "pd.Series[float]" = pivot - (prev_high - prev_low)
        s3: "pd.Series[float]" = pivot - 2.0 * (prev_high - prev_low)

        return pd.DataFrame({
            "pivot": pivot,
            "r1": r1, "r2": r2, "r3": r3,
            "s1": s1, "s2": s2, "s3": s3
        }, index=df.index)
    
    def calculate(self, stock_array: pd.DataFrame) -> Any:
        if len(stock_array) < self.days:
            return 0.0  # 数据不足

        return self.pivot_points(pd.DataFrame(stock_array))

    def score(self, stock_array: pd.DataFrame) -> float:
        """
        计算支撑位和阻力位，根据当天股价在阻力位和支撑位之间摆动，计算分值
        """
        df: pd.DataFrame = self.calculate(stock_array)
        rs_records = df.to_dict(orient="records")
        recent_rs_record = rs_records[-1]
        recent_stock = stock_array.to_dict(orient="records")[-1]
        price: float = 0.0
        if "price" in recent_stock:
            price = recent_stock.get("price", 0.0)
        elif "close" in recent_stock:
            price = recent_stock.get("close", 0.0)
        p = recent_rs_record.get("pivot", 0.0)
        s1 = recent_rs_record.get("s1", 0.0)
        s2 = recent_rs_record.get("s2", 0.0)
        s3 = recent_rs_record.get("s3", 0.0)
        r1 = recent_rs_record.get("r1", 0.0)
        r2 = recent_rs_record.get("r2", 0.0)
        r3 = recent_rs_record.get("r3", 0.0)
        score: float = 0.0
        if p == 0.0:
            return 0.0
        if price >= p: # 行情向上
            if price >= r3 and r3 > 0: # 超出最大阻力位，可以卖出
                score = -10.0
            elif price >= r2 and r2 > 0:
                score = -7.5
            elif price >= r1 and r1 > 0:
                score = -5.0
            else:
                score = 5.0 # 还在上涨中，利好
        else:
            if price <= s3: # 超卖，可做多
                score = 10.0
            elif price <= s2:
                score = 7.5
            elif price <= s1:
                score = 5.0
            else:
                score = -5.0 # 还在下跌中 利空
        return score