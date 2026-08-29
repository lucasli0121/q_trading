from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

from api.market import MarketApi
from app_context import AppContext
from utils import tools

# 增加系统路径变量
curPath = os.getcwd()
sys.path.append(curPath)

from api.client import ApiClient
from factor.volume_expansion_factor import (
    VolumeExpansionFactor
)


class TestVolumeExpansionFactor(unittest.TestCase):
    """
    因子测试
    """

    def setUp(self) -> None:
        """
        初始化测试数据
        """
        self._admin_client: ApiClient = ApiClient()
        admin_token: str = tools.load_admin_token()
        if admin_token:
            self._admin_client.set_token(admin_token)
            AppContext().api_client.set_fallback_token(admin_token)
        self._market_api: MarketApi = MarketApi(self._admin_client)
        
    def test_volume_expantion_factor(self) -> None:
        """
        测试涨幅百分比
        """

        factor = VolumeExpansionFactor(days=10)
        code = "603986"
        start_date = "2026-07-01"
        end_date = "2026-08-14"
        # 先测试日k线
        records = self._market_api.get_day_kline(code = code, start = start_date, end = end_date)
        df = pd.DataFrame(records)
        results = factor.calculate(df)
        print(results)
        result = factor.score(df)
        print(result)
        #再测试分钟行情
        start_time = "2026-08-14 10:00:00"
        end_time = "2026-08-14 11:30:00"
        records = self._market_api.get_minute_kline(code = code, start = start_time, end = end_time)
        df = pd.DataFrame(records)
        results = factor.calculate(df)
        print(results)
        result = factor.score(df)
        print(result)
if __name__ == "__main__":
    unittest.main()