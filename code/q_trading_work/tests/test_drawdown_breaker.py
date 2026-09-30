"""账户级回撤约束测试。

语义（2026-09-29 用户定）：**账户回撤达阈值 → 卖出，不冻结开新仓**。
两条链路必须同口径：

- 回测：`BacktestEngine._drawdown_at` / `_drawdown_breached` / `_apply_drawdown_exit`
- 实盘：`StrategyWorkflow._refresh_account_drawdown`（注入策略，由策略
  `_check_sell_conditions` 计入 `risk.drawdown_score` 分值）

两者都以「账户权益 = 初始资金 + 已实现盈利」为口径（不含持仓浮动盈亏），
与 `_compute_equity_curve` 的权益曲线同源，因此回测里的阈值可以直接与实盘对照。

原实现是「回撤超阈值即停止开新仓」（账户级熔断），实测被证伪：权益按成本计价、
只在卖出结算时变动，回撤因而是离散跳跃的，只要单笔亏损可能超过阈值，
「回撤 ≤ 阈值」在数学上就不成立；且熔断后不再交易、权益不再变动，回撤也无法收窄，
等于一次触发即永久停手（实测阈值 −5 与 −10 给出完全相同的结果）。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

import pandas as pd

from backtest.backtest_engine import BacktestConfig, BacktestEngine, TradeResult
from workflow.strategy_workflow import StrategyState, StrategyWorkflow

INITIAL_CAPITAL: float = 100000.0

# 交易日序列（2026-08-03 周一 ~ 08-14 周五，跳过周末）
TRADING_DAYS: list[str] = [
    "2026-08-03",
    "2026-08-04",
    "2026-08-05",
    "2026-08-06",
    "2026-08-07",
    "2026-08-10",
    "2026-08-11",
    "2026-08-12",
    "2026-08-13",
    "2026-08-14",
]


def make_trade(
    profit_pct: float,
    buy_date: str = "2026-08-01",
    sell_date: str = "2026-08-10",
    code: str = "002050.SZ",
    buy_price_raw: float = 30.0,
) -> TradeResult:
    """构造一笔已结算交易。"""
    return TradeResult(
        code=code,
        strategy_name="成交量放大策略",
        buy_date=buy_date,
        sell_date=sell_date,
        buy_price=30.0,
        sell_price=30.0,
        profit_pct=profit_pct,
        buy_price_raw=buy_price_raw,
    )


def make_engine(max_drawdown_pct: float | None = None) -> BacktestEngine:
    return BacktestEngine(
        config=BacktestConfig(
            initial_capital=INITIAL_CAPITAL,
            position_size_pct=1.0,
            max_drawdown_pct=max_drawdown_pct,
        )
    )


def make_daily_df(code_closes: dict[str, float]) -> pd.DataFrame:
    """按 `day -> 收盘价` 构造日K（只需 create_time / close 两列）。"""
    return pd.DataFrame(
        {
            "create_time": list(code_closes.keys()),
            "close": list(code_closes.values()),
        }
    )


def make_state(
    profit: float,
    peak: float,
    threshold: float | None = -10.0,
    score: float = -6.0,
) -> StrategyState:
    """构造只带回撤判定所需字段的运行时状态。"""
    instance: Any = None
    if threshold is not None:
        instance = _FakeInstance(threshold, score)
    return StrategyState(
        strategy_id="sid",
        user_strategy_id="us1",
        strategy_name="成交量放大策略",
        class_path="strategy.volume_expansion_strategy",
        class_name="VolumeExpansionStrategy",
        initial_amount=INITIAL_CAPITAL,
        current_profit=profit,
        peak_asset=peak,
        instance=instance,
    )


class _FakeInstance:
    """只实现回撤注入接口的策略替身（避免实例化真实策略触发参数查询）。"""

    def __init__(self, threshold: float, score: float) -> None:
        self.RISK_MAX_DRAWDOWN_PCT = threshold
        self.SELL_RISK_DRAWDOWN_SCORE = score
        self.account_drawdown_pct = 0.0

    def set_account_drawdown(self, drawdown_pct: float) -> None:
        self.account_drawdown_pct = float(drawdown_pct or 0.0)


class TestDrawdownAt(unittest.TestCase):
    """回测侧回撤口径。"""

    def setUp(self) -> None:
        self.engine = make_engine()

    def test_drawdown_reflects_realised_loss(self) -> None:
        """单笔 −15% 全额仓位 → 权益 85000，自峰值 100000 回撤 −15%。"""
        dd: float | None = self.engine._drawdown_at(
            [make_trade(-15.0, sell_date="2026-08-05")], "2026-08-05"
        )
        self.assertIsNotNone(dd)
        assert dd is not None
        self.assertAlmostEqual(dd, -15.0, places=6)

    def test_unsettled_trade_does_not_count(self) -> None:
        """卖出日尚未到达的交易不计入（口径是已实现权益）。"""
        self.assertIsNone(
            self.engine._drawdown_at(
                [make_trade(-30.0, sell_date="2026-08-20")], "2026-08-11"
            )
        )

    def test_breached_boundary_is_inclusive(self) -> None:
        """恰好等于阈值即触发（判定用 <=），避免「刚好 10% 不算」的歧义。"""
        trades = [make_trade(-10.0, sell_date="2026-08-05")]
        self.assertTrue(self.engine._drawdown_breached(trades, "2026-08-11", -10.0))
        self.assertFalse(self.engine._drawdown_breached(trades, "2026-08-11", -11.0))

    def test_disabled_when_threshold_non_negative(self) -> None:
        trades = [make_trade(-50.0, sell_date="2026-08-05")]
        self.assertFalse(self.engine._drawdown_breached(trades, "2026-08-11", 0.0))
        self.assertFalse(self.engine._drawdown_breached(trades, "2026-08-11", 5.0))

    def test_resolve_prefers_config_over_strategy(self) -> None:
        """CLI 显式传值优先于策略参数。"""
        engine = make_engine(max_drawdown_pct=-5.0)
        strategy = SimpleNamespace(RISK_MAX_DRAWDOWN_PCT=-10.0)
        self.assertEqual(engine._resolve_breaker_pct(strategy), -5.0)

    def test_resolve_falls_back_to_strategy_param(self) -> None:
        strategy = SimpleNamespace(RISK_MAX_DRAWDOWN_PCT=-10.0)
        self.assertEqual(self.engine._resolve_breaker_pct(strategy), -10.0)

    def test_resolve_defaults_to_disabled(self) -> None:
        """策略未定义该参数（基类默认 0.0）时约束关闭。"""
        self.assertEqual(self.engine._resolve_breaker_pct(SimpleNamespace()), 0.0)


class TestApplyDrawdownExit(unittest.TestCase):
    """账户回撤达阈值 → 当日收盘卖出在持仓位。"""

    def setUp(self) -> None:
        self.engine = make_engine()
        # 08-06 起价格从 30 跌到 24，08-07 收盘 24
        self.df = make_daily_df(
            {
                "2026-08-03": 30.0,
                "2026-08-04": 30.0,
                "2026-08-05": 30.0,
                "2026-08-06": 28.0,
                "2026-08-07": 24.0,
                "2026-08-10": 25.0,
                "2026-08-11": 26.0,
                "2026-08-12": 27.0,
                "2026-08-13": 28.0,
                "2026-08-14": 29.0,
            }
        )
        self.daily_dfs: dict[str, pd.DataFrame] = {"002050.SZ": self.df}

    def test_exits_open_position_on_breach(self) -> None:
        """先在 08-05 结算一笔 −15%，回撤破阈值后 08-07 提前卖出在持仓位。"""
        trades: list[TradeResult] = [
            make_trade(-15.0, buy_date="2026-08-03", sell_date="2026-08-05"),
            make_trade(0.0, buy_date="2026-08-06", sell_date="2026-08-14"),
        ]
        result, exits = self.engine._apply_drawdown_exit(
            trades, self.daily_dfs, -10.0, TRADING_DAYS
        )
        self.assertEqual(exits, 1)
        # 第一笔不动（触发日前已按原规则卖出）
        self.assertEqual(result[0].sell_date, "2026-08-05")
        # 第二笔提前到 08-07 收盘卖出（08-06 为买入当日，不卖）
        self.assertEqual(result[1].sell_date, "2026-08-07")
        self.assertLess(result[1].profit_pct, 0.0)

    def test_profit_pct_recomputed_from_close(self) -> None:
        """新收益率必须由 08-07 收盘价（24.0）重算，而不是沿用原值。"""
        trades: list[TradeResult] = [
            make_trade(-15.0, buy_date="2026-08-03", sell_date="2026-08-05"),
            make_trade(50.0, buy_date="2026-08-06", sell_date="2026-08-14"),
        ]
        result, _ = self.engine._apply_drawdown_exit(
            trades, self.daily_dfs, -10.0, TRADING_DAYS
        )
        # 原始买入价 30 → 24，扣滑点手续费后仍应明显为负（原值 +50% 必须被覆盖）
        self.assertLess(result[1].profit_pct, -15.0)
        self.assertGreater(result[1].profit_pct, -25.0)

    def test_disabled_returns_original(self) -> None:
        """阈值 >= 0 时原样返回，不产生任何提前卖出。"""
        trades: list[TradeResult] = [
            make_trade(-15.0, buy_date="2026-08-03", sell_date="2026-08-05"),
            make_trade(0.0, buy_date="2026-08-06", sell_date="2026-08-14"),
        ]
        result, exits = self.engine._apply_drawdown_exit(
            trades, self.daily_dfs, 0.0, TRADING_DAYS
        )
        self.assertEqual(exits, 0)
        self.assertEqual(result[1].sell_date, "2026-08-14")

    def test_no_breach_keeps_original_schedule(self) -> None:
        """回撤未达阈值时不改动任何交易（等于该约束不存在）。"""
        trades: list[TradeResult] = [
            make_trade(-3.0, buy_date="2026-08-03", sell_date="2026-08-05"),
            make_trade(0.0, buy_date="2026-08-06", sell_date="2026-08-14"),
        ]
        result, exits = self.engine._apply_drawdown_exit(
            trades, self.daily_dfs, -10.0, TRADING_DAYS
        )
        self.assertEqual(exits, 0)
        self.assertEqual([t.sell_date for t in result], ["2026-08-05", "2026-08-14"])

    def test_does_not_sell_on_buy_date(self) -> None:
        """买入当日不卖：建仓当天开盘买入，同日收盘平仓等于空转。"""
        trades: list[TradeResult] = [
            make_trade(-15.0, buy_date="2026-08-03", sell_date="2026-08-05"),
            make_trade(0.0, buy_date="2026-08-07", sell_date="2026-08-14"),
        ]
        result, exits = self.engine._apply_drawdown_exit(
            trades, self.daily_dfs, -10.0, TRADING_DAYS
        )
        self.assertEqual(exits, 1)
        # 08-07 是买入当日 → 顺延到下一个交易日 08-10
        self.assertEqual(result[1].sell_date, "2026-08-10")


class TestWorkflowDrawdownInjection(unittest.TestCase):
    """实盘侧：把账户回撤注入策略（供卖出判定计分），不阻断买入。"""

    def setUp(self) -> None:
        self.wf = StrategyWorkflow(pool_size=2)

    def test_injects_drawdown_into_instance(self) -> None:
        state = make_state(profit=-15000.0, peak=INITIAL_CAPITAL)
        drawdown: float = self.wf._refresh_account_drawdown(state)
        self.assertAlmostEqual(drawdown, -15.0, places=6)
        self.assertAlmostEqual(state.instance.account_drawdown_pct, -15.0, places=6)

    def test_injects_zero_without_peak(self) -> None:
        """峰值未初始化（0）时注入 0，避免除零与误触发。"""
        state = make_state(profit=-50000.0, peak=0.0)
        drawdown: float = self.wf._refresh_account_drawdown(state)
        self.assertEqual(drawdown, 0.0)
        self.assertEqual(state.instance.account_drawdown_pct, 0.0)

    def test_no_instance_returns_zero(self) -> None:
        """策略实例尚未构建时返回 0（不能因为读不到参数就把策略卡死）。"""
        state = make_state(profit=-50000.0, peak=INITIAL_CAPITAL, threshold=None)
        self.assertEqual(self.wf._refresh_account_drawdown(state), 0.0)

    def test_warning_logged_once_then_rearmed(self) -> None:
        """告警只记一次；回撤回到阈值内后复位，再次触发能重新告警。"""
        state = make_state(profit=-15000.0, peak=INITIAL_CAPITAL)
        with self.assertLogs("workflow.strategy_workflow", level="WARNING") as ctx:
            self.wf._refresh_account_drawdown(state)
            self.wf._refresh_account_drawdown(state)
        self.assertEqual(len(ctx.records), 1)
        self.assertTrue(state.drawdown_breaker_noted)

        # 回撤收窄到阈值内 → 复位
        state.current_profit = -1000.0
        self.wf._refresh_account_drawdown(state)
        self.assertFalse(state.drawdown_breaker_noted)

        # 再次跌破阈值 → 重新告警
        state.current_profit = -20000.0
        with self.assertLogs("workflow.strategy_workflow", level="WARNING") as ctx2:
            self.wf._refresh_account_drawdown(state)
        self.assertEqual(len(ctx2.records), 1)

    def test_update_peak_is_monotonic(self) -> None:
        """峰值只增不减：盈利后不回退。"""
        state = make_state(profit=8000.0, peak=INITIAL_CAPITAL)
        self.wf._update_peak_asset(state)
        self.assertEqual(state.peak_asset, 108000.0)
        state.current_profit = -20000.0
        self.wf._update_peak_asset(state)
        self.assertEqual(state.peak_asset, 108000.0)


class TestBuyBlockReasonNotDrawdown(unittest.TestCase):
    """回撤不得再作为买入阻断条件（2026-09-29 语义变更的守卫）。"""

    def setUp(self) -> None:
        self.wf = StrategyWorkflow(pool_size=2)

    def test_drawdown_does_not_block_buy(self) -> None:
        state = make_state(profit=-15000.0, peak=INITIAL_CAPITAL)
        self.assertEqual(self.wf._buy_block_reason(state, {}), "")

    def test_config_limit_still_blocks(self) -> None:
        """配置级限制（max_stock_count=0）仍然生效。"""
        state = make_state(profit=-15000.0, peak=INITIAL_CAPITAL)
        state.max_stock_count = 0
        self.assertIn("max_stock_count=0", self.wf._buy_block_reason(state, {}))

    def test_position_limit_still_blocks(self) -> None:
        state = make_state(profit=-15000.0, peak=INITIAL_CAPITAL)
        state.max_stock_count = 1
        positions = {"002050.SZ": {"code": "002050.SZ"}}
        self.assertIn("已达上限", self.wf._buy_block_reason(state, positions))


if __name__ == "__main__":
    unittest.main()
