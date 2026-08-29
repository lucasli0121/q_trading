# coding="utf8"

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Hashable

from pymongo import UpdateOne
from app_context import AppContext


class MongoCompanyFinanceImpl:
    """公司财务表 MongoDB 操作实现。"""

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.company_finance_table = self.mongo_exec.db["company_finance_tbl"]

    def normalize_code(self, code: str) -> str:
        """提取股票代码中的数字部分，兼容 sh/sz 前缀和后缀。"""
        match = re.search(r"\d+", code)
        return match.group() if match else code

    def insert_or_update_company_finance(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """插入或更新公司财务数据。

        :param data: 公司财务数据字典
        :return: 成功返回 True 和记录 ID，否则返回 False 和 None
        """
        try:
            data = data.copy()
            code = data.get("code", "")
            report_date = data.get("report_date", "")
            if not code:
                self.logger.error("code is empty.")
                return False, None
            if not report_date:
                self.logger.error("report_date is empty.")
                return False, None
            if "id" in data:
                del data["id"]
            pure_code = self.normalize_code(code)
            filter: dict[str, Any] = {
                "code": {"$regex": f"^(sh|sz)?{pure_code}(\\.(SH|SZ))?$"},
                "report_date": report_date,
            }
            result = self.company_finance_table.update_one(filter, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None
        except Exception as e:
            self.logger.error(f"插入或更新公司财务信息失败: {e}")
            return False, None

    def bulk_upsert_company_finance(self, records: Sequence[Mapping[Hashable, Any]]) -> bool:
        """批量插入或更新公司财务数据。"""
        try:
            requests = []
            for data in records:
                data = dict(data)
                data = data.copy()
                if "id" in data:
                    del data["id"]
                code = data.get("code", "")
                report_date = data.get("report_date", "")
                if not code or not report_date:
                    continue
                pure_code = self.normalize_code(code)
                requests.append(
                    UpdateOne(
                        {
                            "code": {"$regex": f"^(sh|sz)?{pure_code}(\\.(SH|SZ))?$"},
                            "report_date": report_date,
                        },
                        {"$set": {k: v for k, v in data.items() if k != "id"}},
                        upsert=True,
                    )
                )
            if not requests:
                return False
            result = self.company_finance_table.bulk_write(requests, ordered=False)
            self.logger.info(
                "批量upsert公司财务: matched=%d, modified=%d, upserted=%d",
                result.matched_count, result.modified_count, result.upserted_count,
            )
            return result.acknowledged
        except Exception as e:
            self.logger.error(f"批量插入或更新公司财务信息失败: {e}")
            return False

    def query_company_finance(
        self,
        code: str,
        report_date: str = "",
        skip: int = 0,
        limit: int = 0,
    ) -> tuple[bool, Any | None]:
        """按股票代码和可选报表日期查询公司财务数据。"""
        try:
            pure_code = self.normalize_code(code)
            query: dict[str, Any] = {
                "code": {"$regex": f"^(sh|sz)?{pure_code}(\\.(SH|SZ))?$"}
            }
            if report_date:
                query["report_date"] = report_date
            return self.mongo_exec.query_by_condition(
                table=self.company_finance_table,
                condition=query,
                sort={"report_date": 1},
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            self.logger.error(f"查询公司财务信息失败: {e}")
            return False, None

    def query_all_company_finance(self, skip: int = 0, limit: int = 0) -> tuple[bool, Any | None]:
        """查询所有公司财务数据。"""
        try:
            return self.mongo_exec.query_by_condition(
                table=self.company_finance_table,
                condition={},
                sort={"code": 1, "report_date": 1},
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            self.logger.error(f"查询所有公司财务信息失败: {e}")
            return False, None

    def query_latest_finance_by_codes(
        self,
        codes: list[str],
        skip: int = 0,
        limit: int = 0,
    ) -> tuple[bool, Any | None]:
        """按股票代码列表批量查询最新一期财务数据。

        对每个代码返回 report_date 最大的一条记录。

        :param codes: 股票代码列表（支持纯数字或带前后缀格式）
        :param skip: 分页跳过条数
        :param limit: 分页限制条数，0 表示不限制
        :return: (ok, records_or_None)
        """
        try:
            if not codes:
                return True, []
            pure_codes = [self.normalize_code(c) for c in codes]
            regex_patterns = [
                f"^(sh|sz)?{pc}(\\.(SH|SZ))?$" for pc in pure_codes
            ]
            or_conditions = [
                {"code": {"$regex": pattern}} for pattern in regex_patterns
            ]
            pipeline: list[dict[str, Any]] = [
                {"$match": {"$or": or_conditions}},
                {"$sort": {"code": 1, "report_date": -1}},
                {
                    "$group": {
                        "_id": "$code",
                        "doc": {"$first": "$$ROOT"},
                    }
                },
                {"$replaceRoot": {"newRoot": "$doc"}},
                {"$sort": {"code": 1}},
            ]
            if skip > 0:
                pipeline.append({"$skip": skip})
            if limit > 0:
                pipeline.append({"$limit": limit})
            cursor = self.company_finance_table.aggregate(pipeline)
            records = list(cursor)
            return True, records
        except Exception as e:
            self.logger.error(f"批量查询最新财务数据失败: {e}")
            return False, None

    def delete_company_finance(self, code: str = "", report_date: str = "") -> bool:
        """按股票代码或报表日期删除公司财务数据。"""
        try:
            query: dict[str, Any] = {}
            if code:
                pure_code = self.normalize_code(code)
                query["code"] = {"$regex": f"^(sh|sz)?{pure_code}(\\.(SH|SZ))?$"}
            if report_date:
                query["report_date"] = report_date
            if not query:
                self.logger.error("delete_company_finance requires code or report_date.")
                return False
            return self.mongo_exec.delete(table=self.company_finance_table, condition=query)
        except Exception as e:
            self.logger.error(f"删除公司财务信息失败: {e}")
            return False
