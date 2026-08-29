'''
Author: liguoqiang
Date: 2026-06-18
Description: 测试 StockFetch.stock_valuation_sync_task 方法，使用 mock 模拟数据库读取和 AkShare 估值同步调用。
'''

import sys
import os
import unittest
from unittest.mock import patch

curPath = os.getcwd()
sys.path.append(curPath)

from stock_fetch.stock_fetch import StockFetch


class TestStockFetchValuationSync(unittest.TestCase):

    def test_stock_valuation_sync_task_calls_sync_stock_valuation_for_each_stock(self):
        stock_fetch = StockFetch()
        stock_list = [
            {"code": "600000", "name": "浦发银行"},
            {"code": "000001", "name": "平安银行"},
        ]

        with patch("stock_fetch.stock_fetch.MongoStockInfoImpl.query_all_stock_info", return_value=(True, stock_list)) as query_mock, patch(
            "stock_fetch.stock_fetch.AkStockProxy.sync_stock_valuation", return_value=True
        ) as sync_val_mock:
            stock_fetch.stock_valuation_sync_task()

        query_mock.assert_called_once()
        self.assertEqual(sync_val_mock.call_count, 2)
        sync_val_mock.assert_any_call("600000")
        sync_val_mock.assert_any_call("000001")

    def test_stock_valuation_sync_task_skips_when_no_stock_info(self):
        stock_fetch = StockFetch()
        stock_fetch.stock_valuation_sync_task()


if __name__ == "__main__":
    unittest.main()
