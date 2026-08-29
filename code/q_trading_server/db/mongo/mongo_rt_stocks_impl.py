# coding="utf8"

import logging
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Hashable

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
            # 先查询是否存在记录（code 支持 xx、xxx.SH、shxxx 等多种格式）
            code_match = re.search(r"\d+", code)
            pure_code: str = code_match.group() if code_match else code
            code_regex = f"^(sh|sz)?{pure_code}(\\.(SH|SZ))?$"
            filter = {"code": {"$regex": code_regex}, "create_time": create_time}
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
            code = data["code"]
            create_time = data["create_time"]
            code_match = re.search(r"\d+", code)
            pure_code_bulk: str = code_match.group() if code_match else code
            requests.append(
                UpdateOne(
                    {
                        "code": {"$regex": f"^(sh|sz)?{pure_code_bulk}(\\.(SH|SZ))?$"},
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
                    # 列表：对每个 code 构建正则，用 $or 连接
                    or_clauses: list[dict[str, Any]] = []
                    for c in code:
                        if c and len(c) > 0:
                            m = re.search(r"\d+", c)
                            pure = m.group() if m else c
                            or_clauses.append({"code": {"$regex": f"^(sh|sz)?{pure}(\\.(SH|SZ))?$"}})
                    if or_clauses:
                        query["$or"] = or_clauses
                elif isinstance(code, str) and len(code) > 0:
                    # 单个字符串：保持原有逻辑
                    match = re.search(r"\d+", code)
                    pure_code = match.group() if match else code
                    query = {"code": {"$regex": f"^(sh|sz)?{pure_code}(\\.(SH|SZ))?$"}}
            if start_time or len(start_time) > 0:
                query['create_time'] = {'$gte': start_time}
            if end_time or len(end_time) > 0:
                if 'create_time' in query:
                    query['create_time']['$lte'] = end_time
                else:
                    query['create_time'] = {'$lte': end_time}
            return self.mongo_exec.query_by_condition(table=self.rt_stocks_table, condition=query, sort={"create_time": 1}, skip=skip, limit=limit)
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
                match = re.search(r"\d+", code)
                pure_code = match.group() if match else code
                query["code"] = {"$regex": f"^(sh|sz)?{pure_code}(\\.(SH|SZ))?$"}
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

    def aggregate_minute_hq_for_codes(
        self, codes: list[str], minute_time: str
    ) -> tuple[bool, Any | None]:
        """对指定代码列表，聚合指定分钟内所有实时行情记录为分钟OHLC

        使用 MongoDB aggregation pipeline，对 rt_stocks_tbl 中每只股票在
        指定分钟内的所有实时行情快照做统计聚合：
        - high: 分钟内最高价（max price）
        - low: 分钟内最低价（min price）
        - open: 分钟内第一笔价格（$first price）
        - price: 分钟内最后一笔价格（$last price）
        - volume: 分钟内成交量（last volume - first volume，delta）
        - amount: 分钟内成交额（last amount - first amount，delta）
        - preclose: 昨收价（来自 rt_stocks 记录，用于首分钟无前一分钟数据时）

        :param codes: 股票代码列表（支持 xxx、xxx.SH、xxx.SZ、shxxx、szxxx 等格式），
                      为空列表时聚合该分钟内所有代码
        :param minute_time: 分钟时间字符串，格式 "YYYY-MM-DD HH:MM:00"
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            # 归一化输入 code：提取纯数字
            pure_codes: list[str] = []
            for c in codes:
                m = re.search(r"\d+", c)
                pure_codes.append(m.group() if m else c)
            # 构建分钟前缀匹配
            minute_prefix = minute_time[:16]  # "YYYY-MM-DD HH:MM"
            # 构建 $match 条件：若 codes 为空则匹配该分钟内所有代码
            match_condition: dict[str, Any] = {
                "create_time": {"$regex": f"^{minute_prefix}"},
            }
            if pure_codes:
                match_condition["pure_code"] = {"$in": pure_codes}
            pipeline: list[dict[str, Any]] = [
                # 归一化 code：提取纯数字部分
                {
                    "$addFields": {
                        "pure_code": {
                            "$regexFind": {"input": "$code", "regex": "\\d+"}
                        }
                    }
                },
                {"$addFields": {"pure_code": "$pure_code.match"}},
                # 按归一化后的 pure_code + 分钟过滤
                {"$match": match_condition},
                # 按 create_time 升序，确保 $first/$last 正确
                {"$sort": {"create_time": 1}},
                # 按 pure_code 分组聚合
                {
                    "$group": {
                        "_id": "$pure_code",
                        "code": {"$last": "$code"},
                        "name": {"$last": "$name"},
                        "open": {"$first": "$price"},
                        "price": {"$last": "$price"},
                        "high": {"$max": "$price"},
                        "low": {"$min": "$price"},
                        "volume_first": {"$first": "$volume"},
                        "volume_last": {"$last": "$volume"},
                        "amount_first": {"$first": "$amount"},
                        "amount_last": {"$last": "$amount"},
                        "preclose": {"$first": "$preclose"},
                        "change_percent": {"$last": "$change_percent"},
                        "change_amount": {"$last": "$change_amount"},
                        "amp": {"$last": "$amp"},
                        "qrr": {"$last": "$qrr"},
                        "turnover": {"$last": "$turnover"},
                        "count": {"$sum": 1},
                    }
                },
                # 计算分钟的 volume/amount delta
                {
                    "$addFields": {
                        "volume": {
                            "$cond": {
                                "if": {"$gt": ["$count", 1]},
                                "then": {"$subtract": ["$volume_last", "$volume_first"]},
                                "else": "$volume_last",
                            }
                        },
                        "amount": {
                            "$cond": {
                                "if": {"$gt": ["$count", 1]},
                                "then": {"$subtract": ["$amount_last", "$amount_first"]},
                                "else": "$amount_last",
                            }
                        },
                    }
                },
                # 移除中间字段
                {
                    "$project": {
                        "volume_first": 0,
                        "volume_last": 0,
                        "amount_first": 0,
                        "amount_last": 0,
                        "count": 0,
                    }
                },
            ]
            result = list(self.rt_stocks_table.aggregate(pipeline))
            return True, result if result else None
        except Exception as e:
            self.logger.error(f"聚合分钟实时行情失败: {e}")
            return False, None

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
            # 归一化输入 code：提取纯数字，兼容 sh000001、000001.SH、000001 等格式
            pure_codes: list[str] = []
            for c in codes:
                m = re.search(r"\d+", c)
                pure_codes.append(m.group() if m else c)
            # 如果未指定分钟时间，取当前时间的分钟值
            if (not minute_time or len(minute_time) <= 0) and use_default_time:
                minute_time = datetime.now().strftime("%Y-%m-%d %H:%M:00")
            # 构建匹配条件：按归一化后的纯数字过滤 + 分钟过滤
            match_stage: dict[str, Any] = {"pure_code": {"$in": pure_codes}}
            # minute_time 格式如 "2026-06-22 09:30:00"，取前 16 位作为分钟前缀
            if minute_time and len(minute_time) > 0:
                minute_prefix = minute_time[:16]  # "2026-06-22 09:30"
                match_stage["create_time"] = {"$regex": f"^{minute_prefix}"}
            pipeline: list[dict[str, Any]] = [
                # 归一化 code：提取纯数字部分（兼容 sh000001、000001.SH 等格式）
                {
                    "$addFields": {
                        "pure_code": {
                            "$regexFind": {"input": "$code", "regex": "\\d+"}
                        }
                    }
                },
                {"$addFields": {"pure_code": "$pure_code.match"}},
                # 按归一化后的 pure_code + 可选分钟过滤
                {"$match": match_stage},
                # 按 create_time 降序，取每组最新一条
                {"$sort": {"create_time": -1}},
                {
                    "$group": {
                        "_id": "$pure_code",
                        "doc": {"$first": "$$ROOT"},
                    }
                },
                {"$replaceRoot": {"newRoot": "$doc"}},
            ]
            result = list(self.rt_stocks_table.aggregate(pipeline))
            return True, result if result else None
        except Exception as e:
            self.logger.error(f"查询最新实时股票信息失败: {e}")
            return False, None