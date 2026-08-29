from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.getcwd())

from db.mongo.mongo_strategy_impl import MongoStrategyImpl

TEST_NAME_PREFIX = "test_q_share_"


def _test_strategy_name(base: str) -> str:
    """生成测试用策略名称，避免与真实数据冲突"""
    return f"{TEST_NAME_PREFIX}{base}"


class TestMongoStrategyImpl(unittest.TestCase):
    """MongoStrategyImpl 单元测试，需要 MongoDB 可用"""

    def setUp(self) -> None:
        super().setUp()
        self.impl = MongoStrategyImpl()
        # 记录测试中创建的策略名称，便于 tearDown 清理
        self._test_names: list[str] = []

    def tearDown(self) -> None:
        # 清理所有测试数据
        for name in self._test_names:
            self.impl.delete_strategy(name)
        super().tearDown()

    # ---- insert_or_update_strategy ----

    def test_insert_or_update_strategy_insert(self) -> None:
        """测试插入新策略"""
        name = _test_strategy_name("insert_test")
        self._test_names.append(name)

        data = {
            "name": name,
            "description": "插入测试策略",
        }
        ok, inserted_id, _ = self.impl.insert_or_update_strategy(data)

        self.assertTrue(ok)
        # upsert 已有记录时 inserted_id 为 None，首次插入应有值
        self.assertIsNotNone(inserted_id)

    def test_insert_or_update_strategy_update(self) -> None:
        """测试更新已存在的策略"""
        name = _test_strategy_name("update_test")
        self._test_names.append(name)

        # 先插入
        data = {
            "name": name,
            "description": "原始描述",
        }
        self.impl.insert_or_update_strategy(data)

        # 再更新
        data["description"] = "更新后的描述"
        ok, ret_id, _ = self.impl.insert_or_update_strategy(data)

        self.assertTrue(ok)
        # 更新已存在记录时 upserted_id 为 None
        self.assertIsNone(ret_id)

        # 验证内容已更新
        ok2, results = self.impl.query_strategy_by_name(name)
        self.assertTrue(ok2)
        self.assertIsNotNone(results)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["description"], "更新后的描述")

    def test_insert_or_update_empty_name(self) -> None:
        """测试名称为空时返回失败"""
        ok, ret_id, _ = self.impl.insert_or_update_strategy({"name": "", "description": ""})
        self.assertFalse(ok)
        self.assertIsNone(ret_id)

    def test_insert_or_update_insert_only_duplicate(self) -> None:
        """测试 insert_only 模式 — 同名策略已存在时返回 duplicate_name 错误"""
        name = _test_strategy_name("insert_only_dup")
        self._test_names.append(name)

        data = {
            "name": name,
            "description": "首次插入",
        }
        # 第一次插入成功
        ok, inserted_id, err = self.impl.insert_or_update_strategy(data, insert_only=True)
        self.assertTrue(ok)
        self.assertIsNotNone(inserted_id)
        self.assertIsNone(err)

        # 第二次插入同名策略应失败
        data["description"] = "重复插入"
        ok2, inserted_id2, err2 = self.impl.insert_or_update_strategy(data, insert_only=True)
        self.assertFalse(ok2)
        self.assertIsNone(inserted_id2)
        self.assertEqual(err2, "duplicate_name")

    # ---- bulk_upsert_strategy ----

    def test_bulk_upsert_strategy(self) -> None:
        """测试批量 upsert"""
        names = [
            _test_strategy_name("bulk_a"),
            _test_strategy_name("bulk_b"),
            _test_strategy_name("bulk_c"),
        ]
        self._test_names.extend(names)

        records = [
            {"name": names[0], "description": "批量-选股"},
            {"name": names[1], "description": "批量-盯盘"},
            {"name": names[2], "description": "批量-复盘"},
        ]
        ok = self.impl.bulk_upsert_strategy(records)
        self.assertTrue(ok)

        # 验证全部入库
        for record in records:
            ok2, results = self.impl.query_strategy_by_name(record["name"])
            self.assertTrue(ok2)
            self.assertIsNotNone(results)
            self.assertEqual(len(results), 1)

    # ---- query_strategy_by_name ----

    def test_query_strategy_by_name_found(self) -> None:
        """测试按名称查询 — 存在"""
        name = _test_strategy_name("query_by_name")
        self._test_names.append(name)

        self.impl.insert_or_update_strategy({
            "name": name,
            "description": "查询测试",
        })

        ok, results = self.impl.query_strategy_by_name(name)
        self.assertTrue(ok)
        self.assertIsNotNone(results)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], name)

    def test_query_strategy_by_name_not_found(self) -> None:
        """测试按名称查询 — 不存在"""
        ok, results = self.impl.query_strategy_by_name("nonexistent_strategy_name_xyz")
        self.assertTrue(ok)
        self.assertIsNone(results)

    # ---- query_all_strategies ----

    def test_query_all_strategies(self) -> None:
        """测试查询所有策略"""
        name = _test_strategy_name("all_query")
        self._test_names.append(name)

        self.impl.insert_or_update_strategy({
            "name": name,
            "description": "全查询测试",
        })

        ok, results = self.impl.query_all_strategies()
        self.assertTrue(ok)
        self.assertIsNotNone(results)
        self.assertGreaterEqual(len(results), 1)

    # ---- delete_strategy ----

    def test_delete_strategy(self) -> None:
        """测试删除策略"""
        name = _test_strategy_name("delete_test")
        self._test_names.append(name)

        self.impl.insert_or_update_strategy({
            "name": name,
            "description": "待删除策略",
        })

        # 确认存在
        ok, results = self.impl.query_strategy_by_name(name)
        self.assertTrue(ok)
        self.assertIsNotNone(results)

        # 删除
        deleted = self.impl.delete_strategy(name)
        self.assertTrue(deleted)

        # 确认已删除
        ok2, results2 = self.impl.query_strategy_by_name(name)
        self.assertTrue(ok2)
        self.assertIsNone(results2)


if __name__ == "__main__":
    unittest.main()
