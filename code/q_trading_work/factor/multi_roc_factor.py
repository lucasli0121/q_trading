# factor/pct_change_factor.py
from __future__ import annotations
from typing import Any, Union
import numpy as np
import pandas as pd
import talib
from factor.base_factor import BaseFactor
from factor.factor_utils import FactorUtils

class MultiROCFactor(BaseFactor):
    """
    多周期变动率指标因子，
    采用 ta-lib ROC 指标，适配 DataFrame 输入
    可用于检测顶背离，拐头向下 返回的值越小越不能买
    """
    factor_name: str = "MultiROCFactor"

    def __init__(self) -> None:
        self.short_period: int = 5
        self.mid_period: int = 10
        self.long_period: int = 20

    def calculate(self, stocks: pd.DataFrame) -> Any:
        """计算涨幅因子。

        :param stocks: 结构化 np.ndarray，含 close 字段
        :return: 最近交易日涨幅百分比
        """
        if len(stocks) < self.short_period + 1:
            return {}  # 数据不足

        close_arr: np.ndarray = FactorUtils.close_array(stocks)

        roc_dict: dict = {}
        # 使用 ta-lib ROC 计算涨幅
        roc_dict["roc_5"] = talib.ROC(close_arr, timeperiod=self.short_period)   # 超短
        if len(stocks) >= self.mid_period + 1:
            roc_dict["roc_10"] = talib.ROC(close_arr, timeperiod=self.mid_period)
        if len(stocks) >= self.long_period + 1:
            roc_dict["roc_20"] = talib.ROC(close_arr, timeperiod=self.long_period)
        # roc 均线
        roc_5_arr: np.ndarray = np.asarray(roc_dict["roc_5"], dtype=np.float64)
        roc_dict["roc_5_ma"] = talib.SMA(roc_5_arr, self.short_period)
        # 价格和均线关系
        roc_dict['ma5'] = talib.SMA(close_arr, timeperiod=5)
        roc_dict['vwap'] = (stocks['close'] * stocks['volume']).cumsum() / stocks['volume'].cumsum()

        return pd.DataFrame(roc_dict)
    
    def score(self, stock_array: pd.DataFrame) -> float:
        if len(stock_array) < self.short_period + 1:
            return 0.0  # 数据不足

        result_df: pd.DataFrame = self.calculate(stock_array)
        if len(result_df) < 2:
            return 0.0

        current_close = stock_array["close"].values[-1]
        result_records = result_df.to_dict(orient="records")
        current_result = result_records[-1]
        prev_result = result_records[-2]
        score: float = 0.0
        total_buy_score: float = 6.0
        # === 低吸信号（T入）===
        # 超卖
        if current_result["roc_5"] < -1.5 and current_result["roc_5"] > prev_result["roc_5"]:
            score += 2.0
        # 金叉均线
        if prev_result["roc_5"] < prev_result["roc_5_ma"] and current_result["roc_5"] > prev_result["roc_5_ma"]:
            score += 2.0
        # 价格在VWAP下方（超跌)
        if current_close < current_result['vwap'] * 0.998:
            score += 1.0
        if "roc_20" in current_result:
            if current_result["roc_20"] > 0:
                score += 1.0

        if score > 0:
            return score / total_buy_score * 10
        # --- 卖出信号(T 出) ---
        total_sell_score: float = 5.0
        # ROC超买回落
        if current_result['roc_5'] > 1.5 and current_result['roc_5'] < prev_result['roc_5']:
            score -= 2.0
        # ROC死叉其均线
        if prev_result['roc_5'] > prev_result['roc_5_ma'] and current_result['roc_5'] < current_result['roc_5_ma']:
            score -= 2.0
        #价格在VWAP上方（超涨）
        if current_close > current_result['vwap'] * 1.002:
            score -= 1.0
        
        return score / total_sell_score * 10
        