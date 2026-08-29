"""
Author: liguoqiang
Date: 2026-06-27
Description: 财务管理 API — 股票基本信息、列表、估值、利润。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class FinanceApi:
    """财务管理 API 客户端。

    封装 /api/finance/* 接口：
    - get_stock_info: 查询公司基本信息
    - get_stock_list: 查询股票列表（分页）
    - get_valuation: 查询估值数据
    - get_profit: 查询利润数据
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化财务 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def get_stock_info(self, code: str) -> dict[str, Any]:
        """查询公司基本信息。

        :param code: 股票代码
        :return: 公司基本信息字典
        """
        result: Any = self._client.get(
            "/api/finance/stock_info", {"code": code}
        )
        return dict(result) if result else {}

    def get_stock_list(
        self, skip: int = 0, limit: int = 0
    ) -> list[dict[str, Any]]:
        """查询股票列表（分页）。

        :param skip: 分页偏移（可选）
        :param limit: 分页大小（可选，0 表示不限制）
        :return: 股票列表
        """
        params: dict[str, int] = {}
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit
        result: Any = self._client.get(
            "/api/finance/stock_list", params if params else None
        )
        return list(result) if result else []

    def get_valuation(self, code: str = "", codes: str = "") -> dict[str, Any]:
        """查询估值数据 — 支持单只或批量查询。

        :param code: 单只股票代码（与 codes 二选一）
        :param codes: 多只股票代码，逗号分隔，如 000001,600519
        :return: 估值数据字典，key 为股票代码，value 为估值字段
        """
        params: dict[str, str] = {}
        if code:
            params["code"] = code
        if codes:
            params["codes"] = codes
        result: Any = self._client.get(
            "/api/finance/valuation", params
        )
        if not result:
            return {}
        # 单只返回 dict，批量返回 list[dict]
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            out: dict[str, Any] = {}
            for item in result:
                if isinstance(item, dict):
                    item_code: str = str(item.get("code", item.get("stock_code", "")))
                    if item_code:
                        out[item_code] = item
            return out
        return {}

    def get_profit(
        self, code: str = "", codes: str = "", report_date: str = ""
    ) -> dict[str, Any]:
        """查询利润数据 — 支持单只或批量查询。

        :param code: 单只股票代码（与 codes 二选一）
        :param codes: 多只股票代码，逗号分隔，如 000001,600519
        :param report_date: 报告期 YYYY-MM-DD（可选）
        :return: 利润数据字典，key 为股票代码，value 为利润字段
        """
        params: dict[str, str] = {}
        if code:
            params["code"] = code
        if codes:
            params["codes"] = codes
        if report_date:
            params["report_date"] = report_date
        result: Any = self._client.get("/api/finance/profit", params if params else None)
        if not result:
            return {}
        # 单只返回 dict，批量返回 list[dict]
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            out: dict[str, Any] = {}
            for item in result:
                if isinstance(item, dict):
                    item_code: str = str(item.get("code", item.get("stock_code", "")))
                    if item_code:
                        out[item_code] = item
            return out
        return {}
