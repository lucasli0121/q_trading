"""归档编排：逐日把 mongo 的分钟K / 日K 转存为本地 parquet。

增量策略是「进度位 + 每次重刷最近若干天」，两个机制各有分工：

- 进度位（ArchiveState.last_date）决定从哪里开始补，停机几天不会漏。
- 重刷最近 refresh_days 天：盘中跑过一次会留下半截的当日文件，
  重刷把它覆盖成完整版本。这是幂等的前提，没有它就得靠人工判断哪天该重跑。

失败处理：某天读取失败就在该天中断进度推进，不跳过继续。跳过会让进度位
越过一个空洞，之后任何一次增量都不会再补它。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from .config import ArchiveConfig
from .mongo_reader import DAY_FIELDS, MINUTE_FIELDS, MongoReader
from .state import ArchiveState
from .store import ParquetStore

# 单日结果状态
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_ERROR = "error"
STATUS_DRY = "dry"


@dataclass
class DayResult:
    day: str
    minute_rows: int = 0
    day_rows: int = 0
    status: str = STATUS_OK
    error: str = ""

    @property
    def written(self) -> bool:
        return self.status == STATUS_OK


@dataclass
class ArchiveReport:
    start: str = ""
    end: str = ""
    days: list[DayResult] = field(default_factory=list)
    last_archived: str | None = None

    @property
    def total_minute_rows(self) -> int:
        return sum(d.minute_rows for d in self.days)

    @property
    def total_day_rows(self) -> int:
        return sum(d.day_rows for d in self.days)

    @property
    def ok_days(self) -> list[DayResult]:
        return [d for d in self.days if d.status == STATUS_OK]

    @property
    def failed_days(self) -> list[DayResult]:
        return [d for d in self.days if d.status == STATUS_ERROR]

    @property
    def dry_days(self) -> list[DayResult]:
        return [d for d in self.days if d.status == STATUS_DRY]

    def summary(self) -> str:
        if self.dry_days:
            return "区间 %s ~ %s，待归档交易日 %d 个（dry-run）" % (
                self.start,
                self.end,
                len(self.dry_days),
            )
        return (
            "区间 %s ~ %s，交易日 %d 个（写入 %d / 空 %d / 失败 %d），"
            "分钟 %d 行，日K %d 行，进度推进到 %s"
            % (
                self.start,
                self.end,
                len(self.days),
                len(self.ok_days),
                len([d for d in self.days if d.status == STATUS_EMPTY]),
                len(self.failed_days),
                self.total_minute_rows,
                self.total_day_rows,
                self.last_archived or "未推进",
            )
        )


class Archiver:
    """逐日归档的执行体。"""

    def __init__(
        self,
        cfg: ArchiveConfig,
        reader: MongoReader,
        store: ParquetStore,
        state: ArchiveState,
        logger: logging.Logger | None = None,
    ) -> None:
        self._cfg = cfg
        self._reader = reader
        self._store = store
        self._state = state
        self._logger = logger or logging.getLogger("archive.archiver")

    # ---------- 计划 ----------

    def plan(
        self,
        start: str | None = None,
        end: str | None = None,
        refresh_days: int | None = None,
        full_refresh: bool = False,
    ) -> list[str]:
        """算出本次要处理的交易日列表。"""
        today = date.today()
        end = end or today.isoformat()
        floor = (today - timedelta(days=self._cfg.max_lookback_days)).isoformat()

        if start:
            begin = start
        elif full_refresh:
            earliest = self._reader.earliest_date() or end
            begin = max(earliest, floor)
        else:
            last = self._state.last_date
            if last:
                span = self._cfg.refresh_days if refresh_days is None else refresh_days
                begin = (date.fromisoformat(last) - timedelta(days=max(int(span), 0))).isoformat()
            else:
                begin = floor

        return self._reader.trading_dates(begin, end)

    # ---------- 执行 ----------

    def run(
        self,
        start: str | None = None,
        end: str | None = None,
        refresh_days: int | None = None,
        full_refresh: bool = False,
        dry_run: bool = False,
    ) -> ArchiveReport:
        days = self.plan(
            start=start, end=end, refresh_days=refresh_days, full_refresh=full_refresh
        )
        report = ArchiveReport(start=days[0] if days else "", end=days[-1] if days else "")

        if not days:
            self._logger.info("区间内没有需要归档的交易日")
            return report

        self._logger.info("计划归档 %d 个交易日: %s ~ %s", len(days), days[0], days[-1])
        if dry_run:
            for day in days:
                self._logger.info("[dry-run] 将归档 %s", day)
            report.days = [DayResult(day=day, status=STATUS_DRY) for day in days]
            return report

        # 区间一次预取，避免逐日查询反复全表扫描（索引前缀是 code，时间区间用不上）。
        prefetch = getattr(self._reader, "prefetch", None)
        if callable(prefetch):
            prefetch(days[0], days[-1])

        for day in days:
            result = self._archive_day(day)
            report.days.append(result)
            if result.status == STATUS_ERROR:
                # 不越过空洞：后续日期留待下次运行。
                self._logger.error("%s 归档失败，本次在此中断: %s", day, result.error)
                break

        report.last_archived = self._advance_state(report.days)
        return report

    def _archive_day(self, day: str) -> DayResult:
        try:
            minute_rows = self._reader.read_minute(day)
            day_rows = self._reader.read_day(day)
        except Exception as exc:
            return DayResult(day=day, status=STATUS_ERROR, error=str(exc))

        if not minute_rows and not day_rows and not self._cfg.write_empty_day:
            self._logger.info("%s 库内无行情记录，跳过", day)
            return DayResult(day=day, status=STATUS_EMPTY)

        try:
            written_minute = 0
            written_day = 0
            if minute_rows or self._cfg.write_empty_day:
                written_minute = self._store.write(
                    self._store.minute_path(day), minute_rows, list(MINUTE_FIELDS)
                )
            if day_rows or self._cfg.write_empty_day:
                written_day = self._store.write(
                    self._store.day_path(day), day_rows, list(DAY_FIELDS)
                )
        except Exception as exc:
            return DayResult(day=day, status=STATUS_ERROR, error=str(exc))

        self._logger.info(
            "%s 归档完成：分钟 %d 行，日K %d 行", day, written_minute, written_day
        )
        return DayResult(
            day=day, minute_rows=written_minute, day_rows=written_day, status=STATUS_OK
        )

    def _advance_state(self, results: list[DayResult]) -> str | None:
        """把进度推进到「连续成功」的最后一天。"""
        last_ok: str | None = None
        for result in results:
            if result.status == STATUS_ERROR:
                break
            if result.status == STATUS_OK:
                last_ok = result.day
        if last_ok:
            self._state.save(last_ok, {"updated_at": _now_iso()})
            self._logger.info("归档进度推进到 %s", last_ok)
        return last_ok


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
