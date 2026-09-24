#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-09-15
Description: 策略模板缓存 — 解决 strategy_api.get_by_id() 高频重复请求问题。

背景：策略模板（/api/strategy/id/{id}）在全项目被高频调用——
策略监控页曾对**每条信号**调一次、首页每秒多次、策略列表每个模板一次、
策略选股页每个 Tab 一次，且原先没有任何缓存层。

多用户安全性说明（重要）：
    策略模板是**全局公共资源**（接口为 /api/strategy/id/{id}，不按用户区分，
    也不绑定 token 所属用户），因此进程级共享缓存不会让用户 A 看到用户 B 的
    数据，多用户并发访问安全。缓存内**不得**放入任何用户私有数据
    （如 user_strategies、订单、选股结果等），那些应放在 app.storage.user。

缓存失效：
    - 正缓存 TTL 默认 300 秒（模板信息基本不变，长 TTL 足够）
    - 负缓存 TTL 默认 5 秒（查询失败时短暂记录，避免故障期间反复打后端）
    - invalidate() 可手动失效（模板新增/改名后调用）

使用方式:
    cache = AppContext().strategy_cache
    tpl = cache.get("64f...")                 # 命中缓存或拉取一次
    names = cache.get_many(["id1", "id2"])    # 批量，失败项为 {}
    name = cache.name_of("64f...")            # 只要名称，失败回退空串
"""
from __future__ import annotations

import copy
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class StrategyTemplateCache:
    """策略模板缓存（进程级，仅缓存全局公共的模板数据）。

    线程安全：内部用锁保护字典读写，可从 UI 线程与线程池并发调用。
    """

    # 正缓存有效期（秒）
    TTL_SECONDS: float = 300.0
    # 负缓存有效期（秒）——查询失败时短暂记录，避免故障期间反复请求
    NEGATIVE_TTL_SECONDS: float = 5.0

    def __init__(self) -> None:
        # sid -> (过期时间戳, 模板字典)
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock: threading.Lock = threading.Lock()

    # ---- 查询 ----

    def get(self, strategy_id: str) -> dict[str, Any]:
        """获取策略模板（命中缓存直接返回，否则请求后端并缓存）。

        :param strategy_id: 策略模板 ID
        :return: 策略模板字典；ID 为空或查询失败时返回空字典
        """
        sid: str = str(strategy_id or "").strip()
        if not sid:
            return {}

        now: float = time.monotonic()
        with self._lock:
            cached = self._items.get(sid)
            if cached is not None and cached[0] > now:
                # 返回深拷贝：调用方可能修改 default_params 等字段，
                # 直接交出内部对象会污染缓存（多用户共享）。
                return copy.deepcopy(cached[1])

        tpl: dict[str, Any] = {}
        try:
            from app_context import AppContext
            tpl = AppContext().strategy_api.get_by_id(sid)
        except Exception:
            # 401 等错误交由上层处理（调用方通常已有 UnauthorizedError 分支），
            # 此处仅按负缓存短暂记录，避免同一渲染周期内反复请求。
            logger.debug("查询策略模板失败: %s", sid, exc_info=True)

        ttl: float = self.TTL_SECONDS if tpl else self.NEGATIVE_TTL_SECONDS
        with self._lock:
            self._items[sid] = (time.monotonic() + ttl, tpl)
        return copy.deepcopy(tpl)

    def get_many(self, strategy_ids: list[str] | set[str]) -> dict[str, dict[str, Any]]:
        """批量获取策略模板。

        先收集未命中的 ID，再逐个回源（已去重），命中缓存的不发请求。

        :param strategy_ids: 策略模板 ID 集合（内部去重）
        :return: {sid: 模板字典}，查询失败的项为 {}
        """
        result: dict[str, dict[str, Any]] = {}
        for sid in {str(s or "").strip() for s in strategy_ids}:
            if sid:
                result[sid] = self.get(sid)
        return result

    def name_of(self, strategy_id: str) -> str:
        """获取策略模板名称（失败回退空串）。

        :param strategy_id: 策略模板 ID
        :return: 策略名称
        """
        return str(self.get(strategy_id).get("name", "") or "")

    # ---- 失效 ----

    def invalidate(self, strategy_id: str | None = None) -> None:
        """使缓存失效。

        :param strategy_id: 指定 ID 时仅失效该项；为 None 时清空全部
        """
        with self._lock:
            if strategy_id is None:
                self._items.clear()
            else:
                self._items.pop(str(strategy_id or "").strip(), None)

    def stats(self) -> dict[str, int]:
        """返回缓存统计（用于排查）。

        :return: {"size": 当前条目数, "valid": 未过期条目数}
        """
        now: float = time.monotonic()
        with self._lock:
            items = list(self._items.values())
        return {
            "size": len(items),
            "valid": sum(1 for expire_at, _ in items if expire_at > now),
        }
