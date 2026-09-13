# coding="utf8"

import logging
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Hashable
from utils import tools
from pymongo import UpdateOne
from app_context import AppContext

class MongoRtStocksImpl():
    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.rt_stocks_table = self.mongo_exec.db["rt_stocks_tbl"]

    """
    插入或更新实时股票信息
    :param data: 股票信息字典
    :return: 成功返回True和记录ID，否则返回False和None
    """
    def insert_or_update_rt_stock_info(self, data: dict[str, Any]) -> tuple[bool, str|None]:
        try:
            data = data.copy()
            if "id" in data:
                del data["id"]
            code = data.get("code", "")
            if not code:
                self.logger.error("code is empty.")
                return False, None
            create_time = data.get("create_time", "")
            if not create_time:
                self.logger.error("create_time is empty.")
                return False, None
            normal_code = tools.guess_stock_code_suffix(code)
            filter = {"code": normal_code, "create_time": create_time}
            result = self.rt_stocks_table.update_one(filter, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None
        except Exception as e:
            self.logger.error(f"插入或更新实时股票信息失败: {e}")
            return False, None
        
    """
    批量插入或更新实时股票信息
    :param records: 股票信息列表
    :return: 成功返回True，否则返回False
    """
    def bulk_upsert_rt_stock_info(self, records: Sequence[Mapping[Hashable, Any]]) -> bool:
        requests = []
        for data in records:
            data = dict(data)
            data = data.copy()
            if "id" in data:
                del data["id"]
            code = tools.guess_stock_code_suffix(data["code"])
            create_time = data["create_time"]
            requests.append(
                UpdateOne(
                    {
                        "code": code,
                        "create_time": create_time
                    },
                    {"$set": dict(data)},
                    upsert=True,
                )
            )
        if not requests:
            return False
        result = self.rt_stocks_table.bulk_write(requests, ordered=False)
        self.logger.info(
            "批量upsert实时行情: matched=%d, modified=%d, upserted=%d",
            result.matched_count, result.modified_count, result.upserted_count,
        )
        return result.acknowledged

    """
    查询实时股票信息
    :param code: 股票代码
    :param start_time: 开始时间
    :param end_time: 结束时间
    :return: 成功返回True和记录列表，否则返回False和None
    """
    def query_rt_stocks(self, code: str | list[str] = "", start_time: str = "", end_time: str = "", skip: int = 0, limit: int = 0) -> tuple[bool, Any|None]:
        """查询实时股票信息

        :param code: 股票代码，支持单个字符串或字符串列表，为空时查询所有代码
        :param start_time: 开始时间，格式 %Y-%m-%d %H:%M:%S
        :param end_time: 结束时间，格式 %Y-%m-%d %H:%M:%S
        :param skip: 分页跳过数
        :param limit: 分页限制数
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            query: dict[str, Any] = {}
            if code:
                if isinstance(code, list):
                    full_codes = [tools.guess_stock_code_suffix(c) for c in code]
                    query = {"code": {"$in": full_codes}}
                elif isinstance(code, str) and len(code) > 0:
                    # 单个字符串：保持原有逻辑
                    query = {"code": tools.guess_stock_code_suffix(code)}
            if start_time or len(start_time) > 0:
                query['create_time'] = {'$gte': start_time}
            if end_time or len(end_time) > 0:
                if 'create_time' in query:
                    query['create_time']['$lte'] = end_time
                else:
                    query['create_time'] = {'$lte': end_time}
            return self.mongo_exec.query_by_condition(table=self.rt_stocks_table, condition=query, sort={"create_time": -1, "change_percent": 1}, skip=skip, limit=limit)
        except Exception as e:
            self.logger.error(f"获取实时股票信息失败: {e}")
            return False, None
    
    """
    删除实时股票信息
    :param code: 股票代码
    :param start_time: 开始时间
    :param end_time: 结束时间
    :return: 成功返回True，否则返回False
    """
    def delete_rt_stocks(self, code: str, start_time: str, end_time: str) -> bool:
        try:
            query :dict[str, Any]= {}
            if code and len(code) > 0:
                query["code"] = tools.guess_stock_code_suffix(code)
            if start_time or len(start_time) > 0:
                query['create_time'] = {'$gte': start_time}
            if end_time or len(end_time) > 0:
                if 'create_time' in query:
                    query['create_time']['$lte'] = end_time
                else:
                    query['create_time'] = {'$lte': end_time}
            result = self.mongo_exec.delete(table=self.rt_stocks_table, condition=query)
            return result
        except Exception as e:
            self.logger.error(f"删除实时股票信息失败: {e}")
            return False

    def query_latest_rt_stocks_for_codes(
        self, codes: list[str], minute_time: str = "", use_default_time: bool = True
    ) -> tuple[bool, Any | None]:
        """查询指定股票代码列表的最新实时行情（每只股票取最新一条）

        code 可能包含不同格式（sh000001、000001.SH、000001），
        通过提取纯数字部分归一化后再分组，确保同只股票只返回一条。

        如果指定 minute_time（格式 YYYY-MM-DD HH:MM:00），则只匹配该分钟内的数据。

        :param codes: 股票代码列表（支持 xxx、xxx.SH、xxx.SZ、shxxx、szxxx 等格式）
        :param minute_time: 可选，分钟时间字符串，只查询该分钟内的行情
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            if not codes:
                return True, None
            # 如果未指定分钟时间，取当前时间的分钟值
            if (not minute_time or len(minute_time) <= 0) and use_default_time:
                now_time = datetime.now()
                minute_time = datetime.now().strftime("%Y-%m-%d %H:%M:00")
                if now_time.hour > 15 or (now_time.hour == 15 and now_time.minute > 30):
                    minute_time = datetime.now().strftime("%Y-%m-%d 15:30:00")
                if now_time.hour < 9 or (now_time.hour == 9 and now_time.minute < 30):
                    minute_time = datetime.now().strftime("%Y-%m-%d 09:30:00")
            # 构建匹配条件：按归一化后的纯数字过滤 + 分钟过滤
            full_codes = [tools.guess_stock_code_suffix(c) for c in codes]
            match_stage: dict[str, Any] = {"code": {"$in": full_codes}}
            # minute_time 格式如 "2026-06-22 09:30:00"，取前 16 位作为分钟前缀
            if minute_time and len(minute_time) > 0:
                minute_prefix = minute_time[:16]  # "2026-06-22 09:30"
                match_stage["create_time"] = {"$regex": f"^{minute_prefix}"}
            pipeline: list[dict[str, Any]] = [
                {"$match": match_stage},
                # 按 create_time 降序，取每组最新一条
                {"$sort": {"create_time": -1, "change_percent": 1}},
                {
                    "$group": {
                        "_id": "$code",
                        "doc": {"$first": "$$ROOT"},
                    }
                },
                {"$replaceRoot": {"newRoot": "$doc"}},
            ]
            result = list(
                self.rt_stocks_table.aggregate(pipeline, allowDiskUse=True)
            )
            return True, result if result else None
        except Exception as e:
            self.logger.error(f"查询最新实时股票信息失败: {e}")
            return False, None