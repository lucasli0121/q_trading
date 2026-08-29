"""
Author: liguoqiang
Date: 2026-08-17
Description: RedisExec 单元测试
    通过 mock StrictRedis 客户端验证配置加载、列表/字符串/hash 读写、
    列表裁剪与删除逻辑，不依赖真实 Redis 服务。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from redis_db.redis_exec import RedisExec


class TestRedisExec(unittest.TestCase):
    """RedisExec 单元测试。"""

    def setUp(self) -> None:
        """构造 RedisExec 并替换为 mock 客户端，避免真实连接。"""
        self.exec: RedisExec = RedisExec()
        self.exec.enabled = True
        self.exec._r = MagicMock()

    def test_load_config_from_cfg(self) -> None:
        """连接配置从 cfg/stock.cfg [redis] 节加载。"""
        self.assertTrue(self.exec.enabled)
        self.assertEqual(self.exec.redis_host, "192.168.1.63")
        self.assertEqual(self.exec.redis_port, 16379)
        self.assertEqual(self.exec.redis_db, 0)
        self.assertFalse(self.exec.redis_ssl)

    def test_config_without_redis_section(self) -> None:
        """配置文件缺少 [redis] 节时 Redis 未启用。"""
        with patch("redis_db.redis_exec.ConfigParser.read", return_value=[]):
            exec_obj = RedisExec()
        self.assertFalse(exec_obj.enabled)

    def test_push_value_to_rlist(self) -> None:
        """push 向列表写入 JSON 字符串并设置过期时间。"""
        self.assertTrue(self.exec.push_value_to_rlist("key1", '{"a": 1}', ex=100))
        self.exec._r.rpush.assert_called_once_with("key1", '{"a": 1}')
        self.exec._r.expire.assert_called_once_with("key1", 100)

    def test_get_value_from_rlist(self) -> None:
        """get 读取列表元素，number=-1 读取全部。"""
        self.exec._r.exists.return_value = 1
        self.exec._r.llen.return_value = 2
        self.exec._r.lrange.return_value = ['{"a": 1}', '{"b": 2}']
        result: list[str] | None = self.exec.get_value_from_rlist("key1", -1, False)
        self.assertEqual(result, ['{"a": 1}', '{"b": 2}'])

    def test_get_value_from_rlist_missing_key(self) -> None:
        """key 不存在时返回空列表。"""
        self.exec._r.exists.return_value = 0
        self.assertEqual(self.exec.get_value_from_rlist("key1", -1, False), [])

    def test_get_value_from_rlist_error_returns_none(self) -> None:
        """读取异常时返回 None（用于与空列表区分）。"""
        self.exec._r.exists.side_effect = Exception("boom")
        self.assertIsNone(self.exec.get_value_from_rlist("key1", -1, False))

    def test_ltrim_rlist(self) -> None:
        """ltrim 裁剪列表只保留最后 N 条。"""
        self.assertTrue(self.exec.ltrim_rlist("key1", -10, -1))
        self.exec._r.ltrim.assert_called_once_with("key1", -10, -1)

    def test_delete_key(self) -> None:
        """delete_key 删除 key。"""
        self.assertTrue(self.exec.delete_key("key1"))
        self.exec._r.delete.assert_called_once_with("key1")

    def test_set_and_get_key_string(self) -> None:
        """字符串 set/get 读写。"""
        self.assertTrue(self.exec.set_key_string("key1", "value1", ex=60))
        self.exec._r.setex.assert_called_once_with("key1", 60, "value1")
        self.exec._r.exists.return_value = 1
        self.exec._r.get.return_value = "value1"
        self.assertEqual(self.exec.get_key_string("key1"), "value1")

    def test_push_and_get_value_from_hash(self) -> None:
        """hash 表 field-value 读写。"""
        self.assertTrue(self.exec.push_value_to_hash("key1", "field1", "value1", ex=60))
        self.exec._r.hset.assert_called_once_with("key1", "field1", "value1")
        self.exec._r.hget.return_value = "value1"
        self.assertEqual(self.exec.get_value_from_hash("key1", "field1", False), "value1")

    def test_disabled_returns_default(self) -> None:
        """未启用时所有操作安全降级为默认值。"""
        self.exec.enabled = False
        self.assertFalse(self.exec.push_value_to_rlist("k", "{}"))
        self.assertIsNone(self.exec.get_value_from_rlist("k", -1, False))
        self.assertFalse(self.exec.set_key_string("k", "v"))
        self.assertIsNone(self.exec.get_key_string("k"))
        self.assertFalse(self.exec.push_value_to_hash("k", "f", "v"))
        self.assertIsNone(self.exec.get_value_from_hash("k", "f", False))
        self.assertFalse(self.exec.ltrim_rlist("k", 0, -1))
        self.assertFalse(self.exec.delete_key("k"))


if __name__ == "__main__":
    unittest.main()
