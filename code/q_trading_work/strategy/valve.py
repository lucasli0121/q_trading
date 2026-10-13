"""盘后个股阀门（daily valve）计算。

语义（2026-10-13 定稿，同日扩展为多档位）：

- 当日出现 **买入信号** 后，若该股在当日剩余分钟里 **收盘价跌破当日支撑位**
  达到 ``VALVE_MIN_BREAKS`` 次（默认 2），说明「买了就破位」，次日该股
  **买入门槛上调**。
- 支撑位分三档（``S1 / S2 / S3``，越深离枢轴点越远），**按触达的最深档决定
  上调幅度**：只破 S1 ⇒ ``+1×step``；破到 S2 ⇒ ``+2×step``；破到 S3 ⇒
  ``+3×step``。档位**不叠加** —— 价格单调下行时「跌破 S3」的那些分钟必然
  也同时跌破 S1/S2，逐档相加等于把同一分钟数重复计三次。
- 卖出侧对称：卖出信号后价格反复 **站上 R1 / R2 / R3**（卖飞），次日该股
  **卖出门槛上调**，档位倍数同理。
- 两侧独立；未触发时为 0。每日重新计算、**不累积**（「每日的阀门值可能
  不一样」）。

支撑/阻力位取 **上一交易日日线** 的经典枢轴点（``InDaySRFactor`` 的
s1..s3 / r1..r3），与 swing 策略盘中判定用的是同一套点位，避免两处口径漂移。

档位可达性提醒：``InDaySRFactor`` 的三档是昨日振幅 ``D`` 的 1/3、2/3、1 倍，
价格落在昨日区间 ``[L, H]`` 内时 ``|price − pivot| <= 2D/3``，
因此**触及 S3/R3 必然意味着突破昨日低点/高点**。S3 命中本身就是「创昨日新低
（新高）」，给它最高档加重是有依据的，不是拍脑袋。

本模块只做纯计算（不读配置、不发请求、不写库），便于单测与回测复用。
符号约定：``buy_valve`` / ``sell_valve`` 均为 **非负** 的「抬高幅度」，
上游把 ``sell_valve`` 作用到卖出门槛时按「更难卖」的方向换算。
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

# 默认参数（BaseStrategy 上另有同名类属性，作为策略侧真源；
# 此处的默认值只用于独立调用/单测）
DEFAULT_STEP: float = 1.0
DEFAULT_MIN_BREAKS: int = 2
DEFAULT_MAX_VALVE: float = 5.0


def _is_valid_level(level: float) -> bool:
    """点位是否为可用于判定的有效值（正数且非 NaN）。"""
    try:
        value = float(level)
    except (TypeError, ValueError):
        return False
    return not math.isnan(value) and value > 0


def count_level_breaks(
    minute_points: Sequence[tuple[str, float]],
    after_time: str,
    level: float,
    below: bool,
) -> int:
    """统计 ``after_time``（含）之后、收盘价穿过 ``level`` 的分钟数。

    每个分钟只计一次（按该分钟的收盘价判定），「反复」= 多个不同的分钟。

    :param minute_points: ``[(create_time, close), ...]``，时间字符串按
        ``YYYY-MM-DD HH:MM:SS`` 可比，函数内部按字符串比较（同日内可比）
    :param after_time: 锚点时间（信号出现的分钟）
    :param level: 支撑位（``below=True``）或阻力位（``below=False``）
    :param below: True 统计「跌破」（``close < level``）；False 统计「站上」
        （``close > level``）
    :return: 穿过的分钟数；点位无效或锚点为空时返回 0
    """
    if not after_time or not _is_valid_level(level):
        return 0
    count = 0
    for point in minute_points:
        if not point or len(point) < 2:
            continue
        ts: str = str(point[0] or "")
        if not ts or ts < after_time:
            continue
        try:
            close: float = float(point[1])
        except (TypeError, ValueError):
            continue
        if math.isnan(close):
            continue
        if (close < level) if below else (close > level):
            count += 1
    return count


def _earliest_time(times: Iterable[str]) -> str:
    """取一组时间字符串里最早的一个（空集合返回空串）。"""
    values: list[str] = [str(t) for t in times if t]
    return min(values) if values else ""


def _valid_tiers(tiers: Sequence[float] | None, fallback: float) -> list[float]:
    """归一化档位表：过滤无效点位（0 / NaN / 非数），按入参顺序保留。

    档位表为空（或全无效）时回退到单档 ``fallback``；fallback 也无效则返回空表
    （等价于该侧不做判定）。
    """
    values: list[float] = []
    for level in tiers or ():
        if _is_valid_level(level):
            values.append(float(level))
    if not values and _is_valid_level(fallback):
        values = [float(fallback)]
    return values


def _deepest_tier_breaks(
    minute_points: Sequence[tuple[str, float]],
    after_time: str,
    tiers: Sequence[float],
    below: bool,
    min_breaks: int,
) -> tuple[int, list[int]]:
    """逐档统计穿破分钟数，返回 ``(最深触发档序号, 各档计数)``。

    档序号从 1 开始（1 = 最浅档，即 S1/R1）；``0`` 表示**没有任何档**达到
    ``min_breaks``。因为价格单调下行时 ``count(S1) >= count(S2) >= count(S3)``，
    直接取「最后一个满足阈值的档」即为最深触发档。
    """
    counts: list[int] = [
        count_level_breaks(minute_points, after_time, level, below) for level in tiers
    ]
    depth: int = 0
    for index, count in enumerate(counts, start=1):
        if count >= min_breaks:
            depth = index
    return depth, counts


def compute_stock_valve(
    minute_points: Sequence[tuple[str, float]],
    buy_signal_times: Sequence[str],
    sell_signal_times: Sequence[str],
    support: float = 0.0,
    resistance: float = 0.0,
    step: float = DEFAULT_STEP,
    min_breaks: int = DEFAULT_MIN_BREAKS,
    max_valve: float = DEFAULT_MAX_VALVE,
    support_tiers: Sequence[float] | None = None,
    resistance_tiers: Sequence[float] | None = None,
) -> dict[str, Any]:
    """计算单只股票当日的阀门。

    锚点取当日 **最早** 的那条同向信号：只要信号出现之后行情继续朝不利方向走，
    就对次日生效（无论后续还有多少条同类信号）。

    :param minute_points: 当日分钟序列 ``[(create_time, close), ...]``
    :param buy_signal_times: 当日买入信号时间列表
    :param sell_signal_times: 当日卖出信号时间列表
    :param support: 单档支撑位（``support_tiers`` 缺失时作为唯一的 S1 使用）
    :param resistance: 单档阻力位（``resistance_tiers`` 缺失时作为唯一的 R1 使用）
    :param step: 单档触发一次的上调幅度；实际幅度 = ``step × 最深档序号``
    :param min_breaks: 判定「反复穿破」所需的最少分钟数（各档同一阈值）
    :param max_valve: 单侧阀门上限（避免单日累计过大）
    :param support_tiers: 多档支撑位（浅 → 深，即 S1/S2/S3）；给定时优先于 ``support``
    :param resistance_tiers: 多档阻力位（浅 → 深，即 R1/R2/R3）
    :return: ``{"buy_valve", "sell_valve", "buy_tier", "sell_tier",
        "support_break_count", "resistance_break_count",
        "support_break_counts", "resistance_break_counts", "step"}``
    """
    step = abs(float(step))
    max_valve = abs(float(max_valve))
    min_breaks = max(int(min_breaks), 1)

    sup_tiers: list[float] = _valid_tiers(support_tiers, support)
    res_tiers: list[float] = _valid_tiers(resistance_tiers, resistance)

    buy_anchor: str = _earliest_time(buy_signal_times)
    sell_anchor: str = _earliest_time(sell_signal_times)

    buy_depth, buy_counts = _deepest_tier_breaks(
        minute_points, buy_anchor, sup_tiers, below=True, min_breaks=min_breaks
    )
    sell_depth, sell_counts = _deepest_tier_breaks(
        minute_points, sell_anchor, res_tiers, below=False, min_breaks=min_breaks
    )

    buy_valve: float = min(step * buy_depth, max_valve) if buy_depth else 0.0
    sell_valve: float = min(step * sell_depth, max_valve) if sell_depth else 0.0

    return {
        "buy_valve": round(buy_valve, 4),
        "sell_valve": round(sell_valve, 4),
        "buy_tier": int(buy_depth),
        "sell_tier": int(sell_depth),
        # 单值字段保留「最浅档（S1/R1）」口径，与历史入库字段兼容
        "support_break_count": int(buy_counts[0]) if buy_counts else 0,
        "resistance_break_count": int(sell_counts[0]) if sell_counts else 0,
        "support_break_counts": [int(c) for c in buy_counts],
        "resistance_break_counts": [int(c) for c in sell_counts],
        "step": step,
    }
