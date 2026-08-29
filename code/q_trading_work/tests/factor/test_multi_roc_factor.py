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
from factor.multi_roc_factor import MultiROCFactor

# 增加系统路径变量
curPath = os.getcwd()
sys.path.append(curPath)

from api.client import ApiClient

class TestMultiROCFactor(unittest.TestCase):
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
    
    def test_adx_trend_factor(self) -> None:
        """
        测试 adx趋势因子
        """
        code = "603986"
        start_time: str = "2026-07-30 9:30:00"
        windows: int = 20
        for i in range(1, 23):
            end_time = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d 15:00:00")
            values = self.market_api.get_minute_kline(code = code, start = start_time, end = end_time)
            if values and len(values) > 0:
                factor = MultiROCFactor()
                for j in range(0, len(values), windows):
                    left = len(values) - j
                    m = min(left, 20)
                    stocks = values[j : j + m]
                    if len(stocks) == 0:
                        continue
                    result = factor.score(pd.DataFrame(stocks))
                    create_time = stocks[0]["create_time"]
                    print(f"code=code, time={create_time}, score={result}")
            start_time = (datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S") + datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

if __name__ == "__main__":
    unittest.main()
