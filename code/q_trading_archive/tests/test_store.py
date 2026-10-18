"""parquet 落盘：分区路径、幂等覆盖、原子写。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archive.store import ParquetStore

MINUTE_COLS = ["code", "minute_time", "open"]
DAY_COLS = ["code", "create_time", "close"]


class TestParquetStore(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = ParquetStore(Path(self._tmp.name))

    def test_minute_path_partitioned_by_year_month(self) -> None:
        path = self.store.minute_path("2026-10-14")
        self.assertEqual(path.name, "2026-10-14.parquet")
        self.assertEqual(path.parent.name, "10")
        self.assertEqual(path.parent.parent.name, "2026")
        self.assertEqual(path.parent.parent.parent.name, "minute")

    def test_day_path_partitioned_by_year(self) -> None:
        path = self.store.day_path("2026-10-14")
        self.assertEqual(path.name, "2026-10-14.parquet")
        self.assertEqual(path.parent.name, "2026")
        self.assertEqual(path.parent.parent.name, "day")

    def test_roundtrip_and_sort_order(self) -> None:
        rows = [
            {"code": "000001.SZ", "minute_time": "2026-10-14 09:31:00", "open": 1.0},
            {"code": "600000.SH", "minute_time": "2026-10-14 09:30:00", "open": 2.0},
            {"code": "000001.SZ", "minute_time": "2026-10-14 09:30:00", "open": 3.0},
        ]
        written = self.store.write(self.store.minute_path("2026-10-14"), rows, MINUTE_COLS)
        self.assertEqual(written, 3)

        back = self.store.read(self.store.minute_path("2026-10-14"))
        self.assertEqual(len(back), 3)
        self.assertEqual(
            list(back["minute_time"]),
            ["2026-10-14 09:30:00", "2026-10-14 09:30:00", "2026-10-14 09:31:00"],
        )
        # 同一分钟内再按 code 升序
        self.assertEqual(list(back["code"])[:2], ["000001.SZ", "600000.SH"])

    def test_overwrite_is_idempotent(self) -> None:
        path = self.store.day_path("2026-10-14")
        self.store.write(
            path,
            [{"code": "000001.SZ", "create_time": "2026-10-14 00:00:00", "close": 10.0}],
            DAY_COLS,
        )
        self.store.write(
            path,
            [
                {"code": "000001.SZ", "create_time": "2026-10-14 00:00:00", "close": 11.0},
                {"code": "600000.SH", "create_time": "2026-10-14 00:00:00", "close": 12.0},
            ],
            DAY_COLS,
        )
        back = self.store.read(path)
        self.assertEqual(len(back), 2)
        self.assertAlmostEqual(float(back["close"].iloc[0]), 11.0)

    def test_repeated_write_produces_identical_bytes(self) -> None:
        path = self.store.day_path("2026-10-14")
        rows = [
            {"code": "600000.SH", "create_time": "2026-10-14 00:00:00", "close": 12.0},
            {"code": "000001.SZ", "create_time": "2026-10-14 00:00:00", "close": 11.0},
        ]
        self.store.write(path, rows, DAY_COLS)
        first = path.read_bytes()
        self.store.write(path, list(reversed(rows)), DAY_COLS)
        second = path.read_bytes()
        # 排序键固定 + 覆盖写，同一份数据两次写出应当字节一致，
        # 否则「重刷」会无意义地改动文件 mtime 与内容哈希。
        self.assertEqual(first, second)

    def test_no_tmp_left_over(self) -> None:
        path = self.store.day_path("2026-10-14")
        self.store.write(
            path, [{"code": "a", "create_time": "x", "close": 1.0}], DAY_COLS
        )
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_empty_rows_keep_columns(self) -> None:
        path = self.store.day_path("2026-10-14")
        written = self.store.write(path, [], DAY_COLS)
        self.assertEqual(written, 0)
        self.assertTrue(path.is_file())
        back = self.store.read(path)
        self.assertEqual(list(back.columns), DAY_COLS)
        self.assertEqual(len(back), 0)

    def test_missing_columns_are_padded(self) -> None:
        path = self.store.day_path("2026-10-14")
        self.store.write(path, [{"code": "a"}], DAY_COLS)
        back = self.store.read(path)
        self.assertEqual(list(back.columns), DAY_COLS)

    def test_exists_helpers(self) -> None:
        self.assertFalse(self.store.exists_day("2026-10-14"))
        self.assertFalse(self.store.exists_minute("2026-10-14"))
        self.store.write(self.store.day_path("2026-10-14"), [], DAY_COLS)
        self.assertTrue(self.store.exists_day("2026-10-14"))
        self.assertFalse(self.store.exists_minute("2026-10-14"))

    def test_list_days(self) -> None:
        self.assertEqual(self.store.list_days("minute"), [])
        self.store.write(self.store.minute_path("2026-10-14"), [], MINUTE_COLS)
        self.store.write(self.store.minute_path("2026-09-30"), [], MINUTE_COLS)
        self.assertEqual(self.store.list_days("minute"), ["2026-09-30", "2026-10-14"])
        self.assertEqual(self.store.list_days("day"), [])


if __name__ == "__main__":
    unittest.main()
