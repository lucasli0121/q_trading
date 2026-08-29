import asyncio
import os
import sys
import unittest
from datetime import datetime

from db.mongo.mongo_data_agent_impl import MongoDataAgentImpl
from db.mongo.mongo_data_agent_pool_stocks_impl import MongoDataAgentPoolStocksImpl
from db.mongo.mongo_stock_pool_impl import MongoStockPoolImpl
from stock_fetch.stock_fetch import StockFetch

# Ensure the project root is on sys.path when tests are run from the test directory.
sys.path.append(os.getcwd())


class TestQueryPoolStocks(unittest.TestCase):
    def setUp(self) -> None:
        self.pool_impl = MongoStockPoolImpl()
        self.agent_pool_stocks_impl = MongoDataAgentPoolStocksImpl()

    def tearDown(self) -> None:
        return super().tearDown()

    def test_query_pool_stocks_returns_code_and_pool_name_mapping(self):
        res, records = self.pool_impl.query_all_pool_stocks()
        self.assertTrue(res)
        self.assertIsNotNone(records)
        self.assertIsInstance(records, list)
        for item in records:
            print(item)
            self.assertIsInstance(item, dict)
            self.assertIn("code", item)
            self.assertIsInstance(item["code"], str)
            self.assertIn("pool_name", item)
            self.assertIsInstance(item["pool_name"], list)
            for pool_name in item["pool_name"]:
                self.assertIsInstance(pool_name, str)


if __name__ == '__main__':
    unittest.main()
