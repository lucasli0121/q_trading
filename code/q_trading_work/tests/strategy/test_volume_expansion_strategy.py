"""成交量放大策略测试。

覆盖：参数映射与校验、因子注册与实例隔离、日线选股各分支、
分钟级买入（门槛/数据不足/涨跌停/时间窗）、分钟级卖出（五项分项与阈值）、
搭配因子切换与最少 K 线数闸门。

成交量的实际取值由桩函数控制（`_volume_detail` / `_partner_score`），
这样用例只验证策略逻辑本身，不受真实行情与因子内部波动影响；
另有一个真实日线用例验证端到端返回值契约（服务端无数据时自动跳过）。
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from app_context import AppContext
from core.user_strategy_cache import UserStrategyCache
from strategy.volume_expansion_strategy import (
    DEFAULT_VOLUME_MODE,
    MODE_MOMENTUM,
    MODE_REVERSION,
    PARTNER_FACTORS,
    VolumeExpansionStrategy,
)

# 测试专用用户策略 ID：缓存 key 前缀，与其它用例/实盘数据隔离
USID: str = "test-volume-expansion-us"
BAR_DAY: str = "2026-09-25"

# 测试基线参数：显式传入可跳过三级回退中的网络查询。
# 方向固定为 momentum —— 多数用例按「放量达标即选中」的直觉编写，reversion 分支
# 由 test_is_match_strategy_reversion_mode 单独覆盖，类属性默认方向由
# test_default_volume_mode_is_reversion 断言。注意这**不等于**类属性默认值。
TEST_PARAMS: dict[str, Any] = {
    "match": {
        "volume_days": 20,
        "volume_score": 4.0,
        "partner_min_score": 0.0,
        "min_score": 5.0,
        "partner_factor": "boll_sr",
        "volume_mode": MODE_MOMENTUM,
        "quiet_volume_score": 4.0,
    },
    "buy": {
        "min_minutes": 20,
        "volume_score": 4.0,
        "partner_min_score": 0.0,
        "min_score": 5.0,
    },
    "sell": {"min_minutes": 20, "max_score": -6.0, "drawdown_pct": -6.0},
}


def make_bar(**overrides: Any) -> dict[str, Any]:
    """构造一根分钟行情（默认 10:30，避开 9:40/9:35 的时间窗过滤）。"""
    bar: dict[str, Any] = {
        "code": "002050.SZ",
        "name": "三花智控",
        "price": 30.0,
        "open": 30.0,
        "preclose": 30.0,
        "high": 30.5,
        "low": 29.5,
        "close": 30.0,
        "volume": 10000,
        "amount": 300_000.0,
        "create_time": f"{BAR_DAY} 10:30:00",
    }
    bar.update(overrides)
    return bar


def make_window(n: int = 40, code: str = "002050.SZ") -> list[dict[str, Any]]:
    """构造日线窗口（is_match_strategy 的输入）。"""
    rows: list[dict[str, Any]] = []
    for i in range(n):
        price: float = 30.0 + i * 0.1
        rows.append(
            {
                "code": code,
                "open": price,
                "close": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "volume": 100000 + i * 1000,
                "amount": price * 100000,
                "create_time": f"2026-08-{i % 28 + 1:02d} 00:00:00",
            }
        )
    return rows


class TestVolumeExpansionStrategy(unittest.TestCase):
    """成交量放大策略单元测试。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.strategy = VolumeExpansionStrategy(
            user_strategy_id=USID, user_strategy_params=TEST_PARAMS
        )

    def setUp(self) -> None:
        cache = UserStrategyCache()
        cache.set_redis_enabled(False)  # 单元测试不触碰真实 Redis
        cache.reset_redis_failure()
        cache.clear_user(USID)
        self._purge_minute_cache()
        with cache._lock:
            cache._highest_price_snapshot.clear()

    def tearDown(self) -> None:
        UserStrategyCache().set_redis_enabled(True)

    # ---- 测试辅助 ----

    @staticmethod
    def _purge_minute_cache() -> None:
        cache = UserStrategyCache()
        with cache._lock:
            cache._minute_snapshot.clear()
            cache._last_day = ""

    def _feed_minutes(self, count: int, code: str = "002050.SZ", **overrides: Any) -> None:
        """往分钟缓存灌 `count` 根行情（缓存写方是 workflow，测试里直接写）。"""
        cache = UserStrategyCache()
        for i in range(count):
            bar = make_bar(
                code=code,
                create_time=f"{BAR_DAY} 10:{i:02d}:00",
                **overrides,
            )
            cache.push_minute_bar(code, bar)

    # ---- 参数映射与校验 ----

    def test_apply_params_maps_nested_config(self) -> None:
        strategy = VolumeExpansionStrategy(
            user_strategy_id=USID,
            user_strategy_params={
                "match": {"volume_days": 30, "partner_factor": "kline_sr"},
                "sell": {"drawdown_pct": -9.0},
            },
        )
        self.assertEqual(strategy.VOLUME_DAYS, 30)
        self.assertEqual(strategy.MATCH_PARTNER_FACTOR, "kline_sr")
        self.assertEqual(strategy.SELL_DRAWDOWN_PCT, -9.0)
        # 未覆盖字段保留类属性默认值
        self.assertEqual(strategy.BUY_VOLUME_SCORE, VolumeExpansionStrategy.BUY_VOLUME_SCORE)

    def test_apply_params_unwraps_desc_value(self) -> None:
        """strategy.default_params 的 {desc, value} 包装格式应被解包后生效。"""
        strategy = VolumeExpansionStrategy(
            user_strategy_id=USID,
            user_strategy_params={"match": {"volume_score": {"desc": "成交量门槛", "value": 7.5}}},
        )
        self.assertEqual(strategy.MATCH_VOLUME_SCORE, 7.5)

    def test_invalid_partner_factor_falls_back(self) -> None:
        """搭配因子名写错时必须回退到默认值，不能静默留空。"""
        with self.assertLogs("strategy.volume_expansion_strategy", level="WARNING"):
            strategy = VolumeExpansionStrategy(
                user_strategy_id=USID,
                user_strategy_params={"match": {"partner_factor": "not_exists"}},
            )
        self.assertEqual(strategy.MATCH_PARTNER_FACTOR, "boll_sr")

    def test_default_volume_mode_is_reversion(self) -> None:
        """类属性默认方向 = 实测方向 reversion（回测依据见模块 docstring）。"""
        self.assertEqual(DEFAULT_VOLUME_MODE, MODE_REVERSION)
        self.assertEqual(VolumeExpansionStrategy.MATCH_VOLUME_MODE, DEFAULT_VOLUME_MODE)

    def test_invalid_volume_mode_falls_back(self) -> None:
        """非法方向必须回退到默认值，且与默认方向同源（不能各写一遍）。"""
        with self.assertLogs("strategy.volume_expansion_strategy", level="WARNING"):
            strategy = VolumeExpansionStrategy(
                user_strategy_id=USID,
                user_strategy_params={"match": {"volume_mode": "whatever"}},
            )
        self.assertEqual(strategy.MATCH_VOLUME_MODE, DEFAULT_VOLUME_MODE)

    def test_buy_side_empty_follows_match(self) -> None:
        """买入侧搭配因子/模式为空时跟随日线配置（不是空串）。"""
        strategy = VolumeExpansionStrategy(
            user_strategy_id=USID,
            user_strategy_params={
                "match": {"partner_factor": "ma", "volume_mode": MODE_REVERSION}
            },
        )
        self.assertEqual(strategy._partner_name(True), "ma")
        self.assertEqual(strategy.BUY_VOLUME_MODE or strategy.MATCH_VOLUME_MODE, MODE_REVERSION)

    def test_min_score_shared_between_match_and_buy(self) -> None:
        """min_score 在 match / buy 两节同名，buy 节存在时以其为准。"""
        strategy = VolumeExpansionStrategy(
            user_strategy_id=USID,
            user_strategy_params={"match": {"min_score": 5.0}, "buy": {"min_score": 7.0}},
        )
        self.assertEqual(strategy.MIN_SCORE, 7.0)

    def test_risk_max_drawdown_param(self) -> None:
        """风控参数取自 risk 节；类属性默认 -10.0（账户级回撤触发卖出）。"""
        self.assertEqual(VolumeExpansionStrategy.RISK_MAX_DRAWDOWN_PCT, -10.0)
        strategy = VolumeExpansionStrategy(
            user_strategy_id=USID,
            user_strategy_params={"risk": {"max_drawdown_pct": -5.0}},
        )
        self.assertEqual(strategy.RISK_MAX_DRAWDOWN_PCT, -5.0)

    def test_risk_drawdown_score_param(self) -> None:
        """回撤分值取自 risk.drawdown_score，默认 -6.0（与 sell.max_score 等值）。"""
        self.assertEqual(VolumeExpansionStrategy.SELL_RISK_DRAWDOWN_SCORE, -6.0)
        strategy = VolumeExpansionStrategy(
            user_strategy_id=USID,
            user_strategy_params={"risk": {"drawdown_score": -3.0}},
        )
        self.assertEqual(strategy.SELL_RISK_DRAWDOWN_SCORE, -3.0)

    def test_risk_disabled_by_zero(self) -> None:
        """0 表示关闭回撤约束（与回测引擎 _resolve_breaker_pct 的语义一致）。"""
        strategy = VolumeExpansionStrategy(
            user_strategy_id=USID,
            user_strategy_params={"risk": {"max_drawdown_pct": 0}},
        )
        self.assertEqual(strategy.RISK_MAX_DRAWDOWN_PCT, 0.0)

    # ---- 账户回撤卖出分值（risk.drawdown_score）----

    def test_drawdown_score_zero_without_injection(self) -> None:
        """未注入回撤时为 0，不改变原有卖出行为。"""
        strategy = VolumeExpansionStrategy(user_strategy_id=USID, user_strategy_params=TEST_PARAMS)
        self.assertEqual(strategy.account_drawdown_pct, 0.0)
        self.assertEqual(strategy._risk_drawdown_score(), 0.0)

    def test_drawdown_score_hits_at_threshold(self) -> None:
        """回撤达阈值即给分（判定用 <=，与回测 _drawdown_breached 一致）。"""
        strategy = VolumeExpansionStrategy(user_strategy_id=USID, user_strategy_params=TEST_PARAMS)
        strategy.set_account_drawdown(-10.0)
        self.assertEqual(strategy._risk_drawdown_score(), -6.0)
        strategy.set_account_drawdown(-12.5)
        self.assertEqual(strategy._risk_drawdown_score(), -6.0)

    def test_drawdown_score_zero_within_threshold(self) -> None:
        strategy = VolumeExpansionStrategy(user_strategy_id=USID, user_strategy_params=TEST_PARAMS)
        strategy.set_account_drawdown(-9.9)
        self.assertEqual(strategy._risk_drawdown_score(), 0.0)

    def test_drawdown_score_zero_when_disabled(self) -> None:
        strategy = VolumeExpansionStrategy(
            user_strategy_id=USID,
            user_strategy_params={"risk": {"max_drawdown_pct": 0.0}},
        )
        strategy.set_account_drawdown(-50.0)
        self.assertEqual(strategy._risk_drawdown_score(), 0.0)

    # ---- 因子注册 ----

    def test_factor_manager_is_instance_private(self) -> None:
        """P0-5：因子管理器必须实例私有，否则多用户会互相覆盖同名因子。"""
        a = VolumeExpansionStrategy(user_strategy_id="us-a", user_strategy_params=TEST_PARAMS)
        b = VolumeExpansionStrategy(user_strategy_id="us-b", user_strategy_params=TEST_PARAMS)
        self.assertIsNot(a.factor_manager, b.factor_manager)

    def test_init_factors_registers_volume_and_partner(self) -> None:
        from factor.boll_support_resistance import BollSRFactor
        from factor.volume_expansion_factor import VolumeExpansionFactor

        self.assertIsInstance(
            self.strategy.factor_manager.get(VolumeExpansionFactor.factor_name),
            VolumeExpansionFactor,
        )
        self.assertIsInstance(
            self.strategy.factor_manager.get(BollSRFactor.factor_name), BollSRFactor
        )

    def test_partner_registry_entries_constructible(self) -> None:
        """注册表里每个搭配因子都必须能用当前窗口构造并打出 float 分。"""
        import pandas as pd

        df = pd.DataFrame(make_window(60))
        df["price"] = df["close"]
        for name in PARTNER_FACTORS:
            strategy = VolumeExpansionStrategy(
                user_strategy_id=USID,
                user_strategy_params={"match": {"partner_factor": name, "volume_days": 20}},
            )
            score = strategy._partner_score(df, False)
            self.assertIsInstance(score, float, f"搭配因子 {name} 未返回 float")

    def test_partner_min_bars_adx_double_period(self) -> None:
        """ADX 的 lookback = 2×period，最少 K 线数必须按 2 倍算。"""
        strategy = VolumeExpansionStrategy(
            user_strategy_id=USID,
            user_strategy_params={"match": {"partner_factor": "adx", "volume_days": 20}},
        )
        self.assertEqual(strategy._partner_min_bars(False), 40)
        # 窗口不足时必须返回 None（而不是被 talib 的 NaN 判成负分）
        import pandas as pd

        short = pd.DataFrame(make_window(30))
        self.assertIsNone(strategy._partner_score(short, False))

    # ---- is_match_strategy ----

    def test_is_match_strategy_insufficient_data(self) -> None:
        matched, extra = self.strategy.is_match_strategy([])
        self.assertFalse(matched)
        self.assertIn("数据不足", extra["reason"])

        matched, extra = self.strategy.is_match_strategy(make_window(5))
        self.assertFalse(matched)
        self.assertIn("数据不足", extra["reason"])

    def test_is_match_strategy_no_code(self) -> None:
        rows = make_window(40)
        for row in rows:
            row.pop("code", None)
        matched, extra = self.strategy.is_match_strategy(rows)
        self.assertFalse(matched)
        self.assertIn("无法获取股票代码", extra["reason"])

    def test_is_match_strategy_buy_branch(self) -> None:
        """成交量与搭配都达标 → 选中，且 score/star 在契约范围内。"""
        with patch.object(
            self.strategy, "_volume_detail", return_value={"score": 6.0, "reason": []}
        ), patch.object(self.strategy, "_partner_score", return_value=5.0):
            matched, extra = self.strategy.is_match_strategy(make_window(40))
        self.assertTrue(matched)
        self.assertEqual(extra["signal"], "BUY")
        self.assertEqual(extra["code"], "002050.SZ")
        self.assertGreaterEqual(extra["score"], self.strategy.MIN_SCORE)
        self.assertLessEqual(extra["score"], 10.0)
        self.assertGreaterEqual(extra["star"], 0.0)
        self.assertLessEqual(extra["star"], 5.0)
        self.assertIn("选中", extra["reason"])

    def test_is_match_strategy_rejects_low_volume(self) -> None:
        with patch.object(
            self.strategy, "_volume_detail", return_value={"score": 2.0, "reason": []}
        ), patch.object(self.strategy, "_partner_score", return_value=10.0):
            matched, extra = self.strategy.is_match_strategy(make_window(40))
        self.assertFalse(matched)
        self.assertIn("未选中", extra["reason"])

    def test_is_match_strategy_rejects_missing_partner(self) -> None:
        """搭配因子算不出来（None）时必须判不中，不能当成中性放行。"""
        with patch.object(
            self.strategy, "_volume_detail", return_value={"score": 8.0, "reason": []}
        ), patch.object(self.strategy, "_partner_score", return_value=None):
            matched, extra = self.strategy.is_match_strategy(make_window(40))
        self.assertFalse(matched)
        self.assertIn("数据不足", extra["reason"])

    def test_is_match_strategy_rejects_negative_partner(self) -> None:
        """搭配因子转负（价格在阻力区）时不买入。"""
        with patch.object(
            self.strategy, "_volume_detail", return_value={"score": 8.0, "reason": []}
        ), patch.object(self.strategy, "_partner_score", return_value=-7.5):
            matched, extra = self.strategy.is_match_strategy(make_window(40))
        self.assertFalse(matched)
        self.assertIn("未选中", extra["reason"])

    def test_is_match_strategy_reversion_mode(self) -> None:
        """reversion 模式：缩量才允许买，放量一律不买。"""
        strategy = VolumeExpansionStrategy(
            user_strategy_id=USID,
            user_strategy_params={
                "match": {"volume_mode": MODE_REVERSION, "quiet_volume_score": 4.0}
            },
        )
        with patch.object(strategy, "_partner_score", return_value=5.0):
            with patch.object(
                strategy, "_volume_detail", return_value={"score": 0.0, "reason": []}
            ):
                matched_quiet, _ = strategy.is_match_strategy(make_window(40))
            with patch.object(
                strategy, "_volume_detail", return_value={"score": 6.0, "reason": []}
            ):
                matched_loud, extra_loud = strategy.is_match_strategy(make_window(40))
        self.assertTrue(matched_quiet)
        self.assertFalse(matched_loud)
        self.assertIn("reversion", extra_loud["reason"])

    def test_is_match_strategy_with_real_daily_data(self) -> None:
        """端到端契约：真实日线数据下返回值类型与字段完整（无数据则跳过）。"""
        rows: list[dict[str, Any]] = []
        for start, end in (
            ("2026-06-01", "2026-09-24"),
            ("2026-05-01", "2026-09-24"),
        ):
            try:
                rows = AppContext().market_api.get_day_kline(
                    code="002050.SZ", start=start, end=end
                )
            except Exception:
                rows = []
            if rows and len(rows) >= 40:
                break
        if not rows or len(rows) < 40:
            self.skipTest("服务端未返回足够日线数据")
        matched, extra = self.strategy.is_match_strategy(rows)
        self.assertIsInstance(matched, bool)
        self.assertIsInstance(extra, dict)
        self.assertIn("volume_score", extra)
        self.assertIn("partner_score", extra)
        if matched:
            self.assertEqual(extra["signal"], "BUY")
        else:
            self.assertIn("reason", extra)

    # ---- 分钟级买入 ----

    def test_buy_rejects_insufficient_minutes(self) -> None:
        self._feed_minutes(10)
        matched, extra = self.strategy._check_buy_conditions("002050.SZ", make_bar())
        self.assertFalse(matched)
        self.assertIn("分钟数据不足", extra["reason"])

    def test_buy_before_open_window_rejected(self) -> None:
        self._feed_minutes(25)
        bar = make_bar(create_time=f"{BAR_DAY} 09:35:00")
        matched, extra = self.strategy._check_buy_conditions("002050.SZ", bar)
        self.assertFalse(matched)
        self.assertIn("9:40", extra["reason"])

    def test_buy_near_limit_rejected(self) -> None:
        self._feed_minutes(25)
        bar = make_bar(price=33.0, preclose=30.0)  # 主板 +10% → 接近涨停
        matched, extra = self.strategy._check_buy_conditions("002050.SZ", bar)
        self.assertFalse(matched)
        self.assertIn("接近涨停或跌停", extra["reason"])

    def test_buy_abnormal_price_rejected(self) -> None:
        self._feed_minutes(25)
        matched, extra = self.strategy._check_buy_conditions(
            "002050.SZ", make_bar(price=0.0)
        )
        self.assertFalse(matched)
        self.assertIn("当前价格异常", extra["reason"])

    def test_buy_branch_signal_contract(self) -> None:
        """两道门槛都通过 → BUY，且 extra 满足 workflow 的字段契约。"""
        self._feed_minutes(25)
        with patch.object(
            self.strategy, "_volume_detail", return_value={"score": 6.5, "reason": []}
        ), patch.object(self.strategy, "_partner_score", return_value=7.5):
            matched, extra = self.strategy._check_buy_conditions("002050.SZ", make_bar())
        self.assertTrue(matched)
        self.assertEqual(extra["signal"], "BUY")
        self.assertEqual(extra["code"], "002050.SZ")
        self.assertEqual(extra["current_price"], 30.0)
        self.assertGreaterEqual(extra["score"], self.strategy.MIN_SCORE)
        self.assertLessEqual(extra["score"], 10.0)
        self.assertGreaterEqual(extra["star"], 0.0)
        self.assertLessEqual(extra["star"], 5.0)
        self.assertIn("买入条件合格", extra["reason"])

    def test_buy_requires_partner_confirmation(self) -> None:
        """成交量达标但搭配因子给负分 → 不买（搭配因子是否决项）。"""
        self._feed_minutes(25)
        with patch.object(
            self.strategy, "_volume_detail", return_value={"score": 10.0, "reason": []}
        ), patch.object(self.strategy, "_partner_score", return_value=-10.0):
            matched, extra = self.strategy._check_buy_conditions("002050.SZ", make_bar())
        self.assertFalse(matched)
        self.assertIn("买入条件未满足", extra["reason"])

    def test_buy_adx_partner_waits_for_double_period(self) -> None:
        """搭配因子选 ADX 时，30 根分钟数据不满足 2×period=40，必须继续等待。"""
        strategy = VolumeExpansionStrategy(
            user_strategy_id=USID,
            user_strategy_params={"match": {"partner_factor": "adx", "volume_days": 20}},
        )
        self._feed_minutes(30)
        matched, extra = strategy._check_buy_conditions("002050.SZ", make_bar())
        self.assertFalse(matched)
        self.assertIn("分钟数据不足", extra["reason"])
        self.assertIn("40", extra["reason"])

    def test_buy_uses_minute_cache(self) -> None:
        """分钟行情来自缓存（写方是 workflow），策略侧不自行取数。"""
        with patch.object(
            self.strategy, "_get_minute_cache", return_value=[]
        ) as mocked, patch.object(
            self.strategy, "_volume_detail", return_value={"score": 9.0, "reason": []}
        ), patch.object(self.strategy, "_partner_score", return_value=9.0):
            matched, _ = self.strategy._check_buy_conditions("002050.SZ", make_bar())
        self.assertFalse(matched)
        mocked.assert_called_once_with("002050.SZ")

    # ---- 分钟级卖出 ----

    def test_sell_triggers_on_volume_down(self) -> None:
        """放量下跌（成交量分转负）+ 破支撑 → 触发卖出，score/star 符号正确。"""
        self._feed_minutes(25)
        with patch.object(
            self.strategy,
            "_volume_detail",
            return_value={"score": -4.0, "vol_ratio": 2.0, "reason": []},
        ), patch.object(
            self.strategy, "_partner_score", return_value=-5.0
        ), patch.object(
            self.strategy, "_vwap_deviation", return_value=-1.5
        ), patch.object(
            self.strategy, "_resolve_highest_price", return_value=30.0
        ):
            matched, extra = self.strategy._check_sell_conditions(
                "002050.SZ", {}, make_bar()
            )
        self.assertTrue(matched)
        self.assertEqual(extra["signal"], "SELL")
        self.assertLessEqual(extra["score"], self.strategy.SELL_MAX_SCORE)
        self.assertGreaterEqual(extra["star"], 0.0)
        self.assertLessEqual(extra["star"], 5.0)
        self.assertIn("放量下跌", extra["reason"])
        self.assertIn("破支撑", extra["reason"])

    def test_sell_triggers_on_drawdown(self) -> None:
        """其它分项都不成立时，仅回撤超限也应触发卖出。"""
        self._feed_minutes(25)
        with patch.object(
            self.strategy,
            "_volume_detail",
            return_value={"score": 0.0, "vol_ratio": 1.0, "reason": []},
        ), patch.object(
            self.strategy, "_partner_score", return_value=0.0
        ), patch.object(
            self.strategy, "_vwap_deviation", return_value=0.0
        ), patch.object(
            self.strategy, "_resolve_highest_price", return_value=35.0
        ):
            matched, extra = self.strategy._check_sell_conditions(
                "002050.SZ", {}, make_bar(price=30.0)
            )
        self.assertTrue(matched)
        self.assertIn("回撤", extra["reason"])
        self.assertAlmostEqual(extra["drawdown"], -14.29, places=1)

    def test_sell_marks_divergence(self) -> None:
        """缩量上涨（量比 < 1 且当根收红）应计入量价背离。"""
        self._feed_minutes(25)
        # 背离的判定读的是分钟缓存里最后两根 close（不是传入的 stock_data），
        # 而 make_bar 默认 close 恒定 30.0，会让「收红」恒不成立。
        # 这里补一根收盘抬高的 K 线，构造出真实的「当根收红」。
        UserStrategyCache().push_minute_bar(
            "002050.SZ",
            make_bar(create_time=f"{BAR_DAY} 10:25:00", close=30.5),
        )
        bar = make_bar(price=30.2, close=30.2)
        with patch.object(
            self.strategy,
            "_volume_detail",
            return_value={"score": 0.0, "vol_ratio": 0.8, "reason": []},
        ), patch.object(
            self.strategy, "_partner_score", return_value=0.0
        ), patch.object(
            self.strategy, "_vwap_deviation", return_value=0.5
        ), patch.object(
            self.strategy, "_resolve_highest_price", return_value=0.0
        ):
            matched, extra = self.strategy._check_sell_conditions("002050.SZ", {}, bar)
        self.assertFalse(matched)
        self.assertIn("缩量上涨", extra["reason"])

    def test_sell_before_0935_rejected(self) -> None:
        self._feed_minutes(25)
        bar = make_bar(create_time=f"{BAR_DAY} 09:34:00")
        matched, extra = self.strategy._check_sell_conditions("002050.SZ", {}, bar)
        self.assertFalse(matched)
        self.assertIn("9:35", extra["reason"])

    def test_sell_near_limit_rejected(self) -> None:
        self._feed_minutes(25)
        bar = make_bar(price=27.0, preclose=30.0)  # 主板 −10%
        matched, extra = self.strategy._check_sell_conditions("002050.SZ", {}, bar)
        self.assertFalse(matched)
        self.assertIn("接近涨停或跌停", extra["reason"])

    def test_sell_no_trigger_when_score_above_threshold(self) -> None:
        self._feed_minutes(25)
        with patch.object(
            self.strategy,
            "_volume_detail",
            return_value={"score": 2.0, "vol_ratio": 1.2, "reason": []},
        ), patch.object(
            self.strategy, "_partner_score", return_value=5.0
        ), patch.object(
            self.strategy, "_vwap_deviation", return_value=1.0
        ), patch.object(
            self.strategy, "_resolve_highest_price", return_value=30.0
        ):
            matched, extra = self.strategy._check_sell_conditions("002050.SZ", {}, make_bar())
        self.assertFalse(matched)
        self.assertIn("未达阈值", extra["reason"])

    def test_sell_counts_account_drawdown_score(self) -> None:
        """账户回撤达阈值时计入 risk.drawdown_score：单独一条即可触发 SELL。"""
        strategy = VolumeExpansionStrategy(
            user_strategy_id=USID, user_strategy_params=TEST_PARAMS
        )
        strategy.set_account_drawdown(-12.0)
        self._feed_minutes(25)
        with patch.object(
            strategy,
            "_volume_detail",
            return_value={"score": 0.0, "vol_ratio": 1.5, "reason": []},
        ), patch.object(
            strategy, "_partner_score", return_value=5.0
        ), patch.object(
            strategy, "_vwap_deviation", return_value=1.0
        ), patch.object(
            strategy, "_resolve_highest_price", return_value=30.0
        ):
            matched, extra = strategy._check_sell_conditions("002050.SZ", {}, make_bar())
        self.assertTrue(matched)
        self.assertEqual(extra["signal"], "SELL")
        self.assertIn("账户回撤", extra["reason"])
        self.assertEqual(extra["account_drawdown"], -12.0)

    def test_sell_ignores_account_drawdown_within_threshold(self) -> None:
        """回撤未达阈值时不计分（-9.9% 对 -10% 阈值不触发）。"""
        strategy = VolumeExpansionStrategy(
            user_strategy_id=USID, user_strategy_params=TEST_PARAMS
        )
        strategy.set_account_drawdown(-9.9)
        self._feed_minutes(25)
        with patch.object(
            strategy,
            "_volume_detail",
            return_value={"score": 0.0, "vol_ratio": 1.5, "reason": []},
        ), patch.object(
            strategy, "_partner_score", return_value=5.0
        ), patch.object(
            strategy, "_vwap_deviation", return_value=1.0
        ), patch.object(
            strategy, "_resolve_highest_price", return_value=30.0
        ):
            matched, extra = strategy._check_sell_conditions("002050.SZ", {}, make_bar())
        self.assertFalse(matched)
        self.assertNotIn("账户回撤", extra["reason"])

    def test_sell_position_buy_time_used(self) -> None:
        """有持仓信息时用真实 buy_time 作为回撤基准（不被最近 2 日覆盖）。"""
        self._feed_minutes(25)
        with patch.object(
            self.strategy,
            "_resolve_highest_price",
            return_value=30.0,
        ) as mocked:
            self.strategy._check_sell_conditions(
                "002050.SZ",
                {"cost_price": 20.0, "buy_time": "2026-09-01 09:35:00"},
                make_bar(),
            )
        self.assertEqual(mocked.call_args[0][1][:10], "2026-09-01")


if __name__ == "__main__":
    unittest.main()
