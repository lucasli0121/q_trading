# factor/rebound_factor.py
from __future__ import annotations
from typing import Any, Union
import numpy as np
import pandas as pd
from factor.base_factor import BaseFactor
from factor.factor_utils import FactorUtils

class InDaySRFactor(BaseFactor):
    """一天日内交易中判断阻力和支撑位，用于做 T 操作。

    枢轴点 `pivot = (H + L + C) / 3` 用**前一交易日**的日线计算（经典枢轴点），
    档位按昨日振幅 `D = H − L` 的比例对称展开：

        r1/r2/r3 = pivot + R1_RATIO/R2_RATIO/R3_RATIO · D
        s1/s2/s3 = pivot − R1_RATIO/R2_RATIO/R3_RATIO · D

    价格 >= pivot 时看阻力侧（负分，超买），否则看支撑侧（正分，超卖），
    两侧都用同一套倍数，与「档位数对称」的语义一致。

    为什么不用经典 `r1 = 2P − L` / `r2 = P + D` / `r3 = P + 2D`：

    1. `r1/s1 = 2P−L / 2P−H` 的档距会随昨日收盘位置漂移在 `D/3 ~ 2D/3` 之间，
       同样的「贴不贴近支撑位」在不同收盘位置下判据不同；
    2. `r3 = P + 2D` 实测近乎死档。2026-06-01~09-28 的 8 只股票 / 596 根日K
       实测（`_probe_pivot2.py`）：档位命中的采样点里 ±10 只占 0.7%/1.3%，
       而 64.3% 的采样点落在 0 分档 —— 档位表形同虚设，与布林带因子等权平均后
       本因子的实际影响接近于零。换成 1/3、2/3、1 倍 D 后 0 分占比降到 45.4%，
       三档分别约占 16%/8%/6%。

    **档位的可达性上界**：价格落在昨日区间 `[L, H]` 内时，
    `|price − pivot| <= 2D/3`（`P−L = (D+C−L)/3 ∈ [D/3, 2D/3]`，`H−P` 同理），
    因此 `R3_RATIO = 1.0` 的第三档必然要求价格**突破昨日高低点**才能触及。
    若把倍数调大到 `m > 2/3`，第二档也会变成「破位档」，0 分档占比会回升
    （实测 0.40/0.80/1.20 组仍有 52.4%）—— 这是本次取 1/3、2/3、1 的原因。
    """

    factor_name: str = "InDaySRFactor"

    # 档位倍数（相对昨日振幅 D），必须满足 0 < R1_RATIO < R2_RATIO < R3_RATIO，
    # 且 R2_RATIO <= 2/3 才能保证「价格不突破昨日区间时可触及」（见类 docstring）
    R1_RATIO: float = 0.33
    R2_RATIO: float = 0.66
    R3_RATIO: float = 1.00

    def __init__(self, days: int = 1, shift_day: int = 0) -> None:
        self.days: int = days
        self.shift_day: int = shift_day

    def pivot_points(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算经典枢轴点与按振幅比例展开的支撑/阻力档位。

        df 需包含 high, low, close。

        :param df: 日线行情（含 high/low/close）；`shift_day=0` 时取最后一行为基准日
        :return: 含 pivot / r1..r3 / s1..s3 的点位表，index 与入参一致
        """
        # 使用前一日数据，显式转为 float 以兼容 pylance 类型检查
        prev_high: "pd.Series[float]" = df["high"].shift(self.shift_day).astype(float)
        prev_low: "pd.Series[float]" = df["low"].shift(self.shift_day).astype(float)
        prev_close: "pd.Series[float]" = df["close"].shift(self.shift_day).astype(float)

        # 枢轴点
        pivot: "pd.Series[float]" = (prev_high + prev_low + prev_close) / 3.0
        # 昨日振幅：档位距离的标尺（一字板 / 无成交时为 0）
        day_range: "pd.Series[float]" = prev_high - prev_low

        # 阻力位与支撑位：以枢轴点为基准、按振幅倍数对称展开
        r1: "pd.Series[float]" = pivot + self.R1_RATIO * day_range
        r2: "pd.Series[float]" = pivot + self.R2_RATIO * day_range
        r3: "pd.Series[float]" = pivot + self.R3_RATIO * day_range
        s1: "pd.Series[float]" = pivot - self.R1_RATIO * day_range
        s2: "pd.Series[float]" = pivot - self.R2_RATIO * day_range
        s3: "pd.Series[float]" = pivot - self.R3_RATIO * day_range

        return pd.DataFrame({
            "pivot": pivot,
            "r1": r1, "r2": r2, "r3": r3,
            "s1": s1, "s2": s2, "s3": s3
        }, index=df.index)
    
    def calculate(self, stock_array: pd.DataFrame) -> Any:
        if len(stock_array) < self.days:
            return 0.0  # 数据不足

        return self.pivot_points(pd.DataFrame(stock_array))

    def score(self, stock_array: pd.DataFrame) -> float:
        """
        计算支撑位和阻力位，根据当天股价在阻力位和支撑位之间摆动，计算分值
        """
        df: pd.DataFrame = self.calculate(stock_array)
        return self.score_with_sr(df, stock_array)

    def score_with_sr(self, sr_df: pd.DataFrame, stock_array: pd.DataFrame) -> float:
        """按枢轴点打分；任一侧数据缺失时返回 0.0（视为无点位，不参与买卖判定）。

        必须做空表保护：`sr_df` 由 `SwingTradingStrategy._get_sr_points` 提供，
        日线取不到时它按设计返回**空表**（注释写明「由下游按无点位处理」）。
        原实现直接 `to_dict(orient="records")[-1]`，空列表会抛 `IndexError`，
        被 `_check_buy_conditions` 的 `except Exception` 吞掉后返回 False ——
        净效果是该股票在这条策略下**永不产生买卖信号**，且每分钟刷一条 warning。
        新股上市首日、停牌复牌、日线回源失败（服务端只留 3 个月）都会命中。

        :param sr_df: 枢轴点位表（含 pivot/r1..r3/s1..s3），可以为空
        :param stock_array: 当日分钟行情 DataFrame（含 price 或 close），可以为空
        :return: 打分（10 / 7.5 / 5 / 0 / -5 / -7.5 / -10）
        """
        if sr_df is None or not isinstance(sr_df, pd.DataFrame) or len(sr_df) == 0:
            return 0.0
        if stock_array is None or len(stock_array) == 0:
            return 0.0
        rs_records = sr_df.to_dict(orient="records")
        recent_rs_record = rs_records[-1]
        recent_stock = stock_array.to_dict(orient="records")[-1]
        price: float = 0.0
        if "price" in recent_stock:
            price = float(recent_stock.get("price", 0.0) or 0.0)
        elif "close" in recent_stock:
            price = float(recent_stock.get("close", 0.0) or 0.0)
        p = recent_rs_record.get("pivot", 0.0)
        s1 = recent_rs_record.get("s1", 0.0)
        s2 = recent_rs_record.get("s2", 0.0)
        s3 = recent_rs_record.get("s3", 0.0)
        r1 = recent_rs_record.get("r1", 0.0)
        r2 = recent_rs_record.get("r2", 0.0)
        r3 = recent_rs_record.get("r3", 0.0)
        score: float = 0.0
        if p == 0.0 or pd.isna(p):
            return 0.0
        # 价格异常（0/负数/NaN）时不做多：否则 `price <= s3` 会假成立给出 +10
        if price <= 0 or pd.isna(price):
            return 0.0
        # 昨日无波动（一字板、停牌后无成交、数据缺失导致 H=L）：D=0 时三档与枢轴点
        # 完全重合，`price >= r3`（等价于 price >= pivot）恒成立 ⇒ 恒判 −10「极度超买」，
        # 涨停一字板会被当成卖点。此时区间不含任何信息，直接返回 0。
        # 用 `r1 <= s1` 而不是新增一列振幅：旧格式的 Redis 缓存里没有振幅列，
        # 读不到会退化成恒 0 分（比原问题更难排查）。
        if r1 <= s1:
            return 0.0
        if price >= p: # 行情向上
            if r3 > 0 and price >= r3: # 超出最大阻力位，可以卖出
                score = -10.0
            elif r2 > 0 and price >= r2:
                score = -7.5
            elif r1 > 0 and price >= r1:
                score = -5.0
        else:
            if s3 > 0 and price <= s3: # 超卖，可做多
                score = 10.0
            elif s2 > 0 and price <= s2:
                score = 7.5
            elif s1 > 0 and price <= s1:
                score = 5.0
        return score