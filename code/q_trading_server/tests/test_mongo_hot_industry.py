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

sys.path.append(os.getcwd())


class TestMongoHotIndustryImpl(unittest.TestCase):
    """MongoHotIndustryImpl 单元测试，需要 MongoDB 可用"""

    def setUp(self) -> None:
        super().setUp()
        self.impl = MongoHotIndustryImpl()

    def tearDown(self) -> None:
        # 清理所有测试数据
        super().tearDown()

    def test_add_hot_industry(self) -> None:
        hot_industrys = ["存储芯片", "半导体", "元件"]
        for name in hot_industrys:
            ok, inserted_id = self.impl.add_hot_industry(name)
            self.assertTrue(ok)
            self.assertIsNotNone(inserted_id)

    def test_remove_from_hot_industry(self) -> None:
        hot_industrys = ["存储芯片", "半导体", "元件"]
        for name in hot_industrys:
            self.impl.delete_hot_industry(name)

    def test_query_hot_industry_list(self) -> None:
        """测试查询热门行业列表"""
        ok, results = self.impl.list_hot_industries()
        self.assertTrue(ok)
        if results is not None:
            for item in results:
                print(f"热门行业: {item}")
                self.assertIn("name", item)
                self.assertIsInstance(item["name"], str)

if __name__ == "__main__":
    unittest.main()
