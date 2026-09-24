#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-08-12
Description: 全局交易信号管理器
    - 订阅 MQTT STOCK_TRADING_SIGNAL topic
    - 线程安全地维护信号日志（内存中，按 user_strategy_id 分组，每组最多 500 条，
      且只保留最近 12 小时内的信号）
    - 支持按 user_strategy_id 注册处理器，信号到达时分发到对应策略
    - 提供方法供 UI 页面轮询获取信号数据（结果按时间倒序）

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

多用户隔离说明（重要）：
    进程只部署一套、SignalManager 是进程级单例，而 MQ 信号本身不带用户标识，
    因此内部容器以 **user_strategy_id** 为键存放所有用户的信号。
    数据隔离靠**读取时按当前会话的用户策略 ID 过滤**实现：
    所有公开查询方法都从 UserStrategyManager()（会话级缓存）取 ID，
    只会返回当前登录用户的策略信号，不会让其他用户的数据可见。
    若将来新增查询方法，务必同样以当前会话的 user_strategy_id 作为范围。

使用方式:
    mgr = SignalManager()
    mgr.start()   # 订阅 MQTT topic

    # 注册策略处理器
    def on_signal(signal: dict) -> None:
        print(f"策略 {signal['user_strategy_id']} 收到信号: {signal['action']} {signal['stock_code']}")

    mgr.register_handler("strategy_123", on_signal)

    # 查询（结果均按时间倒序）
    signals = mgr.get_signals(limit=50)
    strategy_ids = UserStrategyManager().get_user_strategy_ids()
    strategy_signals = mgr.get_signals_by_user_strategy(strategy_ids, limit=20)

    mgr.stop()    # 取消订阅
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from nicegui import app

from core.singleton import Singleton
from core.user_strategy_manager import UserStrategyManager
from mq.mqtt_client import MqttClient, MqttTopic

logger = logging.getLogger(__name__)

# 每个 user_strategy_id 最多保留的信号条数
MAX_SIGNALS: int = 500

# 信号保留时长（小时）——超过该时长的信号在清理时被剔除
SIGNAL_RETENTION_HOURS: int = 12

# 清理检测间隔（秒）——避免每次来信号都全量遍历
PRUNE_INTERVAL_SECONDS: float = 60.0

# 「历史信号已加载」标志（存于 app.storage.user，按会话隔离）。
# 标志值是**当前进程的运行标识**（见 _PROCESS_TOKEN），不是布尔值。
_HISTORY_FLAG_KEY: str = "signal_history_loaded"

# 本进程的运行标识：进程启动（含 NiceGUI 热重载导致的重启）时重新生成。
#
# ⚠ 为什么标志位要存 token 而不是 True：
#   信号容器 self._signals 是**进程内存**，而 app.storage.user 是**跨进程保留**的
#   会话存储。只用一个布尔标志会出现这样的组合：开发时改了代码 → 应用热重载 →
#   进程内存被清空，但会话里的标志仍是 True ⇒ load_history() 直接返回，
#   历史信号再也不加载，信号表（以及所有依赖内存信号的展示）从此恒空，
#   且**刷新页面也无法恢复**（标志永远为真）。
#   存进程 token 后，进程换了一代就必然重新加载。
#
# ⚠ 为什么不用进程级布尔标志（原实现的教训）：那样只有第一个用户能加载到
#   自己的历史信号，其他用户永远空。
_PROCESS_TOKEN: str = uuid4().hex

# 信号时间字段格式
_TIME_FORMAT: str = "%Y-%m-%d %H:%M:%S"

# 信号处理器类型别名
SignalHandler = Callable[[dict[str, Any]], None]


@dataclass
class UserSignal:
    """某个 user_strategy_id 的信号集合。"""

    # 用户策略的信号数据，最多保留 MAX_SIGNALS 条
    signal: list[dict[str, Any]] = field(default_factory=list)


def signal_key(sig: dict[str, Any]) -> str:
    """信号的去重键（历史加载与 MQ 实时两条路径共用同一个键）。

    用「用户策略 + 时间 + 代码 + 方向」而不是后端记录 ``id``，是因为同一条信号
    可能从两条路径进来：MQ 实时推送时载荷里没有 ``id``，而历史加载拿到的记录有
    ``id`` —— 只按 ``id`` 去重时，先到的 MQ 副本与后到的历史副本会被当成两条。

    代价：同一策略在同一**秒**对同一标的的同向信号会被合并成一条。信号时间戳精确
    到秒，这种情况实际就是同一条记录；真要区分需要后端在 MQ 载荷里带 ``id``。
    """
    created: str = str(sig.get("create_time", "") or "")
    if created:
        return "|".join(
            str(sig.get(k, "") or "")
            for k in ("user_strategy_id", "create_time", "stock_code", "action")
        )
    # 没有时间戳（不该出现，历史记录与 MQ 回调都会补）时退回记录 ID，避免整表互相合并
    return f"id:{str(sig.get('id', '') or '')}"


class SignalManager(metaclass=Singleton):
    """全局交易信号管理器（线程安全单例）。

    在 MQTT 回调线程中接收信号，写入 _signals，
    同时按 user_strategy_id 分发给注册的处理器；
    UI 线程通过 get_signals() / get_today_count() 等只读方法查询，
    所有查询按当前会话的用户策略 ID 过滤，并按时间倒序返回。
    """

    def __init__(self) -> None:
        # 记录所有用户策略的信号，键为 user_strategy_id
        self._signals: dict[str, UserSignal] = {}
        # 保护 _signals 的结构与内容（单锁换简单，交易信号频率低，无性能问题）
        self._signals_lock: threading.RLock = threading.RLock()
        self._started: bool = False
        self._mqtt_client: MqttClient | None = None
        self._last_prune: float = 0.0
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

    def _store_signal(self, user_strategy_id: str, sig: dict[str, Any]) -> bool:
        """把一条信号放进内存容器（按 :func:`signal_key` 去重，新的在前）。

        **去重是必需的**：容器 ``self._signals`` 是**进程级**的（所有会话共享同一份），
        而「历史信号已加载」标志是**会话级**的 —— 每个会话都会各自加载一次，若只做
        无脑追加，同一条历史信号就会被复制多份（实测 3 个会话时表格里每条信号出现
        3 次，统计卡从 10 条虚增到 30 条）。MQ 侧 QoS 1 的重投同理。

        :param user_strategy_id: 信号归属的用户策略 ID
        :param sig: 信号数据
        :return: True 表示新增，False 表示容器里已有同一条信号
        """
        key: str = signal_key(sig)
        with self._signals_lock:
            us: UserSignal | None = self._signals.get(user_strategy_id)
            if us is None:
                us = UserSignal()
                self._signals[user_strategy_id] = us
            if any(signal_key(item) == key for item in us.signal):
                return False
            us.signal.insert(0, sig)
            if len(us.signal) > MAX_SIGNALS:
                del us.signal[MAX_SIGNALS:]
            return True

    def _on_signal(self, topic: str, payload: str) -> None:
        """MQTT 消息回调（在 MQTT 网络线程中执行）。

        1. 解析 JSON payload
        2. 追加时间戳（如服务端未提供）
        3. 存入信号日志
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
            data["create_time"] = datetime.now().strftime(_TIME_FORMAT)  # noqa: DTZ005

        user_strategy_id: str = str(data.get("user_strategy_id", "") or "")
        # 存入信号日志（同一信号重复投递时只留一条）
        if user_strategy_id:
            if not self._store_signal(user_strategy_id, data):
                logger.debug("信号已在容器中，跳过重复: %s", signal_key(data))

        logger.info(
            "收到交易信号: user_strategy_id=%s, code=%s, action=%s, price=%s, "
            "profit_rate=%.2f%%, profit_amount=%.2f, reason=%s",
            user_strategy_id,
            data.get("stock_code", ""),
            data.get("action", ""),
            data.get("trade_price", ""),
            float(data.get("profit_rate", 0) or 0),
            float(data.get("profit_amount", 0) or 0),
            data.get("reason", ""),
        )

        # 低频清理：只保留最近 12 小时内的信号
        self._maybe_prune()

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

    # ---- 清理机制 ----

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        """解析信号时间字段。

        :param value: 时间字符串，格式 YYYY-MM-DD HH:MM:SS
        :return: datetime；无法解析时返回 None（此类信号在清理时一律保留）
        """
        text: str = str(value or "").strip()
        if not text:
            return None
        for fmt in (_TIME_FORMAT, "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)  # noqa: DTZ007
            except ValueError:
                continue
        return None

    def _maybe_prune(self) -> None:
        """按固定间隔触发一次清理，避免每条信号都全量遍历。"""
        now: float = time.monotonic()
        if now - self._last_prune < PRUNE_INTERVAL_SECONDS:
            return
        self._last_prune = now
        self.prune_expired()

    def prune_expired(self, retention_hours: int = SIGNAL_RETENTION_HOURS) -> int:
        """清理超过保留时长的信号，并回收已空的策略条目。

        保留时长默认为 12 小时。时间无法解析的信号一律保留（不误删）。

        :param retention_hours: 保留时长（小时）
        :return: 被清理掉的信号条数
        """
        cutoff: datetime = datetime.now() - timedelta(hours=retention_hours)  # noqa: DTZ005
        removed: int = 0
        with self._signals_lock:
            for user_strategy_id in list(self._signals.keys()):
                us: UserSignal = self._signals[user_strategy_id]
                kept: list[dict[str, Any]] = []
                for sig in us.signal:
                    sig_time: datetime | None = self._parse_time(sig.get("create_time", ""))
                    if sig_time is None or sig_time >= cutoff:
                        kept.append(sig)
                removed += len(us.signal) - len(kept)
                if kept:
                    us.signal = kept
                else:
                    # 条目已空则回收，避免长期驻留；信号再次到来时会自动重建
                    del self._signals[user_strategy_id]
        if removed:
            logger.info(
                "信号清理完成：剔除 %d 条超过 %d 小时的信号，剩余 %d 个策略条目",
                removed, retention_hours, len(self._signals),
            )
        return removed

    # ---- 历史数据加载（需在 UI 上下文中调用） ----

    def load_history(self, limit: int = 10) -> None:
        """从数据库加载最近 N 条历史信号到内存。

        必须在 NiceGUI UI 上下文中调用（因为 trade_signal_api 需要 app.storage.user）。

        **同一个会话在同一个进程里只加载一次**：标志位存放在 app.storage.user 中
        （按会话隔离，因此每个登录用户都会各自加载一次自己的历史信号），标志值是
        当前进程的运行标识（见 _PROCESS_TOKEN）—— 热重载/重启后进程标识变了会重新
        加载，不会因为会话里留着上一代的标志而让信号表永久为空。
        加载失败时**回滚标志**，下次页面加载会重试。

        :param limit: 加载条数上限
        """
        if not app.is_started:
            return
        try:
            if app.storage.user.get(_HISTORY_FLAG_KEY, "") == _PROCESS_TOKEN:
                return
            # 先置位，避免同一会话内的并发调用重复加载（跨会话的重复由
            # _store_signal 的去重兜住，不依赖这个标志）
            app.storage.user[_HISTORY_FLAG_KEY] = _PROCESS_TOKEN
        except Exception:
            logger.debug("读取历史信号标志失败（可能无会话上下文）", exc_info=True)
            return

        try:
            from app_context import AppContext
            signal_list: list[dict[str, Any]] = AppContext().trade_signal_api.list(limit=limit)
            added: int = 0
            # 倒序插入（_store_signal 插到表头）⇒ 最终顺序仍是「新的在前」，
            # 与 MQ 实时信号一致；同时按 signal_key 去重，避免多会话各存一份。
            for sig in reversed(signal_list):
                user_strategy_id: str = str(sig.get("user_strategy_id", "") or "")
                if not user_strategy_id:
                    continue
                if self._store_signal(user_strategy_id, sig):
                    added += 1
            logger.info(
                "已加载历史信号：返回 %d 条，新增 %d 条，去重跳过 %d 条",
                len(signal_list), added, len(signal_list) - added,
            )
        except Exception:
            logger.warning("加载历史信号失败", exc_info=True)
            # 回滚标志，让下次页面加载重试；否则本会话在本进程内再也不会加载历史信号
            try:
                if app.storage.user.get(_HISTORY_FLAG_KEY, "") == _PROCESS_TOKEN:
                    del app.storage.user[_HISTORY_FLAG_KEY]
            except Exception:
                logger.debug("回滚历史信号标志失败", exc_info=True)

    # ---- 查询方法（均按当前会话的用户策略 ID 过滤，结果按时间倒序） ----

    def _collect(self, user_strategy_ids: list[str], per_strategy_limit: int | None) -> list[dict[str, Any]]:
        """收集指定用户策略的信号，按 create_time 倒序排序。

        :param user_strategy_ids: 用户策略 ID 列表（必须是当前会话范围内的 ID）
        :param per_strategy_limit: 每个策略最多取多少条（None 表示不限制）
        :return: 排序后的信号列表
        """
        results: list[dict[str, Any]] = []
        with self._signals_lock:
            for user_strategy_id in user_strategy_ids:
                us: UserSignal | None = self._signals.get(user_strategy_id)
                if us is None:
                    continue
                items: list[dict[str, Any]] = list(us.signal)
                if per_strategy_limit is not None:
                    items = items[:per_strategy_limit]
                results.extend(items)
        # 统一按时间倒序（字符串比较即可，格式固定为 YYYY-MM-DD HH:MM:SS）
        results.sort(key=lambda x: str(x.get("create_time", "") or ""), reverse=True)
        return results

    def get_signals_by_user_strategy(
        self, user_strategy_ids: list[str], limit: int = 50,
    ) -> list[dict[str, Any]]:
        """获取指定策略的最近 N 条信号（按时间倒序）。

        注意：``limit`` 是**每个策略**的上限，不是全局条数上限
        （需要全局 N 条请使用 get_signals）。

        :param user_strategy_ids: 用户策略 ID 列表（应为当前会话下的 ID）
        :param limit: 每个策略返回条数上限
        :return: 按时间倒序的信号列表
        """
        return self._collect(list(user_strategy_ids or []), per_strategy_limit=limit)

    def get_latest_signals(self, user_strategy_ids: list[str]) -> dict[str, dict[str, Any]]:
        """批量获取每个用户策略最近的一条信号（纯内存查询，无排序开销）。

        供列表类页面使用：原实现按行调用 get_signals()，而 get_signals()
        每次都会请求 user_strategy_api.list()，单次表格渲染（PAGE_SIZE=10）
        就会发出 10 个重复请求，且受全局条数窗口限制可能取不到该策略的信号。

        :param user_strategy_ids: 用户策略 ID 列表（应为当前会话下的 ID）
        :return: {user_strategy_id: 最近一条信号}
        """
        latest: dict[str, dict[str, Any]] = {}
        with self._signals_lock:
            for user_strategy_id in user_strategy_ids:
                us: UserSignal | None = self._signals.get(user_strategy_id)
                if us is None or not us.signal:
                    continue
                best: dict[str, Any] | None = None
                best_time: str = ""
                for sig in us.signal:
                    sig_time: str = str(sig.get("create_time", "") or "")
                    if best is None or sig_time > best_time:
                        best = sig
                        best_time = sig_time
                if best is not None:
                    latest[user_strategy_id] = best
        return latest

    def get_signals(self, limit: int = 50, *, only_running: bool = True) -> list[dict[str, Any]]:
        """获取当前会话下、最近 N 条信号（全局 N 条，按时间倒序）。

        用户范围来自会话级缓存 UserStrategyManager，不再为此发起 HTTP 请求
        （原实现每次调用都会请求 user_strategy_api.list()，在表格逐行渲染时
        会造成大量重复请求）。

        :param limit: 全局返回条数上限
        :param only_running: 是否只统计运行中（status=running）的策略
        :return: 按时间倒序的信号列表
        """
        manager = UserStrategyManager()
        ids: list[str] = (
            manager.get_running_user_strategy_ids() if only_running
            else manager.get_user_strategy_ids()
        )
        return self._collect(ids, per_strategy_limit=None)[:limit]

    def get_today_count(self) -> int:
        """获取当前会话下今日信号总数。

        :return: 今日信号总数
        """
        today_prefix: str = datetime.now().astimezone().strftime("%Y-%m-%d")
        count: int = 0
        with self._signals_lock:
            for user_strategy_id in UserStrategyManager().get_user_strategy_ids():
                us: UserSignal | None = self._signals.get(user_strategy_id)
                if us is None:
                    continue
                for sig in us.signal:
                    if str(sig.get("create_time", "") or "").startswith(today_prefix):
                        count += 1
        return count

    # ---- 其它 ----

    @property
    def is_started(self) -> bool:
        """信号管理器是否已启动。"""
        return self._started

    def stats(self) -> dict[str, int]:
        """返回内存占用统计（用于排查）。

        :return: {"strategies": 策略条目数, "signals": 信号总条数}
        """
        with self._signals_lock:
            entries: list[UserSignal] = list(self._signals.values())
        return {
            "strategies": len(entries),
            "signals": sum(len(us.signal) for us in entries),
        }
