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


class TestStockFetchPoolDistribution(unittest.TestCase):
    def setUp(self) -> None:
        self.pool_impl = MongoStockPoolImpl()
        self.agent_impl = MongoDataAgentImpl()
        self.agent_stocks_impl = MongoDataAgentPoolStocksImpl()
        self.stock_fetch = StockFetch()
        self.pool_name = "test_pool_distribution"
        self.agent_names = ["agent_dist_a", "agent_dist_b", "agent_dist_c"]
        self.stock_codes = ["000001", "000002", "000003", "000004"]
        self.cleanup_agent_stocks()
        self.cleanup_agents()
        self.cleanup_pool()

    def tearDown(self) -> None:
        self.cleanup_agent_stocks()
        self.cleanup_agents()
        self.cleanup_pool()
        return super().tearDown()

    def cleanup_pool(self) -> None:
        try:
            self.pool_impl.delete_stock_pool(self.pool_name)
        except Exception:
            pass

    def cleanup_agents(self) -> None:
        res, agents = self.agent_impl.query_data_agents()
        if res and agents:
            for agent in agents:
                name = agent.get("agent_name", "")
                if name in self.agent_names:
                    agent_id = str(agent.get("_id", ""))
                    if agent_id:
                        self.agent_impl.delete_data_agent(agent_id)

    def cleanup_agent_stocks(self) -> None:
        for name in self.agent_names:
            try:
                self.agent_stocks_impl.delete_data_agent_pool_stocks_by_agent_name(name)
            except Exception:
                pass

    def test_distribute_pool_stocks_to_data_agents(self):
        # 创建测试数据代理
        for agent_name in self.agent_names:
            ok, _ = self.agent_impl.add_data_agent(
                {
                    "agent_name": agent_name,
                    "description": "测试代理",
                    "is_online": False,
                    "online_time": "",
                }
            )
            self.assertTrue(ok, f"添加 agent {agent_name} 失败")

        # 创建股票池并添加股票
        ok, _ = self.pool_impl.insert_or_update_stock_pool(
            {"name": self.pool_name, "description": "测试分配池"}
        )
        self.assertTrue(ok, "创建测试股票池失败")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        records = [
            {"pool_name": self.pool_name, "code": code, "add_time": now}
            for code in self.stock_codes
        ]
        ok = self.pool_impl.add_stocks_to_pool(records)
        self.assertTrue(ok, "向股票池添加股票失败")
        success = asyncio.run(self.stock_fetch.distribute_pool_stocks_to_data_agents())
        res = self.agent_stocks_impl.delete_data_agent_pool_stocks_by_pool_name(self.pool_name)
        print(f"清理股票池分配记录结果: {res}")


if __name__ == '__main__':
    unittest.main()
