"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-06-21 20:00:00
Description: MQTT 模块单元测试
    - MqttTopic 常量和静态方法测试
    - MqttClient topic 通配符匹配测试
    - MQTT 连接/断开测试（需 broker）
    - MQTT 订阅/推送测试（需 broker）
    - TLS 配置标志测试
"""

import sys
import os
import time
import uuid
import unittest

curPath = os.getcwd()
sys.path.append(curPath)

from mq.mqtt_client import MqttClient, MqttTopic

# 测试用 topic 前缀，避免与正式环境冲突
TEST_TOPIC_PREFIX = "q_share/test"

# MQTT 连接等待时间（秒）
CONNECT_WAIT = 1.0
MESSAGE_WAIT = 0.6


def _make_test_client() -> MqttClient:
    """创建带有唯一 client_id 的测试用 MqttClient，避免多测试间 client_id 冲突"""
    client = MqttClient()
    client.mqtt_client_id = f"test_{uuid.uuid4().hex[:12]}"
    return client


class TestMqttTopic(unittest.TestCase):
    """MqttTopic 常量和静态方法测试（无需 broker）"""

    def test_stock_real_time_constant(self) -> None:
        """测试 STOCK_REAL_TIME 常量"""
        self.assertEqual(MqttTopic.STOCK_REAL_TIME, "q_share/stock/real_time")

    def test_stock_minute_constant(self) -> None:
        """测试 STOCK_MINUTE 常量"""
        self.assertEqual(MqttTopic.STOCK_MINUTE, "q_share/stock/minute")

    def test_stock_real_time_single_wildcard(self) -> None:
        """测试 STOCK_REAL_TIME_SINGLE 通配符常量"""
        self.assertEqual(MqttTopic.STOCK_REAL_TIME_SINGLE, "q_share/stock/real_time/+")

    def test_stock_real_time_all_wildcard(self) -> None:
        """测试 STOCK_REAL_TIME_ALL 通配符常量"""
        self.assertEqual(MqttTopic.STOCK_REAL_TIME_ALL, "q_share/stock/real_time/#")

    def test_stock_all_wildcard(self) -> None:
        """测试 STOCK_ALL 通配符常量"""
        self.assertEqual(MqttTopic.STOCK_ALL, "q_share/stock/#")

    def test_stock_real_time_code(self) -> None:
        """测试 stock_real_time_code 静态方法"""
        self.assertEqual(
            MqttTopic.stock_real_time_code("000001"),
            "q_share/stock/real_time/000001",
        )
        self.assertEqual(
            MqttTopic.stock_real_time_code("600519"),
            "q_share/stock/real_time/600519",
        )

    def test_stock_real_time_code_empty(self) -> None:
        """测试 stock_real_time_code 空代码"""
        result = MqttTopic.stock_real_time_code("")
        self.assertEqual(result, "q_share/stock/real_time/")


class TestTopicPatternMatching(unittest.TestCase):
    """MQTT topic 通配符匹配单元测试（无需 broker）"""

    def setUp(self) -> None:
        super().setUp()
        self.client = MqttClient()

    def tearDown(self) -> None:
        super().tearDown()

    def test_exact_match(self) -> None:
        """精确 topic 匹配"""
        self.assertTrue(
            self.client._match_topic(
                "q_share/stock/real_time", "q_share/stock/real_time"
            )
        )

    def test_exact_no_match(self) -> None:
        """精确 topic 不匹配"""
        self.assertFalse(
            self.client._match_topic(
                "q_share/stock/real_time/000001", "q_share/stock/real_time"
            )
        )

    def test_single_level_wildcard_match(self) -> None:
        """+ 通配符匹配单层"""
        self.assertTrue(
            self.client._match_topic(
                "q_share/stock/real_time/000001", "q_share/stock/real_time/+"
            )
        )
        self.assertTrue(
            self.client._match_topic(
                "q_share/stock/real_time/600519", "q_share/stock/real_time/+"
            )
        )

    def test_single_level_wildcard_no_match_parent(self) -> None:
        """+ 通配符不匹配父层级"""
        self.assertFalse(
            self.client._match_topic(
                "q_share/stock/real_time", "q_share/stock/real_time/+"
            )
        )

    def test_single_level_wildcard_no_match_multi(self) -> None:
        """+ 通配符不匹配多层级"""
        self.assertFalse(
            self.client._match_topic(
                "q_share/stock/real_time/000001/sub", "q_share/stock/real_time/+"
            )
        )

    def test_multi_level_wildcard_match_child(self) -> None:
        """# 通配符匹配子层级"""
        self.assertTrue(
            self.client._match_topic(
                "q_share/stock/real_time/000001", "q_share/stock/real_time/#"
            )
        )

    def test_multi_level_wildcard_match_multi(self) -> None:
        """# 通配符匹配多级子层级"""
        self.assertTrue(
            self.client._match_topic(
                "q_share/stock/real_time/000001/sub", "q_share/stock/real_time/#"
            )
        )

    def test_multi_level_wildcard_match_parent(self) -> None:
        """# 通配符匹配父层级自身"""
        self.assertTrue(
            self.client._match_topic(
                "q_share/stock/real_time", "q_share/stock/real_time/#"
            )
        )

    def test_stock_all_wildcard_match(self) -> None:
        """STOCK_ALL 通配测试"""
        self.assertTrue(
            self.client._match_topic("q_share/stock/real_time", MqttTopic.STOCK_ALL)
        )
        self.assertTrue(
            self.client._match_topic("q_share/stock/minute", MqttTopic.STOCK_ALL)
        )
        self.assertTrue(
            self.client._match_topic(
                "q_share/stock/real_time/000001", MqttTopic.STOCK_ALL
            )
        )
        self.assertTrue(
            self.client._match_topic("q_share/stock", MqttTopic.STOCK_ALL)
        )

    def test_stock_all_wildcard_no_match(self) -> None:
        """STOCK_ALL 不匹配无关 topic"""
        self.assertFalse(
            self.client._match_topic("q_share/industry/tech", MqttTopic.STOCK_ALL)
        )

    def test_hash_only_wildcard(self) -> None:
        """单独 # 匹配所有 topic"""
        self.assertTrue(self.client._match_topic("anything/at/all", "#"))
        self.assertTrue(self.client._match_topic("q_share/stock/real_time", "#"))

    def test_regex_cache_reuse(self) -> None:
        """正则缓存复用测试"""
        pattern = "q_share/stock/#"
        # 首次匹配
        self.assertTrue(
            self.client._match_topic("q_share/stock/real_time", pattern)
        )
        regex1 = self.client._topic_regex_cache.get(pattern)
        self.assertIsNotNone(regex1)

        # 再次匹配应复用缓存
        self.assertTrue(
            self.client._match_topic("q_share/stock/minute", pattern)
        )
        regex2 = self.client._topic_regex_cache.get(pattern)
        self.assertIs(regex1, regex2)


class TestMqttConfig(unittest.TestCase):
    """MQTT 配置读取测试（无需 broker）"""

    def test_config_values(self) -> None:
        """测试从 stock.cfg 读取的配置值"""
        client = _make_test_client()
        self.assertIsInstance(client.mqtt_host, str)
        self.assertGreater(len(client.mqtt_host), 0)
        self.assertIsInstance(client.mqtt_port, int)
        self.assertGreater(client.mqtt_port, 0)
        self.assertIsInstance(client.mqtt_client_id, str)
        self.assertGreater(len(client.mqtt_client_id), 0)
        self.assertIsInstance(client.mqtt_username, str)
        self.assertIsInstance(client.mqtt_password, str)

    def test_need_tls_flag(self) -> None:
        """测试 need_tls 配置标志类型"""
        client = _make_test_client()
        self.assertIsInstance(client.mqtt_need_tls, bool)

    def test_cert_paths(self) -> None:
        """测试证书路径配置"""
        client = _make_test_client()
        self.assertIsInstance(client.mqtt_cert_file, str)
        self.assertIsInstance(client.mqtt_key_file, str)
        self.assertIsInstance(client.mqtt_ca_file, str)


class TestMqttConnectDisconnect(unittest.TestCase):
    """MQTT 连接/断开集成测试（需 broker）"""

    def setUp(self) -> None:
        super().setUp()
        self.client = _make_test_client()

    def tearDown(self) -> None:
        if self.client.is_connected:
            self.client.disconnect()
        super().tearDown()

    def test_connect(self) -> None:
        """测试 MQTT 连接"""
        result = self.client.connect()
        self.assertTrue(result)
        # 等待异步连接建立
        time.sleep(CONNECT_WAIT)
        self.assertTrue(self.client.is_connected)

    def test_disconnect(self) -> None:
        """测试 MQTT 断开连接"""
        self.client.connect()
        time.sleep(CONNECT_WAIT)
        self.assertTrue(self.client.is_connected)

        self.client.disconnect()
        time.sleep(0.3)
        self.assertFalse(self.client.is_connected)

    def test_initial_not_connected(self) -> None:
        """测试初始状态未连接"""
        self.assertFalse(self.client.is_connected)


class TestMqttSubscribePublish(unittest.TestCase):
    """MQTT 订阅和推送集成测试（需 broker）"""

    def setUp(self) -> None:
        super().setUp()
        self.client = _make_test_client()
        self.client.connect()
        time.sleep(CONNECT_WAIT)
        self._received_messages: list[tuple[str, str]] = []

    def tearDown(self) -> None:
        self.client.unsubscribe(f"{TEST_TOPIC_PREFIX}/subscribe")
        self.client.unsubscribe(f"{TEST_TOPIC_PREFIX}/wildcard/#")
        if self.client.is_connected:
            self.client.disconnect()
        super().tearDown()

    def _on_message(self, topic: str, payload: str) -> None:
        """测试用消息回调"""
        self._received_messages.append((topic, payload))

    def test_subscribe_and_publish(self) -> None:
        """测试订阅后推送消息"""
        test_topic = f"{TEST_TOPIC_PREFIX}/subscribe"

        # 订阅
        result = self.client.subscribe(test_topic, self._on_message)
        self.assertTrue(result)
        time.sleep(0.3)

        # 推送
        payload = '{"test": "hello", "value": 123}'
        pub_result = self.client.publish(test_topic, payload)
        self.assertTrue(pub_result)
        # 等待消息到达
        time.sleep(MESSAGE_WAIT)

        # 验证收到
        self.assertEqual(len(self._received_messages), 1)
        self.assertEqual(self._received_messages[0][0], test_topic)
        self.assertEqual(self._received_messages[0][1], payload)

    def test_subscribe_wildcard_plus(self) -> None:
        """测试 + 通配符订阅"""
        pattern = f"{TEST_TOPIC_PREFIX}/wildcard/+"
        result = self.client.subscribe(pattern, self._on_message)
        self.assertTrue(result)
        time.sleep(0.3)

        # 推送匹配的 topic
        self.client.publish(f"{TEST_TOPIC_PREFIX}/wildcard/a", "msg_a")
        self.client.publish(f"{TEST_TOPIC_PREFIX}/wildcard/b", "msg_b")
        time.sleep(MESSAGE_WAIT)

        self.assertEqual(len(self._received_messages), 2)

        # 推送不匹配的 topic（多层级）
        self._received_messages.clear()
        self.client.publish(
            f"{TEST_TOPIC_PREFIX}/wildcard/a/sub", "msg_multi"
        )
        time.sleep(MESSAGE_WAIT)
        self.assertEqual(len(self._received_messages), 0)

    def test_subscribe_wildcard_hash(self) -> None:
        """测试 # 通配符订阅"""
        pattern = f"{TEST_TOPIC_PREFIX}/wildcard/#"
        result = self.client.subscribe(pattern, self._on_message)
        self.assertTrue(result)
        time.sleep(0.3)

        # 推送匹配的 topic（含多层级）
        self.client.publish(f"{TEST_TOPIC_PREFIX}/wildcard/x", "msg_x")
        self.client.publish(f"{TEST_TOPIC_PREFIX}/wildcard/x/y/z", "msg_xyz")
        time.sleep(MESSAGE_WAIT)

        self.assertEqual(len(self._received_messages), 2)

    def test_unsubscribe(self) -> None:
        """测试取消订阅"""
        test_topic = f"{TEST_TOPIC_PREFIX}/unsub"

        self.client.subscribe(test_topic, self._on_message)
        time.sleep(0.3)

        # 取消订阅
        result = self.client.unsubscribe(test_topic)
        self.assertTrue(result)
        time.sleep(0.3)

        # 推送消息
        self.client.publish(test_topic, "should_not_receive")
        time.sleep(MESSAGE_WAIT)

        # 不应收到
        self.assertEqual(len(self._received_messages), 0)

    def test_publish_not_connected(self) -> None:
        """测试未连接时推送返回 False"""
        self.client.disconnect()
        time.sleep(0.3)

        result = self.client.publish(f"{TEST_TOPIC_PREFIX}/nc", "test")
        self.assertFalse(result)

    def test_subscribe_not_connected(self) -> None:
        """测试未连接时订阅返回 False"""
        self.client.disconnect()
        time.sleep(0.3)

        result = self.client.subscribe(
            f"{TEST_TOPIC_PREFIX}/nc", self._on_message
        )
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
