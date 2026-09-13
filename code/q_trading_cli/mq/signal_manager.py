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
        "user_strategy_id": str,
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
        print(f"策略 {signal['user_strategy_id']} 收到信号: {signal['action']} {signal['stock_code']}")

    mgr.register_handler("strategy_123", on_signal)

    # 查询
    signals = mgr.get_signals(limit=50)
    strategy_signals = mgr.get_signals_by_user_strategy("strategy_123", limit=20)

    mgr.stop()    # 取消订阅
"""

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.user_strategy_manager import UserStrategyManager
from mq.mqtt_client import MqttClient, MqttTopic

logger = logging.getLogger(__name__)

# 最大保留信号条数
MAX_SIGNALS: int = 500

# 信号处理器类型别名
SignalHandler = Callable[[dict[str, Any]], None]

@dataclass
class UserSignal:
    # 用户策略的信号数据, 最大保留 MAX_SIGNALS 条
    signal: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

class SignalManager:
    """全局交易信号管理器（线程安全单例）。

    在 MQTT 回调线程中接收信号，写入 _signals 列表，
    同时按 user_strategy_id 分发给注册的处理器；
    UI 线程通过 get_signals() / get_today_count() 等只读方法查询。
    """
    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        self._initialized: bool = True
        # 记录所有用户策略的信号，每个信号和用户策略id对应
        self._signals: dict[str, UserSignal] = {}
        self._started: bool = False
        self._history_loaded: bool = False
        self._mqtt_client: MqttClient | None = None
        # user_strategy_id → handler 映射
        self._handlers: dict[str, SignalHandler] = {}
        self._handler_lock: threading.Lock = threading.Lock()

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
        with self._handler_lock:
            self._handlers.clear()
        logger.info("SignalManager 已停止")

    # ---- 策略处理器注册 ----

    def register_handler(self, user_strategy_id: str, handler: SignalHandler) -> None:
        """注册策略信号处理器。

        同一 user_strategy_id 多次注册会覆盖之前的处理器。

        :param user_strategy_id: 用户策略 ID
        :param handler: 信号处理回调，签名为 (signal_data: dict) -> None
        """
        with self._handler_lock:
            self._handlers[user_strategy_id] = handler
        logger.info("已注册策略信号处理器: user_strategy_id=%s", user_strategy_id)

    def unregister_handler(self, user_strategy_id: str) -> None:
        """取消注册策略信号处理器。

        :param user_strategy_id: 用户策略 ID
        """
        with self._handler_lock:
            self._handlers.pop(user_strategy_id, None)
        logger.info("已取消注册策略信号处理器: user_strategy_id=%s", user_strategy_id)

    def get_registered_user_strategy_ids(self) -> list[str]:
        """获取所有已注册的用户策略 ID 列表。

        :return: 用户策略 ID 列表
        """
        with self._handler_lock:
            return list(self._handlers.keys())

    # ---- MQTT 回调 ----

    def _on_signal(self, topic: str, payload: str) -> None:
        """MQTT 消息回调（在 MQTT 网络线程中执行）。

        1. 解析 JSON payload
        2. 追加时间戳（如服务端未提供）
        3. 存入 _signals 日志
        4. 按 user_strategy_id 分发给注册的处理器

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

        user_strategy_id = data.get("user_strategy_id", "")
        # 存入信号日志
        if user_strategy_id != "":
            if user_strategy_id not in self._signals:
                self._signals[user_strategy_id] = UserSignal()
            with self._signals[user_strategy_id].lock:
                self._signals[user_strategy_id].signal.insert(0, data)
                if len(self._signals[user_strategy_id].signal) > MAX_SIGNALS:
                    self._signals[user_strategy_id].signal = self._signals[user_strategy_id].signal[:MAX_SIGNALS]

        logger.info(
            "收到交易信号: user_strategy_id=%s, code=%s, action=%s, price=%s, "
            "profit_rate=%.2f%%, profit_amount=%.2f, reason=%s",
            data.get("user_strategy_id", ""),
            data.get("stock_code", ""),
            data.get("action", ""),
            data.get("trade_price", ""),
            float(data.get("profit_rate", 0) or 0),
            float(data.get("profit_amount", 0) or 0),
            data.get("reason", ""),
        )

        # 分发给注册的策略处理器
        if user_strategy_id:
            handler: SignalHandler | None = None
            with self._handler_lock:
                handler = self._handlers.get(user_strategy_id)
            if handler is not None:
                try:
                    handler(data)
                except Exception:
                    logger.exception(
                        "策略信号处理器执行失败: user_strategy_id=%s", user_strategy_id,
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
        self._history_loaded = True
        try:
            from app_context import AppContext
            signal_list: list[dict[str, Any]] = AppContext().trade_signal_api.list(limit=limit)
            for sig in signal_list:
                user_strategy_id = sig.get("user_strategy_id", "")
                if user_strategy_id != "":
                    if user_strategy_id not in self._signals:
                        self._signals[user_strategy_id] = UserSignal()
                    with self._signals[user_strategy_id].lock:
                        self._signals[user_strategy_id].signal.append(sig)
            logger.info("已加载 %d 条历史信号", len(signal_list))
        except Exception:
            logger.warning("加载历史信号失败", exc_info=True)

    # ---- （需在 UI 上下文中调用） ----
    def get_today_count(self) -> int:
        """获取今日信号总数。

        :return: 今日信号总数
        """
        today_prefix = datetime.now().astimezone().strftime("%Y-%m-%d")
        today_signal_count: int = 0
        user_strategy_ids: list[str] = UserStrategyManager().get_user_strategy_ids()
        for user_strategy_id in user_strategy_ids:
            if user_strategy_id in self._signals:
                with self._signals[user_strategy_id].lock:
                    for sig in self._signals[user_strategy_id].signal:
                        create_time: str = sig.get("create_time", "")
                        if create_time.startswith(today_prefix):
                            today_signal_count += 1
        return today_signal_count

    # ---- 查询方法 ----
    def get_signals(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取最近 N 条信号（按时间倒序）。

        :param limit: 返回条数上限
        :return: 信号列表
        """
        results: list[dict[str, Any]] = []
        user_strategies: list[dict[str, Any]] = []
        try:
            from app_context import AppContext
            user_strategies = AppContext().user_strategy_api.list()
        except Exception:
            logger.warning("查询用户策略列表失败", exc_info=True)
        strategy_ids: set[str] = {str(s.get("_id", s.get("id", ""))) for s in user_strategies if s.get("status", "stop") == "running"}
        for user_strategy_id in strategy_ids:
            if user_strategy_id in self._signals:
                with self._signals[user_strategy_id].lock:
                    results.extend(self._signals[user_strategy_id].signal[:limit])
        # 按 create_time 倒序排序
        results.sort(key=lambda x: x.get("create_time", ""), reverse=True)
        return results[:limit]
        
    def get_signals_by_user_strategy(self, user_strategy_ids: list, limit: int = 50) -> list[dict[str, Any]]:
        """获取指定策略的最近 N 条信号。

        :param user_strategy_ids: 用户策略 ID 列表
        :param limit: 返回条数上限
        :return: 该策略的信号列表
        """
        results: list[dict[str, Any]] = []
        for user_strategy_id in user_strategy_ids:
            if user_strategy_id in self._signals:
                with self._signals[user_strategy_id].lock:
                    results.extend(self._signals[user_strategy_id].signal[:limit])
        return results
    
    @property
    def is_started(self) -> bool:
        """信号管理器是否已启动。"""
        return self._started
