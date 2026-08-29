# coding="utf8"

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Hashable

from pymongo import UpdateOne
from app_context import AppContext


class MongoCompanyValuationImpl:
    """公司估值表 MongoDB 操作实现。

    提供插入/更新、批量 upsert、查询与删除接口，接口风格与
    `MongoCompanyFinanceImpl` 保持一致以便调用方统一使用。
    """

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.company_valuation_table = self.mongo_exec.db["company_valuation_tbl"]

    def normalize_code(self, code: str) -> str:
        """提取股票代码中的数字部分，兼容 sh/sz 前缀和后缀。"""
        match = re.search(r"\d+", code)
        return match.group() if match else code

    def insert_or_update_company_valuation(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """插入或更新公司估值数据。

        要求 `code` 与 `report_date` 至少包含一项用于定位记录（通常两者都需要）。
        返回 (ok, upserted_id_or_None)
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
            result = self.company_valuation_table.update_one(filter, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None
        except Exception as e:
            self.logger.error(f"插入或更新公司估值信息失败: {e}")
            return False, None

    def bulk_upsert_company_valuation(self, records: Sequence[Mapping[Hashable, Any]]) -> bool:
        """批量插入或更新公司估值数据。"""
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
            result = self.company_valuation_table.bulk_write(requests, ordered=False)
            self.logger.info(
                "批量upsert公司估值: matched=%d, modified=%d, upserted=%d",
                result.matched_count, result.modified_count, result.upserted_count,
            )
            return result.acknowledged
        except Exception as e:
            self.logger.error(f"批量插入或更新公司估值信息失败: {e}")
            return False

    def query_company_valuation(
        self,
        code: str,
        report_date: str = "",
        skip: int = 0,
        limit: int = 0,
    ) -> tuple[bool, Any | None]:
        """按股票代码和可选报表日期查询公司估值数据。"""
        try:
            pure_code = self.normalize_code(code)
            query: dict[str, Any] = {
                "code": {"$regex": f"^(sh|sz)?{pure_code}(\\.(SH|SZ))?$"}
            }
            if report_date:
                query["report_date"] = report_date
            return self.mongo_exec.query_by_condition(
                table=self.company_valuation_table,
                condition=query,
                sort={"report_date": 1},
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            self.logger.error(f"查询公司估值信息失败: {e}")
            return False, None

    def query_all_company_valuation(self, skip: int = 0, limit: int = 0) -> tuple[bool, Any | None]:
        """查询所有公司估值数据。"""
        try:
            return self.mongo_exec.query_by_condition(
                table=self.company_valuation_table,
                condition={},
                sort={"code": 1, "report_date": 1},
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            self.logger.error(f"查询所有公司估值信息失败: {e}")
            return False, None

    def query_valuation_by_ranges(
        self,
        ttm_min: float = 0.0,
        ttm_max: float = 0.0,
        cap_min: float = 0.0,
        cap_max: float = 0.0,
        skip: int = 0,
        limit: int = 0,
    ) -> tuple[bool, Any | None]:
        """按 TTM 市盈率和总市值范围查询公司估值数据。

        仅查询各股票最新一条 report_date 的估值记录。

        :param ttm_min: TTM 市盈率下限（含），0 表示不限制
        :param ttm_max: TTM 市盈率上限（含），0 表示不限制
        :param cap_min: 总市值下限（含），0 表示不限制
        :param cap_max: 总市值上限（含），0 表示不限制
        :param skip: 分页跳过条数
        :param limit: 分页限制条数，0 表示不限制
        :return: (ok, records_or_None)
        """
        try:
            match: dict[str, Any] = {}
            if ttm_min > 0 or ttm_max > 0:
                ttm_filter: dict[str, Any] = {}
                if ttm_min > 0:
                    ttm_filter["$gte"] = ttm_min
                if ttm_max > 0:
                    ttm_filter["$lte"] = ttm_max
                match["ttm_pe"] = ttm_filter
            if cap_min > 0 or cap_max > 0:
                cap_filter: dict[str, Any] = {}
                if cap_min > 0:
                    cap_filter["$gte"] = cap_min
                if cap_max > 0:
                    cap_filter["$lte"] = cap_max
                match["total_market_cap"] = cap_filter

            pipeline: list[dict[str, Any]] = [
                {"$sort": {"code": 1, "report_date": -1}},
                {
                    "$group": {
                        "_id": "$code",
                        "doc": {"$first": "$$ROOT"},
                    }
                },
                {"$replaceRoot": {"newRoot": "$doc"}},
            ]
            if match:
                pipeline.append({"$match": match})
            pipeline.append({"$sort": {"code": 1}})

            facet_stage: dict[str, Any] = {
                "$facet": {
                    "records": [],
                    "total_count": [{"$count": "count"}],
                }
            }
            if skip > 0:
                facet_stage["$facet"]["records"].append({"$skip": skip})
            if limit > 0:
                facet_stage["$facet"]["records"].append({"$limit": limit})
            else:
                pass  # 不限制时无需 $limit

            pipeline.append(facet_stage)

            cursor = self.company_valuation_table.aggregate(pipeline)
            results = list(cursor)
            if not results:
                return True, []
            facet_result = results[0]
            records = facet_result.get("records", [])
            return True, records
        except Exception as e:
            self.logger.error(f"按范围查询公司估值信息失败: {e}")
            return False, None

    def delete_company_valuation(self, code: str = "", report_date: str = "") -> bool:
        """按股票代码或报表日期删除公司估值数据。"""
        try:
            query: dict[str, Any] = {}
            if code:
                pure_code = self.normalize_code(code)
                query["code"] = {"$regex": f"^(sh|sz)?{pure_code}(\\.(SH|SZ))?$"}
            if report_date:
                query["report_date"] = report_date
            if not query:
                self.logger.error("delete_company_valuation requires code or report_date.")
                return False
            return self.mongo_exec.delete(table=self.company_valuation_table, condition=query)
        except Exception as e:
            self.logger.error(f"删除公司估值信息失败: {e}")
            return False

    def query_codes_by_cap_range(
        self,
        cap_min: float = 0.0,
        cap_max: float = 0.0,
        skip: int = 0,
        limit: int = 0,
    ) -> tuple[bool, Any | None]:
        """按总市值范围查询股票代码列表（仅返回各股票最新一条估值记录）

        :param cap_min: 总市值下限（含），0 表示不限制
        :param cap_max: 总市值上限（含），0 表示不限制
        :param skip: 分页跳过条数
        :param limit: 分页限制条数，0 表示不限制
        :return: (ok, records_or_None)
        """
        try:
            match: dict[str, Any] = {}
            if cap_min > 0 or cap_max > 0:
                cap_filter: dict[str, Any] = {}
                if cap_min > 0:
                    cap_filter["$gte"] = cap_min
                if cap_max > 0:
                    cap_filter["$lte"] = cap_max
                match["total_market_cap"] = cap_filter

            pipeline: list[dict[str, Any]] = [
                {"$sort": {"code": 1, "report_date": -1}},
                {
                    "$group": {
                        "_id": "$code",
                        "doc": {"$first": "$$ROOT"},
                    }
                },
                {"$replaceRoot": {"newRoot": "$doc"}},
            ]
            if match:
                pipeline.append({"$match": match})
            pipeline.append({"$sort": {"code": 1}})
            if skip > 0:
                pipeline.append({"$skip": skip})
            if limit > 0:
                pipeline.append({"$limit": limit})

            cursor = self.company_valuation_table.aggregate(pipeline)
            return True, list(cursor)
        except Exception as e:
            self.logger.error(f"按市值范围查询代码失败: {e}")
            return False, None

    def query_codes_by_ttm_range(
        self,
        ttm_min: float = 0.0,
        ttm_max: float = 0.0,
        skip: int = 0,
        limit: int = 0,
    ) -> tuple[bool, Any | None]:
        """按 TTM 市盈率范围查询股票代码列表（仅返回各股票最新一条估值记录）

        :param ttm_min: TTM 市盈率下限（含），0 表示不限制
        :param ttm_max: TTM 市盈率上限（含），0 表示不限制
        :param skip: 分页跳过条数
        :param limit: 分页限制条数，0 表示不限制
        :return: (ok, records_or_None)
        """
        try:
            match: dict[str, Any] = {}
            if ttm_min > 0 or ttm_max > 0:
                ttm_filter: dict[str, Any] = {}
                if ttm_min > 0:
                    ttm_filter["$gte"] = ttm_min
                if ttm_max > 0:
                    ttm_filter["$lte"] = ttm_max
                match["ttm_pe"] = ttm_filter

            pipeline: list[dict[str, Any]] = [
                {"$sort": {"code": 1, "report_date": -1}},
                {
                    "$group": {
                        "_id": "$code",
                        "doc": {"$first": "$$ROOT"},
                    }
                },
                {"$replaceRoot": {"newRoot": "$doc"}},
            ]
            if match:
                pipeline.append({"$match": match})
            pipeline.append({"$sort": {"code": 1}})
            if skip > 0:
                pipeline.append({"$skip": skip})
            if limit > 0:
                pipeline.append({"$limit": limit})

            cursor = self.company_valuation_table.aggregate(pipeline)
            return True, list(cursor)
        except Exception as e:
            self.logger.error(f"按 TTM 市盈率范围查询代码失败: {e}")
            return False, None

    def delete_company_valuation_by_date(self, report_date: str = "") -> bool:
        """按刪除<=报表日期公司估值数据。"""
        try:
            query: dict[str, Any] = {}
            if report_date:
                query["report_date"] = {"$lte": report_date}
            if not query:
                self.logger.error("delete_company_valuation_by_date requires report_date.")
                return False
            return self.mongo_exec.delete(table=self.company_valuation_table, condition=query)
        except Exception as e:
            self.logger.error(f"删除公司估值信息失败: {e}")
            return False