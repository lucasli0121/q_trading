from __future__ import annotations

from configparser import ConfigParser
import datetime
import os
import sys
from typing import Any
import unittest

import numpy as np
import pandas as pd

from api.client import ApiClient
from api.market import MarketApi
from app_context import AppContext
from scipy.signal import find_peaks

# 增加系统路径变量
curPath = os.getcwd()
sys.path.append(curPath)

class TestSupportLevel(unittest.TestCase):
    """
    用不同的方法 测试阻力位和支撑位，判断支撑位和阻力位的股价
    """

    def setUp(self) -> None:
        self._admin_client: ApiClient = ApiClient()
        admin_token: str = self._load_admin_token()
        if admin_token:
            self._admin_client.set_token(admin_token)
            # 同时设置全局 ApiClient 的 fallback token，确保 AppContext().pool_api 等
            # 在后台线程中也能携带认证信息
            AppContext().api_client.set_fallback_token(admin_token)
        self.market_api: MarketApi = MarketApi(self._admin_client)

    def _load_admin_token(self) -> str:
        """从 cfg/stock.cfg 读取 admin_token。

        :return: admin_token 字符串，未配置时返回空字符串
        """
        try:
            from utils.tools import resource_path
            cp = ConfigParser()
            cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
            return cp.get("server", "admin_token", fallback="").strip()
        except Exception:
            return ""

    def load_his_daily_data(self, code: str, days: int) -> pd.DataFrame:
        """使用行情接口加载指定股票的日线数据，并返回最近 days 条记录。"""
        # 计算起始和结束日期，确保至少有足够的历史数据用于因子计算。
        # 结束日期为昨天，起始日期为当前日期向前回溯的天数，至少为 30 天。
        # 日期减一是只取历史数据，不包含当天的未完成交易日数据。
        end_date: str = (
            datetime.datetime.now() - datetime.timedelta(days=1)
        ).strftime("%Y-%m-%d")
        # 为了让均线、回撤等因子有足够的前置历史，至少多取一段历史窗口。
        # 这里使用 `days * 2` 作为默认回溯长度，并保证不小于 30 天。
        lookback_days: int = max(days * 2, 30)
        start_date: str = (
            datetime.datetime.now() - datetime.timedelta(days=lookback_days)
        ).strftime("%Y-%m-%d")

        raw_data: list[dict[str, Any]] = []

        try:
            raw_data = self.market_api.get_day_kline(
                code=code,
                start=start_date,
                end=end_date,
            )
        except Exception as exc:  # pragma: no cover - defensive
            print("market_api.get_day_kline error: %s", exc)

        if not raw_data:
            return pd.DataFrame(columns=pd.Index(["code", "open", "close", "volume", "create_time"]))

        df = pd.DataFrame(raw_data)
        if df.empty:
            return pd.DataFrame(columns=pd.Index(["code", "open", "close", "volume", "create_time"]))

        if not df.empty and "create_time" not in df.columns:
            if "date" in df.columns:
                df["create_time"] = df["date"]
            else:
                df["create_time"] = pd.Series(range(len(df)), index=df.index)

        if "code" not in df.columns:
            df["code"] = code

        df = df.copy()
        df = df.tail(max(days, 1))
        return df


    def pivot_points(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        经典枢轴点计算法
        df需包含: high, low, close
        """
        # 使用前一日数据，显式转为 float 以兼容 pylance 类型检查
        prev_high: "pd.Series[float]" = df["high"].shift(1).astype(float)
        prev_low: "pd.Series[float]" = df["low"].shift(1).astype(float)
        prev_close: "pd.Series[float]" = df["close"].shift(1).astype(float)

        # 枢轴点
        pivot: "pd.Series[float]" = (prev_high + prev_low + prev_close) / 3.0

        # 阻力位
        r1: "pd.Series[float]" = 2.0 * pivot - prev_low
        r2: "pd.Series[float]" = pivot + (prev_high - prev_low)
        r3: "pd.Series[float]" = pivot + 2.0 * (prev_high - prev_low)

        # 支撑位
        s1: "pd.Series[float]" = 2.0 * pivot - prev_high
        s2: "pd.Series[float]" = pivot - (prev_high - prev_low)
        s3: "pd.Series[float]" = pivot - 2.0 * (prev_high - prev_low)

        return pd.DataFrame({
            "pivot": pivot,
            "r1": r1, "r2": r2, "r3": r3,
            "s1": s1, "s2": s2, "s3": s3,
            "create_time": df["create_time"]
        }, index=df.index)

    def find_support_resistance(self, df: pd.DataFrame, window: int = 5, tolerance: float = 0.02) -> dict[str, list[float]]:
        """
        基于局部极值点识别支撑/阻力位
        """
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

    def volume_profile_levels(self, df, num_bins=50, lookback=60, value_area_pct=0.7):
        """
        基于成交量分布找关键价位
        """
        recent = df.tail(lookback)
        
        price_min = recent['low'].min()
        price_max = recent['high'].max()
        bins = np.linspace(price_min, price_max, num_bins)
        
        vol_profile = np.zeros(num_bins - 1)
        
        for _, row in recent.iterrows():
            low_idx = max(0, np.searchsorted(bins, row['low']) - 1)
            high_idx = min(num_bins - 2, np.searchsorted(bins, row['high']) - 1)
            
            if low_idx <= high_idx:
                vol_per = row['volume'] / (high_idx - low_idx + 1)
                for j in range(low_idx, high_idx + 1):
                    vol_profile[j] += vol_per
        
        # POC: 成交量最大价位
        poc_idx = np.argmax(vol_profile)
        poc = (bins[poc_idx] + bins[poc_idx + 1]) / 2
        
        # Value Area (70%成交量区间)
        sorted_idx = np.argsort(vol_profile)[::-1]
        cumsum = 0
        va_indices = []
        for idx in sorted_idx:
            cumsum += vol_profile[idx]
            va_indices.append(idx)
            if cumsum >= vol_profile.sum() * value_area_pct:
                break
        
        return {
            'poc': poc,
            'value_area_low': bins[min(va_indices)],
            'value_area_high': bins[max(va_indices) + 1]
        }

    # def test_pivot_points(self) -> None:
    #     """
    #     经典枢轴点计算法
    #     """
    #     code = "603993"
    #     df = self.load_his_daily_data(code, 30)
    #     result = self.pivot_points(df)
    #     print(result)

    def test_support_resistance(self):
        code = "000001"
        df = self.load_his_daily_data(code, 30)
        result = self.find_support_resistance(df)
        print(result)

    # def test_volume_profile(self):
    #     code = "603993"
    #     df = self.load_his_daily_data(code, 30)
    #     result = self.volume_profile_levels(df)
    #     print(result)

if __name__ == "__main__":
    unittest.main()
