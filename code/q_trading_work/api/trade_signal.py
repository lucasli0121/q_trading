"""
Author: liguoqiang
Date: 2026-08-03
Description: 交易信号 API — 封装交易信号的保存、查询、列表接口。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from api.client import ApiClient, is_not_found

logger = logging.getLogger(__name__)

STAR_MAX: float = 5.0
"""星级上限，与服务端 `_validate_star_value` 一致（`web_api/routers/trade_signal.py`）。"""


def _as_star(value: Any) -> float:
    """把星级截断到服务端字段约束 [0, 5]，保留一位小数。

    与 `_as_text` 同一层防御。服务端对越界 `star` 返回 400
    （`{"detail": "star 应在 0~5.0 之间"}`），而 `_save_trade_signal` 里
    `add()` 抛异常会让**整条信号**连同 MQTT 推送、企业微信/短信推送、
    订单创建一起被跳过 —— 一个越界的星级等于一条信号凭空消失。

    策略侧已统一用 `BaseStrategy.score_to_star()` 夹值，这里再兜一次：
    将来新增策略或调用点漏夹时，最坏也只是星级被截断 + 一条 WARNING，
    而不是信号丢失。（2026-10-12：波段策略卖出侧漏夹，3 条深跌卖出信号
    连同下单一起被丢弃。）

    注意这里只做**截断**、不做取绝对值：入参语义已经是「星级」，
    负数说明上游算错了符号，截成 0 并告警，不猜它的本意。

    :param value: 任意待归一化的星级
    :return: 落在 [0, 5] 的星级，保留一位小数
    """
    try:
        number: float = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN：任何比较都不成立，显式落 0
        return 0.0
    if number < 0 or number > STAR_MAX:
        logger.warning(
            "star 超出服务端允许范围 [0, %.1f]，已截断: 原值=%s", STAR_MAX, value
        )
    return round(max(0.0, min(number, STAR_MAX)), 1)


def _as_text(value: Any) -> str:
    """把任意值归一化成字符串，保证请求体符合服务端 `str` 字段契约。

    背景（2026-09-15 线上 422）：`swing_trading_strategy` 曾把
    `extra["reason"] = (f"..."),` 写成了单元素 tuple，JSON 序列化后变成数组，
    服务端 `TradeSignalCreateRequest.reason: str` 校验报 422，整条交易信号
    （含 MQTT 推送与订单创建判定）全部被丢弃。为让「上游给脏值」不再演变成
    「信号全丢」，这里在 API 边界统一兜底：

    - None → `""`
    - str → 原样返回
    - tuple / list → 用 ", " 连接各元素（保留原因内容，丢掉的只是错误容器类型）
    - 其他 → `str(value)`

    :param value: 任意待归一化的值
    :return: 可直接写入 JSON 请求体的字符串
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (tuple, list)):
        return ", ".join(_as_text(item) for item in value)
    return str(value)


class TradeSignalApi:
    """交易信号 API 客户端。

    封装 /api/trade_signal/* 接口：
    - add: 保存交易信号
    - latest: 查询最近一次交易信号
    - list: 查询交易信号列表
    """

    def __init__(self, client: ApiClient) -> None:
        """初始化交易信号 API 客户端。

        :param client: ApiClient 实例
        """
        self._client: ApiClient = client
        self.logger = logging.getLogger(__name__)

    def add(
        self,
        strategy_id: str,
        user_strategy_id: str,
        stock_code: str,
        trade_price: float,
        action: str,
        profit_rate: float = 0.0,
        profit_amount: float = 0.0,
        reason: str = "",
        score: float = 0.0,
        star: float = 0.0,
    ) -> dict[str, Any]:
        """保存交易信号。

        :param strategy_id: 策略模板 ID
        :param user_strategy_id: 用户策略id
        :param stock_code: 股票代码
        :param trade_price: 交易价格
        :param action: 交易方向（买入/卖出）
        :param profit_rate: 收益率（卖出时计算）
        :param profit_amount: 收益金额（卖出时计算）
        :param reason: 信号原因说明（可选）
        :param score: 策略打分（服务端 `TradeSignalCreateRequest.score`，默认 0）
        :param star: 星级，最大 5 星、保留一位小数（服务端同名字段，默认 0）
        :return: 保存结果字典
        """
        # 服务端 TradeSignalCreateRequest 的 str 字段不接受 null / 数组，
        # 这里统一归一化，避免上游脏值导致整条信号保存 422（详见 _as_text）。
        # star 越界则会让服务端返回 400（详见 _as_star）。
        body: dict[str, Any] = {
            "strategy_id": _as_text(strategy_id),
            "user_strategy_id": _as_text(user_strategy_id),
            "stock_code": _as_text(stock_code),
            "trade_price": trade_price,
            "action": _as_text(action),
            "profit_rate": round(profit_rate, 2),
            "profit_amount": round(profit_amount, 2),
            "reason": _as_text(reason),
            "score": round(float(score or 0.0), 2),
            "star": _as_star(star),
        }
        result: Any = self._client.post("/api/trade_signal/add", body)
        return dict(result) if result else {}

    def latest(
        self,
        strategy_id: str,
        user_strategy_id: str,
        stock_code: str,
        action: str = "",
    ) -> dict[str, Any]:
        """查询最近一次交易信号。

        服务端在「该股票还没有该类信号」时返回 404 + {"detail": "未找到交易信号"}，
        这是**正常业务状态**（首次买入前查历史买入信号、从未卖出的股票查历史卖出
        信号都会命中），因此 404 收敛为空字典、日志降为 DEBUG。

        调用方 `_save_trade_signal` 用它计算卖出收益率、以及信号风暴过滤的
        价格比较，两者都只把空结果当「无历史可比对」处理。

        :param strategy_id: 策略模板 ID
        :param stock_code: 股票代码
        :param action: 交易方向筛选（可选，买入/卖出，不传则不过滤方向）
        :return: 最近一次交易信号字典，无记录时返回空字典
        """
        params: dict[str, str] = {
            "strategy_id": strategy_id,
            "user_strategy_id": user_strategy_id,
            "stock_code": stock_code,
        }
        if action:
            params["action"] = action
        try:
            result: Any = self._client.get(
                "/api/trade_signal/latest",
                params=params,
                allow_not_found=True,
            )
        except httpx.HTTPStatusError as exc:
            if not is_not_found(exc):
                raise
            self.logger.debug("无历史交易信号（服务端 404 表示无数据）: %s", params)
            return {}
        return dict(result) if result else {}

    def delete(self, signal_id: str) -> str:
        """删除交易信号。

        :param signal_id: 信号 ID
        :return: 操作结果提示信息
        """
        result: Any = self._client.delete(f"/api/trade_signal/{signal_id}")
        return str(result)

    def list(
        self,
        strategy_id: str | None = None,
        user_strategy_id: str | None = None,
        action: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """查询交易信号列表。

        :param strategy_id: 策略模板 ID（可选）
        :param user_strategy_id: 用户策略ID
        :param action: 交易方向筛选（可选，买入/卖出）
        :param start_time: 开始时间（可选，格式 YYYY-MM-DD HH:MM:SS）
        :param end_time: 结束时间（可选，格式 YYYY-MM-DD HH:MM:SS）
        :param skip: 分页偏移（可选）
        :param limit: 分页大小（可选，0 表示不限制）
        :return: 交易信号列表
        """
        params: dict[str, Any] = {}
        if strategy_id:
            params["strategy_id"] = strategy_id
        if user_strategy_id:
            params["user_strategy_id"] = user_strategy_id
        if action:
            params["action"] = action
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        if skip:
            params["skip"] = skip
        if limit:
            params["limit"] = limit

        result: Any = self._client.get(
            "/api/trade_signal/list",
            params if params else None,
        )
        if not result:
            return []
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(result)]
