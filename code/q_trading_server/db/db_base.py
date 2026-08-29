'''
Author: liguoqiang
Date: 2023-02-17 14:49:40
LastEditors: liguoqiang
LastEditTime: 2024-07-18 12:10:34
Description: 
'''
# coding="utf8"

from configparser import ConfigParser, NoSectionError
import logging


class DbBaseImpl:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        cp = ConfigParser()
        cp.read("cfg/stock.cfg")
        try:
            self.mongo_host = cp.get("mongo", "host")
            self.mongo_port = cp.get("mongo", "port")
            self.mongo_username = cp.get("mongo", "username")
            self.mongo_password = cp.get("mongo", "password")
            self.mongo_database = cp.get("mongo", "database")
            
            self.redis_enable = cp.get("redis", "enable")
            self.redis_host = cp.get("redis", "host")
            self.redis_port = cp.get("redis", "port")
            self.redis_password = cp.get("redis", "password")
            self.redis_db = cp.get("redis", "db")
            self.redis_ssl = cp.get("redis", "enable_ssl")
            self.redis_cert = cp.get("redis", "cert")
            self.redis_key = cp.get("redis", "key")
            self.redis_ca = cp.get("redis", "ca")
        except NoSectionError as err:
            self.logger.error("not find section:", err.message)