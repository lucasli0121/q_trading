"""买入信号价止损（2026-10-14 替换「自持仓最高点回撤」）的守卫。

为什么替换：实时链路按设计约定 D-1 恒传空持仓，卖出侧拿不到真实买入日 ⇒
`_sell_baseline_buy_time` 回退到「最近 2 个交易日」，`_resolve_highest_price`
随之把「持仓期间最高价」算成「最近 2 个交易日最高价」。买「缩量回调」类标的时，
该股此刻往往已经比前两日高点低 6% 以上 ⇒ **买入瞬间就接近卖出条件**。
实测 2026-10-09 的 605499 / 600875 连续 6 笔卖出理由里的回撤值全是 −11.79%，
一字不差（共用同一个缓存峰值）。

新语义：连续 3 个分钟 bar 现价低于**本次建仓的买入信号价** ⇒ 直接卖出（硬止损）。

本文件守住四件事：
1. 判定表：连续计数、回到基准上方清零、无基准 / 开关关闭时 fail-open；
2. `handle_minute_bar` 的记录与清除时序（买入记录、持仓期间不覆盖、卖出清除）；
3. 基准必须**跨日存活** —— 顺手清掉它会让隔夜跳空失去止损保护；
4. 两个策略的卖出路径不再调用 `_resolve_highest_price`（源码级守卫）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from core.user_strategy_cache import UserStrategyCache
from strategy.swing_trading_strategy import SwingTradingStrategy

PROJECT_ROOT = Path(__file__).resolve().parents[1]
USID: str = "test-buy-signal-stop"


def _strategy(tag: str = USID) -> SwingTradingStrategy:
    """构造只走本地逻辑的波段策略实例（参数全空 ⇒ 取类属性默认值）。

    选波段策略承载判定表用例：它没有成交量策略那套「分钟窗口必须够长」的
    前置闸门，`_buy_signal_break_stop` 又是基类方法，两个策略共用同一份实现。
    """
    return SwingTradingStrategy(
        user_strategy_id=tag,
        user_strategy_params={"match": {}, "buy": {}, "sell": {}, "risk": {}},
    )


def _bar(price: float = 10.0, code: str = "000001") -> dict[str, Any]:
    return {
        "code": code,
        "name": "",
        "price": price,
        "open": price,
        "preclose": 10.0,
        "high": price,
        "low": price,
        "create_time": "2026-07-15 10:30:00",
    }


class TestBreakStopTable:
    """基类 `_buy_signal_break_stop` 的判定表。"""

    def test_counts_until_threshold(self) -> None:
        strategy = _strategy()
        strategy.set_buy_signal_price("000001", 10.0)
        ok, detail = strategy._buy_signal_break_stop("000001", 9.9)
        assert ok is False
        assert detail["break_count"] == 1
        ok, detail = strategy._buy_signal_break_stop("000001", 9.9)
        assert ok is False
        assert detail["break_count"] == 2
        ok, detail = strategy._buy_signal_break_stop("000001", 9.9)
        assert ok is True
        assert detail["break_count"] == 3
        assert detail["buy_signal_price"] == 10.0

    def test_equal_to_baseline_resets_count(self) -> None:
        """等于买入信号价不算「低于」⇒ 计数清零，明细为空。"""
        strategy = _strategy()
        strategy.set_buy_signal_price("000001", 10.0)
        strategy._buy_signal_break_stop("000001", 9.9)
        strategy._buy_signal_break_stop("000001", 9.9)
        ok, detail = strategy._buy_signal_break_stop("000001", 10.0)
        assert ok is False
        assert detail == {}
        # 清零后重新计数
        ok, detail = strategy._buy_signal_break_stop("000001", 9.9)
        assert ok is False
        assert detail["break_count"] == 1

    def test_no_baseline_is_fail_open(self) -> None:
        """从未记录过买入信号价 ⇒ 不判定，绝不误卖。"""
        strategy = _strategy()
        for _ in range(5):
            ok, detail = strategy._buy_signal_break_stop("000001", 1.0)
        assert ok is False
        assert detail == {}

    def test_invalid_price_is_fail_open(self) -> None:
        strategy = _strategy()
        strategy.set_buy_signal_price("000001", 10.0)
        assert strategy._buy_signal_break_stop("000001", 0.0) == (False, {})
        assert strategy._buy_signal_break_stop("000001", -1.0) == (False, {})

    def test_disabled_switch(self) -> None:
        strategy = _strategy()
        strategy.SELL_BUY_PRICE_BREAK_ENABLED = False
        strategy.set_buy_signal_price("000001", 10.0)
        for _ in range(5):
            assert strategy._buy_signal_break_stop("000001", 9.0)[0] is False

    def test_count_is_configurable(self) -> None:
        strategy = _strategy()
        strategy.SELL_BUY_PRICE_BREAK_COUNT = 2
        strategy.set_buy_signal_price("000001", 10.0)
        assert strategy._buy_signal_break_stop("000001", 9.9)[0] is False
        assert strategy._buy_signal_break_stop("000001", 9.9)[0] is True

    def test_counts_are_per_code(self) -> None:
        """计数按股票隔离 —— 共用计数器会让 A 股的跌破次数给 B 股止损。"""
        strategy = _strategy()
        strategy.set_buy_signal_price("000001", 10.0)
        strategy.set_buy_signal_price("600000", 20.0)
        strategy._buy_signal_break_stop("000001", 9.9)
        strategy._buy_signal_break_stop("000001", 9.9)
        ok, detail = strategy._buy_signal_break_stop("600000", 19.9)
        assert ok is False
        assert detail["break_count"] == 1

    def test_clear_removes_both(self) -> None:
        strategy = _strategy()
        strategy.set_buy_signal_price("000001", 10.0)
        strategy._buy_signal_break_stop("000001", 9.9)
        strategy._clear_buy_signal_price("000001")
        assert strategy._buy_signal_break_stop("000001", 9.9) == (False, {})
        assert strategy._below_buy_signal_counts.get("000001") in (None, 0)


class TestRecordSemantics:
    """记录时机：只在无基准时写。"""

    def test_record_only_once(self) -> None:
        """持仓期间仍会判出买入信号，基准不得被新买点改写。

        「缩量回调」类买点出现在下跌途中，若每次都覆盖，基准会被越压越低，
        止损随之失效。
        """
        strategy = _strategy()
        strategy._record_buy_signal_price("000001", {"price": 10.0})
        strategy._record_buy_signal_price("000001", {"price": 8.0})
        assert strategy._buy_signal_prices["000001"] == 10.0

    def test_record_falls_back_to_close(self) -> None:
        strategy = _strategy()
        strategy._record_buy_signal_price("000001", {"close": 12.5})
        assert strategy._buy_signal_prices["000001"] == 12.5

    def test_record_ignores_invalid(self) -> None:
        strategy = _strategy()
        strategy._record_buy_signal_price("000001", {"price": 0.0})
        strategy._record_buy_signal_price("000002", {"price": "abc"})
        assert "000001" not in strategy._buy_signal_prices
        assert "000002" not in strategy._buy_signal_prices

    def test_external_set_overrides(self) -> None:
        """`set_buy_signal_price` 是给重启后回填留的入口，允许覆盖并重置计数。"""
        strategy = _strategy()
        strategy._record_buy_signal_price("000001", {"price": 10.0})
        strategy._buy_signal_break_stop("000001", 9.9)
        strategy.set_buy_signal_price("000001", 9.5)
        assert strategy._buy_signal_prices["000001"] == 9.5
        assert strategy._below_buy_signal_counts["000001"] == 0


class TestHandleMinuteBarWiring:
    """`handle_minute_bar` 的记录与清除时序。

    波段策略**重写**了 `handle_minute_bar`（为了做 T 的买卖顺序），基类那次改动
    不会自动覆盖它 —— 所以这里用 swing 的真实链路验证，而不是拿基类版本代替。
    """

    @staticmethod
    def _buy_bar(strategy: SwingTradingStrategy, price: float) -> None:
        with patch.object(strategy, "_before_minute_route"), patch.object(
            strategy, "_check_buy_conditions", return_value=(True, {"signal": "BUY"})
        ):
            strategy.handle_minute_bar("000001", {}, _bar(price))

    @staticmethod
    def _sell_bar(strategy: SwingTradingStrategy) -> None:
        with patch.object(strategy, "_before_minute_route"), patch.object(
            strategy, "_break_max_r", {"000001": True}
        ), patch.object(
            strategy, "_check_sell_conditions", return_value=(True, {"signal": "SELL"})
        ):
            strategy.handle_minute_bar("000001", {}, _bar())

    def test_buy_records_then_sell_clears(self) -> None:
        strategy = _strategy()
        self._buy_bar(strategy, 10.0)
        assert strategy._buy_signal_prices["000001"] == 10.0

        # 持仓期间的买入信号不得覆盖基准
        self._buy_bar(strategy, 12.0)
        assert strategy._buy_signal_prices["000001"] == 10.0

        self._sell_bar(strategy)
        assert "000001" not in strategy._buy_signal_prices
        assert "000001" not in strategy._below_buy_signal_counts

    def test_buy_not_matched_does_not_record(self) -> None:
        strategy = _strategy()
        with patch.object(strategy, "_before_minute_route"), patch.object(
            strategy, "_check_buy_conditions", return_value=(False, {"reason": "不够分"})
        ), patch.object(
            strategy, "_check_sell_conditions", return_value=(False, {"reason": "无理由"})
        ):
            strategy.handle_minute_bar("000001", {}, _bar(10.0))
        assert "000001" not in strategy._buy_signal_prices

    def test_non_sell_signal_keeps_baseline(self) -> None:
        """`matched=True` 但不是 SELL（异常返回值）时不得清基准。"""
        strategy = _strategy()
        self._buy_bar(strategy, 10.0)
        with patch.object(strategy, "_before_minute_route"), patch.object(
            strategy, "_break_max_r", {"000001": True}
        ), patch.object(
            strategy, "_check_sell_conditions", return_value=(True, {"signal": ""})
        ):
            strategy.handle_minute_bar("000001", {}, _bar())
        assert strategy._buy_signal_prices["000001"] == 10.0

    def test_all_minute_bar_overrides_call_sync(self) -> None:
        """源码守卫：每个 `handle_minute_bar` 实现都必须在返回前调同步方法。

        基类加逻辑、子类重写绕过去，是这条机制最容易静默失效的方式（本文件
        初版就踩过：swing 的基准一直为空，止损从未生效，且不报错）。
        """
        for path in sorted((PROJECT_ROOT / "strategy").glob("*.py")):
            src: str = path.read_text(encoding="utf-8")
            if "def handle_minute_bar" not in src:
                continue
            body: str = src.split("def handle_minute_bar")[1].split("\n    def ")[0]
            assert "_sync_signal_state" in body, f"{path.name} 的 handle_minute_bar 未同步状态"
            # 返回值必须被消费：该方法同时承担日内熔断的否决职责，只做副作用而让
            # 调用方沿用原 `matched`，被封锁的信号照样会发出去（熔断静默失效）。
            consumed: bool = any(
                f"{prefix}self._sync_signal_state(" in body
                for prefix in ("return ", "= ", ", ")
            )
            assert consumed, f"{path.name} 的 handle_minute_bar 丢弃了同步方法的返回值"


class TestBaselineSurvivesBeforeTrading:
    """基准必须跨日存活 —— 隔夜跳空恰恰是最需要止损的场景。"""

    def test_before_trading_does_not_clear_baseline(self) -> None:
        strategy = _strategy()
        strategy.set_buy_signal_price("000001", 10.0)
        # before_trading 会清选股/分钟缓存；基准不在清理范围内
        with patch.object(UserStrategyCache, "clear_user"), patch.object(
            UserStrategyCache, "reset_redis_failure"
        ):
            strategy.before_trading("2026-07-16", ["000001"])
        assert strategy._buy_signal_prices["000001"] == 10.0

    def test_source_guard_before_trading(self) -> None:
        """源码守卫：`before_trading` 里不得出现基准清理。

        有人「顺手统一清理日内状态」就会加上它，症状是隔夜后第一根 bar 无基准、
        止损静默失效 —— 而这不会报任何错。
        """
        src: str = (PROJECT_ROOT / "strategy" / "base_strategy.py").read_text(encoding="utf-8")
        body: str = src.split("def before_trading")[1].split("def handle_minute_bar")[0]
        assert "_buy_signal_prices" not in body
        assert "_below_buy_signal_counts" not in body


class TestStrategyWiring:
    """两个策略的接入点。"""

    def test_sell_paths_use_break_stop(self) -> None:
        for name in ("volume_expansion_strategy.py", "swing_trading_strategy.py"):
            src: str = (PROJECT_ROOT / "strategy" / name).read_text(encoding="utf-8")
            assert "_buy_signal_break_stop" in src, f"{name} 未接入买入信号价止损"

    def test_sell_paths_do_not_resolve_highest_price(self) -> None:
        """源码守卫：卖出路径不得再查「持仓期间最高价」。

        留着它既是多余的服务端查询，也容易在下次改动时被「顺手」接回判定。
        """
        for name in ("volume_expansion_strategy.py", "swing_trading_strategy.py"):
            src: str = (PROJECT_ROOT / "strategy" / name).read_text(encoding="utf-8")
            assert "_resolve_highest_price" not in src, f"{name} 仍引用最高价回撤基准"

    def test_strong_rebound_untouched(self) -> None:
        """反弹策略不在本次改动范围内，仍用自己的最高价口径。"""
        src: str = (PROJECT_ROOT / "strategy" / "strong_rebound_strategy.py").read_text(
            encoding="utf-8"
        )
        assert "SELL_DRAWDOWN_PCT" in src
        assert "_buy_signal_break_stop" not in src
