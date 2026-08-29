"""
Author: liguoqiang
Date: 2026-06-27
Description: 行情管理 API — 实时行情、日/周/月/分钟 K 线。
"""

from __future__ import annotations

import logging
from typing import Any

from api.client import ApiClient


class MarketApi:
    """行情管理 API 客户端。

    封装 /api/market/* 接口：
    - get_real_time: 实时行情查询
    - get_day_kline: 日 K 线
    - get_week_kline: 周 K 线
    - get_month_kline: 月 K 线
    - get_minute_kline: 分钟 K 线
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化行情 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def get_real_time(
        self,
        code: str = "",
        codes: str = "",
        start_time: str = "",
        end_time: str = "",
        use_default_time: bool = False,
    ) -> list[dict[str, Any]]:
        """实时行情查询 — 单只或多只股票，支持时间范围过滤。

        不传 start_time/end_time: 返回每只股票的最新一条记录。
        传入 start_time/end_time: 返回指定时间范围内的所有记录。

        :param code: 单只股票代码（与 codes 二选一）
        :param codes: 多只股票代码，逗号分隔，如 000001,600519
        :param start_time: 开始时间 YYYY-MM-DD HH:MM:SS（可选）
        :param end_time: 结束时间 YYYY-MM-DD HH:MM:SS（可选）
        :param use_default_time: 是否使用默认时间（可选，默认 False）
        :return: 实时行情数据列表
        """
        params: dict[str, str] = {}
        if code:
            params["code"] = code
        if codes:
            params["codes"] = codes
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        if use_default_time:
            params["use_default_time"] = "true"
        result: Any = self._client.get("/api/market/real_time", params)
        return list(result) if result else []

    def get_day_kline(
        self,
        code: str = "",
        codes: str = "",
        start: str = "",
        end: str = "",
    ) -> list[dict[str, Any]]:
        """日 K 线历史行情 — 支持单只或多只股票。

        :param code: 单只股票代码（与 codes 二选一）
        :param codes: 多只股票代码，逗号分隔，如 000001,600519
        :param start: 开始日期 YYYY-MM-DD（可选）
        :param end: 结束日期 YYYY-MM-DD（可选）
        :return: 日 K 线数据列表，每条记录含 code 字段区分股票
        """
        params: dict[str, str] = {}
        if code:
            params["code"] = code
        if codes:
            params["codes"] = codes
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        result: Any = self._client.get("/api/market/kline/day", params)
        return list(result) if result else []

    def get_week_kline(
        self,
        code: str = "",
        codes: str = "",
        start: str = "",
        end: str = "",
    ) -> list[dict[str, Any]]:
        """周 K 线历史行情 — 支持单只或多只股票。

        :param code: 单只股票代码（与 codes 二选一）
        :param codes: 多只股票代码，逗号分隔，如 000001,600519
        :param start: 开始日期 YYYY-MM-DD（可选）
        :param end: 结束日期 YYYY-MM-DD（可选）
        :return: 周 K 线数据列表
        """
        params: dict[str, str] = {}
        if code:
            params["code"] = code
        if codes:
            params["codes"] = codes
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        result: Any = self._client.get("/api/market/kline/week", params)
        return list(result) if result else []

    def get_month_kline(
        self,
        code: str = "",
        codes: str = "",
        start: str = "",
        end: str = "",
    ) -> list[dict[str, Any]]:
        """月 K 线历史行情 — 支持单只或多只股票。

        :param code: 单只股票代码（与 codes 二选一）
        :param codes: 多只股票代码，逗号分隔，如 000001,600519
        :param start: 开始日期 YYYY-MM-DD（可选）
        :param end: 结束日期 YYYY-MM-DD（可选）
        :return: 月 K 线数据列表
        """
        params: dict[str, str] = {}
        if code:
            params["code"] = code
        if codes:
            params["codes"] = codes
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        result: Any = self._client.get("/api/market/kline/month", params)
        return list(result) if result else []

    def get_minute_kline(
        self,
        code: str,
        start: str = "",
        end: str = "",
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """分钟 K 线行情。

        :param code: 股票代码
        :param start: 开始分钟时间 YYYY-MM-DD HH:MM:00（可选）
        :param end: 结束分钟时间 YYYY-MM-DD HH:MM:00（可选）
        :param skip: 分页偏移（可选）
        :param limit: 分页大小（可选，0 表示不限制）
        :return: 分钟 K 线数据列表
        """
        params: dict[str, Any] = {"code": code}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit
        result: Any = self._client.get("/api/market/kline/minute", params)
        return list(result) if result else []

    def get_valuation_by_cap(
        self,
        cap_min: float = 0.0,
        cap_max: float = 0.0,
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """按总市值范围查询股票代码列表。

        :param cap_min: 总市值下限（含），单位亿元，0 表示不限制
        :param cap_max: 总市值上限（含），单位亿元，0 表示不限制
        :param skip: 分页偏移（可选）
        :param limit: 分页大小（可选，0 表示不限制）
        :return: 股票代码列表
        """
        params: dict[str, Any] = {
            "cap_min": cap_min,
            "cap_max": cap_max,
        }
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit
        result: Any = self._client.get("/api/market/valuation/by_cap", params)
        return list(result) if result else []

    def get_valuation_by_ttm_pe(
        self,
        ttm_min: float = 0.0,
        ttm_max: float = 0.0,
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """按 TTM 市盈率范围查询股票代码列表。

        :param ttm_min: TTM市盈率下限（含），0 表示不限制
        :param ttm_max: TTM市盈率上限（含），0 表示不限制
        :param skip: 分页偏移（可选）
        :param limit: 分页大小（可选，0 表示不限制）
        :return: 股票代码列表
        """
        params: dict[str, Any] = {
            "ttm_min": ttm_min,
            "ttm_max": ttm_max,
        }
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit
        result: Any = self._client.get("/api/market/valuation/by_ttm_pe", params)
        return list(result) if result else []
