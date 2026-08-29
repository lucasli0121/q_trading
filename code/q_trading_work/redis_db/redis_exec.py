"""
Author: liguoqiang
Date: 2023-03-31 20:55:56
LastEditors: liguoqiang
LastEditTime: 2026-08-17
Description: Redis 执行器，负责股票实时行情等数据的缓存读写
    从 cfg/stock.cfg [redis] 节读取连接配置；
    作为全局对象通过 AppContext().redis_exec 访问；
    未启用或连接失败时所有操作安全降级，不影响主流程。

    注意：本地包名使用 redis_db 而不是 redis，避免与 pip 安装的
    redis 客户端库同名冲突（同名时本地目录会被 pip 包遮蔽，无法导入）。
"""

# coding="utf8"

import logging
import ssl
from configparser import ConfigParser, NoSectionError
from typing import Any, cast

import redis

from utils.tools import resource_path


class RedisExec:
    """Redis 执行器（全局对象，通过 AppContext().redis_exec 访问）。

    连接参数从 cfg/stock.cfg [redis] 节读取：
        enable=true   是否启用 Redis，未启用时所有操作直接返回默认值
        host / port / password / db   连接参数
        enable_ssl / cert / key / ca  是否启用 TLS 证书认证
    """

    # 默认过期时间（秒）：12 小时
    DEFAULT_EXPIRE: int = 12 * 60 * 60

    def __init__(self) -> None:
        """初始化：读取 [redis] 配置，启用时创建连接池与客户端。"""
        self.logger = logging.getLogger(__name__)
        self.enabled: bool = False  # Redis 是否启用（来自配置文件）
        self.redis_host: str = "127.0.0.1"  # Redis 服务器地址
        self.redis_port: int = 6379  # Redis 服务端口
        self.redis_password: str = ""  # Redis 密码
        self.redis_db: int = 0  # Redis 数据库编号
        self.redis_ssl: bool = False  # 是否启用 TLS
        self.redis_cert: str = ""  # TLS 客户端证书路径
        self.redis_key: str = ""  # TLS 客户端私钥路径
        self.redis_ca: str = ""  # TLS CA 证书路径
        self.pool: redis.ConnectionPool | None = None  # 连接池
        self._r: redis.StrictRedis | None = None  # Redis 客户端
        self._load_config()
        if not self.enabled:
            self.logger.info(
                "Redis 未启用（cfg/stock.cfg [redis] enable != true），读写操作安全降级"
            )
            return
        try:
            self.pool = self._build_pool()
            self._r = redis.StrictRedis(connection_pool=self.pool)
        except Exception as err:
            self.logger.error("Redis 连接池初始化失败: %s", err)
            self.enabled = False
            self.pool = None
            self._r = None

    def _load_config(self) -> None:
        """从 cfg/stock.cfg [redis] 节读取连接配置。"""
        try:
            cp = ConfigParser()
            cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
            if not cp.has_section("redis"):
                self.logger.warning("配置文件缺少 [redis] 节，Redis 未启用")
                return
            self.enabled = cp.getboolean("redis", "enable", fallback=False)
            self.redis_host = cp.get("redis", "host", fallback="127.0.0.1")
            self.redis_port = cp.getint("redis", "port", fallback=6379)
            self.redis_password = cp.get("redis", "password", fallback="")
            self.redis_db = cp.getint("redis", "db", fallback=0)
            self.redis_ssl = cp.getboolean("redis", "enable_ssl", fallback=False)
            self.redis_cert = cp.get("redis", "cert", fallback="")
            self.redis_key = cp.get("redis", "key", fallback="")
            self.redis_ca = cp.get("redis", "ca", fallback="")
        except (NoSectionError, ValueError) as err:
            self.logger.error("读取 Redis 配置失败: %s", err)

    def _build_pool(self) -> redis.ConnectionPool:
        """按配置构建连接池；启用 TLS 时使用 SSLConnection 证书认证。

        socket_connect_timeout / socket_timeout 用于在 Redis 不可用时快速失败，
        避免连接阻塞主流程。
        """
        common: dict[str, Any] = {
            "host": self.redis_host,
            "port": self.redis_port,
            "password": self.redis_password or None,
            "db": self.redis_db,
            "decode_responses": True,
            "socket_connect_timeout": 3,
            "socket_timeout": 5,
        }
        if self.redis_ssl:
            return redis.ConnectionPool(
                connection_class=redis.SSLConnection,
                ssl_cert_reqs=ssl.CERT_REQUIRED,
                ssl_ca_certs=self.redis_ca,
                ssl_certfile=self.redis_cert,
                ssl_keyfile=self.redis_key,
                **common,
            )
        return redis.ConnectionPool(**common)

    def __del__(self) -> None:
        """析构时断开连接池。"""
        pool: redis.ConnectionPool | None = getattr(self, "pool", None)
        if pool is not None:
            try:
                pool.disconnect()
                self.logger.info("redis closed")
            except Exception:
                pass

    def _client(self) -> redis.StrictRedis | None:
        """返回可用的 Redis 客户端；未启用或未初始化时返回 None。

        :return: Redis 客户端实例，不可用时返回 None
        """
        if not self.enabled or self._r is None:
            return None
        return self._r

    def push_value_to_rlist(self, key: str, jstr: str, ex: int = DEFAULT_EXPIRE) -> bool:
        """向 Redis 列表尾部追加一个 JSON 字符串，并设置过期时间。

        :param key: 列表 key
        :param jstr: JSON 字符串
        :param ex: 过期时间（秒），默认 12 小时
        :return: 是否写入成功
        """
        client: redis.StrictRedis | None = self._client()
        if client is None:
            return False
        try:
            client.rpush(key, jstr)
            client.expire(key, ex)
            return True
        except Exception as err:
            self.logger.error("push_value_to_rlist(%s) error: %s", key, err)
            return False

    def get_value_from_rlist(self, key: str, number: int, ifdel: bool) -> list[str] | None:
        """读取 Redis 列表中的元素（JSON 字符串列表）。

        :param key: 列表 key
        :param number: 获取数量，-1 表示全部
        :param ifdel: 是否删除已读取的元素
        :return: 元素列表；key 不存在返回空列表；读取异常返回 None
        """
        client: redis.StrictRedis | None = self._client()
        if client is None:
            return None
        result: list[str] = []
        try:
            if client.exists(key) == 1:
                if number < 0:
                    number = cast(int, client.llen(key))
                result = cast(list[str], client.lrange(key, 0, number - 1))
                if ifdel:
                    len_val: int = cast(int, client.llen(key))
                    if 0 <= number < len_val:
                        client.ltrim(key, number, -1)
                    else:
                        client.ltrim(key, 0, -1)
            return result
        except Exception as err:
            self.logger.error("get_value_from_rlist(%s) error: %s", key, err)
            return None

    def ltrim_rlist(self, key: str, start: int, stop: int) -> bool:
        """裁剪 Redis 列表，仅保留 [start, stop] 区间内的元素。

        :param key: 列表 key
        :param start: 保留区间起始下标（支持负数）
        :param stop: 保留区间结束下标（支持负数，-1 表示最后一个）
        :return: 是否裁剪成功
        """
        client: redis.StrictRedis | None = self._client()
        if client is None:
            return False
        try:
            client.ltrim(key, start, stop)
            return True
        except Exception as err:
            self.logger.error("ltrim_rlist(%s) error: %s", key, err)
            return False

    def delete_key(self, key: str) -> bool:
        """删除 Redis key。

        :param key: 待删除的 key
        :return: 是否删除成功
        """
        client: redis.StrictRedis | None = self._client()
        if client is None:
            return False
        try:
            client.delete(key)
            return True
        except Exception as err:
            self.logger.error("delete_key(%s) error: %s", key, err)
            return False

    def set_key_string(self, key: str, value: str, ex: int | None = DEFAULT_EXPIRE) -> bool:
        """设置字符串类型的 key-value。

        :param key: 字符串 key
        :param value: 字符串值
        :param ex: 过期时间（秒），None 表示永不过期，默认 12 小时
        :return: 是否设置成功
        """
        client: redis.StrictRedis | None = self._client()
        if client is None:
            return False
        try:
            if ex is None:
                return cast(bool, client.set(key, value))
            return cast(bool, client.setex(key, ex, value))
        except Exception as err:
            self.logger.error("set_key_string(%s) error: %s", key, err)
            return False

    def get_key_string(self, key: str, ifdel: bool = False) -> str | None:
        """获取字符串类型的 key 值。

        :param key: 字符串 key
        :param ifdel: 是否在读取后删除
        :return: 字符串值，key 不存在或读取异常返回 None
        """
        client: redis.StrictRedis | None = self._client()
        if client is None:
            return None
        value: str | None = None
        try:
            if client.exists(key) == 1:
                value = cast(str | None, client.get(key))
                if ifdel:
                    client.delete(key)
        except Exception as err:
            self.logger.error("get_key_string(%s) error: %s", key, err)
        return value

    def push_value_to_hash(self, key: str, field: str, value: str, ex: int = DEFAULT_EXPIRE) -> bool:
        """向 Redis hash 表中添加 field-value。

        :param key: hash key
        :param field: hash 字段名
        :param value: 字段值
        :param ex: 过期时间（秒），默认 12 小时
        :return: 是否写入成功
        """
        client: redis.StrictRedis | None = self._client()
        if client is None:
            return False
        try:
            client.hset(key, field, value)
            client.expire(key, ex)
            return True
        except Exception as err:
            self.logger.error("push_value_to_hash(%s) error: %s", key, err)
            return False

    def get_value_from_hash(self, key: str, field: str, ifdef: bool) -> str | None:
        """从 Redis hash 表中获取 field 对应的 value。

        :param key: hash key
        :param field: hash 字段名
        :param ifdef: 是否删除该字段
        :return: 字段值，不存在或读取异常返回 None
        """
        client: redis.StrictRedis | None = self._client()
        if client is None:
            return None
        try:
            result: str | None = cast(str | None, client.hget(key, field))
            if ifdef:
                client.hdel(key, field)
            return result
        except Exception as err:
            self.logger.error("get_value_from_hash(%s) error: %s", key, err)
            return None
