"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-06-21 20:00:00
Description: 分频行情表 MongoDB 操作实现
"""

# coding="utf8"

import logging
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Hashable

from pymongo import UpdateOne
from app_context import AppContext


def _minute_code_regex(code: str) -> dict[str, Any]:
    """返回分频行情 code 字段的正则匹配条件。"""
    m = re.search(r"\d+", code)
    pure = m.group() if m else code
    return {"$regex": f"^(sh|sz)?{pure}(\\.(SH|SZ))?$"}


class MongoStockMinuteHqImpl:
    """分频行情表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.minute_hq_table = self.mongo_exec.db["minute_hq_tbl"]

    def bulk_upsert_minute_hq(self, records: Sequence[Mapping[Hashable, Any]]) -> bool:
        """批量插入或更新分频行情数据

        :param records: 分频行情记录列表，每项需包含 code 和 minute_time
        :return: 成功返回 True，否则返回 False
        """
        try:
            requests = []
            for data in records:
                data = dict(data)
                data = data.copy()
                if "id" in data:
                    del data["id"]
                code = data["code"]
                minute_time = data.get("minute_time", "")
                if not minute_time:
                    continue
                clean_data = {k: v for k, v in data.items() if k != "id"}
                requests.append(
                    UpdateOne(
                        {"code": _minute_code_regex(code), "minute_time": minute_time},
                        {"$set": clean_data},
                        upsert=True,
                    )
                )
            if not requests:
                return False
            result = self.minute_hq_table.bulk_write(requests, ordered=False)
            self.logger.info(
                "批量upsert分频行情: matched=%d, modified=%d, upserted=%d",
                result.matched_count, result.modified_count, result.upserted_count,
            )
            return result.acknowledged
        except Exception as e:
            self.logger.error(f"批量插入分频行情数据失败: {e}")
            return False

    def query_minute_hq(
        self,
        code: str,
        start_time: str = "",
        end_time: str = "",
        skip: int = 0,
        limit: int = 0,
    ) -> tuple[bool, Any | None]:
        """查询分频行情数据

        :param code: 股票代码
        :param start_time: 开始分钟时间
        :param end_time: 结束分钟时间
        :param skip: 跳过的记录数
        :param limit: 返回的记录数上限
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"code": _minute_code_regex(code)}
            if start_time:
                query["minute_time"] = {"$gte": start_time}
            if end_time:
                if "minute_time" in query:
                    query["minute_time"]["$lte"] = end_time
                else:
                    query["minute_time"] = {"$lte": end_time}
            return self.mongo_exec.query_by_condition(
                table=self.minute_hq_table,
                condition=query,
                sort={"minute_time": 1},
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            self.logger.error(f"查询分频行情数据失败: {e}")
            return False, None

    def query_latest_minute_hq_today(self) -> tuple[bool, Any | None]:
        """查询当天每只股票最新一条分频行情记录

        使用 MongoDB 聚合管道按 code 分组，直接返回每只股票当天最新一条记录。
        避免在 Python 层遍历全量数据做去重。

        :return: 成功返回 True 和记录列表（每只股票一条最新记录），否则返回 False 和 None
        """
        try:
            today_start = datetime.now().strftime("%Y-%m-%d 00:00:00")
            pipeline = [
                {"$match": {"minute_time": {"$gte": today_start}}},
                {"$sort": {"minute_time": -1}},
                {"$group": {
                    "_id": "$code",
                    "doc": {"$first": "$$ROOT"},
                }},
                {"$replaceRoot": {"newRoot": "$doc"}},
            ]
            results = list(self.minute_hq_table.aggregate(pipeline))
            return (True, results) if results else (True, None)
        except Exception as e:
            self.logger.error(f"查询当天分频行情失败: {e}")
            return False, None

    def query_minute_hq_by_time(
        self, minute_time: str
    ) -> tuple[bool, Any | None]:
        """查询指定分钟时间的所有股票分频行情

        用于获取前一分钟所有股票的收盘价，作为当前分钟 preclose 的计算依据。

        :param minute_time: 分钟时间字符串，格式 "YYYY-MM-DD HH:MM:00"
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {"minute_time": minute_time}
            return self.mongo_exec.query_by_condition(
                table=self.minute_hq_table,
                condition=query,
                sort={"code": 1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按分钟时间查询分频行情失败: {e}")
            return False, None

    def delete_minute_hq(self, code: str, start_time: str = "", end_time: str = "") -> bool:
        """删除分频行情数据

        :param code: 股票代码，为空则匹配所有
        :param start_time: 开始分钟时间
        :param end_time: 结束分钟时间
        :return: 成功返回 True，否则返回 False
        """
        try:
            query: dict[str, Any] = {}
            if code:
                query["code"] = _minute_code_regex(code)
            if start_time:
                query["minute_time"] = {"$gte": start_time}
            if end_time:
                if "minute_time" in query:
                    query["minute_time"]["$lte"] = end_time
                else:
                    query["minute_time"] = {"$lte": end_time}
            result = self.mongo_exec.delete(table=self.minute_hq_table, condition=query)
            return result
        except Exception as e:
            self.logger.error(f"删除分频行情数据失败: {e}")
            return False
