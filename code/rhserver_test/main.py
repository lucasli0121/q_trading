'''
Author: liguoqiang
Date: 2022-09-17 19:26:01
LastEditors: liguoqiang
LastEditTime: 2023-02-16 16:14:06
Description: 
'''
# coding="utf8"

from configparser import ConfigParser
import logging
import logging.config
import os
import yaml
import urllib3 as ulib

from data.stock_data import StockData
import mainwin

def initLogger(configPath):
    if os.path.exists(configPath):
        with open(configPath, 'r', encoding="utf-8") as f:
            config = yaml.load(f, yaml.FullLoader)
            logging.config.dictConfig(config)
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s-%(name)s-%(levelname)s-%(message)s", filename="log/test.log", filemode="w")

if __name__ == '__main__':
    cp = ConfigParser()
    cp.read("cfg/test.cfg")
    svrUrl = cp.get("server", "url")
    cert = cp.get("server", "cert")
    cfgname = cp.get("log", "config")
    #初始化日志
    initLogger(cfgname)
    logger = logging.getLogger(__name__)
    curPath = os.getcwd()
    ca_file=curPath + "/" + cert
    http = ulib.PoolManager(timeout=30.0, num_pools=5, cert_reqs='CERT_REQUIRED', ca_certs=ca_file)
    stockData = StockData(http, svrUrl)
    mainwin.startQApp(stockData)
