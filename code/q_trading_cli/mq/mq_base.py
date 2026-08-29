"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-06-23 13:50:00
Description: MQTT 配置基类，从 stock.cfg 读取 MQTT 连接参数
"""

# coding="utf8"

import logging
import os
from configparser import ConfigParser, NoSectionError


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
        from utils.tools import resource_path
        cp = ConfigParser()
        cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
        try:
            # 所有键均带 fallback，避免缺失任一配置键导致初始化直接失败
            self.mqtt_host: str = cp.get("mqtt", "host", fallback="127.0.0.1")
            self.mqtt_port: int = int(cp.get("mqtt", "port", fallback="1883"))
            # 在配置的 client_id 基础上追加 PID，避免多进程/多实例 client_id 冲突
            base_client_id: str = cp.get("mqtt", "client_id", fallback="q_share_cli")
            self.mqtt_client_id: str = f"{base_client_id}_{os.getpid()}"
            self.mqtt_username: str = cp.get("mqtt", "username", fallback="")
            self.mqtt_password: str = cp.get("mqtt", "password", fallback="")
            self.mqtt_need_tls: bool = cp.getboolean("mqtt", "need_tls", fallback=False)
            self.mqtt_cert_file: str = cp.get("mqtt", "cert_file", fallback="")
            self.mqtt_key_file: str = cp.get("mqtt", "key_file", fallback="")
            self.mqtt_ca_file: str = cp.get("mqtt", "ca_file", fallback="")
        except NoSectionError as err:
            self.logger.error("not find section: %s", err)
