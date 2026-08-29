import json
import os
import sys
import unittest

from stock_fetch.stock_fetch import StockFetch

# Ensure the project root is on sys.path when tests are run from the test directory.
sys.path.append(os.getcwd())

class TestStockFetchReal(unittest.TestCase):
    def setUp(self) -> None:
        return super().setUp()

    def tearDown(self) -> None:
        return super().tearDown()

    def test_stock_hq_task(self):
        stock_fetch = StockFetch()
        stock_fetch.real_time_hq_sync_task()
    
if __name__ == '__main__':
    unittest.main()            