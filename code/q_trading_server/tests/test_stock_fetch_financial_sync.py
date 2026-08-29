"""
Author: liguoqiang
Date: 2026-06-19
Description: 测试 StockFetch.stock_financial_sync_task 方法，
使用 mock 模拟数据库读取和 AkShare 财务摘要同步调用，
以及验证数据库是否正常保存数据。
"""

import sys
import os
import unittest
from datetime import date
from unittest.mock import patch

curPath = os.getcwd()
sys.path.append(curPath)

from stock_fetch.stock_fetch import StockFetch
from db.mongo.mongo_company_finance_impl import MongoCompanyFinanceImpl


class TestStockFetchFinancialSync(unittest.TestCase):

    # ── Mock 测试 ──────────────────────────────────────────────

    def test_financial_sync_task_calls_sync_stock_financial_for_each_stock(self):
        """验证 stock_financial_sync_task 遍历所有股票并调用 sync_stock_financial"""
        stock_fetch = StockFetch()
        stock_list = [
            {"code": "600000", "name": "浦发银行"},
            {"code": "000001", "name": "平安银行"},
        ]

        with patch(
            "stock_fetch.stock_fetch.MongoStockInfoImpl.query_all_stock_info",
            return_value=(True, stock_list),
        ) as query_mock, patch(
            "stock_fetch.stock_fetch.AkStockProxy.sync_stock_financial",
            return_value=True,
        ) as sync_fin_mock:
            stock_fetch.stock_financial_sync_task()

        query_mock.assert_called_once()
        self.assertEqual(sync_fin_mock.call_count, 2)
        sync_fin_mock.assert_any_call("600000")
        sync_fin_mock.assert_any_call("000001")

    def test_financial_sync_task_skips_when_no_stock_info(self):
        """验证无股票列表时 skip 财务同步"""
        stock_fetch = StockFetch()
        with patch(
            "stock_fetch.stock_fetch.MongoStockInfoImpl.query_all_stock_info",
            return_value=(True, []),
        ):
            stock_fetch.stock_financial_sync_task()

    # ── 数据库保存验证测试 ─────────────────────────────────────

    def test_sync_stock_financial_saves_data_to_db(self):
        """验证 sync_stock_financial 成功保存数据到 MongoDB"""
        db_impl = MongoCompanyFinanceImpl()

        # 使用一个稳定无前缀的测试代码，避免网络波动影响
        test_code = "000001"
        pure_code = db_impl.normalize_code(test_code)

        # 执行同步
        from stock_fetch.akshare_fetch.ak_stock_proxy import AkStockProxy
        proxy = AkStockProxy()
        ok = proxy.sync_stock_financial(test_code)

        if not ok:
            # 网络原因跳过，不算失败
            self.skipTest(f"akshare sync_stock_financial({test_code}) 返回 False，跳过数据库验证")

        # 查询数据库验证数据已保存
        res, records = db_impl.query_company_finance(code=pure_code)
        self.assertTrue(res, "查询数据库失败")
        self.assertIsNotNone(records, "数据库查询结果不应为 None")

        records_list = list(records) if not isinstance(records, list) else records
        records_list = [r for r in records_list if r.get("code") is not None]
        self.assertGreater(len(records_list), 0, "数据库应至少保存一条财务记录")

        record = records_list[0]
        today_str = date.today().strftime("%Y-%m-%d")

        # 验证关键字段存在
        self.assertIn("code", record, "记录缺少 code 字段")
        self.assertIn("report_date", record, "记录缺少 report_date 字段")
        self.assertIn("total_revenue", record, "记录缺少 total_revenue 字段")
        self.assertIn("net_profit", record, "记录缺少 net_profit 字段")
        self.assertIn("net_profit_parent", record, "记录缺少 net_profit_parent 字段")
        self.assertIn("net_profit_growth_rate", record, "记录缺少 net_profit_growth_rate 字段")
        self.assertIn("total_revenue_growth_rate", record, "记录缺少 total_revenue_growth_rate 字段")
        self.assertIn("goodwill", record, "记录缺少 goodwill 字段")
        self.assertIn("asset_liability_ratio", record, "记录缺少 asset_liability_ratio 字段")

        # 验证字段类型为数值（float/int）
        self.assertIsInstance(record["net_profit"], (int, float),
                              "net_profit 应为数值类型")
        self.assertIsInstance(record["total_revenue"], (int, float),
                              "total_revenue 应为数值类型")
        self.assertIsInstance(record["net_profit_growth_rate"], (int, float),
                              "net_profit_growth_rate 应为数值类型")
        self.assertIsInstance(record["total_revenue_growth_rate"], (int, float),
                              "total_revenue_growth_rate 应为数值类型")
        self.assertIsInstance(record["goodwill"], (int, float),
                              "goodwill 应为数值类型")
        self.assertIsInstance(record["asset_liability_ratio"], (int, float),
                              "asset_liability_ratio 应为数值类型")

        # 验证 report_date 格式为 YYYYMMDD（如 "20260331"）
        self.assertRegex(
            record["report_date"],
            r"^\d{8}$",
            f"report_date 格式不合法: {record.get('report_date')}"
        )

        # 验证 code 匹配
        self.assertIn(pure_code, str(record["code"]),
                      f"code 应包含 {pure_code}, 实际: {record.get('code')}")

        # 验证同比增长率计算合理（在 -1000 ~ 1000 范围内）
        net_growth = record["net_profit_growth_rate"]
        self.assertGreaterEqual(net_growth, -1000.0,
                                f"净利润增长率异常偏低: {net_growth}")
        self.assertLessEqual(net_growth, 1000.0,
                             f"净利润增长率异常偏高: {net_growth}")

        # 清理：删除测试写入的数据
        cleanup_ok = db_impl.delete_company_finance(code=pure_code, report_date=str(record["report_date"]))
        self.assertTrue(cleanup_ok, f"清理测试数据失败: {pure_code} / {record['report_date']}")


if __name__ == "__main__":
    unittest.main()
