"""盘中买入门槛（2026-10-14）与 Redis 开关语义的守卫。

覆盖三块：

1. 回测侧 `BacktestEngine._buy_dip_blocked` 的判定表；
2. 回测调用点的「依然缓存」语义（被抑制也要更新 probe，否则压不住阴跌中的连续买点）；
3. `UserStrategyCache.redis_ready` 与 `redis_enabled` 的差别 —— 波段策略的 sr_points
   缓存曾经用的是后者，导致回测关闭 Redis 后照样读写实盘共用实例。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from backtest.backtest_engine import BacktestConfig, BacktestEngine
from core.user_strategy_cache import UserStrategyCache
from workflow.strategy_workflow import StrategyWorkflow

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _engine(enabled: bool = True, gap: float = 10.0) -> BacktestEngine:
    return BacktestEngine(
        config=BacktestConfig(
            buy_dip_gate_enabled=enabled,
            buy_dip_gap_minutes=gap,
        )
    )


class TestBuyDipBlocked:
    """回测侧判定表：跌破上一次买点 + 间隔不足 ⇒ 抑制。"""

    def test_disabled_by_default(self) -> None:
        """默认关闭：保持历史回测口径可比，用 --buy-dip-gate 开启。"""
        engine = BacktestEngine()
        probe = {"price": 10.5, "time": "2026-08-10 09:49:00"}
        assert not engine._buy_dip_blocked(probe, 10.2, "2026-08-10 09:52:00")

    def test_empty_probe_passes(self) -> None:
        engine = _engine()
        assert not engine._buy_dip_blocked({}, 10.2, "2026-08-10 09:52:00")

    def test_price_not_broken_passes(self) -> None:
        """价格站上上次买点（等于也算没跌破）⇒ 放行。"""
        engine = _engine()
        probe = {"price": 10.5, "time": "2026-08-10 09:49:00"}
        assert not engine._buy_dip_blocked(probe, 10.5, "2026-08-10 09:52:00")
        assert not engine._buy_dip_blocked(probe, 10.8, "2026-08-10 09:52:00")

    def test_broken_within_gap_blocks(self) -> None:
        engine = _engine()
        probe = {"price": 10.5, "time": "2026-08-10 09:49:00"}
        # 跌破 10.5，间隔 3 分钟 < 10 分钟
        assert engine._buy_dip_blocked(probe, 10.2, "2026-08-10 09:52:00")

    def test_broken_after_gap_passes(self) -> None:
        """间隔够了就放行 —— 门槛只压「短时间内的连续追跌买点」。"""
        engine = _engine()
        probe = {"price": 10.5, "time": "2026-08-10 09:49:00"}
        assert not engine._buy_dip_blocked(probe, 10.2, "2026-08-10 10:00:00")

    def test_gap_boundary_passes(self) -> None:
        """恰好等于窗口 ⇒ 放行（判定用 <）。"""
        engine = _engine()
        probe = {"price": 10.5, "time": "2026-08-10 09:49:00"}
        assert not engine._buy_dip_blocked(probe, 10.2, "2026-08-10 09:59:00")

    def test_zero_gap_disables(self) -> None:
        """窗口 <= 0 视为关闭（与 cfg 里 0 = 关的约定一致）。"""
        engine = _engine(gap=0.0)
        probe = {"price": 10.5, "time": "2026-08-10 09:49:00"}
        assert not engine._buy_dip_blocked(probe, 10.2, "2026-08-10 09:52:00")

    def test_invalid_price_passes(self) -> None:
        engine = _engine()
        probe = {"price": 0.0, "time": "2026-08-10 09:49:00"}
        assert not engine._buy_dip_blocked(probe, 10.2, "2026-08-10 09:52:00")

    def test_unparsable_time_passes(self) -> None:
        """时间解析不了时 fail-open，不能因为一条坏数据卡住所有买入。"""
        engine = _engine()
        probe = {"price": 10.5, "time": "not-a-time"}
        assert not engine._buy_dip_blocked(probe, 10.2, "2026-08-10 09:52:00")

    def test_cross_day_passes(self) -> None:
        """跨日必然超过窗口，等于自动放行（不必显式判同日）。"""
        engine = _engine()
        probe = {"price": 10.5, "time": "2026-08-10 14:57:00"}
        assert not engine._buy_dip_blocked(probe, 10.2, "2026-08-11 09:31:00")


class TestBuyDipCacheSemantics:
    """调用点必须「无论是否抑制都更新缓存」。"""

    def test_update_precedes_continue_in_source(self) -> None:
        src = (PROJECT_ROOT / "backtest" / "backtest_engine.py").read_text(
            encoding="utf-8"
        )
        assert "last_buy_probe.update(price=bar_price, time=bar_time)" in src
        # 顺序：先更新缓存，再 continue —— 反了就成了「10 分钟最多一单」，
        # 与「被抑制也刷新窗口」的语义不符。
        expected = (
            "last_buy_probe.update(price=bar_price, time=bar_time)\n"
            "                    if dip_blocked:\n"
            "                        continue"
        )
        assert expected in src

    def test_probe_updated_even_when_blocked(self) -> None:
        """用最小片段复现调用点逻辑：被抑制时缓存也要写进新价格。"""
        engine = _engine()
        probe: dict[str, float | str] = {}
        # 第一次：无历史 ⇒ 放行，缓存写入
        first_price, first_time = 10.5, "2026-08-10 09:49:00"
        blocked = engine._buy_dip_blocked(probe, first_price, first_time)
        probe.update(price=first_price, time=first_time)
        assert not blocked
        # 第二次：跌破且 3 分钟后 ⇒ 抑制，但缓存更新为本次价格
        second_price, second_time = 10.2, "2026-08-10 09:52:00"
        blocked = engine._buy_dip_blocked(probe, second_price, second_time)
        probe.update(price=second_price, time=second_time)
        assert blocked
        assert probe["price"] == second_price
        assert probe["time"] == second_time


class TestWorkflowBuyDipGate:
    """实盘 `_save_trade_signal` 的第三条过滤。"""

    @staticmethod
    def _workflow(probe: dict | None = None) -> StrategyWorkflow:
        """不跑 __init__（会连服务/MQTT），只装判定需要的最小状态。"""
        wf = StrategyWorkflow.__new__(StrategyWorkflow)
        wf.logger = logging.getLogger("test.buy_dip")
        wf._last_buy_probe = dict(probe or {})
        wf._load_signal_filter_config = lambda: (2.0, 10, 0.0)  # type: ignore[method-assign]
        # 星级门槛在买入门槛之前执行，必须给具体数值 —— 裸 MagicMock 的 __float__
        # 会被读成 1.0，把「不限」变成「至少 1 星」，改变被测路径。
        wf._notifier = mock.Mock()
        wf._notifier.get_push_min_star.return_value = 0.0
        # 避免 BaseWorkflow.__del__ 在 GC 时报缺属性
        wf._running = False
        return wf

    def test_blocks_and_refreshes_cache(self) -> None:
        key = "7:600150.SH"
        prev_dt = datetime.now() - timedelta(minutes=3)
        wf = self._workflow({key: (10.5, prev_dt)})
        result = wf._save_trade_signal(
            "1", "7", "600150.SH", {"signal": "BUY", "current_price": 10.2}
        )
        assert result is False
        # 被抑制也要缓存本次价格（「依然缓存」）
        assert wf._last_buy_probe[key][0] == 10.2

    def test_blocked_before_any_http(self) -> None:
        """被抑制时必须在访问服务端接口之前就返回 —— 否则日志里会出现无意义的查询。"""
        key = "7:600150.SH"
        wf = self._workflow(
            {key: (10.5, datetime.now() - timedelta(minutes=1))}
        )
        wf._trade_signal_api = mock.Mock()
        wf._save_trade_signal(
            "1", "7", "600150.SH", {"signal": "BUY", "current_price": 10.2}
        )
        wf._trade_signal_api.latest.assert_not_called()
        wf._trade_signal_api.add.assert_not_called()

    def test_sell_ignores_gate(self) -> None:
        """只作用于买入：卖出的判定里不写买点缓存。"""
        key = "7:600150.SH"
        wf = self._workflow({key: (10.5, datetime.now() - timedelta(minutes=1))})
        before = dict(wf._last_buy_probe)
        # 卖出会继续往下走（查询服务端），这里只断言缓存没被动过
        wf._trade_signal_api = mock.Mock()
        wf._trade_signal_api.latest.return_value = None
        try:
            wf._save_trade_signal(
                "1", "7", "600150.SH", {"signal": "SELL", "current_price": 10.2}
            )
        except Exception:  # noqa: BLE001 - 后续链路与本用例无关
            pass
        assert wf._last_buy_probe == before


class TestRedisReadySemantics:
    """`redis_ready` 必须反映「显式关闭」，`redis_enabled` 只反映配置。"""

    def test_ready_follows_explicit_disable(self) -> None:
        cache = UserStrategyCache()
        ready_before = cache.redis_ready
        enabled_before = cache.redis_enabled
        try:
            cache.set_redis_enabled(False)
            # 配置层仍然「启用」，但读写口必须关闭
            assert cache.redis_enabled == enabled_before
            assert cache.redis_ready is False
        finally:
            cache.set_redis_enabled(True)
        assert cache.redis_ready == ready_before

    def test_strategy_uses_ready_not_enabled(self) -> None:
        """源码守卫：sr_points 缓存不得再直接判 `redis_enabled`。"""
        src = (PROJECT_ROOT / "strategy" / "swing_trading_strategy.py").read_text(
            encoding="utf-8"
        )
        assert "self._cache.redis_enabled" not in src
        assert "self._cache.redis_ready" in src
