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

    def test_sync_industry_task(self):
        tickflow_proxy = TickFlowProxy()
        tickflow_proxy.industry_base_sync_task()
    
if __name__ == '__main__':
    unittest.main()            