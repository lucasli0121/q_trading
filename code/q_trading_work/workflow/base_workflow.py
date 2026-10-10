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
import threading
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
        self._config_callback: Callable[[str, str], None] = self._on_config_message
        # 配置变更处理专用执行器（单线程），与行情线程池隔离
        self._config_executor: ThreadPoolExecutor | None = None
        # 已**真正订阅**成功的股票池 ID 集合。
        # 语义只有这一个：成员 = 该池的 real_time/minute topic 已经 subscribe 过。
        # 启动前「打算订阅哪些池」由 `_collect_startup_pool_ids()` 提供，不要往这里预填
        # —— `subscribe_pool` 用「在集合里」判断是否已订阅并短路返回，预填等于
        # 「登记了但一条都没订阅」，行情全丢且不报错（2026-10-10 实测）。
        self._subscribed_pool_ids: set[str] = set()
        # 池订阅会随配置对账动态增删（用户换池），必须在锁里改：
        # 订阅回调/对账线程可能与 _unsubscribe_mqtt 的遍历同时发生。
        self._pool_lock: threading.Lock = threading.Lock()

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

    @property
    def config_executor(self) -> ThreadPoolExecutor:
        """配置变更处理的专用线程池（单线程，懒初始化）。

        与行情线程池（`executor`）分开，原因有两条：

        1. 配置对账要发若干次 HTTP（拉分配表 / 用户策略 / 模板 / 股票池），
           放进只有 4 个 worker 的行情池会与信号判定抢线程，表现为信号延迟；
        2. 单线程天然把多次变更串行化。并发对账会重复建策略实例、重复订阅
           MQTT topic，而这两件事都不幂等（多订阅一次就多收一份行情）。

        :return: 单线程执行器
        """
        if self._config_executor is None:
            self._config_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix=f"wf-cfg-{self.__class__.__name__}",
            )
        return self._config_executor

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

    def _on_config_message(self, topic: str, payload: str) -> None:
        """配置变更 MQTT 回调入口。

        与行情回调不同，这里**不检查 `self._running`**：订阅完成到
        `_running=True` 之间有一个很短的窗口，而配置变更本身是低频事件
        （用户点一次保存才有一条），宁可多对账一次也不要漏掉。

        实际处理投到单线程的 `config_executor`，既不阻塞 MQTT 网络循环，
        也不与行情任务抢线程。

        :param topic: MQTT topic
        :param payload: 消息体（JSON 字符串）
        """
        parsed: Any = self._parse_payload(payload)
        self.logger.info("收到配置变更通知: topic=%s payload=%s", topic, parsed)
        self.config_executor.submit(self._handle_config_safe, parsed)

    def _handle_config_safe(self, payload: Any) -> None:
        """带异常保护的 on_config_change 包装器。

        :param payload: 解析后的消息体
        """
        try:
            self.on_config_change(payload)
        except Exception as exc:
            self.logger.error("on_config_change 异常: %s", exc, exc_info=True)

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

    def on_config_change(self, payload: Any) -> None:
        """配置变更钩子，子类可重写。

        默认什么都不做。实现约定：

        - **幂等**：同一条变更可能重复到达（服务端重发、轮询兜底也会走同一路径），
          重复执行不能造成副作用累积；
        - **不假设 payload 字段存在**：它只用于日志，判断「该同步什么」应当靠
          重新读取服务端当前状态，而不是靠消息里的字段；
        - 在本方法的单线程执行器里同步跑完即可，不需要自己再起线程。

        :param payload: 解析后的消息体（dict / 原样字符串）
        """
        self.logger.debug("[%s] 收到配置变更消息: %s", self.__class__.__name__, payload)

    # ---- 子类可重写的初始化钩子 ----

    def _init_workflow(self) -> None:
        """在 MQTT 订阅前调用的初始化钩子，子类可重写。

        用于加载配置、策略实例和用户策略配置等。
        在 start() 中于 _subscribe_mqtt() 之前调用。
        """
        pass

    # ---- 生命周期 ----

    def _collect_startup_pool_ids(self) -> list[str]:
        """返回启动时需要订阅的股票池 ID，子类可重写。

        与 `_subscribed_pool_ids` 的区别是**意图**与**事实**：这里回答「要订阅哪些」，
        `_subscribed_pool_ids` 记录「已经订阅了哪些」。两者混用一个集合会让
        `subscribe_pool` 的幂等短路把「只登记未订阅」当成「已订阅」，
        结果是启动后一条池级行情都收不到，且日志里全无异常。

        :return: 待订阅的 pool_id 列表（可为空，表示走全局通配的兼容模式）
        """
        return []

    def _subscribe_mqtt(self) -> bool:
        """订阅 MQTT 分钟行情和实时行情 topic。

        启动时要订阅的池由 `_collect_startup_pool_ids()` 给出（非空时逐个
        `subscribe_pool`，由其登记到 `_subscribed_pool_ids`）；为空则订阅全局通配 topic
        （兼容模式）。

        :return: 订阅是否成功
        """
        mqtt_client = AppContext().mqtt_client
        if not getattr(mqtt_client, "is_connected", False) and not mqtt_client.connect():
            self.logger.error("MQTT 未连接，无法订阅行情")
            return False

        startup_pool_ids: list[str] = self._collect_startup_pool_ids()
        if startup_pool_ids:
            # Pool 级订阅：对每个 pool_id 订阅独立的实时行情和分钟行情 topic
            for pool_id in startup_pool_ids:
                self.subscribe_pool(pool_id)
        else:
            # 兼容模式：全局 topic 订阅
            mqtt_client.subscribe(MqttTopic.STOCK_MINUTE_ALL, self._bar_callback)
            self.logger.info("已订阅分钟行情: %s", MqttTopic.STOCK_MINUTE_ALL)

            mqtt_client.subscribe(MqttTopic.STOCK_REAL_TIME_ALL, self._tick_callback)
            self.logger.info("已订阅实时行情: %s", MqttTopic.STOCK_REAL_TIME_ALL)

        # 配置变更通知与行情订阅无关，两种订阅模式下都要订阅：
        # 策略参数、股票池内容、池绑定、启停状态、分配关系都只在进程启动时读过一次，
        # 没有这条消息就要等重启才生效。
        mqtt_client.subscribe(MqttTopic.CONFIG_CHANGE, self._config_callback)
        self.logger.info("已订阅配置变更通知: %s", MqttTopic.CONFIG_CHANGE)

        return True

    # ---- pool 级行情订阅（运行中可增删）----

    @property
    def subscribed_pool_ids(self) -> list[str]:
        """当前已订阅的股票池 ID 快照。

        返回副本而不是原集合：调用方（含取消订阅的遍历）持有一个稳定的列表，
        期间由对账线程增删池不会引发「迭代中集合被修改」。
        """
        with self._pool_lock:
            return list(self._subscribed_pool_ids)

    def subscribe_pool(self, pool_id: str) -> bool:
        """订阅某个股票池的实时 + 分钟行情 topic，并登记到 `_subscribed_pool_ids`。

        幂等：同一个 pool_id 重复调用只订阅一次。重复订阅的后果是同一根行情
        收到两次回调（策略侧会算两遍并推两条信号），因此这里必须挡住。

        :param pool_id: 股票池 ID
        :return: 是否完成订阅（已订阅或订阅成功均为 True）
        """
        if not pool_id:
            return False
        with self._pool_lock:
            if pool_id in self._subscribed_pool_ids:
                return True
            self._subscribed_pool_ids.add(pool_id)

        mqtt_client = AppContext().mqtt_client
        try:
            if not getattr(mqtt_client, "is_connected", False) and not mqtt_client.connect():
                # 登记已经加上：MQTT 恢复后重新订阅由 _subscribe_mqtt 统一处理，
                # 这里回滚登记，避免「集合里有、实际没订阅」的静默不一致。
                with self._pool_lock:
                    self._subscribed_pool_ids.discard(pool_id)
                self.logger.error("MQTT 未连接，无法订阅股票池 %s 的行情", pool_id)
                return False
            # 实时行情通配: q_share/stock/real_time/{pool_id}/#（RabbitMQ 用 . 分隔，股票代码含 . 会导致段数多于1）
            real_topic: str = MqttTopic.stock_real_time_pool_code(pool_id, "#")
            mqtt_client.subscribe(real_topic, self._tick_callback)
            # 分钟行情通配: q_share/stock/minute/{pool_id}/#
            minute_topic: str = MqttTopic.stock_minute_pool_code(pool_id, "#")
            mqtt_client.subscribe(minute_topic, self._bar_callback)
            self.logger.info("已订阅股票池行情: pool_id=%s", pool_id)
            return True
        except Exception as exc:
            with self._pool_lock:
                self._subscribed_pool_ids.discard(pool_id)
            self.logger.error("订阅股票池 %s 行情异常: %s", pool_id, exc, exc_info=True)
            return False

    def unsubscribe_pool(self, pool_id: str) -> None:
        """取消某个股票池的行情订阅，并从 `_subscribed_pool_ids` 移除。

        :param pool_id: 股票池 ID
        """
        if not pool_id:
            return
        with self._pool_lock:
            if pool_id not in self._subscribed_pool_ids:
                return
            self._subscribed_pool_ids.discard(pool_id)
        mqtt_client = AppContext().mqtt_client
        try:
            mqtt_client.unsubscribe(MqttTopic.stock_real_time_pool_code(pool_id, "#"))
            mqtt_client.unsubscribe(MqttTopic.stock_minute_pool_code(pool_id, "#"))
            self.logger.info("已退订股票池行情: pool_id=%s", pool_id)
        except Exception as exc:
            self.logger.warning("退订股票池 %s 行情异常: %s", pool_id, exc)

    def _unsubscribe_mqtt(self) -> None:
        """取消 MQTT 订阅。"""
        mqtt_client = AppContext().mqtt_client
        try:
            if self._subscribed_pool_ids:
                for pool_id in self.subscribed_pool_ids:
                    mqtt_client.unsubscribe(
                        MqttTopic.stock_real_time_pool_code(pool_id, "#")
                    )
                    mqtt_client.unsubscribe(
                        MqttTopic.stock_minute_pool_code(pool_id, "#")
                    )
            else:
                mqtt_client.unsubscribe(MqttTopic.STOCK_MINUTE_ALL)
                mqtt_client.unsubscribe(MqttTopic.STOCK_REAL_TIME_ALL)
            mqtt_client.unsubscribe(MqttTopic.CONFIG_CHANGE)
            self.logger.info("已取消 MQTT 订阅")
        except Exception as exc:
            self.logger.warning("取消 MQTT 订阅异常: %s", exc)

    def start(self) -> bool:
        """启动工作流。

        1. 调用 _init_workflow 钩子（加载配置/策略）
        2. 订阅 MQTT 行情（pool 级或全局通配）
        3. 调用子类的 on_start 钩子
        4. 标记为运行中

        :return: 启动是否成功
        """
        if self._running:
            self.logger.warning("工作流已在运行中")
            return False

        self.logger.info("[%s] 工作流启动中...", self.__class__.__name__)

        # 步骤1：调用子类初始化钩子，加载配置和策略
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

        # 配置对账线程排在 _unsubscribe_mqtt 之后、这里之前，wait=True 会把
        # 正在跑的那一轮对账等完（最多几秒），避免退出时留下半同步的状态。
        if self._config_executor is not None:
            self._config_executor.shutdown(wait=True, cancel_futures=True)
            self._config_executor = None
            self.logger.info("配置变更处理线程池已关闭")

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
