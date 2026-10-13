"""盘前涨跌停家数 → 全局买卖门槛偏移。

语义（2026-10-13 定稿）：以 **前一交易日** 的全市场跌停家数为市场风险代理，
按分档映射成一个门槛偏移量 ``offset >= 0``：

- 买入门槛上调 ``+offset``（跌停越多越难买）；
- 卖出门槛下调 ``-offset``（更容易触发卖出，风控优先）。

数据源为 akshare 的涨跌停池接口（``stock_zt_pool_em`` / ``stock_zt_pool_dtgc_em``）。
两个已知限制必须记住：

1. **只有最近约 30 个交易日** 可取，更早的日期跌停接口直接抛错、涨停接口返回 0
   ⇒ 本机制 **实盘盘前可用，历史回测无法验证**（2026-10-13 已实测）。
2. akshare 依赖第三方站点，可能超时或被限流 ⇒ 取不到时 **偏移落 0（不限制）**，
   并打 WARNING。按「拦全部」兜底会让一次接口抖动停掉整个实例的买卖，
   与推送星级门槛的「取不到按不限兜底」保持一致。
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

logger = logging.getLogger(__name__)

# 分档：(跌停家数上界（含）, 偏移量)
DEFAULT_BANDS: tuple[tuple[float, float], ...] = (
    (5.0, 0.0),
    (15.0, 0.5),
    (30.0, 1.0),
    (float("inf"), 2.0),
)


def limit_down_offset(
    limit_down: int | float | None,
    bands: Sequence[tuple[float, float]] = DEFAULT_BANDS,
) -> float:
    """把跌停家数映射成买入门槛偏移量（>= 0）。

    :param limit_down: 跌停家数；None / 负数视为「无数据」，返回 0
    :param bands: 分档表，按上界升序；最后一项上界应为 inf
    :return: 偏移量；无数据时 0.0
    """
    if limit_down is None:
        return 0.0
    try:
        value: float = float(limit_down)
    except (TypeError, ValueError):
        return 0.0
    if value < 0:
        return 0.0
    for upper, offset in bands:
        if value <= upper:
            return float(offset)
    return 0.0


def resolve_market_offset(
    limit_down: int | float | None,
    bands: Sequence[tuple[float, float]] = DEFAULT_BANDS,
) -> tuple[float, float]:
    """返回 ``(buy_offset, sell_offset)``。

    买入侧取正、卖出侧取负 —— 跌停多 = 市场转弱 = 更难买、更容易卖。
    """
    offset: float = limit_down_offset(limit_down, bands)
    return offset, -offset


def fetch_limit_counts(date_str: str) -> tuple[int | None, int | None, str]:
    """拉取指定交易日的涨停/跌停家数。

    :param date_str: 交易日 ``YYYYMMDD``（akshare 接口要求的格式）
    :return: ``(limit_up, limit_down, error)``；取不到时对应值为 None，
        ``error`` 为原因描述（空串表示成功）
    """
    try:
        import akshare as ak  # 延迟导入：生产环境可能未安装，避免导入期报错
    except Exception as exc:  # noqa: BLE001
        return None, None, f"akshare 不可用: {exc}"

    limit_up: int | None = None
    limit_down: int | None = None
    errors: list[str] = []

    try:
        df_up: Any = ak.stock_zt_pool_em(date=date_str)
        limit_up = int(len(df_up)) if df_up is not None else 0
    except Exception as exc:  # noqa: BLE001
        errors.append(f"涨停池: {exc}")

    try:
        df_down: Any = ak.stock_zt_pool_dtgc_em(date=date_str)
        limit_down = int(len(df_down)) if df_down is not None else 0
    except Exception as exc:  # noqa: BLE001
        errors.append(f"跌停池: {exc}")

    return limit_up, limit_down, "；".join(errors)
