"""
Author: liguoqiang
Date: 2026-08-02
Description: 低频波段策略测试 —— 使用真实 API 行情数据（日K / 分钟字段映射）。
    服务端分钟接口当前返回空，故分钟相关用例使用真实日K数据映射为分钟字段。
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from app_context import AppContext
from strategy.swing_trading_strategy import SwingTradingStrategy
from utils.tools import load_admin_token


def setUpModule() -> None:
    AppContext().api_client.set_fallback_token(load_admin_token())


def fetch_daily(code: str = "000001") -> list[dict[str, Any]]:
    return AppContext().market_api.get_day_kline(
        code=code, start="2026-03-01", end="2026-07-31"
    )


class TestSwingTradingStrategy(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data: list[dict[str, Any]] = fetch_daily("000001")
        cls.strategy = SwingTradingStrategy()

    def setUp(self) -> None:
        self.strategy._matched_support.clear()
        self.strategy._matched_resistances.clear()

    def test_is_match_strategy_with_real_daily_data(self) -> None:
        self.assertGreaterEqual(len(self.data), 15)
        matched, extra = self.strategy.is_match_strategy(self.data)
        self.assertIsInstance(matched, bool)
        self.assertIsInstance(extra, dict)
        if matched:
            self.assertEqual(extra.get("signal"), "BUY")
        else:
            self.assertIn("reason", extra)

    def test_is_match_strategy_short_data_no_crash(self) -> None:
        """KlineSRFactor 数据不足时返回空 dict 结构，不再抛 AttributeError。"""
        matched, extra = self.strategy.is_match_strategy(self.data[:10])
        self.assertIsInstance(matched, bool)
        self.assertIn("reason", extra)

    def test_before_trading_clears_caches(self) -> None:
        self.strategy._matched_support["000001"] = 10.0
        self.strategy._matched_resistances["000001"] = [11.0]
        self.strategy.before_trading("2026-07-16", ["000001"])
        self.assertEqual(self.strategy._matched_support, {})
        self.assertEqual(self.strategy._matched_resistances, {})

    def test_check_sell_conditions_with_matched_support(self) -> None:
        """14:40 后跌破支撑位触发卖出（使用真实行情数值）。"""
        last = self.data[-1]
        support = float(last["close"]) * 1.05  # 支撑位高于现价，视为已跌破
        self.strategy._matched_support["000001"] = support
        position = {"cost_price": 10.0, "buy_time": "2026-07-01 10:00:00"}
        bar = {
            "code": "000001",
            "price": float(last["close"]),
            "open": float(last["open"]),
            "preclose": float(self.data[-2]["close"]),
            "low": float(last["low"]),
            "high": float(last["high"]),
            "create_time": "2026-07-15 14:45:00",
        }
        matched, extra = self.strategy._check_sell_conditions(
            "000001", position, bar
        )
        self.assertTrue(matched)
        self.assertEqual(extra.get("signal"), "SELL")

    def test_check_buy_conditions_with_real_bar(self) -> None:
        """真实数据下买入检查不抛异常。"""
        last = self.data[-1]
        self.strategy._matched_support["000001"] = float(last["low"])
        bar = {
            "code": "000001",
            "price": float(last["close"]),
            "open": float(last["open"]),
            "preclose": float(self.data[-2]["close"]),
            "low": float(last["low"]),
            "high": float(last["high"]),
            "create_time": "2026-07-15 10:30:00",
        }
        matched, extra = self.strategy._check_buy_conditions("000001", bar)
        self.assertIsInstance(matched, bool)
        self.assertIsInstance(extra, dict)

    def test_handle_minute_bar_routes_by_select_list(self) -> None:
        self.strategy._load_select_stocks = MagicMock(return_value={"000001"})
        self.strategy.check_minute_buy = MagicMock(
            return_value=(True, {"signal": "BUY", "current_price": 10.0})
        )
        bar = dict(self.data[-1])
        matched, extra = self.strategy.handle_minute_bar("000001", {}, bar)
        self.assertTrue(matched)

        self.strategy._load_select_stocks = MagicMock(return_value=set())
        self.strategy.check_minute_sell = MagicMock(
            return_value=(False, {"reason": "no"})
        )
        matched, extra = self.strategy.handle_minute_bar("000001", {}, bar)
        self.assertFalse(matched)
        self.strategy.check_minute_sell.assert_called_once()

    def test_check_tick_buy_and_sell_disabled(self) -> None:
        self.assertEqual(self.strategy.check_tick_buy("000001", {}), (False, {}))
        self.assertEqual(
            self.strategy.check_tick_sell("000001", {}, {}), (False, {})
        )

    def test_init_factors_registers_kline_sr(self) -> None:
        from factor.kline_support_resistance import KlineSRFactor

        self.assertIsNotNone(
            self.strategy.factor_manager.get(KlineSRFactor.factor_name)
        )

    # ---- 分支覆盖 ----

    def _make_rows(
        self,
        n: int = 16,
        close: float = 10.0,
        low: float = 9.9,
        code: str = "000001",
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for i in range(n):
            rows.append(
                {
                    "code": code,
                    "open": close,
                    "close": close,
                    "high": close * 1.01,
                    "low": low,
                    "volume": 100000 + i,
                    "amount": 1_000_000.0,
                    "create_time": f"2026-07-{1 + i:02d} 00:00:00",
                }
            )
        return rows

    def test_is_match_strategy_too_short(self) -> None:
        matched, extra = self.strategy.is_match_strategy(self._make_rows(n=3))
        self.assertFalse(matched)
        self.assertIn("数据不足", extra["reason"])

    def test_is_match_strategy_no_code(self) -> None:
        rows = self._make_rows()
        for row in rows:
            row.pop("code", None)
        matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertIn("无法获取股票代码", extra["reason"])

    def test_is_match_strategy_industry_query_error(self) -> None:
        rows = self._make_rows()
        with patch("app_context.AppContext") as mock_ctx:
            mock_ctx.return_value.stock_info_api.get_by_codes.side_effect = Exception(
                "api down"
            )
            matched, extra = self.strategy.is_match_strategy(rows)
        self.assertIsInstance(matched, bool)

    def test_is_match_strategy_pe_check_fails(self) -> None:
        rows = self._make_rows()
        with patch("strategy.swing_trading_strategy.AppContext") as mock_ctx:
            mock_ctx.return_value.stock_info_api.get_by_codes.return_value = [
                {"industry": "半导体"}
            ]
            with patch.object(
                self.strategy,
                "_match_pe_profit",
                return_value=(False, {"reason": "市盈率过高"}),
            ):
                matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertEqual(extra["reason"], "市盈率过高")

    def test_is_match_strategy_sr_factor_missing(self) -> None:
        from factor.kline_support_resistance import KlineSRFactor

        rows = self._make_rows()
        original_get = self.strategy.factor_manager.get

        def fake_get(name):
            if name == KlineSRFactor.factor_name:
                return None
            return original_get(name)

        with patch("app_context.AppContext") as mock_ctx:
            mock_ctx.return_value.stock_info_api.get_by_codes.return_value = [
                {"industry": "金融业"}
            ]
            with patch.object(
                self.strategy.factor_manager, "get", side_effect=fake_get
            ):
                matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertIn("支撑阻力因子未注册", extra["reason"])

    def test_is_match_strategy_no_support_nearby(self) -> None:
        rows = self._make_rows(low=10.0)
        fake_factor = MagicMock()
        fake_factor.calculate.return_value = {
            "support": [5.0],
            "resistance": [15.0],
        }
        with patch("app_context.AppContext") as mock_ctx:
            mock_ctx.return_value.stock_info_api.get_by_codes.return_value = [
                {"industry": "金融业"}
            ]
            with patch.object(
                self.strategy.factor_manager, "get", return_value=fake_factor
            ):
                matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertIn("不在支撑位", extra["reason"])

    def test_is_match_strategy_short_lower_shadow(self) -> None:
        rows = self._make_rows(low=10.0)
        fake_factor = MagicMock()
        fake_factor.calculate.return_value = {
            "support": [9.9],
            "resistance": [15.0],
        }
        with patch("app_context.AppContext") as mock_ctx:
            mock_ctx.return_value.stock_info_api.get_by_codes.return_value = [
                {"industry": "金融业"}
            ]
            with patch.object(
                self.strategy.factor_manager, "get", return_value=fake_factor
            ):
                matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertIn("下影线不够长", extra["reason"])

    def test_is_match_strategy_full_match(self) -> None:
        """全部条件满足：支撑位接近昨日最低价且下影线足够长。"""
        rows = self._make_rows(low=10.0)
        rows[-1]["close"] = 10.5
        rows[-1]["low"] = 10.0
        rows[-1]["high"] = 10.5
        fake_factor = MagicMock()
        fake_factor.calculate.return_value = {
            "support": [9.95],
            "resistance": [15.0],
        }
        with patch("app_context.AppContext") as mock_ctx:
            mock_ctx.return_value.stock_info_api.get_by_codes.return_value = [
                {"industry": "金融业"}
            ]
            with patch.object(
                self.strategy.factor_manager, "get", return_value=fake_factor
            ):
                matched, extra = self.strategy.is_match_strategy(rows)
        self.assertTrue(matched, extra)
        self.assertEqual(extra.get("signal"), "BUY")
        self.assertIn("000001", self.strategy._matched_support)

    def test_handle_tick_bar_routes(self) -> None:
        self.strategy._load_select_stocks = MagicMock(return_value={"000001"})
        self.strategy.check_tick_buy = MagicMock(return_value=(True, {"signal": "BUY"}))
        matched, extra = self.strategy.handle_tick_bar("000001", {}, {})
        self.assertTrue(matched)

        self.strategy._load_select_stocks = MagicMock(return_value=set())
        self.strategy.check_tick_sell = MagicMock(
            return_value=(False, {"reason": "no"})
        )
        matched, extra = self.strategy.handle_tick_bar("000001", {}, {})
        self.assertFalse(matched)

    def test_check_minute_buy_sell_wrappers(self) -> None:
        bar = {
            "code": "000001",
            "price": 10.0,
            "open": 10.0,
            "preclose": 10.0,
            "low": 9.9,
            "high": 10.1,
            "create_time": "2026-07-15 09:30:00",
        }
        matched, extra = self.strategy.check_minute_buy("000001", bar)
        self.assertIsInstance(matched, bool)
        matched, extra = self.strategy.check_minute_sell(
            "000001", {"cost_price": 10.0}, bar
        )
        self.assertIsInstance(matched, bool)

    def test_check_buy_conditions_branches(self) -> None:
        cases = [
            # (bar, reason片段)
            ({"price": 0, "open": 10, "preclose": 10, "create_time": "2026-07-15 10:30:00"}, "当前价格异常"),
            ({"price": 10, "open": 9.8, "preclose": 10, "create_time": "2026-07-15 10:30:00"}, "低开过大"),
            ({"price": 10, "open": 10, "preclose": 10, "create_time": "bad"}, "无法解析分钟时间"),
        ]
        for bar, fragment in cases:
            with self.subTest(fragment=fragment):
                matched, extra = self.strategy._check_buy_conditions("000001", bar)
                self.assertFalse(matched)
                self.assertIn(fragment, extra["reason"])

        # 未找到匹配支撑位
        self.strategy._matched_support.clear()
        matched, extra = self.strategy._check_buy_conditions(
            "000001",
            {"price": 10, "open": 10, "preclose": 10, "create_time": "2026-07-15 10:30:00"},
        )
        self.assertFalse(matched)
        self.assertIn("未找到股票", extra["reason"])

        # 10点前严重跌破支撑位
        self.strategy._matched_support["000001"] = 10.0
        matched, extra = self.strategy._check_buy_conditions(
            "000001",
            {"price": 9.5, "open": 10, "preclose": 10, "create_time": "2026-07-15 09:40:00"},
        )
        self.assertFalse(matched)
        self.assertIn("严重跌破支撑位", extra["reason"])

        # 15:45（14:40 之后）下影线不够长 → 买入条件3不满足
        matched, extra = self.strategy._check_buy_conditions(
            "000001",
            {"price": 10.0, "open": 10.0, "preclose": 10.0, "low": 9.95,
             "create_time": "2026-07-15 15:45:00"},
        )
        self.assertFalse(matched)
        self.assertIn("下影线不够长", extra["reason"])

    def test_check_sell_conditions_branches(self) -> None:
        # 价格异常
        matched, extra = self.strategy._check_sell_conditions(
            "000001", {"cost_price": 10.0}, {"price": 0, "create_time": "2026-07-15 10:00:00"}
        )
        self.assertFalse(matched)
        self.assertIn("当前价格异常", extra["reason"])

        # 接近阻力位触发条件1
        self.strategy._matched_resistances["000001"] = [10.0]
        matched, extra = self.strategy._check_sell_conditions(
            "000001",
            {"cost_price": 9.0},
            {"price": 10.05, "create_time": "2026-07-15 10:00:00"},
        )
        self.assertTrue(matched)
        self.assertEqual(extra.get("signal"), "SELL")

        # 无触发 → False
        self.strategy._matched_resistances["000001"] = [15.0]
        self.strategy._matched_support["000001"] = 5.0
        matched, extra = self.strategy._check_sell_conditions(
            "000001",
            {"cost_price": 9.0},
            {"price": 10.05, "create_time": "2026-07-15 10:00:00"},
        )
        self.assertFalse(matched)

        # 异常路径
        with patch.object(
            self.strategy, "_parse_time", side_effect=RuntimeError("boom")
        ):
            matched, extra = self.strategy._check_sell_conditions(
                "000001", {"cost_price": 9.0}, {"price": 10.0}
            )
        self.assertFalse(matched)
        self.assertIn("检查异常", extra["reason"])


if __name__ == "__main__":
    unittest.main()
