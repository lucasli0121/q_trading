"""
Author: liguoqiang
Date: 2026-07-13
Description: 用户策略关联 API — 管理用户与策略的关联关系、执行结果、运行记录、回测。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from api.client import ApiClient, is_not_found
from dao.user_strategy_dao import (
    DEFAULT_INITIAL_AMOUNT,
    DEFAULT_MAX_STOCK_COUNT,
)


class UserStrategyApi:
    """用户策略关联 API 客户端。

    封装 /api/user_strategy/* 接口：
    - create: 创建用户策略关联
    - delete: 删除用户策略关联
    - get: 查询单个用户策略关联
    - list: 查询用户所有策略关联
    - update: 修改用户策略关联（状态、股票池等）

    以及子资源接口：
    - save_execution: 保存策略执行结果
    - get_execution: 查询策略执行结果
    - get_latest_execution: 查询最近一次策略执行结果
    - delete_execution: 删除策略执行结果
    - list_executions: 查询用户所有策略执行结果
    - save_runlog: 保存运行记录
    - get_runlog: 查看运行记录
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化用户策略关联 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    # ---- 用户策略关联 CRUD ----

    def create(
        self,
        strategy_id: str,
        pool_id: str = "",
        status: str = "stopped",
        initial_amount: float = DEFAULT_INITIAL_AMOUNT,
        max_stock_count: int = DEFAULT_MAX_STOCK_COUNT,
    ) -> dict[str, Any]:
        """创建用户策略关联。

        :param strategy_id: 关联的全局策略 ID（StrategyDao._id）
        :param pool_id: 关联股票池 ID（可选）
        :param status: 策略状态，默认 stopped（可选值: running/stopped/paused）
        :param initial_amount: 初始资金（元）；**0 表示限制买入**（不允许新建持仓/加仓）。
            默认 10 万元，与服务端默认值一致
        :param max_stock_count: 最大持仓数量；**0 表示限制买入**（不允许新建持仓/加仓），
            `-1` 表示不限制。默认 -1（不限制），与服务端默认值一致
        :return: 包含 user_strategy 信息的字典
        """
        # 2026-09-24 语义确认：`max_stock_count=0` 与 `initial_amount=0` 都是「限制买入」，
        # 不是「不限制」。两者都显式下发（旧实现 `if initial_amount:` 会把 0 静默省略，
        # 语义依赖服务端 default，含义不明确）。
        body: dict[str, Any] = {
            "strategy_id": strategy_id,
            "status": status,
            "total_profit": 0,
            "max_stock_count": max_stock_count,
            "initial_amount": initial_amount,
        }
        if pool_id:
            body["pool_id"] = pool_id
        result: Any = self._client.post("/api/user_strategy/create", body)
        return dict(result)

    def delete(self, user_strategy_id: str) -> str:
        """删除用户策略关联。

        :param user_strategy_id: 用户策略关联 ID
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(f"/api/user_strategy/{user_strategy_id}")
        return str(result)

    def get(self, user_strategy_id: str) -> dict[str, Any]:
        """查询单个用户策略关联详情。

        :param user_strategy_id: 用户策略关联 ID
        :return: 用户策略关联信息字典
        """
        result: Any = self._client.get(f"/api/user_strategy/{user_strategy_id}")
        return dict(result)

    def list(self) -> list[dict[str, Any]]:
        """查询用户所有策略关联（需要认证，返回当前用户数据）。

        :return: 用户策略关联列表
        """
        result: Any = self._client.get("/api/user_strategy/list")
        return list(result) if result else []

    def list_all(self) -> list[dict[str, Any]]:
        """查询所有用户的策略关联（无认证接口，供后台 workflow 使用）。

        :return: 所有用户的策略关联列表
        """
        result: Any = self._client.get("/api/user_strategy/all")
        return list(result) if result else []

    def update(
        self,
        user_strategy_id: str,
        pool_id: str | None = None,
        status: str | None = None,
        initial_amount: float | None = None,
        max_stock_count: int | None = None,
        total_profit: float | None = None,
        strategy_params: dict[str, Any] | None = None,
    ) -> str:
        """修改用户策略关联（状态、股票池、初始资金、最大持仓、策略参数等）。

        支持的状态值: running(运行中) / stopped(已停止) / paused(已暂停)

        :param user_strategy_id: 用户策略关联 ID
        :param pool_id: 关联股票池 ID（可选，None 表示不修改）
        :param status: 目标状态（可选，None 表示不修改）
        :param initial_amount: 初始资金（可选，None 表示不修改）；
            **0 表示限制买入**（不允许新建持仓/加仓）
        :param max_stock_count: 最大持仓数量（可选，None 表示不修改）；
            **0 表示限制买入**，`-1` 表示不限制
        :param total_profit: 累计盈利（可选，None 表示不修改）
        :param strategy_params: 策略运行参数（可选，None 表示不修改）。采用**纯值**结构
            `{"match": {...}, "buy": {...}, "sell": {...}, "risk": {...}}`，
            与 `cfg/strategy.yaml` 一致（`strategy.default_params` 才是 `{desc, value}` 包装）。
            **注意非空时优先级高于策略模板的 default_params**（`BaseStrategy._apply_resolved_params`
            是择一而非逐字段合并），因此这里漏掉某个字段时，该字段只能靠策略类属性兜底
            —— 界面上看不到、也调不了。
        :return: 操作结果提示信息
        """
        body: dict[str, Any] = {}
        if pool_id is not None:
            body["pool_id"] = pool_id
        if status is not None:
            body["status"] = status
        if initial_amount is not None:
            body["initial_amount"] = initial_amount
        if max_stock_count is not None:
            body["max_stock_count"] = max_stock_count
        if total_profit is not None:
            body["total_profit"] = total_profit
        if strategy_params is not None:
            body["strategy_params"] = strategy_params
        result: Any = self._client.patch(
            f"/api/user_strategy/{user_strategy_id}", body
        )
        return str(result)

    # ---- 执行结果 ----

    def save_execution(
        self,
        user_strategy_id: str,
        current_return_rate: float = 0.0,
        current_profit: float = 0.0,
        annualized_return_rate: float = 0.0,
        benchmark_return_rate: float = 0.0,
        positions: list[dict[str, Any]] | None = None,
        initial_amount: float = 0.0,
        remaining_cash: float = 0.0,
        start_date: str = "",
        execution_days: int = 0,
    ) -> str:
        """保存策略执行结果（每次持仓变动都会新增一条执行记录）。

        :param user_strategy_id: 用户策略关联 ID
        :param current_return_rate: 目前收益率（如 0.15 = 15%）
        :param current_profit: 目前收益金额（已实现盈利累计）
        :param annualized_return_rate: 年化收益率
        :param benchmark_return_rate: 基准收益率（如沪深300同期收益）
        :param positions: 持仓情况列表
        :param initial_amount: 初始金额
        :param remaining_cash: 剩余资金（每次买卖股票后的剩余金额）
        :param start_date: 开始日期 YYYY-MM-DD
        :param execution_days: 执行天数
        :return: 操作结果提示信息
        """
        body: dict[str, Any] = {
            "current_return_rate": current_return_rate,
            "current_profit": current_profit,
            "annualized_return_rate": annualized_return_rate,
            "benchmark_return_rate": benchmark_return_rate,
            "positions": positions or [],
            "initial_amount": initial_amount,
            "remaining_cash": remaining_cash,
            "start_date": start_date,
            "execution_days": execution_days,
        }
        result: Any = self._client.post(
            f"/api/user_strategy/{user_strategy_id}/execution", body
        )
        return str(result)

    def get_execution(self, user_strategy_id: str) -> list[dict[str, Any]]:
        """查询策略执行结果列表。

        :param user_strategy_id: 用户策略关联 ID
        :return: 策略执行结果字典列表
        """
        result: Any = self._client.get(
            f"/api/user_strategy/{user_strategy_id}/execution"
        )
        return list(result) if result else []

    def get_latest_execution(self, user_strategy_id: str) -> dict[str, Any]:
        """查询最近一次的策略执行结果。

        **404 = 该策略尚无任何执行记录，收敛为空字典**，按「无持仓、资金本地推算」
        处理。这不是容错，而是修一个死锁：

        - 执行记录的唯一来源是订单成交后由 `TradeManager` 写入；
        - 而订单是否创建，取决于调用方 `_load_positions` 能否读到持仓；
        - 若把 404 当「取数失败」→ 跳过下单 → 永远没有执行记录 → 永远 404。

        2026-09-24 线上实测 `user_strategy_id=6aab518ad097eabd87115e90`（波段交易策略）
        即为该状态：14 条交易信号、**0 条订单**、0 条执行记录，新策略下不了第一单。

        真实故障（超时 / 5xx / 连接失败 / 401）照旧上抛，由调用方 fail-closed。
        该 404 的日志已由 `allow_not_found=True` 降为 DEBUG，不污染 errors.log。

        :param user_strategy_id: 用户策略关联 ID
        :return: 最近一次策略执行结果字典，无记录时返回空字典
        """
        try:
            result: Any = self._client.get(
                f"/api/user_strategy/{user_strategy_id}/execution/latest",
                allow_not_found=True,
            )
        except httpx.HTTPStatusError as exc:
            if not is_not_found(exc):
                raise
            self.logger.debug(
                "user_strategy_id=%s 尚无执行记录（服务端 404 表示无数据，按无持仓处理）",
                user_strategy_id,
            )
            return {}
        return dict(result) if result else {}

    def delete_execution(self, user_strategy_id: str) -> str:
        """删除策略执行结果。

        :param user_strategy_id: 用户策略关联 ID
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(
            f"/api/user_strategy/{user_strategy_id}/execution"
        )
        return str(result)

    def list_executions(self) -> list[dict[str, Any]]:
        """查询用户所有策略的执行结果列表。

        :return: 策略执行结果列表
        """
        result: Any = self._client.get("/api/user_strategy/executions")
        return list(result) if result else []

    # ---- 运行记录 ----

    def save_runlog(
        self,
        user_strategy_id: str,
        log_content: str,
        level: str = "INFO",
    ) -> str:
        """保存运行记录。

        :param user_strategy_id: 用户策略关联 ID
        :param log_content: 日志内容
        :param level: 日志级别（默认 INFO）
        :return: 操作结果提示信息
        """
        body: dict[str, str] = {
            "log_content": log_content,
            "level": level,
        }
        result: Any = self._client.post(
            f"/api/user_strategy/{user_strategy_id}/runlog", body
        )
        return str(result)

    def get_runlog(
        self,
        user_strategy_id: str,
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """查看运行记录。

        :param user_strategy_id: 用户策略关联 ID
        :param skip: 分页偏移（可选）
        :param limit: 分页大小（可选，0 表示不限制）
        :return: 运行记录列表
        """
        params: dict[str, int] = {}
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit
        result: Any = self._client.get(
            f"/api/user_strategy/{user_strategy_id}/runlog",
            params if params else None,
        )
        return list(result) if result else []
