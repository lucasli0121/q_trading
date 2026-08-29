from __future__ import annotations

from configparser import ConfigParser
import datetime
import os
import sys
from typing import Any
import unittest

import pandas as pd

from api.market import MarketApi
from app_context import AppContext
from strategy.strong_rebound_strategy import StrongReboundStrategy

# 增加系统路径变量
curPath = os.getcwd()
sys.path.append(curPath)

from api.client import ApiClient

class TestReboundStrategySelect(unittest.TestCase):
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
    

    def test_select_with_day(self) -> None:
        code = "301200"
        start_date: str = "2026-07-17"
        end_date: str = "2026-08-21"
        values = self.market_api.get_day_kline(code=code, start=start_date, end=end_date)
        if values and len(values) > 0:
            strategy = StrongReboundStrategy()
            for j in range(0, len(values)):
                stocks = values[:j]
                if len(stocks) == 0:
                    continue
                res, result = strategy.is_match_strategy(stocks)
                create_time = stocks[-1]["create_time"]
                print(f"code=code, time={create_time}, res={res}, result={result}")
if __name__ == "__main__":
    unittest.main()
