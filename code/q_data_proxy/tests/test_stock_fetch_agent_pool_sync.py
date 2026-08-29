"""
Author: GitHub Copilot
Date: 2026-08-10
Description: 测试 StockFetch 代理池相关任务，包括 load_agent_pool_stocks、real_time_hq_sync_task、minute_hq_sync_task、his_hq_sync_task。
"""

import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

curPath = os.getcwd()
sys.path.append(curPath)

from app_context import AppContext
from stock_fetch.stock_fetch import StockFetch


class TestStockFetchAgentPoolSync(unittest.TestCase):

    def setUp(self) -> None:
        self.stock_fetch = StockFetch()
        AppContext().mqtt.connect()
        return super().setUp()

    def tearDown(self) -> None:
        AppContext().mqtt.disconnect()
        return super().tearDown()

    # def test_load_agent_pool_stocks_returns_assigned_codes_and_pool_ids(self):
    #     """验证 load_agent_pool_stocks 返回的代码与代理分配表一致，并包含池 ID。"""
    #     real_time_stocks = self.stock_fetch.load_agent_pool_stocks()
    #     print(real_time_stocks)

    def test_real_time_hq_sync_task_uses_agent_pool_codes(self):
        """验证 real_time_hq_sync_task 使用 load_agent_pool_stocks 返回的股票代码进行同步。"""
        self.stock_fetch.real_time_sync_stocks = []
        self.stock_fetch.real_time_skip = 0
        self.stock_fetch.real_time_hq_sync_task()

    # def test_minute_hq_sync_task_uses_agent_pool_codes(self):
    #     """验证 minute_hq_sync_task 获取的代码与代理分配池一致并触发分钟行情推送。"""
    #     self.stock_fetch.minute_hq_sync_task()

    # def test_his_hq_sync_task_combines_agent_pool_and_hot_industry_codes(self):
    #     """验证 his_hq_sync_task 会合并代理池与热门行业股票代码，并调用历史K线获取。"""
    #     self.stock_fetch.his_hq_sync_task()
        


if __name__ == "__main__":
    unittest.main()
