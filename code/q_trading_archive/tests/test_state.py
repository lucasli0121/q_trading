"""归档进度文件。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archive.state import ArchiveState


class TestArchiveState(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / ".state.json"

    def test_missing_file_gives_no_progress(self) -> None:
        state = ArchiveState(self.path).load()
        self.assertIsNone(state.last_date)

    def test_construct_reads_existing_file(self) -> None:
        """回归：构造即加载，无需显式调用 load()。"""
        ArchiveState(self.path).save("2026-10-14")
        self.assertEqual(ArchiveState(self.path).last_date, "2026-10-14")

    def test_save_then_load(self) -> None:
        ArchiveState(self.path).save("2026-10-14", {"updated_at": "2026-10-14 15:30:00"})
        state = ArchiveState(self.path).load()
        self.assertEqual(state.last_date, "2026-10-14")
        self.assertEqual(state.as_dict().get("updated_at"), "2026-10-14 15:30:00")

    def test_save_overwrites(self) -> None:
        ArchiveState(self.path).save("2026-10-13")
        ArchiveState(self.path).save("2026-10-14")
        self.assertEqual(ArchiveState(self.path).load().last_date, "2026-10-14")

    def test_corrupt_file_degrades_to_no_progress(self) -> None:
        self.path.write_bytes(b"{ this is not json")
        state = ArchiveState(self.path).load()
        # 进度文件损坏只影响增量起点，不该让进程起不来。
        self.assertIsNone(state.last_date)

    def test_no_tmp_left_over(self) -> None:
        ArchiveState(self.path).save("2026-10-14")
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
