from __future__ import annotations

from configparser import ConfigParser
import datetime
import os
import sys
from typing import Any
import unittest

import numpy as np
import pandas as pd

from api.market import MarketApi
from app_context import AppContext
from factor.multi_trend_factor import MultiTrendFactor

# 增加系统路径变量
curPath = os.getcwd()
sys.path.append(curPath)

from api.client import ApiClient

class TestMultiTrendFactor(unittest.TestCase):
    """
    因子测试
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
        self.TREND_DAYS_LIMIT = 7

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
    
    # def test_multi_trend_factor(self) -> None:
    #     """
    #     测试 多趋势因子
    #     """
    #     code = "603986"
    #     start_time: str = "2026-08-03 9:30:00"
    #     windows: int = 11
    #     for i in range(1, 23):
    #         end_time = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d 15:00:00")
    #         values = self.market_api.get_minute_kline(code = code, start = start_time, end = end_time)
    #         if values and len(values) > 0:
    #             factor = MultiTrendFactor()
    #             for j in range(0, len(values), windows):
    #                 left = len(values) - j
    #                 m = min(left, windows)
    #                 stocks = values[j : j + m]
    #                 if len(stocks) == 0:
    #                     continue
    #                 result = factor.score(pd.DataFrame(stocks))
    #                 create_time = stocks[0]["create_time"]
    #                 print(f"code=code, time={create_time}, score={result}")
    #         start_time = (datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S") + datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    def test_multi_trend_factor_with_day(self) -> None:
        code = "301200"
        start_date: str = "2026-07-17"
        end_date: str = "2026-08-21"
        values = self.market_api.get_day_kline(code=code, start=start_date, end=end_date)
        if values and len(values) > 0:
            factor = MultiTrendFactor(adx_period = self.TREND_DAYS_LIMIT, vwap_period = self.TREND_DAYS_LIMIT, high_low_period = self.TREND_DAYS_LIMIT)
            for j in range(0, len(values)):
                stocks = values[:j]
                if len(stocks) == 0:
                    continue
                result = factor.score(pd.DataFrame(stocks))
                create_time = stocks[-1]["create_time"]
                print(f"code=code, time={create_time}, score={result}")
class TestMultiTrendWindowContract(unittest.TestCase):
    """窗口长度与 MACD 分项的契约（2026-09-28，不触网）。

    背景：MACD 判定是「红柱比上一根放大」，需要 34 根出值（26+9−1）+ 1 根用于
    比较 = **35 根**才能真正参与打分。低于 35 根时那 3 分恒拿不到，趋势分可达
    上限被静默折损到 7.0 —— 不报错、不告警，只能按窗口长度实测才看得出来，
    因此用一组构造序列把长度门槛固定下来。
    """

    N: int = 60

    def _uptrend(self, size: int | None = None) -> pd.DataFrame:
        """加速上涨序列。

        单纯单调上涨不够：这样 MACD 红柱会收敛，`hist > 上一根 hist` 不成立。
        用二次项让涨幅递增，红柱持续放大。
        """
        n: int = size or self.N
        idx = np.arange(n)
        close = 10.0 + 0.05 * idx + 0.002 * idx ** 2
        return pd.DataFrame(
            {
                "high": close * 1.012,
                "low": close * 0.988,
                "close": close,
                "volume": np.full(n, 1_000_000.0),
                "price": close,
            }
        )

    def _factor(self) -> MultiTrendFactor:
        return MultiTrendFactor(adx_period=7, vwap_period=7, high_low_period=7)

    def test_macd_item_unreachable_below_35_bars(self) -> None:
        """20 / 25 / 34 根：最后两根至少一根 hist 为 NaN ⇒「红柱放大」不成立。"""
        df = self._uptrend()
        for win in (20, 25, 34):
            hist = self._factor().multi_trend(
                df.tail(win).reset_index(drop=True)
            )["macd_hist"].to_numpy()
            self.assertTrue(
                np.isnan(hist[-1]) or np.isnan(hist[-2]), f"win={win} 不该拿到 MACD 分"
            )

    def test_macd_item_reachable_from_35_bars(self) -> None:
        """35 / 40 / 60 根：最后两根 hist 都有值，比较得以成立。"""
        df = self._uptrend()
        for win in (35, 40, 60):
            hist = self._factor().multi_trend(
                df.tail(win).reset_index(drop=True)
            )["macd_hist"].to_numpy()
            self.assertFalse(
                np.isnan(hist[-1]) or np.isnan(hist[-2]), f"win={win} 应能拿到 MACD 分"
            )

    def test_40_bars_scores_more_than_20_bars(self) -> None:
        """放大窗口后趋势分恰好高出 MACD 那 3 分。"""
        df = self._uptrend()
        factor = self._factor()
        s20: float = factor.score(df.tail(20).reset_index(drop=True))
        s40: float = factor.score(df.tail(40).reset_index(drop=True))
        self.assertGreater(s40, s20)
        self.assertAlmostEqual(s40 - s20, 3.0, places=6)

    def test_downtrend_scores_zero(self) -> None:
        """下跌序列不得因窗口放大而拿到趋势分。"""
        df = self._uptrend()
        idx = np.arange(self.N)
        down = df.copy()
        down["close"] = 15.0 - 0.05 * idx - 0.002 * idx ** 2
        down["high"] = down["close"] * 1.012
        down["low"] = down["close"] * 0.988
        down["price"] = down["close"]
        self.assertEqual(
            self._factor().score(down.tail(40).reset_index(drop=True)), 0.0
        )

    def test_sliced_index_does_not_shift_prev_high(self) -> None:
        """索引非 0 起始（回测切片形态）时前高不得错位。

        `prev_high` 原先是 `result_df[col] = stock_data['high'].rolling(...)`，
        pandas 按索引对齐：切片索引 i..j 与 result_df 的 0..n-1 只重叠一段，
        前高实际取到窗口前部的价格，判定随之失真且不报错。改为 `.to_numpy()`
        后两种输入必须一致。
        """
        df = self._uptrend()
        factor = self._factor()
        for win in (40, 60):
            base = df.tail(win).reset_index(drop=True)
            sliced = df.tail(win)  # 索引非 0 起始
            self.assertEqual(
                factor.multi_trend(base)["prev_high"].to_numpy()[-1],
                factor.multi_trend(sliced)["prev_high"].to_numpy()[-1],
                f"win={win} 切片输入的前高发生错位",
            )
            self.assertAlmostEqual(
                factor.multi_trend(sliced)["prev_high"].to_numpy()[-1],
                float(sliced["high"].tail(7).max()),
            )


if __name__ == "__main__":
    unittest.main()
