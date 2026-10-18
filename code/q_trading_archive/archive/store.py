"""parquet 落盘：按日分区、原子写、确定性排序。

分区口径是「一个交易日一个文件」（不是一只股票一个文件）。回测与形态统计
都是「取某段时间的全部股票」，按日分区只扫区间内的文件；按股票分区则要遍历
整个池子的目录，且池子成员每天都在变。

原子写：先写同目录下的 .tmp，再用 os.replace 覆盖目标。中途崩溃只会留下
一个 .tmp，不会产出半截 parquet 被下游当成完整数据读走。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

# 排序键：确保同一份数据多次写出得到字节稳定的文件，便于 diff 和校验。
SORT_KEYS: dict[str, list[str]] = {
    "minute": ["minute_time", "code"],
    "day": ["create_time", "code"],
}


class ParquetStore:
    """按日分区的 parquet 存储。"""

    def __init__(self, data_dir: Path) -> None:
        self._root = Path(data_dir)
        self._logger = logging.getLogger("archive.store")

    @property
    def root(self) -> Path:
        return self._root

    def minute_path(self, day: str) -> Path:
        year, month, _ = day.split("-")
        return self._root / "minute" / year / month / ("%s.parquet" % day)

    def day_path(self, day: str) -> Path:
        year, _, _ = day.split("-")
        return self._root / "day" / year / ("%s.parquet" % day)

    def exists_minute(self, day: str) -> bool:
        return self.minute_path(day).is_file()

    def exists_day(self, day: str) -> bool:
        return self.day_path(day).is_file()

    def write(self, path: Path, rows: Iterable[dict[str, Any]], columns: list[str]) -> int:
        """写出一个分区文件，返回行数。已存在则整体覆盖（幂等）。"""
        records = list(rows)
        frame = pd.DataFrame(records, columns=columns) if records else pd.DataFrame(
            {name: pd.Series(dtype="object") for name in columns}
        )

        kind = "minute" if "minute" in path.parts else "day"
        keys = [k for k in SORT_KEYS.get(kind, []) if k in frame.columns]
        if keys:
            frame = frame.sort_values(keys, kind="stable").reset_index(drop=True)

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        try:
            frame.to_parquet(tmp, engine="pyarrow", index=False, compression="zstd")
            os.replace(tmp, path)
        except Exception:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:  # pragma: no cover - 清理失败不改写原异常
                    pass
            raise
        return len(frame)

    def read(self, path: Path) -> pd.DataFrame:
        """读回一个分区文件，供校验使用。"""
        return pd.read_parquet(path, engine="pyarrow")

    def list_days(self, kind: str = "minute") -> list[str]:
        """列出已归档的日期（升序）。kind 取 minute / day。"""
        base = self._root / kind
        if not base.is_dir():
            return []
        days = [p.stem for p in base.rglob("*.parquet")]
        return sorted(days)
