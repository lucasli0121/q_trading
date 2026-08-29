import json
import os
import sys
import unittest

# Ensure the project root is on sys.path when tests are run from the test directory.
sys.path.append(os.getcwd())
from stock_fetch.tickflow_fetch.tickflow_proxy import TickFlowProxy

class TestTickFlow(unittest.TestCase):
    def setUp(self) -> None:
        return super().setUp()

    def tearDown(self) -> None:
        return super().tearDown()

    def test_stock_real_hq_task(self):
        tickflow_proxy = TickFlowProxy()
        df = tickflow_proxy.get_stocks_his_hq("600875", "2026-03-31", "2026-04-17")
        print(df)
if __name__ == '__main__':
    unittest.main()            