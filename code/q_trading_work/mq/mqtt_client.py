"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-06-23 13:50:00
Description: MQTT 客户端实现
    - 定义 MQTT Topic 常量
    - 实现 MQTT 连接、订阅、推送方法
    - 支持 TLS 证书认证
    - 自动重连机制（带指数退避，避免 MQTT_ERR_CONN_LOST 循环）
"""

# coding="utf8"

import re
import ssl
import logging
from typing import Callable, Optional
import paho.mqtt.client as mqtt
from paho.mqtt.reasoncodes import ReasonCode
from mq.mq_base import MqBaseImpl


class MqttTopic:
    """MQTT Topic 定义

    通配符说明:
        + : 单级通配符，匹配一层 topic 层级，如 q_share/stock/real_time/+ 匹配任意单只股票
        # : 多级通配符，匹配任意层级，必须放在 topic 末尾，如 q_share/stock/# 匹配所有股票相关 topic
    """

    # 股票实时行情 topic（所有股票，精确匹配）
    STOCK_REAL_TIME: str = "q_share/stock/real_time"

    # 每分钟行情推送 topic
    STOCK_MINUTE_SINGLE: str = "q_share/stock/minute/+"
    # 匹配多级
    STOCK_MINUTE_ALL: str = "q_share/stock/minute/#"

    # 单只股票实时行情通配（订阅用：匹配任意单只股票代码）
    STOCK_REAL_TIME_SINGLE: str = "q_share/stock/real_time/+"

    # 所有实时行情通配（订阅用：匹配实时行情及子层级）
    STOCK_REAL_TIME_ALL: str = "q_share/stock/real_time/#"

    # 所有股票相关 topic 通配（订阅用）
    STOCK_ALL: str = "q_share/stock/#"

    # 交易信号发送topic
    STOCK_TRADING_SIGNAL = "q_share/trading_signal"

    @staticmethod
    def stock_real_time_code(code: str) -> str:
        """获取单只股票实时行情 topic

        :param code: 股票代码
        :return: topic 字符串，如 q_share/stock/real_time/000001
        """
        return f"q_share/stock/real_time/{code}"
    
    @staticmethod
    def stock_real_time_pool_code(pool_id: str, code: str) -> str:
        """获取股票池+股票实时行情 topic

        :param code: 股票代码
        :return: topic 字符串，如 q_share/stock/real_time/0001/000001
        """
        return f"q_share/stock/real_time/{pool_id}/{code}"
    @staticmethod
    def stock_minute_code(code: str) -> str:
        """获取分钟股票实时行情 topic

        :param code: 股票代码
        :return: topic 字符串，如 {STOCK_MINUTE_SINGLE}/000001
        """
        return f"q_share/stock/minute/{code}"
    @staticmethod
    def stock_minute_pool_code(pool_id: str, code: str) -> str:
        """获取分钟股票池+股票实时行情 topic

        :param code: 股票代码
        :return: topic 字符串，如 {STOCK_MINUTE_SINGLE}/pool_id/000001
        """
        return f"q_share/stock/minute/{pool_id}/{code}"



class MqttClient(MqBaseImpl):
    """MQTT 客户端，负责连接、订阅和推送消息

    使用方式:
        client = MqttClient()
        client.connect()
        client.subscribe("q_share/stock/real_time", callback_func)
        client.publish("q_share/stock/real_time/000001", payload_json)
    """

    # 默认 QoS 级别
    DEFAULT_QOS: int = 1

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self._client: Optional[mqtt.Client] = None
        self._is_connected: bool = False
        # 存储订阅关系: topic_pattern -> callback
        self._subscriptions: dict[str, Callable[[str, str], None]] = {}
        # 预编译的 topic 匹配正则缓存: topic_pattern -> compiled_regex
        self._topic_regex_cache: dict[str, re.Pattern] = {}

    @staticmethod
    def _topic_pattern_to_regex(pattern: str) -> re.Pattern:
        """将 MQTT topic 通配符模式转换为正则表达式

        MQTT 通配符规则:
            + : 匹配单个 topic 层级（不能包含 '/'），如 'a/+/c' 匹配 'a/b/c'、'a/x/c'
            # : 匹配多个 topic 层级（必须在末尾），如 'a/#' 匹配 'a'、'a/b'、'a/b/c/d'；
                单独的 '#' 匹配所有 topic

        :param pattern: 包含通配符的 MQTT topic 模式
        :return: 编译后的正则表达式对象
        """
        # 转义正则特殊字符（re.escape 会将 # 转为 \#，+ 转为 \+）
        escaped = re.escape(pattern)

        # 处理 # 多级通配（必须在末尾）
        if escaped == r"\#":
            # 单独的 # 匹配所有 topic
            return re.compile(r"^.*$")
        if escaped.endswith(r"/\#"):
            # pattern/# → 匹配 pattern 自身及 pattern 下任意层级
            prefix = escaped[:-3]  # 移除末尾的 /\#
            return re.compile(f"^{prefix}(?:/.*)?$")

        # 处理 + 单级通配：\+ → [^/]+（匹配不含 / 的单个层级）
        escaped = escaped.replace(r"\+", r"[^/]+")
        return re.compile(f"^{escaped}$")

    def _match_topic(self, topic: str, pattern: str) -> bool:
        """检查 topic 是否匹配指定的通配符模式

        :param topic: 实际接收到的 topic 字符串
        :param pattern: 订阅时使用的 topic 模式（可能包含 + 或 #）
        :return: 是否匹配
        """
        if pattern == topic:
            return True
        # 从缓存获取或创建正则
        regex = self._topic_regex_cache.get(pattern)
        if regex is None:
            regex = self._topic_pattern_to_regex(pattern)
            self._topic_regex_cache[pattern] = regex
        return bool(regex.match(topic))

    def _create_client(self) -> mqtt.Client:
        """创建 MQTT 客户端实例并配置回调

        :return: 配置好的 MQTT 客户端实例
        """
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.mqtt_client_id,
            clean_session=True,  # 不保留 broker 端会话，避免旧会话冲突
            protocol=mqtt.MQTTv311,
        )

        # 配置重连延迟（指数退避），避免 MQTT_ERR_CONN_LOST 循环时频繁重连
        client.reconnect_delay_set(
            min_delay=self.RECONNECT_MIN_DELAY,
            max_delay=self.RECONNECT_MAX_DELAY,
        )

        # 设置用户名密码
        client.username_pw_set(self.mqtt_username, self.mqtt_password)

        # 配置 TLS/SSL 证书
        if self.mqtt_need_tls:
            try:
                from utils.tools import resource_path
                client.tls_set(
                    ca_certs=resource_path(self.mqtt_ca_file.lstrip("./")),
                    certfile=resource_path(self.mqtt_cert_file.lstrip("./")),
                    keyfile=resource_path(self.mqtt_key_file.lstrip("./")),
                    cert_reqs=ssl.CERT_REQUIRED,
                    tls_version=ssl.PROTOCOL_TLS_CLIENT,
                )
                self.logger.info("MQTT TLS 证书配置成功")
            except Exception as err:
                self.logger.error("MQTT TLS 证书配置失败: %s", err)
        else:
            self.logger.info("MQTT TLS 未启用")

        # 注册回调
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        return client

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: mqtt.ConnectFlags,
        reason_code: ReasonCode,
        properties: Optional[mqtt.Properties] = None,
    ) -> None:
        """MQTT 连接成功回调 (Callback API VERSION2)

        :param client: MQTT 客户端实例
        :param userdata: 用户数据
        :param flags: 连接标志
        :param reason_code: 连接返回原因码
        :param properties: MQTT5 属性（可为 None）
        """
        if not reason_code.is_failure:
            self._is_connected = True
            self.logger.info("MQTT 连接成功，client_id: %s", self.mqtt_client_id)
            # 重新订阅之前的 topic
            for topic, callback in self._subscriptions.items():
                client.subscribe(topic, qos=self.DEFAULT_QOS)
                self.logger.info("MQTT 重新订阅 topic: %s", topic)
        else:
            self._is_connected = False
            self.logger.error(
                "MQTT 连接失败，原因码: %s (value=%d)",
                reason_code,
                reason_code.value,
            )

    # paho-mqtt 错误码到可读描述的映射
    _DISCONNECT_REASON_MAP: dict[int, str] = {
        0: "正常断开",
        1: "内存不足",
        2: "协议错误",
        3: "参数无效",
        4: "无连接",
        5: "连接被拒绝",
        6: "未找到",
        7: "连接丢失（网络断开或 broker 主动关闭）",
        8: "TLS 错误",
        9: "载荷过大",
        10: "不支持",
        11: "认证失败",
        12: "ACL 拒绝",
        13: "未知错误",
        14: "系统错误",
        15: "队列满",
        16: "心跳超时",
    }

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: mqtt.DisconnectFlags,
        reason_code: ReasonCode,
        properties: Optional[mqtt.Properties] = None,
    ) -> None:
        """MQTT 断开连接回调 (Callback API VERSION2)

        :param client: MQTT 客户端实例
        :param userdata: 用户数据
        :param flags: 断开连接标志
        :param reason_code: 断开原因码
        :param properties: MQTT5 属性（可为 None）
        """
        self._is_connected = False
        if reason_code.value != 0:
            reason: str = self._DISCONNECT_REASON_MAP.get(
                reason_code.value, f"未知错误码: {reason_code.value}"
            )
            self.logger.warning(
                "MQTT 意外断开连接，错误码: %d (%s)，%d秒后将自动重连",
                reason_code.value,
                reason,
                self.RECONNECT_MIN_DELAY,
            )
        else:
            self.logger.info("MQTT 正常断开连接")
        # 记录断开时的连接信息，便于排查
        self.logger.info(
            "MQTT 连接状态: host=%s, port=%d, client_id=%s, tls=%s",
            self.mqtt_host, self.mqtt_port, self.mqtt_client_id, self.mqtt_need_tls,
        )

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: object,
        msg: mqtt.MQTTMessage,
    ) -> None:
        """MQTT 收到消息回调

        支持通配符匹配：遍历所有订阅模式，找到匹配的 topic 并调用对应回调。
        一个消息可能匹配多个订阅模式（如同时订阅了 STOCK_REAL_TIME_ALL 和单个代码通配），
        所有匹配的回调都会被调用。

        :param client: MQTT 客户端实例
        :param userdata: 用户数据
        :param msg: 收到的消息对象
        """
        topic: str = msg.topic
        payload: str = msg.payload.decode("utf-8")
        self.logger.debug("MQTT 收到消息, topic: %s, payload: %s", topic, payload)

        matched: bool = False
        for pattern, callback in self._subscriptions.items():
            if self._match_topic(topic, pattern):
                matched = True
                try:
                    callback(topic, payload)
                except Exception as err:
                    self.logger.error(
                        "MQTT 回调函数执行失败, topic: %s, pattern: %s, error: %s",
                        topic,
                        pattern,
                        err,
                    )

        if not matched:
            self.logger.debug("MQTT 收到未匹配的消息, topic: %s", topic)

    def connect(self) -> bool:
        """连接 MQTT 服务器（幂等，重复调用不会创建重复连接）。

        :return: 连接是否成功
        """
        # 防止重复调用创建多个网络线程互相踢对方
        if self._client is not None:
            self.logger.debug("MQTT 客户端已存在，跳过重复连接")
            return True
        try:
            self._client = self._create_client()
            self._client.connect_async(
                host=self.mqtt_host,
                port=self.mqtt_port,
                keepalive=self.KEEP_ALIVE,
            )
            # 启动后台网络线程，自动处理重连
            self._client.loop_start()
            self.logger.info(
                "MQTT 客户端启动成功，服务器: %s:%d",
                self.mqtt_host,
                self.mqtt_port,
            )
            return True
        except Exception as err:
            self.logger.error("MQTT 连接失败: %s", err)
            self._client = None
            return False

    def disconnect(self) -> None:
        """断开 MQTT 连接并释放资源"""
        try:
            if self._client is not None:
                self._client.loop_stop()
                self._client.disconnect()
                self._is_connected = False
                self._client = None
                self._subscriptions.clear()
                self._topic_regex_cache.clear()
                self.logger.info("MQTT 客户端已断开连接")
        except Exception as err:
            self.logger.error("MQTT 断开连接失败: %s", err)

    def subscribe(
        self,
        topic: str,
        callback: Callable[[str, str], None],
        qos: int = DEFAULT_QOS,
    ) -> bool:
        """订阅 MQTT topic

        :param topic: 要订阅的 topic 字符串
        :param callback: 收到消息时的回调函数，参数为 (topic: str, payload: str)
        :param qos: QoS 级别，默认为 1
        :return: 订阅是否成功
        """
        if self._client is None:
            self.logger.error("MQTT 客户端未初始化，请先调用 connect()")
            return False

        try:
            result, mid = self._client.subscribe(topic, qos=qos)
            if result == 0:
                self._subscriptions[topic] = callback
                self.logger.info("MQTT 订阅 topic 成功: %s (QoS: %d, mid=%d)", topic, qos, mid)
                return True
            else:
                self.logger.error("MQTT 订阅 topic 失败: %s, broker 返回错误码: %d", topic, result)
                return False
        except Exception as err:
            self.logger.error("MQTT 订阅 topic 失败: %s, error: %s", topic, err)
            return False

    def unsubscribe(self, topic: str) -> bool:
        """取消订阅 MQTT topic

        :param topic: 要取消订阅的 topic 字符串
        :return: 取消订阅是否成功
        """
        if self._client is None:
            self.logger.error("MQTT 客户端未初始化，请先调用 connect()")
            return False

        try:
            self._client.unsubscribe(topic)
            self._subscriptions.pop(topic, None)
            self.logger.info("MQTT 取消订阅 topic: %s", topic)
            return True
        except Exception as err:
            self.logger.error("MQTT 取消订阅失败: %s, error: %s", topic, err)
            return False

    def publish(
        self,
        topic: str,
        payload: str,
        qos: int = DEFAULT_QOS,
        retain: bool = False,
    ) -> bool:
        """推送消息到 MQTT topic

        :param topic: 目标 topic 字符串
        :param payload: 消息内容（字符串）
        :param qos: QoS 级别，默认为 1
        :param retain: 是否保留消息，默认为 False
        :return: 推送是否成功
        """
        if self._client is None or not self._is_connected:
            self.logger.error("MQTT 未连接，无法推送消息到 topic: %s", topic)
            return False

        try:
            msg_info = self._client.publish(
                topic=topic,
                payload=payload,
                qos=qos,
                retain=retain,
            )
            self.logger.debug(
                "MQTT 推送消息成功, topic: %s, mid: %s",
                topic,
                msg_info.mid,
            )
            return True
        except Exception as err:
            self.logger.error("MQTT 推送消息失败, topic: %s, error: %s", topic, err)
            return False

    @property
    def is_connected(self) -> bool:
        """获取当前 MQTT 连接状态

        :return: 是否已连接
        """
        return self._is_connected

    def __del__(self) -> None:
        """析构时释放 MQTT 连接"""
        self.disconnect()
