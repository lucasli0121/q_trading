# factor/rebound_factor.py
from __future__ import annotations
from typing import Any, Union
import numpy as np
import pandas as pd
import talib
from factor.base_factor import BaseFactor
from factor.factor_utils import FactorUtils

class MultiTrendFactor(BaseFactor):
    """
    多个因子判断趋势
    包括:ADX,MACD,VWAP,HH_HL,

    适用场景: 趋势跟踪
    period: 周期或者天数
    """
    factor_name: str = "MultiTrendFactor"

    def __init__(
            self,
            adx_period: int = 10,
            macd_fast_period: int = 12,
            macd_slow_period: int = 26,
            macd_signal_period: int = 9,
            vwap_period: int = 10,
            high_low_period: int = 10) -> None:
        self.adx_period = adx_period
        self.macd_fast_period = macd_fast_period
        self.macd_slow_period = macd_slow_period
        self.macd_signal_period = macd_signal_period
        self.vwap_period = vwap_period
        self.high_low_period = high_low_period

    def multi_trend(self, stock_data: pd.DataFrame) -> pd.DataFrame:
        """
        包括:ADX,MACD,VWAP,HH_HL, 多趋势判断

        """
        stock_data_len = len(stock_data)
        if stock_data_len < self.adx_period \
            or stock_data_len < self.vwap_period:
            return pd.DataFrame()
        
        highs = FactorUtils.high_array(stock_data)
        lows = FactorUtils.low_array(stock_data)
        closes = FactorUtils.close_array(stock_data)
        volumes = FactorUtils.volume_array(stock_data)

        # MD5 均线
        result_df: pd.DataFrame = pd.DataFrame()
        result_df["ma5"] = talib.SMA(closes, timeperiod=5)
        result_df["ma10"] = talib.SMA(closes, timeperiod=10)

        # MACD
        dif, dea, hist = talib.MACD(
            closes,
            fastperiod=self.macd_fast_period,
            slowperiod=self.macd_slow_period,
            signalperiod=self.macd_signal_period
        )
        result_df["macd_dif"] = dif
        result_df["macd_hist"] = hist

        #ADX
        result_df["adx"] = talib.ADX(highs, lows, closes, timeperiod=self.adx_period)
        result_df["plus_di"] = talib.PLUS_DI(highs, lows, closes, timeperiod=self.adx_period)
        result_df["minus_di"] = talib.MINUS_DI(highs, lows, closes, timeperiod=self.adx_period)

        # VWAP (滚动窗口)
        pv_series = pd.Series(closes * volumes)
        vol_series = pd.Series(volumes)
        result_df['cum_pv'] = pv_series.rolling(window=self.vwap_period).sum()
        result_df['cum_vol'] = vol_series.rolling(window=self.vwap_period).sum()
        result_df['vwap'] = result_df['cum_pv'] / result_df['cum_vol']
        
        # 前高前低
        result_df['prev_high'] = stock_data['high'].rolling(self.high_low_period).max()
        result_df['prev_low'] = stock_data['low'].rolling(self.high_low_period).min()

        return result_df

    def calculate(self, stock_array: pd.DataFrame) -> Any:
        result = self.multi_trend(stock_array)
        return result

    def score(self, stock_array: pd.DataFrame) -> float:
        """
        根据calculate计算结果进行判断打分
        """
        score: float = 0.0
        result_df = self.calculate(stock_array)
        if len(result_df) < 2:
            return 0.0
        result_records = result_df.to_dict(orient="records")
        latest_result = result_records[-1]
        price_arr: np.ndarray
        if "price" in stock_array:
            price_arr = FactorUtils.price_array(stock_array)
        else:
            price_arr = FactorUtils.close_array(stock_array)
        current_price: float = price_arr[-1]

        total_score: float = 8.0
        # 价格在均线上方
        if current_price > latest_result["ma5"] > latest_result["ma10"]:
            score += 2
        # MACD 红柱放大
        if latest_result['macd_hist'] > 0 and latest_result['macd_hist'] > result_records[-2]['macd_hist']:
            score += 2
        #  ADX确认趋势
        if latest_result['adx'] > 25 and latest_result['plus_di'] > latest_result['minus_di']:
            score += 2
        # 价格在VWAP上方
        if current_price > latest_result['vwap']:
            score += 1
        # 突破前高
        if current_price > latest_result['prev_high'] * 0.995:  # 接近前高
            score += 1

        return score / total_score * 10