"""强势反弹策略测试。

**重写说明（2026-09-13）**：原 35 例的 setUp 仍引用策略上已迁出的字段
（`_minute_cache` / `_stock_scores` / `_select_stocks_set` / `_bought_codes`），
在缓存层重构（P0-3 / P0-4 / P2-13 / P2-25）后全部报错。

重构后的实际契约：
- 分钟行情、策略打分、选股结果全部由 `core.user_strategy_cache.UserStrategyCache`
  统一持有，key 含 `user_strategy_id`；策略侧只读行情（写方是 StrategyWorkflow）。
- 「已买入/已卖出」标记由打分缓存的 `done` 字段承担（原 `_bought_codes` 已删除）。
- `factor_manager` 改为**策略实例私有**（原 P0-5）。
- 卖出最高价口径见设计约定 D-3：有持仓 → 买入至今；无持仓 → 最近 2 个交易日。

测试用固定 `user_strategy_id` 隔离缓存，并关闭 Redis 落盘避免污染实盘缓存。
真实行情用例在服务端无数据时自动跳过（服务端日K数据窗口不稳定）。
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

import pandas as pd

from app_context import AppContext
from core.user_strategy_cache import UserStrategyCache
from strategy.strong_rebound_strategy import StrongReboundStrategy
from utils.tools import load_admin_token

# 测试专用用户策略 ID：缓存 key 前缀，保证与其它用例/实盘数据隔离
USID: str = "test-strong-rebound-us"

# 与类属性默认值等价的纯值参数：显式传入可跳过三级回退中的网络查询，
# 让用例只验证策略逻辑本身（参数加载链路由 test_base_strategy 覆盖）。
# `min_score` 按 10 分制给出（2026-09-28 起综合分刻度 = 10）。
TEST_PARAMS: dict[str, Any] = {
    "match": {
        "pe_limit": 300,
        "profit_limit": 10,
        "trend_days_limit": 7,
        "consecutive_days": 5,
        "rebound_score": 10.0,
        "up_trend_score": 6.0,
    },
    "buy": {
        "consecutive_minutes": 5,
        "min_score": 6.0,
        "trend_minutes_limit": 7,
    },
    "sell": {
        "drawdown_pct": -8.0,
        "vwap_minutes_limit": 7,
        "max_score": -15.0,
        "trend_minutes_limit": 7,
    },
}

# 服务端日K数据窗口不稳定，按「取到最多行」的方式尝试多个区间
DAILY_WINDOWS: tuple[tuple[str, str], ...] = (
    ("2026-03-01", "2026-07-31"),
    ("2026-01-01", "2026-08-31"),
    ("2025-09-01", "2026-08-31"),
)

BAR_DAY: str = "2026-07-15"


def setUpModule() -> None:
    AppContext().api_client.set_fallback_token(load_admin_token())


def fetch_daily(code: str = "000001") -> list[dict[str, Any]]:
    """拉取真实日线数据，返回行数最多的那一段（可用时至少 16 行）。"""
    best: list[dict[str, Any]] = []
    api = AppContext().market_api
    for start, end in DAILY_WINDOWS:
        try:
            rows: list[dict[str, Any]] = api.get_day_kline(
                code=code, start=start, end=end
            )
        except Exception:
            rows = []
        if rows and len(rows) > len(best):
            best = rows
        if len(best) >= 16:
            break
    return best


def make_bar(**overrides: Any) -> dict[str, Any]:
    """构造一根分钟行情（默认时间落在 10:30，避开 9:35 过滤与开盘计分分支）。"""
    bar: dict[str, Any] = {
        "code": "000001",
        "price": 10.0,
        "open": 10.0,
        "preclose": 10.0,
        "high": 10.2,
        "low": 9.9,
        "close": 10.0,
        "volume": 1000,
        "amount": 1_000_000.0,
        "create_time": f"{BAR_DAY} 10:30:00",
    }
    bar.update(overrides)
    return bar


class TestStrongReboundStrategy(unittest.TestCase):
    """强势反弹策略单元测试（不依赖策略对象上的缓存字段）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.data: list[dict[str, Any]] = fetch_daily("000001")
        cls.strategy = StrongReboundStrategy(
            user_strategy_id=USID, user_strategy_params=TEST_PARAMS
        )

    def setUp(self) -> None:
        cache = UserStrategyCache()
        cache.set_redis_enabled(False)  # 单元测试不触碰真实 Redis
        cache.reset_redis_failure()
        cache.clear_user(USID)
        self._purge_minute_cache()
        self._purge_highest_price_cache()
        self.strategy._highest_prices.clear()

    def tearDown(self) -> None:
        UserStrategyCache().set_redis_enabled(True)

    # ---- 测试辅助 ----

    @staticmethod
    def _purge_minute_cache() -> None:
        """清空行情内存快照（含 `_last_day`），避免用例之间互相污染。"""
        cache = UserStrategyCache()
        with cache._lock:
            cache._minute_snapshot.clear()
            cache._last_day = ""

    @staticmethod
    def _purge_highest_price_cache() -> None:
        """清空区间最高价内存快照（全局单例，跨用例会串）。"""
        cache = UserStrategyCache()
        with cache._lock:
            cache._highest_price_snapshot.clear()

    @staticmethod
    def _frame(
        closes: list[float],
        highs: list[float] | None = None,
        code: str = "000001",
    ) -> list[dict[str, Any]]:
        """构造日线窗口（is_match_strategy 的输入）。"""
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

    # ---- 参数加载与因子注册 ----

    def test_apply_params_maps_nested_config(self) -> None:
        strategy = StrongReboundStrategy(
            user_strategy_id=USID,
            user_strategy_params={"match": {"pe_limit": 50}, "sell": {"max_score": -9.0}},
        )
        self.assertEqual(strategy.PE_LIMIT, 50)
        self.assertEqual(strategy.SELL_MAX_SCORE, -9.0)
        # 未覆盖的字段保留类属性默认值
        self.assertEqual(strategy.PROFIT_LIMIT, StrongReboundStrategy.PROFIT_LIMIT)

    def test_apply_params_unwraps_desc_value(self) -> None:
        """strategy.default_params 的 {desc, value} 包装格式应被解包后生效。"""
        strategy = StrongReboundStrategy(
            user_strategy_id=USID,
            user_strategy_params={"match": {"pe_limit": {"desc": "最大市盈率", "value": 77}}},
        )
        self.assertEqual(strategy.PE_LIMIT, 77)

    def test_factor_manager_is_instance_private(self) -> None:
        """P0-5：因子管理器必须实例私有，否则多用户会互相覆盖同名因子。"""
        a = StrongReboundStrategy(user_strategy_id="us-a", user_strategy_params=TEST_PARAMS)
        b = StrongReboundStrategy(user_strategy_id="us-b", user_strategy_params=TEST_PARAMS)
        self.assertIsNot(a.factor_manager, b.factor_manager)

    def test_init_factors_does_not_register(self) -> None:
        """契约：本策略不再通过 factor_manager 注册因子（`init_factors` 为 `pass`）。

        2026-09-28 重构后，趋势/反弹/成交量因子都在调用点按需 new 出来
        （`MultiTrendFactor` / `LowReboundFactor` / `VwapFactor`），
        因子管理器不再持有它们。此处锁定该契约，避免又有人按「已注册」去取因子，
        拿到 None 后在运行期才炸。
        """
        from factor.low_rebound_factor import LowReboundFactor
        from factor.multi_trend_factor import MultiTrendFactor
        from factor.vwap_factor import VwapFactor

        for factor_name in (
            LowReboundFactor.factor_name,
            MultiTrendFactor.factor_name,
            VwapFactor.factor_name,
        ):
            self.assertIsNone(
                self.strategy.factor_manager.get(factor_name),
                f"不应再注册因子: {factor_name}",
            )
        # 但这些因子必须可直接实例化使用（策略运行期就是这么做的）
        self.assertIsInstance(MultiTrendFactor(7).score(pd.DataFrame()), float)

    # ---- is_match_strategy 分支 ----

    def test_is_match_strategy_with_real_daily_data(self) -> None:
        if len(self.data) < 5:
            self.skipTest("服务端未返回足够日线数据")
        matched, extra = self.strategy.is_match_strategy(self.data)
        self.assertIsInstance(matched, bool)
        self.assertIsInstance(extra, dict)
        if matched:
            self.assertEqual(extra.get("signal"), "BUY")
        else:
            self.assertIn("reason", extra)

    def test_is_match_strategy_insufficient_data(self) -> None:
        matched, extra = self.strategy.is_match_strategy([])
        self.assertFalse(matched)
        self.assertIn("数据不足", extra["reason"])

        matched, extra = self.strategy.is_match_strategy(self._frame([10.0] * 3))
        self.assertFalse(matched)
        self.assertIn("数据不足", extra["reason"])

    def test_is_match_strategy_branch_no_code(self) -> None:
        rows = self._frame([10.0] * 16)
        for row in rows:
            row.pop("code", None)
        matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertIn("无法获取股票代码", extra["reason"])

    def test_is_match_strategy_branch_pe_profit_rejected(self) -> None:
        """条件1（市盈率/利润增长）不满足时直接返回，且不进入趋势判断。"""
        rows = self._frame([10.0] * 16)
        with patch.object(
            self.strategy,
            "_match_pe_profit",
            return_value=(False, {"reason": "条件1不满足: 市盈率数据异常"}),
        ):
            matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertIn("条件1不满足", extra["reason"])

    def test_is_match_strategy_branch_trend_priority(self) -> None:
        """趋势分达标 **且** 反弹分达标 → 选中（两个条件是 AND，不是 OR）。"""
        rows = self._frame([10.0] * 16)
        with patch.object(
            self.strategy, "_match_pe_profit", return_value=(True, {"score": 2.0})
        ), patch(
            "strategy.strong_rebound_strategy.MultiTrendFactor.score",
            return_value=10.0,
        ), patch(
            "strategy.strong_rebound_strategy.LowReboundFactor.score",
            return_value=20.0,
        ):
            matched, extra = self.strategy.is_match_strategy(rows)
        self.assertTrue(matched, extra)
        self.assertEqual(extra.get("signal"), "BUY")
        self.assertIn("趋势", extra["reason"])

    def test_is_match_strategy_requires_rebound_too(self) -> None:
        """趋势达标但反弹不足 → 未选中（AND 语义，防止把 `or` 当成实现）。"""
        rows = self._frame([10.0] * 16)
        with patch.object(
            self.strategy, "_match_pe_profit", return_value=(True, {"score": 2.0})
        ), patch(
            "strategy.strong_rebound_strategy.MultiTrendFactor.score",
            return_value=10.0,
        ), patch(
            "strategy.strong_rebound_strategy.LowReboundFactor.score",
            return_value=1.0,
        ):
            matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertIn("未选中", extra["reason"])

    def test_is_match_strategy_requires_trend_too(self) -> None:
        """反弹达标但趋势不足 → 未选中。"""
        rows = self._frame([10.0] * 16)
        with patch.object(
            self.strategy, "_match_pe_profit", return_value=(True, {"score": 2.0})
        ), patch(
            "strategy.strong_rebound_strategy.MultiTrendFactor.score",
            return_value=0.0,
        ), patch(
            "strategy.strong_rebound_strategy.LowReboundFactor.score",
            return_value=20.0,
        ):
            matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertIn("未选中", extra["reason"])

    def test_is_match_strategy_branch_score_not_enough(self) -> None:
        """趋势不足且反弹总分不够 → 未选中。"""
        rows = self._frame([10.0] * 16)
        with patch.object(
            self.strategy, "_match_pe_profit", return_value=(True, {"score": 2.0})
        ), patch(
            "strategy.strong_rebound_strategy.MultiTrendFactor.score",
            return_value=0.0,
        ), patch(
            "strategy.strong_rebound_strategy.LowReboundFactor.score",
            return_value=0.0,
        ):
            matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertIn("未选中", extra["reason"])

    def test_select_with_real_data(self) -> None:
        results = self.strategy.select(["000001"], BAR_DAY, days=20)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["code"], "000001")
        self.assertIn("matched", results[0])
        self.assertIsInstance(results[0]["matched"], bool)

    # ---- 分钟行情缓存（现由 UserStrategyCache 持有，策略侧只读） ----

    def test_minute_cache_roundtrip(self) -> None:
        bar = make_bar(low=9.87)
        self.strategy._save_minute_cache("000001", bar)
        entries = self.strategy._get_minute_cache("000001")
        self.assertEqual(len(entries), 1)
        self.assertIn("low", entries[0])
        self.assertAlmostEqual(entries[0]["low"], 9.87)

    def test_minute_cache_bounded(self) -> None:
        for i in range(UserStrategyCache.MAX_MINUTE_CACHE + 60):
            self.strategy._save_minute_cache(
                "000001",
                make_bar(create_time=f"{BAR_DAY} {10 + i // 60:02d}:{i % 60:02d}:00"),
            )
        entries = self.strategy._get_minute_cache("000001")
        self.assertEqual(len(entries), UserStrategyCache.MAX_MINUTE_CACHE)

    def test_minute_cache_key_format(self) -> None:
        self.assertEqual(
            self.strategy._minute_cache_key("000001", BAR_DAY),
            f"bar:000001:{BAR_DAY}",
        )

    def test_minute_cache_isolated_by_day(self) -> None:
        """不同交易日的行情不能互相串读。"""
        self.strategy._save_minute_cache("000001", make_bar(create_time=f"{BAR_DAY} 10:30:00"))
        self.assertEqual(len(self.strategy._get_minute_cache("000001")), 1)
        # 策略侧只读「当日」快照，跨日读取由 UserStrategyCache 显式指定 day
        self.assertEqual(
            UserStrategyCache().get_minute_bars("000001", day="2026-07-14"), []
        )

    def test_before_minute_route_does_not_write_cache(self) -> None:
        """P0-4：行情缓存由 workflow 单点写入，策略侧钩子不得再写。"""
        self.strategy._before_minute_route("000001", make_bar())
        self.assertEqual(self.strategy._get_minute_cache("000001"), [])

    # ---- 打分缓存（原 _stock_scores / _bought_codes 的替代语义） ----

    def test_score_cache_accumulates_and_sets_done(self) -> None:
        self.strategy._save_score_cache("000001", "BUY", 30.0, 1, f"{BAR_DAY} 10:30:00", False)
        entry = self.strategy._get_score_cache("000001", "BUY", f"{BAR_DAY} 10:30:00")
        self.assertEqual(entry["score"], 30.0)
        self.assertEqual(entry["check_number"], 1)
        self.assertFalse(entry["done"])

        self.strategy._save_score_cache("000001", "BUY", 45.0, 2, f"{BAR_DAY} 10:31:00", True)
        entry = self.strategy._get_score_cache("000001", "BUY", f"{BAR_DAY} 10:31:00")
        self.assertEqual(entry["score"], 45.0)
        self.assertEqual(entry["check_number"], 2)
        self.assertTrue(entry["done"])

    # ---- 买入条件 ----

    def test_check_buy_conditions_weighted_score_and_star(self) -> None:
        """R8：综合分 = (成交量/5.5×0.7 + 趋势/10×0.3)×10，成交量为主。

        成交量打满（5.5）+ 趋势打满（10）→ 10 分；两者都归一化后加权，
        不再是把两个分量直接相加再硬除以 2（旧实现成交量条件不成立时
        只有趋势分进分子却仍被除以 2，等于把门槛抬高一倍）。
        """
        with patch.object(
            self.strategy, "_check_volume_price_condition",
            return_value=(True, {"score": 5.5, "signal": "BUY"}),
        ), patch(
            "strategy.strong_rebound_strategy.MultiTrendFactor.score",
            return_value=10.0,
        ):
            matched, extra = self.strategy._check_buy_conditions("000001", make_bar())

        self.assertTrue(matched, extra)
        self.assertEqual(extra.get("signal"), "BUY")
        self.assertAlmostEqual(extra["score"], 10.0, places=2)
        # 10 分制折算 5 星
        self.assertAlmostEqual(extra["star"], 5.0, places=1)

    def test_check_buy_conditions_trend_alone_cannot_pass(self) -> None:
        """R8：成交量为主 —— 趋势打满（权重 0.3）也补不上成交量不足。"""
        with patch.object(
            self.strategy, "_check_volume_price_condition",
            return_value=(False, {"reason": "量能不足", "score": 0.0}),
        ), patch(
            "strategy.strong_rebound_strategy.MultiTrendFactor.score",
            return_value=10.0,
        ):
            matched, extra = self.strategy._check_buy_conditions("000001", make_bar())
        self.assertFalse(matched)
        # 仅趋势侧生效：10/10 × 0.3 × 10 = 3.0，低于 min_score=6.0
        self.assertAlmostEqual(extra["score"], 3.0, places=2)
        self.assertIn("分值不足", extra["reason"])

    def test_check_buy_conditions_volume_alone_can_pass(self) -> None:
        """R8：成交量打满（0.7×10 = 7.0 ≥ 6.0）即可买入，趋势只用于加厚分数。"""
        with patch.object(
            self.strategy, "_check_volume_price_condition",
            return_value=(True, {"score": 5.5, "signal": "BUY"}),
        ), patch(
            "strategy.strong_rebound_strategy.MultiTrendFactor.score",
            return_value=0.0,
        ):
            matched, extra = self.strategy._check_buy_conditions("000001", make_bar())
        self.assertTrue(matched, extra)
        self.assertAlmostEqual(extra["score"], 7.0, places=2)

    def test_check_buy_conditions_score_not_enough(self) -> None:
        with patch.object(
            self.strategy, "_check_volume_price_condition",
            return_value=(True, {"score": 1.0, "signal": "BUY"}),
        ), patch(
            "strategy.strong_rebound_strategy.MultiTrendFactor.score",
            return_value=0.0,
        ):
            matched, extra = self.strategy._check_buy_conditions("000001", make_bar())
        self.assertFalse(matched)
        self.assertIn("分值不足", extra["reason"])

    def test_check_buy_conditions_negative_volume_score_blocks(self) -> None:
        """R2/R8：价跌量增（成交量侧为负）必须拉低综合分，不能被当成中性。"""
        with patch.object(
            self.strategy, "_check_volume_price_condition",
            return_value=(True, {"score": -5.0, "signal": "SELL"}),
        ), patch(
            "strategy.strong_rebound_strategy.MultiTrendFactor.score",
            return_value=10.0,
        ):
            matched, extra = self.strategy._check_buy_conditions("000001", make_bar())
        self.assertFalse(matched)
        # (−5/5.5 × 0.7 + 10/10 × 0.3) × 10 ≈ 3.36
        self.assertLess(extra["score"], self.strategy.BUY_MIN_SCORE)

    def test_check_buy_conditions_invalid_price(self) -> None:
        matched, extra = self.strategy._check_buy_conditions("000001", make_bar(price=0))
        self.assertFalse(matched)
        self.assertIn("当前价格异常", extra["reason"])

    def test_check_buy_conditions_invalid_open(self) -> None:
        matched, extra = self.strategy._check_buy_conditions("000001", make_bar(open=0))
        self.assertFalse(matched)
        self.assertIn("开盘价或昨收价", extra["reason"])

    def test_check_buy_conditions_limit_move_filtered(self) -> None:
        """接近跌停（主板 −10%，阈值 = 10% − 2 个百分点余量 = 8%）不买入。"""
        matched, extra = self.strategy._check_buy_conditions(
            "000001", make_bar(price=9.0, preclose=10.0)
        )
        self.assertFalse(matched)
        self.assertIn("接近涨停或跌停", extra["reason"])

    def test_check_buy_conditions_near_limit_up_filtered(self) -> None:
        """R13：涨停方向同样拦截（原实现用 abs() 两个方向都挡）。

        主板 +8% 恰好等于阈值，必须挡下；否则会去追已接近涨停的股票。
        """
        matched, extra = self.strategy._check_buy_conditions(
            "000001", make_bar(price=10.8, preclose=10.0)
        )
        self.assertFalse(matched)
        self.assertIn("接近涨停或跌停", extra["reason"])

    def test_check_buy_conditions_limit_move_is_board_aware(self) -> None:
        """R13：创业板（20% 板，阈值 18%）跌 9% 远未接近跌停，不该被挡。"""
        with patch.object(
            self.strategy, "_check_volume_price_condition",
            return_value=(True, {"score": 5.5, "signal": "BUY"}),
        ), patch(
            "strategy.strong_rebound_strategy.MultiTrendFactor.score",
            return_value=10.0,
        ):
            matched, extra = self.strategy._check_buy_conditions(
                "300750", make_bar(code="300750", price=9.1, preclose=10.0)
            )
        self.assertNotIn("接近涨停或跌停", extra.get("reason", ""))

    def test_check_buy_conditions_preclose_zero_no_crash(self) -> None:
        """R13：preclose=0（数据不全）不得触发除零异常。"""
        matched, extra = self.strategy._check_buy_conditions(
            "000001", make_bar(preclose=0)
        )
        self.assertFalse(matched)
        self.assertIn("异常", extra["reason"])
        self.assertNotIn("检查异常", extra["reason"])

    def test_check_buy_conditions_before_935(self) -> None:
        matched, extra = self.strategy._check_buy_conditions(
            "000001", make_bar(create_time=f"{BAR_DAY} 09:30:00")
        )
        self.assertFalse(matched)
        self.assertIn("9:35之前", extra["reason"])

    def test_check_buy_conditions_unparsable_time(self) -> None:
        matched, extra = self.strategy._check_buy_conditions(
            "000001", make_bar(create_time="bad-time")
        )
        self.assertFalse(matched)
        self.assertIn("无法解析分钟时间", extra["reason"])

    def test_check_buy_conditions_empty_cache(self) -> None:
        """无分钟行情时安全降级：不抛异常、判定为不买入。"""
        matched, extra = self.strategy._check_buy_conditions("000001", make_bar())
        self.assertFalse(matched)
        self.assertIsInstance(extra, dict)
        self.assertEqual(extra.get("signal"), "BUY")

    def test_check_buy_conditions_with_real_bar(self) -> None:
        """真实行情映射为分钟字段后买入检查不抛异常，返回结构稳定。"""
        if not self.data:
            self.skipTest("服务端未返回日线数据")
        for row in self.data:
            self.strategy._save_minute_cache(
                "000001",
                make_bar(
                    price=float(row["close"]),
                    open=float(row["open"]),
                    close=float(row["close"]),
                    low=float(row["low"]),
                    high=float(row["high"]),
                    preclose=float(row["close"]),
                    volume=int(row.get("volume", 0) or 0),
                    amount=float(row.get("amount", 0) or 0),
                ),
            )
        matched, extra = self.strategy._check_buy_conditions("000001", make_bar())
        self.assertIsInstance(matched, bool)
        self.assertIsInstance(extra, dict)

    # ---- 卖出条件与最高价口径（设计约定 D-3） ----

    def test_check_sell_conditions_drawdown_triggers(self) -> None:
        """回撤足够大（分数低于阈值）→ 触发 SELL。"""
        position = {"cost_price": 20.0, "buy_time": "2026-07-01 10:00:00"}
        with patch.object(self.strategy, "_resolve_highest_price", return_value=20.0):
            matched, extra = self.strategy._check_sell_conditions(
                "000001", position, make_bar(price=9.5, low=9.5, high=10.2)
            )
        self.assertTrue(matched, extra)
        self.assertEqual(extra.get("signal"), "SELL")
        self.assertLess(extra.get("score", 0.0), self.strategy.SELL_MAX_SCORE)

    def test_check_sell_conditions_no_trigger_on_small_drawdown(self) -> None:
        position = {"cost_price": 10.0, "buy_time": "2026-07-01 10:00:00"}
        with patch.object(self.strategy, "_resolve_highest_price", return_value=10.1):
            matched, extra = self.strategy._check_sell_conditions(
                "000001", position, make_bar(price=10.05)
            )
        self.assertFalse(matched)
        self.assertNotEqual(extra.get("signal"), "SELL")

    def test_check_sell_conditions_invalid_price(self) -> None:
        matched, extra = self.strategy._check_sell_conditions(
            "000001", {"cost_price": 10.0}, make_bar(price=0)
        )
        self.assertFalse(matched)
        self.assertIn("异常", extra["reason"])

    def test_check_sell_conditions_limit_move_filtered(self) -> None:
        """接近涨停不卖出：主板 +9%（10% 板留 1 个百分点余量）即被挡下。"""
        matched, extra = self.strategy._check_sell_conditions(
            "000001", {"cost_price": 10.0}, make_bar(price=10.9, preclose=10.0)
        )
        self.assertFalse(matched)
        self.assertIn("接近涨停或跌停", extra["reason"])

    def test_check_sell_conditions_limit_move_is_board_aware(self) -> None:
        """R13：ST（5% 板）+4.6% 已接近涨停，必须挡住挂卖单。

        原实现硬编码 9.5%，ST 封在 +5% 时仍会去挂卖单。
        """
        matched, extra = self.strategy._check_sell_conditions(
            "002456",
            {"cost_price": 10.0},
            make_bar(code="002456", name="*ST欧菲", price=10.46, preclose=10.0),
        )
        self.assertFalse(matched)
        self.assertIn("接近涨停或跌停", extra["reason"])

    def test_check_sell_conditions_preclose_zero_no_crash(self) -> None:
        """R13：preclose=0 时不得除零（原实现会抛 ZeroDivisionError）。"""
        with patch.object(self.strategy, "_resolve_highest_price", return_value=10.1):
            matched, extra = self.strategy._check_sell_conditions(
                "000001", {"cost_price": 10.0}, make_bar(preclose=0)
            )
        self.assertIsInstance(matched, bool)
        self.assertNotIn("检查异常", extra.get("reason", ""))

    def test_check_sell_conditions_reason_field_name(self) -> None:
        """R9：卖点原因必须写进 `reason`（原实现写成 `reasons`，落库后为空）。"""
        with patch.object(self.strategy, "_resolve_highest_price", return_value=20.0):
            matched, extra = self.strategy._check_sell_conditions(
                "000001",
                {"cost_price": 20.0, "buy_time": "2026-07-01 10:00:00"},
                make_bar(price=9.5, low=9.5, high=10.2),
            )
        self.assertTrue(matched, extra)
        self.assertIn("reason", extra)
        self.assertNotIn("reasons", extra)
        self.assertIn("卖出分数", extra["reason"])

    def test_check_sell_conditions_before_935(self) -> None:
        matched, extra = self.strategy._check_sell_conditions(
            "000001",
            {"cost_price": 10.0, "buy_time": "2026-07-01 10:00:00"},
            make_bar(create_time=f"{BAR_DAY} 09:30:00"),
        )
        self.assertFalse(matched)
        self.assertIn("9:35之前", extra["reason"])

    def test_check_sell_conditions_unparsable_time(self) -> None:
        matched, extra = self.strategy._check_sell_conditions(
            "000001", {"cost_price": 10.0}, make_bar(create_time="bad-time")
        )
        self.assertFalse(matched)
        self.assertIn("解析时间异常", extra["reason"])

    def test_check_sell_conditions_empty_cache(self) -> None:
        """分钟缓存为空时卖点安全降级，不抛异常。"""
        with patch.object(self.strategy, "_resolve_highest_price", return_value=10.2):
            matched, extra = self.strategy._check_sell_conditions(
                "000001",
                {"cost_price": 10.0, "buy_time": "2026-07-01 10:00:00"},
                make_bar(low=9.9, high=10.2),
            )
        self.assertIsInstance(matched, bool)
        self.assertIsInstance(extra, dict)

    def test_check_sell_conditions_exception_handled(self) -> None:
        with patch.object(
            self.strategy, "_resolve_highest_price", side_effect=RuntimeError("boom")
        ):
            matched, extra = self.strategy._check_sell_conditions(
                "000001", {"cost_price": 10.0}, make_bar()
            )
        self.assertFalse(matched)
        self.assertIn("检查异常", extra["reason"])

    def test_sell_uses_buy_time_when_position_present(self) -> None:
        """有持仓信息 → 最高价基准为真实 buy_time（买入至今）。"""
        position = {"cost_price": 10.0, "buy_time": "2026-07-01 10:00:00"}
        with patch.object(
            self.strategy, "_resolve_highest_price", return_value=10.1
        ) as mock_highest:
            self.strategy._check_sell_conditions("000001", position, make_bar())
        self.assertEqual(mock_highest.call_args.args[1], "2026-07-01 10:00:00")

    def test_sell_falls_back_to_recent_two_trading_days_without_position(self) -> None:
        """D-3：无持仓信息（实时链路恒传空持仓）→ 以最近 2 个交易日为买入基准。"""
        with patch.object(
            self.strategy, "_resolve_highest_price", return_value=10.1
        ) as mock_highest:
            self.strategy._check_sell_conditions("000001", {}, make_bar())

        buy_time: str = mock_highest.call_args.args[1]
        expected: str = self.strategy.get_previous_trading_day(
            f"{BAR_DAY} 10:30:00", days=2
        )
        self.assertEqual(buy_time, expected)
        self.assertTrue(self.strategy.xshg.is_session(buy_time[:10]))
        self.assertLess(buy_time[:10], BAR_DAY)

    def test_resolve_highest_price_uses_kline_when_available(self) -> None:
        with patch.object(self.strategy, "_get_highest_since_buy", return_value=12.5):
            highest = self.strategy._resolve_highest_price(
                "000001", "2026-07-01 10:00:00", f"{BAR_DAY} 10:30:00", 10.2, 10.0
            )
        self.assertEqual(highest, 12.5)

    def test_resolve_highest_price_falls_back_to_memory_tracking(self) -> None:
        """日K取不到（返回 0）时回退到内存追踪的盘中最高价。"""
        with patch.object(self.strategy, "_get_highest_since_buy", return_value=0.0):
            highest = self.strategy._resolve_highest_price(
                "000001", "2026-07-01 10:00:00", f"{BAR_DAY} 10:30:00", 10.2, 10.0
            )
        self.assertEqual(highest, 10.2)
        self.assertEqual(self.strategy._highest_prices.get("000001"), 10.2)

        # 后续更高的当日最高价应抬高追踪值
        with patch.object(self.strategy, "_get_highest_since_buy", return_value=0.0):
            highest = self.strategy._resolve_highest_price(
                "000001", "2026-07-01 10:00:00", f"{BAR_DAY} 10:35:00", 10.8, 10.0
            )
        self.assertEqual(highest, 10.8)

    # ---- 持仓最高价查询 ----

    def test_get_highest_since_buy_empty_buy_time(self) -> None:
        self.assertEqual(
            self.strategy._get_highest_since_buy("000001", "", f"{BAR_DAY} 10:30:00", 0.0),
            0.0,
        )

    def test_get_highest_since_buy_with_real_kline(self) -> None:
        highest = self.strategy._get_highest_since_buy(
            "000001", "2026-07-01 10:00:00", f"{BAR_DAY} 10:30:00", 0.0
        )
        self.assertIsInstance(highest, float)
        self.assertGreaterEqual(highest, 0.0)

    def test_get_highest_since_buy_api_error_returns_today_high(self) -> None:
        with patch("strategy.strong_rebound_strategy.AppContext") as mock_ctx:
            mock_ctx.return_value.market_api.get_day_kline.side_effect = Exception(
                "api down"
            )
            highest = self.strategy._get_highest_since_buy(
                "000001", "2026-07-01 10:00:00", f"{BAR_DAY} 10:30:00", 9.5
            )
        self.assertEqual(highest, 9.5)

    def test_get_highest_since_buy_hits_intraday_cache(self) -> None:
        """R10：同一天同一区间只查一次日K；命中缓存后不再发请求。"""
        UserStrategyCache().save_highest_price("000001", "2026-07-01", BAR_DAY, 12.5)
        with patch("strategy.strong_rebound_strategy.AppContext") as mock_ctx:
            highest = self.strategy._get_highest_since_buy(
                "000001", "2026-07-01 10:00:00", f"{BAR_DAY} 10:30:00", 10.2
            )
        self.assertEqual(highest, 12.5)
        mock_ctx.return_value.market_api.get_day_kline.assert_not_called()

    def test_get_highest_since_buy_merges_today_high_with_cache(self) -> None:
        """缓存的是日K口径最高价，当日最高价更高时取当日值。"""
        UserStrategyCache().save_highest_price("000001", "2026-07-01", BAR_DAY, 10.2)
        with patch("strategy.strong_rebound_strategy.AppContext") as mock_ctx:
            highest = self.strategy._get_highest_since_buy(
                "000001", "2026-07-01 10:00:00", f"{BAR_DAY} 10:35:00", 11.8
            )
        self.assertEqual(highest, 11.8)
        mock_ctx.return_value.market_api.get_day_kline.assert_not_called()

    def test_get_highest_since_buy_writes_cache_after_query(self) -> None:
        """未命中缓存时查询并回写，第二次调用不再请求。"""
        with patch("strategy.strong_rebound_strategy.AppContext") as mock_ctx:
            mock_ctx.return_value.market_api.get_day_kline.return_value = [
                {"high": 12.0}, {"high": 13.5},
            ]
            first = self.strategy._get_highest_since_buy(
                "000001", "2026-07-01 10:00:00", f"{BAR_DAY} 10:30:00", 0.0
            )
            second = self.strategy._get_highest_since_buy(
                "000001", "2026-07-01 10:00:00", f"{BAR_DAY} 10:40:00", 0.0
            )
        self.assertEqual(first, 13.5)
        self.assertEqual(second, 13.5)
        self.assertEqual(mock_ctx.return_value.market_api.get_day_kline.call_count, 1)

    # ---- R1：实例化契约 ----

    def test_init_accepts_user_strategy_name(self) -> None:
        """R1：workflow 会用关键字传 user_strategy_name，子类签名必须收得住。

        少这个形参时 workflow 的 `_instantiate_strategy` 抛 TypeError 后被
        `except Exception` 兜住并返回 None，整个用户策略被静默跳过。
        """
        strategy = StrongReboundStrategy(
            user_strategy_id=USID,
            user_strategy_name="爸爸的强趋势",
            user_strategy_params=TEST_PARAMS,
        )
        self.assertEqual(strategy.user_strategy_name, "爸爸的强趋势")
        # 且必须往 super 传，否则 self.user_strategy_name 仍是空串，
        # 策略里所有 `f"{self.user_strategy_name},..."` 的原因文本会以逗号开头
        _, extra = strategy.is_match_strategy(self._frame([10.0] * 3))
        self.assertTrue(extra["reason"].startswith("爸爸的强趋势"))

    # ---- 路由与其它接口 ----

    def test_handle_minute_bar_routes_to_buy_when_selected(self) -> None:
        with patch.object(
            self.strategy, "_load_select_stocks", return_value={"000001"}
        ), patch.object(
            self.strategy,
            "check_minute_buy",
            return_value=(True, {"signal": "BUY", "current_price": 10.0}),
        ) as mock_buy, patch.object(self.strategy, "check_minute_sell") as mock_sell:
            matched, extra = self.strategy.handle_minute_bar("000001", {}, make_bar())
        self.assertTrue(matched)
        mock_buy.assert_called_once()
        mock_sell.assert_not_called()

    def test_handle_minute_bar_routes_to_sell_when_not_selected(self) -> None:
        with patch.object(
            self.strategy, "_load_select_stocks", return_value=set()
        ), patch.object(
            self.strategy, "check_minute_sell", return_value=(False, {"reason": "no"})
        ) as mock_sell:
            matched, extra = self.strategy.handle_minute_bar("000001", {}, make_bar())
        self.assertFalse(matched)
        mock_sell.assert_called_once()

    def test_handle_minute_bar_prefers_sell_when_holding(self) -> None:
        """契约：即使股票在选股列表中，只要有持仓就走卖出检查。"""
        with patch.object(
            self.strategy, "_load_select_stocks", return_value={"000001"}
        ), patch.object(
            self.strategy, "check_minute_buy", return_value=(True, {"signal": "BUY"})
        ) as mock_buy, patch.object(
            self.strategy, "check_minute_sell", return_value=(False, {"reason": "no"})
        ) as mock_sell:
            matched, extra = self.strategy.handle_minute_bar(
                "000001", {"cost_price": 10.0}, make_bar()
            )
        self.assertFalse(matched)
        mock_buy.assert_not_called()
        mock_sell.assert_called_once()

    def test_handle_minute_bar_reads_select_list_from_cache(self) -> None:
        """P2-25：选股列表按 user_strategy_id 缓存在 UserStrategyCache，命中即不请求接口。"""
        UserStrategyCache().save_select_stocks(USID, BAR_DAY, {"000001"})
        with patch.object(
            self.strategy,
            "check_minute_buy",
            return_value=(True, {"signal": "BUY", "current_price": 10.0}),
        ), patch("strategy.base_strategy.StrategySelectStockApi") as mock_api:
            matched, extra = self.strategy.handle_minute_bar("000001", {}, make_bar())
        self.assertTrue(matched)
        mock_api.assert_not_called()

    def test_handle_tick_bar_routes_by_select_list(self) -> None:
        with patch.object(
            self.strategy, "_load_select_stocks", return_value={"000001"}
        ), patch.object(
            self.strategy, "check_tick_buy", return_value=(True, {"signal": "BUY"})
        ):
            matched, extra = self.strategy.handle_tick_bar("000001", {}, {})
        self.assertTrue(matched)

        with patch.object(
            self.strategy, "_load_select_stocks", return_value=set()
        ), patch.object(
            self.strategy, "check_tick_sell", return_value=(False, {"reason": "no"})
        ):
            matched, extra = self.strategy.handle_tick_bar("000001", {}, {})
        self.assertFalse(matched)

    def test_check_tick_buy_and_sell_disabled(self) -> None:
        """内置策略未实现 tick 买卖点，恒返回 False。"""
        self.assertEqual(self.strategy.check_tick_buy("000001", {}), (False, {}))
        self.assertEqual(self.strategy.check_tick_sell("000001", {}, {}), (False, {}))

    # ---- 盘前准备 ----

    def test_before_trading_clears_user_state(self) -> None:
        self.strategy._save_score_cache("000001", "BUY", 10.0, 1, f"{BAR_DAY} 10:30:00", True)
        UserStrategyCache().save_select_stocks(USID, BAR_DAY, {"000001"})
        self.strategy._highest_prices["000001"] = 10.0
        self.strategy._save_minute_cache("000001", make_bar())

        self.strategy.before_trading("2026-07-16", ["000001"])

        self.assertEqual(self.strategy._get_score_cache("000001", "BUY", f"{BAR_DAY} 10:30:00"), {})
        self.assertIsNone(UserStrategyCache().get_select_stocks(USID, BAR_DAY))
        self.assertEqual(self.strategy._highest_prices, {})
        # 行情缓存由 workflow 按日期维护，before_trading 不动它
        self.assertEqual(len(self.strategy._get_minute_cache("000001")), 1)


if __name__ == "__main__":
    unittest.main()
