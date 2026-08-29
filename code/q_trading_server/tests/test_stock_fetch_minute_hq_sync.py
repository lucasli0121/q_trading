from datetime import datetime, timedelta
import json
import os
import sys
import unittest

from stock_fetch.stock_fetch import StockFetch

# Ensure the project root is on sys.path when tests are run from the test directory.
sys.path.append(os.getcwd())

class TestStockFetchMinute(unittest.TestCase):
    def setUp(self) -> None:
        return super().setUp()

    def tearDown(self) -> None:
        return super().tearDown()

    def test_stock_all_code_hq_task(self):
        stock_fetch = StockFetch()
        now = datetime.strptime("2026-07-16 09:30", "%Y-%m-%d %H:%M")
        for i in range(0, 90):
            stock_fetch.do_minute_hq_sync_with_time([], now)
            now += timedelta(minutes=1)
            
        now = datetime.strptime("2026-07-16 13:00", "%Y-%m-%d %H:%M")
        for i in range(0, 120):
            stock_fetch.do_minute_hq_sync_with_time([], now)
            now += timedelta(minutes=1)

    # def test_stock_one_code_hq(self):
    #     stock_fetch = StockFetch()
    #     now = datetime.strptime("2026-07-17 09:30", "%Y-%m-%d %H:%M")
    #     for i in range(0, 90):
    #         stock_fetch.do_minute_hq_sync_with_time(["002281.SZ"], now)
    #         now += timedelta(minutes=1)
if __name__ == '__main__':
    unittest.main()            