#!/usr/bin/env python3
"""
Description: 持仓盈亏口径 — 「当前持仓未实现盈亏」与「当前收益率」的唯一计算入口。

概要页（资金曲线末点、近期更新策略表）与策略监控页（卡片浮动盈亏）共用本模块，
避免两处各写一套算法后随版本漂移。

⚠ 后端契约（源码 `q_trading_work/trade/manager.py`，改动前请重新核对，勿凭印象）：

- ``StrategyExecutionDao.current_profit``：**已实现**盈利累计，只在卖出时累加
  （``_refresh_execution_metrics`` 里 ``execution.current_profit += order.profit_amount``），
  并同步到 ``UserStrategyDao.total_profit``（两者同义）。
- ``StrategyExecutionDao.current_return_rate`` = ``current_profit / base_amount``，
  ``base_amount`` 取 ``initial_amount``、为 0 时退化为持仓成本合计。源码注释原话是
  「计算总收益率（已实现收益 / 初始资金）」——**只含已实现，不含浮动盈亏**。
- ``PositionItem.profit_rate / profit_amount`` 是**成交时点快照**：买入时写入、
  持仓期间后端不参与计算、直到卖出才重算。**不得**拿来当浮动盈亏展示。

因此页面上要展示的「当前收益率」必须由前端现算：

    当前收益率 = (current_profit + Σ(实时价 − 成本价) × 数量) / base_amount

未实现那一段只对**最新一条执行记录**计算：历史记录对应的是当时的持仓，实时价无法回溯。

概要页「系统总收益曲线」（2026-09-30 定稿，此前一度改成求和）：
- 曲线**每个点** = 各用户策略收益率的**平均值**（见 :func:`daily_average_rates`），
  只含已实现。单个策略当天的取值 = 它当天**最后一条**记录的 ``current_return_rate``
  （见 :func:`strategy_day_rates`）：该字段是累计值，同一天多条记录相加会把已实现收益
  重复累加（真机上一天 17 条记录把总收益从 -3.73 放大到 -39.36），所以**不取和**。
  当天没有记录的策略**沿用其最近一次的取值**（不归零，否则平均值会随「当天谁交易了」
  上下跳）。窗口开始之前的策略取值由全量记录补（时间范围接口只回窗口内的记录，见
  :func:`strategy_levels_before`）。
- ⚠ **收益为 0 的策略不计入平均**（既不算进分子、也不占分母，见
  :func:`effective_rates`）：新建 / 还没交易过的策略收益率恒为 0，把它们算进分母会
  把平均值往 0 拉。某天所有策略的取值都是 0 ⇒ 该点就是 0.0（曲线不中断）。
- **末点**由 :func:`average_current_rate_pct` 覆盖：各策略最新记录的含浮盈收益率取
  **平均**（同样排除 0）；缺实时价的策略退化到已实现口径，不剔除。

行情与股票名称：:func:`fetch_price_map`（实时价）与 :func:`fetch_stock_name_map`
（名称，进程级缓存）是展示层的取数出口；代码比对一律走 :func:`lookup_stock_name`，
它兼容 ``000001`` 与 ``000001.SZ`` 两种写法。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import Any

from utils.tools import to_float

logger = logging.getLogger(__name__)


def build_price_map(quotes: list[dict[str, Any]]) -> dict[str, float]:
    """把行情接口返回的列表转成 ``{股票代码: 实时价}``。

    只认正价：接口返回 0 / 空值时按「没有实时价」处理，不能当成跌到 0
    （否则持仓会显示成 −100% 的巨亏）。

    :param quotes: 行情列表，每项含 code 与 price（或 close）
    :return: {股票代码: 实时价}，无有效价的代码不在结果里
    """
    price_map: dict[str, float] = {}
    for hq in quotes:
        code: str = str(hq.get("code", "") or "")
        px: float = to_float(hq.get("price") or hq.get("close") or 0)
        if code and px > 0:
            price_map[code] = px
    return price_map


def fetch_price_map(codes: Iterable[str]) -> dict[str, float]:
    """批量查询多只股票的实时价（一次请求）。

    :param codes: 股票代码集合（自动去重、忽略空值）
    :return: {股票代码: 实时价}；无代码或查询失败时为空映射
    """
    code_set: set[str] = {str(c).strip() for c in codes if str(c).strip()}
    if not code_set:
        return {}
    # 延迟导入：app_context 顶层 import 了 api.*，避免模块级成环
    from app_context import AppContext

    try:
        quotes: list[dict[str, Any]] = AppContext().market_api.get_real_time(
            codes=",".join(sorted(code_set))
        )
    except Exception:
        logger.warning("查询实时行情失败", exc_info=True)
        return {}
    return build_price_map(quotes)


def build_name_map(infos: list[dict[str, Any]]) -> dict[str, str]:
    """把股票信息接口返回的列表转成 ``{股票代码: 股票名称}``。

    :param infos: 股票信息列表，每项含 code 与 name
    :return: {股票代码: 名称}，无代码或无名称的条目被丢弃
    """
    name_map: dict[str, str] = {}
    for info in infos:
        code: str = str(info.get("code", "") or "").strip()
        name: str = str(info.get("name", "") or "").strip()
        if code and name:
            name_map[code] = name
    return name_map


def normalize_stock_code(code: str) -> str:
    """把股票代码归一成**去交易所后缀**的形式（``000001.SZ`` → ``000001``）。

    信号里的 ``stock_code`` 可能是 ``000001``，而股票信息接口返回的可能是
    ``000001.SZ``（反之亦然）。比对与缓存一律走归一化后的形式，避免同一只股票
    因为写法不同被当成两个代码（查不到名称、或反复向后端请求）。

    :param code: 股票代码（可带后缀）
    :return: 归一化后的代码；空输入返回空串
    """
    return str(code or "").strip().split(".")[0]


def lookup_stock_name(code: str, name_map: dict[str, str]) -> str:
    """从 ``{代码: 名称}`` 里查名称，**兼容带交易所后缀的代码**。

    :param code: 股票代码（可带后缀）
    :param name_map: 代码 → 名称映射（键可带后缀，也可不带）
    :return: 名称；查不到时返回空串
    """
    if not code:
        return ""
    if code in name_map:
        return name_map[code]
    target: str = normalize_stock_code(code)
    for key, name in name_map.items():
        if normalize_stock_code(key) == target:
            return name
    return ""


# 股票名称是**静态的全局公共数据**（不含任何用户维度），因此用进程级缓存；
# 与「用户私有数据一律放 app.storage.user」不冲突。名称几乎不变，命中后不再请求。
# 键一律是归一化代码（见 normalize_stock_code）。
_NAME_CACHE: dict[str, str] = {}
# 后端没返回名称的代码（退市 / 代码写错）会被无限重查：监控页按 1 秒轮询，
# 不节流就是每秒打一次后端。记录失败时刻，TTL 内不再重试。
_NAME_MISS_AT: dict[str, float] = {}
_NAME_RETRY_SECONDS: float = 300.0


def _pending_name_codes(code_set: set[str], now: float) -> list[str]:
    """挑出本次真正要请求的代码（缓存未命中且不在失败 TTL 内）。

    :param code_set: 归一化后的代码集合
    :param now: 当前时间（``time.monotonic()``）
    :return: 需要请求后端的代码列表（已排序，便于断言与日志）
    """
    pending: list[str] = []
    for code in sorted(code_set):
        if code in _NAME_CACHE:
            continue
        miss_at: float | None = _NAME_MISS_AT.get(code)
        if miss_at is not None and (now - miss_at) < _NAME_RETRY_SECONDS:
            continue
        pending.append(code)
    return pending


def fetch_stock_name_map(codes: Iterable[str]) -> dict[str, str]:
    """批量查询股票名称（进程级缓存，只对没查过的代码发请求）。

    :param codes: 股票代码集合（自动去重、忽略空值，可带交易所后缀）
    :return: {传入的代码: 股票名称}；查不到的代码不在结果里（页面显示 ``--``）
    """
    code_set: set[str] = {str(c).strip() for c in codes if str(c).strip()}
    if not code_set:
        return {}

    now: float = time.monotonic()
    pending: list[str] = _pending_name_codes(
        {normalize_stock_code(c) for c in code_set}, now
    )
    if pending:
        # 延迟导入：app_context 顶层 import 了 api.*，避免模块级成环
        from app_context import AppContext

        try:
            infos: list[dict[str, Any]] = AppContext().stock_info_api.get_by_codes(
                ",".join(pending)
            )
        except Exception:
            logger.warning("查询股票名称失败", exc_info=True)
            infos = []
        got: dict[str, str] = build_name_map(infos)
        for code, name in got.items():
            _NAME_CACHE[normalize_stock_code(code)] = name
        # 没查到的要记下失败时刻：查不到的代码不能反复请求后端
        for code in pending:
            if code not in _NAME_CACHE:
                _NAME_MISS_AT[code] = now

    result: dict[str, str] = {}
    for code in code_set:
        name: str = _NAME_CACHE.get(normalize_stock_code(code), "")
        if name:
            result[code] = name
    return result


def collect_position_codes(
    groups: Iterable[Iterable[dict[str, Any]]],
) -> set[str]:
    """收集若干组持仓里的全部股票代码（去重，忽略空代码）。

    :param groups: 可迭代的持仓列表集合，例如 ``holdings.values()``
    :return: 股票代码集合
    """
    codes: set[str] = set()
    for positions in groups:
        for pos in positions:
            code: str = str(pos.get("code", "") or "")
            if code:
                codes.add(code)
    return codes


def unrealized_profit(
    positions: list[dict[str, Any]], price_map: dict[str, float],
) -> float | None:
    """按实时价与成本价现算当前持仓的**未实现**盈亏。

    只算「持仓还没卖出」这一段；已实现的那段由调用方从执行记录的
    ``current_profit`` 拿到后相加。

    任一持仓缺实时价就返回 None（调用方据此显示 "--"）：用剩余持仓算出的
    数字是「部分持仓的盈亏」，却会被读成全仓浮盈，比不显示更糟。

    :param positions: 持仓列表（含 code/quantity/cost_price）
    :param price_map: {股票代码: 实时价}
    :return: 未实现盈亏（元）；缺实时价时为 None。空持仓为 0.0（不是 None）
    """
    total: float = 0.0
    for pos in positions:
        code: str = str(pos.get("code", "") or "")
        if code not in price_map:
            return None
        quantity: float = to_float(pos.get("quantity"))
        cost_price: float = to_float(pos.get("cost_price"))
        total += (price_map[code] - cost_price) * quantity
    return total


def execution_day(record: dict[str, Any]) -> str:
    """执行记录所属日期（``YYYY-MM-DD``）。

    取 ``update_time``，缺失回退 ``create_time``；不足 10 位（无法截出日期）
    时返回空串，调用方据此跳过该记录。

    :param record: 单条执行记录
    :return: 日期字符串；无法解析时为空串
    """
    raw: str = str(record.get("update_time") or record.get("create_time") or "").strip()
    return raw[:10] if len(raw) >= 10 else ""


def last_execution_day(executions: Iterable[dict[str, Any]]) -> str:
    """全部执行记录里最晚的日期（``YYYY-MM-DD``）。

    曲线窗口的基准：数据不一定每天都有（策略没交易就没新记录），
    以「最后一个数据点」为基准能保证窗口里一定有数据，以今天为基准则可能画出空图。

    :param executions: 执行记录列表
    :return: 最晚日期；无有效记录时为空串
    """
    days: list[str] = [d for d in (execution_day(ex) for ex in executions) if d]
    return max(days) if days else ""


def strategy_day_rates(
    executions: Iterable[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """按日期 + 用户策略聚合出「该策略当天的收益率」（百分数）。

    当天该策略的取值 = 当天它**最后一条**执行记录的 ``current_return_rate``
    （按 ``update_time`` 取最大，输入顺序不影响结果）。

    ⚠ 不能取当天多条之和：``current_return_rate`` 是**累计**收益率，同一天 N 条记录
    相加等于把已实现收益累加 N 次（真机上一天 17 条记录把总收益从 -3.73 放大到
    -39.36）。历史上曾按「相加」实现，2026-09-30 改回取最后一条。

    缺 ``user_strategy_id`` 的记录无法归并到策略上，直接跳过。

    :param executions: 执行记录列表（可以是按时间范围查回来的子集）
    :return: {日期 YYYY-MM-DD: {user_strategy_id: 收益率百分数}}；无有效记录时为空字典
    """
    per_day: dict[str, dict[str, float]] = {}
    seen_time: dict[str, dict[str, str]] = {}
    for ex in executions:
        day: str = execution_day(ex)
        if not day:
            continue
        us_id: str = str(ex.get("user_strategy_id", "") or "")
        if not us_id:
            continue
        raw: Any = ex.get("current_return_rate")
        if raw is None:
            continue
        at: str = str(ex.get("update_time") or ex.get("create_time") or "")
        bucket: dict[str, float] = per_day.setdefault(day, {})
        times: dict[str, str] = seen_time.setdefault(day, {})
        # 同一天同一策略只留时间最大的那条（相等时留先出现的）
        if us_id in bucket and at < times[us_id]:
            continue
        bucket[us_id] = to_float(raw) * 100.0
        times[us_id] = at
    return per_day


# 「收益为 0」的判定阈值。current_return_rate 是浮点除法的结果，严格判等
# （``v != 0``）会把 -1e-16 这类残差当成「有收益」，所以用 epsilon。
_ZERO_EPS: float = 1e-9


def effective_rates(levels: Iterable[float]) -> list[float]:
    """从各策略的取值里剔除**收益为 0**的那些（需求方口径：只统计有收益的策略）。

    既不计入分子、也不占分母 —— 新建或从未交易过的策略收益率恒为 0，把它们留在分母里
    会把平均值往 0 拉。

    :param levels: 各策略的收益率（百分数）
    :return: 非零取值列表；全为 0 时为空列表（调用方按「该点为 0.0」处理）
    """
    return [v for v in levels if abs(v) > _ZERO_EPS]


def strategy_levels_before(
    executions: Iterable[dict[str, Any]], before_day: str,
) -> dict[str, float]:
    """各用户策略在 ``before_day`` **之前**（不含当天）的最后一次取值。

    曲线每个点要把「当天没交易的策略」按它最近一次的值计进去，而按时间范围
    查回来的窗口数据里没有这些历史记录 —— 只能用全量记录算出这个起始基准。
    不补这一份，窗口外的策略会在整个窗口里凭空消失（总和大一截）。

    :param executions: 全部执行记录（不是窗口子集）
    :param before_day: 窗口第一天的日期 ``YYYY-MM-DD``
    :return: {user_strategy_id: 收益率百分数}；之前从未有记录的策略不在结果里
    """
    levels: dict[str, float] = {}
    if not before_day:
        return levels
    per_day: dict[str, dict[str, float]] = strategy_day_rates(executions)
    for day in sorted(d for d in per_day if d < before_day):
        levels.update(per_day[day])
    return levels


def daily_average_rates(
    executions: Iterable[dict[str, Any]],
    carry_in: dict[str, float] | None = None,
) -> dict[str, float]:
    """按日期聚合出「各用户策略收益率的平均值」（百分数），用于总收益曲线的数据点。

    逐个日期累积：先记下每个策略当天的取值（当天取最后一条记录，见
    :func:`strategy_day_rates`），当天没有记录的策略**沿用其最近一次的取值**，
    再对**非零**取值取算术平均（见 :func:`effective_rates`）。沿用最近一次取值保证
    平均值不会因为某个策略当天没交易而少一块；排除 0 保证「还没交易过的策略」不占分母。

    ``carry_in`` 是窗口开始之前的各策略取值（见 :func:`strategy_levels_before`）：
    按时间范围查回来的窗口数据里没有窗口外记录的策略，不给它起始基准就会漏算。

    :param executions: 要出点的执行记录（通常是窗口内的记录）
    :param carry_in: {user_strategy_id: 窗口开始前的取值}；可为 None
    :return: {日期 YYYY-MM-DD: 各策略收益率平均值}；无有效记录时为空字典
    """
    per_day: dict[str, dict[str, float]] = strategy_day_rates(executions)
    levels: dict[str, float] = dict(carry_in or {})
    result: dict[str, float] = {}
    for day in sorted(per_day):
        levels.update(per_day[day])
        rates: list[float] = effective_rates(levels.values())
        result[day] = round(sum(rates) / len(rates), 2) if rates else 0.0
    return result


def strategy_levels_on_days(
    executions: Iterable[dict[str, Any]],
    days: Iterable[str],
    carry_in: float | None = None,
) -> list[float | None]:
    """单个用户策略在给定日期上的取值序列（用于对比曲线）。

    取值规则与 :func:`daily_average_rates` 里的单策略口径一致：当天有记录 ⇒ 当天最后一条
    记录的值；当天没记录 ⇒ 沿用最近一次的取值；在它第一条记录之前 ⇒ ``None``
    （「尚未有数据」与「值就是 0」要区分开，画出来是断线而不是贴着 0 的假线）。

    ⚠ 传入的 ``executions`` 必须**只含该策略**的记录（由调用方按 user_strategy_id
    过滤）：本函数不做归属判断，混进别的策略的记录就会算成它们的合计。

    :param executions: 该策略窗口内的执行记录
    :param days: 要出的日期序列（升序）
    :param carry_in: 该策略在窗口开始前的取值；None 表示窗口前没有它的记录
    :return: 与 days 等长的取值列表，未知处为 None
    """
    per_day: dict[str, dict[str, float]] = strategy_day_rates(executions)
    level: float | None = carry_in
    out: list[float | None] = []
    for day in days:
        bucket: dict[str, float] | None = per_day.get(day)
        if bucket:
            level = sum(bucket.values())
        out.append(round(level, 2) if level is not None else None)
    return out


def latest_execution_map(
    executions: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """按 ``user_strategy_id`` 归并出每个用户策略**更新时间最新**的那条执行记录。

    ⚠ 归并键必须是 ``user_strategy_id``（用户策略 ID），不能用 ``strategy_id``
    （策略模板 ID）：同一模板的多个用户策略共用后者，归并后会串数据。

    :param executions: list_executions() 返回的全部执行记录
    :return: {user_strategy_id: 最新一条执行记录}；缺 user_strategy_id 的记录被跳过
    """
    result: dict[str, dict[str, Any]] = {}
    times: dict[str, str] = {}
    for ex in executions:
        us_id: str = str(ex.get("user_strategy_id", "") or "")
        if not us_id:
            continue
        ex_time: str = str(ex.get("update_time") or ex.get("create_time") or "")
        if us_id in times and ex_time < times[us_id]:
            continue
        times[us_id] = ex_time
        result[us_id] = ex
    return result


def latest_execution(
    executions: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    """取全部执行记录里 ``update_time`` 最新的一条（跨策略，全局唯一）。

    :param executions: list_executions() 返回的全部执行记录
    :return: 最新一条执行记录；无有效记录时返回 None
    """
    latest: dict[str, Any] | None = None
    latest_time: str = ""
    for ex in executions:
        ex_time: str = str(ex.get("update_time") or ex.get("create_time") or "")
        if latest is None or ex_time >= latest_time:
            latest = ex
            latest_time = ex_time
    return latest


def base_amount(record: dict[str, Any]) -> float:
    """执行记录的收益率分母，与后端 ``manager.py`` 的口径保持一致。

    优先取 ``initial_amount``；为 0 时退化为持仓成本合计（Σ 成本价 × 数量）。

    :param record: 单条执行记录
    :return: 分母金额；也算不出来时为 0.0
    """
    base: float = to_float(record.get("initial_amount"))
    if base > 0:
        return base
    positions: list[dict[str, Any]] = list(record.get("positions", []) or [])
    return sum(
        to_float(pos.get("cost_price")) * to_float(pos.get("quantity"))
        for pos in positions
    )


def current_return_rate_pct(
    record: dict[str, Any], unrealized: float | None,
) -> float | None:
    """当前收益率（百分数）= (已实现 + 未实现) / base_amount × 100。

    用它替代执行记录里的 ``current_return_rate``：后者只含已实现，
    直接展示会漏掉持仓浮盈（后端注释也把它称作「总收益率」，口径对不上）。

    :param record: 该策略最新一条执行记录
    :param unrealized: 该记录持仓的未实现盈亏；传 None 表示算不出（缺实时价）
    :return: 收益率百分数；已实现字段缺失或分母为 0 时返回 None（调用方显示 "-"）
    """
    realized_raw: Any = record.get("current_profit")
    if realized_raw is None or unrealized is None:
        return None
    base: float = base_amount(record)
    if base <= 0:
        return None
    return (to_float(realized_raw) + unrealized) / base * 100.0


def current_rate_pct_with_fallback(
    record: dict[str, Any], price_map: dict[str, float],
) -> float | None:
    """单条执行记录的「含浮动盈亏」当前收益率（百分数），缺实时价时退化。

    能取到该记录全部持仓的实时价 ⇒ 与 :func:`current_return_rate_pct` 同口径
    （``(current_profit + 未实现) / base_amount × 100``）；
    缺实时价 ⇒ **退回记录自带的 current_return_rate**（只含已实现）。

    退化而不是丢弃，是为了曲线末点的聚合口径稳定：缺价只是这一条记录算不准，
    若整个策略从平均值里消失，均值就少了一整块（2026-09-30 需求方选定的处理方式）。

    :param record: 该策略最新一条执行记录
    :param price_map: {股票代码: 实时价}
    :return: 收益率百分数；连 current_return_rate 都缺时返回 None
    """
    positions: list[dict[str, Any]] = list(record.get("positions", []) or [])
    unrealized: float | None = unrealized_profit(positions, price_map)
    if unrealized is not None:
        rate: float | None = current_return_rate_pct(record, unrealized)
        if rate is not None:
            return round(rate, 2)
    raw: Any = record.get("current_return_rate")
    if raw is None:
        return None
    return round(to_float(raw) * 100.0, 2)


def average_current_rate_pct(
    executions: Iterable[dict[str, Any]], price_map: dict[str, float],
) -> float | None:
    """所有用户策略「当前」收益率的**平均值**（百分数），用于曲线末点。

    每个策略取最新一条执行记录（按 ``user_strategy_id`` 归并），各自算出含
    浮动盈亏的当前收益率后取**算术平均**，并排除收益为 0 的策略（与
    :func:`daily_average_rates` 同一口径）。缺实时价的策略按
    :func:`current_rate_pct_with_fallback` 退化到已实现口径，不剔除。

    :param executions: 全部执行记录（窗口外也要传，否则漏掉久未更新的策略）
    :param price_map: {股票代码: 实时价}，需覆盖各策略最新记录的持仓代码
    :return: 收益率平均值（百分数）；无有效记录时返回 None
    """
    rates: list[float] = effective_rates(
        rate for rate in (
            current_rate_pct_with_fallback(record, price_map)
            for record in latest_execution_map(executions).values()
        ) if rate is not None
    )
    if not rates:
        return None
    return round(sum(rates) / len(rates), 2)
