"""
Author: liguoqiang
Date: 2024-08-27 22:32:26
LastEditors: liguoqiang
LastEditTime: 2026-03-28 21:22:51
Description: 
"""
from configparser import ConfigParser
import logging
import logging.config
import os
import sys
import urllib3 as ulib
import yaml

from sync_to_qlib import SyncToQlib


def initLogger(configPath):
    if os.path.exists(configPath):
        with open(configPath, 'r', encoding="utf-8") as f:
            config = yaml.load(f, yaml.FullLoader)
            logging.config.dictConfig(config)
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s-%(name)s-%(lineno)s-%(levelname)s-%(message)s", filename="log/info.log", filemode="w")


if __name__ == '__main__':
    cp = ConfigParser()
    cp.read("sync.cfg")
    qlib_data = cp.get("qlib", "data_path")
    start_date = cp.get("qlib", "start_date")
    cfgname = cp.get("log", "config")
    #初始化日志
    initLogger(cfgname)
    logger = logging.getLogger(__name__)
    logger.info("start sync data from rhserver to qlib")
    sync_obj = SyncToQlib(qlib_data, start_date)
    sync_obj()