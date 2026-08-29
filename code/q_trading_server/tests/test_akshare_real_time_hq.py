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
# 增加系统路径变量
curPath = os.getcwd()
sys.path.append(curPath)

from stock_fetch.akshare_fetch.ak_stock_proxy import AkStockProxy

class TestAkshareRT(unittest.TestCase):

    def setUp(self) -> None:
        return super().setUp()

    def tearDown(self) -> None:
        return super().tearDown()

    def test_real_time_stock(self):
        ak_stock_proxy = AkStockProxy()
        ak_stock_proxy.stock_real_hq_sync_task()

    
if __name__ == '__main__':
    unittest.main()