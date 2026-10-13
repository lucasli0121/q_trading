"""盘前涨跌停家数 → 全局买卖门槛偏移。

语义（2026-10-13 定稿）：以 **前一交易日** 的全市场跌停家数为市场风险代理，
按分档映射成一个门槛偏移量 ``offset >= 0``：

- 买入门槛上调 ``+offset``（跌停越多越难买）；
- 卖出门槛下调 ``-offset``（更容易触发卖出，风控优先），但**单独设上限**
  ``MAX_SELL_OFFSET``（见下）。

数据源为 akshare 的涨跌停池接口（``stock_zt_pool_em`` / ``stock_zt_pool_dtgc_em``）。
两个已知限制必须记住：

1. **只有最近约 30 个交易日** 可取，更早的日期跌停接口直接抛错、涨停接口返回 0
   ⇒ 本机制 **实盘盘前可用，历史回测无法验证**（2026-10-13 已实测）。
2. akshare 依赖第三方站点，可能超时或被限流 ⇒ 取不到时 **偏移落 0（不限制）**，
   并打 WARNING。按「拦全部」兜底会让一次接口抖动停掉整个实例的买卖，
   与推送星级门槛的「取不到按不限兜底」保持一致。

**分档基数来自实测**（2026-09-14 ~ 2026-10-13 共 16 个交易日，akshare 可得区间）：

    跌停家数  0  1  2  3  4  8  8  9 10 13 13 13 16 19 27 56
    中位数 10，75 分位 16，90 分位 27，最大值 56（2026-09-28）

据此把 20 作为「明显利空」的分界（用户口径：>20 已经很利空），
>30 归入恐慌档（实测只有 09-28 一天的 56 家）。

**卖出侧上限**：市场偏移对卖出侧取负号 = 放宽卖出门槛。但幅度过大（例如
−3.0 分）意味着「任何轻微负分都卖」，会把正常持有打成高频进出 ——
而本项目实测「交易越密、亏损越重」（9 组回测全负，交易笔数与亏损同向）。
故卖出侧放宽上限单独夹到 ``MAX_SELL_OFFSET``，只做「提前止损」不做「无脑清仓」。
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

logger = logging.getLogger(__name__)

# 分档：(跌停家数上界（含）, 偏移量)
# 依据 2026-09-14 ~ 10-13 实测分布（中位 10 / 75 分位 16 / 90 分位 27 / 最大 56）
DEFAULT_BANDS: tuple[tuple[float, float], ...] = (
    (5.0, 0.0),  # <=5    平静（实测约占 1/3 交易日）
    (10.0, 0.5),  # 6~10   偏弱
    (20.0, 1.0),  # 11~20  弱势（19 家已接近利空分界）
    (30.0, 2.0),  # 21~30  明显利空（用户口径：>20 已经很利空）
    (float("inf"), 3.0),  # >30  恐慌（实测仅 09-28 的 56 家）
)

MAX_SELL_OFFSET: float = 1.5
"""卖出侧放宽幅度上限（绝对值）。买入侧上限为 3.0，卖出侧取一半不到，
避免市场偏移把卖出条件放宽成「见正分就抛」。"""

DEFAULT_STOP_BUY_LIMIT_DOWN: float = 30.0
"""「极端利空禁买」联动的跌停家数阈值（2026-10-13）。

前一交易日跌停家数 **超过** 该值（严格大于）且该股盘后阀门达到 `VALVE_STOP_TIER`
档（跌破 S3）时，当日该股**只卖不买**（见 `BaseStrategy.is_buy_blocked`）。
默认 30 对应实测分布里的最后一档上界（2026-09-14~10-13 最大 56 家）。
``<=0`` 表示关闭该联动，只保留门槛偏移。
"""


def limit_down_offset(
    limit_down: int | float | None,
    bands: Sequence[tuple[float, float]] = DEFAULT_BANDS,
) -> float:
    """把跌停家数映射成买入门槛偏移量（>= 0）。

    **超出最后一档上界时按最后一档取值**（而不是回落 0）—— 跌停家数只会向
    「更差」的方向超出，回落 0 等于在最需要收紧的那天把机制关掉，属于典型的
    静默失效。也正因为这个兜底，cfg 里的分档表**不必写 ``inf``**。

    :param limit_down: 跌停家数；None / 负数视为「无数据」，返回 0
    :param bands: 分档表，按上界升序
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
    return float(bands[-1][1]) if bands else 0.0


def parse_bands(
    text: str | Sequence[tuple[float, float]] | None,
    default: Sequence[tuple[float, float]] = DEFAULT_BANDS,
) -> tuple[tuple[float, float], ...]:
    """解析 cfg 里的分档串，例如 ``"5:0,10:0.5,20:1,30:2"``。

    上界可以留最后一个不写 ``inf``（超出按最后一档处理，见 `limit_down_offset`）。
    空串 / 格式非法 / 上界非升序时返回 ``default`` —— 配置写错时应回退到代码里的
    默认分档，而不是变成「不限制」。

    :param text: 分档串，或已解析好的 ``[(上界, 偏移), ...]``
    :param default: 解析失败时的兜底分档
    :return: 分档元组
    """
    if isinstance(text, (list, tuple)):
        return tuple((float(upper), float(offset)) for upper, offset in text)
    raw: str = str(text or "").strip()
    if not raw:
        return tuple(default)
    parsed: list[tuple[float, float]] = []
    try:
        for item in raw.split(","):
            piece: str = item.strip()
            if not piece:
                continue
            upper_s, sep, offset_s = piece.partition(":")
            if not sep or not offset_s.strip():
                raise ValueError(f"缺少偏移量: {piece}")
            parsed.append((float(upper_s), float(offset_s)))
    except (TypeError, ValueError) as exc:
        logger.warning("涨跌停分档配置非法(%s)，回退默认分档: %s", exc, raw)
        return tuple(default)
    if not parsed:
        return tuple(default)
    uppers: list[float] = [upper for upper, _ in parsed]
    if uppers != sorted(uppers):
        logger.warning("涨跌停分档上界必须升序，回退默认分档: %s", raw)
        return tuple(default)
    return tuple(parsed)


def resolve_market_offset(
    limit_down: int | float | None,
    bands: Sequence[tuple[float, float]] = DEFAULT_BANDS,
    max_sell_offset: float = MAX_SELL_OFFSET,
) -> tuple[float, float]:
    """返回 ``(buy_offset, sell_offset)``。

    买入侧取正、卖出侧取负 —— 跌停多 = 市场转弱 = 更难买、更容易卖。
    卖出侧（负值）的绝对值被夹到 ``max_sell_offset``。

    :param limit_down: 前一交易日跌停家数
    :param bands: 分档表
    :param max_sell_offset: 卖出侧放宽幅度上限（绝对值，<=0 表示不设上限）
    :return: ``(buy_offset >= 0, sell_offset <= 0)``
    """
    offset: float = limit_down_offset(limit_down, bands)
    sell: float = -offset
    cap: float = abs(float(max_sell_offset))
    if cap > 0.0:
        sell = max(sell, -cap)
    return offset, sell



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
