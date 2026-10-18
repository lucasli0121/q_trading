"""跨日形态历史读取：把归档分钟数据编码成「每日一个形态标签」。

用途单一：给策略提供「昨日、前日……分别是什么形态」。这类信息与当日前缀形态
有本质区别——它是**隔夜已定型**的，盘中任何时刻都一样，不存在「上午还没走完」
的问题，因此可以直接作为买入门槛的依据。

## 为什么必须先落成标签再缓存

编码一只股票一天的形态要遍历当日全部分钟 bar。若每次买入检查都重算，
几百只股票 × 每分钟一次，CPU 全部消耗在重复计算上。标签一旦算出就不再变化
（历史日不会再有新 bar），所以按 `(code, date)` 缓存即可，进程生命周期内有效。

## 数据来源

只读 `q_trading_archive` 产出的 parquet，不连服务端 HTTP。理由：
① 服务端分钟只保留 90 天，且没有回源路径；
② 归档目录与 work 同机共享（部署前提），读取是本地 IO；
③ 逐只股票打 HTTP 会让每分钟的买入检查变成网络阻塞。

两个读取入口，数据源不同、各自独立可用：

- `labels()`：日形态标签，来自**分钟**分区（`minute/YYYY/MM/`），要遍历当日
  全部分钟 bar 编码
- `daily_series()`：日线序列（close / high），来自**日K**分区（`day/YYYY/`），
  供跨日趋势判据（收盘 vs 均线、最高价 vs 前日）使用。多取 `ma_period - 1` 天，
  让回看窗口内最早那天也算得出均线

**配置缺失即关闭**（`root` 为空）：跨日层不影响任何原有判定，属 fail-open。
这与 `[local_hq] mode=off` 的默认值一致——不配就什么都不改。
"""

from __future__ import annotations

import logging
from typing import Any

from dao.local_hq_dao import KIND_DAY, KIND_MINUTE, LocalHqDao, LocalHqError
from factor.intraday_shape_factor import IntradayShapeFactor

# 默认回看天数。3 天足够表达「低开高走 → 高开低走 → 低开低走」这类序列，
# 再长就会被衰减权重压到几乎不起作用，只增加读盘量。
DEFAULT_LOOKBACK: int = 5


class ShapeHistory:
    """按日形态标签的本地缓存。

    非线程安全，按策略实例持有一个即可（策略本身由 workflow 单线程驱动每分钟
    检查；若将来多线程化，需在这里加锁）。
    """

    def __init__(
        self,
        root: str,
        factor: IntradayShapeFactor,
        lookback: int = DEFAULT_LOOKBACK,
    ) -> None:
        """初始化。

        :param root: 归档 parquet 根目录；空字符串表示关闭（`enabled=False`）
        :param factor: 形态编码器，与当日形态共用同一实例，保证口径一致
        :param lookback: 回看多少个交易日
        """
        self._root: str = str(root or "").strip()
        self._factor: IntradayShapeFactor = factor
        self._lookback: int = max(int(lookback), 1)
        self._logger: logging.Logger = logging.getLogger(__name__)
        self._dao: LocalHqDao | None = LocalHqDao(self._root) if self._root else None
        # (code, date) -> day_type。历史日的标签不会变，进程内永久有效。
        self._label_cache: dict[tuple[str, str], str] = {}
        # (code, today) -> 标签列表（昨日在前）。换日才失效。
        self._seq_cache: dict[tuple[str, str], list[str]] = {}
        # (code, today, ma_period) -> 日线序列（升序，末尾是昨日）。
        self._daily_cache: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
        # (kind, start, end) -> 是否已尝试过加载，避免「区间无数据」时每分钟重扫目录
        self._loaded_ranges: set[tuple[str, str, str]] = set()

    @property
    def enabled(self) -> bool:
        """是否启用。`root` 为空即关闭。"""
        return self._dao is not None

    @property
    def lookback(self) -> int:
        """回看天数。"""
        return self._lookback

    def _previous_days(self, today: str) -> list[str]:
        """取 today 之前的最近 `lookback` 个已归档交易日（升序）。"""
        if self._dao is None:
            return []
        days = [day for day in self._dao.available_days(KIND_MINUTE) if day < today]
        return days[-self._lookback :]

    def _ensure_loaded(self, kind: str, days: list[str]) -> None:
        """触发一次区间加载（DAO 内部按区间缓存全部股票）。"""
        if self._dao is None or not days:
            return
        key = (kind, days[0], days[-1])
        if key in self._loaded_ranges:
            return
        try:
            self._dao.load_all(kind, days[0], days[-1])
            self._loaded_ranges.add(key)
        except LocalHqError as exc:
            # 归档目录不可读不该让买入检查崩掉：跨日层降级为「无数据」，
            # 当日层与其它判定照常。日志留痕，便于发现配置写错。
            self._logger.warning(
                "跨日形态读取失败（%s 区间 %s~%s，跨日层本次不参与）: %s",
                kind,
                days[0],
                days[-1],
                exc,
            )
            self._loaded_ranges.add(key)

    def _day_label(self, code: str, day: str) -> str:
        """取某只股票某天的形态标签；算不出来返回空串（不是「震荡」）。"""
        key = (code, day)
        cached = self._label_cache.get(key)
        if cached is not None:
            return cached
        # 默认值先落空串：算不出来与「震荡」必须区分，前者不参与序列计数。
        self._label_cache[key] = ""
        if self._dao is None:
            return ""
        try:
            records = self._dao.get_kline(KIND_MINUTE, code, day, day)
        except LocalHqError as exc:
            self._logger.warning("跨日形态读取失败 %s %s: %s", code, day, exc)
            return ""
        if not records:
            return ""
        try:
            features: Any = self._factor.encode(records)
        except Exception as exc:  # pragma: no cover - 编码器内部已容错
            self._logger.warning("跨日形态编码失败 %s %s: %s", code, day, exc)
            return ""
        if not isinstance(features, dict):
            return ""
        label = str(features.get("day_type", "") or "")
        self._label_cache[key] = label
        return label

    def labels(self, code: str, today: str) -> list[str]:
        """取 `today` 之前最近若干交易日的形态标签，**昨日在前**。

        :param code: 股票代码
        :param today: 当日日期 YYYY-MM-DD。该日及之后的记录不会被读到
            （防未来函数：回测里归档可能已经包含当日之后的日期）
        :return: 标签列表，长度 <= `lookback`；无数据返回空列表
        """
        if self._dao is None or not code or not today:
            return []

        cache_key = (code, today)
        cached = self._seq_cache.get(cache_key)
        if cached is not None:
            return cached

        days = self._previous_days(today)
        if not days:
            self._seq_cache[cache_key] = []
            return []
        self._ensure_loaded(KIND_MINUTE, days)

        # 昨日在前：days 是升序，取标签后反转
        labels = [self._day_label(code, day) for day in reversed(days)]
        self._seq_cache[cache_key] = labels
        return labels

    def daily_series(
        self, code: str, today: str, ma_period: int = 5
    ) -> list[dict[str, Any]]:
        """取 `today` 之前最近若干交易日的日线，**升序**（末尾是昨日）。

        给跨日趋势判据用（收盘 vs 均线、最高价 vs 前日，见
        `IntradayShapeFactor.trend_features`）。与 `labels` 的分工：标签来自
        分钟分区、回答「昨天是什么形状」；本方法来自日K 分区、回答
        「最近几天整体在走弱还是走强」。两者数据源不同，各自独立可用。

        多取 `ma_period - 1` 天：回看窗口内最早那天也要能算出均线，
        否则它的「低于均线」计数只能跳过，窗口的有效天数凭空少一天。

        只返回该股**有记录**的日期。停牌日不在序列里，均线按可用K线的
        位置计算（不按自然日对齐），这与常规行情软件的口径一致。

        :param code: 股票代码
        :param today: 当日日期 YYYY-MM-DD。该日及之后的记录不会被读到
        :param ma_period: 均线周期，决定多取多少天
        :return: `[{"date", "close", "high"}]` 升序列表；无数据返回空列表
        """
        if self._dao is None or not code or not today:
            return []

        period: int = max(int(ma_period), 1)
        span: int = self._lookback + period - 1
        cache_key = (code, today, period)
        cached = self._daily_cache.get(cache_key)
        if cached is not None:
            return cached

        days = [
            day for day in self._dao.available_days(KIND_DAY) if day < today
        ][-span:]
        if not days:
            self._daily_cache[cache_key] = []
            return []
        self._ensure_loaded(KIND_DAY, days)

        try:
            records = self._dao.get_kline(KIND_DAY, code, days[0], days[-1])
        except LocalHqError as exc:
            self._logger.warning(
                "跨日趋势读取失败 %s %s~%s: %s", code, days[0], days[-1], exc
            )
            self._daily_cache[cache_key] = []
            return []

        # 同一天多条记录时保留最后一条（按 create_time 升序，后者覆盖前者）
        by_date: dict[str, dict[str, Any]] = {}
        for record in records or []:
            day = str(record.get("create_time", ""))[:10]
            try:
                close = float(record.get("close") or 0.0)
                high = float(record.get("high") or 0.0)
            except (TypeError, ValueError):
                continue
            if not day or close <= 0 or high <= 0:
                continue
            by_date[day] = {"date": day, "close": close, "high": high}

        series = [by_date[day] for day in sorted(by_date)]
        self._daily_cache[cache_key] = series
        return series

    def invalidate(self) -> None:
        """清空缓存。归档补齐了历史数据、或需要释放内存时用。"""
        self._label_cache.clear()
        self._seq_cache.clear()
        self._daily_cache.clear()
        self._loaded_ranges.clear()
        if self._dao is not None:
            self._dao.invalidate()
