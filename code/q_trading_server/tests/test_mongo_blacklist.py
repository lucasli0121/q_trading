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

sys.path.append(os.getcwd())

from db.mongo.mongo_blacklist_impl import MongoBlacklistImpl

TEST_USER_ID = "test_user_id_000000000000000000000001"
TEST_CODES = ["000001", "600519", "000858", "300750"]


class TestMongoBlacklistImpl(unittest.TestCase):
    """MongoBlacklistImpl 单元测试，需要 MongoDB 可用"""

    def setUp(self) -> None:
        super().setUp()
        self.impl = MongoBlacklistImpl()
        # 清理可能残留的测试数据
        self.impl.batch_remove_from_blacklist(TEST_USER_ID, TEST_CODES)

    def tearDown(self) -> None:
        # 清理所有测试数据
        self.impl.batch_remove_from_blacklist(TEST_USER_ID, TEST_CODES)
        super().tearDown()

    # ---- add_to_blacklist ----

    def test_add_to_blacklist(self) -> None:
        """测试添加单只股票到黑名单"""
        data = {
            "user_id": TEST_USER_ID,
            "code": "000001",
            "add_time": "2026-06-21 10:00:00",
            "reason": "测试拉黑原因",
        }
        ok, inserted_id = self.impl.add_to_blacklist(data)

        self.assertTrue(ok)
        self.assertIsNotNone(inserted_id)

    def test_add_to_blacklist_duplicate(self) -> None:
        """测试重复添加同一只股票（upsert，不报错）"""
        data = {
            "user_id": TEST_USER_ID,
            "code": "000001",
            "add_time": "2026-06-21 10:00:00",
            "reason": "测试拉黑原因",
        }
        # 第一次
        ok1, _ = self.impl.add_to_blacklist(data)
        self.assertTrue(ok1)
        # 第二次（更新 add_time）
        data["add_time"] = "2026-06-21 11:00:00"
        ok2, ret_id = self.impl.add_to_blacklist(data)
        self.assertTrue(ok2)
        self.assertIsNone(ret_id)  # upsert 已存在记录，upserted_id 为 None

    def test_add_to_blacklist_empty_params(self) -> None:
        """测试空 user_id 或 code 时返回失败"""
        ok1, ret1 = self.impl.add_to_blacklist({"user_id": "", "code": "000001"})
        self.assertFalse(ok1)
        self.assertIsNone(ret1)

        ok2, ret2 = self.impl.add_to_blacklist({"user_id": TEST_USER_ID, "code": ""})
        self.assertFalse(ok2)
        self.assertIsNone(ret2)

    def test_batch_add_to_blacklist(self) -> None:
        """测试批量添加股票到黑名单"""
        records = [
            {"user_id": TEST_USER_ID, "code": "000001", "add_time": "2026-06-21 10:00:00", "reason": "测试原因1"},
            {"user_id": TEST_USER_ID, "code": "600519", "add_time": "2026-06-21 10:00:00", "reason": "测试原因2"},
            {"user_id": TEST_USER_ID, "code": "000858", "add_time": "2026-06-21 10:00:00", "reason": "测试原因3"},
        ]
        ok = self.impl.batch_add_to_blacklist(records)
        self.assertTrue(ok)

        # 验证全部入库
        ok2, results = self.impl.query_blacklist_by_user(TEST_USER_ID)
        self.assertTrue(ok2)
        self.assertIsNotNone(results)
        codes = [r["code"] for r in results]
        self.assertIn("000001", codes)
        self.assertIn("600519", codes)
        self.assertIn("000858", codes)

    # ---- remove_from_blacklist ----

    def test_remove_from_blacklist(self) -> None:
        """测试从黑名单移除单只股票"""
        # 先添加
        self.impl.add_to_blacklist({
            "user_id": TEST_USER_ID, "code": "300750", "add_time": "2026-06-21 10:00:00", "reason": "测试拉黑",
        })
        # 确认存在
        ok, is_blacklisted = self.impl.is_stock_blacklisted(TEST_USER_ID, "300750")
        self.assertTrue(ok)
        self.assertTrue(is_blacklisted)

        # 移除
        removed = self.impl.remove_from_blacklist(TEST_USER_ID, "300750")
        self.assertTrue(removed)

        # 确认已移除
        ok2, is_blacklisted2 = self.impl.is_stock_blacklisted(TEST_USER_ID, "300750")
        self.assertTrue(ok2)
        self.assertFalse(is_blacklisted2)

    # ---- query_blacklist_by_user ----

    def test_query_blacklist_by_user(self) -> None:
        """测试查询用户的全部黑名单"""
        self.impl.add_to_blacklist({
            "user_id": TEST_USER_ID, "code": "000001", "add_time": "2026-06-21 10:00:00", "reason": "查询测试",
        })

        ok, results = self.impl.query_blacklist_by_user(TEST_USER_ID)
        self.assertTrue(ok)
        self.assertIsNotNone(results)
        self.assertGreaterEqual(len(results), 1)
        codes = [r["code"] for r in results]
        self.assertIn("000001", codes)

    def test_query_blacklist_by_user_empty(self) -> None:
        """测试查询无黑名单的用户"""
        empty_user = "test_empty_user_999999999999999999999999"
        ok, results = self.impl.query_blacklist_by_user(empty_user)
        self.assertTrue(ok)
        self.assertIsNone(results)

    # ---- is_stock_blacklisted ----

    def test_is_stock_blacklisted_true(self) -> None:
        """测试股票在黑名单中"""
        self.impl.add_to_blacklist({
            "user_id": TEST_USER_ID, "code": "600519", "add_time": "2026-06-21 10:00:00", "reason": "存在性检查测试",
        })

        ok, is_blacklisted = self.impl.is_stock_blacklisted(TEST_USER_ID, "600519")
        self.assertTrue(ok)
        self.assertTrue(is_blacklisted)

    def test_is_stock_blacklisted_false(self) -> None:
        """测试股票不在黑名单中"""
        ok, is_blacklisted = self.impl.is_stock_blacklisted(TEST_USER_ID, "999999")
        self.assertTrue(ok)
        self.assertFalse(is_blacklisted)


if __name__ == "__main__":
    unittest.main()
