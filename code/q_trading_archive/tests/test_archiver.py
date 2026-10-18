"""归档编排：进度推进、失败中断、空日跳过。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from archive.archiver import STATUS_EMPTY, STATUS_ERROR, STATUS_OK, Archiver
from archive.config import ArchiveConfig, MongoConfig
from archive.state import ArchiveState
from archive.store import ParquetStore

TODAY = date.today()


class FakeReader:
    """内存版读取器，替代 mongo。"""

    def __init__(self, dates=(), minute=None, day=None, fail=()):
        self.dates = list(dates)
        self.minute = minute or {}
        self.day = day or {}
        self.fail = set(fail)
        self.prefetch_calls = []

    def prefetch(self, start, end):
        self.prefetch_calls.append((start, end))

    def trading_dates(self, start, end):
        # 与 MongoReader 的契约一致：升序返回
        return sorted(d for d in self.dates if start <= d <= end)

    def earliest_date(self):
        return self.dates[0] if self.dates else None

    def read_minute(self, day):
        if day in self.fail:
            raise RuntimeError("read_minute boom")
        return self.minute.get(day, [])

    def read_day(self, day):
        if day in self.fail:
            raise RuntimeError("read_day boom")
        return self.day.get(day, [])


class ArchiverTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = ParquetStore(self.root / "data")
        self.state = ArchiveState(self.root / ".state.json")

    def make_archiver(self, reader, state=None, **overrides) -> Archiver:
        defaults = dict(
            refresh_days=2,
            write_empty_day=False,
            max_lookback_days=400,
        )
        defaults.update(overrides)
        cfg = ArchiveConfig(
            data_dir=self.store.root,
            refresh_days=defaults["refresh_days"],
            batch_size=100,
            schedule_hour=15,
            schedule_minute=30,
            max_lookback_days=defaults["max_lookback_days"],
            write_empty_day=defaults["write_empty_day"],
            mongo=MongoConfig("host", 1, "u", "p", "db"),
        )
        return Archiver(cfg, reader, self.store, state or self.state)


def _minute_row(code, ts):
    return {"code": code, "minute_time": ts, "open": 1.0}


def _day_row(code, day):
    return {"code": code, "create_time": day + " 00:00:00", "close": 1.0}


class TestPlan(ArchiverTestBase):
    def test_explicit_range(self) -> None:
        reader = FakeReader(dates=["2026-10-12", "2026-10-13", "2026-10-14"])
        archiver = self.make_archiver(reader)
        self.assertEqual(
            archiver.plan(start="2026-10-13", end="2026-10-14"),
            ["2026-10-13", "2026-10-14"],
        )

    def test_starts_from_last_state_minus_refresh(self) -> None:
        last = (TODAY - timedelta(days=10)).isoformat()
        self.state.save(last)
        dates = [
            (TODAY - timedelta(days=n)).isoformat() for n in (1, 3, 5, 11, 20)
        ]
        reader = FakeReader(dates=dates)
        archiver = self.make_archiver(reader, refresh_days=2)
        planned = archiver.plan()
        begin = (date.fromisoformat(last) - timedelta(days=2)).isoformat()
        expected = sorted(d for d in dates if begin <= d <= TODAY.isoformat())
        self.assertEqual(planned, expected)
        # 进度位之前 2 天的日期会被重刷，更早的不再处理
        self.assertIn((TODAY - timedelta(days=11)).isoformat(), planned)
        self.assertNotIn((TODAY - timedelta(days=20)).isoformat(), planned)

    def test_full_refresh_honours_lookback_floor(self) -> None:
        old = (TODAY - timedelta(days=900)).isoformat()
        recent = (TODAY - timedelta(days=5)).isoformat()
        reader = FakeReader(dates=[old, recent])
        archiver = self.make_archiver(reader, max_lookback_days=400)
        planned = archiver.plan(full_refresh=True)
        self.assertEqual(planned, [recent])

    def test_fresh_state_instance_reads_progress_from_disk(self) -> None:
        """回归：生产每次都是新建 ArchiveState，必须能从磁盘读到进度。

        旧实现把「加载」留成显式调用且调用方漏了，导致 last_date 恒为 None，
        每次运行都退化成从最早日期全量重跑（且不报错）。这里刻意不复用 setUp
        里的实例，而是新构造一个，以覆盖「从磁盘读取」这条真实路径。
        """
        last = (TODAY - timedelta(days=3)).isoformat()
        self.state.save(last)

        fresh = ArchiveState(self.root / ".state.json")
        self.assertEqual(fresh.last_date, last)

        dates = [(TODAY - timedelta(days=n)).isoformat() for n in (1, 2, 3, 4, 10, 30)]
        reader = FakeReader(dates=dates)
        archiver = self.make_archiver(reader, state=fresh, refresh_days=2)
        planned = archiver.plan()

        self.assertIn((TODAY - timedelta(days=4)).isoformat(), planned)
        self.assertNotIn((TODAY - timedelta(days=30)).isoformat(), planned)


class TestRun(ArchiverTestBase):
    def test_writes_files_and_advances_state(self) -> None:
        reader = FakeReader(
            dates=["2026-10-12", "2026-10-13"],
            minute={
                "2026-10-12": [_minute_row("000001.SZ", "2026-10-12 09:31:00")],
                "2026-10-13": [
                    _minute_row("000001.SZ", "2026-10-13 09:31:00"),
                    _minute_row("600000.SH", "2026-10-13 09:31:00"),
                ],
            },
            day={
                "2026-10-12": [_day_row("000001.SZ", "2026-10-12")],
                "2026-10-13": [_day_row("000001.SZ", "2026-10-13")],
            },
        )
        archiver = self.make_archiver(reader)
        report = archiver.run(start="2026-10-12", end="2026-10-13")

        self.assertEqual(report.total_minute_rows, 3)
        self.assertEqual(report.total_day_rows, 2)
        self.assertEqual(len(report.ok_days), 2)
        self.assertEqual(report.last_archived, "2026-10-13")
        self.assertTrue(self.store.exists_minute("2026-10-13"))
        self.assertEqual(self.state.last_date, "2026-10-13")

    def test_error_stops_and_does_not_advance_past(self) -> None:
        reader = FakeReader(
            dates=["2026-10-12", "2026-10-13", "2026-10-14"],
            minute={"2026-10-12": [_minute_row("000001.SZ", "2026-10-12 09:31:00")]},
            day={"2026-10-12": [_day_row("000001.SZ", "2026-10-12")]},
            fail=["2026-10-13"],
        )
        archiver = self.make_archiver(reader)
        report = archiver.run(start="2026-10-12", end="2026-10-14")

        self.assertEqual(len(report.failed_days), 1)
        self.assertEqual(len(report.days), 2, "失败当天即中断，不应继续处理 10-14")
        self.assertEqual(report.last_archived, "2026-10-12")
        self.assertEqual(self.state.last_date, "2026-10-12")
        self.assertFalse(self.store.exists_minute("2026-10-14"))

    def test_empty_day_skipped_without_advancing(self) -> None:
        reader = FakeReader(dates=["2026-10-12"])
        archiver = self.make_archiver(reader)
        report = archiver.run(start="2026-10-12", end="2026-10-12")

        self.assertEqual(report.days[0].status, STATUS_EMPTY)
        self.assertIsNone(report.last_archived)
        self.assertIsNone(self.state.last_date)

    def test_write_empty_day_when_enabled(self) -> None:
        reader = FakeReader(dates=["2026-10-12"])
        archiver = self.make_archiver(reader, write_empty_day=True)
        report = archiver.run(start="2026-10-12", end="2026-10-12")

        self.assertEqual(report.days[0].status, STATUS_OK)
        self.assertTrue(self.store.exists_minute("2026-10-12"))
        self.assertEqual(self.state.last_date, "2026-10-12")

    def test_dry_run_writes_nothing(self) -> None:
        reader = FakeReader(
            dates=["2026-10-12"],
            minute={"2026-10-12": [_minute_row("000001.SZ", "2026-10-12 09:31:00")]},
        )
        archiver = self.make_archiver(reader)
        report = archiver.run(start="2026-10-12", end="2026-10-12", dry_run=True)

        self.assertFalse(self.store.exists_minute("2026-10-12"))
        self.assertIsNone(self.state.last_date)
        self.assertEqual(len(report.days), 1)

    def test_rerun_is_idempotent(self) -> None:
        rows = [_minute_row("000001.SZ", "2026-10-12 09:31:00")]
        reader = FakeReader(
            dates=["2026-10-12"],
            minute={"2026-10-12": rows},
            day={"2026-10-12": [_day_row("000001.SZ", "2026-10-12")]},
        )
        archiver = self.make_archiver(reader)
        archiver.run(start="2026-10-12", end="2026-10-12")
        first = self.store.minute_path("2026-10-12").read_bytes()
        archiver.run(start="2026-10-12", end="2026-10-12")
        second = self.store.minute_path("2026-10-12").read_bytes()

        self.assertEqual(first, second)
        self.assertEqual(len(self.store.read(self.store.minute_path("2026-10-12"))), 1)

    def test_no_days_in_range(self) -> None:
        archiver = self.make_archiver(FakeReader(dates=[]))
        report = archiver.run(start="2026-10-12", end="2026-10-12")
        self.assertEqual(report.days, [])
        self.assertEqual(report.total_minute_rows, 0)


class TestPrefetch(ArchiverTestBase):
    def _reader(self):
        return FakeReader(
            dates=["2026-10-12", "2026-10-13"],
            minute={
                "2026-10-12": [_minute_row("000001.SZ", "2026-10-12 09:31:00")],
                "2026-10-13": [_minute_row("000001.SZ", "2026-10-13 09:31:00")],
            },
            day={
                "2026-10-12": [_day_row("000001.SZ", "2026-10-12")],
                "2026-10-13": [_day_row("000001.SZ", "2026-10-13")],
            },
        )

    def test_prefetch_called_once_for_whole_range(self) -> None:
        reader = self._reader()
        self.make_archiver(reader).run(start="2026-10-12", end="2026-10-13")
        # 索引前缀是 code，逐日查询会把同一张表反复全表扫；
        # 整个区间只预取一次是有意的性能约束。
        self.assertEqual(reader.prefetch_calls, [("2026-10-12", "2026-10-13")])

    def test_dry_run_does_not_prefetch(self) -> None:
        reader = self._reader()
        self.make_archiver(reader).run(start="2026-10-12", end="2026-10-13", dry_run=True)
        self.assertEqual(reader.prefetch_calls, [])

    def test_no_days_means_no_prefetch(self) -> None:
        reader = FakeReader(dates=[])
        self.make_archiver(reader).run(start="2026-10-12", end="2026-10-12")
        self.assertEqual(reader.prefetch_calls, [])


if __name__ == "__main__":
    unittest.main()
