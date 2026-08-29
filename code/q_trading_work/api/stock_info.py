"""
Author: liguoqiang
Date: 2026-06-27
Description: 股票信息查询 API — 按代码、列表、按板块查询。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class StockInfoApi:
    """股票信息查询 API 客户端。

    封装 /api/stock_info/* 接口：
    - get_by_codes: 根据股票代码查询基本信息
    - get_list: 查询全部股票基本信息（分页）
    - get_by_board: 根据板块查询股票基本信息
    - get_hot_industries: 查询热门行业列表
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化股票信息 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def get_by_codes(self, codes: str) -> list[dict[str, Any]]:
        """根据股票代码查询基本信息（支持单个或多个）。

        :param codes: 股票代码，多个用逗号分隔，如 000001.SZ,600519.SH
        :return: 股票基本信息列表（list[StockInfoItem]）
        """
        result: Any = self._client.get(
            "/api/stock_info/code", {"codes": codes}
        )
        return list(result) if result else []

    def get_list(
        self, skip: int = 0, limit: int = 0
    ) -> list[dict[str, Any]]:
        """查询全部股票基本信息（分页）。

        :param skip: 跳过条数（可选）
        :param limit: 返回条数（可选，0 表示不限制）
        :return: 股票基本信息列表（list[StockInfoItem]）
        """
        params: dict[str, int] = {}
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit
        result: Any = self._client.get(
            "/api/stock_info/list", params if params else None
        )
        return list(result) if result else []

    def get_by_board(
        self,
        board: str,
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """根据板块查询股票基本信息。

        :param board: 板块名称，如 主板、创业板、科创板、北交所
        :param skip: 跳过条数（可选）
        :param limit: 返回条数（可选，0 表示不限制）
        :return: 股票基本信息列表（list[StockInfoItem]）
        """
        params: dict[str, Any] = {"board": board}
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit
        result: Any = self._client.get("/api/stock_info/board", params)
        return list(result) if result else []

    def get_hot_industries(self) -> list[dict[str, Any]]:
        """查询热门行业列表。

        :return: 热门行业数据列表，每项含行业名称、代码等字段
        """
        result: Any = self._client.get("/api/stock_info/hot_industry/list")
        return list(result) if result else []

    def add_hot_industry(self, name: str) -> str:
        """添加热门行业。

        :param name: 行业名称
        :return: 操作结果提示信息
        """
        body: dict[str, str] = {"name": name}
        result: Any = self._client.post("/api/stock_info/hot_industry/add", body)
        return str(result)

    def delete_hot_industry(self, name: str) -> str:
        """删除热门行业。

        :param name: 行业名称
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(
            "/api/stock_info/hot_industry/delete", {"name": name}
        )
        return str(result)

    def get_by_industry(self, industry: str) -> list[dict[str, Any]]:
        """根据行业查询股票列表。

        :param industry: 行业名称，如 信息技术、医药生物
        :return: 该行业下的股票基本信息列表
        """
        result: Any = self._client.get(
            "/api/stock_info/industry", {"industry": industry}
        )
        return list(result) if result else []
