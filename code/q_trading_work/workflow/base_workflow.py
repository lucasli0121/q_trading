"""
Author: liguoqiang
Date: 2026-07-05
Description: 工作流基类
    定义线程池供子类继承，订阅 MQTT 消息获取分钟和实时行情推送回调，
    实现分钟级 handle_bar() 和实时级 handle_tick() 函数。
    子类可以重写 handle_bar 和 handle_tick 以自定义处理逻辑。
"""

from __future__ import annotations

import json
import logging
from abc import ABC
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from app_context import AppContext
from mq.mqtt_client import MqttTopic


class BaseWorkflow(ABC):
    """工作流基类。

    提供：
    - 线程池（子类共享），用于异步执行策略任务
    - MQTT 分钟行情订阅 → handle_bar 回调
    - MQTT 实时行情订阅 → handle_tick 回调
    - 生命周期方法：start / stop

    使用方式:
        class MyWorkflow(BaseWorkflow):
            def handle_bar(self, topic: str, payload: dict[str, Any]) -> None:
                # 处理分钟行情
                ...

            def handle_tick(self, topic: str, payload: dict[str, Any]) -> None:
                # 处理实时行情
                ...
    """

    # 默认线程池大小
    DEFAULT_POOL_SIZE: int = 4

    def __init__(self, pool_size: int | None = None) -> None:
        """初始化工作流基类。

        :param pool_size: 线程池大小，默认 4
        """
        self.logger: logging.Logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )
        self._pool_size: int = pool_size or self.DEFAULT_POOL_SIZE
        self._executor: ThreadPoolExecutor | None = None
        self._running: bool = False
        # 保存 MQTT 回调引用，用于取消订阅
        self._bar_callback: Callable[[str, str], None] = self._on_bar_message
        self._tick_callback: Callable[[str, str], None] = self._on_tick_message
        # 工作流关联的股票池 ID 集合，用于 pool 级 MQTT 订阅
        self._pool_ids: set[str] = set()

    # ---- 线程池 ----

    @property
    def executor(self) -> ThreadPoolExecutor:
        """获取线程池实例（懒初始化）。"""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._pool_size,
                thread_name_prefix=f"wf-{self.__class__.__name__}",
            )
        return self._executor

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
        """提交任务到线程池。

        :param fn: 要执行的函数
        :param args: 位置参数
        :param kwargs: 关键字参数
        :return: Future 对象
        """
        return self.executor.submit(fn, *args, **kwargs)

    # ---- MQTT 消息解析与分发 ----

    @staticmethod
    def _parse_payload(payload: str) -> Any:
        """将 MQTT 原始 payload 字符串解析为 Python 对象。

        :param payload: MQTT 消息体（JSON 字符串）
        :return: 解析后的 dict / list，解析失败时返回原始字符串
        """
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return payload

    def _extract_code_from_topic(self, topic: str) -> str:
        """从 MQTT topic 中提取股票代码。

        Topic 格式: q_share/stock/real_time/000001 或 q_share/stock/minute/000001

        :param topic: MQTT topic 字符串
        :return: 股票代码，提取失败返回空字符串
        """
        parts: list[str] = topic.split("/")
        return parts[-1] if len(parts) >= 4 else ""

    def _on_bar_message(self, topic: str, payload: str) -> None:
        """分钟行情 MQTT 回调入口。

        在 MQTT 回调线程中执行，将实际处理提交到线程池，
        避免阻塞 MQTT 网络循环。

        :param topic: MQTT topic
        :param payload: 消息体（JSON 字符串）
        """
        if not self._running:
            return
        parsed: Any = self._parse_payload(payload)
        self.submit(self._handle_bar_safe, topic, parsed)

    def _on_tick_message(self, topic: str, payload: str) -> None:
        """实时行情 MQTT 回调入口。

        在 MQTT 回调线程中执行，将实际处理提交到线程池。

        :param topic: MQTT topic
        :param payload: 消息体（JSON 字符串）
        """
        if not self._running:
            return
        parsed: Any = self._parse_payload(payload)
        self.submit(self._handle_tick_safe, topic, parsed)

    def _handle_bar_safe(self, topic: str, payload: Any) -> None:
        """带异常保护的 handle_bar 包装器。

        :param topic: MQTT topic
        :param payload: 解析后的消息体
        """
        try:
            self.handle_bar(topic, payload)
        except Exception as exc:
            self.logger.error(
                "handle_bar 异常, topic=%s, error=%s", topic, exc, exc_info=True
            )

    def _handle_tick_safe(self, topic: str, payload: Any) -> None:
        """带异常保护的 handle_tick 包装器。

        :param topic: MQTT topic
        :param payload: 解析后的消息体
        """
        try:
            self.handle_tick(topic, payload)
        except Exception as exc:
            self.logger.error(
                "handle_tick 异常, topic=%s, error=%s", topic, exc, exc_info=True
            )

    # ---- 子类可重写的回调 ----

    def handle_bar(self, topic: str, payload: Any) -> None:
        """处理分钟行情消息，子类可重写。

        :param topic: MQTT topic
        :param payload: 解析后的消息体（dict 或 list）
        """
        self.logger.debug(
            "[%s] handle_bar(topic=%s)", self.__class__.__name__, topic
        )

    def handle_tick(self, topic: str, payload: Any) -> None:
        """处理实时行情消息，子类可重写。

        :param topic: MQTT topic
        :param payload: 解析后的消息体（dict 或 list）
        """
        self.logger.debug(
            "[%s] handle_tick(topic=%s)", self.__class__.__name__, topic
        )

    # ---- 子类可重写的初始化钩子 ----

    def _init_workflow(self) -> None:
        """在 MQTT 订阅前调用的初始化钩子，子类可重写。

        用于加载配置、策略实例和用户策略配置，收集 pool_ids 等。
        在 start() 中于 _subscribe_mqtt() 之前调用。
        """
        pass

    # ---- 生命周期 ----

    def _subscribe_mqtt(self) -> bool:
        """订阅 MQTT 分钟行情和实时行情 topic。

        当 _pool_ids 非空时，按 pool_id 订阅 pool 级 topic；
        否则订阅全局通配 topic（兼容模式）。

        :return: 订阅是否成功
        """
        mqtt_client = AppContext().mqtt_client
        if not getattr(mqtt_client, "is_connected", False) and not mqtt_client.connect():
            self.logger.error("MQTT 未连接，无法订阅行情")
            return False

        if self._pool_ids:
            # Pool 级订阅：对每个 pool_id 订阅独立的实时行情和分钟行情 topic
            for pool_id in self._pool_ids:
                # 实时行情通配: q_share/stock/real_time/{pool_id}/#（RabbitMQ 用 . 分隔，股票代码含 . 会导致段数多于1）
                real_topic: str = MqttTopic.stock_real_time_pool_code(pool_id, "#")
                mqtt_client.subscribe(real_topic, self._tick_callback)
                self.logger.info("已订阅实时行情: %s", real_topic)

                # 分钟行情通配: q_share/stock/minute/{pool_id}/#（RabbitMQ 用 . 分隔，股票代码含 . 会导致段数多于1）
                minute_topic: str = MqttTopic.stock_minute_pool_code(pool_id, "#")
                mqtt_client.subscribe(minute_topic, self._bar_callback)
                self.logger.info("已订阅分钟行情: %s", minute_topic)
        else:
            # 兼容模式：全局 topic 订阅
            mqtt_client.subscribe(MqttTopic.STOCK_MINUTE_ALL, self._bar_callback)
            self.logger.info("已订阅分钟行情: %s", MqttTopic.STOCK_MINUTE_ALL)

            mqtt_client.subscribe(MqttTopic.STOCK_REAL_TIME_ALL, self._tick_callback)
            self.logger.info("已订阅实时行情: %s", MqttTopic.STOCK_REAL_TIME_ALL)

        return True

    def _unsubscribe_mqtt(self) -> None:
        """取消 MQTT 订阅。"""
        mqtt_client = AppContext().mqtt_client
        try:
            if self._pool_ids:
                for pool_id in self._pool_ids:
                    mqtt_client.unsubscribe(
                        MqttTopic.stock_real_time_pool_code(pool_id, "#")
                    )
                    mqtt_client.unsubscribe(
                        MqttTopic.stock_minute_pool_code(pool_id, "#")
                    )
            else:
                mqtt_client.unsubscribe(MqttTopic.STOCK_MINUTE_ALL)
                mqtt_client.unsubscribe(MqttTopic.STOCK_REAL_TIME_ALL)
            self.logger.info("已取消 MQTT 订阅")
        except Exception as exc:
            self.logger.warning("取消 MQTT 订阅异常: %s", exc)

    def start(self) -> bool:
        """启动工作流。

        1. 调用 _init_workflow 钩子（加载配置/策略/收集 pool_ids）
        2. 订阅 MQTT 行情（pool 级或全局通配）
        3. 调用子类的 on_start 钩子
        4. 标记为运行中

        :return: 启动是否成功
        """
        if self._running:
            self.logger.warning("工作流已在运行中")
            return False

        self.logger.info("[%s] 工作流启动中...", self.__class__.__name__)

        # 步骤1：调用子类初始化钩子，加载配置和策略，收集 pool_ids
        try:
            self._init_workflow()
        except Exception as exc:
            self.logger.error("_init_workflow 异常: %s", exc, exc_info=True)
            return False

        # 步骤2：订阅 MQTT 行情
        if not self._subscribe_mqtt():
            return False

        # 步骤3：调用子类 on_start 钩子
        try:
            self.on_start()
        except Exception as exc:
            self.logger.error("on_start 异常: %s", exc, exc_info=True)
            self._unsubscribe_mqtt()
            return False

        self._running = True
        self.logger.info("[%s] 工作流启动成功", self.__class__.__name__)
        return True

    def stop(self) -> None:
        """停止工作流。

        1. 标记为停止
        2. 调用子类的 on_stop 钩子
        3. 取消 MQTT 订阅
        4. 关闭线程池
        """
        if not self._running:
            return

        self.logger.info("[%s] 工作流停止中...", self.__class__.__name__)
        self._running = False

        try:
            self.on_stop()
        except Exception as exc:
            self.logger.error("on_stop 异常: %s", exc, exc_info=True)

        self._unsubscribe_mqtt()

        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
            self.logger.info("线程池已关闭")

        self.logger.info("[%s] 工作流已停止", self.__class__.__name__)

    def on_start(self) -> None:
        """子类可重写的启动钩子，在 MQTT 订阅成功后调用。"""
        pass

    def on_stop(self) -> None:
        """子类可重写的停止钩子，在取消订阅前调用。"""
        pass

    @property
    def is_running(self) -> bool:
        """工作流是否正在运行。

        :return: 运行状态
        """
        return self._running

    def __del__(self) -> None:
        """析构时确保资源释放。"""
        if self._running:
            self.stop()
