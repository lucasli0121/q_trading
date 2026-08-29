# factor/rebound_factor.py
from __future__ import annotations
from typing import Any, Union
import numpy as np
import pandas as pd
from factor.base_factor import BaseFactor
from factor.factor_utils import FactorUtils

class PriceTrendFactor(BaseFactor):
    """
    通过价格高低点结构判断趋势
    适用场景: 波段交易
    Higher High + Higher Low = 上涨趋势
    Lower High + Lower Low = 下跌趋势
    """
    factor_name: str = "PriceTrendFactor"

    def __init__(self, days: int = 10) -> None:
        self.days: int = days

    def calculate(self, stock_array: pd.DataFrame) -> Any:
        if len(stock_array) < self.days:
            return {}
        recent = stock_array[-self.days:]
    
        # 找局部极值
        highs = FactorUtils.high_array(recent)
        lows = FactorUtils.low_array(recent)
        
        # 简化：比较前半段和后半段
        mid = self.days // 2
        prev_high = highs[:mid].max()
        prev_low = lows[:mid].min()
        curr_high = highs[mid:].max()
        curr_low = lows[mid:].min()
        
        hh = curr_high > prev_high  # Higher High
        hl = curr_low > prev_low    # Higher Low
        lh = curr_high < prev_high  # Lower High
        ll = curr_low < prev_low    # Lower Low
        
        if hh and hl:
            return {'trend': 'up', 'level': 'strong', 'details': 'HH+HL'}
        elif hh or hl:
            return {'trend': 'up', 'level': 'weak', 'details': 'HH or HL only'}
        elif lh and ll:
            return {'trend': 'down', 'level': 'strong', 'details': 'LH+LL'}
        elif lh or ll:
            return {'trend': 'down', 'level': 'weak', 'details': 'LH or LL only'}
        else:
            return {'trend': 'sideways', 'level': 'none', 'details': 'no clear structure'}

    def score(self, stock_array: pd.DataFrame) -> float:
        result = self.calculate(stock_array)
        score: float = 0.0
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