# factor/boll_support_resistance.py
from __future__ import annotations
from typing import Any
import numpy as np
import pandas as pd
import talib
from factor.base_factor import BaseFactor

class BollSRFactor(BaseFactor):
    """基于布林带的支撑位和阻力位因子，用于波段操作
    用于做T操作辅助

    轨道以「中轨 ± k 倍标准差」定义（k = 1.0 / 1.5 / 2.0）：
      - pivot = 中轨（n 周期均值），sigma = n 周期标准差
      - r1/r2/r3 = 中轨 + 1.0σ / 1.5σ / 2.0σ
      - s1/s2/s3 = 中轨 − 1.0σ / 1.5σ / 2.0σ

    **为什么是 1σ/1.5σ/2σ 而不是「上轨及外推」**：原实现取
    `r1=上轨(+2σ)`、`r2=+4σ`、`r3=+6σ`，但布林带窗口**含当根价格**，
    单根价格对中轨的偏离存在上界 `|price − mean| <= sqrt(n−1)·σ`
    （窗口内其余 n−1 个值全相等时取等号）。因此：
      - n=5  （BUY_SR_WINDOW 默认） → 上限 2σ  ⇒ +4σ/+6σ 档恒不可达
      - n=10（当前 cfg 配置）      → 上限 3σ  ⇒ +4σ/+6σ 档恒不可达
      - n=15（类默认值）           → 上限 √14≈3.74σ ⇒ +6σ 档仍不可达
    即配置窗口下 `avg_score` 只可能出现 ±5 与 0，档位表形同虚设。
    改用 1σ/1.5σ/2σ 后，n>=5 时三档全部可达（2σ 恰好在上界内）。

    打分逻辑：股价贴近支撑位（超卖）给正分（可做多），
    贴近阻力位（超买）给负分（可卖出）。
    """
    factor_name: str = "BollSRFactor"

    # 轨道倍数（相对标准差 sigma），必须 <= sqrt(timeperiod - 1) 才可达
    R1_SIGMA: float = 1.0
    R2_SIGMA: float = 1.5
    R3_SIGMA: float = 2.0

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

        返回列：pivot（中轨）、sigma（标准差）、r1/r2/r3（阻力位）、s1/s2/s3（支撑位）。
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
        # 由上下轨反推标准差：talib 的 (upper − middle) = nbdevup * sigma
        dev: float = float(self.nbdevup) if self.nbdevup else 1.0
        sigma: np.ndarray = (upper - middle) / dev

        return pd.DataFrame(
            {
                "pivot": middle,
                "sigma": sigma,
                "r1": middle + self.R1_SIGMA * sigma,
                "r2": middle + self.R2_SIGMA * sigma,
                "r3": middle + self.R3_SIGMA * sigma,
                "s1": middle - self.R1_SIGMA * sigma,
                "s2": middle - self.R2_SIGMA * sigma,
                "s3": middle - self.R3_SIGMA * sigma,
            }
        )

    def score(self, stock_array: pd.DataFrame) -> float:
        """计算支撑位和阻力位，根据当天股价在阻力位和支撑位之间摆动，计算分值。

        价格 >= 中轨（向上）：贴近阻力位给负分（超买，可卖出）
        价格 <  中轨（向下）：贴近支撑位给正分（超卖，可做多）

        :param stock_array: 含 price 或 close 列的行情 DataFrame
        :return: 打分（10 / 7.5 / 5 / 0 / -5 / -7.5 / -10）
        """
        df: pd.DataFrame = self.calculate(stock_array)
        if df is None or df.empty:
            return 0.0

        rs_record: dict = df.to_dict(orient="records")[-1]  # 取最近一根 K 线的支撑/阻力位
        recent_stock: dict = stock_array.to_dict(orient="records")[-1]

        price: float = 0.0
        if "price" in recent_stock:
            price = float(recent_stock.get("price", 0.0) or 0.0)
        elif "close" in recent_stock:
            price = float(recent_stock.get("close", 0.0) or 0.0)

        p = rs_record.get("pivot", 0.0)
        sigma = rs_record.get("sigma", 0.0)
        s1 = rs_record.get("s1", 0.0)
        s2 = rs_record.get("s2", 0.0)
        s3 = rs_record.get("s3", 0.0)
        r1 = rs_record.get("r1", 0.0)
        r2 = rs_record.get("r2", 0.0)
        r3 = rs_record.get("r3", 0.0)

        score: float = 0.0
        # 中轨为 0 / NaN（数据不足或异常）时无法判断，直接返回 0
        if p == 0.0 or pd.isna(p):
            return 0.0
        # 价格异常（0/负数/NaN）时不做多，避免 `price <= s3` 假成立给出 +10
        if price <= 0 or pd.isna(price):
            return 0.0
        # 零波动窗口（一字板、长时间无成交）：sigma=0 ⇒ 三轨与中轨重合，
        # 原实现下 `price >= r3`（即 price >= 中轨）恒成立 ⇒ score 恒 −10，
        # 涨停一字板会被判成「极度超买」而触发卖出。此时区间无信息量，直接返回 0。
        if sigma is None or pd.isna(sigma) or float(sigma) <= 0.0:
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
