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

    打分采用 **10 分制**（`score()` 的返回值直接落在 0~10，不再做归一化），
    各分项权重与可达性：

    +------------------+--------+------------------------------------------+
    | 分项              | 分值   | 生效前提                                 |
    +------------------+--------+------------------------------------------+
    | 价格 > MA5 > MA10 | 3.0    | 至少 10 根                                |
    | MACD 红柱放大     | 3.0    | **至少 35 根**，否则该项恒不成立          |
    | ADX > 25 且 +DI>-DI| 3.0   | 至少 2×adx_period 根                      |
    | 价格在 VWAP 上方  | 0.5    | 至少 vwap_period 根                       |
    | 接近/突破前高     | 0.5    | 至少 high_low_period 根                   |
    +------------------+--------+------------------------------------------+

    注意 `macd_hist` 的长度要求是 **35 根**而不是 34：talib.MACD 需要
    26 + 9 − 1 = 34 根才有值，而这里的判定是「红柱比上一根放大」
    （`hist > 0 且 hist > 上一根的 hist`），所以还要再多一根做比较 ——
    实测 34 根时当根 hist 有值但前一根仍是 NaN，`NaN > NaN` 为 False，
    那 3 分照样拿不到。日线窗口自 2026-09-28 起为 **40 根**
    （`StrategyWorkflow.SELECT_KLINE_DAYS` 与回测 `BacktestConfig.signal_window`
    同值），留 5 根余量。此前窗口是 20 根，该分项恒为 NaN、那 3 分从来没拿到过，
    趋势分可达上限被静默折损到 7.0 —— 这种「上限悄悄变低」不会报错也不会告警，
    只有按窗口长度实测分档才看得出来（见 `tests/factor/test_multi_trend_factor.py`）。
    NaN 本身是安全的（`NaN > 0` 为 False，不进入计分分支），风险只在于阈值
    （如 `up_trend_score`）是按旧尺度定的，窗口一变就要重新核一遍余量。
    """
    factor_name: str = "MultiTrendFactor"

    # 各分项满分（与 score() 里的加分保持一致），总分即 10 分制刻度
    MA_SCORE: float = 3.0
    MACD_SCORE: float = 3.0
    ADX_SCORE: float = 3.0
    VWAP_SCORE: float = 0.5
    PREV_HIGH_SCORE: float = 0.5

    # score() 的可达满分：阈值（如 UP_TREND_SCORE）应按它取比例，
    # 改动任一权重时必须同步改这里，避免「阈值与尺度脱节」。
    MAX_SCORE: float = 10.0

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
        
        # 前高前低。
        # 必须 `.to_numpy()` 再赋值：`result_df` 的索引是 0..n-1，而 `stock_data`
        # 的索引未必从 0 开始（调用方传切片时是 i..j）。直接把 Series 赋给列会
        # **按索引对齐**，只有与 0..n-1 重叠的那段有值、且值整体前移错位 ——
        # 表现是「前高」取到了窗口前部的价格，档位判定随之失真，但不会报错。
        # 其余列都来自 talib 返回的 ndarray，天然按位置对齐，不受影响。
        result_df['prev_high'] = (
            stock_data['high'].rolling(self.high_low_period).max().to_numpy()
        )
        result_df['prev_low'] = (
            stock_data['low'].rolling(self.high_low_period).min().to_numpy()
        )

        return result_df

    def calculate(self, stock_array: pd.DataFrame) -> Any:
        result = self.multi_trend(stock_array)
        return result

    def score(self, stock_array: pd.DataFrame) -> float:
        """根据calculate计算结果进行判断打分，返回 0 ~ 10 分（10 分制）。

        各分项权重见类 docstring；条件不成立（含对应指标为 NaN）时该项不计分。

        :param stock_array: 日线或分钟行情 DataFrame（含 high/low/close/volume）
        :return: 趋势分（0 ~ MAX_SCORE，即 0 ~ 10）
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

        # 价格在均线上方
        if current_price > latest_result["ma5"] > latest_result["ma10"]:
            score += self.MA_SCORE
        # MACD 红柱放大（<35 根时该项恒不成立：<34 根 hist 为 NaN，
        # 34 根时前一根仍是 NaN，无法比较放大）
        if latest_result['macd_hist'] > 0 and latest_result['macd_hist'] > result_records[-2]['macd_hist']:
            score += self.MACD_SCORE
        #  ADX确认趋势
        if latest_result['adx'] > 25 and latest_result['plus_di'] > latest_result['minus_di']:
            score += self.ADX_SCORE
        # 价格在VWAP上方
        if current_price > latest_result['vwap']:
            score += self.VWAP_SCORE
        # 突破前高
        if current_price > latest_result['prev_high'] * 0.995:  # 接近前高
            score += self.PREV_HIGH_SCORE

        return score
