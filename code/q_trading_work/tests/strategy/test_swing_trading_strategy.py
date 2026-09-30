"""
Author: liguoqiang
Date: 2026-08-02
Description: 低频波段策略测试 —— 使用真实 API 行情数据（日K / 分钟字段映射）。
    服务端分钟接口当前返回空，故分钟相关用例使用真实日K数据映射为分钟字段。
"""

from __future__ import annotations

from configparser import ConfigParser
from datetime import datetime, timedelta
import unittest
from typing import Any
from unittest.mock import patch

import pandas as pd

from api.client import ApiClient
from api.market import MarketApi
from app_context import AppContext
from factor.boll_support_resistance import BollSRFactor
from factor.in_day_support_resistance import InDaySRFactor
from strategy.swing_trading_strategy import SwingTradingStrategy
from utils.tools import load_admin_token


def setUpModule() -> None:
    AppContext().api_client.set_fallback_token(load_admin_token())


def fetch_daily(code: str = "000001") -> list[dict[str, Any]]:
    return AppContext().market_api.get_day_kline(
        code=code, start="2026-03-01", end="2026-07-31"
    )


class TestSwingTradingStrategy(unittest.TestCase):

    def setUp(self) -> None:
        self._admin_client: ApiClient = ApiClient()
        admin_token: str = self._load_admin_token()
        if admin_token:
            self._admin_client.set_token(admin_token)
            # 同时设置全局 ApiClient 的 fallback token，确保 AppContext().pool_api 等
            # 在后台线程中也能携带认证信息
            AppContext().api_client.set_fallback_token(admin_token)
        self.market_api: MarketApi = MarketApi(self._admin_client)

    def _load_admin_token(self) -> str:
        """从 cfg/stock.cfg 读取 admin_token。

        :return: admin_token 字符串，未配置时返回空字符串
        """
        try:
            from utils.tools import resource_path
            cp = ConfigParser()
            cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
            return cp.get("server", "admin_token", fallback="").strip()
        except Exception:
            return ""
            
    def test_buy_condition(self) -> None:
        code = "603993"
        start: str = "2026-07-28 09:30:00"
        end: str = "2026-07-28 15:00:00"
        strategy = SwingTradingStrategy()
        
        for i in range(0, 50):
            strategy.before_trading(start, [code])
            minute_hq_list = self.market_api.get_minute_kline(code=code, start=start, end=end)
            if len(minute_hq_list) > 0:
                for stock_item in minute_hq_list:
                    strategy._save_minute_cache(code=code, stock_data=stock_item)
                    # result, reason = strategy._check_buy_conditions(
                    #     code=code, stock_data = stock_item
                    # )
                    # if result:
                    #     print(f"Buy condition check result: {result}, info: {reason}")
                    result, reason = strategy._check_sell_conditions(
                        code=code, position = {}, stock_data = stock_item
                    )
                    if result:
                        print(f"Sell condition check result: {result}, info: {reason}")
            next_day = (datetime.strptime(start, "%Y-%m-%d %H:%M:%S") + timedelta(days=1)).strftime("%Y-%m-%d")
            start = f"{next_day} 09:30:00"
            end = f"{next_day} 15:00:00"

class TestSwingLimitAware(unittest.TestCase):
    """R13：波段策略的涨跌停阈值按板块判定（两个方向都挡），且 preclose=0 不再除零。

    这些用例只走「时间/价格/涨跌停」的前置分支，并把 `_get_sr_points` 打桩成空表，
    因此不触网；空表本身同时验证 R3 的空表保护（不再抛 IndexError）。
    """

    USID: str = "test-swing-limit-aware"

    def _strategy(self) -> SwingTradingStrategy:
        return SwingTradingStrategy(
            user_strategy_id=self.USID,
            user_strategy_params={"match": {}, "buy": {}, "sell": {}},
        )

    @staticmethod
    def _bar(**overrides: Any) -> dict[str, Any]:
        bar: dict[str, Any] = {
            "code": "000001",
            "name": "",
            "price": 10.0,
            "open": 10.0,
            "preclose": 10.0,
            "high": 10.0,
            "low": 10.0,
            "create_time": "2026-07-15 10:30:00",
        }
        bar.update(overrides)
        return bar

    def test_buy_blocked_near_limit_main_board(self) -> None:
        """主板 −9.1%（10% 板留 1 个百分点余量）→ 接近跌停，不买入。"""
        strategy = self._strategy()
        matched, extra = strategy._check_buy_conditions("000001", self._bar(price=9.09))
        self.assertFalse(matched)
        self.assertIn("接近涨停或跌停", extra["reason"])

    def test_buy_blocked_near_limit_up_main_board(self) -> None:
        """R13：涨停方向同样拦（类文档「涨停不买入」）→ 主板 +9.1% 不买入。"""
        strategy = self._strategy()
        matched, extra = strategy._check_buy_conditions("000001", self._bar(price=10.91))
        self.assertFalse(matched)
        self.assertIn("接近涨停或跌停", extra["reason"])

    def test_buy_not_blocked_on_chinext_9pct_drop(self) -> None:
        """创业板 −9%（20% 板）远未接近跌停，不应被涨跌停分支挡掉。"""
        strategy = self._strategy()
        with patch.object(
            strategy, "_get_sr_points", return_value=pd.DataFrame()
        ), patch.object(strategy, "_get_minute_cache", return_value=[]):
            matched, extra = strategy._check_buy_conditions(
                "300750", self._bar(code="300750", name="宁德时代", price=9.1)
            )
        self.assertFalse(matched)
        self.assertNotIn("接近涨停或跌停", extra.get("reason", ""))
        # 空表保护生效：分值为 0 而不是抛 IndexError
        self.assertIn("未满足", extra["reason"])

    def test_sell_blocked_near_limit_st(self) -> None:
        """ST（5% 板）+4.1% 已接近涨停，不挂卖单。"""
        strategy = self._strategy()
        matched, extra = strategy._check_sell_conditions(
            "002456", {}, self._bar(code="002456", name="*ST欧菲", price=10.41)
        )
        self.assertFalse(matched)
        self.assertIn("接近涨停或跌停", extra["reason"])

    def test_preclose_zero_does_not_raise(self) -> None:
        """preclose=0 时不得除零（原实现会抛 ZeroDivisionError）。"""
        strategy = self._strategy()
        with patch.object(
            strategy, "_get_sr_points", return_value=pd.DataFrame()
        ), patch.object(strategy, "_get_minute_cache", return_value=[]):
            matched, extra = strategy._check_buy_conditions(
                "000001", self._bar(preclose=0)
            )
        self.assertFalse(matched)
        self.assertNotIn("检查异常", extra.get("reason", ""))


def _bar(**overrides: Any) -> dict[str, Any]:
    """构造一根分钟行情：默认涨跌幅 0、时间在 9:40 之后，不触发前置闸门。"""
    bar: dict[str, Any] = {
        "code": "000001",
        "name": "",
        "price": 10.0,
        "open": 10.0,
        "preclose": 10.0,
        "high": 10.0,
        "low": 10.0,
        "create_time": "2026-07-15 10:30:00",
    }
    bar.update(overrides)
    return bar


def _swing_strategy(tag: str) -> SwingTradingStrategy:
    """构造只走本地逻辑的波段策略实例（参数全空 ⇒ 取类属性默认值）。"""
    return SwingTradingStrategy(
        user_strategy_id=tag,
        user_strategy_params={"match": {}, "buy": {}, "sell": {}, "risk": {}},
    )


class TestSwingTrendFilter(unittest.TestCase):
    """趋势闸门：日线下跌趋势时跳过买入判定，卖出与风控判定照常（2026-09-29）。

    `_is_downtrend` 的判定表在下面逐个用例覆盖；这里重点是「闸门确实接在
    买入判定之前，且没有顺手把卖出也关掉」—— 后者会让下跌趋势中的持仓
    同时失去支撑位卖出与回撤止损，正是最需要离场的时刻。
    """

    USID: str = "test-swing-trend-filter"

    @staticmethod
    def _scores(value: float):
        """把两个位置因子打桩成固定分：位置分 = (inday + boll) / 2。"""
        return (
            patch.object(InDaySRFactor, "score_with_sr", return_value=value),
            patch.object(BollSRFactor, "score", return_value=value),
        )

    def _run(self, strategy: SwingTradingStrategy, price: float = 10.0):
        p1, p2 = self._scores(10.0)  # 位置分 +10：正常必然买入
        with p1, p2, patch.object(
            strategy, "_get_sr_points", return_value=pd.DataFrame()
        ), patch.object(strategy, "_get_minute_cache", return_value=[]):
            return strategy.handle_minute_bar("000001", {}, _bar(price=price))

    def test_downtrend_blocks_buy(self) -> None:
        """趋势向下时，位置分 +10 也不得产生买入信号。"""
        strategy = _swing_strategy(self.USID)
        strategy._downtrend_codes.add("000001")
        matched, extra = self._run(strategy)
        self.assertFalse(matched)
        self.assertNotEqual(extra.get("signal"), "BUY")

    def test_uptrend_still_buys(self) -> None:
        """对照组：同一份行情、同一套打分，无趋势标记时照常买入。

        没有这条对照，`test_downtrend_blocks_buy` 即使在闸门接错位置
        （例如把买入整条链路关掉）时也会通过。
        """
        strategy = _swing_strategy(self.USID)
        matched, extra = self._run(strategy)
        self.assertTrue(matched)
        self.assertEqual(extra["signal"], "BUY")

    def test_sell_still_runs_when_downtrend(self) -> None:
        """趋势向下时卖出判定仍在执行：位置分 −10 应照常产出 SELL。"""
        strategy = _swing_strategy(self.USID)
        strategy._downtrend_codes.add("000001")
        p1, p2 = self._scores(-10.0)
        with p1, p2, patch.object(
            strategy, "_get_sr_points", return_value=pd.DataFrame()
        ), patch.object(strategy, "_get_minute_cache", return_value=[]):
            matched, extra = strategy.handle_minute_bar("000001", {}, _bar())
        self.assertTrue(matched)
        self.assertEqual(extra["signal"], "SELL")

    def test_is_downtrend_decision_table(self) -> None:
        """判定必须同时要求 ADX 达标与 −DI > +DI；数据缺失时 fail-open。"""
        strategy = _swing_strategy(self.USID)
        nan: float = float("nan")
        cases: list[tuple[dict[str, Any], bool, str]] = [
            ({"adx": 30.0, "+DI": 20.0, "-DI": 25.0}, True, "ADX 达标且 −DI > +DI"),
            ({"adx": 24.9, "+DI": 20.0, "-DI": 25.0}, False, "ADX 未达门槛 ⇒ 视为震荡"),
            ({"adx": 30.0, "+DI": 25.0, "-DI": 20.0}, False, "方向向上"),
            ({"adx": 30.0, "+DI": 25.0, "-DI": 25.0}, False, "DI 相等 ⇒ 方向不明"),
            ({}, False, "无数据 ⇒ 不过滤（fail-open）"),
            ({"adx": nan, "+DI": 20.0, "-DI": 25.0}, False, "ADX 为 NaN ⇒ 判不出趋势"),
            ({"adx": 30.0, "+DI": nan, "-DI": nan}, False, "DI 为 NaN ⇒ 判不出趋势"),
        ]
        for detail, expected, msg in cases:
            with self.subTest(detail=detail):
                self.assertEqual(strategy._is_downtrend(detail), expected, msg)

    def test_trend_kline_days_covers_adx_lookback(self) -> None:
        """取数根数必须覆盖 ADX 的 2×period lookback。

        少一根时 talib 的末值是 NaN，`NaN > 阈值` 恒为 False ⇒ 判不出下跌趋势
        ⇒ 趋势闸门静默失效（不报错、不告警，只是永远不过滤）。
        """
        strategy = _swing_strategy(self.USID)
        self.assertGreaterEqual(
            strategy._trend_kline_days(), 2 * strategy.TREND_DAYS_LIMIT + 1
        )

    def test_match_strategy_rejects_downtrend_before_pe_query(self) -> None:
        """选股链路同样过滤下跌趋势，且必须排在市盈率查询之前（省一次 HTTP）。"""
        strategy = _swing_strategy(self.USID)
        stock_data: list[dict[str, Any]] = [
            {"code": "000001", "high": 10.0, "low": 9.0, "close": 9.5, "open": 9.6}
            for _ in range(40)
        ]
        with patch.object(
            strategy,
            "_adx_detail",
            return_value={"adx": 31.0, "+DI": 18.0, "-DI": 27.0},
        ), patch.object(strategy, "_check_industry_pe") as pe:
            matched, extra = strategy.is_match_strategy(stock_data)
        self.assertFalse(matched)
        self.assertIn("趋势向下", extra["reason"])
        pe.assert_not_called()

    def test_build_sr_points_returns_empty_on_short_data(self) -> None:
        """日线为空时返回空表而不是抛异常（空表会中断整个股票池的盘前准备）。"""
        strategy = _swing_strategy(self.USID)
        self.assertTrue(strategy._build_sr_points("000001", "2026-07-15", pd.DataFrame()).empty)

    def test_build_sr_points_uses_last_row_as_baseline(self) -> None:
        """枢轴点必须取「最后一根（= 前一交易日）」而不是整段的首行。"""
        strategy = _swing_strategy(self.USID)
        klines = pd.DataFrame(
            [
                {"high": 20.0, "low": 10.0, "close": 15.0},  # 更早的一根
                {"high": 11.0, "low": 8.0, "close": 11.0},  # 最后一根 = 基准日
            ]
        )
        sr_df = strategy._build_sr_points("000001", "2026-07-15", klines)
        self.assertEqual(len(sr_df), 1)
        self.assertAlmostEqual(float(sr_df.iloc[0]["pivot"]), 10.0, places=6)


class TestSwingSellDrawdown(unittest.TestCase):
    """卖出侧回撤：个股回撤与账户回撤作为独立负分项叠加在位置分上。

    两个位置因子被打桩成 0，因此触发与否完全由回撤决定 —— 这正是用户现场
    （枢轴点 0.00、布林带 -7.50）的极端版：位置分 0 时，一条回撤理由也该卖出。
    """

    USID: str = "test-swing-sell-drawdown"

    def _sell(
        self,
        strategy: SwingTradingStrategy,
        highest_price: float,
        account_dd: float = 0.0,
    ):
        """跑一次卖出判定：位置分固定 0，持仓最高价与账户回撤可控。"""
        if account_dd:
            strategy.set_account_drawdown(account_dd)
        with patch.object(InDaySRFactor, "score_with_sr", return_value=0.0), patch.object(
            BollSRFactor, "score", return_value=0.0
        ), patch.object(
            strategy, "_get_sr_points", return_value=pd.DataFrame()
        ), patch.object(
            strategy, "_get_minute_cache", return_value=[]
        ), patch.object(
            strategy, "_resolve_highest_price", return_value=highest_price
        ):
            return strategy._check_sell_conditions("000001", {}, _bar())

    def test_position_drawdown_triggers_sell(self) -> None:
        """现价 10.0 / 持仓最高 11.0 ⇒ 回撤 -9.09%，超过 -6% 阈值 ⇒ 卖出。"""
        strategy = _swing_strategy(self.USID)
        matched, extra = self._sell(strategy, highest_price=11.0)
        self.assertTrue(matched)
        self.assertEqual(extra["signal"], "SELL")
        self.assertAlmostEqual(extra["score"], -6.0, places=2)
        self.assertAlmostEqual(extra["star"], 3.0, places=1)
        # 原有文案段必须保留，回撤段追加在后面
        self.assertIn("枢轴点分值:0.00", extra["reason"])
        self.assertIn("平均分值:0.00", extra["reason"])
        self.assertIn("持仓回撤", extra["reason"])
        self.assertAlmostEqual(extra["drawdown"], -9.09, places=1)

    def test_drawdown_within_threshold_keeps_original_reason(self) -> None:
        """回撤未达阈值时不追加任何字段（卖出文案逐字与改动前一致）。"""
        strategy = _swing_strategy(self.USID)
        matched, extra = self._sell(strategy, highest_price=10.2)  # -1.96%
        self.assertFalse(matched)
        self.assertNotIn("持仓回撤", extra.get("reason", ""))
        self.assertNotIn("账户回撤", extra.get("reason", ""))

    def test_position_drawdown_threshold_is_configurable(self) -> None:
        """阈值放宽到 -20% 后，同一回撤（-9.09%）不再触发。"""
        strategy = _swing_strategy(self.USID)
        strategy.SELL_DRAWDOWN_PCT = -20.0
        matched, _extra = self._sell(strategy, highest_price=11.0)
        self.assertFalse(matched)

    def test_account_drawdown_triggers_sell(self) -> None:
        """账户回撤 -12.5% 达默认阈值 -10% ⇒ 单独一条即可触发卖出。"""
        strategy = _swing_strategy(self.USID)
        matched, extra = self._sell(strategy, highest_price=10.2, account_dd=-12.5)
        self.assertTrue(matched)
        self.assertAlmostEqual(extra["score"], -6.0, places=2)
        self.assertIn("账户回撤", extra["reason"])

    def test_account_drawdown_below_threshold_does_not_trigger(self) -> None:
        strategy = _swing_strategy(self.USID)
        matched, _extra = self._sell(strategy, highest_price=10.2, account_dd=-9.9)
        self.assertFalse(matched)

    def test_two_drawdowns_stack(self) -> None:
        """两条回撤理由同时成立时叠加（-6.0 + -6.0），而不是二选一。"""
        strategy = _swing_strategy(self.USID)
        matched, extra = self._sell(strategy, highest_price=11.0, account_dd=-12.5)
        self.assertTrue(matched)
        self.assertAlmostEqual(extra["score"], -12.0, places=2)
        self.assertIn("持仓回撤", extra["reason"])
        self.assertIn("账户回撤", extra["reason"])

    def test_drawdown_absent_keeps_original_behaviour(self) -> None:
        """位置分 0 且无回撤 ⇒ 不卖（加回撤不得改变原有卖出边界）。"""
        strategy = _swing_strategy(self.USID)
        matched, _extra = self._sell(strategy, highest_price=0.0)
        self.assertFalse(matched)


if __name__ == "__main__":
    unittest.main()
