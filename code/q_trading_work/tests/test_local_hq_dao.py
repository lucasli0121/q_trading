"""LocalHqDao 的契约与边界。

重点守三件事：

1. **分区路径**：分钟是 `minute/YYYY/MM/`，日K 是 `day/YYYY/`（少一层月份）。
   写反了不会报错，只会永远读不到数据，然后被当成「本地没有」。
2. **coverage 的端点语义**：请求区间超出本地覆盖范围必须判为「不完整」。
   漏判的后果是拿半个区间当成全部，与服务端那个「回源判据只看区间内有任意
   记录」的缺陷同型。
3. **get_kline 的 None 与 [] 语义不同**：None = 本地没这只股票（调用方按模式
   决定回落还是报错）；[] = 本地覆盖完整，但这只股票在这段时间确实没记录。
"""

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from dao.local_hq_dao import KIND_DAY, KIND_MINUTE, LocalHqDao, LocalHqError


def write_partition(root: Path, kind: str, day: str, rows: list[dict]) -> None:
    """按归档进程的布局写一个分区文件。"""
    year, month, _ = day.split("-")
    if kind == KIND_MINUTE:
        path = root / KIND_MINUTE / year / month / f"{day}.parquet"
    else:
        path = root / KIND_DAY / year / f"{day}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, engine="pyarrow", index=False)


def minute_row(code: str, stamp: str) -> dict:
    return {
        "code": code,
        "minute_time": stamp,
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.2,
        "price": 10.2,
        "preclose": 10.0,
        "volume": 1000,
        "amount": 10200.0,
        "change_percent": 0.02,
        "turnover": 0.001,
        "amp": 0.07,
    }


def day_row(code: str, stamp: str) -> dict:
    return {
        "code": code,
        "create_time": stamp,
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.2,
        "volume": 100000,
        "amount": 1020000.0,
    }


class PathLayoutTest(unittest.TestCase):
    """两类分区的层级不同，这是最容易写错又最难发现的地方。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dao = LocalHqDao(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_minute_path_has_month_level(self) -> None:
        path = self.dao._path(KIND_MINUTE, "2026-10-14")
        self.assertEqual(
            path, self.root / "minute" / "2026" / "10" / "2026-10-14.parquet"
        )

    def test_day_path_has_no_month_level(self) -> None:
        path = self.dao._path(KIND_DAY, "2026-10-14")
        self.assertEqual(path, self.root / "day" / "2026" / "2026-10-14.parquet")

    def test_available_days_ignores_non_date_files(self) -> None:
        write_partition(self.root, KIND_DAY, "2026-10-13", [day_row("A.SZ", "2026-10-13 00:00:00")])
        write_partition(self.root, KIND_DAY, "2026-10-14", [day_row("A.SZ", "2026-10-14 00:00:00")])
        # 原子写留下的临时文件不该被当成一天
        junk = self.root / "day" / "2026" / "2026-10-14.parquet.tmp"
        junk.write_bytes(b"")
        self.assertEqual(self.dao.available_days(KIND_DAY), ["2026-10-13", "2026-10-14"])


class CoverageTest(unittest.TestCase):
    """coverage 决定「本地能不能当成权威来源」，端点必须严格。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for day in ("2026-10-12", "2026-10-13", "2026-10-14"):
            write_partition(self.root, KIND_DAY, day, [day_row("A.SZ", f"{day} 00:00:00")])
        self.dao = LocalHqDao(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_full_range_is_covered(self) -> None:
        ok, reason = self.dao.coverage(KIND_DAY, "2026-10-12", "2026-10-14")
        self.assertTrue(ok, reason)

    def test_start_before_local_range_is_not_covered(self) -> None:
        ok, reason = self.dao.coverage(KIND_DAY, "2026-10-01", "2026-10-14")
        self.assertFalse(ok)
        self.assertIn("更早", reason)

    def test_end_after_local_range_is_not_covered(self) -> None:
        ok, reason = self.dao.coverage(KIND_DAY, "2026-10-12", "2026-10-31")
        self.assertFalse(ok)
        self.assertIn("更晚", reason)

    def test_range_without_any_file_is_not_covered(self) -> None:
        ok, _ = self.dao.coverage(KIND_DAY, "2026-01-01", "2026-01-31")
        self.assertFalse(ok)

    def test_missing_kind_is_not_covered(self) -> None:
        ok, reason = self.dao.coverage(KIND_MINUTE, "2026-10-12", "2026-10-14")
        self.assertFalse(ok)
        self.assertIn("没有任何", reason)


class GetKlineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write_partition(
            self.root,
            KIND_DAY,
            "2026-10-13",
            [day_row("A.SZ", "2026-10-13 00:00:00"), day_row("B.SZ", "2026-10-13 00:00:00")],
        )
        write_partition(
            self.root,
            KIND_DAY,
            "2026-10-14",
            [day_row("A.SZ", "2026-10-14 00:00:00")],
        )
        self.dao = LocalHqDao(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_records_sorted_by_time(self) -> None:
        records = self.dao.get_kline(KIND_DAY, "A.SZ", "2026-10-13", "2026-10-14")
        self.assertIsNotNone(records)
        assert records is not None
        self.assertEqual(
            [r["create_time"] for r in records],
            ["2026-10-13 00:00:00", "2026-10-14 00:00:00"],
        )

    def test_unknown_code_returns_none(self) -> None:
        """None 表示「本地没这只股票」，调用方据此决定回落还是报错。"""
        self.assertIsNone(self.dao.get_kline(KIND_DAY, "Z.SZ", "2026-10-13", "2026-10-14"))

    def test_code_outside_range_returns_none(self) -> None:
        """B.SZ 在 10-13 有记录，但请求区间是 10-14，桶里没有它。"""
        self.assertIsNone(self.dao.get_kline(KIND_DAY, "B.SZ", "2026-10-14", "2026-10-14"))

    def test_load_all_groups_by_code(self) -> None:
        buckets = self.dao.load_all(KIND_DAY, "2026-10-13", "2026-10-14")
        self.assertEqual(sorted(buckets), ["A.SZ", "B.SZ"])
        self.assertEqual(len(buckets["A.SZ"]), 2)
        self.assertEqual(len(buckets["B.SZ"]), 1)


class MinuteKindTest(unittest.TestCase):
    """分钟路径多一层月目录，走通一次避免只在日K 上测试通过。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write_partition(
            self.root,
            KIND_MINUTE,
            "2026-10-14",
            [
                minute_row("A.SZ", "2026-10-14 09:32:00"),
                minute_row("A.SZ", "2026-10-14 09:30:00"),
            ],
        )
        self.dao = LocalHqDao(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_reads_and_sorts_by_minute_time(self) -> None:
        records = self.dao.get_kline(KIND_MINUTE, "A.SZ", "2026-10-14", "2026-10-14")
        self.assertIsNotNone(records)
        assert records is not None
        self.assertEqual(
            [r["minute_time"] for r in records],
            ["2026-10-14 09:30:00", "2026-10-14 09:32:00"],
        )

    def test_corrupt_file_raises_instead_of_returning_empty(self) -> None:
        """读不出来必须抛错。静默返回空会被上层当成「这只股票没有数据」，
        从而跳过它 —— 那是把一个故障伪装成一个正常结论。"""
        broken = self.root / "minute" / "2026" / "10" / "2026-10-13.parquet"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_bytes(b"not a parquet file")
        with self.assertRaises(LocalHqError):
            self.dao.get_kline(KIND_MINUTE, "A.SZ", "2026-10-13", "2026-10-14")


if __name__ == "__main__":
    unittest.main()
