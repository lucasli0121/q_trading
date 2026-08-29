"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-06-21 20:00:00
Description: MongoBlacklistImpl 单元测试，需要 MongoDB 可用
"""

from __future__ import annotations

import os
import sys
import unittest

from db.mongo.mongo_hot_industry_impl import MongoHotIndustryImpl
from db.mongo.mongo_rt_stocks_impl import MongoRtStocksImpl

sys.path.append(os.getcwd())


class TestMongoRTSocketImpl(unittest.TestCase):
    """MongoHotIndustryImpl 单元测试，需要 MongoDB 可用"""

    def setUp(self) -> None:
        super().setUp()
        self.impl = MongoHotIndustryImpl()

    def tearDown(self) -> None:
        # 清理所有测试数据
        super().tearDown()

    def test_aggregate_minute_hq(self) -> None:
        rt_impl = MongoRtStocksImpl()
        res, values = rt_impl.aggregate_minute_hq_for_codes(["002281.SZ"], "2026-07-17 09:51")
        if values:
            print(values)

if __name__ == "__main__":
    unittest.main()
