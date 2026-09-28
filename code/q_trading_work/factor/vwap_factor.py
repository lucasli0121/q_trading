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
        """初始化 VWAP 因子。

        :param days: 滚动窗口的周期数（分钟线场景即分钟数），<=0 表示不截断
        """
        self.days: int = days

    def calculate(self, stocks: pd.DataFrame) -> Any:
        """计算最近 `self.days` 个周期的成交量加权平均价（滚动窗口）。

        `days` 必须生效：原实现对整张表求和，等价于「当日累计 VWAP」，
        构造时传入的窗口（如配置的 `vwap_minutes_limit`）完全没作用 ——
        窗口越长，开盘初期的 VWAP 越被早盘成交量拽住，与「最近 N 分钟均价」
        的语义差别在盘中很显著。

        df 需包含: price, volume

        :param stocks: 含 price/volume 列的行情感 DataFrame
        :return: 窗口内 VWAP；数据/成交量异常时返回 0.0
        """
        df = pd.DataFrame(stocks)
        if df.empty or "price" not in df.columns or "volume" not in df.columns:
            return 0.0
        # days <= 0 时不截断（等价于全表），保持与原实现一致
        window: pd.DataFrame = df.tail(self.days) if self.days and self.days > 0 else df
        volume_sum: float = float(window["volume"].sum())
        if volume_sum == 0:
            return 0.0  # 成交量异常/为零，避免除零
        # 不再 round(2)：先取整再算百分比会对低价股引入固定的相对误差
        # （如 3.00 元附近的 0.005 元即约 0.17%），且该误差会直接进入卖出分值
        result = (window["price"] * window["volume"]).sum() / volume_sum

        return float(result)

    def score(self, stock_array: pd.DataFrame) -> float:
        avg_price = self.calculate(stock_array)
        if avg_price == 0.0:
            return 0.0
        price_arr = FactorUtils.price_array(stock_array)
        current_price = price_arr[-1]
        score = (current_price - avg_price) / avg_price * 100
        return score