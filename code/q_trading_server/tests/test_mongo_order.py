"""
Author: liguoqiang
Date: 2026-07-02 00:00:00
LastEditors: liguoqiang
LastEditTime: 2026-07-02 00:00:00
Description: MongoOrderImpl 单元测试，需要 MongoDB 可用
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.getcwd())

from db.mongo.mongo_order_impl import MongoOrderImpl


class TestMongoOrderImpl(unittest.TestCase):
    """MongoOrderImpl 单元测试，需要 MongoDB 可用"""

    def setUp(self) -> None:
        super().setUp()
        self.impl = MongoOrderImpl()
        self._created_ids: list[str] = []

    def tearDown(self) -> None:
        for order_id in self._created_ids:
            self.impl.delete_order(order_id)
        super().tearDown()

    def test_save_and_query_order(self) -> None:
        """测试保存订单并按策略查询"""
        ok, order_id = self.impl.save_order({
            "user_strategy_id": "strategy-001",
            "stock_code": "000001.SZ",
            "entrust_quantity": 100,
            "trade_price": 12.34,
            "trade_quantity": 100,
            "status": "委托",
            "time": "2026-07-02 10:00:00",
        })
        self.assertTrue(ok)
        self.assertIsNotNone(order_id)
        self._created_ids.append(order_id)

        ok2, result = self.impl.query_orders_by_user_strategy("strategy-001")
        self.assertTrue(ok2)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(len(result), 1)
        self.assertEqual(result[0]["stock_code"], "000001.SZ")

    def test_update_order_status(self) -> None:
        """测试更新订单状态"""
        ok, order_id = self.impl.save_order({
            "user_strategy_id": "strategy-002",
            "stock_code": "600000.SH",
            "entrust_quantity": 200,
            "trade_price": 9.99,
            "trade_quantity": 0,
            "status": "委托",
            "time": "2026-07-02 11:00:00",
        })
        self.assertTrue(ok)
        self.assertIsNotNone(order_id)
        self._created_ids.append(order_id)

        ok2 = self.impl.update_order_status(order_id, "成功")
        self.assertTrue(ok2)

        ok3, result = self.impl.query_order_by_id(order_id)
        self.assertTrue(ok3)
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["status"], "成功")


if __name__ == "__main__":
    unittest.main()
