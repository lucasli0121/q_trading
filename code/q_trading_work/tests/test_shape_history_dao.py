"""跨日形态历史的读取层（`dao/shape_history_dao.py`）。

守四件事：

1. **只读已收盘的历史日**。`labels(code, today)` 不能读到 today 及之后的 parquet，
   否则回测里归档已包含未来日期时就是未来函数。
2. **昨日在前**。策略按「第一个元素 = 昨天」喂给 `sequence_features`，顺序反了
   不会报错，但 `weak_streak` 会变成「从最早那天往后数」，含义完全不同。
3. **算不出来的那天留空串**，不能被跳过或被读成「震荡」。缺数据不等于走势中性。
4. **分钟与日K 两个分区相互独立**。标签读分钟、趋势序列读日K，任一方缺数据
   不影响另一方；日线序列多取 `ma_period - 1` 天给均线做种子。

用临时目录写真 parquet（与归档布局一致），不 mock DAO：路径层级写错这类缺陷
只有真读文件才能发现。
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path

import pandas as pd

if "talib" not in sys.modules:
    try:
        import talib  # noqa: F401
    except ImportError:
        sys.modules["talib"] = types.ModuleType("talib")

from dao.local_hq_dao import KIND_DAY, KIND_MINUTE  # noqa: E402
from dao.shape_history_dao import ShapeHistory  # noqa: E402
from factor.intraday_shape_factor import (  # noqa: E402
    DAY_TYPE_FLAT,
    DAY_TYPE_LOW_HIGH,
    IntradayShapeFactor,
)


def write_minute_partition(root: Path, day: str, rows: list[dict]) -> None:
    """按归档布局写分钟分区：minute/YYYY/MM/YYYY-MM-DD.parquet。"""
    year, month, _ = day.split("-")
    path = root / KIND_MINUTE / year / month / f"{day}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, engine="pyarrow", index=False)


def write_day_partition(root: Path, day: str, rows: list[dict]) -> None:
    """按归档布局写日K分区：day/YYYY/YYYY-MM-DD.parquet（比分钟少一层月份）。"""
    year, _month, _ = day.split("-")
    path = root / KIND_DAY / year / f"{day}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, engine="pyarrow", index=False)


def day_rows(code: str, day: str, close: float, high: float) -> list[dict]:
    """构造一只股票一天的日K记录。"""
    return [
        {
            "code": code,
            "create_time": f"{day} 00:00:00",
            "open": close,
            "high": high,
            "low": close,
            "close": close,
            "volume": 1000,
            "amount": 10000.0,
        }
    ]


def day_bars(
    code: str,
    day: str,
    open_price: float,
    close_price: float,
    preclose: float,
) -> list[dict]:
    """构造一天 26 根普通 bar（每 10 分钟一根），价格从开盘线性走到收盘。"""
    stamps: list[str] = []
    for minute in range(9 * 60 + 30, 11 * 60 + 31, 10):
        stamps.append(f"{minute // 60:02d}:{minute % 60:02d}")
    for minute in range(13 * 60, 15 * 60 + 1, 10):
        stamps.append(f"{minute // 60:02d}:{minute % 60:02d}")

    rows: list[dict] = []
    total = len(stamps) - 1
    running_high = open_price
    running_low = open_price
    for index, stamp in enumerate(stamps):
        close = open_price + (close_price - open_price) * index / total
        running_high = max(running_high, close)
        running_low = min(running_low, close)
        rows.append(
            {
                "code": code,
                "minute_time": f"{day} {stamp}:00",
                "open": open_price,
                "high": running_high,
                "low": running_low,
                "close": close,
                "preclose": preclose,
                "volume": 1000,
                "amount": 10000.0,
            }
        )
    return rows


class ShapeHistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.factor = IntradayShapeFactor()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_disabled_when_root_empty(self) -> None:
        store = ShapeHistory("", self.factor)
        self.assertFalse(store.enabled)
        self.assertEqual(store.labels("000001.SZ", "2026-10-14"), [])

    def test_labels_are_newest_first_and_exclude_today(self) -> None:
        # 09-29 低开高走（低开 2%，收涨 4%）
        write_minute_partition(
            self.root,
            "2026-09-29",
            day_bars("000001.SZ", "2026-09-29", 9.8, 10.2, 10.0),
        )
        # 10-09 平开平收
        write_minute_partition(
            self.root,
            "2026-10-09",
            day_bars("000001.SZ", "2026-10-09", 10.0, 10.0, 10.0),
        )
        # 10-14 也写一份，验证它不会被 todoy=10-14 读到
        write_minute_partition(
            self.root,
            "2026-10-14",
            day_bars("000001.SZ", "2026-10-14", 9.8, 10.2, 10.0),
        )

        store = ShapeHistory(str(self.root), self.factor, lookback=5)
        labels = store.labels("000001.SZ", "2026-10-14")
        self.assertEqual(len(labels), 2)
        self.assertEqual(labels[0], DAY_TYPE_FLAT)  # 10-09 最近
        self.assertEqual(labels[1], DAY_TYPE_LOW_HIGH)  # 09-29

    def test_unknown_code_returns_empty_labels(self) -> None:
        """该股在历史日里没有记录时，标签是空串而不是被丢掉。

        保留位置的理由：空串表示「那天算不出来」，与「没有历史日期」是两件事。
        对序列特征来说两者都会让 `sequence_ok=False`（fail-open），但保留位置
        才能让日志里看出「查了 5 天、5 天都没数据」。
        """
        write_minute_partition(
            self.root,
            "2026-10-09",
            day_bars("000001.SZ", "2026-10-09", 10.0, 10.0, 10.0),
        )
        store = ShapeHistory(str(self.root), self.factor, lookback=5)
        labels = store.labels("600000.SH", "2026-10-14")
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels, [""])

    def test_lookback_limits_days(self) -> None:
        for day in ("2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29"):
            write_minute_partition(
                self.root,
                day,
                day_bars("000001.SZ", day, 10.0, 10.0, 10.0),
            )
        store = ShapeHistory(str(self.root), self.factor, lookback=2)
        labels = store.labels("000001.SZ", "2026-10-14")
        self.assertEqual(len(labels), 2)

    def test_labels_are_cached(self) -> None:
        write_minute_partition(
            self.root,
            "2026-10-09",
            day_bars("000001.SZ", "2026-10-09", 10.0, 10.0, 10.0),
        )
        store = ShapeHistory(str(self.root), self.factor, lookback=5)
        first = store.labels("000001.SZ", "2026-10-14")
        # 第二次必须命中缓存：清掉 DAO 的区间缓存后仍返回同样结果
        store._dao.invalidate()  # type: ignore[union-attr]
        second = store.labels("000001.SZ", "2026-10-14")
        self.assertEqual(first, second)
        self.assertEqual(len(store._label_cache), 1)


DAYS: tuple[str, ...] = tuple(f"2026-10-{day:02d}" for day in range(1, 10))


class DailySeriesTest(unittest.TestCase):
    """日线序列读取（跨日趋势判据的数据源）。

    与 `labels` 的分工在这里被固化为断言：两者读不同分区，任一方缺数据
    不影响另一方。趋势判据（收盘 vs 均线、最高价 vs 前日）全部由日线驱动。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.factor = IntradayShapeFactor()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_days(self, code: str = "000001.SZ") -> None:
        for index, day in enumerate(DAYS):
            write_day_partition(
                self.root, day, day_rows(code, day, 10.0 + index, 10.5 + index)
            )

    def test_disabled_when_root_empty(self) -> None:
        store = ShapeHistory("", self.factor)
        self.assertEqual(store.daily_series("000001.SZ", "2026-10-14"), [])

    def test_series_is_ascending_and_excludes_today(self) -> None:
        self._write_days()
        # 10-14 也写一份：today 及之后的记录不能被读到（防未来函数）
        write_day_partition(
            self.root, "2026-10-14", day_rows("000001.SZ", "2026-10-14", 99.0, 99.0)
        )
        store = ShapeHistory(str(self.root), self.factor, lookback=5)
        series = store.daily_series("000001.SZ", "2026-10-14", ma_period=5)
        # span = lookback(5) + ma_period(5) - 1 = 9，正好覆盖已写的 9 天
        self.assertEqual(len(series), 9)
        self.assertEqual(series[0]["date"], "2026-10-01")
        self.assertEqual(series[-1]["date"], "2026-10-09")
        self.assertAlmostEqual(series[-1]["close"], 18.0)
        # 升序：趋势计算（均线、前日高点）依赖时间方向
        dates = [row["date"] for row in series]
        self.assertEqual(dates, sorted(dates))

    def test_span_covers_ma_seeding(self) -> None:
        """回看窗口 + ma_period - 1：窗口内最早那天才算得出均线。"""
        self._write_days()
        store = ShapeHistory(str(self.root), self.factor, lookback=2)
        series = store.daily_series("000001.SZ", "2026-10-14", ma_period=5)
        # span = 2 + 5 - 1 = 6
        self.assertEqual(len(series), 6)
        self.assertEqual(series[-1]["date"], "2026-10-09")

    def test_unknown_code_returns_empty(self) -> None:
        self._write_days()
        store = ShapeHistory(str(self.root), self.factor, lookback=5)
        self.assertEqual(store.daily_series("600000.SH", "2026-10-14"), [])

    def test_minute_and_day_are_independent(self) -> None:
        """只有日K 分区时：趋势序列可用，标签为空（反之亦然）。"""
        self._write_days()
        store = ShapeHistory(str(self.root), self.factor, lookback=5)
        self.assertTrue(store.daily_series("000001.SZ", "2026-10-14"))
        self.assertEqual(store.labels("000001.SZ", "2026-10-14"), [])

    def test_series_is_cached(self) -> None:
        self._write_days()
        store = ShapeHistory(str(self.root), self.factor, lookback=5)
        first = store.daily_series("000001.SZ", "2026-10-14")
        store._dao.invalidate()  # type: ignore[union-attr]
        second = store.daily_series("000001.SZ", "2026-10-14")
        self.assertEqual(first, second)
        self.assertEqual(len(store._daily_cache), 1)

    def test_ma_period_changes_span(self) -> None:
        self._write_days()
        store = ShapeHistory(str(self.root), self.factor, lookback=5)
        short = store.daily_series("000001.SZ", "2026-10-14", ma_period=2)
        # span = 5 + 2 - 1 = 6
        self.assertEqual(len(short), 6)


if __name__ == "__main__":
    unittest.main()
