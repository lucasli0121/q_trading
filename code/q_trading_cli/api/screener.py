"""
Author: liguoqiang
Date: 2026-06-27
Description: 股票筛选 API — 按财务指标范围筛选股票。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class ScreenerApi:
    """股票筛选 API 客户端。

    封装 /api/screener/* 接口：
    - search: 按 TTM 市盈率、总市值、利润率范围筛选股票。

    综合查询公司估值表和公司财务表，返回同时满足所有范围条件的股票列表。
    每个范围参数为 0 表示该维度不设限制。
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化筛选 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def search(
        self,
        ttm_min: float = 0.0,
        ttm_max: float = 0.0,
        cap_min: float = 0.0,
        cap_max: float = 0.0,
        margin_min: float = 0.0,
        margin_max: float = 0.0,
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """按财务指标范围筛选股票。

        综合查询公司估值表（TTM市盈率、总市值）和公司财务表（利润率=净利润/营业总收入），
        返回同时满足所有范围条件的股票列表。
        每个范围参数为 0 表示该维度不设限制。

        :param ttm_min: TTM市盈率下限（含），0 表示不限制
        :param ttm_max: TTM市盈率上限（含），0 表示不限制
        :param cap_min: 总市值下限（含），单位亿元，0 表示不限制
        :param cap_max: 总市值上限（含），单位亿元，0 表示不限制
        :param margin_min: 利润率下限（含），如 0.15 表示 15%，0 表示不限制
        :param margin_max: 利润率上限（含），如 0.30 表示 30%，0 表示不限制
        :param skip: 分页跳过条数（可选）
        :param limit: 分页返回条数（可选，0 表示不限制）
        :return: 满足条件的股票列表（list[StockScreenerItem]）
        """
        params: dict[str, Any] = {
            "ttm_min": ttm_min,
            "ttm_max": ttm_max,
            "cap_min": cap_min,
            "cap_max": cap_max,
            "margin_min": margin_min,
            "margin_max": margin_max,
        }
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit
        result: Any = self._client.get("/api/screener/search", params)
        return list(result) if result else []
