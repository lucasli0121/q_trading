#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-09-15
Description: 用户策略管理器 — 用户策略列表的会话级缓存。

数据存放：
    用户策略列表保存在 app.storage.user["user_strategies"] 中（按会话隔离，
    多用户互不可见），本节流为 app.storage.user["user_strategies_loaded_at"]。

本次改造要点（对应代码审查清单 CR-06 ~ CR-09）：
- CR-06 缓存读取全部改为 .get() 兜底，消除 KeyError。
        不再在 __init__ 里写 app.storage.user —— Singleton 的 __init__ 全进程
        只执行一次，只会对"触发首次实例化的那个会话"生效，其他会话取不到该 key。
- CR-07 新增 ensure_loaded()，由所有页面入口（main_page / main_page_with_content）
        调用，并按会话做节流（默认 60 秒），既保证子页面缓存可用，
        又能感知后端的状态变更（如策略被外部停止）。
- CR-08 remove_user_strategy 修正为可安全调用；新增 delete_user_strategy()
        在删除后端记录的同时清理缓存（当前 UI 尚无删除入口，供后续接入）。
- CR-09 get_user_strategy 原实现为 O(n·m) 的嵌套推导且返回结构错误
        （返回的是 {id: 最后一条匹配} 的 dict 而非按 id 查询的记录），
        改为单 ID 精确查询。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from nicegui import app

from core.singleton import Singleton

logger = logging.getLogger(__name__)

# 缓存键（app.storage.user 内）
_CACHE_KEY: str = "user_strategies"
_LOADED_AT_KEY: str = "user_strategies_loaded_at"


class UserStrategyManager(metaclass=Singleton):
    """用户策略管理器。

    负责把当前用户的策略列表（user_strategy）缓存到 app.storage.user 中，
    供各页面同步读取，避免每次渲染都请求后端。

    缓存刷新策略：
    - 所有页面入口调用 ensure_loaded()，按会话节流（RELOAD_MIN_INTERVAL_SECONDS）
      重新拉取，因此后端的状态变更最多在节流窗口后被感知。
    - 增删改后可主动调用 update_user_strategy / remove_user_strategy 做增量同步。
    """

    # 同一会话内两次自动拉取的最小间隔（秒）
    RELOAD_MIN_INTERVAL_SECONDS: float = 60.0

    def __init__(self) -> None:
        # 刻意不在 __init__ 中初始化 app.storage.user：
        # 1) Singleton 的 __init__ 全进程只执行一次，只对首个会话生效；
        # 2) __init__ 可能在没有请求上下文的线程中被触发，访问 app.storage.user
        #    会抛 RuntimeError。
        # 缓存缺失统一由 _cache() 的 .get() 兜底处理。
        pass

    # ---- 缓存读写（全部走 .get() 兜底，不再直接下标） ----

    @staticmethod
    def _cache() -> list[dict[str, Any]]:
        """读取当前会话缓存的用户策略列表。

        :return: 用户策略列表；会话不可用或无缓存时返回空列表
        """
        try:
            if not app.is_started:
                return []
            data: Any = app.storage.user.get(_CACHE_KEY, [])
            return data if isinstance(data, list) else []
        except Exception:
            # 无请求上下文（如 MQTT 线程）时按空缓存处理，避免抛 RuntimeError
            logger.debug("读取用户策略缓存失败（可能无会话上下文）", exc_info=True)
            return []

    @staticmethod
    def _write_cache(strategies: list[dict[str, Any]]) -> None:
        """写入当前会话缓存并记录拉取时间。

        :param strategies: 用户策略列表
        """
        try:
            if not app.is_started:
                return
            app.storage.user[_CACHE_KEY] = strategies
            app.storage.user[_LOADED_AT_KEY] = time.time()
        except Exception:
            logger.debug("写入用户策略缓存失败（可能无会话上下文）", exc_info=True)

    # ---- 加载 ----

    def ensure_loaded(self, *, force: bool = False) -> None:
        """确保缓存可用：按会话节流地从后端拉取用户策略列表。

        由所有页面入口调用。节流窗口内不重复请求，既避免频繁打后端，
        又能让子页面感知后端的策略状态变更。

        :param force: 为 True 时忽略节流强制刷新
        """
        try:
            if not app.is_started:
                return
            now: float = time.time()
            loaded_at: float = float(app.storage.user.get(_LOADED_AT_KEY, 0) or 0)
            if not force and (now - loaded_at) < self.RELOAD_MIN_INTERVAL_SECONDS:
                return
        except Exception:
            logger.debug("检查用户策略缓存时间失败（可能无会话上下文）", exc_info=True)
            return
        self.load_user_strategies()

    def load_user_strategies(self) -> None:
        """从后端拉取用户策略列表并写入会话缓存。

        失败时不更新时间戳，以便下次页面加载重试。
        """
        from api.client import UnauthorizedError
        from app_context import AppContext
        try:
            strategies: list[dict[str, Any]] = AppContext().user_strategy_api.list()
        except UnauthorizedError:
            # 401 已由 ApiClient._on_401 清除会话（authenticated=False），
            # 心跳定时器（main_page._check_auth_heartbeat）会跳转登录页；
            # 此处静默返回避免异常冒泡中断页面渲染。
            logger.debug("加载用户策略未授权（401），会话已过期，等待跳转登录")
            return
        except Exception:
            logger.warning("加载用户策略失败", exc_info=True)
            return
        self._write_cache(list(strategies or []))

    # ---- 查询 ----

    def list_user_strategies(self) -> list[dict[str, Any]]:
        """列出当前会话缓存的全部用户策略。

        :return: 用户策略列表
        """
        return self._cache()

    def get_user_strategy(self, user_strategy_id: str) -> dict[str, Any] | None:
        """按用户策略 ID 查询缓存的详细信息。

        :param user_strategy_id: 用户策略 ID（UserStrategy 记录 ID）
        :return: 用户策略详细信息，不存在时返回 None
        """
        target: str = str(user_strategy_id or "")
        if not target:
            return None
        for strategy in self._cache():
            if str(strategy.get("_id", strategy.get("id", ""))) == target:
                return strategy
        return None

    def get_user_strategy_ids(self) -> list[str]:
        """获取当前会话所有用户策略的 ID 列表。

        :return: 用户策略 ID 列表
        """
        return [
            str(strategy.get("_id", strategy.get("id", "")))
            for strategy in self._cache()
        ]

    def get_running_user_strategy_ids(self) -> list[str]:
        """获取当前会话中状态为 running 的用户策略 ID 列表。

        :return: 运行中用户策略 ID 列表
        """
        return [
            str(strategy.get("_id", strategy.get("id", "")))
            for strategy in self._cache()
            if strategy.get("status") == "running"
        ]

    # ---- 统计 ----

    def get_running_count(self) -> int:
        """获取当前正在运行的用户策略数量。

        :return: 正在运行的用户策略数量
        """
        return sum(1 for s in self._cache() if s.get("status") == "running")

    def get_strategy_count(self) -> int:
        """获取当前用户策略总数。

        :return: 用户策略总数
        """
        return len(self._cache())

    def get_month_new_count(self) -> int:
        """获取当前月份新增的用户策略数量。

        :return: 当前月份新增的用户策略数量
        """
        from datetime import datetime

        month_prefix: str = datetime.now().astimezone().strftime("%Y-%m")
        return sum(
            1 for s in self._cache()
            if str(s.get("create_time", "")).startswith(month_prefix)
        )

    # ---- 增量更新 ----

    def update_user_strategy(self, user_strategy_id: str, strategy_info: dict[str, Any]) -> None:
        """更新指定用户策略的缓存信息（合并写入）。

        :param user_strategy_id: 用户策略 ID
        :param strategy_info: 用户策略信息（未提供的字段保持原值）

        注意：采用合并而非整体替换。调用方可能只传入展示用的行数据
        （仅含 us_id/sid/name 等），整体替换会丢掉 _id/strategy_id/status
        等原始字段，导致依赖这些字段的页面（如策略选股）取不到策略。
        """
        if not app.is_started:
            return
        try:
            strategies: list[dict[str, Any]] = self._cache()
            has_updated: bool = False
            for i, strategy in enumerate(strategies):
                if str(strategy.get("_id", strategy.get("id", ""))) == user_strategy_id:
                    merged: dict[str, Any] = dict(strategy)
                    merged.update(strategy_info)
                    strategies[i] = merged
                    has_updated = True
                    break
            if not has_updated:
                strategies.append(strategy_info)
            self._write_cache(strategies)
        except Exception:
            logger.warning("更新用户策略缓存失败: %s", user_strategy_id, exc_info=True)

    def remove_user_strategy(self, user_strategy_id: str) -> None:
        """从缓存中移除指定用户策略。

        :param user_strategy_id: 用户策略 ID
        """
        if not app.is_started:
            return
        try:
            remaining: list[dict[str, Any]] = [
                strategy for strategy in self._cache()
                if str(strategy.get("_id", strategy.get("id", ""))) != user_strategy_id
            ]
            self._write_cache(remaining)
        except Exception:
            logger.warning("移除用户策略缓存失败: %s", user_strategy_id, exc_info=True)

    def delete_user_strategy(self, user_strategy_id: str) -> None:
        """删除后端用户策略记录，并同步清理会话缓存。

        供"删除策略"入口调用。当前页面尚无删除入口，接入时直接调用本方法即可。

        :param user_strategy_id: 用户策略 ID
        :raises ApiError: 后端业务错误
        :raises UnauthorizedError: 401 未授权
        :raises httpx.HTTPStatusError: 其他 HTTP 错误
        """
        from app_context import AppContext

        AppContext().user_strategy_api.delete(user_strategy_id)
        self.remove_user_strategy(user_strategy_id)
        logger.info("已删除用户策略并清理缓存: %s", user_strategy_id)
