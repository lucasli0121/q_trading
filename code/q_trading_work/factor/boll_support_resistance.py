# factor/boll_support_resistance.py
from __future__ import annotations
from typing import Any
import numpy as np
import pandas as pd
import talib
from factor.base_factor import BaseFactor

class BollSRFactor(BaseFactor):
    """
    基于布林带的支撑位和阻力位因子，用于波段操作
    用于做T操作辅助

    支撑/阻力位由布林带三轨外推得到：
      - pivot = 中轨（均值）
      - r1/r2/r3 = 阻力位（上轨及向上外推 1/2 倍带宽）
      - s1/s2/s3 = 支撑位（下轨及向下外推 1/2 倍带宽）

    打分逻辑：股价贴近支撑位（超卖）给正分（可做多），
    贴近阻力位（超买）给负分（可卖出）。
    """
    factor_name: str = "BollSRFactor"

    def __init__(
        self,
        timeperiod: int = 15,
        nbdevup: int = 2,
        nbdevdn: int = 2
    ) -> None:
        self.timeperiod: int = timeperiod
        self.nbdevup: int = nbdevup
        self.nbdevdn: int = nbdevdn

    def calculate(self, stock_array: pd.DataFrame) -> pd.DataFrame:
        """基于布林带计算支撑位与阻力位，返回 DataFrame（每根 K 线一行）。

        返回列：pivot、r1/r2/r3（阻力位）、s1/s2/s3（支撑位）。
        数据不足 timeperiod 根时返回空 DataFrame。

        :param stock_array: 含 price 或 close 列的行情 DataFrame
        :return: 支撑/阻力位 DataFrame
        """
        if len(stock_array) < self.timeperiod:
            return pd.DataFrame()  # 数据不足，返回空（与 score 的空结果判断兼容）

        price_series: np.ndarray
        if "price" in stock_array.columns:
            price_series = np.asarray(stock_array["price"], dtype=np.float64)
        elif "close" in stock_array.columns:
            price_series = np.asarray(stock_array["close"], dtype=np.float64)
        else:
            raise ValueError("DataFrame must contain 'price' or 'close' column")

        upper, middle, lower = talib.BBANDS(
            price_series,
            timeperiod=self.timeperiod,
            nbdevup=self.nbdevup,
            nbdevdn=self.nbdevdn,
        )
        # 布林带上下带宽（nbdev * std）
        band_up: np.ndarray = upper - middle
        band_dn: np.ndarray = middle - lower

        return pd.DataFrame(
            {
                "pivot": middle,
                "r1": upper,
                "r2": upper + band_up,
                "r3": upper + 2 * band_up,
                "s1": lower,
                "s2": lower - band_dn,
                "s3": lower - 2 * band_dn,
            }
        )

    def score(self, stock_array: pd.DataFrame) -> float:
        """
        计算支撑位和阻力位，根据当天股价在阻力位和支撑位之间摆动，计算分值

        价格 >= 中轨（向上）：贴近阻力位给负分（超买，可卖出）
        价格 <  中轨（向下）：贴近支撑位给正分（超卖，可做多）
        """
        df: pd.DataFrame = self.calculate(stock_array)
        if df is None or df.empty:
            return 0.0

        rs_record: dict = df.to_dict(orient="records")[-1]  # 取最近一根 K 线的支撑/阻力位
        recent_stock: dict = stock_array.to_dict(orient="records")[-1]

        price: float = 0.0
        if "price" in recent_stock:
            price = float(recent_stock.get("price", 0.0))
        elif "close" in recent_stock:
            price = float(recent_stock.get("close", 0.0))

        p = rs_record.get("pivot", 0.0)
        s1 = rs_record.get("s1", 0.0)
        s2 = rs_record.get("s2", 0.0)
        s3 = rs_record.get("s3", 0.0)
        r1 = rs_record.get("r1", 0.0)
        r2 = rs_record.get("r2", 0.0)
        r3 = rs_record.get("r3", 0.0)

        score: float = 0.0
        # 中轨为 0 或 NaN（数据不足/异常）时无法判断，直接返回 0
        if p == 0.0 or pd.isna(p):
            return 0.0
        if price >= p:  # 行情向上，接近阻力位 → 卖出信号（负分）
            if r3 > 0 and price >= r3:  # 超出最大阻力位，可以卖出
                score = -10.0
            elif r2 > 0 and price >= r2:
                score = -7.5
            elif r1 > 0 and price >= r1:
                score = -5.0
        else:  # 行情向下，接近支撑位 → 买入信号（正分）
            if s3 > 0 and price <= s3:  # 超卖，可做多
                score = 10.0
            elif s2 > 0 and price <= s2:
                score = 7.5
            elif s1 > 0 and price <= s1:
                score = 5.0
        return score
