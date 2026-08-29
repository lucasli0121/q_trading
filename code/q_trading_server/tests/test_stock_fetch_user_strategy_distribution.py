import asyncio
import os
import sys
import unittest
from datetime import datetime

from db.mongo.mongo_data_agent_impl import MongoDataAgentImpl
from db.mongo.mongo_data_agent_pool_stocks_impl import MongoDataAgentPoolStocksImpl
from db.mongo.mongo_stock_pool_impl import MongoStockPoolImpl
from db.mongo.mongo_user_strategy_impl import MongoUserStrategyImpl
from db.mongo.mongo_workflow_service_impl import MongoWorkFlowServiceImpl
from db.mongo.mongo_workflow_service_user_strategy_impl import MongoWorkFlowServiceUserStrategyImpl
from stock_fetch.stock_fetch import StockFetch

# Ensure the project root is on sys.path when tests are run from the test directory.
sys.path.append(os.getcwd())


class TestStockFetchPoolDistribution(unittest.TestCase):
    def setUp(self) -> None:
        self.user_strategy_impl = MongoUserStrategyImpl()
        self.service_impl = MongoWorkFlowServiceImpl()
        self.service_user_strategy_impl = MongoWorkFlowServiceUserStrategyImpl()
        self.stock_fetch = StockFetch()
        

    def tearDown(self) -> None:
        return super().tearDown()

    def test_distribute_pool_stocks_to_data_agents(self):
        # 执行分配逻辑
        success = asyncio.run(self.stock_fetch.distribute_user_strategy_to_workflow())



if __name__ == '__main__':
    unittest.main()
