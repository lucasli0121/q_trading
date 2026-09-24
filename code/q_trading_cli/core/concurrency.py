#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-09-15
Description: 并发与轮询工具 —

1) run_blocking(): 把同步阻塞函数放进线程池执行，并**继承调用方的上下文变量**
   （contextvars.copy_context()）。

   为什么需要它：NiceGUI 的 app.storage.user 依赖 request_contextvar
   （nicegui/storage.py 中的模块级 ContextVar），而 asyncio 的
   loop.run_in_executor 底层是 ThreadPoolExecutor.submit，**不会**把
   ContextVar 复制到工作线程，因此在线程池里访问 app.storage.user 会抛
   RuntimeError，ApiClient 取不到 token，后台请求不带认证头被后端 401。
   而 NiceGUI 的 Client 长期持有初始 Request（client.request），
   request_contextvar 在握手时 set 过，所以只要把当前上下文复制过去，
   工作线程内就能正确读到**本会话**的 token —— 这也是多用户场景下
   不会串号的关键（相比进程级的全局 fallback token）。

2) PollingTask: 带重入保护与最小间隔的轮询任务。

   为什么需要它：NiceGUI 的 Timer._run_in_loop 逻辑为
       start = time.time()
       await self._invoke_callback()
       dt = time.time() - start
       await asyncio.sleep(self.interval - dt)
   dt 只统计回调耗时。当回调耗时超过 interval 时，sleep 收到 **0 或负数**
   会立即返回，循环退化为**背靠背无间隔执行**（不是阻塞，而是不停地打后端）。
   PollingTask 用"每轮结束后留出完整周期 + 重入跳过"兜住这种情况，
   并把同步 HTTP 放到线程池，保证事件循环不被阻塞。

使用方式:
    from core.concurrency import PollingTask, run_blocking

    data = await run_blocking(fetch_sync)          # 线程池 + 上下文继承
    PollingTask(1.0, refresh).start()              # 替代 ui.timer(1.0, refresh)
"""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
import time
from collections.abc import Callable
from typing import Any

from nicegui import ui

logger = logging.getLogger(__name__)

# 轮询检测间隔上限：PollingTask 用一个较小的 tick 反复检查是否到了执行时刻，
# 从而把"两次执行之间必须留出完整周期"的粒度控制在 tick 以内。
_MAX_TICK_SECONDS: float = 0.25

# tick 下限，避免极小周期导致事件循环空转
_MIN_TICK_SECONDS: float = 0.05


async def run_blocking(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """在线程池中执行同步阻塞函数，并继承调用方的上下文变量。

    与直接使用 loop.run_in_executor 的区别：本函数先用
    contextvars.copy_context() 抓取当前上下文，再用 ctx.run() 在
    工作线程内恢复它，使 app.storage.user / app.storage.browser 等
    依赖 request_contextvar 的能力在线程池中依然可用，
    从而让后台请求携带**当前会话**的 token（而非进程级共享 token）。

    :param fn: 同步函数（内部可安全进行阻塞 HTTP 调用）
    :param args: 位置参数
    :param kwargs: 关键字参数
    :return: fn 的返回值
    """
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    return await loop.run_in_executor(None, lambda: ctx.run(fn, *args, **kwargs))


class PollingTask:
    """带重入保护与最小间隔的轮询任务。

    语义：每轮执行结束后至少间隔 ``period`` 秒才会开始下一轮。
    - 正常情况下（回调耗时远小于周期）：节奏约等于 period（误差不超过 tick）。
    - 回调耗时超过周期时：节奏变为 ``耗时 + period``，**不会**背靠背连续执行，
      从而避免 NiceGUI 内部 ``sleep(interval - dt)`` 收到负数后退化成
      无间隔循环、把后端打爆。

    :param period: 期望的轮询周期（秒）
    :param callback: 回调函数（同步或异步），同步回调会被放进线程池执行
    :param in_thread: 是否把回调放进线程池（回调含同步 HTTP 时应为 True）
    :param name: 任务名（用于日志）
    """

    def __init__(
        self,
        period: float,
        callback: Callable[[], Any],
        *,
        in_thread: bool = True,
        name: str = "",
    ) -> None:
        self.period: float = max(_MIN_TICK_SECONDS, float(period))
        self._callback: Callable[[], Any] = callback
        self._in_thread: bool = in_thread
        self._name: str = name or getattr(callback, "__name__", "polling")
        self._running: bool = False
        self._next_run_at: float = 0.0
        self._timer: ui.timer | None = None
        # 统计信息（便于排查轮询是否成为负担）
        self.run_count: int = 0
        self.skip_count: int = 0
        self.last_cost: float = 0.0

    @property
    def tick(self) -> float:
        """内部检测间隔（始终不大于周期，且不超过 _MAX_TICK_SECONDS）。"""
        return max(_MIN_TICK_SECONDS, min(self.period, _MAX_TICK_SECONDS))

    async def _tick(self) -> None:
        """内部检测函数：到点才真正执行回调，未到点或被跳过时立即返回。"""
        now: float = time.monotonic()
        if self._running:
            # 上一轮尚未结束，跳过本轮（避免任务堆叠）
            self.skip_count += 1
            return
        if now < self._next_run_at:
            # 冷却中：这里主动耗掉一小段时间（不超过 tick），
            # 让 NiceGUI 内部的 sleep(interval - dt) 不会因为 dt≈0 而持续收到
            # 负数退化成紧循环——否则长耗时回调结束后事件循环会被空转打满。
            self.skip_count += 1
            await asyncio.sleep(min(self._next_run_at - now, self.tick))
            return

        self._running = True
        start: float = time.monotonic()
        try:
            if inspect.iscoroutinefunction(self._callback):
                # 异步回调：直接 await（其内部如需阻塞 IO，应自行使用 run_blocking）
                await self._callback()
            elif self._in_thread:
                await run_blocking(self._callback)
            else:
                result: Any = self._callback()
                if inspect.isawaitable(result):
                    await result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[轮询:%s] 回调执行失败", self._name, exc_info=True)
        finally:
            self._running = False
            self.run_count += 1
            self.last_cost = time.monotonic() - start
            # 关键：以"本轮结束时刻 + 完整周期"作为下一轮最早开始时间，
            # 保证两次执行之间一定留有空隙，杜绝连续执行。
            self._next_run_at = time.monotonic() + self.period
            if self.last_cost > self.period:
                logger.warning(
                    "[轮询:%s] 单轮耗时 %.2fs 已超过周期 %.2fs，"
                    "已自动降为「耗时+周期」节奏（原 ui.timer 会在此情况下连续执行）",
                    self._name, self.last_cost, self.period,
                )

    def start(self) -> ui.timer:
        """启动轮询，返回底层 ui.timer（随页面销毁自动取消）。

        :return: ui.timer 实例
        """
        # immediate=False：首次渲染由调用方自行完成，避免重复渲染
        self._timer = ui.timer(self.tick, self._tick, immediate=False)
        return self._timer

    def stop(self) -> None:
        """停止轮询。"""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
