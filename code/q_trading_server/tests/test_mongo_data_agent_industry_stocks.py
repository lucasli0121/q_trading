"""
Author: liguoqiang
Date: 2026-08-09
Description: MongoDataAgentIndustryStocksImpl 单元测试，需要 MongoDB 可用
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.getcwd())

from db.mongo.mongo_data_agent_industry_stocks_impl import MongoDataAgentIndustryStocksImpl

TEST_AGENT_1 = "test_agent_industry_001"
TEST_AGENT_2 = "test_agent_industry_002"
TEST_AGENT_3 = "test_agent_industry_003"
TEST_AGENTS = [TEST_AGENT_1, TEST_AGENT_2, TEST_AGENT_3]

TEST_INDUSTRY = "白酒"


class TestMongoDataAgentIndustryStocksImpl(unittest.TestCase):
    """MongoDataAgentIndustryStocksImpl 单元测试，需要 MongoDB 可用"""

    def setUp(self) -> None:
        super().setUp()
        self.impl = MongoDataAgentIndustryStocksImpl()
        # 清理可能残留的测试数据
        self._cleanup()

    def tearDown(self) -> None:
        # 清理所有测试数据
        self._cleanup()
        super().tearDown()

    def _cleanup(self) -> None:
        for agent_name in TEST_AGENTS:
            try:
                self.impl.delete_by_agent_name(agent_name)
            except Exception:
                pass

    def _test_data(self) -> dict[str, object]:
        return {
            "agent_name": TEST_AGENT_1,
            "stock_codes_industry": [
                {"000001": ["银行", TEST_INDUSTRY]},
                {"600519": [TEST_INDUSTRY]},
            ],
        }

    # ---- add ----

    def test_add(self) -> None:
        """测试新增数据代理行业分配记录"""
        ok, inserted_id = self.impl.add(self._test_data())
        self.assertTrue(ok)
        self.assertIsNotNone(inserted_id)

    # ---- query ----

    def test_query_by_id(self) -> None:
        """测试按 id 查询数据代理行业分配记录"""
        _, inserted_id = self.impl.add(self._test_data())
        self.assertIsNotNone(inserted_id)

        res, records = self.impl.query_by_id(inserted_id)
        self.assertTrue(res)
        self.assertIsNotNone(records)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["agent_name"], TEST_AGENT_1)

    def test_query_by_id_invalid(self) -> None:
        """测试按非法 id 查询返回失败"""
        res, records = self.impl.query_by_id("invalid_id_not_objectid")
        self.assertFalse(res)
        self.assertIsNone(records)

    def test_query_by_agent_name(self) -> None:
        """测试按 agent_name 查询数据代理行业分配记录"""
        self.impl.add(self._test_data())

        res, records = self.impl.query_by_agent_name(TEST_AGENT_1)
        self.assertTrue(res)
        self.assertIsNotNone(records)
        self.assertEqual(len(records), 1)
        stock_codes_industry = records[0].get("stock_codes_industry", [])
        self.assertEqual(len(stock_codes_industry), 2)

    def test_query_by_agent_name_not_found(self) -> None:
        """测试查询不存在的数据代理返回空结果"""
        empty_agent = "test_agent_industry_not_exist"
        res, records = self.impl.query_by_agent_name(empty_agent)
        self.assertTrue(res)
        self.assertIsNone(records)

    # ---- update ----

    def test_update(self) -> None:
        """测试更新数据代理行业分配记录"""
        _, inserted_id = self.impl.add(self._test_data())
        self.assertIsNotNone(inserted_id)

        new_data = {
            "stock_codes_industry": [{"000858": ["银行"]}],
        }
        ok = self.impl.update(inserted_id, new_data)
        self.assertTrue(ok)

        res, records = self.impl.query_by_id(inserted_id)
        self.assertTrue(res)
        self.assertIsNotNone(records)
        self.assertEqual(records[0]["stock_codes_industry"], [{"000858": ["银行"]}])

    def test_update_not_exist(self) -> None:
        """测试更新不存在的记录返回失败"""
        ok = self.impl.update("000000000000000000000000", {"stock_codes_industry": []})
        self.assertFalse(ok)

    # ---- upsert ----

    def test_upsert_insert(self) -> None:
        """测试 upsert 新增记录"""
        ok, inserted_id = self.impl.upsert(self._test_data())
        self.assertTrue(ok)
        self.assertIsNotNone(inserted_id)

    def test_upsert_update(self) -> None:
        """测试 upsert 更新已存在记录（同 agent_name）"""
        _, first_id = self.impl.upsert(self._test_data())
        self.assertIsNotNone(first_id)

        update_data = {
            "agent_name": TEST_AGENT_1,
            "stock_codes_industry": [{"300750": ["新能源"]}],
        }
        ok, ret_id = self.impl.upsert(update_data)
        self.assertTrue(ok)

        res, records = self.impl.query_by_agent_name(TEST_AGENT_1)
        self.assertTrue(res)
        self.assertIsNotNone(records)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["stock_codes_industry"], [{"300750": ["新能源"]}])
        # 不新增记录，返回原记录的 id
        if ret_id:
            self.assertEqual(str(records[0].get("_id", "")), ret_id)

    def test_upsert_empty_agent_name(self) -> None:
        """测试 upsert 空 agent_name 返回失败"""
        ok, ret_id = self.impl.upsert({"agent_name": "", "stock_codes_industry": []})
        self.assertFalse(ok)
        self.assertIsNone(ret_id)

    # ---- delete ----

    def test_delete_by_id(self) -> None:
        """测试按 id 删除数据代理行业分配记录"""
        _, inserted_id = self.impl.add(self._test_data())
        self.assertIsNotNone(inserted_id)

        ok = self.impl.delete(inserted_id)
        self.assertTrue(ok)

        res, records = self.impl.query_by_id(inserted_id)
        self.assertTrue(res)
        self.assertIsNone(records)

    def test_delete_by_agent_name(self) -> None:
        """测试按 agent_name 删除数据代理行业分配记录"""
        self.impl.add(self._test_data())
        self.impl.add({
            "agent_name": TEST_AGENT_2,
            "stock_codes_industry": [{"000002": ["房地产"]}],
        })

        ok = self.impl.delete_by_agent_name(TEST_AGENT_1)
        self.assertTrue(ok)

        res, records = self.impl.query_by_agent_name(TEST_AGENT_1)
        self.assertTrue(res)
        self.assertIsNone(records)
        # 不影响其他 agent 的记录
        res2, records2 = self.impl.query_by_agent_name(TEST_AGENT_2)
        self.assertTrue(res2)
        self.assertIsNotNone(records2)

    def test_delete_by_agent_name_with_stock_code_match(self) -> None:
        """测试按 agent_name + stock_code 删除（命中则整条删除）"""
        self.impl.add(self._test_data())

        ok = self.impl.delete_by_agent_name(TEST_AGENT_1, stock_code="000001")
        self.assertTrue(ok)

        res, records = self.impl.query_by_agent_name(TEST_AGENT_1)
        self.assertTrue(res)
        self.assertIsNone(records)

    def test_delete_by_agent_name_with_stock_code_no_match(self) -> None:
        """测试按 agent_name + stock_code 删除（未命中则不删除）"""
        self.impl.add(self._test_data())

        ok = self.impl.delete_by_agent_name(TEST_AGENT_1, stock_code="300750")
        self.assertFalse(ok)

        res, records = self.impl.query_by_agent_name(TEST_AGENT_1)
        self.assertTrue(res)
        self.assertIsNotNone(records)

    def test_delete_by_agent_name_empty(self) -> None:
        """测试空 agent_name 删除返回失败"""
        ok = self.impl.delete_by_agent_name("")
        self.assertFalse(ok)

    # ---- delete_by_industry ----

    def test_delete_by_industry_removes_industry(self) -> None:
        """测试按行业删除：行业从记录中被移除，同时删除其余行业的记录"""
        self.impl.add(self._test_data())

        self.impl.delete_by_industry(TEST_INDUSTRY)

        res, records = self.impl.query_by_agent_name(TEST_AGENT_1)
        self.assertTrue(res)
        self.assertIsNotNone(records)
        self.assertEqual(len(records), 1)
        stock_codes_industry = records[0].get("stock_codes_industry", [])
        for item in stock_codes_industry:
            for industries in item.values():
                self.assertNotIn(TEST_INDUSTRY, industries)
        # 移除白酒后，600519 的行业列表为空，应被过滤掉；000001 保留银行
        self.assertEqual(stock_codes_industry, [{"000001": ["银行"]}])

    def test_delete_by_industry_only_industry(self) -> None:
        """测试按行业删除：记录只包含该行业时，行业列表被清空但记录保留"""
        self.impl.add({
            "agent_name": TEST_AGENT_2,
            "stock_codes_industry": [{"600519": [TEST_INDUSTRY]}],
        })

        self.impl.delete_by_industry(TEST_INDUSTRY)

        res, records = self.impl.query_by_agent_name(TEST_AGENT_2)
        self.assertTrue(res)
        self.assertIsNotNone(records)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].get("stock_codes_industry", []), [])

    def test_delete_by_industry_not_exist(self) -> None:
        """测试删除不存在的行业不影响现有记录"""
        self.impl.add(self._test_data())

        self.impl.delete_by_industry("不存在行业")

        res, records = self.impl.query_by_agent_name(TEST_AGENT_1)
        self.assertTrue(res)
        self.assertIsNotNone(records)
        self.assertEqual(records[0].get("stock_codes_industry"), self._test_data()["stock_codes_industry"])

    def test_delete_by_industry_empty(self) -> None:
        """测试空行业名删除返回失败"""
        self.impl.add(self._test_data())

        ok = self.impl.delete_by_industry("")
        self.assertFalse(ok)

        res, records = self.impl.query_by_agent_name(TEST_AGENT_1)
        self.assertTrue(res)
        self.assertIsNotNone(records)


if __name__ == "__main__":
    unittest.main()
