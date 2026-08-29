"""
Author: liguoqiang
Date: 2026-07-14
Description: 历史K线行情表 MongoDB 操作实现 — 日线/周线/月线
"""

# coding="utf8"

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Hashable

from pymongo import UpdateOne
from app_context import AppContext


def _his_code_regex(code: str) -> dict[str, Any]:
    """返回历史K线 code 字段的正则匹配条件。"""
    m = re.search(r"\d+", code)
    pure = m.group() if m else code
    return {"$regex": f"^(sh|sz)?{pure}(\\.(SH|SZ))?$"}


class MongoStockHisHqImpl:
    """历史K线行情表 MongoDB 操作实现 — 日线/周线/月线"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.day_hq_table = self.mongo_exec.db["day_hq_tbl"]
        self.week_hq_table = self.mongo_exec.db["week_hq_tbl"]
        self.month_hq_table = self.mongo_exec.db["month_hq_tbl"]

    # ===================== 日线 =====================

    def bulk_upsert_day_hq(self, records: Sequence[Mapping[Hashable, Any]]) -> bool:
        """批量插入或更新日线行情数据

        :param records: 日线行情记录列表，每项需包含 code 和 create_time
        :return: 成功返回 True，否则返回 False
        """
        return self._bulk_upsert_hq(self.day_hq_table, records, "日线")

    def query_day_hq(
        self,
        codes: list[str],
        start_time: str = "",
        end_time: str = "",
        skip: int = 0,
        limit: int = 0,
    ) -> tuple[bool, Any | None]:
        """查询日线行情数据

        :param codes: 股票代码列表
        :param start_time: 开始时间
        :param end_time: 结束时间
        :param skip: 跳过的记录数
        :param limit: 返回的记录数上限
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        return self._query_hq(self.day_hq_table, codes, start_time, end_time, skip, limit)

    def delete_day_hq(self, end_time: str) -> bool:
        """删除过期的日线行情数据

        :param end_time: 删除此时间之前的数据
        :return: 成功返回 True，否则返回 False
        """
        return self._delete_hq(self.day_hq_table, end_time, "日线")

    # ===================== 周线 =====================

    def bulk_upsert_week_hq(self, records: Sequence[Mapping[Hashable, Any]]) -> bool:
        """批量插入或更新周线行情数据"""
        return self._bulk_upsert_hq(self.week_hq_table, records, "周线")

    def query_week_hq(
        self,
        codes: list[str],
        start_time: str = "",
        end_time: str = "",
        skip: int = 0,
        limit: int = 0,
    ) -> tuple[bool, Any | None]:
        """查询周线行情数据"""
        return self._query_hq(self.week_hq_table, codes, start_time, end_time, skip, limit)

    def delete_week_hq(self, end_time: str) -> bool:
        """删除过期的周线行情数据"""
        return self._delete_hq(self.week_hq_table, end_time, "周线")

    # ===================== 月线 =====================

    def bulk_upsert_month_hq(self, records: Sequence[Mapping[Hashable, Any]]) -> bool:
        """批量插入或更新月线行情数据"""
        return self._bulk_upsert_hq(self.month_hq_table, records, "月线")

    def query_month_hq(
        self,
        codes: list[str],
        start_time: str = "",
        end_time: str = "",
        skip: int = 0,
        limit: int = 0,
    ) -> tuple[bool, Any | None]:
        """查询月线行情数据"""
        return self._query_hq(self.month_hq_table, codes, start_time, end_time, skip, limit)

    def delete_month_hq(self, end_time: str) -> bool:
        """删除过期的月线行情数据"""
        return self._delete_hq(self.month_hq_table, end_time, "月线")

    # ===================== 内部通用实现 =====================

    def _bulk_upsert_hq(
        self,
        table: Any,
        records: Sequence[Mapping[Hashable, Any]],
        label: str,
    ) -> bool:
        """批量插入或更新K线行情数据（内部通用实现）

        :param table: MongoDB 表对象
        :param records: 行情记录列表，每项需包含 code 和 create_time
        :param label: 日志标签（日线/周线/月线）
        :return: 成功返回 True，否则返回 False
        """
        try:
            requests = []
            for data in records:
                data = dict(data)
                data = data.copy()
                if "id" in data:
                    del data["id"]
                code = data.get("code", "")
                create_time = data.get("create_time", "")
                if not code or not create_time:
                    continue
                clean_data = {k: v for k, v in data.items() if k != "id"}
                requests.append(
                    UpdateOne(
                        {
                            "code": _his_code_regex(code),
                            "create_time": create_time,
                        },
                        {"$set": clean_data},
                        upsert=True,
                    )
                )
            if not requests:
                return False
            result = table.bulk_write(requests, ordered=False)
            self.logger.info(
                "批量upsert%s行情: matched=%d, modified=%d, upserted=%d",
                label,
                result.matched_count,
                result.modified_count,
                result.upserted_count,
            )
            return result.acknowledged
        except Exception as e:
            self.logger.error("批量插入%s行情数据失败: %s", label, e)
            return False

    @staticmethod
    def _normalize_query_time(time_str: str, is_start: bool) -> str:
        """归一化查询时间格式，兼容 YYYY-MM-DD 和 YYYY-MM-DD HH:MM:SS 两种 DB 存储格式

        当传入时间为 YYYY-MM-DD（10 位）时：
        - start_time: 保持原样，因为 "$gte": "2026-07-18" 在字符串比较中
          同时 <= "2026-07-18" 和 <= "2026-07-18 00:00:00"（前缀 < 长串）
        - end_time: 补充为 "YYYY-MM-DD 23:59:59"，确保 $lte 能匹配当天所有记录
        当传入时间已包含 HH:MM:SS 时不作处理。

        :param time_str: 原始时间字符串
        :param is_start: True 表示用于 $gte（开始时间），False 表示用于 $lte（结束时间）
        :return: 归一化后的时间字符串
        """
        if not time_str:
            return time_str
        # YYYY-MM-DD 格式：10 位，不含空格（即不含时间部分）
        if len(time_str) == 10 and " " not in time_str:
            if is_start:
                return time_str  # $gte 下 YYYY-MM-DD 已能同时匹配两种 DB 格式
            return f"{time_str} 23:59:59"  # $lte 下补齐时间，囊括当天所有数据
        return time_str

    def _query_hq(
        self,
        table: Any,
        codes: list[str],
        start_time: str,
        end_time: str,
        skip: int,
        limit: int,
    ) -> tuple[bool, Any | None]:
        """查询K线行情数据（内部通用实现，支持多代码）

        :param table: MongoDB 表对象
        :param codes: 股票代码列表
        :param start_time: 开始时间，支持 YYYY-MM-DD 和 YYYY-MM-DD HH:MM:SS 两种格式
        :param end_time: 结束时间，支持 YYYY-MM-DD 和 YYYY-MM-DD HH:MM:SS 两种格式
        :param skip: 跳过的记录数
        :param limit: 返回的记录数上限
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            if not codes:
                return True, None
            # 归一化 code：提取纯数字，构建 $or 条件
            code_conditions: list[dict[str, Any]] = []
            for c in codes:
                code_conditions.append({"code": _his_code_regex(c)})
            query: dict[str, Any] = {"$or": code_conditions} if len(code_conditions) > 1 else code_conditions[0]
            # 归一化时间格式，使 YYYY-MM-DD 能与 DB 中 YYYY-MM-DD HH:MM:SS 格式匹配
            start_normalized: str = self._normalize_query_time(start_time, is_start=True)
            end_normalized: str = self._normalize_query_time(end_time, is_start=False)
            if start_normalized:
                query["create_time"] = {"$gte": start_normalized}
            if end_normalized:
                if "create_time" in query:
                    query["create_time"]["$lte"] = end_normalized
                else:
                    query["create_time"] = {"$lte": end_normalized}
            sort_opts: dict[str, int] = {"code": 1, "create_time": 1}
            return self.mongo_exec.query_by_condition(
                table=table,
                condition=query,
                sort=sort_opts,
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            self.logger.error("查询K线行情数据失败: %s", e)
            return False, None

    def _delete_hq(self, table: Any, end_time: str, label: str) -> bool:
        """删除过期的K线行情数据

        :param table: MongoDB 表对象
        :param end_time: 删除此时间之前的数据
        :param label: 日志标签
        :return: 成功返回 True，否则返回 False
        """
        try:
            result = self.mongo_exec.delete(
                table=table,
                condition={"create_time": {"$lte": end_time}},
            )
            self.logger.info("删除过期%s行情: end_time=%s", label, end_time)
            return result
        except Exception as e:
            self.logger.error("删除过期%s行情失败: %s", label, e)
            return False
