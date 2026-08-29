from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.getcwd())

from db.mongo.mongo_company_finance_impl import MongoCompanyFinanceImpl

TEST_CODE_PREFIX = "test_q_share_cf_"


def _test_code(base: str) -> str:
    return f"sh6000{base}" if base.isdigit() else f"sh6000{base[:2]}"


class TestMongoCompanyFinanceImpl(unittest.TestCase):
    """MongoCompanyFinanceImpl 单元测试，需要 MongoDB 可用。"""

    def setUp(self) -> None:
        super().setUp()
        self.impl = MongoCompanyFinanceImpl()
        self._test_codes: list[str] = []
        self._test_dates: list[str] = []

    def tearDown(self) -> None:
        for code, report_date in zip(self._test_codes, self._test_dates):
            self.impl.delete_company_finance(code=code, report_date=report_date)
        super().tearDown()

    def test_insert_or_update_company_finance_insert(self) -> None:
        code = _test_code("01")
        report_date = "2024-12-31"
        self._test_codes.append(code)
        self._test_dates.append(report_date)

        data = {
            "code": code,
            "name": "测试公司",
            "total_revenue": 1000000.0,
            "operating_cost": 500000.0,
            "net_profit": 200000.0,
            "net_profit_parent": 180000.0,
            "net_profit_excl_nonrecurring": 170000.0,
            "net_profit_growth_rate": 0.15,
            "total_revenue_growth_rate": 0.1,
            "total_market_cap": 500000000.0,
            "flow_market_cap": 300000000.0,
            "industry": "测试行业",
            "concept": "测试概念",
            "list_date": "2020-01-01",
            "report_date": report_date,
        }

        ok, inserted_id = self.impl.insert_or_update_company_finance(data)

        self.assertTrue(ok)
        self.assertIsNotNone(inserted_id)

    def test_insert_or_update_company_finance_update(self) -> None:
        code = _test_code("02")
        report_date = "2024-06-30"
        self._test_codes.append(code)
        self._test_dates.append(report_date)

        data = {
            "code": code,
            "name": "测试公司",
            "net_profit": 250000.0,
            "report_date": report_date,
        }
        self.impl.insert_or_update_company_finance(data)

        data["net_profit"] = 300000.0
        ok, inserted_id = self.impl.insert_or_update_company_finance(data)

        self.assertTrue(ok)
        self.assertIsNone(inserted_id)

        ok, results = self.impl.query_company_finance(code=code, report_date=report_date)
        self.assertTrue(ok)
        self.assertIsNotNone(results)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["net_profit"], 300000.0)

    def test_query_company_finance_not_found(self) -> None:
        ok, results = self.impl.query_company_finance(code="sh999999", report_date="2099-12-31")
        self.assertTrue(ok)
        self.assertIsNone(results)

    def test_bulk_upsert_company_finance(self) -> None:
        records = []
        for i in range(3):
            code = _test_code(str(10 + i))
            report_date = "2025-03-31"
            self._test_codes.append(code)
            self._test_dates.append(report_date)
            records.append(
                {
                    "code": code,
                    "name": f"测试公司{i}",
                    "net_profit": 100000.0 + i * 1000,
                    "report_date": report_date,
                }
            )

        ok = self.impl.bulk_upsert_company_finance(records)
        self.assertTrue(ok)

        for record in records:
            ok, results = self.impl.query_company_finance(code=record["code"], report_date=record["report_date"])
            self.assertTrue(ok)
            self.assertIsNotNone(results)
            self.assertEqual(len(results), 1)

    def test_delete_company_finance(self) -> None:
        code = _test_code("20")
        report_date = "2023-09-30"
        data = {
            "code": code,
            "name": "测试公司删除",
            "net_profit": 12345.0,
            "report_date": report_date,
        }
        self.impl.insert_or_update_company_finance(data)

        deleted = self.impl.delete_company_finance(code=code, report_date=report_date)
        self.assertTrue(deleted)

        ok, results = self.impl.query_company_finance(code=code, report_date=report_date)
        self.assertTrue(ok)
        self.assertIsNone(results)

    def test_query_all_company_finance(self) -> None:
        code = _test_code("30")
        report_date = "2025-09-30"
        self._test_codes.append(code)
        self._test_dates.append(report_date)
        self.impl.insert_or_update_company_finance(
            {
                "code": code,
                "name": "测试公司全量",
                "net_profit": 22222.0,
                "report_date": report_date,
            }
        )

        ok, results = self.impl.query_all_company_finance()
        self.assertTrue(ok)
        self.assertIsNotNone(results)
        self.assertGreaterEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
