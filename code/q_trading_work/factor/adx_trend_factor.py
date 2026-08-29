# factor/rebound_factor.py
from __future__ import annotations
from typing import Any, Union
import numpy as np
import pandas as pd
import talib
from factor.base_factor import BaseFactor
from factor.factor_utils import FactorUtils

class AdxTrendFactor(BaseFactor):
    """
    ADX判断趋势方向和强度
    ADX > 25: 趋势明显, < 20: 震荡
    +DI > -DI: 上涨, 反之下跌

    适用场景: 趋势跟踪
    period: 周期或者天数
    14（默认）	平衡型，兼顾灵敏度和稳定性	日线、周线
    7-10	更灵敏，信号更快	短线/日内交易
    20-25	更平滑，过滤噪音	中长线趋势跟踪

    """
    factor_name: str = "AdxTrendFactor"

    def __init__(self, days: int = 15) -> None:
        self.days: int = days

    def adx_trend(self, stock_data: pd.DataFrame, period=14, threshold=25):
        """
        ADX判断趋势方向和强度
        ADX > 25: 趋势明显, < 20: 震荡
        +DI > -DI: 上涨, 反之下跌

        """
        highs = FactorUtils.high_array(stock_data)
        lows = FactorUtils.low_array(stock_data)
        closes = FactorUtils.close_array(stock_data)
        
        adx = talib.ADX(highs, lows, closes, timeperiod=period)
        plus_di = talib.PLUS_DI(highs, lows, closes, timeperiod=period)
        minus_di = talib.MINUS_DI(highs, lows, closes, timeperiod=period)
        
        current_adx = adx[-1]
        current_plus = plus_di[-1]
        current_minus = minus_di[-1]
        
        # 判断方向
        if current_plus > current_minus:
            direction = 'up'
        else:
            direction = 'down'
        
        # 判断强度
        if current_adx > threshold:
            strength = 'strong' # 趋势强烈
        elif current_adx > 20:
            strength = 'moderate' # 趋势 温和 平庸
        else:
            strength = 'weak/sideways' # 震荡，趋势较弱
        
        return {
            'trend': direction,
            'level': strength,
            'adx': round(current_adx, 2),
            '+DI': round(current_plus, 2),
            '-DI': round(current_minus, 2)
        }

    def calculate(self, stock_array: pd.DataFrame) -> Any:
        result = self.adx_trend(stock_array, period=self.days)
        return result

    def score(self, stock_array: pd.DataFrame) -> float:
        """
        根据calculate计算结果进行判断打分
        """
        score: float = 0.0
        result = self.calculate(stock_array)
        direction = result.get("trend", "")
        score = score + 1 if direction == "up" else score - 1
        level = result.get("level", "")
        if level == "strong":
            score = score * 2
        elif level == "moderate":
            score = score * 1.5
        elif level == "weak/sideways":
            score = score * 0.5

        return score / 2 * 10