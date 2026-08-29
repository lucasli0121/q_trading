#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-08-12
Description: 全局交易信号管理器
    - 订阅 MQTT STOCK_TRADING_SIGNAL topic
    - 线程安全地维护信号日志（内存中，最多 500 条）
    - 支持按 strategy_id 注册处理器，信号到达时分发到对应策略
    - 提供方法供 UI 页面轮询获取信号数据

MQ 消息格式:
    {
        "strategy_id": str,
        "stock_code": str,
        "trade_price": float,
        "action": str,       # "买入" / "卖出"
        "profit_rate": float,
        "profit_amount": float,
        "reason": str,
    }

使用方式:
    mgr = SignalManager()
    mgr.start()   # 订阅 MQTT topic

    # 注册策略处理器
    def on_signal(signal: dict) -> None:
        print(f"策略 {signal['strategy_id']} 收到信号: {signal['action']} {signal['stock_code']}")

    mgr.register_handler("strategy_123", on_signal)

    # 查询
    signals = mgr.get_signals(limit=50)
    strategy_signals = mgr.get_signals_by_strategy("strategy_123", limit=20)

    mgr.stop()    # 取消订阅
"""

import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any, Self

from mq.mqtt_client import MqttClient, MqttTopic

logger = logging.getLogger(__name__)

# 最大保留信号条数
MAX_SIGNALS: int = 500

# 信号处理器类型别名
SignalHandler = Callable[[dict[str, Any]], None]


class SignalManager:
    """全局交易信号管理器（线程安全单例）。

    在 MQTT 回调线程中接收信号，写入 _signals 列表，
    同时按 strategy_id 分发给注册的处理器；
    UI 线程通过 get_signals() / get_today_count() 等只读方法查询。
    """

    _instance: "SignalManager | None" = None
    _instance_lock: threading.Lock = threading.Lock()

    def __new__(cls) -> Self:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        self._initialized: bool = True
        self._lock: threading.Lock = threading.Lock()
        self._signals: list[dict[str, Any]] = []
        self._new_count: int = 0  # 自上次 reset 以来的新信号数（供 badge 使用）
        self._today_base: int = 0  # 今日信号基数（页面打开时从数据库查询初始化）
        self._today_base_date: str = ""  # 基数对应日期，跨天自动失效
        self._started: bool = False
        self._history_loaded: bool = False
        self._mqtt_client: MqttClient | None = None
        # strategy_id → handler 映射
        self._handlers: dict[str, SignalHandler] = {}

    # ---- 生命周期 ----

    def start(self, mqtt_client: MqttClient | None = None) -> None:
        """启动信号管理器：订阅 MQTT 交易信号 topic。

        :param mqtt_client: MQTT 客户端实例，未传则从 AppContext 获取
        """
        if self._started:
            return
        from app_context import AppContext
        if mqtt_client is None:
            mqtt_client = AppContext().mqtt_client
        self._mqtt_client = mqtt_client
        mqtt_client.subscribe(
            MqttTopic.STOCK_TRADING_SIGNAL,
            self._on_signal,
        )
        self._started = True
        logger.info(
            "SignalManager 已启动，订阅 topic: %s", MqttTopic.STOCK_TRADING_SIGNAL,
        )

    def stop(self) -> None:
        """停止信号管理器：取消 MQTT 订阅，清空处理器。"""
        if not self._started:
            return
        if self._mqtt_client is not None:
            self._mqtt_client.unsubscribe(MqttTopic.STOCK_TRADING_SIGNAL)
        self._started = False
        with self._lock:
            self._handlers.clear()
        logger.info("SignalManager 已停止")

    # ---- 策略处理器注册 ----

    def register_handler(self, strategy_id: str, handler: SignalHandler) -> None:
        """注册策略信号处理器。

        同一 strategy_id 多次注册会覆盖之前的处理器。

        :param strategy_id: 策略模板 ID
        :param handler: 信号处理回调，签名为 (signal_data: dict) -> None
        """
        with self._lock:
            self._handlers[strategy_id] = handler
        logger.info("已注册策略信号处理器: strategy_id=%s", strategy_id)

    def unregister_handler(self, strategy_id: str) -> None:
        """取消注册策略信号处理器。

        :param strategy_id: 策略模板 ID
        """
        with self._lock:
            self._handlers.pop(strategy_id, None)
        logger.info("已取消注册策略信号处理器: strategy_id=%s", strategy_id)

    def get_registered_strategy_ids(self) -> list[str]:
        """获取所有已注册的策略 ID 列表。

        :return: 策略 ID 列表
        """
        with self._lock:
            return list(self._handlers.keys())

    # ---- MQTT 回调 ----

    def _on_signal(self, topic: str, payload: str) -> None:
        """MQTT 消息回调（在 MQTT 网络线程中执行）。

        1. 解析 JSON payload
        2. 追加时间戳（如服务端未提供）
        3. 存入 _signals 日志
        4. 按 strategy_id 分发给注册的处理器

        :param topic: MQTT topic
        :param payload: JSON 字符串，包含信号数据
        """
        try:
            data: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("SignalManager 收到非 JSON 消息: %s", payload[:200])
            return

        # 确保有 create_time 字段
        if "create_time" not in data:
            data["create_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: DTZ005

        # 存入信号日志
        with self._lock:
            self._signals.insert(0, data)
            if len(self._signals) > MAX_SIGNALS:
                self._signals = self._signals[:MAX_SIGNALS]
            self._new_count += 1

        logger.info(
            "收到交易信号: strategy_id=%s, code=%s, action=%s, price=%s, "
            "profit_rate=%.2f%%, profit_amount=%.2f, reason=%s",
            data.get("strategy_id", ""),
            data.get("stock_code", ""),
            data.get("action", ""),
            data.get("trade_price", ""),
            float(data.get("profit_rate", 0) or 0),
            float(data.get("profit_amount", 0) or 0),
            data.get("reason", ""),
        )

        # 分发给注册的策略处理器
        strategy_id: str = data.get("strategy_id", "")
        if strategy_id:
            handler: SignalHandler | None = None
            with self._lock:
                handler = self._handlers.get(strategy_id)
            if handler is not None:
                try:
                    handler(data)
                except Exception:
                    logger.exception(
                        "策略信号处理器执行失败: strategy_id=%s", strategy_id,
                    )

    # ---- 历史数据加载（需在 UI 上下文中调用） ----

    def load_history(self, limit: int = 10) -> None:
        """从数据库加载最近 N 条历史信号到内存。

        必须在 NiceGUI UI 上下文中调用（因为 trade_signal_api 需要 app.storage.user）。
        仅首次调用生效，后续调用会跳过（避免页面刷新时重复加载）。

        :param limit: 加载条数上限
        """
        if self._history_loaded:
            return
        try:
            if len(self._signals) == 0:
                from app_context import AppContext
                signal_list: list[dict[str, Any]] = AppContext().trade_signal_api.list(limit=limit)
                with self._lock:
                    self._signals.extend(signal_list)
                    self._new_count = len(self._signals)
                self._history_loaded = True
                logger.info("已加载 %d 条历史信号", len(signal_list))
        except Exception:
            logger.warning("加载历史信号失败", exc_info=True)

    # ---- 查询方法 ----

    def get_signals(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取最近 N 条信号（最新在前）。

        :param limit: 返回条数上限
        :return: 信号列表（副本，线程安全）
        """
        with self._lock:
            return list(self._signals[:limit])

    def get_signals_by_strategy(self, strategy_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """获取指定策略的最近 N 条信号。

        :param strategy_id: 策略模板 ID
        :param limit: 返回条数上限
        :return: 该策略的信号列表
        """
        with self._lock:
            return [
                s for s in self._signals
                if s.get("strategy_id") == strategy_id
            ][:limit]

    def init_today_count(self, db_count: int) -> None:
        """用数据库中的今日信号总数初始化基数（每天仅生效一次）。

        页面首次打开时调用：数据库计数已包含此前通过 MQTT 收到并落库的信号，
        因此需扣除当前内存中已有的今日信号数，避免重复统计；
        之后新到的 MQ 信号在 get_today_count() 中实时累加。

        :param db_count: 数据库查询到的今日信号总数
        """
        today: str = datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005
        with self._lock:
            if self._today_base_date == today:
                return
            mq_today: int = sum(
                1 for s in self._signals
                if str(s.get("create_time", ""))[:10] == today
            )
            self._today_base = max(db_count - mq_today, 0)
            self._today_base_date = today
        logger.info(
            "今日信号基数初始化: db_count=%d, mq_cached=%d, base=%d",
            db_count, mq_today, self._today_base,
        )

    def get_today_count(self) -> int:
        """获取今日信号总数（数据库基数 + 本次运行期间 MQ 收到的信号数）。

        :return: 今日信号条数
        """
        today: str = datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005
        with self._lock:
            if self._today_base_date and self._today_base_date != today:
                # 跨天：已设置的基数失效，仅统计本次运行期间收到的信号
                self._today_base = 0
                self._today_base_date = ""
            mq_today: int = sum(
                1 for s in self._signals
                if str(s.get("create_time", ""))[:10] == today
            )
            return self._today_base + mq_today

    def get_new_count(self) -> int:
        """获取自上次 reset 以来的新信号数（供顶部 badge 使用）。

        :return: 新信号数
        """
        with self._lock:
            return self._new_count

    def reset_new_count(self) -> None:
        """清零新信号计数器。"""
        with self._lock:
            self._new_count = 0

    def clear(self) -> None:
        """清空所有信号记录。"""
        with self._lock:
            self._signals.clear()
            self._new_count = 0

    @property
    def is_started(self) -> bool:
        """信号管理器是否已启动。"""
        return self._started
