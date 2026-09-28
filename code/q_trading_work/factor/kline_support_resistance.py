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

    def __init__(self, days: int = 5, tolerance: float = 0.02) -> None:
        self.days: int = days
        self.tolerance: float = tolerance

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

        return self.find_support_resistance(pd.DataFrame(stock_array), window=self.days, tolerance=self.tolerance)

    def score(self, stock_array: pd.DataFrame) -> float:
        """按「股价相对支撑/阻力档位的位置」打分，返回 10 / 7.5 / 5 / 0 或负值。

        档位按价格升序（支撑 `s[0]` 最小、`s[-1]` 最大；阻力 `r[0]` 最小、`r[-1]` 最大），
        打分随「距最低支撑（最高阻力）的距离」逐级衰减。

        支撑侧（n = len(s)）：
            - n >= 1 且 price <= s[0]   → 10.0（跌破最低支撑，超卖）
            - n >= 3 且 price <= s[1]   →  7.5（位于最低与次低支撑之间）
            - n >= 2 且 price <= s[-1]  →  5.0（只低于最高支撑）
        阻力侧按「越过几档」对称（`s[0]` → `r[-1]`、`s[1]` → `r[-2]`、`s[-1]` → `r[0]`）：
            - n >= 1 且 price >= r[-1]  → -10.0（越过全部阻力档）
            - n >= 3 且 price >= r[-2]  →  -7.5（只差最高一档没越过）
            - n >= 2 且 price >= r[0]   →  -5.0（只越过最低一档）

        两侧在「跌破/越过档位数」上是完全对称的：n 档时 1~2 档给 ±5.0、3 档给 ±7.5、
        4 档给 ±10.0（支撑侧计的是价格下方的档位数，阻力侧计的是价格上方的档位数）。
        注意阻力侧取的是 `r[-2]` 而不是 `r[1]`：档位数 > 3 时两者不同，`r[1]` 会让
        「只越过两档」也拿到 −7.5，与支撑侧不再对称。

        因此档位只有 1 个时不会出现 ±7.5/±5.0，只有 2 个时不会出现 ±7.5。
        原实现把 ±7.5 分支写在 ±5.0 之后，而后者区间（`price <= s[-1]`）包含前者
        （`price <= s[1]`，且 `s[1] <= s[-1]`），elif 链使 ±7.5 恒不可达；阻力侧同理。

        :param stock_array: 日线行情 DataFrame（含 high/low 及 price 或 close）
        :return: 打分（10 / 7.5 / 5 / 0 / -5 / -7.5 / -10）
        """
        if stock_array is None or len(stock_array) == 0:
            return 0.0
        result = self.calculate(stock_array)
        s = result.get("support", [])
        r = result.get("resistance", [])
        recent_stock = stock_array.to_dict(orient="records")[-1]
        price: float = 0.0
        if "price" in recent_stock:
            price = float(recent_stock.get("price", 0.0) or 0.0)
        elif "close" in recent_stock:
            price = float(recent_stock.get("close", 0.0) or 0.0)
        # 价格缺失/异常（0 或负数）时不做多：否则 `price <= s[0]` 会假成立给出 +10
        if price <= 0:
            return 0.0

        score: float = 0.0
        # s[0] 最小支撑，价格越低越超卖
        len_s: int = len(s)
        if len_s >= 1 and price <= s[0]:
            score = 10.0
        elif len_s >= 3 and price <= s[1]:
            score = 7.5
        elif len_s >= 2 and price <= s[-1]:
            score = 5.0

        # r[-1] 最大阻力，价格越高越过越多档；r[-2] 是「只差最高一档没越过」
        len_r: int = len(r)
        if len_r >= 1 and price >= r[-1]:
            score = -10.0
        elif len_r >= 3 and price >= r[-2]:
            score = -7.5
        elif len_r >= 2 and price >= r[0]:
            score = -5.0

        return score