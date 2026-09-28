"""
Description: 用户策略 DAO 默认值契约测试（2026-09-28）。

`max_stock_count` / `initial_amount` 的「默认」与「显式 0」是两个**语义相反**的值：

- 默认 = 不限制持仓（-1）+ 初始资金 10 万 ⇒ 策略可以下单
- 显式 0 = 限制买入 ⇒ 策略必须不下单（只卖不买）

两者混在一起不会抛任何异常，只会在行为上相反：把 0 当成「未配置」会让限制静默失效，
把「未配置」当成 0 会让新策略永远下不了第一单。因此这里用矩阵把口径固定下来，
重点覆盖「解析函数不能用 `or` 兜底」这一条。
"""

from __future__ import annotations

import unittest

from dao.user_strategy_dao import (
    DEFAULT_INITIAL_AMOUNT,
    DEFAULT_MAX_STOCK_COUNT,
    UserStrategyDao,
    parse_initial_amount,
    parse_max_stock_count,
)


class TestDefaultConstants(unittest.TestCase):
    def test_default_values(self) -> None:
        """默认值为「不限制持仓 + 10 万初始资金」，与服务端模型默认值一致。"""
        self.assertEqual(DEFAULT_MAX_STOCK_COUNT, -1)
        self.assertEqual(DEFAULT_INITIAL_AMOUNT, 100000.0)
        # -1 留给「不限制」，0 留给「限制买入」，两者不能混用
        self.assertLess(DEFAULT_MAX_STOCK_COUNT, 0)


class TestParseInitialAmount(unittest.TestCase):
    def test_missing_or_invalid_falls_back(self) -> None:
        for raw in (None, "", "abc", {}, []):
            self.assertEqual(
                parse_initial_amount(raw), DEFAULT_INITIAL_AMOUNT, f"raw={raw!r}"
            )

    def test_explicit_zero_kept(self) -> None:
        """显式 0 = 限制买入，必须原样保留（`raw or DEFAULT` 会把它洗成 10 万）。"""
        self.assertEqual(parse_initial_amount(0), 0.0)
        self.assertEqual(parse_initial_amount(0.0), 0.0)
        self.assertEqual(parse_initial_amount("0"), 0.0)

    def test_numbers_preserved(self) -> None:
        self.assertEqual(parse_initial_amount(100000), 100000.0)
        self.assertEqual(parse_initial_amount("50000"), 50000.0)


class TestParseMaxStockCount(unittest.TestCase):
    def test_missing_or_invalid_falls_back(self) -> None:
        for raw in (None, "", "abc"):
            self.assertEqual(parse_max_stock_count(raw), DEFAULT_MAX_STOCK_COUNT)

    def test_explicit_zero_kept(self) -> None:
        self.assertEqual(parse_max_stock_count(0), 0)
        self.assertEqual(parse_max_stock_count("0"), 0)

    def test_explicit_values_preserved(self) -> None:
        self.assertEqual(parse_max_stock_count(-1), -1)
        self.assertEqual(parse_max_stock_count(4), 4)
        self.assertEqual(parse_max_stock_count("6"), 6)


class TestUserStrategyDaoDefaults(unittest.TestCase):
    def test_constructor_defaults(self) -> None:
        dao = UserStrategyDao()
        self.assertEqual(dao.initial_amount, DEFAULT_INITIAL_AMOUNT)
        self.assertEqual(dao.max_stock_count, DEFAULT_MAX_STOCK_COUNT)

    def test_constructor_explicit_zero_kept(self) -> None:
        dao = UserStrategyDao(initial_amount=0, max_stock_count=0)
        self.assertEqual(dao.initial_amount, 0.0)
        self.assertEqual(dao.max_stock_count, 0)

    def test_from_db_missing_fields(self) -> None:
        """服务端未返这两个字段 ⇒ 默认值，且标记为「未显式配置」。"""
        dao = UserStrategyDao()
        dao.from_db({"_id": "x"})
        self.assertEqual(dao.initial_amount, DEFAULT_INITIAL_AMOUNT)
        self.assertEqual(dao.max_stock_count, DEFAULT_MAX_STOCK_COUNT)
        self.assertFalse(dao.initial_amount_configured)

    def test_from_db_empty_string_treated_as_missing(self) -> None:
        dao = UserStrategyDao()
        dao.from_db({"_id": "x", "initial_amount": "", "max_stock_count": ""})
        self.assertEqual(dao.initial_amount, DEFAULT_INITIAL_AMOUNT)
        self.assertEqual(dao.max_stock_count, DEFAULT_MAX_STOCK_COUNT)
        self.assertFalse(dao.initial_amount_configured)

    def test_from_db_explicit_zero_kept(self) -> None:
        dao = UserStrategyDao()
        dao.from_db({"_id": "x", "initial_amount": 0, "max_stock_count": 0})
        self.assertEqual(dao.initial_amount, 0.0)
        self.assertEqual(dao.max_stock_count, 0)
        self.assertTrue(dao.initial_amount_configured)

    def test_from_db_values_preserved(self) -> None:
        dao = UserStrategyDao()
        dao.from_db({"_id": "x", "initial_amount": 80000, "max_stock_count": -1})
        self.assertEqual(dao.initial_amount, 80000.0)
        self.assertEqual(dao.max_stock_count, -1)
        self.assertTrue(dao.initial_amount_configured)


if __name__ == "__main__":
    unittest.main()
