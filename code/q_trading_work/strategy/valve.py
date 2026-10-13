"""盘后个股阀门（daily valve）计算。

语义（2026-10-13 定稿）：

- 当日出现 **买入信号** 后，若该股在当日剩余分钟里 **收盘价跌破当日 S1 支撑位**
  达到 ``VALVE_MIN_BREAKS`` 次（默认 2），说明「买了就破位」，次日该股
  **买入门槛上调** ``VALVE_STEP``（默认 +1.0 分）—— 即更难买入。
- 当日出现 **卖出信号** 后，若该股在当日剩余分钟里 **收盘价重新站上当日 R1 阻力位**
  达到同样的次数，说明「卖了就反弹」（卖飞），次日该股 **卖出门槛上调** ``VALVE_STEP``
  —— 即更难卖出。两侧口径对称：**信号之后出现不利走势 → 抬高该侧门槛**。
- 两侧独立；未触发时为 0。每日重新计算、**不累积**（「每日的阀门值可能不一样」）。

支撑/阻力位取 **上一交易日日线** 的经典枢轴点（``InDaySRFactor`` 的 s1 / r1），
与 swing 策略盘中判定用的是同一套点位，避免两处口径漂移。

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


def compute_stock_valve(
    minute_points: Sequence[tuple[str, float]],
    buy_signal_times: Sequence[str],
    sell_signal_times: Sequence[str],
    support: float,
    resistance: float,
    step: float = DEFAULT_STEP,
    min_breaks: int = DEFAULT_MIN_BREAKS,
    max_valve: float = DEFAULT_MAX_VALVE,
) -> dict[str, Any]:
    """计算单只股票当日的阀门。

    锚点取当日 **最早** 的那条同向信号：只要信号出现之后行情继续朝不利方向走，
    就对次日生效（无论后续还有多少条同类信号）。

    :param minute_points: 当日分钟序列 ``[(create_time, close), ...]``
    :param buy_signal_times: 当日买入信号时间列表
    :param sell_signal_times: 当日卖出信号时间列表
    :param support: 当日 S1 支撑位（来自上一交易日枢轴点）
    :param resistance: 当日 R1 阻力位
    :param step: 触发一次上调的幅度
    :param min_breaks: 判定「反复」所需的最少穿破分钟数
    :param max_valve: 阀门上限（避免单日累计过大）
    :return: ``{"buy_valve", "sell_valve", "support_break_count",
        "resistance_break_count", "step"}``
    """
    step = abs(float(step))
    max_valve = abs(float(max_valve))
    min_breaks = max(int(min_breaks), 1)

    buy_anchor: str = _earliest_time(buy_signal_times)
    sell_anchor: str = _earliest_time(sell_signal_times)

    support_breaks: int = count_level_breaks(
        minute_points, buy_anchor, support, below=True
    )
    resistance_breaks: int = count_level_breaks(
        minute_points, sell_anchor, resistance, below=False
    )

    buy_valve: float = 0.0
    if support_breaks >= min_breaks:
        buy_valve = min(step, max_valve)

    sell_valve: float = 0.0
    if resistance_breaks >= min_breaks:
        sell_valve = min(step, max_valve)

    return {
        "buy_valve": round(buy_valve, 4),
        "sell_valve": round(sell_valve, 4),
        "support_break_count": int(support_breaks),
        "resistance_break_count": int(resistance_breaks),
        "step": step,
    }
