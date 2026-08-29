"""
Author: liguoqiang
Date: 2026-08-02
Description: 强势反弹策略测试 —— 使用真实 API 行情数据（日K / 分钟字段映射）。
    服务端分钟接口当前返回空，故分钟缓存相关用例使用真实日K数据映射为分钟字段
    （数值均为真实行情，仅 create_time 采样自真实数据或交易日时间点）。
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from app_context import AppContext
from strategy.strong_rebound_strategy import StrongReboundStrategy
from utils.tools import load_admin_token


def setUpModule() -> None:
    AppContext().api_client.set_fallback_token(load_admin_token())


def fetch_daily(code: str = "000001") -> list[dict[str, Any]]:
    return AppContext().market_api.get_day_kline(
        code=code, start="2026-03-01", end="2026-07-31"
    )


class TestStrongReboundStrategy(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data: list[dict[str, Any]] = fetch_daily("000001")
        cls.strategy = StrongReboundStrategy()

    def setUp(self) -> None:
        # 单元测试不依赖真实 Redis，关闭 Redis 路径走本地内存缓存
        self.strategy._redis_enabled = False
        self.strategy._minute_cache.clear()
        self.strategy._minute_cache_day = ""
        self.strategy._redis_failed = False
        self.strategy._stock_scores.clear()
        self.strategy._highest_prices.clear()
        self.strategy._select_stocks_set.clear()
        self.strategy._select_stocks_date = ""

    # ---- 真实数据日线信号 ----

    def test_is_match_strategy_with_real_daily_data(self) -> None:
        self.assertGreaterEqual(len(self.data), 15)
        matched, extra = self.strategy.is_match_strategy(self.data)
        self.assertIsInstance(matched, bool)
        self.assertIsInstance(extra, dict)
        # 返回结构稳定：要么触发买入信号，要么带原因
        if matched:
            self.assertEqual(extra.get("signal"), "BUY")
        else:
            self.assertTrue(
                "reason" in extra or "decline_pct" in extra or "rebound_from_low" in extra
            )

    def test_is_match_strategy_insufficient_data(self) -> None:
        matched, extra = self.strategy.is_match_strategy(self.data[:5])
        self.assertFalse(matched)
        self.assertIn("reason", extra)

    def test_select_with_real_data(self) -> None:
        results = self.strategy.select(["000001"], days=20)
        self.assertEqual(len(results), 1)
        self.assertIn("code", results[0])
        self.assertIn("matched", results[0])
        self.assertIsInstance(results[0]["matched"], bool)

    # ---- 分钟缓存 ----

    def test_save_minute_cache_stores_low(self) -> None:
        bar = dict(self.data[-1])
        self.strategy._save_minute_cache("000001", bar)
        entry = self.strategy._get_minute_cache("000001")[-1]
        self.assertIn("low", entry)
        self.assertGreater(entry["low"], 0)
        self.assertAlmostEqual(entry["low"], float(bar["low"]))

    def test_minute_cache_bounded(self) -> None:
        for i in range(500):
            bar = dict(self.data[-1])
            bar["create_time"] = f"2026-07-15 {10 + i // 60:02d}:{i % 60:02d}:00"
            self.strategy._save_minute_cache("000001", bar)
        self.assertLessEqual(len(self.strategy._get_minute_cache("000001")), 60 * 4)

    def test_save_minute_cache_stores_score_and_buyed(self) -> None:
        """分钟缓存条目保存策略打分 score 与已买入标记 buyed。"""
        bar = dict(self.data[-1])
        self.strategy._stock_scores["000001"] = 15.5
        self.strategy._bought_codes.add("000001")
        self.strategy._save_minute_cache("000001", bar)
        entry = self.strategy._get_minute_cache("000001")[-1]
        self.assertEqual(entry["score"], 15.5)
        self.assertTrue(entry["buyed"])

        # 显式传参优先于策略状态
        self.strategy._save_minute_cache("000001", bar, score=1.0, buyed=False)
        entry = self.strategy._get_minute_cache("000001")[-1]
        self.assertEqual(entry["score"], 1.0)
        self.assertFalse(entry["buyed"])

    def test_minute_cache_key_format(self) -> None:
        """Redis key 为 code+日期（YYYY-MM-DD）。"""
        self.assertEqual(
            self.strategy._minute_cache_key("000001", "2026-07-15"),
            "000001:2026-07-15",
        )

    def test_buy_success_marks_bought_and_score(self) -> None:
        """买入条件满足时记录策略打分并标记已买入。"""
        from factor.in_day_support_resistance import InDaySRFactor

        bar = {
            "price": 10.0,
            "open": 10.0,
            "preclose": 10.0,
            "high": 10.2,
            "low": 9.9,
            "close": 10.0,
            "create_time": "2026-07-15 10:30:00",
        }
        inday_factor = self.strategy.factor_manager.get(InDaySRFactor.factor_name)
        with patch.object(
            self.strategy, "_check_volume_price_condition",
            return_value=(True, {"score": 30.0}),
        ), patch.object(
            self.strategy, "_check_buy_above_avg_price",
            return_value=(True, {"score": 5.0}),
        ), patch.object(inday_factor, "calculate", return_value={"s2": 0.0}):
            matched, extra = self.strategy._check_buy_conditions("000001", bar)
        self.assertTrue(matched)
        self.assertIn("000001", self.strategy._bought_codes)
        self.assertGreaterEqual(self.strategy._stock_scores.get("000001", 0.0), 35.0)

    def test_sell_success_discards_bought_mark(self) -> None:
        """卖出条件满足后清除已买入标记。"""
        self.strategy._bought_codes.add("000001")
        self.strategy._stock_scores["000001"] = 10.0
        position = {"cost_price": 10.0, "buy_time": "2026-07-01 10:00:00"}
        bar = {
            "price": 9.5,
            "open": 10.0,
            "preclose": 10.0,
            "high": 10.2,
            "low": 9.5,
            "create_time": "2026-07-15 13:45:00",
        }
        with patch.object(
            self.strategy, "_get_highest_since_buy", return_value=0.0
        ), patch.object(self.strategy, "_highest_prices", {"000001": 10.2}):
            matched, extra = self.strategy._check_sell_conditions(
                "000001", position, bar
            )
        self.assertTrue(matched)
        self.assertEqual(extra.get("signal"), "SELL")
        self.assertNotIn("000001", self.strategy._bought_codes)

    def test_check_buy_conditions_with_real_bar(self) -> None:
        """真实行情数据下买入检查不抛异常，并正确返回结构。"""
        # 用真实日K映射为分钟字段并填充缓存
        for i, row in enumerate(self.data):
            self.strategy._save_minute_cache(
                "000001",
                {
                    "price": float(row["close"]),
                    "open": float(row["open"]),
                    "close": float(row["close"]),
                    "low": float(row["low"]),
                    "high": float(row["high"]),
                    "volume": int(row["volume"]),
                    "amount": float(row["amount"]),
                    "create_time": "2026-07-15 10:30:00",
                },
            )
        last = self.data[-1]
        preclose = float(self.data[-2]["close"])
        bar = {
            "code": "000001",
            "price": float(last["close"]),
            "open": float(last["open"]),
            "preclose": preclose,
            "close": float(last["close"]),
            "low": float(last["low"]),
            "high": float(last["high"]),
            "volume": int(last["volume"]),
            "amount": float(last["amount"]),
            "create_time": "2026-07-15 10:30:00",
        }

        matched, extra = self.strategy._check_buy_conditions("000001", bar)

        self.assertIsInstance(matched, bool)
        self.assertIsInstance(extra, dict)
        if matched:
            self.assertEqual(extra.get("signal"), "BUY")

    def test_check_sell_conditions_1440_branch_no_keyerror(self) -> None:
        """14:40 后的 VWAP 卖点不再因缺少 low 字段抛 KeyError。"""
        for row in self.data:
            self.strategy._save_minute_cache(
                "000001",
                {
                    "price": float(row["close"]),
                    "open": float(row["open"]),
                    "close": float(row["close"]),
                    "low": float(row["low"]),
                    "high": float(row["high"]),
                    "volume": int(row["volume"]),
                    "amount": float(row["amount"]),
                    "create_time": "2026-07-15 14:45:00",
                },
            )
        last = self.data[-1]
        position = {
            "cost_price": 10.0,
            "buy_time": "2026-07-01 10:00:00",
            "quantity": 100,
        }
        bar = {
            "code": "000001",
            "price": float(last["close"]),
            "open": float(last["open"]),
            "preclose": float(self.data[-2]["close"]),
            "close": float(last["close"]),
            "low": float(last["low"]),
            "high": float(last["high"]),
            "create_time": "2026-07-15 14:45:00",
        }

        matched, extra = self.strategy._check_sell_conditions("000001", position, bar)

        self.assertIsInstance(matched, bool)
        self.assertIsInstance(extra, dict)

    def test_check_sell_conditions_empty_cache(self) -> None:
        """缓存为空时卖点5安全降级，不抛异常。"""
        position = {"cost_price": 10.0, "buy_time": "2026-07-01 10:00:00"}
        bar = {
            "code": "000001",
            "price": 10.0,
            "open": 10.0,
            "preclose": 10.0,
            "low": 9.9,
            "high": 10.2,
            "create_time": "2026-07-15 14:45:00",
        }
        with patch.object(
            self.strategy, "_get_highest_since_buy", return_value=0.0
        ):
            matched, extra = self.strategy._check_sell_conditions(
                "000001", position, bar
            )
        self.assertIsInstance(matched, bool)
        self.assertIn("reason", extra)

    # ---- 持仓最高价查询（真实K线） ----

    def test_get_highest_since_buy_with_real_kline(self) -> None:
        highest = self.strategy._get_highest_since_buy(
            "000001", "2026-07-01 10:00:00", today_high=0.0
        )
        self.assertGreater(highest, 0)

    # ---- 其他接口方法 ----

    def test_before_trading_clears_cache(self) -> None:
        self.strategy._minute_cache["000001"] = [{"price": 1.0}]
        self.strategy._stock_scores["000001"] = 10.0
        self.strategy._bought_codes.add("000001")
        self.strategy._highest_prices["000001"] = 10.0
        self.strategy._select_stocks_set = {"000001"}
        self.strategy._select_stocks_date = "2026-07-15"
        self.strategy.before_trading("2026-07-16", ["000001"])
        self.assertEqual(self.strategy._minute_cache, {})
        self.assertEqual(self.strategy._get_minute_cache("000001"), [])
        self.assertEqual(self.strategy._stock_scores, {})
        self.assertEqual(self.strategy._bought_codes, set())
        self.assertEqual(self.strategy._highest_prices, {})
        self.assertEqual(self.strategy._select_stocks_set, set())

    def test_handle_minute_bar_routes_by_select_list(self) -> None:
        self.strategy._load_select_stocks = MagicMock(return_value={"000001"})
        self.strategy.check_minute_buy = MagicMock(
            return_value=(True, {"signal": "BUY", "current_price": 10.0})
        )
        bar = dict(self.data[-1])
        matched, extra = self.strategy.handle_minute_bar("000001", {}, bar)
        self.assertTrue(matched)
        self.strategy.check_minute_buy.assert_called_once()
        self.assertIn("low", self.strategy._get_minute_cache("000001")[-1])

        # 不在选股列表 → 检查卖出
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

    def test_init_factors_registers(self) -> None:
        from factor.volume_expansion_factor import VolumeExpansionFactor

        self.assertIsNotNone(
            self.strategy.factor_manager.get(VolumeExpansionFactor.factor_name)
        )

    # ---- 分支覆盖：is_match_strategy 各条件分支（基于真实数据结构构造窗口） ----

    def _frame(
        self,
        closes: list[float],
        highs: list[float] | None = None,
        code: str = "000001",
    ) -> list[dict[str, Any]]:
        highs = highs or list(closes)
        rows: list[dict[str, Any]] = []
        for i, (c, h) in enumerate(zip(closes, highs)):
            rows.append(
                {
                    "code": code,
                    "open": c,
                    "close": c,
                    "high": h,
                    "low": min(c, h) * 0.99,
                    "volume": 100000 + i,
                    "amount": 1_000_000.0,
                    "create_time": f"2026-07-{1 + i:02d} 00:00:00",
                }
            )
        return rows

    def test_is_match_strategy_branch_no_code(self) -> None:
        rows = self._frame([10.0] * 16)
        for row in rows:
            row.pop("code", None)
        matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertIn("无法获取股票代码", extra["reason"])

    def test_is_match_strategy_branch_small_decline(self) -> None:
        rows = self._frame([10.0] * 16, highs=[10.01] * 16)
        with patch.object(
            self.strategy, "_match_pe_profit", return_value=(True, {})
        ):
            matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertIn("条件2不满足", extra["reason"])

    def test_is_match_strategy_branch_low_rebound(self) -> None:
        closes = [12.0, 11.8, 11.6, 11.4, 11.2, 11.0, 10.8, 10.6,
                  10.4, 10.2, 10.0, 10.0, 10.0, 10.0, 10.05, 10.1]
        rows = self._frame(closes)
        with patch.object(
            self.strategy, "_match_pe_profit", return_value=(True, {})
        ):
            matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertTrue("条件3不满足" in extra["reason"] or "涨幅" in extra["reason"])

    def test_is_match_strategy_branch_full_match(self) -> None:
        closes = [12.0, 11.8, 11.6, 11.4, 11.2, 11.0, 10.8, 10.6,
                  10.4, 10.2, 10.0, 10.0, 10.0, 10.3, 10.5, 11.0]
        rows = self._frame(closes)
        with patch.object(
            self.strategy, "_match_pe_profit", return_value=(True, {})
        ):
            matched, extra = self.strategy.is_match_strategy(rows)
        self.assertTrue(matched, extra)
        self.assertEqual(extra.get("signal"), "BUY")

    def test_is_match_strategy_branch_gap_from_high(self) -> None:
        closes = [12.0, 11.8, 11.6, 11.4, 11.2, 11.0, 10.8, 10.6,
                  10.4, 10.2, 10.0, 10.0, 10.0, 10.8, 10.8, 10.9]
        highs = [12.0, 11.8, 11.6, 11.4, 11.2, 11.0, 10.8, 10.6,
                 10.4, 10.2, 10.0, 10.0, 10.0, 10.8, 10.8, 11.2]
        rows = self._frame(closes, highs=highs)
        with patch.object(
            self.strategy, "_match_pe_profit", return_value=(True, {})
        ):
            matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertIn("条件4不满足", extra["reason"])

    def test_is_match_strategy_branch_decline_factor_missing(self) -> None:
        from factor.decline_factor import DeclineFactor

        rows = self._frame([10.0] * 16)
        original = self.strategy.factor_manager.get

        def fake_get(name: str):
            if name == DeclineFactor.factor_name:
                return None
            return original(name)

        with patch.object(
            self.strategy, "_match_pe_profit", return_value=(True, {})
        ), patch.object(self.strategy.factor_manager, "get", side_effect=fake_get):
            matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertIn("跌幅因子未注册", extra["reason"])

    # ---- 分支覆盖：分钟检查 ----

    def test_check_minute_buy_sell_wrappers(self) -> None:
        last = self.data[-1]
        bar = {
            "code": "000001",
            "price": float(last["close"]),
            "open": float(last["open"]),
            "preclose": float(self.data[-2]["close"]),
            "low": float(last["low"]),
            "high": float(last["high"]),
            "volume": 1,
            "amount": 1.0,
            "create_time": "2026-07-15 09:00:00",
        }
        matched, extra = self.strategy.check_minute_buy("000001", bar)
        self.assertIsInstance(matched, bool)
        position = {"cost_price": 10.0, "buy_time": "2026-07-01 10:00:00"}
        matched, extra = self.strategy.check_minute_sell("000001", position, bar)
        self.assertIsInstance(matched, bool)

    def test_handle_tick_bar_routes(self) -> None:
        self.strategy._load_select_stocks = MagicMock(return_value={"000001"})
        self.strategy.check_tick_buy = MagicMock(
            return_value=(True, {"signal": "BUY"})
        )
        matched, extra = self.strategy.handle_tick_bar("000001", {}, {})
        self.assertTrue(matched)

        self.strategy._load_select_stocks = MagicMock(return_value=set())
        self.strategy.check_tick_sell = MagicMock(
            return_value=(False, {"reason": "no"})
        )
        matched, extra = self.strategy.handle_tick_bar("000001", {}, {})
        self.assertFalse(matched)

    def test_check_buy_conditions_invalid_price(self) -> None:
        bar = {"price": 0, "open": 10, "preclose": 10, "create_time": "2026-07-15 10:30:00"}
        matched, extra = self.strategy._check_buy_conditions("000001", bar)
        self.assertFalse(matched)
        self.assertIn("当前价格异常", extra["reason"])

    def test_check_buy_conditions_invalid_open(self) -> None:
        bar = {"price": 10, "open": 0, "preclose": 10, "create_time": "2026-07-15 10:30:00"}
        matched, extra = self.strategy._check_buy_conditions("000001", bar)
        self.assertFalse(matched)
        self.assertIn("开盘价或昨收价", extra["reason"])

    def test_check_buy_conditions_low_open_gap(self) -> None:
        bar = {"price": 10, "open": 9.8, "preclose": 10, "create_time": "2026-07-15 10:30:00"}
        matched, extra = self.strategy._check_buy_conditions("000001", bar)
        self.assertFalse(matched)
        self.assertIn("低开过大", extra["reason"])

    def test_check_buy_conditions_before_10(self) -> None:
        bar = {"price": 10, "open": 10, "preclose": 10, "create_time": "2026-07-15 09:30:00"}
        matched, extra = self.strategy._check_buy_conditions("000001", bar)
        self.assertFalse(matched)
        self.assertIn("10:00之前", extra["reason"])

    def test_check_buy_conditions_unparsable_time(self) -> None:
        bar = {"price": 10, "open": 10, "preclose": 10, "create_time": "bad-time"}
        matched, extra = self.strategy._check_buy_conditions("000001", bar)
        self.assertFalse(matched)
        self.assertIn("无法解析分钟时间", extra["reason"])

    def test_check_buy_conditions_empty_cache(self) -> None:
        bar = {"price": 10, "open": 10, "preclose": 10, "create_time": "2026-07-15 10:30:00"}
        matched, extra = self.strategy._check_buy_conditions("000001", bar)
        self.assertFalse(matched)
        self.assertIn("分钟缓存为空", extra["reason"])

    def test_get_highest_since_buy_edge_cases(self) -> None:
        self.assertEqual(self.strategy._get_highest_since_buy("000001", ""), 0.0)
        with patch(
            "strategy.strong_rebound_strategy.AppContext"
        ) as mock_ctx:
            mock_ctx.return_value.market_api.get_day_kline.side_effect = Exception(
                "api down"
            )
            self.assertEqual(
                self.strategy._get_highest_since_buy("000001", "2026-07-01 10:00:00"),
                0.0,
            )

    def test_check_sell_conditions_1330_branch(self) -> None:
        position = {"cost_price": 10.0, "buy_time": "2026-07-01 10:00:00"}
        bar = {
            "price": 9.5,
            "open": 10.0,
            "preclose": 10.0,
            "high": 10.2,
            "low": 9.5,
            "create_time": "2026-07-15 13:45:00",
        }
        with patch.object(
            self.strategy, "_get_highest_since_buy", return_value=0.0
        ), patch.object(self.strategy, "_highest_prices", {"000001": 10.2}):
            matched, extra = self.strategy._check_sell_conditions(
                "000001", position, bar
            )
        self.assertTrue(matched)
        self.assertEqual(extra.get("signal"), "SELL")

    def test_check_sell_conditions_invalid_low_in_cache(self) -> None:
        self.strategy._minute_cache["000001"] = [
            {"price": 10.0, "volume": 1, "amount": 1.0, "close": 10.0, "low": 0.0}
        ]
        bar = {
            "price": 10.0,
            "open": 10.0,
            "preclose": 10.0,
            "high": 10.2,
            "low": 9.9,
            "create_time": "2026-07-15 14:45:00",
        }
        with patch.object(
            self.strategy, "_get_highest_since_buy", return_value=0.0
        ):
            matched, extra = self.strategy._check_sell_conditions(
                "000001", {"cost_price": 10.0}, bar
            )
        self.assertIsInstance(matched, bool)
        self.assertIn("reason", extra)

    def test_check_sell_conditions_exception_handled(self) -> None:
        with patch.object(
            self.strategy, "_get_highest_since_buy", side_effect=RuntimeError("boom")
        ):
            matched, extra = self.strategy._check_sell_conditions(
                "000001", {"cost_price": 10.0}, {"price": 10.0, "create_time": "x"}
            )
        self.assertFalse(matched)
        self.assertIn("检查异常", extra["reason"])


if __name__ == "__main__":
    unittest.main()
