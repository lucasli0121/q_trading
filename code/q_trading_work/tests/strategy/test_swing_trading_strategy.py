"""
Author: liguoqiang
Date: 2026-08-02
Description: 低频波段策略测试 —— 使用真实 API 行情数据（日K / 分钟字段映射）。
    服务端分钟接口当前返回空，故分钟相关用例使用真实日K数据映射为分钟字段。
"""

from __future__ import annotations

from configparser import ConfigParser
from datetime import datetime, timedelta
import unittest
from typing import Any

import pandas as pd

from api.client import ApiClient
from api.market import MarketApi
from app_context import AppContext
from strategy.swing_trading_strategy import SwingTradingStrategy
from utils.tools import load_admin_token


def setUpModule() -> None:
    AppContext().api_client.set_fallback_token(load_admin_token())


def fetch_daily(code: str = "000001") -> list[dict[str, Any]]:
    return AppContext().market_api.get_day_kline(
        code=code, start="2026-03-01", end="2026-07-31"
    )


class TestSwingTradingStrategy(unittest.TestCase):

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
            
    def test_buy_condition(self) -> None:
        code = "603993"
        start: str = "2026-07-28 09:30:00"
        end: str = "2026-07-28 15:00:00"
        strategy = SwingTradingStrategy()
        
        for i in range(0, 50):
            strategy.before_trading(start, [code])
            minute_hq_list = self.market_api.get_minute_kline(code=code, start=start, end=end)
            if len(minute_hq_list) > 0:
                for stock_item in minute_hq_list:
                    strategy._save_minute_cache(code=code, stock_data=stock_item)
                    # result, reason = strategy._check_buy_conditions(
                    #     code=code, stock_data = stock_item
                    # )
                    # if result:
                    #     print(f"Buy condition check result: {result}, info: {reason}")
                    result, reason = strategy._check_sell_conditions(
                        code=code, position = {}, stock_data = stock_item
                    )
                    if result:
                        print(f"Sell condition check result: {result}, info: {reason}")
            next_day = (datetime.strptime(start, "%Y-%m-%d %H:%M:%S") + timedelta(days=1)).strftime("%Y-%m-%d")
            start = f"{next_day} 09:30:00"
            end = f"{next_day} 15:00:00"

if __name__ == "__main__":
    unittest.main()
