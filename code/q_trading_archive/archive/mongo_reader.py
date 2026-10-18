"""只读 mongo 的行情读取。

三个约束来自实测，不是推测：

1. 该 mongo 实例限制了聚合管道（aggregate 报 40324、distinct 静默返回空），
   所以一律用 find + 本地去重，不依赖服务端聚合。
2. 表内 code 带后缀（603993.SH / 000001.SZ），且存在逐字重复记录
   （同一 (code, create_time) 存了多条完全相同的文档）。读取时按主键去重，
   同键取最后一条，与写入端的 upsert 语义一致。
3. 索引次序是 [("code", 1), ("minute_time", 1)] / [("code", 1), ("create_time", 1)]，
   前缀是 code。只按时间区间过滤时用不上索引，逐日循环会把同一张表反复全表扫。
   因此批量归档一律走 prefetch()：区间拉一次，本地按日分桶。

本模块只有读操作，任何写库行为都不应该出现在这里。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from pymongo import MongoClient

from .config import MongoConfig

MINUTE_COLLECTION = "minute_hq_tbl"
DAY_COLLECTION = "day_hq_tbl"

# 只保留归档需要的列，避免把 name/qrr 等冗余字段一并搬进 parquet。
MINUTE_FIELDS: tuple[str, ...] = (
    "code",
    "minute_time",
    "open",
    "high",
    "low",
    "close",
    "price",
    "preclose",
    "volume",
    "amount",
    "change_percent",
    "turnover",
    "amp",
)
DAY_FIELDS: tuple[str, ...] = (
    "code",
    "create_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
)


def _projection(fields: Iterable[str]) -> dict[str, int]:
    proj = {name: 1 for name in fields}
    proj["_id"] = 0
    return proj


def _bounds(time_field: str, start: str, end: str) -> dict[str, dict[str, str]]:
    return {time_field: {"$gte": start + " 00:00:00", "$lte": end + " 23:59:59"}}


def _day_bounds(day: str) -> dict[str, str]:
    return {"$gte": day + " 00:00:00", "$lte": day + " 23:59:59"}


class MongoReader:
    """只读行情读取器。"""

    def __init__(self, cfg: MongoConfig, batch_size: int = 20000) -> None:
        self._cfg = cfg
        self._batch_size = max(int(batch_size), 1)
        self._logger = logging.getLogger("archive.mongo")
        self._client = MongoClient(
            cfg.uri(),
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
        )
        self._db = self._client[cfg.database]
        self._minute_cache: dict[str, list[dict[str, Any]]] | None = None
        self._day_cache: dict[str, list[dict[str, Any]]] | None = None

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # pragma: no cover - 关闭失败不该影响主流程
            pass

    def ping(self) -> bool:
        """连通性自检，供启动时快速失败。"""
        try:
            self._client.admin.command("ping")
            return True
        except Exception as exc:
            self._logger.error("mongo 连通性检查失败: %s", exc)
            return False

    # ---------- 日期 ----------

    def trading_dates(self, start: str, end: str) -> list[str]:
        """返回区间内「库里有日K记录」的日期，升序。

        用有日K记录来判定交易日：非交易日不会有 day_hq_tbl 文档，
        不需要另外引入交易日历。
        """
        cursor = self._db[DAY_COLLECTION].find(
            _bounds("create_time", start, end),
            {"create_time": 1, "_id": 0},
            batch_size=self._batch_size,
        )
        days = {doc["create_time"][:10] for doc in cursor if doc.get("create_time")}
        return sorted(days)

    def earliest_date(self) -> str | None:
        doc = self._db[DAY_COLLECTION].find_one(
            {}, {"create_time": 1, "_id": 0}, sort=[("create_time", 1)]
        )
        if not doc or not doc.get("create_time"):
            return None
        return doc["create_time"][:10]

    # ---------- 批量预取 ----------

    def prefetch(self, start: str, end: str) -> None:
        """把区间内的分钟K / 日K 一次读进内存，按自然日分桶。

        逐日查询在本库上等价于反复全表扫描（索引前缀是 code，时间区间用不上），
        一次区间读取把扫描次数从「天数」降到 1。
        """
        self._minute_cache = self._load_range(
            MINUTE_COLLECTION, "minute_time", start, end, MINUTE_FIELDS
        )
        self._day_cache = self._load_range(
            DAY_COLLECTION, "create_time", start, end, DAY_FIELDS
        )
        self._logger.info(
            "预取完成：分钟 %d 天，日K %d 天",
            len(self._minute_cache),
            len(self._day_cache),
        )

    def clear_cache(self) -> None:
        self._minute_cache = None
        self._day_cache = None

    def _load_range(
        self,
        collection: str,
        time_field: str,
        start: str,
        end: str,
        fields: tuple[str, ...],
    ) -> dict[str, list[dict[str, Any]]]:
        cursor = self._db[collection].find(
            _bounds(time_field, start, end),
            _projection(fields),
            batch_size=self._batch_size,
        )
        buckets: dict[str, dict[tuple, dict[str, Any]]] = {}
        for doc in cursor:
            stamp = doc.get(time_field)
            if not stamp:
                continue
            day = str(stamp)[:10]
            buckets.setdefault(day, {})[(doc.get("code"), stamp)] = doc
        return {day: list(rows.values()) for day, rows in buckets.items()}

    # ---------- 行情 ----------

    def read_minute(self, day: str) -> list[dict[str, Any]]:
        if self._minute_cache is not None:
            return self._minute_cache.get(day, [])
        cursor = self._db[MINUTE_COLLECTION].find(
            {"minute_time": _day_bounds(day)},
            _projection(MINUTE_FIELDS),
            batch_size=self._batch_size,
        )
        return _dedup(cursor, key=("code", "minute_time"))

    def read_day(self, day: str) -> list[dict[str, Any]]:
        if self._day_cache is not None:
            return self._day_cache.get(day, [])
        cursor = self._db[DAY_COLLECTION].find(
            {"create_time": _day_bounds(day)},
            _projection(DAY_FIELDS),
            batch_size=self._batch_size,
        )
        return _dedup(cursor, key=("code", "create_time"))


def _dedup(cursor: Iterable[dict[str, Any]], key: tuple[str, ...]) -> list[dict[str, Any]]:
    """按 key 去重，同键后者覆盖前者。

    表内存在逐字重复文档，不去重会让 parquet 行数虚高，回测按行统计时被重复计入。
    """
    seen: dict[tuple, dict[str, Any]] = {}
    for doc in cursor:
        try:
            k = tuple(doc[name] for name in key)
        except KeyError:
            continue
        seen[k] = doc
    return list(seen.values())
