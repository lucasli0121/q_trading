"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-06-21 20:00:00
Description: MongoUserImpl 单元测试，需要 MongoDB 可用
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.getcwd())

from db.mongo.mongo_user_impl import MongoUserImpl

TEST_ACCOUNT_PREFIX = "test_q_share_user_"


def _test_account(base: str) -> str:
    """生成测试用账号，避免与真实数据冲突"""
    return f"{TEST_ACCOUNT_PREFIX}{base}"


class TestMongoUserImpl(unittest.TestCase):
    """MongoUserImpl 单元测试，需要 MongoDB 可用"""

    def setUp(self) -> None:
        super().setUp()
        self.impl = MongoUserImpl()
        # 记录测试中创建的账号，便于 tearDown 清理
        self._test_accounts: list[str] = []

    def tearDown(self) -> None:
        # 清理所有测试数据
        for account in self._test_accounts:
            self.impl.delete_user(account)
        super().tearDown()

    # ---- insert_or_update_user ----

    def test_insert_or_update_user_insert(self) -> None:
        """测试插入新用户"""
        account = _test_account("insert_test")
        self._test_accounts.append(account)

        data = {
            "account": account,
            "password": "test_password_123",
            "phone": "13800001111",
            "email": "test@example.com",
            "create_time": "2026-06-21 10:00:00",
        }
        ok, inserted_id, _ = self.impl.insert_or_update_user(data)

        self.assertTrue(ok)
        self.assertIsNotNone(inserted_id)

    def test_insert_or_update_user_update(self) -> None:
        """测试更新已存在的用户"""
        account = _test_account("update_test")
        self._test_accounts.append(account)

        # 先插入
        data = {
            "account": account,
            "password": "old_password",
            "phone": "13800002222",
            "email": "old@example.com",
        }
        self.impl.insert_or_update_user(data)

        # 再更新密码
        data["password"] = "new_password"
        data["phone"] = "13900003333"
        ok, ret_id, _ = self.impl.insert_or_update_user(data)

        self.assertTrue(ok)
        # 更新已存在记录时 upserted_id 为 None
        self.assertIsNone(ret_id)

        # 验证内容已更新
        ok2, results = self.impl.query_user_by_account(account)
        self.assertTrue(ok2)
        self.assertIsNotNone(results)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["password"], "new_password")
        self.assertEqual(results[0]["phone"], "13900003333")

    def test_insert_or_update_empty_account(self) -> None:
        """测试空账号时返回失败"""
        ok, ret_id, _ = self.impl.insert_or_update_user({"account": "", "password": "pwd"})
        self.assertFalse(ok)
        self.assertIsNone(ret_id)

    def test_insert_or_update_user_insert_only_duplicate(self) -> None:
        """测试 insert_only 模式 — 账号已存在时返回 duplicate_account 错误"""
        account = _test_account("insert_only_dup")
        self._test_accounts.append(account)

        data = {
            "account": account,
            "password": "pwd",
            "phone": "13800005555",
            "email": "dup@example.com",
        }
        # 第一次插入成功
        ok, inserted_id, err = self.impl.insert_or_update_user(data, insert_only=True)
        self.assertTrue(ok)
        self.assertIsNotNone(inserted_id)
        self.assertIsNone(err)

        # 第二次插入相同账号应失败
        data["email"] = "dup2@example.com"
        ok2, inserted_id2, err2 = self.impl.insert_or_update_user(data, insert_only=True)
        self.assertFalse(ok2)
        self.assertIsNone(inserted_id2)
        self.assertEqual(err2, "duplicate_account")

    # ---- query_user_by_account ----

    def test_query_user_by_account_found(self) -> None:
        """测试按账号查询 — 存在"""
        account = _test_account("query_found")
        self._test_accounts.append(account)

        self.impl.insert_or_update_user({
            "account": account,
            "password": "pwd",
            "phone": "13800004444",
            "email": "found@example.com",
        })

        ok, results = self.impl.query_user_by_account(account)
        self.assertTrue(ok)
        self.assertIsNotNone(results)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["account"], account)
        self.assertEqual(results[0]["email"], "found@example.com")

    def test_query_user_by_account_not_found(self) -> None:
        """测试按账号查询 — 不存在"""
        ok, results = self.impl.query_user_by_account("nonexistent_account_xyz")
        self.assertTrue(ok)
        self.assertIsNone(results)

    # ---- query_user_by_id ----

    def test_query_user_by_id(self) -> None:
        """测试按 _id 查询用户"""
        account = _test_account("query_by_id")
        self._test_accounts.append(account)

        ok, inserted_id, _ = self.impl.insert_or_update_user({
            "account": account,
            "password": "pwd",
            "email": "id_test@example.com",
        })
        self.assertTrue(ok)
        self.assertIsNotNone(inserted_id)

        ok2, results = self.impl.query_user_by_id(inserted_id)
        self.assertTrue(ok2)
        self.assertIsNotNone(results)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["account"], account)

    # ---- delete_user ----

    def test_delete_user(self) -> None:
        """测试删除用户"""
        account = _test_account("delete_test")
        self._test_accounts.append(account)

        self.impl.insert_or_update_user({
            "account": account,
            "password": "pwd",
        })

        # 确认存在
        ok, results = self.impl.query_user_by_account(account)
        self.assertTrue(ok)
        self.assertIsNotNone(results)

        # 删除
        deleted = self.impl.delete_user(account)
        self.assertTrue(deleted)

        # 确认已删除（手动清理标记，避免 tearDown 重复删除报错）
        self._test_accounts.remove(account)

        ok2, results2 = self.impl.query_user_by_account(account)
        self.assertTrue(ok2)
        self.assertIsNone(results2)


if __name__ == "__main__":
    unittest.main()
