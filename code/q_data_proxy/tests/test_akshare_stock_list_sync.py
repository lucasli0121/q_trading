'''
Author: liguoqiang
Date: 2026-06-17
Description: 测试 AkStockProxy.sync_all_stock_lists 方法，
使用 mock 模拟 akshare 列表接口和 MongoDB 批量保存。
'''

import sys
import os
import unittest
from unittest.mock import patch

import pandas as pd

curPath = os.getcwd()
sys.path.append(curPath)

from stock_fetch.akshare_fetch.ak_stock_proxy import AkStockProxy


class TestAkStockListSync(unittest.TestCase):

    def setUp(self) -> None:
        super().setUp()
        self.ak_stock_proxy = AkStockProxy()

    @patch("stock_fetch.akshare_fetch.ak_stock_proxy.MongoStockInfoImpl.bulk_upsert_stock_info")
    def test_sync_all_stock_lists(self, bulk_upsert_mock):
        result = self.ak_stock_proxy.sync_all_stock_lists()


if __name__ == "__main__":
    unittest.main()
