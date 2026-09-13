"""
Author: liguoqiang
Date: 2026-08-02
Description: 回测命令行入口测试 —— 参数解析、策略解析、配置构建、
    JSON 导出与主流程（引擎侧 mock，避免真实回测耗时）。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from mq import MqttTopic



class TestMqTopic(unittest.TestCase):
    def setUp(self) -> None:
        return super().setUp()
    def test_decode_minute_topic(self):
        topic = "q_share/stock/minute/6a58b9e928893aa08c3eb62c/603986/SH"
        pool_id, stock = MqttTopic.decode_stock_minute_topic(topic)
        print(f"pool_id:{pool_id}, stock:{stock}")
        topic2 = "q_share/stock/real_time/6a58b9e928893aa08c3eb62c/603986/SH"
        pool_id, stock = MqttTopic.decode_stock_realtime_topic(topic2)
        print(f"pool_id:{pool_id}, stock:{stock}")


if __name__ == "__main__":
    unittest.main()
