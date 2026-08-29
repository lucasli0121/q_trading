import json
import os
import sys
import unittest

from stock_fetch.stock_fetch import StockFetch

# Ensure the project root is on sys.path when tests are run from the test directory.
sys.path.append(os.getcwd())

class TestTickFlow(unittest.TestCase):
    def setUp(self) -> None:
        return super().setUp()

    def tearDown(self) -> None:
        return super().tearDown()

    def test_stock_hq_task(self):
        stock_fetch = StockFetch()
        stock_fetch.his_hq_sync_task()
    
    def test_get_his_hq(self):
        stock_fetch = StockFetch()
        start_date = "2026-06-01"
        end_date = "2026-07-30"
        stock_fetch.do_his_hq_sync_task(start_date, end_date)
if __name__ == '__main__':
    unittest.main()            