"""
Author: liguoqiang
Date: 2026-08-12
Description: 策略选股结果 API — 封装策略选股结果的保存、查询、删除接口。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class StrategySelectStockApi:
    """策略选股结果 API 客户端。

    封装 /api/strategy_select_stocks/* 接口：
    - add: 保存策略选股结果
    - list: 查询策略选股结果列表
    - list_by_user: 按用户查询选股结果
    - delete: 删除选股结果
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化策略选股结果 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def add(
        self,
        user_strategy_id: str,
        stocks: list[Any],
    ) -> list[dict[str, Any]]:
        """保存策略选股结果（含评分）。

        服务端契约已变更：`StrategySelectStockCreateRequest` 由
        `codes: list[str]` 改为 `stocks: list[StrategySelectStockInput]`，
        即每只股票需要携带 `score`（新增字段，缺失按 0.0 处理）。
        为兼容调用方仍可能传 `["000001", ...]` 的旧写法，这里统一归一化：
        字符串元素按 `{"code": x, "score": 0.0}` 处理，字典元素取 code/score。

        注意：服务端 `stocks` 为空会直接返回 400，因此空列表不再发请求。

        :param user_strategy_id: 用户策略 ID
        :param stocks: 选股列表，元素为 `{"code": str, "score": float}` 或纯代码字符串
        :return: 保存结果列表；无有效数据时返回空列表
        """
        normalized: list[dict[str, Any]] = [
            item for item in (self._normalize_stock(s) for s in (stocks or [])) if item
        ]
        if not normalized:
            self.logger.warning(
                "选股结果为空，跳过保存: user_strategy_id=%s", user_strategy_id
            )
            return []
        body: dict[str, Any] = {
            "user_strategy_id": user_strategy_id,
            "stocks": normalized,
        }
        result: Any = self._client.post("/api/strategy_select_stocks/add", body)
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    @staticmethod
    def _normalize_stock(item: Any) -> dict[str, Any]:
        """把单条选股元素归一化为服务端要求的 `{code, score}`。

        :param item: `{"code": ..., "score": ...}` 字典，或纯代码字符串
        :return: `{"code": str, "score": float}`；无法解析出 code 时返回空字典
        """
        if isinstance(item, dict):
            code: str = str(item.get("code", item.get("stock_code", "")) or "")
            if not code:
                return {}
            try:
                score: float = float(item.get("score", 0.0) or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            return {"code": code, "score": score}
        code = str(item or "")
        return {"code": code, "score": 0.0} if code else {}

    def list(
        self,
        user_strategy_id: str = "",
        start_time: str = "",
        end_time: str = "",
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """查询策略选股结果列表。

        :param user_strategy_id: 用户策略 ID（可选；服务端 query 参数名，原为 strategy_id）
        :param start_time: 开始时间 YYYY-MM-DD HH:MM:SS（可选）
        :param end_time: 结束时间 YYYY-MM-DD HH:MM:SS（可选）
        :param skip: 分页偏移（可选）
        :param limit: 分页大小（可选，0 表示不限制）
        :return: 选股结果列表（含实时行情数据）
        """
        params: dict[str, Any] = {}
        if user_strategy_id:
            params["user_strategy_id"] = user_strategy_id
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit
        result: Any = self._client.get(
            "/api/strategy_select_stocks/list",
            params if params else None,
        )
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    def list_by_user(
        self,
        start_time: str = "",
        end_time: str = "",
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """查询当前用户所有策略的选股结果。

        :param start_time: 开始时间 YYYY-MM-DD HH:MM:SS（可选）
        :param end_time: 结束时间 YYYY-MM-DD HH:MM:SS（可选）
        :param skip: 分页偏移（可选）
        :param limit: 分页大小（可选，0 表示不限制）
        :return: 选股结果列表（含实时行情数据）
        """
        params: dict[str, Any] = {}
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit
        result: Any = self._client.get(
            "/api/strategy_select_stocks/by_user",
            params if params else None,
        )
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]

    def delete(self, id: str) -> str:
        """删除选股结果。

        :param id: 选股结果 ID
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(f"/api/strategy_select_stocks/{id}")
        return str(result)
