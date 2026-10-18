"""日内熔断（2026-10-14）的守卫。

要解决的问题是硬止损的**空转**。v5 回测（27 组，2026-07-21~10-13）显示：止损把仓位
清零之后，「缩量回调」这个买入条件往往仍然成立，成交间隔一到就买回同一只票、再被
同一个止损打出来。成交量策略最严重：「止损后 2 小时内再入场」的笔数占止损笔数的
60%~100%，其中 **93% / 81% / 80%** 又被同一条件二次止损。止损只把大亏切成小亏，
总亏损没变，笔数涨到 2.4~2.5 倍。

新语义（作用域 = **单只股票**，解除时机 = **次日自动**）：
- 买入侧：连续 3 次亏损卖出（卖出价 < 买入信号价）后，当日不再对该股发买入信号；
- 卖出侧：连续 3 次卖飞（卖出后当日价格涨过该次卖出价）后，当日不再对该股发卖出信号。

本文件守住五件事：
1. 买入侧判定表：连续计数、盈利卖出清零、跨日累计、次日自动解除；
2. 卖出侧判定表：突破才计数、等价不算突破、跨日未突破则清零、观察位只消费一次；
3. 「没有建仓基准的卖出」既不计也不清零（策略收空持仓，未持仓股票照样会判出
   卖出信号，记进去会让计数凭空虚增或错误清零）；
4. 被熔断否决的信号不写回任何计数（那笔交易根本没发生）；
5. 源码级守卫：收尾方法的返回值必须被每个路由消耗，否则熔断静默失效。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from strategy.swing_trading_strategy import SwingTradingStrategy
from strategy.volume_expansion_strategy import VolumeExpansionStrategy

PROJECT_ROOT = Path(__file__).resolve().parents[1]
USID: str = "test-intraday-breaker"

DAY1: str = "2026-07-15"
DAY2: str = "2026-07-16"
DAY3: str = "2026-07-17"


def _strategy(tag: str = USID) -> SwingTradingStrategy:
    """构造只走本地逻辑的波段策略实例（参数全空 ⇒ 取类属性默认值）。

    `_sync_signal_state` 与两个 `intraday_*_blocked` 都在基类，三个策略共用同一份
    实现；波段策略没有成交量策略那套「分钟窗口必须够长」的前置闸门，用它承载
    判定表最直接。
    """
    return SwingTradingStrategy(
        user_strategy_id=tag,
        user_strategy_params={"match": {}, "buy": {}, "sell": {}, "risk": {}},
    )


def _bar(price: float = 10.0, code: str = "000001", day: str = DAY1) -> dict[str, Any]:
    return {
        "code": code,
        "name": "",
        "price": price,
        "close": price,
        "open": price,
        "preclose": 10.0,
        "high": price,
        "low": price,
        "create_time": f"{day} 10:30:00",
    }


def _sell(
    strategy: SwingTradingStrategy,
    code: str = "000001",
    price: float = 9.0,
    day: str = DAY1,
    baseline: float = 10.0,
) -> None:
    """模拟一次「有建仓基准的卖出」：先写基准，再走收尾钩子。

    基准必须在这里显式写：真实的链路里它由上一个买入信号写入，而本文件只测
    「卖出之后」的行为，不重放完整的买卖序列。
    """
    strategy._buy_signal_prices[code] = baseline
    strategy._below_buy_signal_counts[code] = 0
    strategy._sync_signal_state(code, True, {"signal": "SELL"}, _bar(price, code, day))


def _tick(
    strategy: SwingTradingStrategy,
    price: float,
    code: str = "000001",
    day: str = DAY1,
) -> tuple[bool, dict[str, Any]]:
    """模拟一根「没有信号」的 bar：只驱动观察类逻辑。"""
    return strategy._sync_signal_state(code, False, {}, _bar(price, code, day))


class TestBuySideBlockTable:
    """买入侧：连续亏损卖出达阈值后当日停买。"""

    def test_three_loss_sells_block_same_day(self) -> None:
        strategy = _strategy()
        for _ in range(2):
            _sell(strategy, price=9.0)
        assert strategy.intraday_buy_blocked("000001", _bar(9.5)) is False
        _sell(strategy, price=9.0)
        assert strategy._loss_sell_streak["000001"] == 3
        assert strategy.intraday_buy_blocked("000001", _bar(9.5)) is True

    def test_profit_sell_resets_streak(self) -> None:
        """「连续」的定义：中间夹一笔盈利卖出即打断累计。"""
        strategy = _strategy()
        _sell(strategy, price=9.0)
        _sell(strategy, price=9.0)
        assert strategy._loss_sell_streak["000001"] == 2
        _sell(strategy, price=10.5)
        assert strategy._loss_sell_streak["000001"] == 0
        _sell(strategy, price=9.0)
        assert strategy._loss_sell_streak["000001"] == 1
        assert strategy.intraday_buy_blocked("000001", _bar(9.5)) is False

    def test_break_even_sell_resets_streak(self) -> None:
        """卖出价等于买入信号价不算亏损（判定用严格 `<`）。"""
        strategy = _strategy()
        _sell(strategy, price=9.0)
        _sell(strategy, price=10.0)
        assert strategy._loss_sell_streak["000001"] == 0

    def test_streak_accumulates_across_days(self) -> None:
        """计数跨日累计，但**封锁只在触发它的当日生效**。"""
        strategy = _strategy()
        _sell(strategy, price=9.0, day=DAY1)
        _sell(strategy, price=9.0, day=DAY2)
        assert strategy._loss_sell_streak["000001"] == 2
        assert strategy.intraday_buy_blocked("000001", _bar(9.5, day=DAY1)) is False
        _sell(strategy, price=9.0, day=DAY2)
        assert strategy.intraday_buy_blocked("000001", _bar(9.5, day=DAY2)) is True

    def test_block_expires_next_day(self) -> None:
        strategy = _strategy()
        for _ in range(3):
            _sell(strategy, price=9.0, day=DAY1)
        assert strategy.intraday_buy_blocked("000001", _bar(9.5, day=DAY1)) is True
        # 次日自动解除，不需要显式清除：封锁表按「日期相等」判定
        assert strategy.intraday_buy_blocked("000001", _bar(9.5, day=DAY2)) is False

    def test_sell_without_baseline_is_ignored(self) -> None:
        """没有建仓基准的卖出既**不计数也不清零**。

        策略恒收空持仓，未持仓的股票照样会被跑一遍卖出判定（波段策略尤其如此，
        它把整个池当交易域）。把它当亏损卖出会凭空虚增计数，当盈利卖出会错误地
        打断「连续亏损」的累计。
        """
        strategy = _strategy()
        _sell(strategy, price=9.0)
        assert strategy._loss_sell_streak["000001"] == 1
        # 基准已在上一笔卖出时清除，这次的卖出信号没有对应建仓
        _tick(strategy, 9.0)
        strategy._sync_signal_state("000001", True, {"signal": "SELL"}, _bar(9.5))
        assert strategy._loss_sell_streak["000001"] == 1

    def test_disabled_switch_keeps_no_state(self) -> None:
        strategy = _strategy()
        strategy.INTRADAY_BREAKER_ENABLED = False
        for _ in range(3):
            _sell(strategy, price=9.0)
        assert strategy._loss_sell_streak.get("000001", 0) == 0
        assert strategy.intraday_buy_blocked("000001", _bar(9.5)) is False

    def test_block_is_per_stock(self) -> None:
        """作用域是单只股票：封锁 A 不影响 B。"""
        strategy = _strategy()
        for _ in range(3):
            _sell(strategy, code="000001", price=9.0)
        assert strategy.intraday_buy_blocked("000001", _bar(9.5, code="000001")) is True
        assert strategy.intraday_buy_blocked("000002", _bar(9.5, code="000002")) is False


class TestSellSideBlockTable:
    """卖出侧：连续卖飞达阈值后当日停卖。"""

    def test_three_fly_sells_block_same_day(self) -> None:
        strategy = _strategy()
        for _ in range(3):
            _sell(strategy, price=9.0)
            _tick(strategy, 9.5)  # 涨过卖出价 9.0 ⇒ 记一次卖飞
        assert strategy._fly_sell_streak["000001"] == 3
        assert strategy.intraday_sell_blocked("000001", _bar(9.5)) is True

    def test_equal_price_is_not_fly(self) -> None:
        """「突破」用严格 `>`；价格等于卖出价不算突破，观察位继续挂着。"""
        strategy = _strategy()
        _sell(strategy, price=9.0)
        _tick(strategy, 9.0)
        assert strategy._fly_sell_streak.get("000001", 0) == 0
        assert "000001" in strategy._sell_fly_watch

    def test_fly_watch_consumed_once(self) -> None:
        """同一笔卖出的观察位第一次突破就被消费，后续 bar 不再重复计数。"""
        strategy = _strategy()
        _sell(strategy, price=9.0)
        _tick(strategy, 9.5)
        _tick(strategy, 9.8)
        _tick(strategy, 10.5)
        assert strategy._fly_sell_streak["000001"] == 1
        assert "000001" not in strategy._sell_fly_watch

    def test_no_fly_across_day_resets_streak(self) -> None:
        """当日收盘都没涨过卖出价 ⇒ 视为「没卖飞」，连续计数清零。"""
        strategy = _strategy()
        _sell(strategy, price=9.0, day=DAY1)
        _tick(strategy, 9.5, day=DAY1)
        assert strategy._fly_sell_streak["000001"] == 1
        _sell(strategy, price=9.0, day=DAY2)
        _tick(strategy, 8.8, day=DAY2)  # 当日没突破
        _tick(strategy, 8.8, day=DAY3)  # 跨日 ⇒ 清零
        assert strategy._fly_sell_streak["000001"] == 0

    def test_missing_time_is_fail_open(self) -> None:
        """行情缺时间字段时不判定、也不清零：数据不全不该改写累计状态。"""
        strategy = _strategy()
        _sell(strategy, price=9.0, day=DAY1)
        _tick(strategy, 9.5, day=DAY1)
        assert strategy._fly_sell_streak["000001"] == 1
        strategy._sell_fly_watch["000001"] = (DAY1, 9.0)
        strategy._sync_signal_state("000001", False, {}, {"price": 99.0})
        assert strategy._fly_sell_streak["000001"] == 1

    def test_sell_without_date_does_not_arm_watch(self) -> None:
        """没有交易日时不得挂观察位。

        观察位靠「日期相同」判断是否同日，空日期会让它跟**任何一天**都算同日，
        于是隔天一根更高的 bar 就能凭空记一次卖飞。
        """
        strategy = _strategy()
        strategy._buy_signal_prices["000001"] = 10.0
        matched, _ = strategy._sync_signal_state(
            "000001", True, {"signal": "SELL"}, {"price": 9.0}
        )
        assert matched is True
        assert strategy._loss_sell_streak["000001"] == 1
        assert "000001" not in strategy._sell_fly_watch


class TestBlockedSignalIsNotRecorded:
    """被熔断否决的信号必须原样退回，且不写回任何计数。"""

    def test_blocked_sell_keeps_streak_and_baseline(self) -> None:
        strategy = _strategy()
        _sell(strategy, price=9.0)
        _sell(strategy, price=9.0)
        assert strategy._loss_sell_streak["000001"] == 2
        strategy._sell_block_dates["000001"] = DAY1
        strategy._buy_signal_prices["000001"] = 10.0
        matched, extra = strategy._sync_signal_state(
            "000001", True, {"signal": "SELL"}, _bar(9.0)
        )
        assert matched is False
        assert extra["intraday_blocked"] == "sell"
        # 那笔卖出没有发生 ⇒ 亏损计数不动、建仓基准不清
        assert strategy._loss_sell_streak["000001"] == 2
        assert strategy._buy_signal_prices["000001"] == 10.0

    def test_blocked_buy_does_not_record_baseline(self) -> None:
        strategy = _strategy()
        for _ in range(3):
            _sell(strategy, price=9.0)
        matched, extra = strategy._sync_signal_state(
            "000001", True, {"signal": "BUY"}, _bar(9.0)
        )
        assert matched is False
        assert extra["intraday_blocked"] == "buy"
        assert "000001" not in strategy._buy_signal_prices


class TestRoutesHonourTheBlock:
    """端到端：封锁生效时路由必须把信号压掉。"""

    def test_swing_buy_route_is_suppressed(self) -> None:
        strategy = _strategy()
        for _ in range(3):
            _sell(strategy, price=9.0)
        with patch.object(
            strategy, "_check_buy_conditions", return_value=(True, {"signal": "BUY", "score": 6.0})
        ), patch.object(strategy, "_check_sell_conditions", return_value=(False, {})):
            matched, extra = strategy.handle_minute_bar("000001", {}, _bar(9.0))
        assert matched is False
        assert extra.get("intraday_blocked") == "buy"

    def test_swing_sell_route_is_suppressed(self) -> None:
        strategy = _strategy()
        strategy._buy_signal_prices["000001"] = 10.0
        strategy._sell_block_dates["000001"] = DAY1
        with patch.object(
            strategy, "_check_buy_conditions", return_value=(False, {})
        ), patch.object(
            strategy,
            "_check_sell_conditions",
            return_value=(True, {"signal": "SELL", "score": -6.0}),
        ):
            matched, extra = strategy.handle_minute_bar("000001", {}, _bar(9.5))
        assert matched is False
        assert extra.get("intraday_blocked") == "sell"

    def test_blocked_buy_falls_through_from_buy_side_in_base_route(self) -> None:
        """基类路由：买入被封锁时沿用「没匹配到」的路径，落到卖出判定。

        不能提前 return —— 实时链路恒传空持仓，持仓股的卖出判定正是靠
        「买入不成立 ⇒ 继续走卖出分支」才跑得到的。同时熔断原因必须留存下来，
        否则引擎只会打出卖出侧的原因，「被熔断挡掉」与「本来就没信号」在日志里
        长得一样。

        本条只能用**未重写 `handle_minute_bar`** 的策略（成交量 / 强趋势）：
        波段策略重写了路由，patch 基类的 `check_minute_sell` 对它无效。
        """
        strategy = VolumeExpansionStrategy(
            user_strategy_id=f"{USID}-base-route",
            user_strategy_params={"match": {}, "buy": {}, "sell": {}, "risk": {}},
        )
        strategy._buy_block_dates["000001"] = DAY1
        with patch.object(
            strategy, "_load_select_stocks", return_value={"000001"}
        ), patch.object(
            strategy, "check_minute_buy", return_value=(True, {"signal": "BUY"})
        ), patch.object(
            strategy, "check_minute_sell", return_value=(False, {})
        ):
            matched, extra = strategy.handle_minute_bar("000001", {}, _bar(9.0))
        assert matched is False
        assert extra.get("intraday_blocked") == "buy"
        assert "日内熔断" in str(extra.get("reason", ""))
        # 被否决的买入不得写入止损基准（基准只在真正放行的买入信号上记）
        assert "000001" not in strategy._buy_signal_prices


class TestWiring:
    """源码级守卫：这条机制靠「每个路由都调用收尾方法」成立。"""

    def test_tick_route_consumes_sync_result(self) -> None:
        src: str = (PROJECT_ROOT / "strategy" / "base_strategy.py").read_text(encoding="utf-8")
        body: str = src.split("def handle_tick_bar")[1].split("\n    def ")[0]
        assert "return self._sync_signal_state(" in body, "handle_tick_bar 未消费熔断结果"

    def test_no_stale_call_name_remains(self) -> None:
        """改名后不得残留旧名字：旧名只做副作用、不返回放行结果。"""
        for path in sorted((PROJECT_ROOT / "strategy").glob("*.py")):
            src: str = path.read_text(encoding="utf-8")
            assert "_sync_buy_signal_price" not in src, f"{path.name} 残留旧方法名"

    def test_before_trading_does_not_reset_streaks(self) -> None:
        """源码守卫：`before_trading` 不得清熔断计数。

        「连续亏损卖出」按需求是**跨日累计**的，跨日清理会让它退化成「当日连续」。
        """
        src: str = (PROJECT_ROOT / "strategy" / "base_strategy.py").read_text(encoding="utf-8")
        body: str = src.split("def before_trading")[1].split("def handle_minute_bar")[0]
        for name in ("_loss_sell_streak", "_fly_sell_streak", "_buy_block_dates", "_sell_block_dates"):
            assert name not in body, f"before_trading 清掉了 {name}"
