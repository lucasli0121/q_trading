# factor/kline_support_resistance.py
from __future__ import annotations
from typing import Any, Union
import numpy as np
import pandas as pd
from factor.base_factor import BaseFactor

class KlineSRFactor(BaseFactor):
    """
    根据历史记录计算一段时期内阻力和支撑位
    用于波段操作
    """
    factor_name: str = "KlineSRFactor"

    def __init__(self, days: int = 5) -> None:
        self.days: int = days

    def find_support_resistance(self, df: pd.DataFrame, window: int = 5, tolerance: float = 0.02) -> dict[str, list[float]]:
        """
        基于局部极值点识别支撑/阻力位
        """
        from scipy.signal import find_peaks  # 延迟导入，避免启动时依赖 scipy

        highs: np.ndarray = np.asarray(df["high"].astype(float), dtype=float)
        lows: np.ndarray = np.asarray(df["low"].astype(float), dtype=float)

        # 找局部高点（阻力候选）
        peak_idx, _ = find_peaks(highs, distance=window, prominence=np.std(highs) * 0.5)
        resistance_prices: np.ndarray = highs[peak_idx]

        # 找局部低点（支撑候选）- 对low取负找峰值
        trough_idx, _ = find_peaks(-lows, distance=window, prominence=np.std(lows) * 0.5)
        support_prices: np.ndarray = lows[trough_idx]

        # 聚类去重（相近价位合并）
        def cluster_levels(prices: np.ndarray, tol: float) -> list[float]:
            if len(prices) == 0:
                return []
            sorted_prices: list[float] = np.sort(prices).tolist()
            clusters: list[list[float]] = [[sorted_prices[0]]]
            for p in sorted_prices[1:]:
                if abs(p - clusters[-1][0]) / clusters[-1][0] <= tol:
                    clusters[-1].append(p)
                else:
                    clusters.append([p])
            return [float(np.mean(c)) for c in clusters]
        
        return {
            'support': cluster_levels(support_prices, tolerance),
            'resistance': cluster_levels(resistance_prices, tolerance)
        }
    
    def calculate(self, stock_array: pd.DataFrame) -> Any:
        if len(stock_array) < self.days:
            return {"support": [], "resistance": []}  # 数据不足，返回空结果（与正常返回类型一致）

        return self.find_support_resistance(pd.DataFrame(stock_array))

    def score(self, stock_array: pd.DataFrame) -> float:
        """
        计算支撑位和阻力位，不打分
        """
        result = self.calculate(stock_array)
        s = result.get("support", [])
        r = result.get("resistance", [])
        recent_stock = stock_array.to_dict(orient="records")[-1]
        price: float = 0.0
        if "price" in recent_stock:
            price = recent_stock.get("price", 0.0)
        elif "close" in recent_stock:
            price = recent_stock.get("close", 0.0)

        score: float = 0.0
        # s[1] 最小支撑，超卖可做多
        if len(s) >= 1 and price <= s[0]:
            score = 10.0
        elif len(s) >= 2 and price <= s[1]:
            score = 5.0
        elif len(s) >= 3 and price <= s[2]:
            score = 1.0
        if len(r) >= 1 and price >= r[0]:
            score = -1.0
        elif len(r) >= 2 and price >= r[1]:
            score = -5.0
        elif len(r) >= 3 and price >= r[2]:
            score = -10.0

        return score