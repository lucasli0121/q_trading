# factor/volume_expansion_factor.py
from __future__ import annotations
from typing import Any, Union
import numpy as np
import pandas as pd
import talib
from factor.base_factor import BaseFactor
from factor.factor_utils import FactorUtils


class VolumeExpansionFactor(BaseFactor):
    """成交量放大因子。

    连续 N 次每个周期满足：成交量均线,量比,成交量连续放大，成交量趋势，量价配合因素给成交量打分，
    成交量连续放大 + 价格上涨 = 资金持续流入 = 买入信号
    成交量连续放大 + 价格下跌 = 资金恐慌出逃 = 观望/卖出
    成交量萎缩 + 价格上涨 = 上涨乏力 = 警惕
    """

    factor_name: str = "VolumeExpansionFactor"

    def __init__(
        self,
        days: int = 20,
    ) -> None:
        """初始化成交量放大因子。

        :param days: 连续周期数最小5
        """
        self.days: int = max(days, 5)

    def calculate(self, stocks: pd.DataFrame) -> Any:
        """计算成交量放大信号。

        检查最近 self.days 个周期：
        成交量均线,量比,成交量连续放大，成交量趋势，量价配合因素
        :return: 返回各指标结果值
        """
        df = pd.DataFrame()
        if len(stocks) < self.days:
            return df  # 数据不足

        vol: np.ndarray = FactorUtils.volume_array(stocks)
        close: np.ndarray = FactorUtils.close_array(stocks)
        df = pd.DataFrame(index=stocks.index)
        df['close'] = stocks['close']
        df['high'] = stocks['high']
        df['low'] = stocks['low']
        df['volume'] = stocks['volume']

        # 1. 成交量均线
        # ===== 1. 成交量均线（动态周期）=====
        short_period = min(5, self.days)
        mid_period = min(10, self.days)

        df['vol_ma_short'] = stocks['volume'].rolling(short_period).mean()
        df['vol_ma_mid'] = stocks['volume'].rolling(mid_period).mean()
        df['vol_ma_long'] = stocks['volume'].rolling(self.days).mean()

        # 2. 量比（当前量 / 前N日均量）
        df['vol_ratio_short'] = stocks['volume'] / df['vol_ma_short']
        df['vol_ratio_mid'] = stocks['volume'] / df['vol_ma_mid']
        df['vol_ratio_long'] = stocks['volume'] / df['vol_ma_long']

        # 3. 成交量连续放大天数
        df['vol_up_and_price_up'] = (
            (stocks['volume'] > stocks['volume'].shift(1)) & 
            (stocks['close'] > stocks['close'].shift(1))
        )
        df['consecutive_vol_up'] = df['vol_up_and_price_up'].rolling(self.days).sum()

        # 4. 成交量趋势（斜率）
        # df['vol_slope_short'] = self.rolling_slope(stocks['volume'], short_period)
        # df['vol_slope_mid'] = self.rolling_slope(stocks['volume'], mid_period)
        df['vol_slope'] = self.rolling_slope(stocks['volume'], self.days)

        # 5. 量价配合
        df['price_change'] = stocks['close'].pct_change()
        df['vol_price_align'] = np.where(
            (df['price_change'] > 0) & (stocks['volume'] > df['vol_ma_short']),
            1,   # 价涨量增 ✅
            np.where(
                (df['price_change'] < 0) & (stocks['volume'] > df['vol_ma_short']),
                -1,  # 价跌量增 ❌
                0    # 其他
            )
        )
        # 6. OBV（能量潮）
        df['obv'] = talib.OBV(close, vol)
        df['obv_ma'] = df['obv'].rolling(self.days).mean()
        df['obv_above_ma'] = df['obv'] > df['obv_ma']
        return df

    def rolling_slope(self, series, window):
        """通用快速线性回归斜率"""
        x = np.arange(window)
        w = x - x.mean()
        denom = (w ** 2).sum()
        
        return series.rolling(window).apply(
            lambda y: np.dot(y, w) / denom, raw=True
        )

    def score(self, stock_array: pd.DataFrame) -> dict[str, Any]:
        """计算成交量放大综合评分（**10 分制**）。

        评分维度与满分（合计 10.0）：

        +------------------+--------+----------------------------------------+
        | 维度              | 满分   | 说明                                    |
        +------------------+--------+----------------------------------------+
        | 成交量连续放大    | 2.0    | 连续 5 天 2.0 / 4 天 1.6 / 3 天 1.2 …   |
        | 量比              | 2.0    | >=3 倍 2.0 / >=2 倍 1.6 / …（下跌取负） |
        | 成交量趋势（斜率）| 1.0    | 斜率>0 且量均线金叉 1.0，否则 0.7（下跌取负）|
        | 量价配合          | 2.0    | 价涨量增 +2.0，价跌量增 −2.0            |
        | OBV 上升          | 1.0    | OBV 在均线上方且较上一根抬升            |
        | 放量突破前期高点  | 2.0    | 收盘接近近期高点且量比 > 1.5            |
        +------------------+--------+----------------------------------------+

        信号阈值按同一刻度：`score >= 6.0` → STRONG_BUY，`>= 4.0` → BUY，
        否则 None。

        **返回值契约（与 BaseFactor 家族其它因子不同，使用方需注意）**：
        本因子返回 `dict`（含 signal/score/price/volume/reason 等明细），
        而 `KlineSRFactor` / `BollSRFactor` / `MultiTrendFactor` / `VwapFactor`
        等均返回 `float`。因此**不能**写成 `score += factor.score(df)`，
        必须显式取字段：`score += factor.score(df)["score"]`。
        保留 dict 是为了让调用方能拿到 `reason` 明细；若将来要并入统一因子
        接口，应另加一个返回 float 的入口，而不是直接改这里的返回类型。

        **`data_ok` 字段（2026-10-14 新增）**：关键输入（量比 / 斜率 / 涨跌幅 /
        连续放量天数 / OBV）任一为 NaN 时返回 `data_ok=False`，`score` 归零。
        调用方**必须**先看 `data_ok` 再使用 `score` —— `score=0` 与「算不出来」
        在买入侧 reversion 模式下都会命中「极度缩量」这个最理想买点。

        :param stock_array: 含 close/high/low/volume 列的行情 DataFrame
        :return: 明细字典，`score` 字段为 0~10 的综合分，`data_ok` 为输入完整性
        """
        score: float = 0.0
        reason: list[str] = []
        if len(stock_array) < self.days:
            return {
                'signal': 'None', 'score': score, 'price': 0.0, 'volume': 0.0,
                'vol_ratio': 0.0, 'reason': reason, 'consecutive': 0.0,
                'vol_slope': 0.0, 'vol_price_align': 0, 'data_ok': False,
            }

        df: pd.DataFrame = self.calculate(stock_array)
        row = df.iloc[-1]
        prev = df.iloc[-2]

        # === 关键输入缺失（NaN）必须先拒绝，不能让它「算成 0 分」===
        # 六项分项全部依赖区间比较（`ratio >= 3.0`、`slope > 0.0`、`obv > obv_ma` …），
        # NaN 参与比较恒为 False ⇒ 各项都不计分 ⇒ 总分落在 0 附近。
        # 而买入侧 reversion 模式的「量能强度」= (缩量上限 − 成交量分) / 缩量上限，
        # **成交量分为 0 时正好给满分** ⇒ 数据缺失反倒被判成「极度缩量」这个最理想买点。
        # 实测 405/4110 次买入信号含 NaN，落地的 5 笔全部亏损（均 −3.87%，正常组 −1.54%）。
        # 这里显式标注 `data_ok=False`，由调用方（`_combine`）判为不达标，属 fail-closed。
        missing: list[str] = [
            name
            for name, value in (
                ("量比", row['vol_ratio_short']),
                ("成交量斜率", row['vol_slope']),
                ("涨跌幅", row['price_change']),
                ("连续放量天数", row['consecutive_vol_up']),
                ("OBV", row['obv']),
            )
            if value is None or pd.isna(value)
        ]
        if missing:
            return {
                'signal': 'None', 'score': 0.0, 'price': 0.0, 'volume': 0.0,
                'vol_ratio': 0.0, 'reason': [f"数据缺失({','.join(missing)})"],
                'consecutive': 0.0, 'vol_slope': 0.0, 'vol_price_align': 0,
                'data_ok': False,
            }
        price_up = row["price_change"] > 0 # 价格上涨标志

        # === 条件1: 成交量连续放大（满分 2.0）===
        # 连续2天以上放量；未达 2 天时该项不计分（用局部变量，
        # 原实现写 `score = 0.0` 会把前面已累计的分整体清零）
        consecutive = row['consecutive_vol_up']
        reason.append(f'连续{int(row["consecutive_vol_up"])}天放量')
        consecutive_score: float = 0.0
        if consecutive >= 5:
            consecutive_score = 2.0
        elif consecutive >= 4:
            consecutive_score = 1.6
        elif consecutive >= 3:
            consecutive_score = 1.2
        elif consecutive >= 2:
            consecutive_score = 0.8
        score += consecutive_score

        # === 条件2: 量比达标（满分 2.0）===
        ratio = row['vol_ratio_short']
        ratio_score: float = 0.0
        if ratio >= 3.0:
            ratio_score += 2.0
            reason.append('量比≥3倍')
        elif ratio >= 2.0:
            ratio_score += 1.6
            reason.append('量比≥2倍')
        elif ratio >= 1.5:
            ratio_score += 1.2
            reason.append('量比≥1.5倍')
        elif ratio >= 1.2:
            ratio_score += 0.6
            reason.append('量比≥1.2倍')
        else:
            ratio_score += 0.0

        # 股票下跌为负分
        if not price_up:
            ratio_score *= -1
        score += ratio_score

        # === 条件3: 成交量趋势向上（满分 1.0）===
        slope = float(row['vol_slope'])
        vol_ma = df['vol_ma_short']
        slope_score: float = 0.0
        if slope > 0.0 and float(vol_ma.iloc[-1]) > float(vol_ma.shift(5).iloc[-1]):
            reason.append('成交量斜率向上并且成交量ma金叉')
            slope_score += 1.0
        elif slope > 0.0:
            reason.append('成交量斜率向上')
            slope_score += 0.7
        if not price_up:
            slope_score *= -1
        score += slope_score

        # === 条件4: 量价配合（价涨量增 +2.0 / 价跌量增 −2.0）===
        vol_price_align = row['vol_price_align']
        if vol_price_align == 1:
            score += 2.0
            reason.append('价涨量增')
        elif vol_price_align == -1:
            score -= 2.0

        # === 条件5: OBV创新高（满分 1.0）===
        if row['obv'] > row['obv_ma'] and row['obv'] > prev['obv']:
            score += 1.0
            reason.append('OBV上升')

        # === 条件6: 价格突破（满分 2.0）===
        # 放量突破近期高点
        recent_high = df['high'].iloc[-self.days:-1].max()
        if row['close'] > recent_high * 0.99 and row['vol_ratio_short'] > 1.5:
            score += 2.0
            reason.append('突破前期高点')

        # === 综合判断（10 分制刻度：6.0 强买 / 4.0 买入）===
        signal = "BUY"
        if score >= 6.0:
            signal = 'STRONG_BUY'
        elif score >= 4.0:
            signal = "BUY"
        else:
            signal = "None"

        return {
            'signal': signal,
            'score': score,
            'price': row['close'],
            'volume': row['volume'],
            'vol_ratio': round(row['vol_ratio_short'], 2),
            'reason': reason,
            'consecutive': consecutive,
            'vol_slope': slope,
            'vol_price_align': row['vol_price_align'],
            # 关键输入是否齐全。False 表示「算不出来」，**不等价于 0 分**：
            # 买入侧 reversion 会把 0 分读成「极缩量」并给满分。调用方必须先用本字段
            # 过滤，再拿 score 做门槛比较（见 `VolumeExpansionStrategy._combine`）。
            'data_ok': True,
        }