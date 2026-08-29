"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-06-23 13:50:00
Description: MQTT 配置基类，从 stock.cfg 读取 MQTT 连接参数
"""

# coding="utf8"

import os
from configparser import ConfigParser, NoSectionError
import logging


class MqBaseImpl:
    """MQTT 配置基类，负责从 cfg/stock.cfg 读取 [mqtt] 节配置

    client_id 会追加进程 PID，确保多进程环境下 client_id 唯一，避免
    MQTT broker 因 client_id 冲突而反复断开连接（MQTT_ERR_CONN_LOST 循环）。
    """

    # 重连参数
    RECONNECT_MIN_DELAY: int = 2  # 最小重连间隔（秒）
    RECONNECT_MAX_DELAY: int = 120  # 最大重连间隔（秒）

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        cp = ConfigParser()
        cp.read("cfg/stock.cfg")
        try:
            self.mqtt_host: str = cp.get("mqtt", "host")
            self.mqtt_port: int = int(cp.get("mqtt", "port"))
            # 在配置的 client_id 基础上追加 PID，避免多进程/多实例 client_id 冲突
            base_client_id: str = cp.get("mqtt", "client_id")
            self.mqtt_client_id: str = f"{base_client_id}_{os.getpid()}"
            self.mqtt_username: str = cp.get("mqtt", "username")
            self.mqtt_password: str = cp.get("mqtt", "password")
            self.mqtt_need_tls: bool = cp.get("mqtt", "need_tls") == "true"
            self.mqtt_cert_file: str = cp.get("mqtt", "cert_file")
            self.mqtt_key_file: str = cp.get("mqtt", "key_file")
            self.mqtt_ca_file: str = cp.get("mqtt", "ca_file")
        except NoSectionError as err:
            self.logger.error("not find section: %s", err)
