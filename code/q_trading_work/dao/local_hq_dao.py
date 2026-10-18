"""本地归档行情读取。

服务端对日/周/月K 与分钟统一只保留 90 天（`stock_fetch.remove_over_time_task`），
其中分钟没有任何回源路径，被删掉即永久丢失。`q_trading_archive` 进程把这些数据
按日转存成 parquet，本模块负责把它读回来给回测用。

分区布局与归档进程一致，注意两类目录的层级不同：

    <root>/minute/2026/10/2026-10-14.parquet
    <root>/day/2026/2026-10-14.parquet

读取策略：回测按 (code, 区间) 取数，而分区是「一天一个文件」。逐只股票去扫目录
会把同一批文件反复读一遍（池子几百只就是几百遍），所以这里按区间做一次懒加载，
再在内存里按 code 分桶，同一区间的后续请求直接命中内存。

完整性：**不允许把「本地只覆盖一部分」当成「这就是全部」**。服务端的回源判据
只看「区间内有任意记录」，导致跨越 90 天边界时静默返回残缺区间；本地读取不能
重犯这个错。归档进程的语义是「某天失败即中断、不越过空洞推进进度」，所以只要它
正常跑过，中间不会留缺口，据此用首末日期判断覆盖是否完整。覆盖不足时由调用方
按模式决定报错还是回落，见 `BacktestEngine._fetch_stock_data`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

KIND_MINUTE = "minute"
KIND_DAY = "day"

# 读取模式。默认 off，保证「不改配置就没有任何行为变化」。
MODE_OFF = "off"  # 全部走服务端 HTTP
MODE_PREFER = "prefer"  # 本地覆盖完整时用本地，否则整体回落 HTTP
MODE_ONLY = "only"  # 只用本地；覆盖不足或读取失败直接报错，不回落
VALID_MODES: tuple[str, ...] = (MODE_OFF, MODE_PREFER, MODE_ONLY)

# 各频率的时间列名（与归档列名、mongo 字段名一致）
TIME_FIELD: dict[str, str] = {
    KIND_MINUTE: "minute_time",
    KIND_DAY: "create_time",
}


class LocalHqError(RuntimeError):
    """本地归档数据缺失或读取失败，调用方应中止而不是降级成空表。"""


@dataclass
class LocalHqStats:
    """本次回测的本地读取统计。

    存在的理由：回测结果对取数来源敏感，而「这次到底读的本地还是 HTTP」
    如果不落数字，事后无法从结果反推。日志里有统计才谈得上复现。
    """

    local_hits: int = 0  # 本地命中并返回了数据
    local_empty: int = 0  # 本地覆盖完整，但该股票在区间内无记录（返回空表）
    coverage_miss: int = 0  # 本地覆盖不足，prefer 回落 HTTP / only 报错中止

    def summary(self) -> str:
        return (
            f"本地命中 {self.local_hits} / 本地空 {self.local_empty} / "
            f"覆盖不足 {self.coverage_miss}"
        )


class LocalHqDao:
    """读取 `q_trading_archive` 产出的按日 parquet。

    返回值与 `MarketApi.get_day_kline` / `get_minute_kline` 同构（list[dict]，
    列名即 mongo 字段名），调用方不需要做列名映射。
    """

    def __init__(self, root: str | Path) -> None:
        self._root: Path = Path(root)
        self._logger: logging.Logger = logging.getLogger(__name__)
        # 已归档日期列表按 kind 缓存：回测期间归档目录不会变，重复扫目录没有意义
        self._days_cache: dict[str, list[str]] = {}
        # 区间 → {code: [记录]}。key 含区间，避免不同区间互相污染
        self._bucket_cache: dict[tuple[str, str, str], dict[str, list[dict[str, Any]]]] = {}
        self.stats: LocalHqStats = LocalHqStats()

    # ---- 路径 ----

    def _path(self, kind: str, day: str) -> Path:
        """拼分区文件路径。分钟比日K 多一层月份目录，别写反。"""
        year, month, _ = day.split("-")
        if kind == KIND_MINUTE:
            return self._root / KIND_MINUTE / year / month / f"{day}.parquet"
        return self._root / KIND_DAY / year / f"{day}.parquet"

    # ---- 可用区间 ----

    def available_days(self, kind: str) -> list[str]:
        """列出已归档的日期（升序）。文件名为空或不符合 YYYY-MM-DD 的一律忽略。"""
        cached = self._days_cache.get(kind)
        if cached is not None:
            return cached

        base = self._root / kind
        days: list[str] = []
        if base.is_dir():
            for path in base.rglob("*.parquet"):
                stamp = path.stem
                # 归档的原子写会留下 .tmp，虽然产物里不该有，但扫到也不能当数据用
                if len(stamp) == 10 and stamp[4] == "-" and stamp[7] == "-":
                    days.append(stamp)
        days = sorted(set(days))
        self._days_cache[kind] = days
        return days

    def coverage(self, kind: str, start: str, end: str) -> tuple[bool, str]:
        """判断本地是否完整覆盖 [start, end]，返回 (是否完整, 原因)。

        只用首末日期判断：归档进程「失败即中断、不推进进度」，正常跑过就不会有
        中间缺口。区间内一天文件都没有时也算不完整。
        """
        days = self.available_days(kind)
        if not days:
            return False, f"本地没有任何 {kind} 归档文件（{self._root}）"

        within = [day for day in days if start <= day <= end]
        if not within:
            return False, f"区间 {start} ~ {end} 内没有 {kind} 归档文件"

        first, last = days[0], days[-1]
        if start < first:
            return False, f"本地最早只到 {first}，请求起点 {start} 更早"
        if end > last:
            return False, f"本地最晚只到 {last}，请求终点 {end} 更晚"
        return True, ""

    # ---- 读取 ----

    def _load_buckets(
        self, kind: str, start: str, end: str
    ) -> dict[str, list[dict[str, Any]]]:
        """把区间内的全部分区读进内存并按 code 分桶（结果缓存）。"""
        cache_key = (kind, start, end)
        cached = self._bucket_cache.get(cache_key)
        if cached is not None:
            return cached

        buckets: dict[str, list[dict[str, Any]]] = {}
        days = [day for day in self.available_days(kind) if start <= day <= end]
        total_rows = 0
        for day in days:
            path = self._path(kind, day)
            if not path.is_file():
                # 目录列表与实际文件不一致（并发归档中），跳过并留痕
                self._logger.warning("归档文件缺失，跳过: %s", path)
                continue
            try:
                frame = pd.read_parquet(path, engine="pyarrow")
            except Exception as exc:  # noqa: BLE001 - 读不了要报出来，不能当空数据
                raise LocalHqError(f"读取归档文件失败 {path}: {exc}") from exc

            records = frame.to_dict("records")
            total_rows += len(records)
            for record in records:
                buckets.setdefault(str(record.get("code", "")), []).append(record)

        self._logger.info(
            "本地 %s 归档加载完成: %d 天 / %d 行 / %d 只股票（%s ~ %s）",
            kind,
            len(days),
            total_rows,
            len(buckets),
            start,
            end,
        )
        self._bucket_cache[cache_key] = buckets
        return buckets

    def get_kline(
        self, kind: str, code: str, start: str, end: str
    ) -> list[dict[str, Any]] | None:
        """取单只股票在 [start, end] 的 K 线。

        :return: 升序记录列表；本地没有该股票的记录时返回 None（与空列表区分，
            便于调用方按模式决定回落还是报错）
        """
        buckets = self._load_buckets(kind, start, end)
        records = buckets.get(code)
        if not records:
            return None

        time_field = TIME_FIELD[kind]
        return sorted(records, key=lambda item: str(item.get(time_field, "")))

    def load_all(
        self, kind: str, start: str, end: str
    ) -> dict[str, list[dict[str, Any]]]:
        """按区间加载全部记录，返回 ``{code: [记录]}``。

        给「需要横截面」的离线分析用（形态统计、因子研究）：这些分析一次要
        全部股票的同一段时间，逐只调用 `get_kline` 会把同一批文件读 N 遍。
        记录未排序，调用方按需自行排序。
        """
        return self._load_buckets(kind, start, end)

    def invalidate(self) -> None:
        """清空缓存。回测中途归档目录变了（极少见）或需要释放内存时用。"""
        self._days_cache.clear()
        self._bucket_cache.clear()
