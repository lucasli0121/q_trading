'''
Author: liguoqiang
Date: 2022-06-14 16:04:36
LastEditors: liguoqiang
LastEditTime: 2023-06-06 12:11:39
Description: 
'''

import sys
from configparser import ConfigParser
import json
import os
import unittest
import urllib3 as ulib
from datetime import datetime, timedelta

from stock_fetch.stock_fetch import StockFetch
# 增加系统路径变量
curPath = os.getcwd()
sys.path.append(curPath)

class TestAkStockInfo(unittest.TestCase):

    def setUp(self) -> None:
        return super().setUp()

    def tearDown(self) -> None:
        return super().tearDown()

    def test_sync_stock_list_info(self):
        stock_fetch = StockFetch()
        stock_fetch.stock_list_info_sync_task()

    
if __name__ == '__main__':
    unittest.main()