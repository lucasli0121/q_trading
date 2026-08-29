"""
Author: liguoqiang
Date: 2026-03-22 23:02:13
LastEditors: liguoqiang
LastEditTime: 2026-03-29 15:43:22
Description: 
"""

from configparser import ConfigParser
import logging
import logging.config
import os
import unittest
import yaml
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sync_to_qlib import SyncToQlib

def initLogger(configPath):
    if os.path.exists(configPath):
        with open(configPath, 'r', encoding="utf-8") as f:
            config = yaml.load(f, yaml.FullLoader)
            logging.config.dictConfig(config)
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s-%(name)s-%(lineno)s-%(levelname)s-%(message)s", filename="log/info.log", filemode="w")

class TestSyncToQlib(unittest.TestCase):

    def setUp(self):
        cp = ConfigParser()
        cp.read("sync.cfg")
        qlib_data = cp.get("qlib", "data_path")
        start_date = cp.get("qlib", "start_date")
        cfgname = cp.get("log", "config")
        #初始化日志
        initLogger(cfgname)
        logger = logging.getLogger(__name__)
        self.sync_obj = SyncToQlib(qlib_data, start_date)

    # def test_create_calendars_files(self):
    #     result = self.sync_obj.create_calendars_files()
    #     self.assertTrue(result)

    # def test_create_instruments_files(self):
    #     result = self.sync_obj.create_instruments_files()
    #     self.assertTrue(result)

    # def test_create_stock_files(self):
    #     result = self.sync_obj.create_stock_files()
    #     self.assertTrue(result)

    def test_convert_to_qlib_format(self):
        self.sync_obj.convert_to_qlib_format()
        self.assertTrue(True)
    # def test_create_all(self):
    #     result = self.sync_obj.create_all()
    #     self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()