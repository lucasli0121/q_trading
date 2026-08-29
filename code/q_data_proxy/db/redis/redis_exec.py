'''
Author: liguoqiang
Date: 2023-03-31 20:55:56
LastEditors: liguoqiang
LastEditTime: 2024-07-21 10:28:18
Description: redis resposible for caching stock real-time data
    setting validity period time for each real-time data
'''
# coding="utf8"

import logging
import ssl
import redis
from db.db_base import DbBaseImpl
from typing import cast

class RedisExec(DbBaseImpl):
    _initialized = False

    def __init__(self):
        if not self._initialized:
            self._initialized = True
            super().__init__()
            self.logger = logging.getLogger(__name__)
            try:
                # read redis config
                # 支持证书认证
                if self.redis_ssl == "true":
                    self.pool = redis.ConnectionPool(
                        connection_class = redis.SSLConnection,
                        host=self.redis_host,
                        port=int(self.redis_port),
                        password=self.redis_password,
                        db=self.redis_db,
                        ssl_cert_reqs = ssl.CERT_REQUIRED,
                        ssl_ca_certs = self.redis_ca,
                        ssl_certfile = self.redis_cert,
                        ssl_keyfile = self.redis_key,
                        decode_responses=True)
                else:
                    self.pool = redis.ConnectionPool(
                        host=self.redis_host,
                        port=int(self.redis_port),
                        password=self.redis_password,
                        db=self.redis_db,
                        decode_responses=True)
                self._r = redis.StrictRedis(connection_pool=self.pool)
            except Exception as err:
                self.logger.error(err)
    
    def __del__(self):
        del self._r
        self.pool.disconnect()
        self.logger.info("redis closed")
    
    '''
    function: push_value_to_rlist
    description: 
    param {*} self
    param {*} jstr
    param {*} ex 过期时间，默认为12小时，单位秒
    return {*}
    '''    
    def push_value_to_rlist(self, key, jstr, ex=43200) -> bool:
        try:
            # current day string as list'name
            self._r.rpush(key, jstr)
            # 过期时间为推后一天
            self._r.expire(key, ex)
            return True
        except Exception as err:
            self.logger.error(err)
            return False
        
    '''
    function: get_value_from_rlist
    description: 
    param {*} self
    param {*} key
    param {*} number 获取的数量
    param {bool} ifdel 是否删除
    return {*}
    '''    
    def get_value_from_rlist(self, key, number: int, ifdel: bool) -> list|None:
        result: list = []
        if self._r.exists(key) == 1:
            if number < 0:
                number = cast(int, self._r.llen(key))
            result = cast(list, self._r.lrange(key, 0, number - 1))
            if ifdel:
                len_val = cast(int, self._r.llen(key))
                if number >= 0 and number < len_val:
                    self._r.ltrim(key, number, -1)
                else:
                    self._r.ltrim(key, 0, -1)
        return result
    
    '''
    function: set_key_string
    description: set a key value pair into redis
        过期时间默认为12小时
    param {*} self
    param {*} key
    param {*} value
    param {*} ex
    return {*}
    '''    
    def set_key_string(self, key, value, ex: int | None = 43200) -> bool:
        try:
            if ex is None:
                return cast(bool, self._r.set(key, value))
            return cast(bool, self._r.setex(key, ex, value))
        except Exception as err:
            self.logger.error(err)
            return False

    '''
    function: get_key_string
    description: get the value of a key from redis
    param {*} self
    param {*} key
    param {bool} ifdel
    return {*}
    '''    
    def get_key_string(self, key, ifdel: bool = False) -> str|None:
        value: str|None = None
        try:
            if self._r.exists(key) == 1:
                value = cast(str | None, self._r.get(key))
                if ifdel:
                    self._r.delete(key)
        except Exception as err:
            self.logger.error(err)
        return value

    '''
    function: push_value_to_hash
    description: 向hash表中添加field-value
    param {*} self
    param {*} key
    param {*} field
    param {*} value
    param {*} ex 过期时间，默认为12小时
    return {*}
    '''    
    def push_value_to_hash(self, key, field, value, ex=43200) -> bool:
        try:
            self._r.hset(key, field, value)
            self._r.expire(key, ex)
            return True
        except Exception as err:
            self.logger.error(err)
            return False
    '''
    function: get_value_from_hash
    description: 从hash表中获取field对应的value
    param {*} self
    param {*} key
    param {*} field
    param {*} ifdef 是否删除
    return {*}
    '''    
    def get_value_from_hash(self, key, field, ifdef: bool) -> str | None:
        try:
            result = cast(str | None, self._r.hget(key, field))
            if ifdef:
                self._r.hdel(key, field)
            return result
        except Exception as err:
            self.logger.error(err)
            return None