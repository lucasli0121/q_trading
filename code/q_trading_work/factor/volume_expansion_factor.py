# factor/volume_expansion_factor.py
from __future__ import annotations
from typing import Any, Union
import numpy as np
import pandas as pd
from factor.base_factor import BaseFactor
from factor.factor_utils import FactorUtils


class VolumeExpansionFactor(BaseFactor):
    """成交量放大因子。

    连续 N 次每个周期满足：成交量 >= min_volume 手 or 成交额 >= min_amount 元，
    且从 N 周期内最低点到当前收盘的涨幅 > min_price_pct%。
    """

    factor_name: str = "VolumeExpansionFactor"

    def __init__(
        self,
        days: int = 3,
        min_volume: int = 500,       # 最低成交量（手）
        min_amount: float = 10_000_000.0,  # 最低成交额（元，默认 1000 万）
        min_price_pct: float = 2.0,  # 最低累计涨幅（%）
    ) -> None:
        """初始化成交量放大因子。

        :param days: 连续周期数
        :param min_volume: 每周期最低成交量（手）
        :param min_amount: 每周期最低成交额（元）
        :param min_price_pct: 从最低点涨幅阈值（%）
        """
        self.days: int = days
        self.min_volume: int = min_volume
        self.min_amount: float = min_amount
        self.min_price_pct: float = min_price_pct

    def calculate(self, stocks: pd.DataFrame) -> Any:
        """计算成交量放大信号。

        检查最近 self.days 个周期：
        - 每个周期 volume >= min_volume 或 amount >= min_amount
        - 累计涨幅 > min_price_pct

        全部满足则返回从最低点的涨幅，否则返回 0.0。

        :param stocks: 结构化 np.ndarray，含 close/volume/amount 字段
        :return: 从最低点的涨幅（%），不满足条件时返回 0.0
        """
        if len(stocks) < self.days:
            return 0.0  # 数据不足

        price_arr: np.ndarray
        if "price" in stocks:
            price_arr = FactorUtils.price_array(stocks)
        elif "close" in stocks:
            price_arr = FactorUtils.close_array(stocks)
        volume_arr: np.ndarray = FactorUtils.volume_array(stocks)
        amount_arr: np.ndarray = FactorUtils.amount_array(stocks)

        # 取最近 self.days 个周期
        recent_price: np.ndarray = price_arr[-self.days:]
        recent_volume: np.ndarray = volume_arr[-self.days:]
        recent_amount: np.ndarray = amount_arr[-self.days:]
        avg_volumn = np.average(recent_volume)
        avg_amt = np.average(recent_amount)
        # 检查每个周期是否满足量/额条件
        vol_number = 0
        amt_number = 0
        
        for i in range(self.days):
            if recent_volume[i] > self.min_volume and recent_volume[i] > avg_volumn:
                vol_number = vol_number + 1
            if recent_amount[i] > self.min_amount and recent_amount[i] > avg_amt:
                amt_number = amt_number + 1
        rate_vol: float = vol_number / self.days * 10
        rate_amt: float = amt_number / self.days * 10
        if rate_vol < 6 or rate_amt < 6:
            return 0.0
        # 计算从 N 周期内最低点到当前收盘的涨幅
        lowest_price: float = float(np.min(recent_price))
        latest_price: float = float(recent_price[-1])

        if lowest_price <= 0:
            return 0.0

        price_pct: float = (latest_price - lowest_price) / lowest_price * 100

        if price_pct <= self.min_price_pct:
            return 0.0
        volume_sum: float = np.sum(volume_arr)
        avg_volume_sum: float = avg_volumn * self.days
        volume_rate = (volume_sum - avg_volume_sum) / volume_sum * 10
        total_score = price_pct + volume_rate
        return round(total_score, 2)

    def score(self, stock_array: pd.DataFrame) -> float:
        result = self.calculate(stock_array)
        return result