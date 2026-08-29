"""
Author: liguoqiang
Date: 2026-07-13
Description: 用户策略关联 API — 管理用户与策略的关联关系、执行结果、运行记录、回测。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


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
        initial_amount: float = 0.0,
        max_stock_count: int = 0,
        strategy_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """创建用户策略关联。

        :param strategy_id: 关联的全局策略 ID（StrategyDao._id）
        :param pool_id: 关联股票池 ID（可选）
        :param status: 策略状态，默认 stopped（可选值: running/stopped/paused）
        :param initial_amount: 初始资金（元）
        :param max_stock_count: 最大持仓数量，默认 0 表示不限制
        :param strategy_params: 策略运行参数（可选）
        :return: 包含 user_strategy 信息的字典
        """
        body: dict[str, Any] = {
            "strategy_id": strategy_id,
            "status": status,
            "total_profit": 0,
            "max_stock_count": max_stock_count,
        }
        if pool_id:
            body["pool_id"] = pool_id
        if initial_amount:
            body["initial_amount"] = initial_amount
        if strategy_params is not None:
            body["strategy_params"] = strategy_params
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
        """修改用户策略关联（状态、股票池、初始资金、最大持仓等）。

        支持的状态值: running(运行中) / stopped(已停止) / paused(已暂停)

        :param user_strategy_id: 用户策略关联 ID
        :param pool_id: 关联股票池 ID（可选，None 表示不修改）
        :param status: 目标状态（可选，None 表示不修改）
        :param initial_amount: 初始资金（可选，None 表示不修改）
        :param max_stock_count: 最大持仓数量（可选，None 表示不修改）
        :param total_profit: 累计盈利（可选，None 表示不修改）
        :param strategy_params: 策略运行参数（可选，None 表示不修改）
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

        :param user_strategy_id: 用户策略关联 ID
        :return: 最近一次策略执行结果字典，无记录时返回空字典
        """
        result: Any = self._client.get(
            f"/api/user_strategy/{user_strategy_id}/execution/latest"
        )
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
