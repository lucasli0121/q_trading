"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-06-23 13:50:00
Description: MQTT 配置基类，从 stock.cfg 读取 MQTT 连接参数
"""

# coding="utf8"

import os
from configparser import NoSectionError
import logging
from utils.config import load_stock_cfg, stock_instance_name


class MqBaseImpl:
    """MQTT 配置基类，从当前实例的配置文件读取 [mqtt] 节配置

    client_id 由「配置前缀 + 实例名 + 进程 PID」组成，确保多进程、多容器环境下
    client_id 唯一，避免 MQTT broker 因 client_id 冲突而反复断开连接
    （MQTT_ERR_CONN_LOST 循环）。
    """

    # 重连参数
    RECONNECT_MIN_DELAY: int = 2  # 最小重连间隔（秒）
    RECONNECT_MAX_DELAY: int = 120  # 最大重连间隔（秒）

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        cp = load_stock_cfg()
        try:
            self.mqtt_host: str = cp.get("mqtt", "host")
            self.mqtt_port: int = int(cp.get("mqtt", "port"))
            # client_id 只拼 PID 时，各实例在独立容器内的 PID 往往同为 1，
            # 会算出相同 client_id，broker 随即踢掉先连上的实例，形成反复重连。
            # 故改为「配置前缀 + 实例名 + PID」，实例名取自 [data_proxy] name。
            base_client_id: str = cp.get("mqtt", "client_id")
            instance_name: str = stock_instance_name(cp)
            self.mqtt_client_id: str = f"{base_client_id}_{instance_name}_{os.getpid()}"
            self.mqtt_username: str = cp.get("mqtt", "username")
            self.mqtt_password: str = cp.get("mqtt", "password")
            self.mqtt_need_tls: bool = cp.get("mqtt", "need_tls") == "true"
            self.mqtt_cert_file: str = cp.get("mqtt", "cert_file")
            self.mqtt_key_file: str = cp.get("mqtt", "key_file")
            self.mqtt_ca_file: str = cp.get("mqtt", "ca_file")
        except NoSectionError as err:
            self.logger.error("not find section: %s", err)
